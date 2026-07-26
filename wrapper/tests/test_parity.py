"""57B-86: the dev-only deterministic parity comparator.

Fixtures here build minimal-but-complete run directories by hand (the
comparator tolerates missing artifacts, so most tests only need the handful
of files relevant to what they're checking). One fuller test builds a real
system-model.json pair through ``system_model_fixtures.write_run`` +
``system_model.assemble`` instead of a hand-written one.
"""

import json
from pathlib import Path

from analysis_wrapper import parity
from analysis_wrapper.cli import main
from analysis_wrapper.system_model import assemble as sm_assemble
from system_model_fixtures import write_run

_HEAD = "b" * 40


def _write(run: Path, relpath: str, obj) -> None:
    path = run / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", "utf-8")


def _write_text(run: Path, relpath: str, text: str) -> None:
    path = run / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def _capabilities(*, scan_date="2026-07-23", status="complete", details=None):
    return {
        "schema_version": "2.0.0", "project_ref": "proj", "scan_date": scan_date,
        "aggregate_status": "complete",
        "capabilities": [{
            "capability_id": "callgraph", "applicable": True, "status": status,
            "reason": "", "expected_artifacts": ["callgraph-coverage.json"],
            "observed_artifacts": ["callgraph-coverage.json"], "missing_artifacts": [],
            "details": (details if details is not None else
                        [{"repository_ref": "api", "status": "complete"}]),
        }],
    }


def _evidence_catalog():
    return {
        "schema_version": "1.0.0", "project_ref": "proj",
        "capabilities": {
            "callgraph": {
                "total_count": 1, "included_count": 1, "truncated": False,
                "items": [{
                    "capability_id": "callgraph", "provider_id": "prov", "scope": "api",
                    "coverage": {"applicability": "applicable", "status": "complete",
                                "reason_code": "ok", "detail": ""},
                    "facet_provenance": [],
                    "facts": {"total_count": 1, "included_count": 1, "truncated": False,
                             "items": [{"fact_id": "fact:1", "kind": "observation",
                                       "data": {}, "source_refs": []}]},
                    "source_refs": {"total_count": 0, "included_count": 0,
                                   "truncated": False, "items": []},
                    "artifacts": [],
                }],
            },
        },
    }


def _system_model(*, generator="analysis-system-model/0.4.0", scan_date="2026-07-23"):
    return {
        "schema_version": "2.0.0", "generator": generator, "scan_date": scan_date,
        "project_ref": "proj", "stats": {},
        "coverage": {
            "repositories": {
                "status": "complete", "producers": ["discovery"],
                "node_kinds": ["repository"], "edge_types": ["containment"],
                "counts": {"repositories": 1}, "caps": [],
                "source_universe": "every top-level target repo", "unresolved": {},
                "notes": [],
            },
        },
        "nodes": [{
            "id": "repo:api", "kind": "repository", "label": "api", "status": "observed",
            "repository_ref": "api", "key": ["api"], "producers": ["discovery"],
            "evidence": [], "evidence_basis": "static-reference", "attrs": {},
        }],
        "edges": [],
    }


def _callgraph_coverage(*, tool_version="v1"):
    return {
        "schema_version": "2.0.0", "scan_date": "2026-07-23", "determinism": "x",
        "repos": [{
            "repository_ref": "api", "lang": "go", "status": "complete",
            "tool": "callgraph", "tool_version": tool_version, "algorithm": "vta",
            "warm_cache": "n/a", "reason": "", "candidates_by_ext": {},
            "analyzed_by_ext": {}, "excluded_by_reason": {}, "parse_load_failures": 0,
            "call_sites": {"resolved": 0, "ambiguous": 0, "external": 0,
                          "unresolved": 0, "total": 0},
            "edges_emitted": 0, "notes": "",
        }],
    }


def _signals(*, status="complete"):
    return {
        "schema_version": "2.0.0", "aggregate_status": status,
        "signals": [{"tool": "scc", "repository_ref": "api", "status": status,
                    "reason": "", "view": "x.view.txt", "manifest": "x.manifest.json"}],
    }


def _discovery(*, workspace_root="/ws-a", not_targeted=None, facet_state="resolved"):
    return {
        "schema_version": "2.0.0", "project_ref": "proj", "workspace_root": workspace_root,
        "repos": [{
            "repository_ref": "api",
            "provenance": {"is_git": True, "head": "a" * 40,
                          "path": workspace_root + "/api"},
            "stacks": {"stacks": ["go"], "frameworks": [], "analysis_roots": [],
                      "evidence": []},
            "technology_facets": [{
                "profile_id": "language.go", "kind": "language", "scope_roots": ["."],
                "evidence": ["go.mod"], "confidence": "high", "state": facet_state,
            }],
            "unclassified_file_inventory": [], "technology_detection_notes": [],
            "package_manager": {"name": "go", "lockfile": "", "evidence": ""},
            "tier2_exclusions": {"dirs": [], "evidence": ""},
            "module_signals": {"folders": [], "routes": [], "tables": [],
                              "api_configs": [], "notes": []},
            "candidate_notes": [],
            "integration_evidence": {"available": True, "notes": []},
            "access_model": {"available": True, "notes": []},
            "notes": [],
        }],
        "not_targeted": sorted(not_targeted or []), "reduced_coverage_targets": [],
        "integration_candidate_count": 0,
    }


def _provenance(*, analyzer_root="/analyzer-a", analyzer_version="0.4.0",
               analyzer_head="a" * 40, target_head=_HEAD):
    return {
        "schema_version": 1, "analyzed_at": "2026-07-23T00:00:00+00:00",
        "analyzer": {"package": "analysis-wrapper", "version": analyzer_version,
                    "root": analyzer_root, "git_head": analyzer_head,
                    "git_branch": "main", "dirty_detail": "no",
                    "source_state_sha256": "x"},
        "targets": [{"repo_id": "api-11111111", "path": "/ws-a/api", "head": target_head,
                    "branch": "main", "dirty_detail": "no", "state": "git",
                    "source_state_sha256": ""}],
        "generation": {"language": "en", "model": "unknown", "effort": "unknown"},
        "preparation": {"scan_date": "2026-07-23", "history_since": "2024-01-01",
                        "coupling_sample_cap": 0, "network_authorized": False,
                        "allowed_hosts": []},
        "tool_versions": [],
    }


