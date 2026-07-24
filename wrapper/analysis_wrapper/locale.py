"""Bundled locale catalog for presentation-layer strings.

All rendered UI/report text (Markdown findings labels, HTML chrome, section
labels, structured-component headers) is data, not code: it lives in one flat,
namespaced catalog instead of five scattered per-file dictionaries, each with
its own ``{"en": ..., "zh-CN": ...}`` shape and its own fallback logic. Keys
are dotted namespaces that mirror where they render — e.g. ``findings.top``,
``chrome.nav.index``, ``narrative.drilldown``, ``landing.run_status``,
``components.snapshot.title`` — chosen to preserve the exact meaning of the
string each call site used before consolidation. No rendered byte changes for
an existing language as a result of this module existing.

This module is data-only and import-free of the analysis plane: nothing that
produces findings, coverage, the system model, or evidence may import it.
Only presentation code may: ``findings.py``'s ``render_*`` functions, and
everything under ``report_html/`` and ``export/``.

``zh-CN`` values that are direct human translations of existing labels are
carried over verbatim from the dictionaries they replace. One documented
subset has no human translation yet — the ``components.*`` keys, sourced from
English-hardcoded literals in ``report_html/components.py`` — so those
entries mirror the English value exactly: rendering today is English in both
locales and must stay byte-identical until a translator supplies real values.
"""

from __future__ import annotations

from collections.abc import Mapping

_EN: dict[str, str] = {
    # -- findings.py: Markdown findings labels ------------------------------
    "findings.top": "Top problems",
    "findings.lens": "Lens",
    "findings.confidence": "Confidence",
    "findings.basis": "Evidence basis",
    "findings.modules": "Affected modules",
    "findings.evidence": "Atomic evidence",
    "findings.impact": "Impact",
    "findings.limitations": "Limitations",
    "findings.direction": "Suggested direction",
    "findings.observed_impact": "Observed impact",
    "findings.pm_evidence": "Evidence",

    # -- report_html/pages.py: chrome (nav + shell strings) -----------------
    "chrome.nav.index": "Report overview",
    "chrome.nav.findings": "Findings & diagnosis",
    "chrome.nav.coverage": "Evidence & coverage",
    "chrome.nav.topology": "System topology",
    "chrome.nav.modules": "Modules",
    "chrome.nav.documents": "Documents",
    "chrome.full_docs": "Full documents",
    "chrome.subtitle": "Offline project-analysis report",
    "chrome.theme": "Toggle theme",
    "chrome.on_this_page": "On this page",

    # -- report_html/narrative.py: module drill-down entrance ---------------
    "narrative.drilldown": "Module drill-down",
    "narrative.map": "System module map",

    # -- report_html/generate.py: landing page + run-status note ------------
    "landing.run_status": "Run status",
    "landing.diagnosis": "Diagnosis",
    "landing.diagnosis_unavailable": "No narrative document present.",
    "landing.inspection_only_message": (
        "<strong>Inspection-only:</strong> at least one repository was not a "
        "clean worktree during analysis; this run cannot be accepted as current."
    ),

    # -- report_html/components.py: structured-component headers/labels -----
    # English-only today (no human zh-CN translation yet); see module
    # docstring — zh-CN mirrors these verbatim below.
    "components.header.repository": "repository",
    "components.header.commit": "commit",
    "components.header.stacks": "stacks",
    "components.header.frameworks": "frameworks",
    "components.header.package_manager": "package manager",
    "components.header.commits": "commits",
    "components.header.head": "HEAD",
    "components.header.working_tree_dirty": "working tree dirty",
    "components.header.status": "status",
    "components.header.tool": "tool",
    "components.header.detail": "detail",
    "components.header.lens": "lens",
    "components.header.counts": "counts",
    "components.header.unresolved": "unresolved",
    "components.header.caps": "caps",
    "components.header.edge_count": "edge count",
    "components.header.meaning": "meaning",
    "components.header.boundary_kind": "boundary kind",
    "components.header.count": "count",
    "components.header.labels": "labels",
    "components.header.table": "table",
    "components.header.access_types": "access types",

    "components.snapshot.title": "System snapshot",
    "components.snapshot.unavailable": (
        "system-model.json is absent; the compact snapshot is unavailable."
    ),
    "components.snapshot.tile.repositories": "repositories",
    "components.snapshot.tile.modules": "modules",
    "components.snapshot.tile.deployable_units": "deployable units",
    "components.snapshot.tile.languages": "languages",
    "components.snapshot.tile.data_stores": "data stores",
    "components.snapshot.tile.routes": "routes",
    "components.snapshot.tile.external_boundaries": "external boundaries",
    "components.snapshot.tile.symbols": "symbols",
    "components.snapshot.tile.data_stores_note": "distinct tables",
    "components.snapshot.tile.external_boundaries_note": "incl. dependency candidates",
    "components.snapshot.modules_note": "synthesis-inferred; not machine-computed",

    "components.provenance.title": "Analyzed revisions",
    "components.provenance.unavailable": "run-state.json carries no provenance rows.",
    "components.provenance.analyzed_at": "Analyzed at",
    "components.provenance.language": "language",
    "components.provenance.run": "run",

    "components.coverage.title": "Lens coverage",
    "components.coverage.unavailable": (
        "system-model.json is absent; per-lens coverage is unavailable."
    ),
    "components.coverage.cap_label": "cap(s)",

    "components.legend.title": "Relationship status",
    "components.legend.unavailable": (
        "system-model.json is absent; edge status counts are unavailable."
    ),
    "components.legend.meaning.observed": (
        "recorded directly by a tool (e.g. a resolved call/import edge)"
    ),
    "components.legend.meaning.inferred": "derived by a producer with lower certainty",
    "components.legend.meaning.unresolved": (
        "a call/import site seen but its target not resolvable"
    ),
    "components.legend.meaning.unavailable": "no producer supplied this relationship class",
    "components.legend.edge_types_prefix": "Edge types:",

    "components.callgraph_coverage.title": "Call-graph coverage (per repository)",
    "components.depmap_coverage.title": "Dependency-map coverage (per repository)",
    "components.per_repo.absent_suffix": "coverage report absent.",

    "components.topology.title": "System topology (structured)",
    "components.topology.unavailable": (
        "system-model.json is absent; the structured topology is unavailable."
    ),
    "components.topology.note": (
        "Built from structured <code>route-linkage</code> and <code>data</code> "
        "edges (which routes the frontend calls; which repos touch persistence). "
        "Inter-service business roles and named external systems are "
        "synthesis-inferred narrative — see the authored topology and the "
        "Project Map."
    ),

    "components.externals.title": "External boundaries",
    "components.externals.unavailable": (
        "system-model.json is absent; external boundaries are unavailable."
    ),
    "components.externals.placeholder": "filter boundary kinds…",
    "components.externals.note": (
        "Resolved hosts/packages (<code>host-fragment</code>, "
        "<code>integration-package</code>) are named external systems; "
        "<code>integration-candidate</code> entries are dependency-manifest "
        "candidates, not confirmed runtime integrations."
    ),

    "components.datastores.title": "Data stores",
    "components.datastores.unavailable": (
        "system-model.json is absent; data stores are unavailable."
    ),
    "components.datastores.placeholder": "filter tables…",
}

