"""system_model.assemble — end-to-end over a synthetic run dir."""

import json

import analysis_wrapper.system_model.assemble as sm
from system_model_fixtures import write_run

from analysis_wrapper.system_model.schema import EDGE_TYPES


def test_assemble_builds_every_node_kind_from_evidence(tmp_path):
    model = sm.assemble(write_run(tmp_path / "run"))
    kinds = {n.kind for n in model.nodes}
    # module is intentionally absent (inferred elsewhere, not computed here).
    assert {"repository", "file", "symbol", "route", "data-store",
            "external-boundary", "deployable-unit"} <= kinds
    assert "module" not in kinds
    assert model.scan_date == "2026-02-02"      # from callgraph-coverage (recorded)
    assert model.project_id == "PROJ-1"


def test_edge_types_are_kept_distinct_by_producer(tmp_path):
    model = sm.assemble(write_run(tmp_path / "run", with_imports=True))
    by_type = {t: [e for e in model.edges if e.type == t] for t in EDGE_TYPES}
    # The language call edge only ever comes from the call graph...
    assert by_type["call"] and all(e.producer == "callgraph" for e in by_type["call"])
    # ...and no other edge type is ever labeled a call.
    assert all(e.producer == "dependency-cruiser" for e in by_type["dependency"])
    assert all(e.producer.startswith("discovery/liveness") for e in by_type["route-linkage"])
    assert all(e.producer == "discovery/tables" for e in by_type["data"])
    assert {"call", "dependency"}.isdisjoint(
        {e.type for e in by_type["route-linkage"] + by_type["data"]})


def test_call_edge_resolution_maps_to_status_and_confidence(tmp_path):
    model = sm.assemble(write_run(tmp_path / "run"))
    calls = [e for e in model.edges if e.type == "call"]
    observed = [e for e in calls if e.status == "observed"]
    inferred = [e for e in calls if e.status == "inferred"]
    assert observed and inferred
    assert all(e.confidence is None for e in observed)
    assert all(e.confidence == 0.5 for e in inferred)     # Go VTA dynamic dispatch


def test_all_references_resolve_or_are_explicitly_unresolved(tmp_path):
    model = sm.assemble(write_run(tmp_path / "run", with_imports=True))
    ids = {n.id for n in model.nodes}
    for edge in model.edges:
        if edge.status == "unresolved":
            assert edge.unresolved_target        # kept explicit, never dropped
        else:
            assert edge.src in ids and edge.dst in ids


def test_every_node_and_edge_carries_provenance_and_status(tmp_path):
    model = sm.assemble(write_run(tmp_path / "run", with_imports=True))
    for node in model.nodes:
        assert node.status in ("observed", "inferred", "unresolved")
        assert node.producers
    for edge in model.edges:
        assert edge.status in ("observed", "inferred", "unresolved")
        assert edge.producer


def test_ui_called_route_links_frontend_file_to_backend_route(tmp_path):
    model = sm.assemble(write_run(tmp_path / "run"))
    links = [e for e in model.edges if e.type == "route-linkage"]
    assert len(links) == 1                       # only the ui-called row has a caller
    link = links[0]
    src = next(n for n in model.nodes if n.id == link.src)
    dst = next(n for n in model.nodes if n.id == link.dst)
    assert src.repo_id == "web-22222222" and src.label == "src/api/users.ts"
    assert dst.kind == "route" and dst.repo_id == "api-11111111"


def test_output_is_byte_identical_across_runs(tmp_path):
    run = write_run(tmp_path / "run")
    first = sm.assemble(run).to_json()
    second = sm.assemble(run).to_json()
    assert first == second


def test_stable_ids_survive_identical_revision_rerun(tmp_path):
    # Two independent run dirs with identical inputs must mint identical IDs.
    a = sm.assemble(write_run(tmp_path / "a"))
    b = sm.assemble(write_run(tmp_path / "b"))
    assert {n.id for n in a.nodes} == {n.id for n in b.nodes}
    assert {e.id for e in a.edges} == {e.id for e in b.edges}


def test_dump_writes_sanitized_file(tmp_path):
    run = write_run(tmp_path / "run")
    out = sm.write_system_model(run)
    assert out.name == "system-model.json"
    doc = json.loads(out.read_text())
    assert doc["nodes"] and doc["edges"] and "coverage" in doc
