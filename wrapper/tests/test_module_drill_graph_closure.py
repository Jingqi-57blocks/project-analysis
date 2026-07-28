"""Feature-scoped graph closure tests for 57B-154."""

import json

import pytest

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.frontier_candidates import write as write_candidates
from analysis_wrapper.module_drill.graph_closure import build, write
from analysis_wrapper.module_drill.validation import ContractError
from test_module_drill_frontier_candidates import _prepared


def test_closure_materializes_only_the_exact_observed_handler_candidate(tmp_path):
    module_run = _prepared(tmp_path)
    write_candidates(load(module_run))
    closure = build(load(module_run))
    assert closure["schema_version"] == "feature-graph-closure/v1"
    assert any(row["kind"] == "symbol" for row in closure["nodes"])
    assert any(row["kind"] == "call" for row in closure["edges"])
    assert {row["state"] for row in closure["candidate_dispositions"]} == {"expanded"}
    assert all(row["state"] != "pending" for row in closure["frontier_dispositions"])


def test_closure_refuses_candidate_state_binding_drift(tmp_path):
    module_run = _prepared(tmp_path)
    path = write_candidates(load(module_run))
    document = json.loads(path.read_text())
    document["frontier_state_digest"] = "0" * 64
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractError, match="frontier state"):
        build(load(module_run))


def test_cli_writes_graph_closure(tmp_path, capsys):
    module_run = _prepared(tmp_path)
    assert main(["module-build-frontier-candidates", "--run", str(module_run)]) == 0
    capsys.readouterr()
    assert main(["module-build-graph-closure", "--run", str(module_run)]) == 0
    assert json.loads(capsys.readouterr().out)["graph_closure"].endswith("feature-graph-closure.json")
