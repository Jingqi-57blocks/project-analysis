"""Deterministic module-map.json writer tests (57B-113 / 57B-116, M2):
happy path, exactly-one enforcement, unknown-field/schema_version stamping,
and end-to-end acceptance by module_map.validate()/expand_candidate_rules()
(the existing finalize-module-map gate, unchanged) on a small fixture
universe."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper import identity, module_map
from analysis_wrapper.orchestrator import formation
from analysis_wrapper.orchestrator.composer import compose
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine, now_iso
from analysis_wrapper.targetspec import TargetSpec, stable_repo_id


def _build_run(tmp_path) -> Path:
    """A minimal prepared run dir: identity + targets.json +
    discovery-report.json + module-candidates.json (two candidates, one per
    repo) -- everything module_map.validate()/expand_candidate_rules() (via
    identity.load + _candidate_universe) need. No signals/synthesis-input
    needed for these tests."""
    workspace = tmp_path / "ws"
    api_root = workspace / "api"
    web_root = workspace / "web"
    (api_root / "internal").mkdir(parents=True)
    web_root.mkdir(parents=True)

    targets = {
        "schema_version": "3.0.0",
        "repos": [
            {"repo_id": "api-11111111", "path": str(api_root),
             "git": {"head": "a" * 40, "branch": "main", "commit_count": 1}},
            {"repo_id": "web-22222222", "path": str(web_root),
             "git": {"head": "", "branch": "", "commit_count": 0}},
        ],
    }
    run = tmp_path / "run"
    run.mkdir()
    (run / "targets.json").write_text(json.dumps(targets), "utf-8")
    spec = TargetSpec.from_dict(targets)
    project_id = stable_repo_id(str(workspace))
    mapping = identity.build(spec, workspace_root=workspace, project_id=project_id)
    identity.write_mapping(run, mapping)
    (run / "discovery-report.json").write_text(
        json.dumps({"project_ref": mapping.project.reference}), "utf-8")
    (run / "module-candidates.json").write_text(json.dumps({
        "schema_version": "3.0.0", "project_ref": mapping.project.reference,
        "candidate_count": 2,
        "candidates": [
            {"candidate_id": "mc-api-folder", "repository_ref": "api",
             "signal_kind": "folder", "value": "internal",
             "evidence": ["discovery-report.json:x"], "node_ids": []},
            {"candidate_id": "mc-web-folder", "repository_ref": "web",
             "signal_kind": "folder", "value": "internal",
             "evidence": ["discovery-report.json:y"], "node_ids": []},
        ],
    }), "utf-8")
    return run


def _register_and_validate(run: Path, task_id: str, output: dict, *,
                           task_type: str = "formation-proposal") -> None:
    engine = Engine(run)
    packets = compose(
        task_id=task_id, template_id="t", template_version="1", task_type=task_type,
        instructions="do it", inputs={"a": "x"},
        output_schema_id=f"{task_type}.v1", context_budget_tokens=8000)
    engine.create_tasks(packets)
    claimed = engine.claim(1, executor_kind="manual", model="test")
    assert claimed and claimed[0].packet.task_id == task_id
    item = claimed[0]
    at = now_iso()
    result = TaskResult(
        task_id=task_id, status="ok", output=output,
        executor=ExecutorInfo(kind="manual", model="test", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.1),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()),
        attempt=item.attempt)
    outcome = engine.submit(task_id, result.to_dict())
    assert outcome["status"] == "validated", outcome


_MODULES = [{"module_id": "core", "name": "Core", "classification": "business",
            "confidence": "medium", "aliases": []}]
_DISPOSITIONS = [
    {"candidate_id": "mc-api-folder", "disposition": "standalone",
     "module_ids": ["core"], "reason": "sole implementation of this capability"},
    {"candidate_id": "mc-web-folder", "disposition": "merged",
     "module_ids": ["core"], "reason": "shares the core capability's boundary"},
]


def test_write_module_map_writes_the_validated_formation_proposal_output(tmp_path):
    run = _build_run(tmp_path)
    _register_and_validate(run, "formation",
                          {"modules": _MODULES, "candidate_dispositions": _DISPOSITIONS})
    out_path = formation.write(run)
    assert out_path == run / "module-map.json"
    document = json.loads(out_path.read_text("utf-8"))
    assert document["schema_version"] == module_map.MAP_SCHEMA_VERSION
    assert document["modules"] == _MODULES
    assert document["candidate_dispositions"] == _DISPOSITIONS
    assert "candidate_rules" not in document
    assert "additional_candidates" not in document

    # The existing finalize-module-map gate (unchanged) accepts it outright.
    candidates_doc, module_doc = module_map.validate(run)
    assert candidates_doc["candidate_count"] == 2
    assert module_doc["modules"] == _MODULES


def test_write_module_map_defaults_to_the_run_dirs_canonical_path(tmp_path):
    run = _build_run(tmp_path)
    _register_and_validate(run, "formation",
                          {"modules": _MODULES, "candidate_dispositions": _DISPOSITIONS})
    formation.write(run)
    assert (run / "module-map.json").is_file()


def test_write_module_map_honors_an_out_override_without_touching_the_run_dir(tmp_path):
    run = _build_run(tmp_path)
    _register_and_validate(run, "formation",
                          {"modules": _MODULES, "candidate_dispositions": _DISPOSITIONS})
    custom_out = tmp_path / "inspect" / "module-map.json"
    custom_out.parent.mkdir()
    out_path = formation.write(run, out=custom_out)
    assert out_path == custom_out
    assert custom_out.is_file()
    assert not (run / "module-map.json").exists()


def test_write_module_map_expands_candidate_rules_via_the_unchanged_finalize_gate(tmp_path):
    run = _build_run(tmp_path)
    rules = [
        {"rule_id": "everything", "disposition": "standalone", "module_ids": ["core"],
         "reason": "single business capability", "selectors": [
            {"repository_refs": ["api", "web"]}]},
    ]
    _register_and_validate(run, "formation", {"modules": _MODULES, "candidate_rules": rules})
    formation.write(run)
    document = json.loads((run / "module-map.json").read_text("utf-8"))
    assert document["candidate_rules"] == rules
    assert "candidate_dispositions" not in document

    # expand_candidate_rules (existing, unchanged) turns the compact rule
    # into per-candidate dispositions covering the whole fixture universe --
    # the zero-omission/zero-overlap gate this fix must not disturb.
    module_map.expand_candidate_rules(run)
    candidates_doc, module_doc = module_map.validate(run)
    assert {row["candidate_id"] for row in module_doc["candidate_dispositions"]} == {
        "mc-api-folder", "mc-web-folder"}
    assert candidates_doc["candidate_count"] == 2


def test_write_module_map_stamps_canonical_schema_version_and_drops_unknown_fields(tmp_path):
    """A formation-proposal output that (accidentally or not) carries a
    spoofed schema_version or a stray unrecognized field must never leak
    into the canonical artifact -- schemas.py's formation-proposal validator
    does not reject unknown top-level keys, so this module is the one place
    that whitelists them."""
    run = _build_run(tmp_path)
    _register_and_validate(run, "formation", {
        "modules": _MODULES, "candidate_dispositions": _DISPOSITIONS,
        "schema_version": "not-the-real-one", "some_stray_debug_field": "leak-me-not",
    })
    out_path = formation.write(run)
    document = json.loads(out_path.read_text("utf-8"))
    assert document["schema_version"] == module_map.MAP_SCHEMA_VERSION
    assert "some_stray_debug_field" not in document
    assert set(document) == {"schema_version", "modules", "candidate_dispositions"}


def test_write_module_map_raises_when_no_formation_proposal_validated(tmp_path):
    run = _build_run(tmp_path)
    with pytest.raises(formation.FormationWriterError, match="no validated formation-proposal"):
        formation.write(run)


def test_write_module_map_raises_when_more_than_one_validated(tmp_path):
    run = _build_run(tmp_path)
    _register_and_validate(run, "formation-a",
                          {"modules": _MODULES, "candidate_dispositions": _DISPOSITIONS})
    _register_and_validate(run, "formation-b",
                          {"modules": _MODULES, "candidate_dispositions": _DISPOSITIONS})
    with pytest.raises(formation.FormationWriterError, match="expected exactly one"):
        formation.write(run)


def test_write_module_map_ignores_a_validated_boundary_resolution_task(tmp_path):
    """boundary-resolution stays a defined, distinct task type -- it must
    never be mistaken for a formation-proposal output even if one happens
    to validate in the same run."""
    run = _build_run(tmp_path)
    _register_and_validate(
        run, "some-boundary-task",
        {"dispositions": [{"candidate_id": "mc-api-folder", "disposition": "unresolved",
                           "module_ids": [], "reason": "unrelated task type"}]},
        task_type="boundary-resolution")
    with pytest.raises(formation.FormationWriterError, match="no validated formation-proposal"):
        formation.write(run)


def test_partition_plan_has_stable_local_ownership_and_explicit_cross_links(tmp_path):
    run = _build_run(tmp_path)
    candidates_path = run / "module-candidates.json"
    doc = json.loads(candidates_path.read_text("utf-8"))
    doc["candidates"].extend([
        {"candidate_id": "mc-api-route", "repository_ref": "api",
         "signal_kind": "route", "value": "GET /internal/jobs",
         "evidence": ["api@" + "a" * 40 + ":internal/jobs.go:1"], "node_ids": []},
        {"candidate_id": "mc-web-route", "repository_ref": "web",
         "signal_kind": "route", "value": "GET /portal/jobs",
         "evidence": ["web@NON-GIT:portal/jobs.js:1"], "node_ids": []},
    ])
    doc["candidate_count"] = len(doc["candidates"])
    candidates_path.write_text(json.dumps(doc), "utf-8")
    (run / "cohesion-bundle.json").write_text(json.dumps({
        "clusters": [
            {"kind": "import", "members": ["mc-api-folder", "mc-api-route"],
             "evidence_refs": ["api@" + "a" * 40 + ":internal/jobs.go:1"]},
            {"kind": "route-prefix", "members": ["mc-api-route", "mc-web-route"],
             "evidence_refs": ["signals/routes.view.txt:2"]},
        ],
        "kinds": {}, "limits": {},
    }), "utf-8")

    first = formation.build_partition_plan(run)
    second = formation.build_partition_plan(run)
    assert first == second
    candidate_ids = {row["candidate_id"] for row in doc["candidates"]}
    assert set(first["candidate_ownership"]) == candidate_ids
    assert first["merge_order"] == [row["partition_id"] for row in first["partitions"]]
    assert any(row["cross_repository"] for row in first["cross_links"])
    assert any({"mc-api-folder", "mc-api-route"} <= set(row["candidate_ids"])
               for row in first["partitions"])


def test_partitioned_formation_merges_a_cross_repository_module_in_plan_order(tmp_path):
    run = _build_run(tmp_path)
    (run / "cohesion-bundle.json").write_text(json.dumps({
        "clusters": [{
            "kind": "route-prefix", "members": ["mc-api-folder", "mc-web-folder"],
            "evidence_refs": ["signals/routes.view.txt:2"],
        }],
        "kinds": {}, "limits": {},
    }), "utf-8")
    plan = formation.build_partition_plan(run)
    formation.write_partition_plan(run)
    shared = [{"module_id": "shared-capability", "name": "Shared capability",
               "classification": "business", "confidence": "medium", "aliases": []}]
    for partition in plan["partitions"]:
        _register_and_validate(run, formation.formation_task_id(partition["partition_id"]), {
            "modules": shared,
            "candidate_dispositions": [{
                "candidate_id": candidate_id, "disposition": "merged",
                "module_ids": ["shared-capability"],
                "reason": "cross-repository cohesion evidence supports one boundary",
            } for candidate_id in partition["candidate_ids"]],
        })
    formation.write(run)
    _candidates, document = module_map.validate(run)
    assert document["modules"] == shared
    assert {row["module_ids"][0] for row in document["candidate_dispositions"]} == {
        "shared-capability"}


def test_partitioned_formation_conservatively_merges_shared_module_metadata(tmp_path):
    run = _build_run(tmp_path)
    (run / "cohesion-bundle.json").write_text(json.dumps({
        "clusters": [{
            "kind": "route-prefix", "members": ["mc-api-folder", "mc-web-folder"],
            "evidence_refs": ["signals/routes.view.txt:2"],
        }],
        "kinds": {}, "limits": {},
    }), "utf-8")
    plan = formation.build_partition_plan(run)
    formation.write_partition_plan(run)
    for index, partition in enumerate(plan["partitions"]):
        _register_and_validate(run, formation.formation_task_id(partition["partition_id"]), {
            "modules": [{
                "module_id": "shared-capability", "name": "Shared capability",
                "classification": "business",
                "confidence": ("high", "low")[index],
                "aliases": (["Order access"], ["Access orders"])[index],
            }],
            "candidate_dispositions": [{
                "candidate_id": candidate_id, "disposition": "merged",
                "module_ids": ["shared-capability"],
                "reason": "cross-repository cohesion evidence supports one boundary",
            } for candidate_id in partition["candidate_ids"]],
        })

    formation.write(run)

    document = json.loads((run / "module-map.json").read_text("utf-8"))
    assert document["modules"] == [{
        "module_id": "shared-capability", "name": "Shared capability",
        "classification": "business", "confidence": "low",
        "aliases": ["Access orders", "Order access"],
    }]


def test_partitioned_formation_still_rejects_competing_module_definitions(tmp_path):
    run = _build_run(tmp_path)
    (run / "cohesion-bundle.json").write_text(json.dumps({
        "clusters": [{
            "kind": "route-prefix", "members": ["mc-api-folder", "mc-web-folder"],
            "evidence_refs": ["signals/routes.view.txt:2"],
        }],
        "kinds": {}, "limits": {},
    }), "utf-8")
    plan = formation.build_partition_plan(run)
    formation.write_partition_plan(run)
    for index, partition in enumerate(plan["partitions"]):
        _register_and_validate(run, formation.formation_task_id(partition["partition_id"]), {
            "modules": [{
                "module_id": "shared-capability",
                "name": ("Shared capability", "Different capability")[index],
                "classification": "business", "confidence": "medium", "aliases": [],
            }],
            "candidate_dispositions": [{
                "candidate_id": candidate_id, "disposition": "merged",
                "module_ids": ["shared-capability"],
                "reason": "cross-repository cohesion evidence supports one boundary",
            } for candidate_id in partition["candidate_ids"]],
        })

    with pytest.raises(formation.FormationWriterError, match="conflicting definition"):
        formation.write(run)


def test_structural_quality_gate_requires_resolution_without_a_fixed_ratio(tmp_path):
    run = _build_run(tmp_path)
    _register_and_validate(run, "formation", {
        "modules": _MODULES,
        "candidate_dispositions": [
            {"candidate_id": "mc-api-folder", "disposition": "unresolved",
             "module_ids": [], "reason": "the first pass cannot justify ownership"},
            _DISPOSITIONS[1],
        ],
    })
    formation.write(run)
    module_map.expand_candidate_rules(run)
    module_map.validate(run)

    initial = formation.formation_quality(run, refined=False)
    assert initial["status"] == "blocked"
    assert initial["requires_boundary_resolution"] is True
    assert "unresolved_ratio" not in initial
    assert "max_unresolved_ratio" not in initial
    row = initial["unresolved"][0]
    assert row["candidate_id"] == "mc-api-folder"
    assert row["evidence_bearing"] is True
    assert "immediate_graph" in row and "prior_module_ids" in row

    refined = formation.formation_quality(run, refined=True)
    assert refined["status"] == "blocked"
    document = json.loads((run / "module-map.json").read_text("utf-8"))
    document["candidate_dispositions"][0].update({
        "evidence_refs": ["discovery-report.json:x"],
        "coverage_impact": "Coverage cannot claim a complete ownership boundary for this candidate.",
    })
    (run / "module-map.json").write_text(json.dumps(document), "utf-8")
    refined = formation.formation_quality(run, refined=True)
    assert refined["status"] == "passed"
    assert refined["authoritative"] is True


def test_apply_boundary_resolution_replaces_every_unresolved_candidate_once(tmp_path):
    run = _build_run(tmp_path)
    _register_and_validate(run, "formation", {
        "modules": _MODULES,
        "candidate_dispositions": [
            {"candidate_id": "mc-api-folder", "disposition": "unresolved",
             "module_ids": [], "reason": "first pass needs graph context"},
            _DISPOSITIONS[1],
        ],
    })
    formation.write(run)
    module_map.expand_candidate_rules(run)
    _register_and_validate(run, "boundary-resolution", {
        "dispositions": [{
            "candidate_id": "mc-api-folder", "disposition": "merged",
            "module_ids": ["core"], "reason": "targeted evidence shares the core boundary",
        }],
    }, task_type="boundary-resolution")
    assert formation.apply_boundary_resolution(run) is True
    _candidates, document = module_map.validate(run)
    api = next(row for row in document["candidate_dispositions"]
               if row["candidate_id"] == "mc-api-folder")
    assert api["disposition"] == "merged"
    assert api["module_ids"] == ["core"]


def test_apply_boundary_resolution_merges_disjoint_shards(tmp_path):
    run = _build_run(tmp_path)
    candidates_path = run / "module-candidates.json"
    candidates = json.loads(candidates_path.read_text("utf-8"))
    candidates["candidates"].append({
        "candidate_id": "mc-api-route", "repository_ref": "api",
        "signal_kind": "route", "value": "GET /internal/jobs",
        "evidence": ["api@" + "a" * 40 + ":internal/jobs.go:1"], "node_ids": [],
    })
    candidates["candidate_count"] = len(candidates["candidates"])
    candidates_path.write_text(json.dumps(candidates), "utf-8")
    _register_and_validate(run, "formation", {
        "modules": _MODULES,
        "candidate_dispositions": [
            {"candidate_id": "mc-api-folder", "disposition": "unresolved",
             "module_ids": [], "reason": "first pass needs graph context"},
            {"candidate_id": "mc-api-route", "disposition": "unresolved",
             "module_ids": [], "reason": "first pass needs graph context"},
            _DISPOSITIONS[1],
        ],
    })
    formation.write(run)
    module_map.expand_candidate_rules(run)
    _register_and_validate(run, "boundary-resolution-shard-0000", {
        "dispositions": [{
            "candidate_id": "mc-api-folder", "disposition": "merged",
            "module_ids": ["core"], "reason": "targeted evidence shares the core boundary",
        }],
    }, task_type="boundary-resolution")
    _register_and_validate(run, "boundary-resolution-shard-0001", {
        "dispositions": [{
            "candidate_id": "mc-api-route", "disposition": "merged",
            "module_ids": ["core"], "reason": "targeted evidence shares the core boundary",
        }],
    }, task_type="boundary-resolution")
    assert formation.apply_boundary_resolution(run) is True
    _candidates, document = module_map.validate(run)
    assert {row["disposition"] for row in document["candidate_dispositions"]} == {"merged"}
