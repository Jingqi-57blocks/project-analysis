"""Cohesion-bundle producer (57B-116, M2) — domain-neutral fixtures only."""

import json
from pathlib import Path

from analysis_wrapper import capabilities, cohesion, coverage_render, module_map, workspace_metrics
from analysis_wrapper.system_model import assemble as sm
from system_model_fixtures import write_run


def _candidate_id(run, signal_kind, value, *, repository_ref="api"):
    candidates = json.loads((run / "module-candidates.json").read_text())["candidates"]
    matches = [row["candidate_id"] for row in candidates
               if row["signal_kind"] == signal_kind and row["value"] == value
               and row["repository_ref"] == repository_ref]
    assert len(matches) == 1, (signal_kind, value, matches)
    return matches[0]


def _built_run(tmp_path, **kwargs):
    run = write_run(tmp_path / "run", **kwargs)
    model = sm.assemble(run)
    sm.dump(model, run)
    module_map.write_candidates(run, model.to_dict())
    return run, model.to_dict()


def _add_signals_and_depmap_coverage(run):
    """The remaining canonical inputs ``workspace_metrics.build`` needs
    (mirrors test_overview_contracts.py's own ``_prepared`` helper) — needed
    only by the full-pipeline parity test below; the cohesion producer
    itself never reads either of these."""
    signals = run / "signals"
    signals.mkdir()
    manifest_name = "structure-api.manifest.json"
    (signals / manifest_name).write_text(json.dumps({
        "schema_version": "3.0.0",
        "tool": "structure", "status": "complete",
        "repos": [{"repository_ref": "api"}],
    }), "utf-8")
    (signals / "x.view.txt").write_text("items: 1\n", "utf-8")
    (signals / "run-summary.json").write_text(json.dumps({
        "schema_version": "3.0.0",
        "aggregate_status": "complete",
        "signals": [{"tool": "structure", "repository_ref": "api",
                     "status": "complete", "reason": "", "view": "x.view.txt",
                     "manifest": manifest_name}],
    }), "utf-8")
    imports = run / "imports"
    imports.mkdir(exist_ok=True)
    maps = sorted(imports.glob("*.json"))
    (imports / "depmap-coverage.json").write_text(json.dumps({
        "schema_version": "3.0.0",
        "scan_date": "2026-02-02",
        "repos": [{"repository_ref": "web", "lane": "js",
                   "status": "complete", "map_file": maps[0].name, "units": 1}]
        if maps else [],
    }), "utf-8")


# --------------------------------------------------------------------------- #
# integration: real fixture run
# --------------------------------------------------------------------------- #

def test_cohesion_bundle_forms_expected_clusters_on_the_shared_fixture(tmp_path):
    run, model_doc = _built_run(tmp_path, with_imports=True)
    doc = cohesion.build(run, model_doc)

    assert doc["schema_version"] == cohesion.SCHEMA_VERSION
    assert set(doc["kinds"]) == set(cohesion.KINDS)

    # No git-history signal view exists in this fixture run at all -- absence
    # is disclosed, never fabricated.
    assert doc["kinds"]["co-change"] == {
        "available": False,
        "reason": "no complete/partial git-history signal view is present in this run",
    }

    get_route = _candidate_id(run, "route", "GET /users")
    post_route = _candidate_id(run, "route", "POST /users")
    id_route = _candidate_id(run, "route", "GET /:id")
    table = _candidate_id(run, "data-store", "users")
    folder = _candidate_id(run, "folder", "internal")

    route_clusters = [row for row in doc["clusters"] if row["kind"] == "route-prefix"]
    assert len(route_clusters) == 1
    assert route_clusters[0]["members"] == sorted([get_route, post_route])
    assert id_route not in route_clusters[0]["members"]

    folder_clusters = [row for row in doc["clusters"] if row["kind"] == "folder"]
    assert len(folder_clusters) == 1
    assert folder_clusters[0]["members"] == sorted([get_route, post_route, id_route])
    assert folder not in folder_clusters[0]["members"]  # a different, shallower prefix

    table_clusters = [row for row in doc["clusters"] if row["kind"] == "table-ownership"]
    assert len(table_clusters) == 1
    assert table_clusters[0]["members"] == sorted([table, folder])

    # Both web-repo import-candidate files (src/a.ts, src/b.ts) prefix-match
    # the SAME "src" folder candidate, so the one dependency edge between
    # them never produces a cross-candidate pair.
    assert doc["kinds"]["import"]["total_clusters"] == 0


