"""Ledger-reading helper tests (57B-113 / 57B-116, M2): validated_outputs
reads the same ledger the Engine writes, including across a re-planned
(digest-changed) generation of the same task_id."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator.composer import compose
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine, now_iso
from analysis_wrapper.orchestrator.results import validated_outputs

_LENS_OUTPUT = {
    "findings": [{
        "finding_id": "finding-sample",
        "claim": "sample claim", "lens": "complexity",
        "affected_modules": ["mc-x"],
        "evidence": [{"fact": "one fact", "refs": ["signals/x.view.txt:1"],
                     "basis": "static-reference"}],
        "evidence_basis": ["static-reference"],
        "impact": "impact text", "priority": "medium", "confidence": "medium",
        "limitations": "none", "suggested_direction": "direction",
        "changeability_question": "none",
    }],
    "coverage": [{"signal": "x", "status": "complete", "note": ""}],
}


def _submit(engine, task_id, output, *, status="ok"):
    # engine.claim() dispatches whatever is READY in sorted task_id order,
    # not necessarily this specific task_id (another ready task may sort
    # first) -- claim everyone ready and pick the one this call is about;
    # any others stay claimed-but-outstanding, which is fine here (tests
    # only assert on `task_id`'s own resulting state).
    claimed = {item.packet.task_id: item for item in
              engine.claim(len(engine.ready_task_ids()) or 1,
                          executor_kind="manual", model="test")}
    assert task_id in claimed, (claimed, task_id)
    item = claimed[task_id]
    at = now_iso()
    result = TaskResult(
        task_id=task_id, status=status, output=output,
        executor=ExecutorInfo(kind="manual", model="test", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.1),
        tokens=None, validation=ValidationOutcome(passed=(status == "ok"), failures=(
            () if status == "ok" else ({"check": "x", "detail": "y", "location": ""},))),
        attempt=item.attempt)
    return engine.submit(task_id, result.to_dict())


def test_empty_ledger_returns_empty_dict(tmp_path):
    assert validated_outputs(tmp_path) == {}


def test_returns_only_validated_outputs_filtered_by_task_type(tmp_path):
    engine = Engine(tmp_path)
    lens_packets = compose(
        task_id="lens-a", template_id="t", template_version="1", task_type="lens-findings",
        instructions="do it", inputs={"a": "x"}, output_schema_id="lens-findings.v1",
        context_budget_tokens=8000)
    dedup_packets = compose(
        task_id="dedup-rank", template_id="t", template_version="1", task_type="dedup-rank",
        instructions="merge", inputs={"a": "x"}, output_schema_id="dedup-rank.v1",
        context_budget_tokens=8000)
    engine.create_tasks(lens_packets + dedup_packets)
    outcome = _submit(engine, "lens-a", _LENS_OUTPUT)
    assert outcome["status"] == "validated"

    outputs = validated_outputs(tmp_path)
    assert set(outputs) == {"lens-a"}  # dedup-rank never claimed/submitted -> not validated
    assert outputs["lens-a"] == _LENS_OUTPUT

    only_lens = validated_outputs(tmp_path, task_type="lens-findings")
    assert set(only_lens) == {"lens-a"}
    only_dedup = validated_outputs(tmp_path, task_type="dedup-rank")
    assert only_dedup == {}


def test_failed_and_pending_tasks_are_excluded(tmp_path):
    engine = Engine(tmp_path)
    packets = compose(
        task_id="lens-a", template_id="t", template_version="1", task_type="lens-findings",
        instructions="do it", inputs={"a": "x"}, output_schema_id="lens-findings.v1",
        context_budget_tokens=8000)
    engine.create_tasks(packets)
    # A malformed (schema-invalid) output fails validation but the task is
    # not yet exhausted -- it stays "pending", and its bogus output must not
    # leak out of validated_outputs.
    _submit(engine, "lens-a", {"findings": "not-a-list", "coverage": []})
    assert validated_outputs(tmp_path) == {}

    packets2 = compose(
        task_id="lens-b", template_id="t", template_version="1", task_type="lens-findings",
        instructions="do it", inputs={"a": "x"}, output_schema_id="lens-findings.v1",
        context_budget_tokens=8000)
    engine.create_tasks(packets2)
    # lens-b was created but never claimed -- still pending, excluded too.
    assert validated_outputs(tmp_path) == {}


def test_reflects_the_latest_generation_after_a_digest_change(tmp_path):
    engine = Engine(tmp_path)
    old_output = {**_LENS_OUTPUT, "coverage": [{"signal": "x", "status": "complete", "note": "old"}]}
    new_output = {**_LENS_OUTPUT, "coverage": [{"signal": "x", "status": "complete", "note": "new"}]}

    old_packets = compose(
        task_id="lens-a", template_id="t", template_version="1", task_type="lens-findings",
        instructions="do it (v1)", inputs={"a": "x"}, output_schema_id="lens-findings.v1",
        context_budget_tokens=8000)
    engine.create_tasks(old_packets)
    outcome = _submit(engine, "lens-a", old_output)
    assert outcome["status"] == "validated"
    assert validated_outputs(tmp_path)["lens-a"] == old_output

    # A changed instructions text -> a new input_digest -> a new generation
    # under the SAME task_id (engine.py's digest-keyed resume). The engine
    # resets attempts for it, so it must be re-claimed/re-submitted before it
    # counts as validated again.
    new_packets = compose(
        task_id="lens-a", template_id="t", template_version="1", task_type="lens-findings",
        instructions="do it (v2, edited)", inputs={"a": "x"}, output_schema_id="lens-findings.v1",
        context_budget_tokens=8000)
    engine.create_tasks(new_packets)
    assert validated_outputs(tmp_path) == {}, "a fresh generation is pending, not validated"

    outcome = _submit(engine, "lens-a", new_output)
    assert outcome["status"] == "validated"
    assert validated_outputs(tmp_path)["lens-a"] == new_output
