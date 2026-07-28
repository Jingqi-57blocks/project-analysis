"""Deterministic candidate-universe tests for 57B-137."""

import json
from pathlib import Path

import pytest

from analysis_wrapper.module_drill.candidate_universe import build, write
from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.feature_evidence import write as write_feature_evidence
from analysis_wrapper.module_drill.validation import ContractError
from analysis_wrapper.cli import main
from test_module_drill_feature_evidence import _run


def test_universe_groups_exact_ui_to_route_evidence_without_selector_inference(tmp_path):
    _, module_run = _run(tmp_path)
    context = load(module_run)
    write_feature_evidence(context)

    document = build(load(module_run))
    linked = [row for row in document["candidates"] if len(row["seed_ids"]) == 2]
    assert len(linked) == 1
    assert linked[0]["reason"] == "exact UI-to-route method and path linkage"
    assert linked[0]["repository_refs"] == ["service"]
    assert document == build(load(module_run))


def test_universe_refuses_stale_feature_evidence_binding(tmp_path):
    _, module_run = _run(tmp_path)
    context = load(module_run)
    path = write_feature_evidence(context)
    document = json.loads(path.read_text("utf-8"))
    document["source_snapshot_id"] = "b" * 64
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractError, match="source snapshot"):
        build(load(module_run))


def test_universe_is_written_once(tmp_path):
    _, module_run = _run(tmp_path)
    write_feature_evidence(load(module_run))
    out = write(load(module_run))
    assert json.loads(out.read_text("utf-8"))["schema_version"] == "candidate-universe/v1"
    with pytest.raises(FileExistsError):
        write(load(module_run))


def test_public_cli_builds_candidates_only_after_evidence(tmp_path, capsys):
    _, module_run = _run(tmp_path)
    assert main(["module-build-evidence", "--run", str(module_run)]) == 0
    capsys.readouterr()
    assert main(["module-build-candidates", "--run", str(module_run)]) == 0
    assert Path(json.loads(capsys.readouterr().out)["candidates"]).is_file()
