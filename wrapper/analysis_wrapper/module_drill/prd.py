"""Deterministic PM-facing as-is module PRD from ModuleEvidence v1."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..executor import write_new_text
from .contracts import ModuleScope, load_scope
from .evidence import load_module_evidence


def _labels(language: str) -> dict[str, str]:
    if language == "zh-CN":
        return {
            "title": "模块现状 PRD", "as_is": "本文描述从当前代码中恢复的现状，不代表目标设计或生产运行事实。",
            "purpose": "模块范围", "behavior": "已观察到的实现范围", "interfaces": "直接接口与依赖",
            "unknown": "未知项与覆盖限制", "evidence": "证据", "none": "未观察到可陈述的事实。",
            "activation": "静态证据无法证明此关系在生产环境中已激活。",
            "files": "已验证的模块文件", "hints": "相关 overview 线索（尚未作为模块事实验证）",
        }
    return {
        "title": "Module As-Is PRD", "as_is": "This document describes current state recovered from code. It is not target design or proof of production runtime behavior.",
        "purpose": "Module scope", "behavior": "Observed implementation scope", "interfaces": "Direct interfaces and dependencies",
        "unknown": "Unknowns and coverage limits", "evidence": "Evidence", "none": "No supported statement was observed.",
        "activation": "Static evidence does not establish that this relationship is active in production.",
        "files": "Verified module files", "hints": "Related overview hints (not verified as module facts)",
    }


def render_module_prd(scope: ModuleScope, evidence: dict[str, Any], *, language: str) -> str:
    """Render only facts held in the canonical evidence bundle.

    The document intentionally omits a section rather than extrapolating UI,
    roles, business rules, states, data ownership, or schedules from filenames.
    """
    labels = _labels(language)
    facts = evidence["facts"]
    boundaries = evidence["boundaries"]
    lines = [f"# {scope.module.name} — {labels['title']}", "", f"> {labels['as_is']}", "",
             f"## {labels['purpose']}", "",
             f"- **Module:** {scope.module.name} (`{scope.module.module_id}`)",
             f"- **Classification:** {scope.module.classification} · **Confidence:** {scope.module.confidence}",
             f"- **Source mode:** {scope.source_mode} · **Snapshot:** `{scope.snapshot_id}`", "",
             f"## {labels['behavior']}", ""]
    file_facts = [item for item in facts if item["fact"].get("kind") == "owned-file"]
    if file_facts:
        lines.append(f"### {labels['files']}")
        lines.append("")
        for item in file_facts:
            data, fact = item["fact"]["data"], item["fact"]
            lines.append(f"- {data['repository_ref']}: `{data['path']}` — {data['bytes']} bytes "
                         f"({labels['evidence']}: `{fact['fact_id']}`)")
        lines.append("")
    else:
        lines.extend([labels["none"], ""])

    lines.extend([f"## {labels['interfaces']}", ""])
    if boundaries:
        for boundary in boundaries:
            direction = boundary["direction"]
            lines.append(f"- **{direction} {boundary['kind']}:** `{boundary['neighbor_id']}` "
                         f"({labels['evidence']}: {', '.join(f'`{value}`' for value in boundary['artifact_refs'] + boundary['source_refs'])})")
        lines.extend(["", labels["activation"], ""])
    else:
        lines.extend([labels["none"], ""])

    lines.extend([f"## {labels['unknown']}", ""])
    unknowns = list(evidence.get("unknowns", []))
    for item in evidence.get("coverage", {}).get("limitations", []):
        unknowns.append(item)
    if unknowns:
        lines.extend(f"- {item}" for item in sorted(set(unknowns)))
    else:
        lines.append(labels["none"])
    lines.append("")
    hints = evidence.get("finding_hints", [])
    if hints:
        lines.extend([f"## {labels['hints']}", ""])
        for hint in hints:
            lines.append(f"- `{hint['finding_id']}` ({hint['status']})")
        lines.append("")
    return "\n".join(lines)


def write_module_prd(scope_path: str | Path, evidence_path: str | Path, destination: str | Path,
                     *, language: str) -> Path:
    scope = load_scope(scope_path)
    evidence = load_module_evidence(evidence_path)
    if evidence["scope_ref"]["module_id"] != scope.module.module_id \
            or evidence["scope_ref"]["snapshot_id"] != scope.snapshot_id:
        raise ValueError("ModuleEvidence does not match ModuleScope")
    path = Path(destination)
    write_new_text(path, render_module_prd(scope, evidence, language=language))
    return path
