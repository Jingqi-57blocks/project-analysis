"""Deterministic Markdown projection of a finalized ModuleModel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from .context import load as load_context
from .finalize import MODEL_FILENAME, MODEL_SCHEMA
from .model import ModuleModel
from .validation import ContractError, sha256_json

CATALOG = ("module.md", "details/behavior.md", "details/architecture.md", "details/data.md",
           "details/changeability.md", "details/evidence-and-unknowns.md")

_TEXT = {
    "en": {"overview": "Module overview", "status": "Closure status", "behavior": "Behavior and rules",
           "architecture": "Architecture and boundaries", "data": "Data and effects", "change": "Changeability",
           "evidence": "Evidence and unknowns", "claims": "Claims", "flows": "Flows", "coverage": "Coverage",
           "unknown": "Unknowns", "nodes": "Nodes", "edges": "Edges", "source": "Source snapshot"},
    "zh-CN": {"overview": "模块概览", "status": "闭包状态", "behavior": "行为与规则",
              "architecture": "架构与边界", "data": "数据与影响", "change": "可变更性",
              "evidence": "证据与未知项", "claims": "声明", "flows": "流程", "coverage": "覆盖情况",
              "unknown": "未知项", "nodes": "节点", "edges": "边", "source": "源码快照"},
}


def _load(run: Path) -> tuple[dict[str, Any], ModuleModel, str]:
    context = load_context(run)
    path = run / "evidence" / MODEL_FILENAME
    try:
        document = json.loads(path.read_text("utf-8"))
        provenance = json.loads((run / "provenance.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("a finalized ModuleModel is required before report projection") from exc
    if not isinstance(document, dict) or document.get("schema_version") != MODEL_SCHEMA \
            or document.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError("module model artifact is invalid or stale")
    language = provenance.get("language", "en") if isinstance(provenance, dict) else "en"
    if language not in _TEXT:
        language = "en"
    return document, ModuleModel.from_dict(document.get("model"), "module model artifact"), language


def _table(headers: list[str], rows: list[list[str]]) -> str:
    body = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    body.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(body) + "\n"


def _claim_rows(model: ModuleModel) -> list[list[str]]:
    return [[claim.claim_id, claim.kind, claim.subject, claim.operation, str(claim.value),
             ", ".join(claim.evidence_refs)] for claim in model.claims]


def _mermaid(model: ModuleModel) -> str:
    if len(model.edges) < 2:
        return ""
    lines = ["```mermaid", "flowchart LR"]
    for node in model.nodes:
        lines.append(f'  {node.node_id.replace("-", "_")}["{node.kind}"]')
    for edge in model.edges:
        lines.append(f"  {edge.source_node_id.replace('-', '_')} -->|{edge.kind}| {edge.target_node_id.replace('-', '_')}")
    return "\n".join(lines) + "\n```\n"


def render(run: str | Path) -> dict[str, Path]:
    root = Path(run).expanduser().resolve()
    _, model, language = _load(root)
    text = _TEXT[language]
    details = create_stage_dir(root / "details")
    claims = _claim_rows(model)
    coverage_rows = [[name, value.coverage.applicability, value.coverage.status, value.closure_status,
                      "; ".join(value.unresolved_reasons or value.coverage.limitations)]
                     for name, value in sorted(model.dimension_coverage.items())]
    outputs: dict[str, str] = {
        "module.md": f"# {text['overview']}: `{model.feature_id}`\n\n"
        f"{text['status']}: **{model.closure_status}**\n\n"
        "- [" + text["behavior"] + "](details/behavior.md)\n"
        "- [" + text["architecture"] + "](details/architecture.md)\n"
        "- [" + text["data"] + "](details/data.md)\n"
        "- [" + text["change"] + "](details/changeability.md)\n"
        "- [" + text["evidence"] + "](details/evidence-and-unknowns.md)\n",
        "details/behavior.md": f"# {text['behavior']}\n\n## {text['claims']}\n\n" +
        _table(["Claim", "Kind", "Subject", "Operation", "Value", "Evidence"], claims) +
        f"\n## {text['flows']}\n\n" + _table(["Flow", "Edges", "Claims"],
            [[flow.flow_id, ", ".join(flow.edge_ids), ", ".join(flow.claim_ids)] for flow in model.flows]),
        "details/architecture.md": f"# {text['architecture']}\n\n" + _mermaid(model) +
        f"\n## {text['nodes']}\n\n" + _table(["ID", "Kind", "Repository", "Evidence"],
            [[node.node_id, node.kind, node.repository_ref, ", ".join(node.evidence_refs)] for node in model.nodes]) +
        f"\n## {text['edges']}\n\n" + _table(["ID", "Kind", "From", "To", "Evidence"],
            [[edge.edge_id, edge.kind, edge.source_node_id, edge.target_node_id, ", ".join(edge.evidence_refs)] for edge in model.edges]),
        "details/data.md": f"# {text['data']}\n\n" + _table(["Claim", "Operation", "Subject", "Evidence"],
            [[claim.claim_id, claim.operation, claim.subject, ", ".join(claim.evidence_refs)]
             for claim in model.claims if claim.operation in {"reads", "writes"}]),
        "details/changeability.md": f"# {text['change']}\n\n" + _table(["Boundary", "State", "Reason"],
            [[item.frontier_id, item.state, item.reason] for item in model.dispositions]),
        "details/evidence-and-unknowns.md": f"# {text['evidence']}\n\n## {text['coverage']}\n\n" +
        _table(["Dimension", "Applicability", "Status", "Closure", "Limitations"], coverage_rows) +
        f"\n## {text['unknown']}\n\n" + "\n".join(
            f"- `{item.frontier_id}`: {item.reason}" for item in model.dispositions if item.state in {"unresolved", "blocked"}) + "\n",
    }
    paths: dict[str, Path] = {}
    for relative in CATALOG:
        path = root / relative
        create_stage_dir(path.parent)
        write_new_text(path, outputs[relative])
        paths[relative] = path
    return paths
