"""No-loss ledger-to-artifact accounting for 57B-146."""

import json

from analysis_wrapper.orchestrator import consumption
from analysis_wrapper.orchestrator.composer import compose
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine, now_iso


def _finding(finding_id):
    return {
        "finding_id": finding_id, "claim": "claim", "lens": "complexity",
        "affected_modules": ["mc-1"],
        "evidence": [{"fact": "fact", "refs": ["metric:code.analyzed-scope.total"],
                      "basis": "static-reference"}],
        "evidence_basis": ["static-reference"], "impact": "impact", "priority": "medium",
        "confidence": "medium", "limitations": "none", "suggested_direction": "direction",
        "changeability_question": "none",
    }


def _submit(engine, task_id, output):
    item = engine.claim(1, executor_kind="manual", model="test")[0]
    assert item.packet.task_id == task_id
    at = now_iso()
    result = TaskResult(
        task_id=task_id, status="ok", output=output,
        executor=ExecutorInfo(kind="manual", model="test", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.1), tokens=None,
        validation=ValidationOutcome(passed=True, failures=()), attempt=item.attempt,
    )
    assert engine.submit(task_id, result.to_dict())["status"] == "validated"


def test_consumption_counts_absorbed_lens_findings_as_explicitly_assembled(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    engine = Engine(run)
    for task_id in ("lens-one", "lens-two"):
        engine.create_tasks(compose(
            task_id=task_id, template_id=task_id, template_version="1", task_type="lens-findings",
            instructions="analyze", inputs={"evidence": "x"}, output_schema_id="lens-findings.v1",
            context_budget_tokens=1000,
        ))
    _submit(engine, "lens-one", {"findings": [_finding("finding-one")], "coverage": []})
    _submit(engine, "lens-two", {"findings": [_finding("finding-two")], "coverage": []})
    engine.create_tasks(compose(
        task_id="dedup", template_id="dedup", template_version="1", task_type="dedup-rank",
        instructions="dedup", inputs={"pool": "x"}, output_schema_id="dedup-rank.v1",
        context_budget_tokens=1000,
    ))
    _submit(engine, "dedup", {
        "input_finding_ids": ["finding-one", "finding-two"],
        "merge_map": {
            "finding-one": {"status": "surviving", "absorbed_into": None, "reason": "primary"},
            "finding-two": {"status": "absorbed", "absorbed_into": "finding-one", "reason": "same issue"},
        },
        "rank": [{"finding_id": "finding-one", "reason": "primary finding"}],
    })
    (run / "findings.json").write_text(json.dumps({"findings": [_finding("finding-one")]}), "utf-8")
    (run / "tasks").mkdir(exist_ok=True)
    (run / "tasks" / "assembled-findings.json").write_text("{}", "utf-8")

    manifest = consumption.build(run)
    lens_rows = [row for row in manifest["tasks"] if row["task_type"] == "lens-findings"]
    assert all(row["consumed"] for row in lens_rows)
    consumption.write(run)
    assert consumption.problems(run) == []