def test_cohesion_bundle_is_byte_deterministic_across_rebuilds(tmp_path):
    run, model_doc = _built_run(tmp_path, with_imports=True)
    first = cohesion.write(run, model_doc).read_bytes()
    second = cohesion.write(run, model_doc).read_bytes()
    assert first == second


def test_cohesion_producer_changes_nothing_else_in_the_prepare_pipeline(tmp_path):
    """Parity discipline: adding the cohesion producer to the prepare stage
    plan must not reorder or alter any EXISTING artifact byte -- only one new
    file (cohesion-bundle.json) may appear."""
    run_without, model_without = _built_run(tmp_path / "a", with_imports=True)
    run_with, model_with = _built_run(tmp_path / "b", with_imports=True)
    _add_signals_and_depmap_coverage(run_without)
    _add_signals_and_depmap_coverage(run_with)

    capabilities.write(run_without)
    coverage_render.write(run_without)
    workspace_metrics.write(run_without)

    cohesion.write(run_with, model_with)
    capabilities.write(run_with)
    coverage_render.write(run_with)
    workspace_metrics.write(run_with)

    files_without = {p.relative_to(run_without) for p in run_without.rglob("*") if p.is_file()}
    files_with = {p.relative_to(run_with) for p in run_with.rglob("*") if p.is_file()}
    assert files_with - files_without == {Path(cohesion.FILENAME)}
    assert files_without - files_with == set()
    for rel in sorted(files_without):
        assert (run_without / rel).read_bytes() == (run_with / rel).read_bytes(), rel


def test_cohesion_bundle_is_covered_by_the_overview_audit(tmp_path):
    """cohesion-bundle.json must not sit outside every audit allowlist: a
    clean bundle passes; a wrong schema_version is caught by
    artifact-contract-versions; a leaked internal id is caught by
    external-identity-boundary -- both checks walk a CLOSED list of known
    filenames (see overview_audit.py's own docstring), so a new canonical
    artifact has to be added to each list explicitly."""
    from analysis_wrapper import identity, overview_audit, synthesis_input

    run, model_doc = _built_run(tmp_path, with_imports=True)
    _add_signals_and_depmap_coverage(run)
    cohesion.write(run, model_doc)
    capabilities.write(run)
    coverage_render.write(run)
    workspace_metrics.write(run)
    synthesis_input.write(run)

    assert overview_audit.audit(run)["status"] == "passed"

    doc = json.loads((run / cohesion.FILENAME).read_text())
    doc["schema_version"] = "1.0.0"
    (run / cohesion.FILENAME).write_text(json.dumps(doc), "utf-8")
    result = overview_audit.audit(run)
    assert any(row["check"] == "artifact-contract-versions" and row["status"] == "fail"
               for row in result["checks"])

    doc["schema_version"] = cohesion.SCHEMA_VERSION
    internal_id = identity.load(run).repositories[0].internal_id
    doc["leak"] = internal_id
    (run / cohesion.FILENAME).write_text(json.dumps(doc), "utf-8")
    result = overview_audit.audit(run)
    assert any(row["check"] == "external-identity-boundary" and row["status"] == "fail"
               for row in result["checks"])


# --------------------------------------------------------------------------- #
# unit: caps / disclosure
# --------------------------------------------------------------------------- #

def test_cap_keeps_the_largest_clusters_first_and_discloses_truncation():
    rows = [
        cohesion._cluster_row("folder", "m0", {"a", "b"}, set()),
        cohesion._cluster_row("folder", "m1", {"c", "d", "e"}, set()),
        cohesion._cluster_row("folder", "m2", {"f", "g", "h", "i"}, set()),
    ]
    selected, total, truncated = cohesion._cap(rows, 2)
    assert total == 3
    assert truncated is True
    assert [len(row["members"]) for row in selected] == [4, 3]


def test_finish_records_disclosure_and_applies_the_cap(monkeypatch):
    monkeypatch.setattr(cohesion, "_MAX_CLUSTERS_PER_KIND", 1)
    rows = [cohesion._cluster_row("folder", "m0", {"a", "b"}, set()),
            cohesion._cluster_row("folder", "m1", {"c", "d", "e"}, set())]
    kinds: dict = {}
    selected = cohesion._finish(kinds, "folder", rows)
    assert len(selected) == 1
    assert kinds["folder"] == {"available": True, "total_clusters": 2,
                               "included_clusters": 1, "truncated": True}


