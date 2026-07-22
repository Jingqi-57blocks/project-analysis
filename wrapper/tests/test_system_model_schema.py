"""system_model.schema — node/edge contract + deterministic serialization."""

import json

import pytest

from analysis_wrapper.system_model.schema import (SCHEMA_VERSION, Edge, Node,
                                                  SystemModel)


def test_node_and_edge_validate_kind_and_status():
    with pytest.raises(ValueError):
        Node(id="x", kind="bogus", label="l", status="observed")
    with pytest.raises(ValueError):
        Node(id="x", kind="file", label="l", status="bogus")
    with pytest.raises(ValueError):
        Edge(id="e", type="bogus", src="a", dst="b", status="observed")
    with pytest.raises(ValueError):
        Edge(id="e", type="call", src="a", dst="b", status="bogus")


def test_node_to_dict_dedupes_and_omits_none_confidence():
    node = Node(id="n", kind="symbol", label="Foo", status="observed",
                producers=["callgraph", "callgraph"], evidence=["c2", "c1", "c1"])
    out = node.to_dict()
    assert out["producers"] == ["callgraph"]
    assert out["evidence"] == ["c1", "c2"]
    assert "confidence" not in out
    assert Edge(id="e", type="call", src="a", dst="b", status="inferred",
                confidence=0.5).to_dict()["confidence"] == 0.5


def _model(nodes, edges):
    return SystemModel(scan_date="2026-02-02", project_ref="P", generator="g",
                       nodes=nodes, edges=edges, coverage={})


def test_to_dict_sorts_nodes_edges_and_is_order_independent():
    n1 = Node(id="sym:1", kind="symbol", label="a", status="observed")
    n2 = Node(id="file:2", kind="file", label="b", status="observed")
    e1 = Edge(id="edge:1", type="containment", src="file:2", dst="sym:1",
              status="observed")
    forward = _model([n1, n2], [e1]).to_json()
    reversed_ = _model([n2, n1], [e1]).to_json()
    assert forward == reversed_               # order-independent -> deterministic
    doc = json.loads(forward)
    assert doc["schema_version"] == SCHEMA_VERSION
    assert [n["id"] for n in doc["nodes"]] == ["file:2", "sym:1"]  # sorted by id


def test_stats_tally_by_kind_type_status():
    nodes = [Node(id="a", kind="file", label="a", status="observed"),
             Node(id="b", kind="symbol", label="b", status="observed"),
             Node(id="c", kind="module", label="c", status="inferred")]
    edges = [Edge(id="e1", type="call", src="a", dst="b", status="observed"),
             Edge(id="e2", type="call", src="a", dst="b", status="inferred")]
    stats = _model(nodes, edges).to_dict()["stats"]
    assert stats["node_count"] == 3 and stats["edge_count"] == 2
    assert stats["nodes_by_kind"] == {"file": 1, "module": 1, "symbol": 1}
    assert stats["edges_by_status"] == {"inferred": 1, "observed": 1}
