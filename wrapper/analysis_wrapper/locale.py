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
Presentation code may: ``findings.py``'s ``render_*`` functions, and
everything under ``report_html/`` and ``export/``. Run-metadata/provenance
code may also import it — ``run_provenance.py`` uses ``is_delivered``/
``missing_keys``/``delivered_languages`` to gate which language a run may be
started in; that is metadata about a run, not analysis output, and creates no
cycle back into the analysis plane.

Every key in the English reference catalog has a genuine, human-authored
``zh-CN`` translation (57B-111) — no catalog value is byte-identical to its
English counterpart, except the handful of intentionally-identical keys in
``_MIRROR_ALLOWLIST`` (verbatim identifiers such as a bare git ref name).
``mirrored_keys()`` is the machine-checkable signal for this; see the
"Delivered-language completeness" section below.
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

    # -- report_html/components.py: structured-component headers/labels -----
    "components.header.repository": "仓库",
    "components.header.commit": "提交",
    "components.header.stacks": "技术栈",
    "components.header.frameworks": "框架",
    "components.header.package_manager": "包管理器",
    "components.header.commits": "提交数",
    # "HEAD" is git's own ref name, a verbatim identifier -- see
    # _MIRROR_ALLOWLIST below; kept in English by design.
    "components.header.head": "HEAD",
    "components.header.working_tree_dirty": "工作树不干净",
    "components.header.status": "状态",
    "components.header.tool": "工具",
    "components.header.detail": "详情",
    "components.header.lens": "维度",
    "components.header.counts": "计数",
    "components.header.unresolved": "未解决",
    "components.header.caps": "上限",
    "components.header.edge_count": "边数",
    "components.header.meaning": "含义",
    "components.header.boundary_kind": "边界类型",
    "components.header.count": "数量",
    "components.header.labels": "标签",
    "components.header.table": "表",
    "components.header.access_types": "访问类型",

    "components.snapshot.title": "系统快照",
    "components.snapshot.unavailable": (
        "system-model.json 缺失；精简快照不可用。"
    ),
    "components.snapshot.tile.repositories": "仓库",
    "components.snapshot.tile.modules": "模块",
    "components.snapshot.tile.deployable_units": "可部署单元",
    "components.snapshot.tile.languages": "语言",
    "components.snapshot.tile.data_stores": "数据存储",
    "components.snapshot.tile.routes": "路由",
    "components.snapshot.tile.external_boundaries": "外部边界",
    "components.snapshot.tile.symbols": "符号",
    "components.snapshot.tile.data_stores_note": "不同的表",
    "components.snapshot.tile.external_boundaries_note": "含依赖候选项",
    "components.snapshot.modules_note": "综合推断；非机器计算",

    "components.provenance.title": "已分析修订",
    "components.provenance.unavailable": "run-state.json 未包含溯源记录。",
    "components.provenance.analyzed_at": "分析时间",
    "components.provenance.language": "语言",
    "components.provenance.run": "运行",

    "components.coverage.title": "维度覆盖率",
    "components.coverage.unavailable": (
        "system-model.json 缺失；各维度覆盖率不可用。"
    ),
    "components.coverage.cap_label": "上限",

    "components.legend.title": "关系状态",
    "components.legend.unavailable": (
        "system-model.json 缺失；边状态计数不可用。"
    ),
    "components.legend.meaning.observed": (
        "由工具直接记录（例如已解析的调用/导入边）"
    ),
    "components.legend.meaning.inferred": "由生成器以较低确定性推导得出",
    "components.legend.meaning.unresolved": (
        "观察到调用/导入位置，但目标无法解析"
    ),
    "components.legend.meaning.unavailable": "没有生成器提供此关系类别",
    "components.legend.edge_types_prefix": "边类型：",

    "components.callgraph_coverage.title": "调用图覆盖率（按仓库）",
    "components.depmap_coverage.title": "依赖图覆盖率（按仓库）",
    "components.per_repo.absent_suffix": "覆盖率报告缺失。",

    "components.topology.title": "系统拓扑（结构化）",
    "components.topology.unavailable": (
        "system-model.json 缺失；结构化拓扑不可用。"
    ),
    "components.topology.note": (
        "基于结构化的 <code>route-linkage</code> 与 <code>data</code> 边构建"
        "（前端调用哪些路由；哪些仓库涉及持久化存储）。服务间业务角色与已命名的"
        "外部系统属于综合推断的叙述内容——参见权威拓扑与项目地图。"
    ),

    "components.externals.title": "外部边界",
    "components.externals.unavailable": (
        "system-model.json 缺失；外部边界不可用。"
    ),
    "components.externals.placeholder": "筛选边界类型…",
    "components.externals.note": (
        "已解析的主机/包（<code>host-fragment</code>、"
        "<code>integration-package</code>）是已命名的外部系统；"
        "<code>integration-candidate</code> 条目是依赖清单候选项，"
        "并非已确认的运行时集成。"
    ),

    "components.datastores.title": "数据存储",
    "components.datastores.unavailable": (
        "system-model.json 缺失；数据存储不可用。"
    ),
    "components.datastores.placeholder": "筛选表…",
}

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
    module or touching any analysis-plane module. Registration never pads the
    catalog with English values — an incomplete registration stays visibly
    incomplete (see ``missing_keys``/``is_delivered``) rather than silently
    becoming "English in disguise".
    """
    _registry[code] = dict(catalog)


def labels(language: str) -> Mapping[str, str]:
    """The full label catalog for ``language``.

    A registered or bundled locale need not define every key: any gap falls
    back to the English value for that key rather than raising ``KeyError``
    (a last-resort runtime safety net so a partially-translated locale never
    crashes mid-render). An unrecognized language code falls back to English
    entirely. This per-key fallback is *silent* by design here — callers that
    need to know whether a fallback happened (e.g. the completeness gate)
    must consult ``missing_keys(language)`` explicitly rather than infer it
    from the returned mapping; see the module docstring's delivered-languages
    section for why the two questions ("what renders" vs. "is it complete")
    are kept separate.
    """
    en = _registry["en"]
    if language == "en":
        return en
    catalog = _registry.get(language)
    if not catalog:
        return en
    return {**en, **catalog}


# --------------------------------------------------------------------------- #
# Delivered-language completeness (57B-111)
# --------------------------------------------------------------------------- #
#
# A "delivered" language is one a run may be started in: its catalog covers
# every key the English reference catalog defines, so nothing in a fresh run
# silently renders English. ``labels()`` above still pads gaps with English at
# render time (so a partial catalog never crashes a run already in flight),
# but that padding must never be how a *run's primary language* is delivered —
# post-hoc translation is a separate, future feature. The functions below give
# the run-creation gate and the overview audit a precise, non-heuristic signal
# for "is this catalog actually complete", independent of what any single
# render call happens to touch.

REFERENCE_LANGUAGE = "en"


def missing_keys(language: str) -> list[str]:
    """Keys the English reference catalog defines that ``language`` lacks.

    Empty for a fully delivered catalog (including "en" itself, trivially).
    A language with no registered catalog at all is reported as missing every
    reference key.
    """
    reference = _registry[REFERENCE_LANGUAGE]
    catalog = _registry.get(language, {})
    return sorted(key for key in reference if key not in catalog)


# Keys that are intentionally byte-identical between English and a
# non-English catalog: verbatim identifiers with no meaningful translation.
# Every entry here needs a comment justifying why identity is correct rather
# than a missed translation. Checked for every non-English language — none of
# these are language-specific (a git ref name is just as untranslatable in
# any target language).
_MIRROR_ALLOWLIST: frozenset[str] = frozenset({
    # "HEAD" is git's own ref name, a verbatim identifier quoted from the
    # tool itself -- not a UI label to translate.
    "components.header.head",
})


def mirrored_keys(language: str) -> list[str]:
    """Non-English keys whose catalog value is byte-identical to English.

    A byte-identical value in a non-English catalog is suspicious: the most
    likely explanation is that no one actually translated the key, and it is
    silently rendering English chrome under a "delivered" label (see
    ``is_delivered``). ``_MIRROR_ALLOWLIST`` carves out the rare cases where
    identity is intentional (e.g. a bare git ref name) instead of treating
    every hit as a bug.

    Always empty for "en" itself and for any language with no registered
    catalog (there is nothing to compare).
    """
    if language == REFERENCE_LANGUAGE:
        return []
    reference = _registry[REFERENCE_LANGUAGE]
    catalog = _registry.get(language)
    if not catalog:
        return []
    return sorted(
        key for key, value in catalog.items()
        if key not in _MIRROR_ALLOWLIST
        and key in reference and reference[key] == value
    )


def is_delivered(language: str) -> bool:
    """Whether ``language`` is registered, key-complete, and free of any
    non-allowlisted English-mirrored value.

    All three conditions must hold for a language to be safe to start a run
    in: a registered catalog that covers every reference key, with no gap
    silently padded by ``labels()``, and no key whose "translation" is
    actually just an uncaught copy of the English value.
    """
    return (language in _registry and not missing_keys(language)
            and not mirrored_keys(language))


def delivered_languages() -> list[str]:
    """Every currently registered language whose catalog is key-complete and
    free of non-allowlisted English-mirrored values.

    Reflects live registry state, so a locale added via ``register_locale``
    after import counts once (and only once) its catalog is complete.
    """
    return sorted(lang for lang in _registry if is_delivered(lang))
