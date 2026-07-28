"""First-wave deterministic frontier receipt tests for 57B-138."""

import json

import pytest

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.feature_graph import write as write_graph
from analysis_wrapper.module_drill.frontier_receipts import build, write
from analysis_wrapper.module_drill.validation import ContractError
from test_module_drill_feature_graph import _prepared_scope


def test_receipts_expand_only_exact_ui_route_edges(tmp_path):
    module_run = _prepared_scope(tmp_path)
    write_graph(load(module_run))
    state = build(load(module_run))
    by_state = {row["state"] for row in state["frontiers"]}
    assert by_state == {"expanded", "pending"}
    expanded = next(row for row in state["frontiers"] if row["state"] == "expanded")
    assert expanded["resulting_ids"]
    assert expanded["reason"] == "exact observed UI-to-route graph edge"


def test_receipts_refuse_a_tampered_graph_binding(tmp_path):
    module_run = _prepared_scope(tmp_path)
    graph_path = write_graph(load(module_run))
    graph = json.loads(graph_path.read_text())
    graph["source_manifest_digest"] = "0" * 64
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(ContractError, match="source manifest"):
        build(load(module_run))


def test_cli_writes_frontier_receipts_once(tmp_path, capsys):
    module_run = _prepared_scope(tmp_path)
    assert main(["module-build-graph", "--run", str(module_run)]) == 0
    capsys.readouterr()
    assert main(["module-build-frontier-receipts", "--run", str(module_run)]) == 0
    assert json.loads(capsys.readouterr().out)["frontier_state"].endswith("frontier-state.json")