# --------------------------------------------------------------------------- #
# unit: connected components (shared by import / co-change)
# --------------------------------------------------------------------------- #

def test_connected_components_group_transitively_and_drop_self_pairs():
    pairs = [("a", "b", "ref1"), ("b", "c", "ref2"), ("x", "x", "ref-self")]
    components = cohesion._connected_components(pairs)
    assert len(components) == 1
    members, evidence = components[0]
    assert members == {"a", "b", "c"}
    assert evidence == {"ref1", "ref2"}


def test_connected_components_order_independence():
    """The PARTITION (which members end up together, and the evidence that
    connects them) never depends on processing order -- an order-independent
    graph property (see ``_UnionFind``'s docstring). The returned LIST's own
    order is not itself a promise here: ``build()`` sorts the final cluster
    rows deterministically downstream, so this compares components as an
    order-independent set of (members, evidence) pairs."""
    forward = [("a", "b", "r1"), ("b", "c", "r2"), ("d", "e", "r3")]
    backward = list(reversed(forward))

    def normalize(components):
        return sorted((tuple(sorted(members)), tuple(sorted(evidence)))
                      for members, evidence in components)

    assert normalize(cohesion._connected_components(forward)) == normalize(
        cohesion._connected_components(backward))


# --------------------------------------------------------------------------- #
# unit: route-prefix / folder lanes
# --------------------------------------------------------------------------- #

def test_route_prefix_clusters_group_by_bounded_path_prefix_ignoring_method():
    candidates = [
        {"candidate_id": "mc-1", "repository_ref": "api", "signal_kind": "route",
         "value": "GET /users", "evidence": ["api@abc:h.go:1"]},
        {"candidate_id": "mc-2", "repository_ref": "api", "signal_kind": "route",
         "value": "POST /users", "evidence": ["api@abc:h.go:2"]},
        {"candidate_id": "mc-3", "repository_ref": "api", "signal_kind": "route",
         "value": "GET /orders", "evidence": ["api@abc:h.go:3"]},
    ]
    by_id = {row["candidate_id"]: row for row in candidates}
    rows = cohesion._route_prefix_clusters(candidates, by_id)
    assert len(rows) == 1
    assert rows[0]["members"] == ["mc-1", "mc-2"]
    assert rows[0]["evidence_refs"] == ["api@abc:h.go:1", "api@abc:h.go:2"]


def test_folder_clusters_group_by_bounded_directory_prefix_and_ignore_synthetic_evidence():
    candidates = [
        {"candidate_id": "mc-f", "repository_ref": "api", "signal_kind": "folder",
         "value": "internal",
         "evidence": ["discovery-report.json:repos[api].module_signals.folders"]},
        {"candidate_id": "mc-r1", "repository_ref": "api", "signal_kind": "route",
         "value": "GET /users", "evidence": ["api@abc:internal/h.go:1"]},
        {"candidate_id": "mc-r2", "repository_ref": "api", "signal_kind": "route",
         "value": "POST /users", "evidence": ["api@abc:internal/h.go:2"]},
    ]
    by_id = {row["candidate_id"]: row for row in candidates}
    rows = cohesion._folder_clusters(candidates, by_id)
    assert len(rows) == 1
    assert rows[0]["members"] == ["mc-f", "mc-r1", "mc-r2"]


# --------------------------------------------------------------------------- #
# unit: import / table-ownership lanes
# --------------------------------------------------------------------------- #

def test_import_clusters_connect_candidates_via_dependency_edges():
    candidates = [
        {"candidate_id": "mc-a", "repository_ref": "api", "signal_kind": "folder",
         "value": "pkg-a", "evidence": [], "node_ids": ["file:a"]},
        {"candidate_id": "mc-b", "repository_ref": "api", "signal_kind": "folder",
         "value": "pkg-b", "evidence": [], "node_ids": ["file:b"]},
    ]
    model = {"edges": [
        {"type": "dependency", "status": "observed", "src": "file:a", "dst": "file:b",
         "evidence": ["api@abc:pkg-a/x.go:1"]},
    ]}
    node_to_candidates = cohesion._node_to_candidates(candidates)
    rows = cohesion._import_clusters(model, node_to_candidates)
    assert len(rows) == 1
    assert rows[0]["members"] == ["mc-a", "mc-b"]
    assert rows[0]["evidence_refs"] == ["api@abc:pkg-a/x.go:1"]


