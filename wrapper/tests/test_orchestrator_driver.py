"""Host-default pipeline tests for 57B-145."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper import acceptance
from analysis_wrapper.orchestrator import driver
from analysis_wrapper.orchestrator.contracts import TaskPacket
from analysis_wrapper.orchestrator.engine import Engine
from analysis_wrapper.cli import parser


def _packet() -> TaskPacket:
    return TaskPacket.create(
        task_id="host-task", task_type="lens-findings", template_id="tpl",
        template_version="1.0.0", instructions="do it", inputs={"x": "y"},
        output_schema_id="lens-findings.v1", context_budget_tokens=1000)


def test_default_host_pipeline_needs_no_api_key_or_api_module(tmp_path, monkeypatch):
    """Host mode runs deterministic work, then exposes a claimable packet."""
    (tmp_path / "targets.json").write_text("{}", "utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delitem(sys.modules, "analysis_wrapper.orchestrator.executor_api", raising=False)

    def semantic_boundary(state):
        Engine(state.run).create_tasks([_packet()])
        outcome = state.phase("judgment")
        drained = driver._drain(state, outcome)
        outcome.finished_at = driver.time.monotonic()
        return drained

    monkeypatch.setattr(driver, "PHASES", (("judgment", semantic_boundary),))
    result = driver.run_pipeline(tmp_path, log=lambda _line: None)

    assert result["complete"] is False
    assert result["summary"]["executor"] == "host"
    assert result["summary"]["blocked_on"] == "judgment"
    assert result["summary"]["ready_tasks"] == ["host-task"]
    assert Engine(tmp_path).ready_task_ids() == ["host-task"]
    assert "analysis_wrapper.orchestrator.executor_api" not in sys.modules


def test_api_pipeline_preflights_before_running_any_phase(tmp_path, monkeypatch):
    (tmp_path / "targets.json").write_text("{}", "utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def should_not_run(_state):
        raise AssertionError("API preflight must happen before phase planning or claims")

    monkeypatch.setattr(driver, "PHASES", (("judgment", should_not_run),))
    with pytest.raises(driver.DriverError, match="ANTHROPIC_API_KEY") as error:
        driver.run_pipeline(tmp_path, executor="api", model="claude-x", log=lambda _line: None)
    assert "--executor host" in str(error.value)
    assert not (tmp_path / "tasks" / "ledger.jsonl").exists()


def test_cli_defaults_to_host_and_keeps_external_as_a_compatibility_alias():
    args = parser().parse_args(["run-pipeline", "--run", "/tmp/example"])
    assert args.executor == "host"
    alias = parser().parse_args(
        ["run-pipeline", "--run", "/tmp/example", "--executor", "external"])
    assert alias.executor == "external"


def test_judgment_freezes_only_the_initial_packet_wave(tmp_path, monkeypatch):
    """A resumed source-read run must not re-freeze derived lens packets."""
    (tmp_path / "run-provenance.json").write_text("{}\n", "utf-8")
    calls: list[Path] = []

    from analysis_wrapper.orchestrator import planner

    monkeypatch.setattr(planner, "plan_judgment", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(driver, "_drain", lambda _state, _outcome: False)

    def freeze(run: Path) -> Path:
        calls.append(run)
        manifest = run / acceptance.FILENAME
        manifest.write_text('{"manifest_digest":"fixture"}\n', "utf-8")
        return manifest

    monkeypatch.setattr(acceptance, "freeze", freeze)
    state = driver.RunState(run=tmp_path, executor="host", context_budget_tokens=1000,
                            log=lambda _line: None)

    assert driver._phase_judgment(state) is False
    assert driver._phase_judgment(state) is False
    assert calls == [tmp_path]
