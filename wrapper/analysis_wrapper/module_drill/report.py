"""Deterministic, readable Markdown projection of a finalized ModuleModel.

This module deliberately does not recover new facts.  It turns only finalized,
source-backed model objects into a small set of readable documents.  Keeping
the projection deterministic means a report cannot become more confident than
the model it cites.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..executor import create_stage_dir, write_new_text
from .context import load as load_context
from .finalize import MODEL_FILENAME, MODEL_SCHEMA
from .model import FeatureClaim, FeatureEdge, FeatureFlow, FeatureNode, ModuleModel
from .validation import ContractError, sha256_json

CATALOG = (
    "module.md",
    "details/behavior.md",
    "details/architecture.md",
    "details/data.md",
    "details/changeability.md",
    "details/evidence-and-unknowns.md",
)

# Presentation strings and operation wording are data, rather than language
# conditionals spread through the renderer.  A claim's source-derived subject,
# scalar value and evidence references are always preserved verbatim.
_TEXT = {
    "en": {
        "overview": "Module overview", "status": "Closure status", "behavior": "Behavior and rules",
        "architecture": "Architecture and boundaries", "data": "Data and effects", "change": "Changeability",
        "evidence": "Evidence and unknowns", "claims": "Source-backed observations", "flows": "Recovered flows",
        "coverage": "Coverage", "unknown": "Unknowns", "nodes": "Evidence index: nodes",
        "edges": "Evidence index: relationships", "source": "Source snapshot", "selector": "Requested feature",
        "summary": "What this report establishes", "contents": "Report contents", "purpose": "Observed behavior",
        "permissions": "Access and authorization", "rules": "Rules and state", "no_claims": "No finalized claims were recovered for this category.",
        "no_flows": "No end-to-end flow was finalized from the available evidence.",
        "no_data": "No finalized persistence claim was recovered from the available evidence.",
        "repositories": "Repositories and observed responsibilities", "relationships": "Recovered relationships",
        "dispositions": "Frontier disposition", "limitations": "Coverage limits", "claim_index": "Claim index",
        "unresolved": "Open or blocked frontiers", "none": "None", "closed": "closed", "open": "open", "blocked": "blocked",
        "observed": "observed", "inferred": "inferred", "unresolved_observation": "unresolved",
        "observations": {"observed": "observed", "inferred": "inferred", "unresolved": "unresolved"},
        "coverage_applicability": {"applicable": "applicable", "not-applicable": "not applicable", "unknown": "unknown"},
        "coverage_status": {"complete": "complete", "partial": "partial", "unavailable": "unavailable", "skipped": "skipped", "failed": "failed"},
        "frontier_states": {"expanded": "expanded", "terminal": "terminal", "excluded": "excluded", "unresolved": "unresolved", "blocked": "blocked"},
        "headers": {
            "dimension": "Dimension", "applicability": "Applicability", "status": "Status", "closure": "Closure",
            "limits": "Limitations", "flow": "Flow", "path": "Observed path", "related_claims": "Related claims",
            "evidence": "Evidence", "repository": "Repository", "kinds": "Observed node kinds", "relationship": "Relationship",
            "from": "From", "to": "To", "boundary": "Observed boundary", "state": "State", "count": "Count",
            "reasons": "Recorded reasons", "selector": "Requested feature", "source_mode": "Source mode", "claim": "Claim",
            "observation": "Observation", "frontier": "Frontier", "reason": "Reason", "id": "ID", "kind": "Kind",
        },
        "operations": {
            "emits": "emits", "requires": "requires", "transitions": "transitions to", "writes": "writes", "reads": "reads",
            "validates": "validates", "assigns": "assigns", "compares": "compares", "invokes": "invokes",
            "increments": "increments", "decrements": "decrements",
        },
    },
    "zh-CN": {
        "overview": "模块概览", "status": "闭包状态", "behavior": "行为与规则",
        "architecture": "架构与边界", "data": "数据与影响", "change": "可变更性",
        "evidence": "证据与未知项", "claims": "有源码证据的观察", "flows": "已恢复的流程",
        "coverage": "覆盖情况", "unknown": "未知项", "nodes": "证据索引：节点",
        "edges": "证据索引：关系", "source": "源码快照", "selector": "请求分析的功能",
        "summary": "本报告已确认的内容", "contents": "报告内容", "purpose": "观察到的行为",
        "permissions": "访问与授权", "rules": "规则与状态", "no_claims": "该类别没有已最终确认的声明。",
        "no_flows": "现有证据没有形成已最终确认的端到端流程。",
        "no_data": "现有证据没有形成已最终确认的持久化声明。", "repositories": "仓库与观察到的职责",
        "relationships": "已恢复的关系", "dispositions": "前沿处置", "limitations": "覆盖限制",
        "claim_index": "声明索引", "unresolved": "未解决或受阻的前沿", "none": "无", "closed": "已闭合",
        "open": "未闭合", "blocked": "受阻", "observed": "已观察", "inferred": "推断", "unresolved_observation": "未解决",
        "observations": {"observed": "已观察", "inferred": "推断", "unresolved": "未解决"},
        "coverage_applicability": {"applicable": "适用", "not-applicable": "不适用", "unknown": "未知"},
        "coverage_status": {"complete": "完整", "partial": "部分覆盖", "unavailable": "不可用", "skipped": "已跳过", "failed": "失败"},
        "frontier_states": {"expanded": "已展开", "terminal": "终止", "excluded": "已排除", "unresolved": "未解决", "blocked": "受阻"},
        "headers": {
            "dimension": "维度", "applicability": "适用性", "status": "状态", "closure": "闭包", "limits": "限制",
            "flow": "流程", "path": "观察到的路径", "related_claims": "关联声明", "evidence": "证据",
            "repository": "仓库", "kinds": "观察到的节点类型", "relationship": "关系", "from": "起点", "to": "终点",
            "boundary": "观察到的边界", "state": "状态", "count": "数量", "reasons": "记录的原因",
            "selector": "请求分析的功能", "source_mode": "来源模式", "claim": "声明", "observation": "观察状态",
            "frontier": "前沿", "reason": "原因", "id": "标识", "kind": "类型",
        },
        "operations": {
            "emits": "发起/产生", "requires": "要求", "transitions": "转换为", "writes": "写入", "reads": "读取",
            "validates": "校验", "assigns": "赋值", "compares": "比较", "invokes": "调用",
            "increments": "增加", "decrements": "减少",
        },
    },
}

_BEHAVIOR_KINDS = frozenset({"ui-visibility", "flow-condition", "cancellation", "error"})
_PERMISSION_KINDS = frozenset({"authorization", "access"})
_RULE_KINDS = frozenset({"validation", "state-transition", "comparison", "default"})
_DATA_OPERATIONS = frozenset({"reads", "writes", "assigns", "increments", "decrements"})


def _load(run: Path) -> tuple[dict[str, Any], ModuleModel, str, dict[str, Any]]:
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
    if not isinstance(provenance, dict):
        provenance = {}
    language = provenance.get("language", "en")
    if language not in _TEXT:
        language = "en"
    return document, ModuleModel.from_dict(document.get("model"), "module model artifact"), language, provenance


def _cell(value: object) -> str:
    """Keep source-derived text safe and readable inside a Markdown table cell."""
    text = str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")
    return text or "—"


def _headers(text: dict[str, Any], *keys: str) -> list[str]:
    return [text["headers"][key] for key in keys]


def _code(value: object) -> str:
    return "`" + str(value).replace("`", "\\`") + "`"


def _table(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    header_cells = [_cell(value) for value in headers]
    body = ["| " + " | ".join(header_cells) + " |", "| " + " | ".join("---" for _ in header_cells) + " |"]
    body.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(body) + "\n"


def _heading(level: int, value: str) -> str:
    return f"{'#' * level} {value}\n\n"


def _refs(refs: Iterable[str]) -> str:
    values = list(refs)
    return "; ".join(f"`{value}`" for value in values) if values else "—"


def _claim_line(claim: FeatureClaim, text: dict[str, Any]) -> str:
    operation = text["operations"].get(claim.operation, claim.operation.replace("-", " "))
    value = "—" if claim.value is None else _code(claim.value)
    return f"- {claim.subject} {operation} {value}. [`{claim.claim_id}`] {_refs(claim.evidence_refs)}\n"


def _claim_section(title: str, claims: Iterable[FeatureClaim], text: dict[str, Any]) -> str:
    rows = tuple(claims)
    rendered = _heading(2, title)
    return rendered + ("".join(_claim_line(claim, text) for claim in rows) if rows else text["no_claims"] + "\n")


def _claim_groups(model: ModuleModel) -> dict[str, tuple[FeatureClaim, ...]]:
    buckets: dict[str, list[FeatureClaim]] = defaultdict(list)
    for claim in model.claims:
        if claim.kind in _PERMISSION_KINDS or "authorization" in claim.support_roles:
            buckets["permissions"].append(claim)
        elif claim.kind in _RULE_KINDS:
            buckets["rules"].append(claim)
        elif claim.operation in _DATA_OPERATIONS or "persistence" in claim.support_roles:
            buckets["data"].append(claim)
        elif claim.kind in _BEHAVIOR_KINDS:
            buckets["behavior"].append(claim)
        else:
            buckets["other"].append(claim)
    return {key: tuple(sorted(value, key=lambda item: item.claim_id)) for key, value in buckets.items()}


def _node_label(node: FeatureNode) -> str:
    return f"{node.repository_ref} · {node.kind}"


def _flow_edges(model: ModuleModel) -> tuple[FeatureEdge, ...]:
    edge_by_id = {edge.edge_id: edge for edge in model.edges}
    ordered: list[FeatureEdge] = []
    seen: set[str] = set()
    for flow in model.flows:
        for edge_id in flow.edge_ids:
            if edge_id not in seen and edge_id in edge_by_id:
                ordered.append(edge_by_id[edge_id])
                seen.add(edge_id)
    return tuple(ordered)


def _mermaid(model: ModuleModel) -> str:
    """Draw only explicitly finalized flow edges, never the whole closure graph."""
    edges = _flow_edges(model)
    if not edges:
        return ""
    nodes = {node.node_id: node for node in model.nodes}
    used_ids = sorted({edge.source_node_id for edge in edges} | {edge.target_node_id for edge in edges})
    lines = ["```mermaid", "flowchart LR"]
    for index, node_id in enumerate(used_ids, start=1):
        node = nodes[node_id]
        label = _node_label(node).replace('"', "'")
        lines.append(f'  n{index}["{label}"]')
    aliases = {node_id: f"n{index}" for index, node_id in enumerate(used_ids, start=1)}
    for edge in edges:
        lines.append(f"  {aliases[edge.source_node_id]} -->|{edge.kind}| {aliases[edge.target_node_id]}")
    return "\n".join(lines) + "\n```\n"


def _flow_rows(model: ModuleModel, text: dict[str, Any]) -> list[list[str]]:
    edges = {edge.edge_id: edge for edge in model.edges}
    nodes = {node.node_id: node for node in model.nodes}
    rows: list[list[str]] = []
    for flow in model.flows:
        steps = []
        refs: list[str] = []
        for edge_id in flow.edge_ids:
            edge = edges[edge_id]
            steps.append(f"{_node_label(nodes[edge.source_node_id])} → {_node_label(nodes[edge.target_node_id])} ({edge.kind})")
            refs.extend(edge.evidence_refs)
        claim_refs = [f"`{claim_id}`" for claim_id in flow.claim_ids]
        rows.append([flow.flow_id, "<br>".join(steps), "; ".join(claim_refs) or "—", _refs(sorted(set(refs)))])
    return rows


def _coverage_rows(model: ModuleModel, text: dict[str, Any]) -> list[list[str]]:
    statuses = {"closed": text["closed"], "open": text["open"], "blocked": text["blocked"]}
    return [[name, text["coverage_applicability"].get(value.coverage.applicability, value.coverage.applicability),
             text["coverage_status"].get(value.coverage.status, value.coverage.status),
             statuses.get(value.closure_status, value.closure_status),
             "; ".join(value.unresolved_reasons or value.coverage.limitations) or "—"]
            for name, value in sorted(model.dimension_coverage.items())]


def _report_overview(model: ModuleModel, provenance: dict[str, Any], text: dict[str, Any]) -> str:
    selector = provenance.get("selector") if isinstance(provenance.get("selector"), str) else model.feature_id
    groups = _claim_groups(model)
    highlights = tuple(model.claims[:4])
    body = _heading(1, f"{text['overview']}: {selector}")
    body += f"**{text['status']}:** {text.get(model.closure_status, model.closure_status)}\n\n"
    body += _heading(2, text["summary"])
    if highlights:
        body += "".join(_claim_line(claim, text) for claim in highlights)
    else:
        body += text["no_claims"] + "\n"
    body += "\n" + _heading(2, text["contents"])
    body += "- [" + text["behavior"] + "](details/behavior.md)\n"
    body += "- [" + text["architecture"] + "](details/architecture.md)\n"
    body += "- [" + text["data"] + "](details/data.md)\n"
    body += "- [" + text["change"] + "](details/changeability.md)\n"
    body += "- [" + text["evidence"] + "](details/evidence-and-unknowns.md)\n"
    body += "\n" + _heading(2, text["coverage"])
    body += _table(_headers(text, "dimension", "applicability", "status", "closure", "limits"), _coverage_rows(model, text))
    # Preserve a deterministic summary even when no particular claim category is present.
    if groups.get("data"):
        body += "\n" + _heading(2, text["data"])
        body += "".join(_claim_line(claim, text) for claim in groups["data"])
    return body


def _report_behavior(model: ModuleModel, text: dict[str, Any]) -> str:
    groups = _claim_groups(model)
    body = _heading(1, text["behavior"])
    body += _claim_section(text["purpose"], (*groups.get("behavior", ()), *groups.get("other", ())), text)
    body += _claim_section(text["permissions"], groups.get("permissions", ()), text)
    body += _claim_section(text["rules"], groups.get("rules", ()), text)
    body += _heading(2, text["flows"])
    if model.flows:
        body += _table(_headers(text, "flow", "path", "related_claims", "evidence"), _flow_rows(model, text))
    else:
        body += text["no_flows"] + "\n"
    return body


def _report_architecture(model: ModuleModel, text: dict[str, Any]) -> str:
    nodes_by_repo: dict[str, list[FeatureNode]] = defaultdict(list)
    for node in model.nodes:
        nodes_by_repo[node.repository_ref].append(node)
    body = _heading(1, text["architecture"])
    diagram = _mermaid(model)
    if diagram:
        body += diagram + "\n"
    body += _heading(2, text["repositories"])
    body += _table(_headers(text, "repository", "kinds", "evidence"), [
        [repository, ", ".join(sorted({node.kind for node in nodes})), _refs(sorted({ref for node in nodes for ref in node.evidence_refs}))]
        for repository, nodes in sorted(nodes_by_repo.items())
    ])
    body += "\n" + _heading(2, text["relationships"])
    flow_edge_ids = {edge.edge_id for edge in _flow_edges(model)}
    visible_edges = [edge for edge in model.edges if edge.edge_id in flow_edge_ids]
    if visible_edges:
        node_by_id = {node.node_id: node for node in model.nodes}
        body += _table(_headers(text, "relationship", "from", "to", "evidence"), [
            [edge.kind, _node_label(node_by_id[edge.source_node_id]), _node_label(node_by_id[edge.target_node_id]), _refs(edge.evidence_refs)]
            for edge in visible_edges
        ])
    else:
        body += text["no_flows"] + "\n"
    return body


def _report_data(model: ModuleModel, text: dict[str, Any]) -> str:
    groups = _claim_groups(model)
    data_nodes = [node for node in model.nodes if node.kind == "datastore"]
    body = _heading(1, text["data"])
    body += _claim_section(text["claims"], groups.get("data", ()), text)
    body += _heading(2, text["nodes"])
    if data_nodes:
        body += _table(_headers(text, "repository", "boundary", "evidence"), [
            [node.repository_ref, node.kind, _refs(node.evidence_refs)] for node in data_nodes
        ])
    else:
        body += text["no_data"] + "\n"
    return body


def _report_changeability(model: ModuleModel, text: dict[str, Any]) -> str:
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in model.dispositions:
        grouped[item.state].append(item.reason)
    body = _heading(1, text["change"])
    body += _heading(2, text["dispositions"])
    body += _table(_headers(text, "state", "count", "reasons"), [
        [text["frontier_states"].get(state, state), len(items), "; ".join(sorted(set(reason for reason in items if reason))) or "—"]
        for state, items in sorted(grouped.items())
    ])
    body += "\n" + _heading(2, text["limitations"])
    limits = sorted({limit for item in model.dimension_coverage.values()
                     for limit in (*item.coverage.limitations, *item.unresolved_reasons)})
    body += "\n".join(f"- {limit}" for limit in limits) + "\n" if limits else text["none"] + "\n"
    return body


def _report_evidence(model: ModuleModel, provenance: dict[str, Any], text: dict[str, Any]) -> str:
    selector = provenance.get("selector") if isinstance(provenance.get("selector"), str) else model.feature_id
    source_mode = provenance.get("source_mode") if isinstance(provenance.get("source_mode"), str) else "unknown"
    body = _heading(1, text["evidence"])
    body += _heading(2, text["source"])
    body += _table(_headers(text, "selector", "source_mode", "status"), [[selector, source_mode, text.get(model.closure_status, model.closure_status)]])
    body += "\n" + _heading(2, text["coverage"])
    body += _table(_headers(text, "dimension", "applicability", "status", "closure", "limits"), _coverage_rows(model, text))
    body += "\n" + _heading(2, text["claim_index"])
    body += _table(_headers(text, "claim", "observation", "evidence"), [[
        claim.claim_id,
        f"{claim.subject} {text['operations'].get(claim.operation, claim.operation.replace('-', ' '))} {claim.value if claim.value is not None else '—'}",
        _refs(claim.evidence_refs),
    ] for claim in model.claims])
    body += "\n" + _heading(2, text["unresolved"])
    unresolved = [item for item in model.dispositions if item.state in {"unresolved", "blocked"}]
    if unresolved:
        body += _table(_headers(text, "frontier", "state", "reason"), [[
            item.frontier_id, text["frontier_states"].get(item.state, item.state), item.reason
        ] for item in unresolved])
    else:
        body += text["none"] + "\n"
    body += "\n" + _heading(2, text["nodes"])
    body += _table(_headers(text, "id", "repository", "kind", "observation", "evidence"), [[
        node.node_id, node.repository_ref, node.kind, text["observations"].get(node.observation, node.observation), _refs(node.evidence_refs)
    ] for node in model.nodes])
    body += "\n" + _heading(2, text["edges"])
    body += _table(_headers(text, "id", "kind", "from", "to", "observation", "evidence"), [[
        edge.edge_id, edge.kind, edge.source_node_id, edge.target_node_id,
        text["observations"].get(edge.observation, edge.observation), _refs(edge.evidence_refs)
    ] for edge in model.edges])
    return body


def render(run: str | Path) -> dict[str, Path]:
    root = Path(run).expanduser().resolve()
    _, model, language, provenance = _load(root)
    text = _TEXT[language]
    create_stage_dir(root / "details")
    outputs = {
        "module.md": _report_overview(model, provenance, text),
        "details/behavior.md": _report_behavior(model, text),
        "details/architecture.md": _report_architecture(model, text),
        "details/data.md": _report_data(model, text),
        "details/changeability.md": _report_changeability(model, text),
        "details/evidence-and-unknowns.md": _report_evidence(model, provenance, text),
    }
    paths: dict[str, Path] = {}
    for relative in CATALOG:
        path = root / relative
        create_stage_dir(path.parent)
        write_new_text(path, outputs[relative])
        paths[relative] = path
    return paths
