"""Deterministic findings assembler tests (57B-113 / 57B-116, M2):
assemble.assemble() applies a validated dedup-rank output's merge_map/rank
to the run's validated lens-findings pool -- pure mechanical merge, no
judgment. Ledger fixtures follow test_orchestrator_results.py's pattern
(compose + Engine.create_tasks + a manual submit helper) rather than the
full plan_judgment/plan_dedup DAG -- assemble.py only ever reads validated
outputs through results.validated_outputs, so no other run artifact is
needed."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper import findings
from analysis_wrapper.orchestrator import assemble, schemas
from analysis_wrapper.orchestrator.composer import compose
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine, now_iso


def _finding(finding_id, *, evidence, affected_modules=("mc-a",),
            limitations="known gap", claim="a claim"):
    return {
        "finding_id": finding_id, "claim": claim, "lens": "complexity",
        "affected_modules": list(affected_modules), "evidence": evidence,
        "evidence_basis": sorted({item["basis"] for item in evidence}),
        "impact": "impact text", "priority": "medium", "confidence": "medium",
        "limitations": limitations, "suggested_direction": "direction",
        "changeability_question": "none",
    }


def _submit(engine, task_id, output):
    claimed = {item.packet.task_id: item for item in
              engine.claim(len(engine.ready_task_ids()) or 1,
                          executor_kind="manual", model="test")}
    item = claimed[task_id]
    at = now_iso()
    result = TaskResult(
        task_id=task_id, status="ok", output=output,
        executor=ExecutorInfo(kind="manual", model="test", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.1),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()),
        attempt=item.attempt)
    outcome = engine.submit(task_id, result.to_dict())
    assert outcome["status"] == "validated", outcome
    return outcome


def _create(engine, task_id, task_type, output_schema_id):
    packets = compose(
        task_id=task_id, template_id="t", template_version="1", task_type=task_type,
        instructions="do it", inputs={"a": "x"}, output_schema_id=output_schema_id,
        context_budget_tokens=8000)
    engine.create_tasks(packets)


def _lens_task(engine, task_id, rows):
    _create(engine, task_id, "lens-findings", "lens-findings.v1")
    _submit(engine, task_id, {
        "findings": rows,
        "coverage": [{"signal": "x", "status": "complete", "note": ""}],
    })


def _dedup_task(engine, merge_map, rank, *, task_id="dedup-rank"):
    _create(engine, task_id, "dedup-rank", "dedup-rank.v1")
    _submit(engine, task_id, {
        "input_finding_ids": sorted(merge_map),
        "merge_map": merge_map, "rank": rank,
    })


# --------------------------------------------------------------------------- #
# happy path: 3 findings, 1 absorption
# --------------------------------------------------------------------------- #

def test_happy_path_merges_evidence_modules_basis_and_trace_in_rank_order(tmp_path):
    engine = Engine(tmp_path)
    fact_a1 = {"fact": "fact A1", "refs": ["signals/x.view.txt:1"], "basis": "static-reference"}
    fact_b1 = {"fact": "fact B1", "refs": ["signals/y.view.txt:2"], "basis": "declaration"}
    fact_c1 = {"fact": "fact C1", "refs": ["signals/z.view.txt:3"], "basis": "history"}

    _lens_task(engine, "lens-1", [
        _finding("finding-a", evidence=[fact_a1], affected_modules=["mc-a"]),
        _finding("finding-b", evidence=[fact_b1], affected_modules=["mc-b"]),
    ])
    _lens_task(engine, "lens-2", [
        _finding("finding-c", evidence=[fact_c1], affected_modules=["mc-c"]),
    ])

    merge_map = {
        "finding-a": {"status": "surviving", "absorbed_into": None, "reason": "primary"},
        "finding-b": {"status": "absorbed", "absorbed_into": "finding-a",
                     "reason": "same root cause"},
        "finding-c": {"status": "surviving", "absorbed_into": None, "reason": "distinct"},
    }
    rank = [
        {"finding_id": "finding-c", "reason": "highest blast radius"},
        {"finding_id": "finding-a", "reason": "second"},
    ]
    _dedup_task(engine, merge_map, rank)

    doc = assemble.assemble(tmp_path)
    assert doc["schema_version"] == findings.SCHEMA_VERSION
    assert [row["finding_id"] for row in doc["findings"]] == ["finding-c", "finding-a"]

    row_c, row_a = doc["findings"]

    # finding-c: no absorption -> passes through unchanged (limitations intact).
    assert row_c["evidence"] == [fact_c1]
    assert row_c["affected_modules"] == ["mc-c"]
    assert row_c["evidence_basis"] == ["history"]
    assert row_c["limitations"] == "known gap"

    # finding-a: absorbed finding-b's evidence appended (survivor first),
    # modules unioned+sorted, basis recomputed, trace appended to limitations.
    assert row_a["evidence"] == [fact_a1, fact_b1]
    assert row_a["affected_modules"] == ["mc-a", "mc-b"]
    assert row_a["evidence_basis"] == ["declaration", "static-reference"]
    assert row_a["claim"] == "a claim"  # verbatim, never rewritten
    assert row_a["limitations"].startswith("known gap ")
    assert "merged: absorbed finding-b per dedup-rank" in row_a["limitations"]
    assert "same root cause" in row_a["limitations"]
    assert "\n" not in row_a["limitations"]


# --------------------------------------------------------------------------- #
# exact-duplicate evidence row dedup vs near-duplicate kept
# --------------------------------------------------------------------------- #

def test_exact_duplicate_evidence_row_deduped_but_near_duplicate_kept(tmp_path):
    engine = Engine(tmp_path)
    shared = {"fact": "shared fact", "refs": ["signals/x.view.txt:1"],
             "basis": "static-reference"}
    exact_copy = dict(shared)  # identical fact+refs+basis -> must collapse to one
    near_dup_refs = {"fact": "shared fact", "refs": ["signals/x.view.txt:2"],
                     "basis": "static-reference"}  # same fact, different ref -> kept
    near_dup_fact = {"fact": "shared fact, worded differently",
                     "refs": ["signals/x.view.txt:1"], "basis": "static-reference"}

    _lens_task(engine, "lens-1", [
        _finding("finding-a", evidence=[shared, near_dup_refs]),
        _finding("finding-b", evidence=[exact_copy, near_dup_fact]),
    ])
    merge_map = {
        "finding-a": {"status": "surviving", "absorbed_into": None, "reason": "primary"},
        "finding-b": {"status": "absorbed", "absorbed_into": "finding-a", "reason": "dup"},
    }
    rank = [{"finding_id": "finding-a", "reason": "only one"}]
    _dedup_task(engine, merge_map, rank)

    doc = assemble.assemble(tmp_path)
    (row,) = doc["findings"]
    # 4 input rows, 1 exact duplicate collapsed -> 3 survive, order preserved.
    assert row["evidence"] == [shared, near_dup_refs, near_dup_fact]


# --------------------------------------------------------------------------- #
# fail-closed conditions
# --------------------------------------------------------------------------- #

def test_no_dedup_output_fails_closed(tmp_path):
    engine = Engine(tmp_path)
    _lens_task(engine, "lens-1", [_finding("finding-a", evidence=[
        {"fact": "f", "refs": ["signals/x.view.txt:1"], "basis": "static-reference"}])])
    with pytest.raises(assemble.AssembleError, match="no validated dedup-rank"):
        assemble.assemble(tmp_path)


def test_multiple_dedup_outputs_fail_closed(tmp_path):
    engine = Engine(tmp_path)
    _lens_task(engine, "lens-1", [_finding("finding-a", evidence=[
        {"fact": "f", "refs": ["signals/x.view.txt:1"], "basis": "static-reference"}])])
    merge_map = {"finding-a": {"status": "surviving", "absorbed_into": None, "reason": "x"}}
    rank = [{"finding_id": "finding-a", "reason": "x"}]
    _dedup_task(engine, merge_map, rank, task_id="dedup-rank")
    _dedup_task(engine, merge_map, rank, task_id="dedup-rank-2")
    with pytest.raises(assemble.AssembleError, match="expected exactly one"):
        assemble.assemble(tmp_path)


def test_id_set_mismatch_fails_closed(tmp_path):
    engine = Engine(tmp_path)
    _lens_task(engine, "lens-1", [
        _finding("finding-a", evidence=[
            {"fact": "f", "refs": ["signals/x.view.txt:1"], "basis": "static-reference"}]),
        _finding("finding-b", evidence=[
            {"fact": "g", "refs": ["signals/y.view.txt:1"], "basis": "static-reference"}]),
    ])
    # merge_map only accounts for finding-a -- finding-b is in the pool but
    # missing from merge_map's declared universe.
    merge_map = {"finding-a": {"status": "surviving", "absorbed_into": None, "reason": "x"}}
    rank = [{"finding_id": "finding-a", "reason": "x"}]
    _dedup_task(engine, merge_map, rank)
    with pytest.raises(assemble.AssembleError, match="does not cover exactly"):
        assemble.assemble(tmp_path)


def test_duplicate_finding_id_across_lens_outputs_fails_closed(tmp_path):
    engine = Engine(tmp_path)
    ev = [{"fact": "f", "refs": ["signals/x.view.txt:1"], "basis": "static-reference"}]
    _lens_task(engine, "lens-1", [_finding("finding-collision", evidence=ev)])
    _lens_task(engine, "lens-2", [_finding("finding-collision", evidence=ev)])
    with pytest.raises(assemble.AssembleError, match="globally unique"):
        assemble.assemble(tmp_path)


# --------------------------------------------------------------------------- #
# output shape
# --------------------------------------------------------------------------- #

def test_output_passes_a_findings_py_shaped_structural_check(tmp_path):
    engine = Engine(tmp_path)
    _lens_task(engine, "lens-1", [
        _finding("finding-a", evidence=[
            {"fact": "f", "refs": ["signals/x.view.txt:1"], "basis": "static-reference"},
            {"fact": "g", "refs": ["signals/y.view.txt:2"], "basis": "declaration"}],
            affected_modules=["mc-a"]),
        _finding("finding-b", evidence=[
            {"fact": "h", "refs": ["signals/z.view.txt:3"], "basis": "history"}],
            affected_modules=["mc-b"]),
    ])
    merge_map = {
        "finding-a": {"status": "surviving", "absorbed_into": None, "reason": "primary"},
        "finding-b": {"status": "absorbed", "absorbed_into": "finding-a", "reason": "dup"},
    }
    rank = [{"finding_id": "finding-a", "reason": "only one"}]
    _dedup_task(engine, merge_map, rank)

    doc = assemble.assemble(tmp_path)
    assert doc["schema_version"] == findings.SCHEMA_VERSION
    assert isinstance(doc["findings"], list) and doc["findings"]
    # Reuse orchestrator/schemas.py's structural finding-shape check (the
    # same one lens-findings outputs are validated against) -- the exact
    # per-row shape findings.py expects, without findings.py's own run-dir-
    # dependent checks (finalized module ids, citation resolution against
    # real files), which do not apply until AFTER rekey-findings runs.
    wrapped = {"findings": doc["findings"], "coverage": []}
    assert schemas.validate_output("lens-findings", wrapped) == []
