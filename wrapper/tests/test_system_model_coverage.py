"""system_model.coverage — caps, partial/failed/unavailable partitions."""

import json
import pytest

import analysis_wrapper.system_model.assemble as sm
from system_model_fixtures import write_run


def _coverage(run):
    return sm.assemble(run).coverage


def test_missing_callgraph_is_disclosed_partition_not_empty_graph(tmp_path):
    model = sm.assemble(write_run(tmp_path / "run", with_callgraph=False))
    part = model.coverage["symbols_and_calls"]
    assert part["status"] == "unavailable"
    assert any("callgraph" in n.lower() for n in part["notes"])
    # The rest of the graph is still built — absence is disclosed, not fatal.
    assert not [n for n in model.nodes if n.kind == "symbol"]
    assert [n for n in model.nodes if n.kind == "route"]


def test_capped_route_summary_is_recorded_but_not_used_as_source(tmp_path):
    cov = _coverage(write_run(tmp_path / "run", capped_routes=True))
    routes = cov["routes"]
    assert routes["status"] == "complete"        # detailed liveness IS available
    assert any("200-row cap" in n for n in routes["notes"])
    assert any("route_liveness" in n for n in routes["notes"])


def test_routes_partial_when_only_capped_summary_available(tmp_path):
    # No route_liveness -> the capped module_signals.routes is NOT used as source.
    cov = _coverage(write_run(tmp_path / "run", route_liveness=False))
    routes = cov["routes"]
    assert routes["status"] == "partial"
    assert routes["counts"]["routes"] == 0
    assert any("canonical source" in n for n in routes["notes"])


def test_route_unresolved_relationships_preserved(tmp_path):
    routes = _coverage(write_run(tmp_path / "run"))["routes"]
    # no-direct-path-match + match-ambiguous rows preserved as unresolved counts.
    assert routes["unresolved"]["no_caller_found"] == 1
    assert routes["unresolved"]["match_ambiguous"] == 1


def test_tables_partial_when_sql_sublane_incomplete(tmp_path):
    cov = _coverage(write_run(tmp_path / "run", sql_complete=False))
    tables = cov["tables"]
    assert tables["status"] == "partial"
    assert any("SQL" in n or "sql" in n for n in tables["notes"])
    # uncapped table_evidence is the source, and unresolved bindings are kept.
    assert tables["unresolved"]["table_bindings"] >= 1


def test_tables_use_uncapped_evidence_source(tmp_path):
    tables = _coverage(write_run(tmp_path / "run"))["tables"]
    assert any("UNCAPPED" in n or "uncapped" in n for n in tables["notes"])


def test_store_reference_is_not_promoted_to_declaration(tmp_path):
    run = write_run(tmp_path / "run")
    path = run / "discovery-report.json"
    report = json.loads(path.read_text("utf-8"))
    evidence = report["repos"][0]["table_evidence"]
    evidence["tables"] = {"events": {"unresolved": ["internal/events.go:4"]}}
    evidence["store_metadata"] = {"events": {
        "kind": "collection", "families": ["document-driver"],
        "physical_name": "events", "logical_names": []}}
    evidence["detector_coverage"] = {
        "complete": True, "detected_families": ["document-driver"],
        "supported_families": ["document-driver"],
        "unsupported_families": [], "extracted_families": ["document-driver"]}
    path.write_text(json.dumps(report), "utf-8")
    model = sm.assemble(run)
    node = next(node for node in model.nodes
                if node.kind == "data-store" and node.label == "events")
    assert node.evidence_basis == "static-reference"


def test_dependency_partition_partial_when_absent(tmp_path):
    cov = _coverage(write_run(tmp_path / "run"))          # no imports/ dir
    dep = cov["dependency_imports"]
    assert dep["status"] == "partial"
    assert dep["counts"]["dependency_edges"] == 0


