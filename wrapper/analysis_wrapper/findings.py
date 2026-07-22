"""Validated atomic findings and deterministic report projections.

The LLM supplies diagnosis text, but every supporting statement is an atomic
fact with independently inspectable refs.  Rendering is machine-owned so a
later synthesis pass cannot merge unrelated evidence or add unsupported
subclaims to the findings sections.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re

from .executor import replace_artifact_text
from . import identity
from .lifecycle import RunState
from .sanitize import sanitize_text
from .targetspec import TargetSpec

SCHEMA_VERSION = "2.0.0"
FINDINGS_FILE = "findings.json"
TECHNICAL_FILE = "findings-summary.md"
PM_FILE = "findings-pm-summary.md"
TECHNICAL_BEGIN = "<!-- BEGIN MACHINE VERIFIED FINDINGS -->"
TECHNICAL_END = "<!-- END MACHINE VERIFIED FINDINGS -->"
PM_BEGIN = "<!-- BEGIN MACHINE PM FINDINGS -->"
PM_END = "<!-- END MACHINE PM FINDINGS -->"

_ID = re.compile(r"^finding-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_SIGNAL = re.compile(r"^signals/([^:]+):(\d+)$")
_METRIC = re.compile(r"^(?:metric:|workspace-metrics\.json#metric:)(.+)$")
_PRIORITIES = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_CONFIDENCE = {"high": 0, "medium": 1, "low": 2}
_BASES = {
    "static-reference", "declaration", "configuration", "history",
    "inferred-linkage", "runtime-observation", "user-confirmed",
}

_LABELS = {
    "en": {
        "top": "Top problems", "lens": "Lens", "confidence": "Confidence",
        "basis": "Evidence basis", "modules": "Affected modules",
        "evidence": "Atomic evidence", "impact": "Impact",
        "limitations": "Limitations", "direction": "Suggested direction",
        "observed_impact": "Observed impact", "pm_evidence": "Evidence",
    },
    "zh-CN": {
        "top": "主要问题", "lens": "分析维度", "confidence": "置信度",
        "basis": "证据类型", "modules": "受影响模块",
        "evidence": "原子证据", "impact": "影响",
        "limitations": "局限", "direction": "方向建议",
        "observed_impact": "已观察到的影响", "pm_evidence": "证据",
    },
}


def _load(path: Path) -> dict:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _one_line(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise ValueError(f"{label} must be one non-empty line")
    return text


def _safe_relative(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"{label} must be a safe relative path")
    return path


def _source_parts(ref: str) -> tuple[str, str, str, str] | None:
    repository_ref, marker, tail = ref.rpartition("@")
    revision, separator, position = tail.partition(":")
    relative, line_separator, line = position.rpartition(":")
    if not marker or not separator or not line_separator or not repository_ref \
            or not revision or not relative or not line.isdigit():
        return None
    return repository_ref, revision, relative, line


def _validate_source_ref(ref: str, spec: TargetSpec,
                         identities: identity.IdentityMap) -> None:
    parts = _source_parts(ref)
    if not parts:
        raise ValueError(f"invalid source ref: {ref}")
    repository_ref, revision, relative, line_text = parts
    target = spec.repo(identities.internal_id_for(repository_ref))
    expected = ("NON-GIT" if not target.git.is_git else
                "WORKTREE" if target.git.dirty_detail != "no" else target.git.head.lower())
    if revision.lower() != expected.lower():
        raise ValueError(
            f"source ref revision mismatch for {repository_ref}: {revision}")
    relative_path = _safe_relative(relative, "source ref path")
    root = Path(target.path).expanduser().resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"source ref file missing or outside target: {ref}")
    line = int(line_text)
    line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
    if line < 1 or line > line_count:
        raise ValueError(f"source ref line out of range: {ref}")


def _signal_views(run: Path) -> dict[str, str]:
    summary = _load(run / "signals" / "run-summary.json")
    return {
        str(row.get("view")): str(row.get("tool", "unknown"))
        for row in summary.get("signals", [])
        if isinstance(row, dict)
        and row.get("status") in {"complete", "partial"}
        and str(row.get("view", "")).endswith(".view.txt")
    }


def _validate_signal_ref(ref: str, run: Path, allowed_views: dict[str, str]) -> None:
    match = _SIGNAL.fullmatch(ref)
    if not match:
        raise ValueError(f"invalid signal ref: {ref}")
    relative, line_text = match.groups()
    relative_path = _safe_relative(relative, "signal ref path")
    if relative not in allowed_views:
        raise ValueError(f"signal ref is not an indexed sanitized view: {ref}")
    path = (run / "signals" / relative_path).resolve()
    signals = (run / "signals").resolve()
    if not path.is_relative_to(signals) or not path.is_file():
        raise ValueError(f"signal ref missing, raw, or outside signals: {ref}")
    line = int(line_text)
    line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
    if line < 1 or line > line_count:
        raise ValueError(f"signal ref line out of range: {ref}")


def _validate_ref(ref: str, run: Path, spec: TargetSpec,
                  identities: identity.IdentityMap,
                  metric_refs: set[str], allowed_views: dict[str, str]) -> None:
    metric = _METRIC.fullmatch(ref)
    if metric:
        if metric.group(1) not in metric_refs:
            raise ValueError(f"unknown metric ref: {ref}")
        return
    if ref.startswith("signals/"):
        _validate_signal_ref(ref, run, allowed_views)
        return
    _validate_source_ref(ref, spec, identities)


def _metric_origins(row: dict, run: Path) -> set[str]:
    origins: set[str] = set()
    for source_ref in row.get("source_refs", []):
        source = str(source_ref).split("#", 1)[0]
        if source.startswith("signals/"):
            path = run / source
            try:
                tool = str(_load(path).get("tool", "unknown"))
            except (OSError, ValueError, json.JSONDecodeError):
                tool = "unknown"
            origins.add(f"signal-tool:{tool}")
        else:
            origins.add(f"artifact:{source}")
    return origins or {"artifact:workspace-metrics"}


def _independent_ref_keys(ref: str, *, run: Path,
                          allowed_views: dict[str, str],
                          metric_origins: dict[str, set[str]]) -> set[str]:
    metric = _METRIC.fullmatch(ref)
    if metric:
        return metric_origins.get(metric.group(1), {"artifact:workspace-metrics"})
    signal = _SIGNAL.fullmatch(ref)
    if signal:
        return {f"signal-tool:{allowed_views.get(signal.group(1), 'unknown')}"}
    source = _source_parts(ref)
    if source:
        return {f"source-repo:{source[0]}"}
    return {ref}


def _labels(run: Path) -> dict[str, str]:
    language = RunState.load(run).language if (run / RunState.FILENAME).is_file() else "en"
    return _LABELS.get(language, _LABELS["en"])


def validate(run_dir: str | Path) -> dict:
    run = Path(run_dir).expanduser().resolve()
    doc = _load(run / FINDINGS_FILE)
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"findings schema_version must be {SCHEMA_VERSION}")
    rows = doc.get("findings")
    if not isinstance(rows, list):
        raise ValueError("findings must be a list")
    spec = TargetSpec.load(run / "targets.json")
    identities = identity.load(run)
    metrics = _load(run / "workspace-metrics.json")
    metric_rows = {str(row.get("metric_ref", "")): row
                   for row in metrics.get("metrics", []) if isinstance(row, dict)}
    metric_refs = set(metric_rows)
    metric_origins = {ref: _metric_origins(row, run) for ref, row in metric_rows.items()}
    allowed_views = _signal_views(run)
    module_doc = _load(run / "module-map.json")
    module_ids = {str(row.get("module_id", "")) for row in module_doc.get("modules", [])}
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"findings[{index}] must be an object")
        finding_id = _one_line(row.get("finding_id"), f"findings[{index}].finding_id")
        if not _ID.fullmatch(finding_id) or finding_id in seen:
            raise ValueError(f"invalid or duplicate finding_id: {finding_id}")
        seen.add(finding_id)
        for key in ("claim", "lens", "impact", "limitations", "suggested_direction"):
            _one_line(row.get(key), f"{finding_id}.{key}")
        if row.get("priority") not in _PRIORITIES:
            raise ValueError(f"{finding_id}.priority is invalid")
        if row.get("confidence") not in _CONFIDENCE:
            raise ValueError(f"{finding_id}.confidence is invalid")
        affected = row.get("affected_modules")
        if not isinstance(affected, list) or not affected or not all(
                isinstance(value, str) and value in module_ids for value in affected):
            raise ValueError(f"{finding_id}.affected_modules must use finalized module IDs")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"{finding_id}.evidence must be non-empty")
        bases = set()
        independent_refs: set[str] = set()
        for evidence_index, item in enumerate(evidence):
            if not isinstance(item, dict):
                raise ValueError(f"{finding_id}.evidence[{evidence_index}] must be an object")
            _one_line(item.get("fact"), f"{finding_id}.evidence[{evidence_index}].fact")
            basis = str(item.get("basis", ""))
            if basis not in _BASES:
                raise ValueError(f"{finding_id}.evidence[{evidence_index}].basis is invalid")
            if basis in {"runtime-observation", "user-confirmed"}:
                raise ValueError(
                    f"{finding_id}.evidence[{evidence_index}].basis {basis} "
                    "has no supported provenance artifact in a static overview")
            bases.add(basis)
            refs = item.get("refs")
            if not isinstance(refs, list) or not refs:
                raise ValueError(f"{finding_id}.evidence[{evidence_index}].refs is empty")
            for ref in refs:
                normalized_ref = _one_line(ref, "evidence ref")
                _validate_ref(normalized_ref, run, spec, identities,
                              metric_refs, allowed_views)
                independent_refs.update(_independent_ref_keys(
                    normalized_ref, run=run,
                    allowed_views=allowed_views, metric_origins=metric_origins))
        declared_bases = row.get("evidence_basis")
        if not isinstance(declared_bases, list) or set(declared_bases) != bases:
            raise ValueError(f"{finding_id}.evidence_basis must equal evidence bases")
        if row.get("confidence") == "high" and (
                len(evidence) < 2 or len(independent_refs) < 2):
            raise ValueError(
                f"{finding_id}.confidence high requires at least two independent signals")
    return doc


def _ordered(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (
        _PRIORITIES[row["priority"]], _CONFIDENCE[row["confidence"]],
        -len(set(row.get("affected_modules", []))), row["finding_id"]))


def _refs(refs: list[str]) -> str:
    return ", ".join(f"`{ref}`" for ref in refs)


def render_technical(run_dir: str | Path) -> str:
    run = Path(run_dir).expanduser().resolve()
    rows = _ordered(validate(run)["findings"])
    label = _labels(run)
    lines = [TECHNICAL_BEGIN, f"## {label['top']}", ""]
    for row in rows:
        lines += [f"<a id=\"{row['finding_id']}\"></a>",
                  f"### {row['finding_id']}: {row['claim']} — `{row['priority']}`",
                  f"- **{label['lens']}:** {row['lens']} · **{label['confidence']}:** {row['confidence']} · "
                  f"**{label['basis']}:** {', '.join(sorted(row['evidence_basis']))}",
                  f"- **{label['modules']}:** {', '.join(f'`{value}`' for value in sorted(set(row['affected_modules'])))}",
                  f"- **{label['evidence']}:**"]
        for item in row["evidence"]:
            lines.append(f"  - {item['fact']} — {_refs(item['refs'])} · basis=`{item['basis']}`")
        lines += [f"- **{label['impact']}:** {row['impact']}",
                  f"- **{label['limitations']}:** {row['limitations']}",
                  f"- **{label['direction']}:** {row['suggested_direction']}", ""]
    lines += [TECHNICAL_END, ""]
    return "\n".join(lines)


def render_pm(run_dir: str | Path) -> str:
    run = Path(run_dir).expanduser().resolve()
    rows = _ordered(validate(run)["findings"])[:7]
    label = _labels(run)
    lines = [PM_BEGIN, ""]
    for row in rows:
        modules = ", ".join(f"`{value}`" for value in sorted(set(row["affected_modules"])))
        lines += [f"### {row['claim']}",
                  f"- **{label['modules']}:** {modules}",
                  f"- **{label['observed_impact']}:** {row['impact']}",
                  f"- **{label['pm_evidence']}:** [{row['finding_id']}](technical-overview.md#{row['finding_id']})",
                  f"- **{label['confidence']}:** {row['confidence']}",
                  f"- **{label['limitations']}:** {row['limitations']}", ""]
    lines += [PM_END, ""]
    return "\n".join(lines)


def write(run_dir: str | Path) -> tuple[Path, Path]:
    run = Path(run_dir).expanduser().resolve()
    technical = run / TECHNICAL_FILE
    pm = run / PM_FILE
    replace_artifact_text(technical, sanitize_text(render_technical(run)))
    replace_artifact_text(pm, sanitize_text(render_pm(run)))
    return technical, pm


def _extract(text: str, begin: str, end: str) -> str:
    if text.count(begin) != 1 or text.count(end) != 1:
        return ""
    start = text.find(begin)
    finish = text.find(end, start + len(begin)) if start >= 0 else -1
    if start < 0 or finish < 0:
        return ""
    return text[start:finish + len(end)] + "\n"


def extract_technical(text: str) -> str:
    return _extract(text, TECHNICAL_BEGIN, TECHNICAL_END)


def extract_pm(text: str) -> str:
    return _extract(text, PM_BEGIN, PM_END)