def _identity_map(*, canonical_path="/ws-a"):
    return {
        "schema_version": 1, "source": "native",
        "project": {"internal_id": "proj-id", "display_name": "proj", "reference": "proj",
                   "artifact_key": "proj", "canonical_path": canonical_path},
        "repositories": [{
            "internal_id": "api-11111111", "display_name": "api", "reference": "api",
            "artifact_key": "api", "workspace_relative_path": "api",
            "canonical_path": canonical_path + "/api",
        }],
    }


def _provider_execution(*, network_authorized=False):
    return {
        "schema_version": "1.0.0", "scan_date": "2026-07-23",
        "network_authorized": network_authorized,
        "executions": [{
            "provider_id": "prov", "capability_id": "callgraph", "repository_ref": "api",
            "matched_profiles": ["language.go"], "outcome": "completed", "reason": "",
            "coverage": {"applicability": "applicable", "status": "complete",
                        "reason_code": "ok"},
            "tools": [],
        }],
    }


def _write_minimal_run(run: Path, **kwargs) -> Path:
    run.mkdir(parents=True, exist_ok=True)
    workspace_root = kwargs.get("workspace_root", "/ws-a")
    _write(run, "capabilities.json", _capabilities(
        scan_date=kwargs.get("scan_date", "2026-07-23"),
        status=kwargs.get("capability_status", "complete"),
        details=kwargs.get("capability_details")))
    _write(run, "evidence-catalog.json", _evidence_catalog())
    _write(run, "system-model.json", _system_model(
        generator=kwargs.get("generator", "analysis-system-model/0.4.0"),
        scan_date=kwargs.get("scan_date", "2026-07-23")))
    _write(run, "callgraph-coverage.json",
          _callgraph_coverage(tool_version=kwargs.get("tool_version", "v1")))
    _write(run, "signals/run-summary.json", _signals())
    _write(run, "discovery-report.json", _discovery(
        workspace_root=workspace_root, not_targeted=kwargs.get("not_targeted"),
        facet_state=kwargs.get("facet_state", "resolved")))
    if kwargs.get("route_inventory") is not None:
        # 57B-84 B2: route_inventory lives in its own routes.emit.assemble
        # run-level artifact now, not an embedded discovery-report field.
        _write(run, "routes/route-inventory.json", kwargs["route_inventory"])
    _write(run, "run-provenance.json", _provenance(
        analyzer_root=kwargs.get("analyzer_root", "/analyzer-a"),
        analyzer_version=kwargs.get("analyzer_version", "0.4.0"),
        target_head=kwargs.get("head", _HEAD)))
    _write(run, "identity-map.json", _identity_map(canonical_path=workspace_root))
    _write(run, "provider-execution.json", _provider_execution())
    return run


# ---------------------------------------------------------------------------
# 1. Identical runs.
# ---------------------------------------------------------------------------


