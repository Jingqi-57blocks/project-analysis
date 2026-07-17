"""system_model.builder — idempotent accumulation + reference resolution."""

from analysis_wrapper.system_model import ids
from analysis_wrapper.system_model.builder import ModelBuilder


def test_add_node_is_idempotent_and_merges_provenance():
    b = ModelBuilder()
    first = b.add_node("symbol", ["r", "Foo", "c"], label="Foo", status="observed",
                       producer="callgraph", evidence=["c1"])
    again = b.add_node("symbol", ["r", "Foo", "c"], label="Foo", status="observed",
                       producer="other", evidence=["c2"])
    assert first == again
    assert len(b.nodes) == 1
    node = b.nodes[0]
    assert sorted(node.producers) == ["callgraph", "other"]
    assert sorted(node.evidence) == ["c1", "c2"]


def test_note_file_creates_file_and_containment_once():
    b = ModelBuilder()
    f1 = b.note_file("repo-1", "src/a.ts", producer="callgraph", evidence="c1")
    f2 = b.note_file("repo-1", "src/a.ts", producer="callgraph", evidence="c2")
    assert f1 == f2
    files = [n for n in b.nodes if n.kind == "file"]
    assert len(files) == 1
    containment = [e for e in b.edges if e.type == "containment"]
    assert len(containment) == 1
    repo_id = ids.stable_id("repository", "repo-1")
    assert containment[0].src == repo_id and containment[0].dst == f1


def test_add_unresolved_edge_stays_explicit():
    b = ModelBuilder()
    src = b.note_file("r", "src/a.ts", producer="p")
    b.add_unresolved_edge("dependency", src, {"specifier": "lodash"}, producer="p")
    b.resolve()
    edge = next(e for e in b.edges if e.type == "dependency")
    assert edge.status == "unresolved"
    assert edge.dst == ""
    assert edge.unresolved_target == {"specifier": "lodash"}


def test_resolve_downgrades_edge_with_missing_endpoint():
    b = ModelBuilder()
    src = b.note_file("r", "src/a.ts", producer="p")
    # Point at a node id that was never materialized.
    b.add_edge("call", src, "sym:doesnotexist", status="observed", producer="p")
    b.resolve()
    edge = next(e for e in b.edges if e.type == "call")
    assert edge.status == "unresolved"
    assert edge.unresolved_target == {"dst": "sym:doesnotexist"}
