"""57B-79: technology-neutral System-Model ingestion from CapabilityResults.

`from_capability_results.load` is not wired into `system_model.assemble` yet
(no real provider exists); these tests call it directly against a fresh
ModelBuilder with synthetic CapabilityResults, mirroring how from_callgraph.py
and from_discovery.py are tested.
"""

from pathlib import Path

import pytest

from analysis_wrapper import identity
from analysis_wrapper.evidence import Coverage, Fact, SourceRef
from analysis_wrapper.profiles.contracts import CapabilityResult
from analysis_wrapper.system_model import from_capability_results
from analysis_wrapper.system_model.builder import ModelBuilder
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id

_BANNED_LITERALS = (
    "\"go\"", "'go'", "javascript", "typescript", "\"js\"", "'js'",
    "\"ts\"", "'ts'", "python", "express", "django", "gorm", "react",
)


def _target(path: Path) -> RepoTarget:
    path.mkdir(parents=True, exist_ok=True)
    return RepoTarget(repo_id=stable_repo_id(str(path)), path=str(path.resolve()))


@pytest.fixture
def identities(tmp_path):
    workspace = tmp_path / "workspace"
    api = _target(workspace / "api")
    return identity.build(
        TargetSpec([api]), workspace_root=workspace,
        project_id=stable_repo_id(str(workspace))), api


def _coverage(**overrides):
    fields = {"applicability": "applicable", "status": "complete",
              "reason_code": "ok", "detail": ""}
    fields.update(overrides)
    return Coverage(**fields)


def test_ingestion_keys_repository_node_off_machine_repo_id_not_reference(identities):
    mapping, api = identities
    builder = ModelBuilder()

    from_capability_results.load(builder, [], mapping)
    fact = Fact(fact_id="fact:route1", kind="route", data={"method": "GET", "path": "/x"})
    result = CapabilityResult(
        capability_id="route-inventory", provider_id="synthetic-provider",
        repo_id=api.repo_id, coverage=_coverage(), facts=(fact,))
    summary = from_capability_results.load(builder, [result], mapping)

    assert summary == {"present": True, "results": 1, "nodes_created": 1, "edges_created": 0}
    repo_nodes = [node for node in builder.nodes if node.kind == "repository"]
    assert len(repo_nodes) == 1
    assert repo_nodes[0].key == [api.repo_id]
    assert repo_nodes[0].repository_ref == mapping.reference_for(api.repo_id)
    assert repo_nodes[0].label == mapping.repository(api.repo_id).display_name


def test_ingestion_materializes_node_facts_with_containment(identities):
    mapping, api = identities
    builder = ModelBuilder()
    ref = SourceRef(repository_ref=mapping.reference_for(api.repo_id),
                    revision="a" * 40, path="internal/h.go", line=3)
    fact = Fact(fact_id="fact:route1", kind="route",
               data={"method": "GET", "path": "/x"}, source_refs=(ref,))
    result = CapabilityResult(
        capability_id="route-inventory", provider_id="synthetic-provider",
        repo_id=api.repo_id, coverage=_coverage(), facts=(fact,))

    from_capability_results.load(builder, [result], mapping)

    route_nodes = [node for node in builder.nodes if node.kind == "route"]
    assert len(route_nodes) == 1
    route_node = route_nodes[0]
    assert route_node.key == [api.repo_id, "route", "fact:route1"]
    assert route_node.attrs == {"method": "GET", "path": "/x"}
    assert route_node.evidence == [ref.to_string()]
    containment_edges = [edge for edge in builder.edges if edge.type == "containment"]
    assert any(edge.dst == route_node.id for edge in containment_edges)


def test_ingestion_materializes_edge_facts_from_generic_target_fields(identities):
    mapping, api = identities
    builder = ModelBuilder()
    fact = Fact(fact_id="fact:dep1", kind="dependency",
               data={"target_kind": "external-boundary",
                     "target_key": ["candidate", "stripe"],
                     "target_label": "stripe"})
    result = CapabilityResult(
        capability_id="dependency-map", provider_id="synthetic-provider",
        repo_id=api.repo_id, coverage=_coverage(), facts=(fact,))

    from_capability_results.load(builder, [result], mapping)

    dependency_edges = [edge for edge in builder.edges if edge.type == "dependency"]
    assert len(dependency_edges) == 1
    boundary_nodes = [node for node in builder.nodes if node.kind == "external-boundary"]
    assert len(boundary_nodes) == 1
    assert boundary_nodes[0].label == "stripe"


def test_ingestion_rejects_edge_fact_missing_generic_target_fields(identities):
    mapping, api = identities
    builder = ModelBuilder()
    fact = Fact(fact_id="fact:dep-bad", kind="dependency", data={})
    result = CapabilityResult(
        capability_id="dependency-map", provider_id="synthetic-provider",
        repo_id=api.repo_id, coverage=_coverage(), facts=(fact,))

    with pytest.raises(ValueError, match="target_kind"):
        from_capability_results.load(builder, [result], mapping)


def test_ingestion_is_idempotent_across_repeated_results(identities):
    mapping, api = identities
    builder = ModelBuilder()
    fact = Fact(fact_id="fact:route1", kind="route", data={"path": "/x"})
    result = CapabilityResult(
        capability_id="route-inventory", provider_id="synthetic-provider",
        repo_id=api.repo_id, coverage=_coverage(), facts=(fact,))

    from_capability_results.load(builder, [result, result], mapping)

    route_nodes = [node for node in builder.nodes if node.kind == "route"]
    assert len(route_nodes) == 1


def test_ingestion_module_has_no_technology_literals():
    source = Path(from_capability_results.__file__).read_text("utf-8")
    lowered = source.lower()
    hits = [literal for literal in _BANNED_LITERALS if literal in lowered]
    assert hits == []