def test_identical_minimal_runs_have_zero_semantic_differences(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")

    report = parity.compare(a, b)

    assert not parity.has_semantic_differences(report)
    assert json.dumps(report, sort_keys=True) == json.dumps(
        parity.compare(a, b), sort_keys=True)


# ---------------------------------------------------------------------------
# 2. Noise-only pair.
# ---------------------------------------------------------------------------


def test_noise_only_differences_are_fully_normalized(tmp_path):
    a = _write_minimal_run(
        tmp_path / "a", scan_date="2026-01-01", workspace_root="/ws-a",
        analyzer_root="/analyzer-a", analyzer_version="0.4.0",
        generator="analysis-system-model/0.4.0", tool_version="v1")
    b = _write_minimal_run(
        tmp_path / "b", scan_date="2026-07-23", workspace_root="/ws-b",
        analyzer_root="/analyzer-b", analyzer_version="0.5.0",
        generator="analysis-system-model/0.5.0", tool_version="v2")

    report = parity.compare(a, b)

    assert not parity.has_semantic_differences(report), report["sections"]
    assert report["baseline"]["base"]["identity"]["analyzer"]["version"] == "0.4.0"
    assert report["baseline"]["candidate"]["identity"]["analyzer"]["version"] == "0.5.0"
    assert report["baseline"]["base"]["system_model_generator"] == \
        "analysis-system-model/0.4.0"
    assert report["tool_drift"], "differing tool_version must surface as tool drift"
    assert not report["warnings"]


# ---------------------------------------------------------------------------
# 3. Seeded mutations.
# ---------------------------------------------------------------------------


def test_capability_status_change_is_reclassified(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b", capability_status="partial")

    report = parity.compare(a, b)

    changed = report["sections"]["capability_records"]["changed"]
    assert len(changed) == 1
    assert changed[0]["key"] == "callgraph"
    assert changed[0]["reclassified"] is True


def test_capability_detail_row_removal_surfaces_as_a_changed_capability_entry(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b", capability_details=[])

    report = parity.compare(a, b)

    section = report["sections"]["capability_records"]
    assert section["removed"] == []
    assert len(section["changed"]) == 1
    entry = section["changed"][0]
    assert entry["key"] == "callgraph"
    assert entry["base"]["details"] == [{"repository_ref": "api", "status": "complete"}]
    assert entry["candidate"]["details"] == []
    # Details are not separately keyed (only capability_id is): a removed
    # detail row surfaces as a "changed" entry on its owning capability, not
    # as a standalone "removed" entry.
    assert entry["reclassified"] is False


def test_system_model_node_added_surfaces_as_added(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    sm = json.loads((b_run / "system-model.json").read_text("utf-8"))
    sm["nodes"].append({
        "id": "repo:web", "kind": "repository", "label": "web", "status": "observed",
        "repository_ref": "web", "key": ["web"], "producers": ["discovery"],
        "evidence": [], "evidence_basis": "static-reference", "attrs": {},
    })
    _write(b_run, "system-model.json", sm)

    report = parity.compare(a, b_run)

    section = report["sections"]["system_model_nodes"]
    assert section["removed"] == [] and section["changed"] == []
    assert len(section["added"]) == 1
    assert section["added"][0]["key"] == "repository / web"


def test_system_model_node_status_change_is_reclassified(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    sm = json.loads((b_run / "system-model.json").read_text("utf-8"))
    sm["nodes"][0]["status"] = "inferred"
    _write(b_run, "system-model.json", sm)

    report = parity.compare(a, b_run)

    changed = report["sections"]["system_model_nodes"]["changed"]
    assert len(changed) == 1
    assert changed[0]["key"] == "repository / api"
    assert changed[0]["reclassified"] is True


def _model_with_edge(dst_id: str) -> dict:
    model = _system_model()
    for repo_id, ref in (("repo:web", "web"), ("repo:worker", "worker")):
        model["nodes"].append({
            "id": repo_id, "kind": "repository", "label": ref, "status": "observed",
            "repository_ref": ref, "key": [ref], "producers": ["discovery"],
            "evidence": [], "evidence_basis": "static-reference", "attrs": {},
        })
    model["edges"] = [{
        "id": "edge:1", "type": "containment", "src": "repo:api", "dst": dst_id,
        "status": "observed", "producer": "discovery", "evidence": [],
        "evidence_basis": "static-reference", "attrs": {},
    }]
    return model


def test_system_model_edge_endpoint_change_is_a_remove_add_pair(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    _write(a, "system-model.json", _model_with_edge("repo:web"))
    _write(b, "system-model.json", _model_with_edge("repo:worker"))

    report = parity.compare(a, b)

    section = report["sections"]["system_model_edges"]
    assert section["changed"] == []
    assert len(section["removed"]) == 1 and len(section["added"]) == 1
    assert "web" in section["removed"][0]["key"]
    assert "worker" in section["added"][0]["key"]


def test_system_model_partition_status_change_is_reclassified(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    sm = json.loads((b_run / "system-model.json").read_text("utf-8"))
    sm["coverage"]["repositories"]["status"] = "partial"
    _write(b_run, "system-model.json", sm)

    report = parity.compare(a, b_run)

    changed = report["sections"]["system_model_partitions"]["changed"]
    assert len(changed) == 1
    assert changed[0]["key"] == "repositories"
    assert changed[0]["reclassified"] is True


def test_evidence_catalog_fact_kind_change_is_conflicting_not_reclassified(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    doc = json.loads((b_run / "evidence-catalog.json").read_text("utf-8"))
    doc["capabilities"]["callgraph"]["items"][0]["facts"]["items"][0]["kind"] = "different"
    _write(b_run, "evidence-catalog.json", doc)

    report = parity.compare(a, b_run)

    changed = report["sections"]["evidence_catalog"]["changed"]
    fact_changes = [row for row in changed if row["key"].endswith("/ fact:1")]
    assert len(fact_changes) == 1
    assert fact_changes[0]["reclassified"] is False


def test_discovery_facet_state_change_is_reclassified(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b", facet_state="conflicting")

    report = parity.compare(a, b)

    changed = report["sections"]["discovery_facets"]["changed"]
    assert len(changed) == 1
    assert changed[0]["reclassified"] is True


def test_signal_status_change_is_reclassified(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    doc = json.loads((b_run / "signals" / "run-summary.json").read_text("utf-8"))
    doc["signals"][0]["status"] = "partial"
    _write(b_run, "signals/run-summary.json", doc)

    report = parity.compare(a, b_run)

    changed = report["sections"]["signals"]["changed"]
    assert len(changed) == 1
    assert changed[0]["reclassified"] is True


def test_prose_not_targeted_addition_is_scrubbed_of_machine_local_paths(tmp_path):
    a = _write_minimal_run(tmp_path / "a", workspace_root="/ws-a")
    b = _write_minimal_run(
        tmp_path / "b", workspace_root="/ws-b",
        not_targeted=["/ws-b/vendor (excluded by operator flag)"])

    report = parity.compare(a, b)

    rows = report["prose"]["not_targeted"]
    assert rows["removed"] == []
    assert len(rows["added"]) == 1
    assert rows["added"][0] == "$WORKSPACE/vendor (excluded by operator flag)"


# ---------------------------------------------------------------------------
# 4. One-sided artifact absence.
# ---------------------------------------------------------------------------


def test_one_sided_evidence_catalog_absence_is_a_counted_disclosure(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    (b_run / "evidence-catalog.json").unlink()

    report = parity.compare(a, b_run)

    section = report["sections"]["evidence_catalog"]
    assert section["base_present"] is True
    assert section["candidate_present"] is False
    assert parity.has_semantic_differences(report)


# ---------------------------------------------------------------------------
# 5. Targets differ.
# ---------------------------------------------------------------------------


def test_differing_target_heads_produce_a_targets_differ_warning(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    provenance = json.loads((b_run / "run-provenance.json").read_text("utf-8"))
    provenance["targets"][0]["head"] = "c" * 40
    _write(b_run, "run-provenance.json", provenance)

    report = parity.compare(a, b_run)

    assert any("TARGETS DIFFER" in warning for warning in report["warnings"])
    # The comparison still proceeded (sections are still fully populated).
    assert set(report["sections"]) == {
        "capability_records", "evidence_catalog", "system_model_nodes",
        "system_model_edges", "system_model_partitions", "lane_coverage",
        "signals", "discovery_facets", "discovery_evidence", "provider_execution",
    }


# ---------------------------------------------------------------------------
# 6. CLI.
# ---------------------------------------------------------------------------


def test_cli_compare_runs_exit_codes_and_report_file(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    assert main(["compare-runs", str(a), str(b)]) == 0

    b_mutated = _write_minimal_run(tmp_path / "b-mutated", capability_status="partial")
    report_path = tmp_path / "report.json"
    assert main([
        "compare-runs", str(a), str(b_mutated), "--report", str(report_path),
    ]) == 3
    written = json.loads(report_path.read_text("utf-8"))
    assert written["schema_version"] == parity.SCHEMA_VERSION
    assert json.dumps(written, sort_keys=True) == json.dumps(
        parity.compare(a, b_mutated), sort_keys=True)

    missing = tmp_path / "does-not-exist"
    assert main(["compare-runs", str(a), str(missing)]) == 2


# ---------------------------------------------------------------------------
# 7. A real system-model.json pair via the actual assembler.
# ---------------------------------------------------------------------------


def test_real_system_model_pair_has_zero_differences_until_mutated(tmp_path):
    run_a = write_run(tmp_path / "run-a")
    run_b = write_run(tmp_path / "run-b")
    sm_assemble.dump(sm_assemble.assemble(run_a), run_a)
    sm_assemble.dump(sm_assemble.assemble(run_b), run_b)

    report = parity.compare(run_a, run_b)
    for name in ("system_model_nodes", "system_model_edges", "system_model_partitions"):
        section = report["sections"][name]
        assert not section["added"] and not section["removed"] and not section["changed"]

    doc = json.loads((run_b / "system-model.json").read_text("utf-8"))
    repo_nodes = [node for node in doc["nodes"] if node["kind"] == "repository"]
    assert repo_nodes
    repo_nodes[0]["status"] = "inferred"
    (run_b / "system-model.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", "utf-8")

    mutated = parity.compare(run_a, run_b)
    changed = mutated["sections"]["system_model_nodes"]["changed"]
    assert len(changed) == 1
    assert changed[0]["reclassified"] is True


# ---------------------------------------------------------------------------
# 8. Review-blind-spot regressions (B1, B1-adjacent, B2, N1, N3, N4).
# ---------------------------------------------------------------------------


def test_evidence_catalog_fact_data_change_is_conflicting_not_reclassified(tmp_path):
    """B1: fact_id only hashes (capability_id, repo_id, kind, natural_key), so
    a fact keeping the same fact_id/kind but different `data` must NOT compare
    clean."""
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    doc = json.loads((b_run / "evidence-catalog.json").read_text("utf-8"))
    doc["capabilities"]["callgraph"]["items"][0]["facts"]["items"][0]["data"] = {
        "observed": False}
    _write(b_run, "evidence-catalog.json", doc)

    report = parity.compare(a, b_run)

    changed = report["sections"]["evidence_catalog"]["changed"]
    fact_changes = [row for row in changed if row["key"].endswith("/ fact:1")]
    assert len(fact_changes) == 1
    assert fact_changes[0]["reclassified"] is False


def test_evidence_catalog_fact_source_refs_change_is_a_changed_entry(tmp_path):
    """B1: a different citation for the same fact_id/kind must also surface."""
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    doc = json.loads((b_run / "evidence-catalog.json").read_text("utf-8"))
    doc["capabilities"]["callgraph"]["items"][0]["facts"]["items"][0]["source_refs"] = [
        "api@NON-GIT:internal/handlers/foo.go:1"]
    _write(b_run, "evidence-catalog.json", doc)

    report = parity.compare(a, b_run)

    changed = report["sections"]["evidence_catalog"]["changed"]
    fact_changes = [row for row in changed if row["key"].endswith("/ fact:1")]
    assert len(fact_changes) == 1
    assert fact_changes[0]["reclassified"] is False


def test_system_model_node_attrs_change_is_a_changed_entry(tmp_path):
    """B1-adjacent: node values must include attrs, not just status."""
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    sm = json.loads((b_run / "system-model.json").read_text("utf-8"))
    sm["nodes"][0]["attrs"] = {"stacks": ["go"]}
    _write(b_run, "system-model.json", sm)

    report = parity.compare(a, b_run)

    changed = report["sections"]["system_model_nodes"]["changed"]
    assert len(changed) == 1
    assert changed[0]["key"] == "repository / api"
    assert changed[0]["reclassified"] is False  # "attrs" is not an outcome field


def test_node_producers_difference_alone_has_zero_semantic_differences(tmp_path):
    """B1-adjacent: producers legitimately churn (e.g. a capability migrating
    onto a provider) and must never read as fact drift."""
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    sm = json.loads((b_run / "system-model.json").read_text("utf-8"))
    sm["nodes"][0]["producers"] = ["discovery", "capability-provider"]
    _write(b_run, "system-model.json", sm)

    report = parity.compare(a, b_run)

    assert not parity.has_semantic_differences(report)


def _model_with_parallel_data_edges(*, include_write: bool = True,
                                    write_status: str = "observed") -> dict:
    model = _system_model()
    model["nodes"].append({
        "id": "file:api/model/user.go", "kind": "file", "label": "user.go",
        "status": "observed", "repository_ref": "api",
        "key": ["api", "model/user.go"], "producers": ["discovery"],
        "evidence": [], "evidence_basis": "static-reference", "attrs": {},
    })
    model["nodes"].append({
        "id": "data:users", "kind": "data-store", "label": "users",
        "status": "observed", "repository_ref": "api", "key": ["api", "users"],
        "producers": ["discovery/tables"], "evidence": [],
        "evidence_basis": "static-reference", "attrs": {},
    })
    edges = [{
        "id": "edge:read-1", "type": "data", "src": "file:api/model/user.go",
        "dst": "data:users", "status": "observed", "producer": "discovery/tables",
        "evidence": [], "evidence_basis": "static-reference", "attrs": {"access": "read"},
    }]
    if include_write:
        edges.append({
            "id": "edge:write-1", "type": "data", "src": "file:api/model/user.go",
            "dst": "data:users", "status": write_status, "producer": "discovery/tables",
            "evidence": [], "evidence_basis": "static-reference",
            "attrs": {"access": "write"},
        })
    model["edges"] = edges
    return model


def test_parallel_data_edge_dropped_is_a_removed_entry_not_collapsed(tmp_path):
    """B2: (type, src, dst) alone collapses the read/write parallel edges into
    one key — dropping the write edge must surface as a removed entry, not
    disappear into a merge with the surviving read edge."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_minimal_run(a)
    _write_minimal_run(b)
    _write(a, "system-model.json", _model_with_parallel_data_edges(include_write=True))
    _write(b, "system-model.json", _model_with_parallel_data_edges(include_write=False))

    report = parity.compare(a, b)

    section = report["sections"]["system_model_edges"]
    assert section["changed"] == [] and section["added"] == []
    assert len(section["removed"]) == 1
    assert "edge:write-1" in section["removed"][0]["key"]


def test_parallel_data_edge_status_flip_is_reclassified(tmp_path):
    """B2: an observed->inferred flip on ONE of two parallel edges sharing
    (type, src, dst) must surface distinctly from its sibling."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_minimal_run(a)
    _write_minimal_run(b)
    _write(a, "system-model.json",
          _model_with_parallel_data_edges(write_status="observed"))
    _write(b, "system-model.json",
          _model_with_parallel_data_edges(write_status="inferred"))

    report = parity.compare(a, b)

    section = report["sections"]["system_model_edges"]
    assert section["added"] == [] and section["removed"] == []
    assert len(section["changed"]) == 1
    assert "edge:write-1" in section["changed"][0]["key"]
    assert section["changed"][0]["reclassified"] is True


def test_offsetting_edge_add_and_drop_are_both_itemized(tmp_path):
    """A net-zero edge count (one added, one removed) must never collapse
    into "0 differences" — both sides are itemized."""
    def _model(edge_id: str) -> dict:
        model = _system_model()
        model["nodes"].append({
            "id": "repo:web", "kind": "repository", "label": "web", "status": "observed",
            "repository_ref": "web", "key": ["web"], "producers": ["discovery"],
            "evidence": [], "evidence_basis": "static-reference", "attrs": {},
        })
        model["edges"] = [{
            "id": edge_id, "type": "containment", "src": "repo:api", "dst": "repo:web",
            "status": "observed", "producer": "discovery", "evidence": [],
            "evidence_basis": "static-reference", "attrs": {},
        }]
        return model

    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_minimal_run(a)
    _write_minimal_run(b)
    _write(a, "system-model.json", _model("edge:alpha"))
    _write(b, "system-model.json", _model("edge:beta"))

    report = parity.compare(a, b)

    section = report["sections"]["system_model_edges"]
    assert len(section["added"]) == 1 and len(section["removed"]) == 1
    assert section["changed"] == []
    assert section["added"][0]["key"] != section["removed"][0]["key"]


def test_warm_cache_difference_alone_has_zero_semantic_differences(tmp_path):
    """N1: warm_cache is build-cache state, not a fact about the target or a
    tool version."""
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    doc = json.loads((b_run / "callgraph-coverage.json").read_text("utf-8"))
    doc["repos"][0]["warm_cache"] = "warm"
    _write(b_run, "callgraph-coverage.json", doc)

    report = parity.compare(a, b_run)

    assert not parity.has_semantic_differences(report)


def test_route_inventory_tool_identity_difference_has_zero_semantic_differences(tmp_path):
    row = {"repository_ref": "api", "method": "GET", "path": "/x",
          "route_evidence": "internal/h.go:1", "registration_kind": "endpoint"}
    a = _write_minimal_run(tmp_path / "a", route_inventory={
        "notes": [], "rows": [row], "tool": "ast-grep",
        "tool_path": "/tool-a", "tool_version": "v1", "version_drift": ""})
    b_run = _write_minimal_run(tmp_path / "b", route_inventory={
        "notes": [], "rows": [row], "tool": "ast-grep",
        "tool_path": "/tool-b", "tool_version": "v2", "version_drift": "drifted"})

    report = parity.compare(a, b_run)

    assert not parity.has_semantic_differences(report)
    assert report["tool_drift"]  # the ast-grep version drift is still surfaced


def _write_run_with_route_docs(run: Path, *, embedded: bool, rows: list[dict],
                               linkage_rows: list[dict] | None = None) -> Path:
    """Build a minimal run whose route facts live either EMBEDDED in
    discovery-report.json (the pre-57B-84-B2 shape) or in the standalone
    ``routes/route-inventory.json``/``routes/ui-route-linkage.json``
    artifacts (57B-84 B2+) — the two eras ``compare()`` must treat as
    equivalent via ``_resolve_route_docs``'s fallback."""
    run.mkdir(parents=True, exist_ok=True)
    _write(run, "capabilities.json", _capabilities())
    _write(run, "evidence-catalog.json", _evidence_catalog())
    _write(run, "system-model.json", _system_model())
    _write(run, "callgraph-coverage.json", _callgraph_coverage())
    _write(run, "signals/run-summary.json", _signals())
    inventory = {"notes": [], "rows": rows, "tool": "ast-grep",
                "tool_path": "/tool", "tool_version": "v1", "version_drift": ""}
    linkage = {"frontends": [], "calls_by_frontend_repository": {},
              "notes": [], "rows": linkage_rows or []}
    discovery = _discovery()
    if embedded:
        discovery["route_inventory"] = inventory
        discovery["ui_route_linkage"] = linkage
    _write(run, "discovery-report.json", discovery)
    if not embedded:
        _write(run, "routes/route-inventory.json", inventory)
        _write(run, "routes/ui-route-linkage.json", linkage)
    _write(run, "run-provenance.json", _provenance())
    _write(run, "identity-map.json", _identity_map())
    _write(run, "provider-execution.json", _provider_execution())
    return run


def test_mixed_era_route_comparison_treats_embedded_and_standalone_as_equivalent(tmp_path):
    """57B-84 B2's own acceptance gate: a base run predating the migration
    (route_inventory/ui_route_linkage embedded in discovery-report.json)
    compared against a candidate run postdating it (the same facts in the
    standalone routes/*.json artifacts) must show ZERO route-related
    differences when the underlying facts are identical — the exact shape
    57B-87's acceptance gate exercises against the pre-refactor mainline
    run. Without the fallback, the base run would contribute zero route
    facts and every candidate row would look like a spurious add."""
    row = {"repository_ref": "api", "method": "GET", "path": "/items",
          "route_evidence": "h.go:1", "registration_kind": "endpoint"}
    base = _write_run_with_route_docs(tmp_path / "base", embedded=True, rows=[row])
    candidate = _write_run_with_route_docs(
        tmp_path / "candidate", embedded=False, rows=[row])

    report = parity.compare(base, candidate)

    assert report["sections"]["discovery_evidence"]["added"] == []
    assert report["sections"]["discovery_evidence"]["removed"] == []
    assert report["sections"]["discovery_evidence"]["changed"] == []
    assert not parity.has_semantic_differences(report)


def test_mixed_era_route_comparison_surfaces_a_real_mutated_row(tmp_path):
    """Same mixed-era shape as above, but the candidate's row genuinely
    differs (a status flip) — the comparator must still catch it, proving
    the fallback does not also mask REAL differences."""
    base_row = {"repository_ref": "api", "method": "GET", "path": "/items",
               "route_evidence": "h.go:1", "registration_kind": "endpoint"}
    candidate_row = {**base_row, "route_evidence": "h.go:2"}
    base = _write_run_with_route_docs(tmp_path / "base", embedded=True, rows=[base_row])
    candidate = _write_run_with_route_docs(
        tmp_path / "candidate", embedded=False, rows=[candidate_row])

    report = parity.compare(base, candidate)

    evidence = report["sections"]["discovery_evidence"]
    assert [row["value"] for row in evidence["added"]] == [
        json.dumps(candidate_row, sort_keys=True)]
    assert [row["value"] for row in evidence["removed"]] == [
        json.dumps(base_row, sort_keys=True)]
    assert parity.has_semantic_differences(report)


def test_provider_execution_reason_change_is_prose_only_and_path_scrubbed(tmp_path):
    """Reason text is surfaced (scrubbed) for a human reader, but is
    informational-only — like tool_drift — so it must never flip
    has_semantic_differences when it's the ONLY thing that differs."""
    a = _write_minimal_run(tmp_path / "a", workspace_root="/ws-a")
    b_run = _write_minimal_run(tmp_path / "b", workspace_root="/ws-b")
    doc = json.loads((b_run / "provider-execution.json").read_text("utf-8"))
    doc["executions"][0]["reason"] = "failed: could not read /ws-b/api/broken.go"
    _write(b_run, "provider-execution.json", doc)

    report = parity.compare(a, b_run)

    section = report["sections"]["provider_execution"]
    assert section["added"] == [] and section["removed"] == [] and section["changed"] == []
    rows = report["provider_execution_reasons"]
    assert rows["removed"] == []
    assert len(rows["added"]) == 1
    assert "/ws-b" not in rows["added"][0]
    assert "$WORKSPACE" in rows["added"][0]
    assert not parity.has_semantic_differences(report)


def test_differing_target_path_alone_does_not_warn_or_diff(tmp_path):
    """targets[].path is machine-local; only reference/head/branch/dirty are
    the portable identity that TARGETS-DIFFER guards."""
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    provenance = json.loads((b_run / "run-provenance.json").read_text("utf-8"))
    provenance["targets"][0]["path"] = "/completely/different/path/api"
    _write(b_run, "run-provenance.json", provenance)

    report = parity.compare(a, b_run)

    assert not any("TARGETS DIFFER" in warning for warning in report["warnings"])
    assert not parity.has_semantic_differences(report)


def test_capability_applicable_flag_change_is_reclassified(tmp_path):
    """N3: capabilities.json spells the applicability axis `applicable`
    (bool), not `applicability` (str) — both must count as reclassification."""
    a = _write_minimal_run(tmp_path / "a")
    b_run = _write_minimal_run(tmp_path / "b")
    doc = json.loads((b_run / "capabilities.json").read_text("utf-8"))
    doc["capabilities"][0]["applicable"] = False
    _write(b_run, "capabilities.json", doc)

    report = parity.compare(a, b_run)

    changed = report["sections"]["capability_records"]["changed"]
    assert len(changed) == 1
    assert changed[0]["reclassified"] is True


# ---------------------------------------------------------------------------
# 9. 57B-112 §2 regressions: mixed-era row-key collapse + totals off-by-one.
# ---------------------------------------------------------------------------


def test_lane_coverage_legacy_repo_id_rows_stay_distinct():
    """A pre-57B-88 callgraph-coverage.json row carries `repo_id`, not
    `repository_ref` (17f7bb3's rename) -- keying purely by `repository_ref`
    reads every row as the same empty string, collapsing two different
    repos sharing `lang` onto one dict key."""
    doc = {"scan_date": "2026-01-01", "determinism": "x", "repos": [
        {"repo_id": "api", "lang": "go", "status": "complete"},
        {"repo_id": "worker", "lang": "go", "status": "complete"},
    ]}

    result = parity._lane_coverage(doc, None)

    assert len(result) == 2
    assert {key[1] for key in result} == {"api", "worker"}


def test_signals_legacy_repo_id_rows_stay_distinct():
    """Same rename affected signals/run-summary.json rows."""
    doc = {"aggregate_status": "complete", "signals": [
        {"tool": "scc", "repo_id": "api", "status": "complete", "reason": ""},
        {"tool": "scc", "repo_id": "worker", "status": "complete", "reason": ""},
    ]}

    result = parity._signals(doc)

    repo_keys = {key for key in result if key != ("__aggregate__",)}
    assert len(repo_keys) == 2
    assert {key[1] for key in repo_keys} == {"api", "worker"}


def _legacy_callgraph_row(repo_id: str, status: str) -> dict:
    return {"repo_id": repo_id, "lang": "go", "status": status, "tool": "callgraph",
            "tool_version": "v1", "algorithm": "vta", "warm_cache": "n/a", "reason": "",
            "candidates_by_ext": {}, "analyzed_by_ext": {}, "excluded_by_reason": {},
            "parse_load_failures": 0,
            "call_sites": {"resolved": 0, "ambiguous": 0, "external": 0, "unresolved": 0,
                          "total": 0},
            "edges_emitted": 0, "notes": ""}


def test_lane_coverage_two_legacy_repos_sharing_lang_do_not_collapse(tmp_path):
    """The actual danger of the collapse this fixes: TWO distinct pre-88
    repos sharing `lang` key to the SAME (kind, "", lang) tuple without the
    fallback, so the second row silently overwrites the first in the
    projected dict -- a real status change on the first repo ("api") would
    never surface as a difference at all, not even a wrong one."""
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    _write(a, "callgraph-coverage.json", {
        "scan_date": "2026-07-23", "determinism": "x",
        "repos": [_legacy_callgraph_row("api", "complete"),
                 _legacy_callgraph_row("worker", "complete")]})
    _write(b, "callgraph-coverage.json", {
        "scan_date": "2026-07-23", "determinism": "x",
        "repos": [_legacy_callgraph_row("api", "partial"),
                 _legacy_callgraph_row("worker", "failed")]})

    report = parity.compare(a, b)

    section = report["sections"]["lane_coverage"]
    assert section["added"] == [] and section["removed"] == []
    assert {row["key"] for row in section["changed"]} == {
        "callgraph / api / go", "callgraph / worker / go"}


def test_signals_two_legacy_repos_sharing_tool_do_not_collapse(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    _write(a, "signals/run-summary.json", {"aggregate_status": "complete", "signals": [
        {"tool": "scc", "repo_id": "api", "status": "complete", "reason": ""},
        {"tool": "scc", "repo_id": "worker", "status": "complete", "reason": ""}]})
    _write(b, "signals/run-summary.json", {"aggregate_status": "partial", "signals": [
        {"tool": "scc", "repo_id": "api", "status": "partial", "reason": "x"},
        {"tool": "scc", "repo_id": "worker", "status": "failed", "reason": "y"}]})

    report = parity.compare(a, b)

    section = report["sections"]["signals"]
    assert section["added"] == [] and section["removed"] == []
    changed_keys = {row["key"] for row in section["changed"]}
    assert "scc / api" in changed_keys and "scc / worker" in changed_keys


def test_summary_total_differences_always_equals_sum_of_by_section(tmp_path):
    """57B-112 §2: summary.total_differences must be COMPUTED FROM
    summary.by_section (never a second, independently-accumulated number) so
    the two can never diverge again -- previously `total` also added the
    prose lanes' contribution while `by_section` silently omitted them
    entirely (98,475 vs 98,474 on a real mainline comparison, traced to a
    single `not_targeted` addition nobody could find in the per-section
    breakdown)."""
    a = _write_minimal_run(tmp_path / "a", workspace_root="/ws-a")
    b = _write_minimal_run(
        tmp_path / "b", workspace_root="/ws-b",
        not_targeted=["/ws-b/vendor (excluded by operator flag)"])

    report = parity.compare(a, b)

    by_section = report["summary"]["by_section"]
    assert report["summary"]["total_differences"] == sum(by_section.values())
    # The ONLY actual difference here is the prose addition -- by_section
    # must itself carry it (not just the total), or this assertion is
    # trivially satisfied by an empty report.
    assert report["summary"]["total_differences"] == 1
    assert by_section.get("prose/not_targeted") == 1
    assert all(count == 0 for name, count in by_section.items()
              if name != "prose/not_targeted")


# ---------------------------------------------------------------------------
# 10. 57B-114: --semantic mode.
# ---------------------------------------------------------------------------


def _finding_row(finding_id, *, lens="architecture", priority="high", confidence="medium",
                 affected_modules=None, refs=None):
    return {
        "finding_id": finding_id, "claim": f"claim for {finding_id}", "lens": lens,
        "priority": priority, "confidence": confidence,
        "affected_modules": affected_modules or ["mod-a"],
        "evidence": [{"fact": "observed", "basis": "static-reference",
                     "refs": refs or ["api@NON-GIT:internal/handlers/foo.go:10"]}],
        "evidence_basis": ["static-reference"],
        "impact": "impact text", "limitations": "none", "suggested_direction": "investigate",
    }


def _findings_doc(rows):
    return {"schema_version": "2.0.0", "project_ref": "proj", "scan_date": "2026-07-23",
            "findings": rows}


def _module_map_doc(dispositions, modules):
    return {"schema_version": "2.0.0", "project_ref": "proj",
            "candidate_dispositions": dispositions, "modules": modules}


def test_findings_matched_by_evidence_overlap_tolerates_differing_ids(tmp_path):
    """--semantic must match the SAME underlying finding across a migration
    that renumbers finding_ids, by evidence overlap (same repo+file,
    overlapping line ranges, same lens) -- not by id."""
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    _write(a, "findings.json", _findings_doc([
        _finding_row("finding-base-shared", priority="high",
                    refs=["api@NON-GIT:internal/handlers/foo.go:10",
                          "api@NON-GIT:internal/handlers/foo.go:15"]),
        _finding_row("finding-base-only", lens="performance",
                    refs=["api@NON-GIT:internal/other.go:1"]),
    ]))
    _write(b, "findings.json", _findings_doc([
        _finding_row("finding-candidate-shared", priority="critical",
                    refs=["api@NON-GIT:internal/handlers/foo.go:12",
                          "api@NON-GIT:internal/handlers/foo.go:20"]),
        _finding_row("finding-candidate-only", lens="security",
                    refs=["api@NON-GIT:internal/new.go:1"]),
    ]))

    report = parity.compare_semantic(a, b)

    findings = report["findings"]
    assert findings["unmatched_left"] == ["finding-base-only"]
    assert findings["unmatched_right"] == ["finding-candidate-only"]
    assert len(findings["matched"]) == 1
    pair = findings["matched"][0]
    assert pair["base_finding_id"] == "finding-base-shared"
    assert pair["candidate_finding_id"] == "finding-candidate-shared"
    assert pair["deltas"] == {"priority": {"base": "high", "candidate": "critical"}}
    assert parity.has_semantic_mode_differences(report)


def test_findings_pure_id_churn_has_zero_semantic_differences(tmp_path):
    """The whole point of --semantic: identical substance under a different
    finding_id must compare clean."""
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    refs = ["api@NON-GIT:internal/handlers/foo.go:10"]
    _write(a, "findings.json", _findings_doc([_finding_row("finding-old-id", refs=refs)]))
    _write(b, "findings.json", _findings_doc([_finding_row("finding-new-id", refs=refs)]))

    report = parity.compare_semantic(a, b)

    findings = report["findings"]
    assert findings["unmatched_left"] == [] and findings["unmatched_right"] == []
    assert len(findings["matched"]) == 1
    assert findings["matched"][0]["deltas"] == {}
    assert not parity.has_semantic_mode_differences(report)


def test_module_map_classification_and_membership_diffs(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    _write(a, "module-map.json", _module_map_doc(
        [{"candidate_id": "mc-1", "disposition": "standalone", "module_ids": ["mod-a"],
          "reason": ""}],
        [{"module_id": "mod-a", "name": "Mod A", "classification": "business",
          "aliases": [], "confidence": "high"}]))
    _write(b, "module-map.json", _module_map_doc(
        [{"candidate_id": "mc-1", "disposition": "standalone", "module_ids": ["mod-a"],
          "reason": ""},
         {"candidate_id": "mc-2", "disposition": "standalone", "module_ids": ["mod-b"],
          "reason": ""}],
        [{"module_id": "mod-a", "name": "Mod A", "classification": "platform",
          "aliases": [], "confidence": "high"},
         {"module_id": "mod-b", "name": "Mod B", "classification": "business",
          "aliases": [], "confidence": "high"}]))

    report = parity.compare_semantic(a, b)

    module_map = report["module_map"]
    assert module_map["added"] == ["mod-b"]
    assert module_map["removed"] == []
    assert len(module_map["changed"]) == 1
    changed = module_map["changed"][0]
    assert changed["module_id"] == "mod-a"
    assert changed["base"]["classification"] == "business"
    assert changed["candidate"]["classification"] == "platform"
    assert parity.has_semantic_mode_differences(report)


def test_disposition_totals_mismatch_is_flagged(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    _write(a, "module-map.json", _module_map_doc(
        [{"candidate_id": "mc-1", "disposition": "standalone", "module_ids": ["mod-a"],
          "reason": ""}],
        [{"module_id": "mod-a", "name": "Mod A", "classification": "business",
          "aliases": [], "confidence": "high"}]))
    _write(b, "module-map.json", _module_map_doc(
        [{"candidate_id": "mc-1", "disposition": "excluded", "module_ids": [], "reason": "dup"}],
        []))

    report = parity.compare_semantic(a, b)

    disposition = report["disposition_totals"]
    assert not disposition["equal"]
    assert disposition["base"]["standalone"] == 1
    assert disposition["candidate"]["excluded"] == 1
    assert "standalone" in disposition["differing_classes"]
    assert "excluded" in disposition["differing_classes"]
    assert parity.has_semantic_mode_differences(report)


def test_citations_and_section_completeness(tmp_path):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    base_overview = (
        "## Executive summary\n"
        "This finding is backed by `api@NON-GIT:internal/handlers/foo.go:10` "
        "and also cites api@NON-GIT:internal/handlers/bar.go:5 directly.\n\n"
        "## Analysis scope\n"
        "Short section.\n"
    )
    candidate_overview = (
        "## Executive summary\n"
        "This finding is backed by `api@NON-GIT:internal/handlers/foo.go:10` "
        "and a NEW citation api@NON-GIT:internal/handlers/baz.go:9 as well, "
        "with quite a lot more words describing the same evidence in depth.\n\n"
    )
    _write_text(a, "overview.md", base_overview)
    _write_text(b, "overview.md", candidate_overview)

    report = parity.compare_semantic(a, b)

    citations = report["citations"]["overview.md"]
    assert citations["removed"] == ["api@NON-GIT:internal/handlers/bar.go:5"]
    assert citations["added"] == ["api@NON-GIT:internal/handlers/baz.go:9"]
    assert report["citations"]["technical-overview.md"] == "not present in both runs"

    completeness = report["section_completeness"]["overview.md"]
    exec_summary = completeness["sections"]["Executive summary"]
    assert exec_summary["equal_or_greater"] is True
    scope = completeness["sections"]["Analysis scope"]
    assert scope["candidate_present"] is False
    assert scope["equal_or_greater"] is False
    assert report["section_completeness"]["technical-overview.md"] == "not present in both runs"
    assert parity.has_semantic_mode_differences(report)


def test_semantic_coverage_reflects_byte_level_capability_and_signal_sections(tmp_path):
    """`coverage` is a semantic READING of compare()'s own sections, not a
    second implementation of the same check."""
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b", capability_status="partial")

    report = parity.compare_semantic(a, b)

    coverage = report["coverage"]
    assert coverage["capability_records"]["equal"] is False
    assert coverage["capability_records"]["difference_count"] == 1
    assert coverage["lane_coverage"]["equal"] is True
    assert coverage["signals"]["equal"] is True
    assert parity.has_semantic_mode_differences(report)


def test_semantic_mode_degrades_gracefully_when_narrative_artifacts_absent(tmp_path):
    """Deterministic-only runs have no findings.json/module-map.json/
    narrative reports at all -- every section depending on one must degrade
    to the literal 'not present in both runs' marker, never crash, and never
    count as a difference."""
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")

    report = parity.compare_semantic(a, b)

    assert report["findings"] == "not present in both runs"
    assert report["module_map"] == "not present in both runs"
    assert report["disposition_totals"] == "not present in both runs"
    for name in ("overview.md", "technical-overview.md"):
        assert report["citations"][name] == "not present in both runs"
        assert report["section_completeness"][name] == "not present in both runs"
    assert not parity.has_semantic_mode_differences(report)


def test_cli_compare_runs_semantic_writes_default_report_and_prints_summary(
        tmp_path, capsys, monkeypatch):
    a = _write_minimal_run(tmp_path / "a")
    b = _write_minimal_run(tmp_path / "b")
    monkeypatch.chdir(tmp_path)

    assert main(["compare-runs", str(a), str(b), "--semantic"]) == 0
    default_path = tmp_path / "parity-semantic.json"
    assert default_path.is_file()
    written = json.loads(default_path.read_text("utf-8"))
    assert written["schema_version"] == parity.SEMANTIC_SCHEMA_VERSION
    out = capsys.readouterr().out
    assert "findings: not present in both runs" in out

    b_mutated = _write_minimal_run(tmp_path / "b-mutated", capability_status="partial")
    custom_path = tmp_path / "custom-semantic.json"
    assert main([
        "compare-runs", str(a), str(b_mutated), "--semantic", "--report", str(custom_path),
    ]) == 3
    assert custom_path.is_file()
    assert json.loads(custom_path.read_text("utf-8"))["coverage"]["capability_records"][
        "equal"] is False
