"""Deterministic finding re-key (57B-116, M2) — domain-neutral fixtures only."""

import json

import pytest

from analysis_wrapper import module_map
from analysis_wrapper.cli import main
from analysis_wrapper.orchestrator import rekey
from analysis_wrapper.orchestrator.composer import compose
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine, now_iso
from analysis_wrapper.system_model import assemble as sm
from system_model_fixtures import write_run


def _built_run(tmp_path):
    run = write_run(tmp_path / "run", with_imports=True)
    model = sm.assemble(run)
    sm.dump(model, run)
    module_map.write_candidates(run, model.to_dict())
    return run


def _module_map_with_one_excluded(run):
    """Every candidate dispositioned 'merged' into one module except the
    last (sorted) candidate_id, which is 'excluded' -- gives a resolvable
    id and a dead-end id to route findings against."""
    candidates = json.loads((run / "module-candidates.json").read_text())["candidates"]
    ids = sorted(row["candidate_id"] for row in candidates)
    excluded_id = ids[-1]
    merged_ids = ids[:-1]
    payload = {
        "schema_version": module_map.MAP_SCHEMA_VERSION,
        "modules": [{"module_id": "sample-capability", "name": "Sample capability",
                     "classification": "business", "confidence": "medium", "aliases": []}],
        "candidate_dispositions": (
            [{"candidate_id": cid, "disposition": "merged",
              "module_ids": ["sample-capability"], "reason": "fixture boundary"}
             for cid in merged_ids]
            + [{"candidate_id": excluded_id, "disposition": "excluded",
                "module_ids": [], "reason": "not a real boundary"}]
        ),
    }
    (run / "module-map.json").write_text(json.dumps(payload), "utf-8")
    return merged_ids, excluded_id


def _register_finding_resolution(run, output):
    engine = Engine(run)
    packets = compose(
        task_id="finding-resolution", template_id="finding-resolution", template_version="1",
        task_type="finding-resolution", instructions="resolve", inputs={"tail": "[]"},
        output_schema_id="finding-resolution.v1", context_budget_tokens=8000,
    )
    engine.create_tasks(packets)
    item = engine.claim(1, executor_kind="manual", model="test")[0]
    at = now_iso()
    result = TaskResult(
        task_id=item.packet.task_id, status="ok", output=output,
        executor=ExecutorInfo(kind="manual", model="test", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.1), tokens=None,
        validation=ValidationOutcome(passed=True, failures=()), attempt=item.attempt,
    )
    outcome = engine.submit(item.packet.task_id, result.to_dict())
    assert outcome["status"] == "validated", outcome


def test_rekey_maps_resolvable_candidates_and_routes_dead_ends_to_tail(tmp_path):
    run = _built_run(tmp_path)
    merged_ids, excluded_id = _module_map_with_one_excluded(run)

    findings_doc = {"findings": [
        {"finding_id": "finding-resolved", "affected_modules": [merged_ids[0]]},
        {"finding_id": "finding-partial", "affected_modules": [merged_ids[0], excluded_id]},
        {"finding_id": "finding-dead-end", "affected_modules": [excluded_id]},
        {"finding_id": "finding-unknown", "affected_modules": ["mc-does-not-exist"]},
    ]}

    result = rekey.rekey(run, findings_doc)
    rekeyed_by_id = {row["finding_id"]: row for row in result["rekeyed"]}
    tail_by_id = {row["finding_id"]: row for row in result["tail"]}

    assert set(rekeyed_by_id) == {"finding-resolved", "finding-partial"}
    assert set(tail_by_id) == {"finding-dead-end", "finding-unknown"}
    assert rekeyed_by_id["finding-resolved"]["affected_modules"] == ["sample-capability"]
    # A finding whose candidates are a MIX of resolvable and dead-end still
    # rekeys, using only what resolved -- never guessed onto a neighbor.
    assert rekeyed_by_id["finding-partial"]["affected_modules"] == ["sample-capability"]
    assert tail_by_id["finding-dead-end"]["candidate_dispositions"] == {excluded_id: "excluded"}
    assert tail_by_id["finding-unknown"]["candidate_dispositions"] == {
        "mc-does-not-exist": "unknown-candidate"}
    assert len(result["rekeyed"]) + len(result["tail"]) == len(findings_doc["findings"])