def test_dependency_partition_populated_when_present(tmp_path):
    cov = _coverage(write_run(tmp_path / "run", with_imports=True))
    dep = cov["dependency_imports"]
    assert dep["counts"]["dependency_edges"] == 1          # only the in-repo edge
    assert dep["counts"]["unresolved_edges"] == 2
    # external/unresolvable specifiers are preserved, not dropped.
    assert dep["unresolved"]["external_or_unresolvable_specifiers"] == 2
    assert dep["status"] == "partial"


def test_producer_file_cap_degrades_partition_to_partial(tmp_path):
    cov = _coverage(write_run(tmp_path / "run", deploy_capped=True))
    assert cov["deployable_units"]["status"] == "partial"


# --- the three canonical-graph partitions: cap-hit -> partial, no-cap -> complete

def test_routes_partial_on_liveness_scan_cap(tmp_path):
    cov = _coverage(write_run(tmp_path / "run", routes_capped=True))
    assert cov["routes"]["status"] == "partial"
    assert any("COVERAGE CAP" in n for n in cov["routes"]["notes"])


def test_routes_complete_when_no_cap(tmp_path):
    assert _coverage(write_run(tmp_path / "run"))["routes"]["status"] == "complete"


def test_tables_partial_on_evidence_cap(tmp_path):
    cov = _coverage(write_run(tmp_path / "run", tables_capped=True))
    assert cov["tables"]["status"] == "partial"
    assert any("8-site cap" in n for n in cov["tables"]["notes"])


def test_tables_complete_when_no_cap(tmp_path):
    # default: both repos available, SQL complete, no evidence cap.
    assert _coverage(write_run(tmp_path / "run"))["tables"]["status"] == "complete"


def test_boundaries_partial_on_evidence_cap(tmp_path):
    cov = _coverage(write_run(tmp_path / "run", boundaries_capped=True))
    assert cov["external_boundaries"]["status"] == "partial"
    assert any("5-site cap" in n for n in cov["external_boundaries"]["notes"])


def test_boundaries_complete_when_no_cap(tmp_path):
    assert _coverage(write_run(tmp_path / "run"))["external_boundaries"]["status"] \
        == "complete"


def test_scan_date_absence_is_disclosed(tmp_path):
    cov = _coverage(write_run(tmp_path / "run", with_callgraph=False))
    assert any("scan_date is empty" in n
               for n in cov["symbols_and_calls"]["notes"])


def test_modules_partition_unavailable_never_computed(tmp_path):
    modules = _coverage(write_run(tmp_path / "run"))["modules"]
    assert modules["status"] == "unavailable"
    assert any("INFERRED" in n or "inferred" in n for n in modules["notes"])


def test_every_partition_declares_caps_or_source_universe(tmp_path):
    cov = _coverage(write_run(tmp_path / "run", with_imports=True))
    for name, part in cov.items():
        assert part["status"] in ("complete", "partial", "failed", "unavailable")
        assert part["source_universe"] or part["notes"], name


def test_model_matches_minimal_json_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    doc = sm.assemble(write_run(tmp_path / "run", with_imports=True)).to_dict()
    schema = {
        "type": "object",
        "required": ["schema_version", "generator", "scan_date", "project_id",
                     "stats", "coverage", "nodes", "edges"],
        "properties": {
            "schema_version": {"type": "string"},
            "nodes": {"type": "array", "items": {
                "type": "object",
                "required": ["id", "kind", "label", "status", "producers"],
                "properties": {
                    "kind": {"enum": ["repository", "module", "file", "symbol",
                                      "route", "data-store", "external-boundary",
                                      "deployable-unit"]},
                    "status": {"enum": ["observed", "inferred", "unresolved"]},
                }}},
            "edges": {"type": "array", "items": {
                "type": "object",
                "required": ["id", "type", "src", "dst", "status", "producer"],
                "properties": {
                    "type": {"enum": ["containment", "dependency", "call",
                                      "route-linkage", "data", "boundary"]},
                    "status": {"enum": ["observed", "inferred", "unresolved"]},
                }}},
        },
    }
    jsonschema.validate(doc, schema)
