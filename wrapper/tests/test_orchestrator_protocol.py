"""Executor protocol CLI round-trip tests (57B-113 / 57B-115, M1):
next-task / submit-task via ``cli.main(argv)`` — claim, submit a valid
result, submit an invalid one, and the distinct exit codes each path takes."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.cli import main
from analysis_wrapper.orchestrator.contracts import TaskPacket
from analysis_wrapper.orchestrator.engine import Engine


def _packet(task_id: str) -> TaskPacket:
    return TaskPacket.create(
        task_id=task_id, task_type="lens-findings", template_id="tpl",
        template_version="1.0.0", instructions="do it", inputs={"x": "y"},
        output_schema_id="lens-findings.v1", context_budget_tokens=1000)


def _result_doc(task_id: str, attempt: int, *, output=None) -> dict:
    output = output if output is not None else {"findings": [], "coverage": []}
    return {
        "contract_version": "1.0.0", "task_id": task_id, "status": "ok", "output": output,
        "executor": {"kind": "manual", "model": "human", "params": {}},
        "timing": {"started_at": "2026-07-26T00:00:00Z",
                  "finished_at": "2026-07-26T00:00:01Z", "wall_clock_s": 1.0},
        "tokens": None, "validation": {"passed": True, "failures": []}, "attempt": attempt,
    }


def test_next_task_returns_distinct_exit_code_when_no_ledger_exists(tmp_path, capsys):
    rc = main(["next-task", "--run", str(tmp_path)])
    assert rc == 6
    assert "no orchestrator ledger" in capsys.readouterr().err


def test_next_task_returns_empty_json_and_exit_zero_when_nothing_ready(tmp_path, capsys):
    Engine(tmp_path).create_tasks([_packet("a")])
    Engine(tmp_path).claim(1, executor_kind="x", model="y")  # a is now outstanding
    rc = main(["next-task", "--run", str(tmp_path)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_reclaim_tasks_makes_an_abandoned_claim_ready_without_ledger_editing(tmp_path, capsys):
    engine = Engine(tmp_path)
    engine.create_tasks([_packet("a")])
    engine.claim(1, executor_kind="host", model="codex")

    rc = main(["reclaim-tasks", "--run", str(tmp_path), "--all",
               "--reason", "session exited"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"reclaimed": ["a"]}

    rc = main(["next-task", "--run", str(tmp_path)])
    assert rc == 0
    claimed = json.loads(capsys.readouterr().out)
    assert claimed[0]["task"]["task_id"] == "a"
    assert claimed[0]["attempt"] == 2


def test_claim_submit_valid_round_trip(tmp_path, capsys):
    Engine(tmp_path).create_tasks([_packet("a")])

    rc = main(["next-task", "--run", str(tmp_path), "--claim", "1",
              "--executor-kind", "manual", "--model", "human"])
    assert rc == 0
    claimed = json.loads(capsys.readouterr().out)
    assert len(claimed) == 1
    assert claimed[0]["task"]["task_id"] == "a"
    attempt = claimed[0]["attempt"]
    assert attempt == 1

    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result_doc("a", attempt)))
    rc = main(["submit-task", "--run", str(tmp_path), "--task", "a", "--result", str(result_path)])
    assert rc == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["status"] == "validated"
    assert outcome["failures"] == []

    # Nothing left ready.
    rc = main(["next-task", "--run", str(tmp_path)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_claim_submit_invalid_output_exits_nonzero_with_failures(tmp_path, capsys):
    Engine(tmp_path).create_tasks([_packet("a")])
    main(["next-task", "--run", str(tmp_path), "--claim", "1"])
    claimed = json.loads(capsys.readouterr().out)
    attempt = claimed[0]["attempt"]

    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result_doc("a", attempt, output={"bad": "shape"})))
    rc = main(["submit-task", "--run", str(tmp_path), "--task", "a", "--result", str(result_path)])
    assert rc == 3
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["status"] == "failed"
    assert outcome["failures"]  # non-empty, names the schema problems


def test_submit_task_reads_from_stdin_with_dash(tmp_path, capsys, monkeypatch):
    import io
    Engine(tmp_path).create_tasks([_packet("a")])
    main(["next-task", "--run", str(tmp_path), "--claim", "1"])
    claimed = json.loads(capsys.readouterr().out)
    attempt = claimed[0]["attempt"]

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_result_doc("a", attempt))))
    rc = main(["submit-task", "--run", str(tmp_path), "--task", "a", "--result", "-"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["status"] == "validated"


def test_submit_task_rejects_non_json_result_with_wrapper_input_error(tmp_path, capsys):
    Engine(tmp_path).create_tasks([_packet("a")])
    main(["next-task", "--run", str(tmp_path), "--claim", "1"])
    capsys.readouterr()

    bad_path = tmp_path / "bad.json"
    bad_path.write_text("this is not json")
    rc = main(["submit-task", "--run", str(tmp_path), "--task", "a", "--result", str(bad_path)])
    assert rc == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_submit_task_rejects_unknown_task_id_as_wrapper_input_error(tmp_path, capsys):
    Engine(tmp_path).create_tasks([_packet("a")])
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result_doc("nope", 1)))
    rc = main(["submit-task", "--run", str(tmp_path), "--task", "nope", "--result", str(result_path)])
    assert rc == 2
    assert "wrapper input error" in capsys.readouterr().err
