"""Execution and controlled-comparison provenance for 57B-146."""

import json

from analysis_wrapper.orchestrator import provenance
from analysis_wrapper.orchestrator.composer import compose
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskResult, TaskTiming, TokenUsage, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine, now_iso


def test_execution_provenance_records_task_executor_timing_tokens_and_packet_baseline(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "targets.json").write_text("{}", "utf-8")
    (run / "run-provenance.json").write_text(json.dumps({
        "generation": {"language": "en", "model": "model-a", "effort": "high"},
    }), "utf-8")
    engine = Engine(run)
    engine.create_tasks(compose(
        task_id="selection", template_id="selection", template_version="v1",
        task_type="selection-fetch", instructions="select", inputs={"evidence": "x"},
        output_schema_id="selection-fetch.v1", context_budget_tokens=1000,
    ))
    item = engine.claim(1, executor_kind="manual", model="model-a")[0]
    at = now_iso()
    result = TaskResult(
        task_id="selection", status="ok", output={"selections": [{
            "selection_id": "source", "purpose": "confirm behavior",
            "ref": "api@" + "a" * 40 + ":main.go:1", "quoted_text": "",
        }]}, executor=ExecutorInfo(kind="manual", model="model-a", params={"effort": "high"}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.2),
        tokens=TokenUsage(input=20, output=22),
        validation=ValidationOutcome(passed=True, failures=()), attempt=item.attempt,
    )
    assert engine.submit("selection", result.to_dict())["status"] == "validated"

    path = provenance.write(run)
    observed = json.loads(path.read_text("utf-8"))
    assert observed["generation"] == {"language": "en", "model": "model-a", "effort": "high"}
    assert observed["tasks"] == [{
        "attempt": 1, "executor": {"kind": "manual", "model": "model-a", "params": {"effort": "high"}},
        "input_digest": item.packet.input_digest, "model_available": True,
        "task_id": "selection", "timing": result.timing.to_dict(),
        "tokens": {"input": 20, "output": 22},
        "tokens_available": True,
    }]
    acceptance = json.loads((run / "tasks" / "acceptance-manifest.json").read_text("utf-8"))
    assert acceptance["baseline"]["language"] == "en"
    assert acceptance["baseline"]["task_packets"][0]["input_digest"] == item.packet.input_digest
    assert provenance.problems(run) == []


def test_execution_provenance_labels_unavailable_generation_metadata_as_unknown(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "targets.json").write_text("{}", "utf-8")
    observed = json.loads(provenance.write(run).read_text("utf-8"))
    assert observed["generation"] == {
        "language": "unknown", "model": "unknown", "effort": "unknown",
    }
