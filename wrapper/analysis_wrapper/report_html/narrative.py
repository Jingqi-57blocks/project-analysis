"""Section-aware narrative components.

These place *LLM-authored prose* into designed sections using the Markdown AST's
own heading structure — never by recognizing what a heading *means* (that would
be project- and language-specific semantic extraction, which the semantic
boundary forbids). A section's verbatim heading carries its meaning; the code
only carries structure.

Every card links back to the section's lossless full-document destination, so
designed summaries add zero content loss. The functions also return the extra
content-map destinations they create.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .content_map import Destination
from .htmlutil import attr, esc, slugify
from .markdown_render import MarkdownDoc
from .run_inputs import DrilldownModule, RunInputs


@dataclass
class NarrativeBlock:
    html: str
    # (source_doc_id, source_anchor, Destination) for the content map.
    destinations: list[tuple[str, str, Destination]]
    # (anchor, title) entries for this page's TOC drawer.
    toc: list[tuple[str, str]] = field(default_factory=list)


_MODULE_LABELS = {
    "en": {"drilldown": "Module drill-down", "map": "System module map"},
    "zh-CN": {"drilldown": "模块下钻", "map": "系统模块图"},
}


def document_outline(rendered: MarkdownDoc, full_page: str, *, max_level: int = 3) -> str:
    """A compact, clickable outline of a document's headings (links to full doc)."""
    items = []
    for sec in rendered.sections:
        if sec.level > max_level:
            continue
        indent = f" outline-l{sec.level}"
        items.append(
            f'<li class="outline-item{indent}">'
            f'<a href="{attr(full_page)}#{attr(sec.anchor)}">{esc(sec.text)}</a></li>'
        )
    if not items:
        return '<p class="muted">No sections.</p>'
    return f'<ul class="outline">{"".join(items)}</ul>'


def narrative_cards(
    rendered: MarkdownDoc, full_page: str, this_page: str, *, card_level: int = 2
) -> NarrativeBlock:
    """Render a document's top-level sections as searchable, collapsible cards.

    Each card is one ``card_level`` section (its heading + full body, nested
    subsections included), rendered verbatim from the AST. A card links to its
    lossless full-document destination and registers a ``markdown-section``
    content-map entry.
    """
    cards = []
    destinations: list[tuple[str, str, Destination]] = []
    toc: list[tuple[str, str]] = []
    for sec in rendered.sections:
        if sec.level != card_level:
            continue
        body = rendered.section_html(sec.anchor) or ""
        search_key = esc(sec.text.lower())
        cards.append(
            f'<article class="card" data-search="{search_key}" id="{attr(sec.anchor)}">'
            f'<div class="card-body">{body}</div>'
            f'<p class="card-source"><a href="{attr(full_page)}#{attr(sec.anchor)}">'
            f"open in full document →</a></p></article>"
        )
        destinations.append(
            (rendered.doc_id, sec.anchor, Destination(this_page, sec.anchor, "markdown-section"))
        )
        toc.append((sec.anchor, sec.text))
    if not cards:
        return NarrativeBlock('<p class="muted">No narrative sections.</p>', [])

    search = (
        '<div class="filter-bar"><input type="text" name="narrative-filter" '
        'class="filter-input" data-filter-target="narrative-cards" '
        'placeholder="filter sections…" aria-label="filter sections"></div>'
    )
    grouping_note = (
        '<div class="note"><p>Impact / lens / module grouping is a structured '
        "component. This run has no machine-readable findings artifact, so the "
        "diagnosis is presented as its authored sections (verbatim), grouped by "
        "the document’s own structure. Grouping facets are "
        "<strong>unavailable</strong> — never inferred from prose.</p></div>"
    )
    html = (
        grouping_note + search
        + f'<div class="cards" id="narrative-cards">{"".join(cards)}</div>'
    )
    return NarrativeBlock(html, destinations, toc)


def module_entrance(
    inputs: RunInputs,
    project_map: MarkdownDoc | None,
    project_map_page: str | None,
    module_page_for: "callable | None" = None,
) -> NarrativeBlock:
    """The Modules entrance: an honest stub today, auto-lit when drill-down exists.

    Never fabricates module content. It detects whether per-module drill-down
    artifacts (``prd.md`` / ``health.md``) are present and renders the matching
    state. The system-level module map (authored narrative in the Project Map) is
    always linked so the entrance is useful even in the stub state.
    """
    labels = _MODULE_LABELS.get(inputs.language, _MODULE_LABELS["en"])
    modules = inputs.drilldown_modules

    if not modules:
        drill = (
            '<div class="note note-stub"><p><strong>Module drill-down is not yet '
            "available for this run.</strong> Per-module PM PRDs and developer "
            "health reports are Phase 2 artifacts. This entrance lights up "
            "automatically once <code>drilldown/&lt;module&gt;/prd.md</code> or "
            "<code>health.md</code> are present in the run.</p></div>"
        )
    else:
        rows = []
        for m in sorted(modules, key=lambda x: x.module_id):
            links = []
            if m.has_prd and module_page_for:
                links.append(f'<a href="{attr(module_page_for(m, "prd"))}">PM PRD</a>')
            if m.has_health and module_page_for:
                links.append(f'<a href="{attr(module_page_for(m, "health"))}">Dev health</a>')
            rows.append(
                f"<tr><td>{esc(m.module_id)}</td>"
                f'<td>{" · ".join(links) or "—"}</td></tr>'
            )
        drill = (
            '<div class="note note-live"><p><strong>Module drill-down available.'
            "</strong> PM-facing PRDs and developer-facing health reports render "
            "as lossless documents below.</p></div>"
            '<div class="table-scroll"><table class="data"><thead><tr>'
            "<th>module</th><th>views</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>"
        )

    if project_map and project_map_page:
        map_html = (
            '<p class="muted">The system-level module map (authored narrative) '
            f'lives in the <a href="{attr(project_map_page)}">Project Map</a>.</p>'
        )
    else:
        map_html = '<p class="muted">No project map document present in this run.</p>'

    html = (
        f'<section class="section" id="module-drilldown">'
        f'<h2 class="doc-h">{esc(labels["drilldown"])}</h2>{drill}</section>'
        f'<section class="section" id="module-map">'
        f'<h2 class="doc-h">{esc(labels["map"])}</h2>{map_html}</section>'
    )
    toc = [
        ("module-drilldown", labels["drilldown"]),
        ("module-map", labels["map"]),
    ]
    return NarrativeBlock(html, [], toc)


def drilldown_page_id(module: DrilldownModule, kind: str) -> str:
    """Deterministic page id for a rendered drill-down document."""
    return f"module-{slugify(module.module_id)}-{kind}"
