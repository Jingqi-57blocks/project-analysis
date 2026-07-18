"""Page shell, navigation, and reusable view assembly.

Pure layout: given already-prepared HTML fragments, this builds each page's
full document (shared nav + head + body) deterministically. It decides *where*
things sit and how the report is navigated; it never derives content.

The report is a small multi-page static site (main page + purpose-built
sub-pages + lossless full-document views), so the persistent nav and consistent
shell are what make it a designed report rather than a rendered Markdown dump.
"""

from __future__ import annotations

from dataclasses import dataclass

from .assets import APP_SCRIPT, MERMAID_SCRIPT, STYLESHEET
from .htmlutil import attr, esc
from .markdown_render import MarkdownDoc
from .run_inputs import DocSource, RunInputs

PAGE_FILES = {
    "index": "index.html",
    "findings": "findings.html",
    "coverage": "coverage.html",
    "topology": "topology.html",
    "modules": "modules.html",
    "documents": "documents.html",
}


def doc_page(doc_id: str) -> str:
    return f"doc-{doc_id}.html"


# Chrome (UI) strings only — never applied to source content, which is rendered
# verbatim. Localized so a zh-CN run reads natively; defaults to English.
_CHROME = {
    "en": {
        "index": "Report overview",
        "findings": "Findings & diagnosis",
        "coverage": "Evidence & coverage",
        "topology": "System topology",
        "modules": "Modules",
        "documents": "Documents",
        "full_docs": "Full documents",
        "subtitle": "Offline project-analysis report",
        "theme": "Toggle theme",
        "on_this_page": "On this page",
    },
    "zh-CN": {
        "index": "报告总览",
        "findings": "发现与诊断",
        "coverage": "证据与覆盖",
        "topology": "系统拓扑",
        "modules": "模块",
        "documents": "文档",
        "full_docs": "完整文档",
        "subtitle": "离线项目分析报告",
        "theme": "切换主题",
        "on_this_page": "本页目录",
    },
}


def chrome(language: str) -> dict:
    return _CHROME.get(language, _CHROME["en"])


@dataclass(frozen=True)
class Page:
    page_id: str
    filename: str
    html: str


def _nav(active: str, inputs: RunInputs) -> str:
    c = chrome(inputs.language)
    primary = [
        ("index", c["index"]),
        ("findings", c["findings"]),
        ("coverage", c["coverage"]),
        ("topology", c["topology"]),
        ("modules", c["modules"]),
        ("documents", c["documents"]),
    ]
    links = []
    for page_id, label in primary:
        cls = "nav-link active" if page_id == active else "nav-link"
        links.append(
            f'<a class="{cls}" href="{attr(PAGE_FILES[page_id])}">{esc(label)}</a>'
        )
    full_docs = []
    for d in inputs.docs:
        cls = "nav-link active" if active == f"doc-{d.doc_id}" else "nav-link"
        full_docs.append(
            f'<a class="{cls}" href="{attr(doc_page(d.doc_id))}">{esc(d.title)}</a>'
        )
    full_docs_group = (
        f'<div class="nav-group"><h2>{esc(c["full_docs"])}</h2>{"".join(full_docs)}</div>'
        if full_docs else ""
    )
    return (
        '<nav class="nav" aria-label="report navigation">'
        f'<h1>{esc(inputs.project_id)}</h1>'
        f'<p class="project-id">{esc(c["subtitle"])}</p>'
        f'<div class="nav-group">{"".join(links)}</div>'
        f"{full_docs_group}"
        f'<button type="button" class="theme-toggle">{esc(c["theme"])}</button>'
        "</nav>"
    )


def shell(inputs: RunInputs, active: str, title: str, subtitle: str, body: str) -> str:
    """Wrap a page body in the shared shell (nav + head + scripts)."""
    return (
        "<!doctype html>\n"
        f'<html lang="{attr(inputs.language)}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)} · {esc(inputs.project_id)}</title>\n"
        f'<link rel="stylesheet" href="{attr(STYLESHEET)}">\n'
        "</head>\n<body>\n"
        '<div class="layout">\n'
        f"{_nav(active, inputs)}\n"
        '<main class="main">\n'
        '<header class="page-head">'
        f"<h1>{esc(title)}</h1>"
        f'<p class="subtitle">{esc(subtitle)}</p></header>\n'
        f"{body}\n"
        "</main>\n</div>\n"
        f'<script src="{attr(MERMAID_SCRIPT)}"></script>\n'
        f'<script src="{attr(APP_SCRIPT)}"></script>\n'
        "</body>\n</html>\n"
    )


def section(anchor: str, title: str, html: str) -> str:
    """A titled content section with a stable anchor."""
    return (
        f'<section class="section" id="{attr(anchor)}">'
        f'<h2 class="doc-h">{esc(title)}</h2>{html}</section>'
    )


def full_document_page(inputs: RunInputs, doc: DocSource, rendered: MarkdownDoc) -> Page:
    """A lossless full-document view with an in-page table of contents."""
    c = chrome(inputs.language)
    toc_items = []
    for sec in rendered.sections:
        if sec.level > 3:
            continue
        cls = f"outline-item outline-l{sec.level}"
        toc_items.append(
            f'<li class="{cls}"><a href="#{attr(sec.anchor)}">{esc(sec.text)}</a></li>'
        )
    toc = (
        f'<nav class="doc-toc" aria-label="table of contents">'
        f'<button class="doc-toc-toggle" aria-expanded="true" aria-controls="doc-toc-panel">'
        f'{esc(c["on_this_page"])}</button>'
        f'<div class="doc-toc-panel" id="doc-toc-panel">'
        f'<ul class="outline">{"".join(toc_items)}</ul></div></nav>'
        if toc_items else ""
    )
    body = toc + f'<article class="doc-body">{rendered.html}</article>'
    filename = doc_page(doc.doc_id)
    html = shell(inputs, f"doc-{doc.doc_id}", doc.title, doc.filename, body)
    return Page(f"doc-{doc.doc_id}", filename, html)
