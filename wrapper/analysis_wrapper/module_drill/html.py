"""Self-contained HTML export for one completed Module Drill run.

This is deliberately a presentation adapter over immutable ``prd.md`` and
``health.md``.  It neither discovers new facts nor rewrites their prose.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..report_html import assets, content_map, pages
from ..report_html.content_map import DocEntry
from ..report_html.htmlutil import attr, esc
from ..report_html.markdown_render import render_document
from ..report_html.run_inputs import DocSource, _html_markdown
from .contracts import ModuleScope, load_scope
from .evidence import load_module_evidence
from .layout import MODULE_RUN_VERSION


_DOCS: tuple[tuple[str, str, str], ...] = (
    ("prd", "prd.md", "PM PRD"),
    ("health", "health.md", "Developer health"),
)

_UI = {
    "en": {
        "overview": "Module report overview", "prd": "PM PRD", "health": "Developer health",
        "documents": "Documents", "module": "Module", "source_mode": "Source mode",
        "snapshot": "Snapshot", "run": "Run", "coverage": "Coverage and unknowns",
        "unknown": "No unknowns were recorded in the evidence bundle.",
        "subtitle": "Module Drill · static source evidence",
    },
    "zh-CN": {
        "overview": "模块报告总览", "prd": "PM PRD", "health": "开发健康报告",
        "documents": "文档", "module": "模块", "source_mode": "来源模式",
        "snapshot": "快照", "run": "运行", "coverage": "覆盖与未知项",
        "unknown": "证据包未记录未知项。",
        "subtitle": "模块深钻 · 静态源码证据",
    },
}


@dataclass(frozen=True)
class ModuleHtmlInputs:
    run_dir: Path
    run_state: dict[str, Any]
    scope: ModuleScope
    evidence: dict[str, Any]
    docs: list[DocSource]

    @property
    def project_ref(self) -> str:
        return self.scope.project.project_ref

    @property
    def run_id(self) -> str:
        return str(self.run_state["run_id"])

    @property
    def language(self) -> str:
        return str(self.run_state["language"])


@dataclass
class ModuleHtmlResult:
    report_dir: Path
    pages: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    section_count: int = 0
    diagram_count: int = 0
    missing_artifacts: list[str] = field(default_factory=list)


def is_module_run(run_dir: str | Path) -> bool:
    path = Path(run_dir)
    return (path / "module-scope.json").is_file() or (path / "module-evidence.json").is_file()


def load_module_html_inputs(run_dir: str | Path) -> ModuleHtmlInputs:
    path = Path(run_dir).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"run directory not found: {path}")
    try:
        state = json.loads((path / "run-state.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load module run state: {exc}") from exc
    if not isinstance(state, dict) or state.get("contract_version") != MODULE_RUN_VERSION:
        raise ValueError("module run uses an unsupported contract")
    stages = state.get("stages")
    if not isinstance(stages, dict) or any(stages.get(stage) != "done" for stage in ("scope", "evidence", "prd", "health")):
        raise ValueError("module HTML export requires completed scope, evidence, PRD, and health stages")
    scope = load_scope(path / "module-scope.json")
    evidence = load_module_evidence(path / "module-evidence.json")
    if evidence["scope_ref"]["module_id"] != scope.module.module_id or evidence["scope_ref"]["snapshot_id"] != scope.snapshot_id:
        raise ValueError("ModuleEvidence does not match ModuleScope")
    docs: list[DocSource] = []
    for doc_id, filename, title in _DOCS:
        source = path / filename
        if not source.is_file():
            raise ValueError(f"completed module run is missing {filename}")
        docs.append(DocSource(doc_id, filename, title, _html_markdown(source.read_text("utf-8"))))
    return ModuleHtmlInputs(path, state, scope, evidence, docs)


def _labels(language: str) -> dict[str, str]:
    try:
        return _UI[language]
    except KeyError as exc:
        raise ValueError(f"unsupported report language: {language!r}") from exc


def _toc(toc: list[tuple[str, str]], language: str) -> str:
    # Reuse the established responsive drawer and its accessible behavior.
    return pages._toc_drawer(toc, language)


def _shell(inputs: ModuleHtmlInputs, active: str, title: str, subtitle: str, body: str,
           toc: list[tuple[str, str]] | None = None) -> str:
    labels = _labels(inputs.language)
    nav = []
    for page_id, filename, label in (
        ("index", "index.html", labels["overview"]),
        ("prd", "prd.html", labels["prd"]),
        ("health", "health.html", labels["health"]),
    ):
        active_class = " active" if page_id == active else ""
        nav.append(f'<a class="nav-link{active_class}" href="{attr(filename)}">{esc(label)}</a>')
    return (
        "<!doctype html>\n"
        f'<html lang="{attr(inputs.language)}" data-theme="light">\n<head>\n'
        '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)} · {esc(inputs.project_ref)}</title>\n"
        f'<link rel="stylesheet" href="{attr(assets.STYLESHEET)}">\n'
        "</head>\n<body>\n<div class=\"layout\">\n"
        '<nav class="nav" aria-label="module report navigation">'
        f'<h1>{esc(inputs.project_ref)}</h1><p class="project-id">{esc(labels["subtitle"])}</p>'
        f'<div class="nav-group"><h2>{esc(labels["documents"])}</h2>{"".join(nav)}</div></nav>\n'
        '<main class="main">\n<header class="page-head">'
        f'<h1>{esc(title)}</h1><p class="subtitle">{esc(subtitle)}</p></header>\n'
        f"{_toc(toc or [], inputs.language)}\n{body}\n</main>\n</div>\n"
        f'<script src="{attr(assets.MERMAID_SCRIPT)}"></script>\n'
        f'<script src="{attr(assets.APP_SCRIPT)}"></script>\n</body>\n</html>\n'
    )


def _unknowns(evidence: dict[str, Any]) -> list[str]:
    values = list(evidence.get("unknowns", []))
    values.extend(evidence.get("coverage", {}).get("limitations", []))
    return sorted(set(values))


def _leak_check(report_dir: Path, run_dir: Path) -> None:
    needles = {"/Users/", "/home/", str(run_dir.resolve())}
    home = os.environ.get("HOME")
    if home and home != "/":
        needles.add(home)
    for file in report_dir.rglob("*"):
        if file.suffix not in {".html", ".json"}:
            continue
        text = file.read_text("utf-8")
        for needle in needles:
            if needle and needle in text:
                raise RuntimeError(f"absolute-path leak in {file.name}: found {needle!r}")


def generate_module_html(inputs: ModuleHtmlInputs, report_dir: str | Path) -> ModuleHtmlResult:
    """Render lossless PRD and health views with no analysis-side effects."""
    destination = Path(report_dir)
    if destination.resolve() == inputs.run_dir.resolve():
        raise ValueError("report output directory must not be the module run directory")
    document_links = {doc.filename: f"{doc.doc_id}.html" for doc in inputs.docs}
    rendered = {doc.doc_id: render_document(doc.doc_id, doc.text, link_map=document_links)
                for doc in inputs.docs}
    entries = [DocEntry(doc.doc_id, doc.filename, f"{doc.doc_id}.html", rendered[doc.doc_id])
               for doc in inputs.docs]
    labels = _labels(inputs.language)

    overview_body = [
        '<section class="section" id="module-summary"><h2 class="doc-h">'
        f'{esc(labels["module"])}</h2><ul>'
        f'<li>{esc(inputs.scope.module.name)} (<code>{esc(inputs.scope.module.module_id)}</code>)</li>'
        f'<li>{esc(labels["source_mode"])}: {esc(inputs.scope.source_mode)}</li>'
        f'<li>{esc(labels["snapshot"])}: <code>{esc(inputs.scope.snapshot_id)}</code></li>'
        f'<li>{esc(labels["run"])}: <code>{esc(inputs.run_id)}</code></li></ul></section>',
        f'<section class="section" id="coverage"><h2 class="doc-h">{esc(labels["coverage"])}</h2>',
    ]
    unknowns = _unknowns(inputs.evidence)
    if unknowns:
        overview_body.append("<ul>" + "".join(f"<li>{esc(item)}</li>" for item in unknowns) + "</ul>")
    else:
        overview_body.append(f'<p class="muted">{esc(labels["unknown"])}</p>')
    overview_body.append("</section>")
    index = _shell(inputs, "index", labels["overview"], labels["subtitle"], "".join(overview_body),
                   [("module-summary", labels["module"]), ("coverage", labels["coverage"])])

    out_pages = {"index.html": index}
    for entry in entries:
        toc = [(section.anchor, section.text) for section in entry.rendered.sections if section.level <= 3]
        out_pages[entry.full_page] = _shell(
            inputs, entry.doc_id, entry.rendered.sections[0].text if entry.rendered.sections else entry.title,
            entry.filename, f'<article class="doc-body">{entry.rendered.html}</article>', toc)

    cmap = content_map.build_content_map(entries, {}, [])
    missing = content_map.verify_completeness(cmap)
    if missing:
        raise RuntimeError(f"content map incomplete (lossless parity violated): {missing}")
    diagrams = content_map.build_diagram_manifest(entries, [])

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    written_assets = assets.copy_assets(destination)
    for filename, html in sorted(out_pages.items()):
        (destination / filename).write_text(html, "utf-8")
    (destination / "content-map.json").write_text(
        json.dumps(cmap, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    (destination / "diagram-manifest.json").write_text(
        json.dumps(diagrams, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    _leak_check(destination, inputs.run_dir)
    return ModuleHtmlResult(destination, sorted(out_pages), written_assets,
                            [entry.filename for entry in entries],
                            sum(len(entry.rendered.sections) for entry in entries),
                            len(diagrams["diagrams"]))
