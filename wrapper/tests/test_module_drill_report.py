"""Deterministic split Markdown report projection tests."""

import json

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.finalize import finalize
from analysis_wrapper.module_drill.report import CATALOG, render
from test_module_drill_finalize import _ready


def _add_claim_and_flow(module_run, *, language="en"):
    """Give the deterministic renderer a finalized-model fixture with behavior."""
    model_path = module_run / "evidence" / "module-model.json"
    document = json.loads(model_path.read_text())
    node = document["model"]["nodes"][0]
    edge = document["model"]["edges"][0]
    document["model"]["claims"] = [{
        "claim_id": "claim-record-submit",
        "kind": "ui-visibility",
        "anchor_ids": [node["node_id"]],
        "evidence_refs": list(node["evidence_refs"]),
        "support_roles": ["trigger"],
        "subject": "record submission",
        "operation": "emits",
        "value": "/records",
    }]
    document["model"]["flows"] = [{
        "flow_id": "flow-record-submit",
        "edge_ids": [edge["edge_id"]],
        "claim_ids": ["claim-record-submit"],
    }]
    model_path.write_text(json.dumps(document), encoding="utf-8")
    provenance_path = module_run / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance.update({"language": language, "selector": "record workflow", "source_mode": "standalone"})
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")


def test_render_projects_each_finalized_category_into_stable_markdown(tmp_path):
    module_run = _ready(tmp_path)
    _, audit = finalize(module_run)
    assert audit.passed
    _add_claim_and_flow(module_run)
    paths = render(module_run)
    assert tuple(paths) == CATALOG
    assert all(path.is_file() for path in paths.values())
    behavior = paths["details/behavior.md"].read_text()
    evidence = paths["details/evidence-and-unknowns.md"].read_text()
    architecture = paths["details/architecture.md"].read_text()
    overview = paths["module.md"].read_text()
    assert "Observed behavior" in behavior and "record submission emits `/records`" in behavior
    assert "claim-record-submit" in behavior
    assert "Coverage" in evidence and "Claim index" in evidence
    assert "flowchart LR" in architecture
    assert "record workflow" in overview
    assert "node-" not in architecture


def test_evidence_report_keeps_a_compact_index_and_points_to_the_canonical_model(tmp_path):
    module_run = _ready(tmp_path)
    assert finalize(module_run)[1].passed
    _add_claim_and_flow(module_run)

    evidence = render(module_run)["details/evidence-and-unknowns.md"].read_text()

    assert "Canonical source model" in evidence
    assert "`evidence/module-model.json`" in evidence
    assert "node-" not in evidence


def test_render_localizes_presentation_without_translating_source_claim_values(tmp_path):
    module_run = _ready(tmp_path)
    assert finalize(module_run)[1].passed
    _add_claim_and_flow(module_run, language="zh-CN")

    paths = render(module_run)

    overview = paths["module.md"].read_text()
    behavior = paths["details/behavior.md"].read_text()
    assert "# 模块概览: record workflow" in overview
    assert "# 行为与规则" in behavior
    assert "record submission 发起/产生 `/records`" in behavior
    assert "| 流程 | 观察到的路径 | 关联声明 | 证据 |" in behavior


def test_render_localizes_supported_operations_without_translating_ui_literals(tmp_path):
    module_run = _ready(tmp_path)
    assert finalize(module_run)[1].passed
    _add_claim_and_flow(module_run, language="zh-CN")
    model_path = module_run / "evidence" / "module-model.json"
    document = json.loads(model_path.read_text())
    document["model"]["claims"][0].update({"operation": "allows", "value": "Submit"})
    model_path.write_text(json.dumps(document), encoding="utf-8")

    paths = render(module_run)

    behavior = paths["details/behavior.md"].read_text()
    assert "record submission 可执行 `Submit`" in behavior
    assert "record submission allows" not in behavior


def test_render_localizes_fixed_contract_vocabulary_but_not_source_literals(tmp_path):
    module_run = _ready(tmp_path)
    assert finalize(module_run)[1].passed
    _add_claim_and_flow(module_run, language="zh-CN")

    paths = render(module_run)

    overview = paths["module.md"].read_text()
    architecture = paths["details/architecture.md"].read_text()
    assert "| 界面入口 |" in overview
    assert "接口路由" in architecture
    assert "异步边界" in architecture


def test_report_projection_deduplicates_equivalent_claims_but_keeps_distinct_evidence(tmp_path):
    module_run = _ready(tmp_path)
    assert finalize(module_run)[1].passed
    _add_claim_and_flow(module_run)
    model_path = module_run / "evidence" / "module-model.json"
    document = json.loads(model_path.read_text())
    duplicate = {**document["model"]["claims"][0], "claim_id": "claim-record-submit-duplicate"}
    document["model"]["claims"].append(duplicate)
    model_path.write_text(json.dumps(document), encoding="utf-8")

    paths = render(module_run)

    behavior = paths["details/behavior.md"].read_text()
    evidence = paths["details/evidence-and-unknowns.md"].read_text()
    assert behavior.count("record submission emits `/records`. [`claim-record-submit`]") == 1
    assert "claim-record-submit-duplicate" not in behavior
    assert "claim-record-submit-duplicate" not in evidence


def test_report_groups_unclaimed_structural_paths_and_keeps_their_evidence(tmp_path):
    module_run = _ready(tmp_path)
    assert finalize(module_run)[1].passed
    model_path = module_run / "evidence" / "module-model.json"
    document = json.loads(model_path.read_text())
    edge = document["model"]["edges"][0]
    document["model"]["flows"] = [
        {"flow_id": "flow-structural-a", "edge_ids": [edge["edge_id"]], "claim_ids": []},
        {"flow_id": "flow-structural-b", "edge_ids": [edge["edge_id"]], "claim_ids": []},
    ]
    model_path.write_text(json.dumps(document), encoding="utf-8")

    behavior = render(module_run)["details/behavior.md"].read_text()

    assert "2 observed structural paths" in behavior
    assert "flow-structural-a" not in behavior
    assert next(iter(edge["evidence_refs"])) in behavior


def test_cli_renders_the_complete_catalog(tmp_path, capsys):
    module_run = _ready(tmp_path)
    assert finalize(module_run)[1].passed
    assert main(["module-render-report", "--run", str(module_run)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result["reports"]) == set(CATALOG)