def test_rekey_rejects_duplicate_finding_ids(tmp_path):
    run = _built_run(tmp_path)
    merged_ids, _excluded_id = _module_map_with_one_excluded(run)
    findings_doc = {"findings": [
        {"finding_id": "finding-a", "affected_modules": [merged_ids[0]]},
        {"finding_id": "finding-a", "affected_modules": [merged_ids[0]]},
    ]}
    with pytest.raises(ValueError, match="duplicate finding_id"):
        rekey.rekey(run, findings_doc)


def test_rekey_rejects_malformed_input(tmp_path):
    run = _built_run(tmp_path)
    _module_map_with_one_excluded(run)
    with pytest.raises(ValueError, match="JSON object"):
        rekey.rekey(run, [])
    with pytest.raises(ValueError, match="'findings' list"):
        rekey.rekey(run, {"findings": "nope"})
    with pytest.raises(ValueError, match="must be an object"):
        rekey.rekey(run, {"findings": ["not-a-dict"]})
    with pytest.raises(ValueError, match="finding_id must be"):
        rekey.rekey(run, {"findings": [{"affected_modules": ["x"]}]})
    with pytest.raises(ValueError, match="affected_modules must be"):
        rekey.rekey(run, {"findings": [{"finding_id": "finding-a", "affected_modules": []}]})


def test_rekey_fails_closed_when_module_map_is_incomplete(tmp_path):
    run = _built_run(tmp_path)
    _module_map_with_one_excluded(run)
    mapping = json.loads((run / "module-map.json").read_text())
    mapping["candidate_dispositions"].pop()
    (run / "module-map.json").write_text(json.dumps(mapping), "utf-8")
    with pytest.raises(ValueError, match="omits"):
        rekey.rekey(run, {"findings": []})


def test_rekey_resolution_preserves_assigned_findings_and_records_every_nonassigned_tail(tmp_path):
    run = _built_run(tmp_path)
    merged_ids, excluded_id = _module_map_with_one_excluded(run)
    result = rekey.rekey(run, {"findings": [
        {"finding_id": "finding-assign", "affected_modules": [excluded_id], "claim": "assign"},
        {"finding_id": "finding-unsupported", "affected_modules": ["mc-missing"], "claim": "keep"},
    ]})
    assert not result["rekeyed"] and len(result["tail"]) == 2
    _register_finding_resolution(run, {"dispositions": [
        {"finding_id": "finding-assign", "disposition": "assigned",
         "affected_modules": ["sample-capability"], "reason": "nearest supported boundary",
         "evidence_refs": ["metric:code.analyzed-scope.total"]},
        {"finding_id": "finding-unsupported", "disposition": "unsupported",
         "affected_modules": [], "reason": "no supported module mapping",
         "evidence_refs": ["metric:code.analyzed-scope.total"]},
    ]})

    resolved = rekey.apply_resolution(run, result["tail"])
    assert resolved["assigned"] == [{
        "finding_id": "finding-assign", "affected_modules": ["sample-capability"], "claim": "assign",
    }]
    assert resolved["remainder"] == [{
        "finding_id": "finding-unsupported", "disposition": "unsupported",
        "reason": "no supported module mapping",
        "evidence_refs": ["metric:code.analyzed-scope.total"],
        "candidate_dispositions": {"mc-missing": "unknown-candidate"},
    }]


def test_rekey_findings_cli_writes_result_file(tmp_path):
    run = _built_run(tmp_path)
    merged_ids, excluded_id = _module_map_with_one_excluded(run)
    findings_path = tmp_path / "in-findings.json"
    findings_path.write_text(json.dumps({"findings": [
        {"finding_id": "finding-resolved", "affected_modules": [merged_ids[0]]},
        {"finding_id": "finding-dead-end", "affected_modules": [excluded_id]},
    ]}), "utf-8")
    out_path = tmp_path / "out.json"
    assert main(["rekey-findings", "--run", str(run), "--in", str(findings_path),
                "--out", str(out_path)]) == 0
    result = json.loads(out_path.read_text())
    assert len(result["rekeyed"]) == 1
    assert len(result["tail"]) == 1
