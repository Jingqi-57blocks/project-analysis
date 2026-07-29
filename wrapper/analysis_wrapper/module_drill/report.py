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
        "representative": "Representative source-backed observations", "evidence_index": "See the evidence index for the complete source list.",
        "permissions": "Access and authorization", "rules": "Rules and state", "no_claims": "No finalized claims were recovered for this category.",
        "no_flows": "No end-to-end flow was finalized from the available evidence.",
        "structural_flows": "observed structural paths",
        "no_data": "No finalized persistence claim was recovered from the available evidence.",
        "repositories": "Repositories and observed responsibilities", "relationships": "Recovered relationships",
        "dispositions": "Frontier disposition", "limitations": "Coverage limits", "claim_index": "Claim index",
        "unresolved": "Open or blocked frontiers", "none": "None", "closed": "closed", "open": "open", "blocked": "blocked",
        "canonical_model": "Canonical source model", "canonical_model_note": "The complete node and relationship evidence is retained in `evidence/module-model.json`; this document shows a compact, grouped index.",
        "observed": "observed", "inferred": "inferred", "unresolved_observation": "unresolved",
        "observations": {"observed": "observed", "inferred": "inferred", "unresolved": "unresolved"},
        "dimensions": {
            "asynchronous-behavior": "asynchronous behavior", "authorization": "authorization",
            "configuration": "configuration", "data": "data", "integration": "integration",
            "synchronous-behavior": "synchronous behavior", "ui-entry": "UI entry",
        },
        "node_kinds": {
            "ui-action": "UI action", "route": "route", "handler": "handler", "symbol": "symbol",
            "datastore": "data store", "access-check": "access check", "access-role": "access role",
            "async-boundary": "asynchronous boundary", "configuration": "configuration",
            "integration-host": "external host", "integration-package": "integration package",
            "test-file": "test file", "test-link": "test link",
        },
        "edge_kinds": {
            "ui-route": "UI to route", "routes-to": "route to handler", "calls": "calls",
            "reads": "reads", "writes": "writes", "emits": "emits", "consumes": "consumes",
            "async-boundary": "asynchronous boundary",
        },
        "generated_limits": {
            "no feature-local provider evidence was observed": "no feature-local provider evidence was observed",
            "feature-local provider evidence was observed but no source-verified semantic claim was recovered": "feature-local provider evidence was observed but no source-verified semantic claim was recovered",
            "exact observed UI-to-route graph edge": "exact observed UI-to-route graph edge",
            "exact observed route-to-handler graph edge": "exact observed route-to-handler graph edge",
            "direct observed source anchor is the bounded semantic recovery unit; no exact call edge was observed": "a direct source anchor is the bounded semantic recovery unit; no exact call edge was observed",
        },
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
            "increments": "increments", "decrements": "decrements", "allows": "allows", "denies": "denies",
            "schedules": "schedules", "starts": "starts", "consumes": "consumes", "retries": "retries",
            "notifies": "notifies", "configures": "configures", "guards": "guards",
        },
    },
    "zh-CN": {
        "overview": "模块概览", "status": "闭包状态", "behavior": "行为与规则",
        "architecture": "架构与边界", "data": "数据与影响", "change": "可变更性",
        "evidence": "证据与未知项", "claims": "有源码证据的观察", "flows": "已恢复的流程",
        "coverage": "覆盖情况", "unknown": "未知项", "nodes": "证据索引：节点",
        "edges": "证据索引：关系", "source": "源码快照", "selector": "请求分析的功能",
        "summary": "本报告已确认的内容", "contents": "报告内容", "purpose": "观察到的行为",
        "representative": "具有源码证据的代表性观察", "evidence_index": "完整源码列表请参见证据索引。",
        "permissions": "访问与授权", "rules": "规则与状态", "no_claims": "该类别没有已最终确认的声明。",
        "no_flows": "现有证据没有形成已最终确认的端到端流程。",
        "structural_flows": "条已观察的结构路径",
        "no_data": "现有证据没有形成已最终确认的持久化声明。", "repositories": "仓库与观察到的职责",
        "relationships": "已恢复的关系", "dispositions": "前沿处置", "limitations": "覆盖限制",
        "claim_index": "声明索引", "unresolved": "未解决或受阻的前沿", "none": "无", "closed": "已闭合",
        "canonical_model": "规范化源码模型", "canonical_model_note": "完整的节点与关系证据保存在 `evidence/module-model.json`；本文仅展示紧凑的分组索引。",
        "open": "未闭合", "blocked": "受阻", "observed": "已观察", "inferred": "推断", "unresolved_observation": "未解决",
        "observations": {"observed": "已观察", "inferred": "推断", "unresolved": "未解决"},
        "dimensions": {
            "asynchronous-behavior": "异步行为", "authorization": "授权", "configuration": "配置",
            "data": "数据", "integration": "外部集成", "synchronous-behavior": "同步行为",
            "ui-entry": "界面入口",
        },
        "node_kinds": {
            "ui-action": "界面操作", "route": "接口路由", "handler": "处理器", "symbol": "符号",
            "datastore": "数据存储", "access-check": "访问检查", "access-role": "访问角色",
            "async-boundary": "异步边界", "configuration": "配置",
            "integration-host": "外部主机", "integration-package": "集成包",
            "test-file": "测试文件", "test-link": "测试关联",
        },
        "edge_kinds": {
            "ui-route": "界面到接口", "routes-to": "路由到处理器", "calls": "调用",
            "reads": "读取", "writes": "写入", "emits": "产生", "consumes": "消费",
            "async-boundary": "异步边界",
        },
        "generated_limits": {
            "no feature-local provider evidence was observed": "没有观察到该功能范围内的提供方证据",
            "feature-local provider evidence was observed but no source-verified semantic claim was recovered": "观察到该功能范围内的提供方证据，但没有恢复出经源码验证的语义声明",
            "exact observed UI-to-route graph edge": "已观察到精确的界面到路由关系",
            "exact observed route-to-handler graph edge": "已观察到精确的路由到处理器关系",
            "direct observed source anchor is the bounded semantic recovery unit; no exact call edge was observed": "直接观察到的源码锚点是有界语义恢复单元；未观察到精确调用边",
        },
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
            "increments": "增加", "decrements": "减少", "allows": "可执行", "denies": "拒绝",
            "schedules": "调度", "starts": "启动", "consumes": "消费", "retries": "重试",
            "notifies": "通知", "configures": "配置", "guards": "控制/保护",
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


def _structured_label(text: dict[str, Any], category: str, value: str) -> str:
    """Localize only fixed contract vocabulary, never source-derived values."""
    return text[category].get(value, value.replace("-", " "))


def _generated_limit(text: dict[str, Any], value: str) -> str:
    """Translate deterministic diagnostic wording while preserving unknown text."""
    return text["generated_limits"].get(value, value)


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


def _brief_refs(refs: Iterable[str], text: dict[str, Any], *, limit: int = 2) -> str:
    """Keep reader-facing projections compact; the index retains every ref."""
    values = tuple(sorted(set(refs)))
    rendered = _refs(values[:limit])
    if len(values) > limit:
        return f"{rendered}; +{len(values) - limit}. {text['evidence_index']}"
    return rendered


def _claim_phrase(claim: FeatureClaim, text: dict[str, Any]) -> str:
    operation = text["operations"].get(claim.operation, claim.operation.replace("-", " "))
    value = "—" if claim.value is None else _code(claim.value)
    return f"{claim.subject} {operation} {value}"


def _claim_line(claim: FeatureClaim, text: dict[str, Any]) -> str:
    return f"- {_claim_phrase(claim, text)}. [`{claim.claim_id}`] {_brief_refs(claim.evidence_refs, text)}\n"


def _claim_identity(claim: FeatureClaim) -> tuple[object, ...]:
    """Reader-facing identity for equivalent independently-recovered facts.

    Semantic packets can legitimately overlap at a source span: an anchor
    requirement owns graph closure while a span requirement owns the source
    read.  Both outputs remain in the canonical artifact for task lineage, but
    repeating an otherwise identical fact in every Markdown section adds no
    information.  This projection groups only facts with identical semantics
    and identical source support; facts with a different literal or evidence
    remain separate.
    """
    return (
        claim.kind, claim.subject, claim.operation,
        json.dumps(claim.value, ensure_ascii=False, sort_keys=True),
        claim.evidence_refs, claim.support_roles,
    )


def _display_claims(claims: Iterable[FeatureClaim]) -> tuple[FeatureClaim, ...]:
    """Select one stable display representative for each equivalent fact."""
    representatives: dict[tuple[object, ...], FeatureClaim] = {}
    for claim in sorted(claims, key=lambda item: item.claim_id):
        representatives.setdefault(_claim_identity(claim), claim)
    return tuple(sorted(representatives.values(), key=lambda item: item.claim_id))


def _claim_section(title: str, claims: Iterable[FeatureClaim], text: dict[str, Any]) -> str:
    rows = tuple(claims)
    rendered = _heading(2, title)
    return rendered + ("".join(_claim_line(claim, text) for claim in rows) if rows else text["no_claims"] + "\n")


def _claim_groups(model: ModuleModel) -> dict[str, tuple[FeatureClaim, ...]]:
    buckets: dict[str, list[FeatureClaim]] = defaultdict(list)
    for claim in _display_claims(model.claims):
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


def _node_label(node: FeatureNode, claims_by_anchor: dict[str, tuple[FeatureClaim, ...]] | None = None,
                text: dict[str, Any] | None = None) -> str:
    """Prefer a finalized source-backed claim over an opaque graph identifier."""
    if claims_by_anchor:
        claims = claims_by_anchor.get(node.node_id, ())
        if claims:
            claim = claims[0]
            value = "" if claim.value is None else f" {claim.value}"
            return f"{claim.subject}{value}"[:120]
    # A node label is presentation text, while claim subject/value remain the
    # only route for source-derived literals into a diagram or prose.
    kind = _structured_label(text, "node_kinds", node.kind) if text is not None else node.kind
    return f"{node.repository_ref} · {kind}"


def _claims_by_anchor(model: ModuleModel) -> dict[str, tuple[FeatureClaim, ...]]:
    grouped: dict[str, list[FeatureClaim]] = defaultdict(list)
    for claim in model.claims:
        for anchor_id in claim.anchor_ids:
            grouped[anchor_id].append(claim)
    return {anchor_id: tuple(sorted(rows, key=lambda row: row.claim_id))
            for anchor_id, rows in grouped.items()}


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


def _display_edge_groups(model: ModuleModel, text: dict[str, Any], edges: Iterable[FeatureEdge]) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    """Collapse visually identical graph links without discarding evidence.

    The canonical feature graph retains one edge per observed relation for
    audit. A reader-facing document instead groups links with the same
    rendered endpoints and keeps the union of their source references.
    """
    nodes = {node.node_id: node for node in model.nodes}
    claims_by_anchor = _claims_by_anchor(model)
    grouped: dict[tuple[str, str, str], list[FeatureEdge]] = defaultdict(list)
    for edge in edges:
        source = _node_label(nodes[edge.source_node_id], claims_by_anchor, text)
        target = _node_label(nodes[edge.target_node_id], claims_by_anchor, text)
        grouped[(edge.kind, source, target)].append(edge)
    return tuple((kind, source, target, tuple(sorted({ref for edge in rows for ref in edge.evidence_refs})))
                 for (kind, source, target), rows in sorted(grouped.items()))


def _mermaid(model: ModuleModel, text: dict[str, Any]) -> str:
    """Draw only explicitly finalized flow edges, never the whole closure graph."""
    edges = _flow_edges(model)
    if not edges:
        return ""
    groups = _display_edge_groups(model, text, edges)
    labels = sorted({label for _, source, target, _ in groups for label in (source, target)})
    lines = ["```mermaid", "flowchart LR"]
    for index, value in enumerate(labels, start=1):
        label = value.replace("\\", "\\\\").replace('"', "'").replace("\n", " ")
        lines.append(f'  n{index}["{label}"]')
    aliases = {value: f"n{index}" for index, value in enumerate(labels, start=1)}
    for kind, source, target, _ in groups:
        lines.append(f"  {aliases[source]} -->|{_structured_label(text, 'edge_kinds', kind)}| {aliases[target]}")
    return "\n".join(lines) + "\n```\n"


def _flow_rows(model: ModuleModel, text: dict[str, Any]) -> list[list[str]]:
    edges = {edge.edge_id: edge for edge in model.edges}
    nodes = {node.node_id: node for node in model.nodes}
    claims_by_id = {claim.claim_id: claim for claim in model.claims}
    claims_by_anchor = _claims_by_anchor(model)
    rows: list[list[str]] = []
    structural: dict[tuple[str, ...], list[tuple[FeatureFlow, tuple[str, ...]]]] = defaultdict(list)
    for flow in model.flows:
        steps = []
        refs: list[str] = []
        for edge_id in flow.edge_ids:
            edge = edges[edge_id]
            step = (
                f"{_node_label(nodes[edge.source_node_id], claims_by_anchor, text)} → "
                f"{_node_label(nodes[edge.target_node_id], claims_by_anchor, text)} "
                f"({_structured_label(text, 'edge_kinds', edge.kind)})")
            if step not in steps:
                steps.append(step)
            refs.extend(edge.evidence_refs)
        related_claims = [claims_by_id[claim_id] for claim_id in flow.claim_ids if claim_id in claims_by_id]
        refs.extend(ref for claim in related_claims for ref in claim.evidence_refs)
        claim_refs = [f"`{claim.claim_id}`" for claim in related_claims]
        if related_claims:
            title = _claim_phrase(related_claims[0], text)
            rows.append([title, "<br>".join(steps), "; ".join(claim_refs), _brief_refs(refs, text)])
        else:
            structural[tuple(steps)].append((flow, tuple(refs)))
    for steps, group in sorted(structural.items()):
        refs = tuple(ref for _, values in group for ref in values)
        title = f"{len(group)} {text['structural_flows']}"
        rows.append([title, "<br>".join(steps), "—", _brief_refs(refs, text)])
    return rows


def _coverage_rows(model: ModuleModel, text: dict[str, Any]) -> list[list[str]]:
    statuses = {"closed": text["closed"], "open": text["open"], "blocked": text["blocked"]}
    return [[_structured_label(text, "dimensions", name),
             text["coverage_applicability"].get(value.coverage.applicability, value.coverage.applicability),
             text["coverage_status"].get(value.coverage.status, value.coverage.status),
             statuses.get(value.closure_status, value.closure_status),
             "; ".join(_generated_limit(text, reason) for reason in
                       (value.unresolved_reasons or value.coverage.limitations)) or "—"]
            for name, value in sorted(model.dimension_coverage.items())]


def _report_overview(model: ModuleModel, provenance: dict[str, Any], text: dict[str, Any]) -> str:
    selector = provenance.get("selector") if isinstance(provenance.get("selector"), str) else model.feature_id
    groups = _claim_groups(model)
    claims_by_id = {claim.claim_id: claim for claim in model.claims}
    highlights: list[FeatureClaim] = []
    for flow in sorted(model.flows, key=lambda item: item.flow_id)[:2]:
        for claim_id in flow.claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is not None and claim not in highlights:
                highlights.append(claim)
                break
    for key in ("permissions", "data", "rules", "behavior", "other"):
        for claim in groups.get(key, ()):
            if claim not in highlights:
                highlights.append(claim)
    highlights = highlights[:4]
    body = _heading(1, f"{text['overview']}: {selector}")
    body += f"**{text['status']}:** {text.get(model.closure_status, model.closure_status)}\n\n"
    body += _heading(2, text["representative"])
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
    diagram = _mermaid(model, text)
    if diagram:
        body += diagram + "\n"
    body += _heading(2, text["repositories"])
    body += _table(_headers(text, "repository", "kinds", "evidence"), [
        [repository,
         ", ".join(sorted({_structured_label(text, "node_kinds", node.kind) for node in nodes})) + f" ({len(nodes)})",
         _brief_refs((ref for node in nodes for ref in node.evidence_refs), text)]
        for repository, nodes in sorted(nodes_by_repo.items())
    ])
    body += "\n" + _heading(2, text["relationships"])
    flow_edge_ids = {edge.edge_id for edge in _flow_edges(model)}
    visible_edges = [edge for edge in model.edges if edge.edge_id in flow_edge_ids]
    if visible_edges:
        body += _table(_headers(text, "relationship", "from", "to", "evidence"), [
            [_structured_label(text, "edge_kinds", kind), source, target, _brief_refs(refs, text)]
            for kind, source, target, refs in _display_edge_groups(model, text, visible_edges)
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
            [node.repository_ref, _structured_label(text, "node_kinds", node.kind),
             _brief_refs(node.evidence_refs, text)] for node in data_nodes
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
        [text["frontier_states"].get(state, state), len(items),
         "; ".join(sorted({_generated_limit(text, reason) for reason in items if reason})) or "—"]
        for state, items in sorted(grouped.items())
    ])
    body += "\n" + _heading(2, text["limitations"])
    limits = sorted({_generated_limit(text, limit) for item in model.dimension_coverage.values()
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
        _claim_phrase(claim, text),
        _refs(claim.evidence_refs),
    ] for claim in _display_claims(model.claims)])
    body += "\n" + _heading(2, text["unresolved"])
    unresolved = [item for item in model.dispositions if item.state in {"unresolved", "blocked"}]
    if unresolved:
        body += _table(_headers(text, "frontier", "state", "reason"), [[
            item.frontier_id, text["frontier_states"].get(item.state, item.state), _generated_limit(text, item.reason)
        ] for item in unresolved])
    else:
        body += text["none"] + "\n"
    body += "\n" + _heading(2, text["canonical_model"])
    body += text["canonical_model_note"] + "\n"
    node_groups: dict[tuple[str, str, str], list[FeatureNode]] = defaultdict(list)
    for node in model.nodes:
        node_groups[(node.repository_ref, node.kind, node.observation)].append(node)
    body += "\n" + _heading(2, text["nodes"])
    body += _table(_headers(text, "repository", "kind", "observation", "count", "evidence"), [[
        repository, _structured_label(text, "node_kinds", kind),
        text["observations"].get(observation, observation), len(nodes),
        _brief_refs((ref for node in nodes for ref in node.evidence_refs), text),
    ] for (repository, kind, observation), nodes in sorted(node_groups.items())])
    edge_groups: dict[tuple[str, str, str, str], list[FeatureEdge]] = defaultdict(list)
    nodes_by_id = {node.node_id: node for node in model.nodes}
    for edge in model.edges:
        edge_groups[(edge.kind, nodes_by_id[edge.source_node_id].repository_ref,
                     nodes_by_id[edge.target_node_id].repository_ref, edge.observation)].append(edge)
    body += "\n" + _heading(2, text["edges"])
    body += _table(_headers(text, "relationship", "from", "to", "observation", "count", "evidence"), [[
        _structured_label(text, "edge_kinds", kind), source, target,
        text["observations"].get(observation, observation), len(edges),
        _brief_refs((ref for edge in edges for ref in edge.evidence_refs), text),
    ] for (kind, source, target, observation), edges in sorted(edge_groups.items())])
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