def test_import_clusters_ignore_unresolved_edges_and_self_loops():
    candidates = [
        {"candidate_id": "mc-a", "repository_ref": "api", "signal_kind": "folder",
         "value": "pkg-a", "evidence": [], "node_ids": ["file:a", "file:a2"]},
    ]
    model = {"edges": [
        {"type": "dependency", "status": "unresolved", "src": "file:a", "dst": "",
         "evidence": []},
        {"type": "dependency", "status": "observed", "src": "file:a", "dst": "file:a2",
         "evidence": ["api@abc:pkg-a/x.go:1"]},
    ]}
    node_to_candidates = cohesion._node_to_candidates(candidates)
    rows = cohesion._import_clusters(model, node_to_candidates)
    assert rows == []


def test_table_ownership_clusters_group_candidates_sharing_table_access():
    by_id = {
        "mc-folder": {"candidate_id": "mc-folder", "evidence": []},
        "mc-table": {"candidate_id": "mc-table",
                    "evidence": ["api@abc:migrations/users.sql:1"]},
    }
    node_to_candidates = {"file:x": {"mc-folder"}, "data:users": {"mc-table"}}
    nodes_by_id = {"data:users": {"kind": "data-store", "label": "users",
                                  "repository_ref": "api"}}
    model = {"edges": [
        {"type": "data", "status": "observed", "src": "file:x", "dst": "data:users",
         "evidence": ["api@abc:internal/repo/user.go:5"]},
    ]}
    rows = cohesion._table_ownership_clusters(model, node_to_candidates, nodes_by_id, by_id)
    assert len(rows) == 1
    assert rows[0]["members"] == ["mc-folder", "mc-table"]
    assert set(rows[0]["evidence_refs"]) == {
        "api@abc:internal/repo/user.go:5", "api@abc:migrations/users.sql:1"}


# --------------------------------------------------------------------------- #
# unit: co-change lane
# --------------------------------------------------------------------------- #

def test_co_change_clusters_parse_the_coupling_section_and_map_via_folder_prefix(tmp_path):
    run = tmp_path / "run"
    (run / "signals").mkdir(parents=True)
    view_text = "\n".join([
        "backend: pydriller 2.10", "coverage_status: complete", "since: 2024-01-01",
        "commits_used: 10", "shallow: False",
        "bulk_changesets_excluded_from_coupling: 0",
        "coupling_sample_cap: 0 (commits_for_coupling: 10, sampled: false)",
        "", "churn:", "5\t40\tinternal/handlers/users.go",
        "", "coupling:",
        "80.0\t4\tinternal/handlers/users.go\tinternal/model/user.go",
        "", "cross_dir_coupling (change-friction: pairs spanning different "
        "top-level areas — ripple signal):",
        "(none above min-shared)",
        "", "ownership:",
    ]) + "\n"
    expected_line = view_text.splitlines().index("coupling:") + 2
    (run / "signals" / "history-api.view.txt").write_text(view_text, "utf-8")
    (run / "signals" / "run-summary.json").write_text(json.dumps({
        "schema_version": "3.0.0", "aggregate_status": "complete",
        "signals": [{"tool": "git-history", "repository_ref": "api",
                    "status": "complete", "reason": "", "view": "history-api.view.txt",
                    "manifest": "history-api.manifest.json"}],
    }), "utf-8")
    candidates = [
        {"candidate_id": "mc-handlers", "repository_ref": "api", "signal_kind": "folder",
         "value": "internal/handlers", "evidence": [], "node_ids": []},
        {"candidate_id": "mc-model", "repository_ref": "api", "signal_kind": "folder",
         "value": "internal/model", "evidence": [], "node_ids": []},
    ]
    rows, available, reason = cohesion._co_change_clusters(run, candidates)
    assert available is True
    assert reason == ""
    assert len(rows) == 1
    assert rows[0]["members"] == ["mc-handlers", "mc-model"]
    assert rows[0]["evidence_refs"] == [f"signals/history-api.view.txt:{expected_line}"]


def test_co_change_is_absent_when_no_git_history_view_exists(tmp_path):
    run = tmp_path / "run"
    (run / "signals").mkdir(parents=True)
    (run / "signals" / "run-summary.json").write_text(json.dumps({
        "schema_version": "3.0.0", "aggregate_status": "complete", "signals": [],
    }), "utf-8")
    rows, available, reason = cohesion._co_change_clusters(run, [])
    assert rows == []
    assert available is False
    assert reason
