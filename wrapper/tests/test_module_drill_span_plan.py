"""Semantic span plan tests for 57B-139."""

import json

import pytest

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.frontier_candidates import write as write_candidates
from analysis_wrapper.module_drill.span_plan import build, write
from analysis_wrapper.module_drill.span_fetch import build as build_spans, write as write_spans
from analysis_wrapper.module_drill.validation import ContractError
from test_module_drill_frontier_candidates import _prepared


def test_span_plan_covers_selected_graph_anchors_and_call_targets(tmp_path):
    module_run = _prepared(tmp_path)
    write_candidates(load(module_run))
    plan = build(load(module_run))
    assert plan["schema_version"] == "semantic-span-requests/v1"
    assert len(plan["requests"]) >= 3
    assert {row["kind"] for row in plan["requests"]} >= {"handler", "function"}
    assert len({row["span_id"] for row in plan["requests"]}) == len(plan["requests"])


def test_span_plan_rejects_frontier_candidate_graph_mismatch(tmp_path):
    module_run = _prepared(tmp_path)
    path = write_candidates(load(module_run))
    document = json.loads(path.read_text())
    document["feature_graph_digest"] = "0" * 64
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractError, match="do not bind"):
        build(load(module_run))


def test_cli_writes_span_plan(tmp_path, capsys):
    module_run = _prepared(tmp_path)
    assert main(["module-build-frontier-candidates", "--run", str(module_run)]) == 0
    capsys.readouterr()
    assert main(["module-plan-spans", "--run", str(module_run)]) == 0
    assert json.loads(capsys.readouterr().out)["span_plan"].endswith("semantic-span-requests.json")


def test_planned_fetch_binds_plan_and_returns_all_requested_spans(tmp_path):
    module_run = _prepared(tmp_path)
    write_candidates(load(module_run))
    write(load(module_run))

    document = build_spans(load(module_run))

    assert document["schema_version"] == "semantic-spans/v1"
    assert document["semantic_span_plan_digest"]
    assert {row["span_id"] for row in document["spans"]} == {
        row["span_id"] for row in build(load(module_run))["requests"]
    }
    assert write_spans(load(module_run)).name == "semantic-spans.json"


def test_planned_fetch_rejects_tampered_or_stale_plan(tmp_path):
    module_run = _prepared(tmp_path)
    write_candidates(load(module_run))
    path = write(load(module_run))
    document = json.loads(path.read_text())
    document["requests"] = []
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ContractError, match="does not match"):
        build_spans(load(module_run))


def test_cli_writes_plan_bound_spans(tmp_path, capsys):
    module_run = _prepared(tmp_path)
    assert main(["module-build-frontier-candidates", "--run", str(module_run)]) == 0
    capsys.readouterr()
    assert main(["module-plan-spans", "--run", str(module_run)]) == 0
    capsys.readouterr()
    assert main(["module-fetch-planned-spans", "--run", str(module_run)]) == 0
    assert json.loads(capsys.readouterr().out)["semantic_spans"].endswith("semantic-spans.json")