_ZH_CN: dict[str, str] = {
    # -- findings.py ---------------------------------------------------------
    "findings.top": "主要问题",
    "findings.lens": "分析维度",
    "findings.confidence": "置信度",
    "findings.basis": "证据类型",
    "findings.modules": "受影响模块",
    "findings.evidence": "原子证据",
    "findings.impact": "影响",
    "findings.limitations": "局限",
    "findings.direction": "方向建议",
    "findings.observed_impact": "已观察到的影响",
    "findings.pm_evidence": "证据",

    # -- report_html/pages.py -------------------------------------------------
    "chrome.nav.index": "报告总览",
    "chrome.nav.findings": "发现与诊断",
    "chrome.nav.coverage": "证据与覆盖",
    "chrome.nav.topology": "系统拓扑",
    "chrome.nav.modules": "模块",
    "chrome.nav.documents": "文档",
    "chrome.full_docs": "完整文档",
    "chrome.subtitle": "离线项目分析报告",
    "chrome.theme": "切换主题",
    "chrome.on_this_page": "本页目录",

    # -- report_html/narrative.py ----------------------------------------------
    "narrative.drilldown": "模块下钻",
    "narrative.map": "系统模块图",

    # -- report_html/generate.py -----------------------------------------------
    "landing.run_status": "运行状态",
    "landing.diagnosis": "诊断",
    "landing.diagnosis_unavailable": "没有可用的叙述性文档。",
    "landing.inspection_only_message": (
        "<strong>仅供检查：</strong>分析时至少一个仓库不是干净工作树；"
        "此运行不能被接受为 current。"
    ),

    # -- report_html/components.py: no human zh-CN translation exists yet for
    # structured-component chrome; see module docstring. Populated below from
    # _EN so the two catalogs share the exact same "components.*" values.
}
_ZH_CN.update(
    (key, value) for key, value in _EN.items() if key.startswith("components.")
)

BUNDLED_LOCALES: dict[str, dict[str, str]] = {
    "en": _EN,
    "zh-CN": _ZH_CN,
}

_registry: dict[str, dict[str, str]] = {
    code: dict(catalog) for code, catalog in BUNDLED_LOCALES.items()
}


def register_locale(code: str, catalog: Mapping[str, str]) -> None:
    """Additively register (or replace) a locale's catalog.

    Mirrors the exporter-registry idiom in ``export/__init__.py``: callers add
    a new locale from anywhere in presentation code without editing this
    module or touching any analysis-plane module.
    """
    _registry[code] = dict(catalog)


def labels(language: str) -> Mapping[str, str]:
    """The full label catalog for ``language``.

    A registered or bundled locale need not define every key: any gap falls
    back to the English value for that key rather than raising ``KeyError``.
    An unrecognized language code falls back to English entirely.
    """
    en = _registry["en"]
    if language == "en":
        return en
    catalog = _registry.get(language)
    if not catalog:
        return en
    return {**en, **catalog}
