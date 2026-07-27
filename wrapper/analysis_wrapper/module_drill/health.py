"""Deterministic developer-facing module health report from ModuleEvidence v1."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..executor import write_new_text
from .contracts import ModuleScope, load_scope
from .evidence import load_module_evidence


_LABELS = {
    "en": {
        "title": "Module Health Report",
        "as_is": "This report describes only evidence recovered from the current source snapshot. It does not prove production activation, runtime behavior, or the absence of risks.",
        "scope": "Scope and evidence boundary",
        "boundaries": "Direct dependency boundary",
        "changeability": "Observed changeability",
        "safety": "Safety-net evidence",
        "paths": "Representative change-impact paths",
        "coverage": "Coverage limits and unknowns",
        "evidence": "Evidence",
        "confidence": "Confidence",
        "status": "Status",
        "module": "Module", "classification": "Classification", "source_mode": "Source mode", "snapshot": "Snapshot",
        "direction": "Direction", "kind": "Kind", "neighbor": "Neighbor", "bytes": "bytes",
        "none": "No supported statement was observed.",
        "not_established": "Not established by the ModuleEvidence bundle.",
        "static": "Static evidence does not establish production activation.",
        "owned_files": "Verified owned files",
        "direct_boundary": "direct boundary",
        "scope_fact": "scope fact",
        "unavailable": "unavailable",
        "observation": "Observation",
        "impact": "Potential change effect",
        "boundary_observation": "{count} direct boundary relationship(s) are evidenced from the owned scope",
        "boundary_impact": "A change that alters one of these direct interfaces may require reviewing the connected neighbour; the bundle does not establish downstream implementation impact.",
        "rule_locality": "Rule locality",
        "rule_unknown": "ModuleEvidence v1 has no rule-location facts.",
        "safety_heading": "Tests, type checks, migrations, and CI",
        "safety_unknown": "No module-scoped facts for these categories are present in this evidence bundle.",
        "path": "Path", "limitation": "Limitation", "path_text": "owned scope → {direction} `{kind}` boundary → neighbour.",
    },
    "zh-CN": {
        "title": "模块健康报告",
        "as_is": "本文仅描述从当前源码快照恢复的证据，不证明生产环境已激活、运行时行为或不存在风险。",
        "scope": "范围与证据边界",
        "boundaries": "直接依赖边界",
        "changeability": "已观察到的可变更性",
        "safety": "安全网证据",
        "paths": "代表性变更影响路径",
        "coverage": "覆盖限制与未知项",
        "evidence": "证据",
        "confidence": "置信度",
        "status": "状态",
        "module": "模块", "classification": "分类", "source_mode": "来源模式", "snapshot": "快照",
        "direction": "方向", "kind": "类型", "neighbor": "相邻对象", "bytes": "字节",
        "none": "未观察到可支撑的陈述。",
        "not_established": "当前 ModuleEvidence 包未能建立此事实。",
        "static": "静态证据无法证明该关系在生产环境中已激活。",
        "owned_files": "已验证的归属文件",
        "direct_boundary": "直接边界",
        "scope_fact": "范围事实",
        "unavailable": "不可用",
        "observation": "观察结果",
        "impact": "潜在变更影响",
        "boundary_observation": "已在归属范围内观测到 {count} 个直接边界关系",
        "boundary_impact": "变更其中任一直接接口时，可能需要检查相连对象；该证据包不证明下游实施影响。",
        "rule_locality": "规则集中性",
        "rule_unknown": "当前 ModuleEvidence v1 未包含规则位置事实。",
        "safety_heading": "测试、类型检查、迁移与 CI",
        "safety_unknown": "此证据包中没有这些类别的模块范围事实。",
        "path": "路径", "limitation": "限制", "path_text": "归属范围 → {direction} `{kind}` 边界 → 相邻对象。",
    },
}


def _labels(language: str) -> dict[str, str]:
    try:
        return _LABELS[language]
    except KeyError as exc:
        raise ValueError(f"unsupported report language: {language!r}") from exc


def _refs(*groups: list[str]) -> str:
    values = sorted({value for group in groups for value in group})
    return ", ".join(f"`{value}`" for value in values)


def _fact_refs(item: dict[str, Any]) -> list[str]:
    return list(item["fact"].get("source_refs", [])) + list(item.get("artifact_refs", []))


def _unknowns(evidence: dict[str, Any]) -> list[str]:
    values = list(evidence.get("unknowns", []))
    values.extend(evidence.get("coverage", {}).get("limitations", []))
    for row in evidence.get("coverage", {}).get("capabilities", []):
        coverage = row.get("coverage", {})
        if coverage.get("status") != "complete":
            detail = coverage.get("detail") or coverage.get("reason_code") or "coverage is incomplete"
            values.append(f"{row.get('capability_id', 'capability')}: {detail}")
    return sorted(set(values))


def render_module_health(scope: ModuleScope, evidence: dict[str, Any], *, language: str) -> str:
    """Render bounded developer evidence without inferring unseen implementation.

    A boundary is a direct, first-order relationship only.  A reported
    change-impact path therefore never absorbs neighbour implementation or
    claims that a downstream behavior will occur.
    """
    labels = _labels(language)
    facts = list(evidence["facts"])
    boundaries = list(evidence["boundaries"])
    owned = [item for item in facts if item["fact"].get("kind") == "owned-file"]
    lines = [
        f"# {scope.module.name} — {labels['title']}", "",
        f"> {labels['as_is']}", "",
        f"## {labels['scope']}", "",
        f"- **{labels['module']}:** {scope.module.name} (`{scope.module.module_id}`)",
        f"- **{labels['classification']}:** {scope.module.classification} · **{labels['confidence']}:** {scope.module.confidence}",
        f"- **{labels['source_mode']}:** {scope.source_mode} · **{labels['snapshot']}:** `{scope.snapshot_id}`", "",
        f"### {labels['owned_files']}", "",
    ]
    if owned:
        for item in owned:
            fact, data = item["fact"], item["fact"]["data"]
            lines.append(
                f"- `{data['repository_ref']}:{data['path']}` ({data['bytes']} {labels['bytes']}; "
                f"{labels['evidence']}: {_refs(_fact_refs(item))})"
            )
    else:
        lines.append(labels["none"])
    lines.extend(["", f"## {labels['boundaries']}", ""])
    if boundaries:
        lines.extend([
            f"| {labels['status']} | {labels['direction']} | {labels['kind']} | {labels['neighbor']} | {labels['evidence']} |",
            "| --- | --- | --- | --- | --- |",
        ])
        for boundary in boundaries:
            lines.append(
                f"| {boundary['status']} | {boundary['direction']} | `{boundary['kind']}` | "
                f"`{boundary['neighbor_id']}` | {_refs(boundary['source_refs'], boundary['artifact_refs'])} |"
            )
        lines.extend(["", labels["static"]])
    else:
        lines.append(labels["none"])

    lines.extend(["", f"## {labels['changeability']}", ""])
    if boundaries:
        refs = _refs(*[row["source_refs"] + row["artifact_refs"] for row in boundaries])
        lines.append(
            f"- **{labels['observation']}:** {labels['boundary_observation'].format(count=len(boundaries))} "
            f"(**{labels['confidence']}:** medium; {labels['evidence']}: {refs})."
        )
        lines.append(
            f"- **{labels['impact']}:** {labels['boundary_impact']}"
        )
    else:
        lines.append(labels["not_established"])
    lines.append(
        f"- **{labels['rule_locality']}:** {labels['not_established']} {labels['rule_unknown']}"
    )

    lines.extend(["", f"## {labels['safety']}", ""])
    lines.append(
        f"- **{labels['safety_heading']}:** {labels['not_established']} {labels['safety_unknown']}"
    )
    capability_rows = evidence.get("coverage", {}).get("capabilities", [])
    if capability_rows:
        lines.extend(["", f"| Capability | {labels['status']} | Detail |", "| --- | --- | --- |"])
        for row in capability_rows:
            coverage = row["coverage"]
            detail = coverage.get("detail") or coverage.get("reason_code") or ""
            lines.append(f"| `{row['capability_id']}` | {coverage['status']} | {detail} |")

    lines.extend(["", f"## {labels['paths']}", ""])
    if boundaries:
        for index, boundary in enumerate(boundaries[:3], 1):
            refs = _refs(boundary["source_refs"], boundary["artifact_refs"])
            lines.extend([
                f"### {index}. {scope.module.name} → `{boundary['neighbor_id']}`", "",
                f"- **{labels['path']}:** {labels['path_text'].format(direction=boundary['direction'], kind=boundary['kind'])}",
                f"- **{labels['evidence']}:** {refs}",
                f"- **{labels['limitation']}:** {labels['static']}", "",
            ])
    else:
        lines.append(labels["not_established"])

    lines.extend(["", f"## {labels['coverage']}", ""])
    unknowns = _unknowns(evidence)
    if unknowns:
        lines.extend(f"- {item}" for item in unknowns)
    else:
        lines.append(labels["none"])
    lines.append("")
    return "\n".join(lines)


def write_module_health(scope_path: str | Path, evidence_path: str | Path, destination: str | Path,
                        *, language: str) -> Path:
    scope = load_scope(scope_path)
    evidence = load_module_evidence(evidence_path)
    if evidence["scope_ref"]["module_id"] != scope.module.module_id \
            or evidence["scope_ref"]["snapshot_id"] != scope.snapshot_id:
        raise ValueError("ModuleEvidence does not match ModuleScope")
    path = Path(destination)
    write_new_text(path, render_module_health(scope, evidence, language=language))
    return path
