"""Deterministic orchestrator: run directory -> self-contained report folder.

Reads a completed run, renders the canonical documents, builds the structured
and section-aware views, composes the multi-page report, and writes it with the
content map + diagram manifest. No timestamps or randomness enter the output
(everything time-like is sourced from the run's own artifacts), so identical
input artifacts yield byte-identical output.

A hard leak invariant runs before returning: if any absolute machine path
reaches the generated HTML/JSON, generation fails loudly rather than shipping it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from . import assets, components, content_map, narrative, pages, run_inputs
from .content_map import Destination, DocEntry
from .htmlutil import attr, esc
from .markdown_render import mermaid_figure, render_document


@dataclass
class GenerateResult:
    report_dir: Path
    pages: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    section_count: int = 0
    diagram_count: int = 0
    missing_artifacts: list[str] = field(default_factory=list)
    drilldown_available: bool = False


def _write_json(path: Path, obj: dict) -> None:
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _coverage_status_summary(inputs: run_inputs.RunInputs) -> str:
    sm = inputs.system_model or {}
    coverage = sm.get("coverage", {})
    if not coverage:
        return (
            '<div class="unavailable"><p>system-model coverage is unavailable; '
            "see the run's coverage artifacts.</p></div>"
        )
    counts = Counter(rec.get("status", "unknown") for rec in coverage.values())
    tiles = "".join(
        f'<div class="tile"><span class="tile-value">{esc(counts.get(s, 0))}</span>'
        f'<span class="tile-label">{esc(s)} lenses</span></div>'
        for s in ("complete", "partial", "unavailable")
    )
    link = (
        f'<p><a href="{attr(pages.PAGE_FILES["coverage"])}">'
        "Full evidence &amp; coverage →</a></p>"
    )
    return f'<div class="tiles">{tiles}</div>{link}'


_NUMBERED_HEADING = re.compile(r"^\s*(\d+)\.")

# The overview contract fixes these section numbers across report languages. The
# exporter uses only that structural contract: it never searches prose for
# business meaning and never synthesizes a second summary.
_LANDING_SECTION_ORDER = (2, 14, 11, 12, 3, 16)
_DIAGNOSIS_SECTION_ORDER = (11, 12, 13, 14)


def _authored_section_items(
    rendered,
    *,
    section_numbers: tuple[int, ...],
    page_file: str,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, Destination]]]:
    """Render selected overview sections verbatim in a presentation page.

    Selection is by the overview template's stable numeric structure, so the
    same mapping works for English and Chinese headings. Missing sections are
    simply absent; the lossless full-document page remains the canonical view.
    """
    by_number = {}
    for section in rendered.sections:
        if section.level != 2:
            continue
        match = _NUMBERED_HEADING.match(section.text)
        if match:
            by_number[int(match.group(1))] = section

    items = []
    destinations = []
    for number in section_numbers:
        section = by_number.get(number)
        if section is None:
            continue
        body = rendered.section_body_html(section.anchor) or ""
        # Curated HTML views reorder canonical Markdown sections, so carrying
        # source numbers into the new order would produce sequences such as
        # 2 -> 14 -> 11. Preserve the verbatim heading in the full document and
        # content map, but show the semantic title here without its source index.
        display_title = _NUMBERED_HEADING.sub("", section.text, count=1).strip()
        items.append((
            section.anchor,
            display_title,
            pages.section(section.anchor, display_title, body),
        ))
        destinations.append((
            rendered.doc_id,
            section.anchor,
            Destination(page_file, section.anchor, "markdown-section"),
        ))
    return items, destinations


def _run_status_summary(inputs: run_inputs.RunInputs) -> str:
    if not inputs.inspection_only:
        return ""
    if inputs.language == "zh-CN":
        message = (
            "<strong>仅供检查：</strong>分析时至少一个仓库不是干净工作树；"
            "此运行不能被接受为 current。"
        )
    else:
        message = (
            "<strong>Inspection-only:</strong> at least one repository was not a "
            "clean worktree during analysis; this run cannot be accepted as current."
        )
    return f'<div class="note"><p>{message}</p></div>'


def _landing_labels(language: str) -> dict[str, str]:
    labels = {
        "en": {
            "run_status": "Run status",
            "diagnosis": "Diagnosis",
            "diagnosis_unavailable": "No narrative document present.",
        },
        "zh-CN": {
            "run_status": "运行状态",
            "diagnosis": "诊断",
            "diagnosis_unavailable": "没有可用的叙述性文档。",
        },
    }
    return labels.get(language, labels["en"])


def _authored_diagrams_section(doc_entries: list[DocEntry]) -> str | None:
    blocks = []
    for entry in doc_entries:
        for block in entry.rendered.mermaid_blocks:
            anchor = block.nearest_anchor or ""
            link = (
                f'<a href="{attr(pages.doc_page(entry.doc_id))}#{attr(anchor)}">'
                f"{esc(entry.filename)}</a>"
            )
            caption = (
                f'<figcaption class="muted">Authored ({esc(block.diagram_type)}) — '
                f"{link}</figcaption>"
            )
            blocks.append(
                mermaid_figure(block.source, dom_id=f"{entry.doc_id}-{block.index}")
                + caption
            )
    if not blocks:
        return None
    note = (
        '<p class="muted">Synthesis-authored diagrams, rendered verbatim as '
        "presentation evidence (not machine-verifiable workflow models). The "
        "structured topology above is built from typed edges.</p>"
    )
    return note + "".join(blocks)


def _leak_check(report_dir: Path, run_dir: Path) -> None:
    forbidden = ["/Users/", "/home/"]
    home = os.environ.get("HOME", "")
    if home and home not in ("/", ""):
        forbidden.append(home)
    forbidden.append(str(run_dir.resolve()))
    forbidden = sorted({f for f in forbidden if f})
    for path in sorted(report_dir.rglob("*")):
        if path.suffix not in (".html", ".json"):
            continue
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                raise RuntimeError(
                    f"absolute-path leak in {path.name}: found {needle!r}"
                )


def generate(run_dir: str | Path, out_dir: str | Path | None = None) -> GenerateResult:
    """Load a completed run directory and render the report into ``out_dir``.

    Thin wrapper over :func:`generate_from_inputs` so the exporter framework can
    share the same source layer (:class:`RunInputs`).
    """
    inputs = run_inputs.load(run_dir)
    report_dir = Path(out_dir) if out_dir else inputs.run_dir / "report"
    return generate_from_inputs(inputs, report_dir)


def generate_from_inputs(
    inputs: run_inputs.RunInputs, report_dir: str | Path
) -> GenerateResult:
    report_dir = Path(report_dir)
    if report_dir.resolve() == inputs.run_dir.resolve():
        raise ValueError("report output directory must not be the run directory")

    document_link_map = {
        d.filename: pages.doc_page(d.doc_id)
        for d in inputs.docs
    }
    rendered = {
        d.doc_id: render_document(d.doc_id, d.text, link_map=document_link_map)
        for d in inputs.docs
    }
    doc_entries = [
        DocEntry(d.doc_id, d.filename, pages.doc_page(d.doc_id), rendered[d.doc_id])
        for d in inputs.docs
    ]
    primary_doc = inputs.doc("overview") or (inputs.docs[0] if inputs.docs else None)

    # Drill-down module documents (present only once Phase 2 emits them). Each is
    # rendered as a lossless full-document page and folded into the content map.
    module_docs: list[tuple] = []  # (module, kind, DocSource, MarkdownDoc)
    for m in inputs.drilldown_modules:
        for kind, rel in (("prd", m.prd_relpath), ("health", m.health_relpath)):
            if not rel:
                continue
            text = (inputs.run_dir / rel).read_text(encoding="utf-8")
            did = narrative.drilldown_page_id(m, kind)
            ds = run_inputs.DocSource(did, rel, f"{m.module_id} — {kind}", text)
            md = render_document(did, text, link_map=document_link_map)
            module_docs.append((m, kind, ds, md))
            doc_entries.append(DocEntry(did, rel, pages.doc_page(did), md))

    def module_page_for(module, kind: str) -> str:
        return pages.doc_page(narrative.drilldown_page_id(module, kind))

    structured_registry: list[dict] = []
    extra_dests: dict[tuple[str, str], list] = {}

    # Each page is a list of (anchor, title, section_html); _compose derives the
    # body and the TOC-drawer entries from it so the two always agree.
    def sec(anchor: str, title: str, html: str) -> tuple[str, str, str]:
        return (anchor, title, pages.section(anchor, title, html))

    def place(comp, page_file: str) -> tuple[str, str, str]:
        structured_registry.append(
            {
                "component": comp.key,
                "page": page_file,
                "anchor": comp.anchor,
                "mode": "structured",
                "sources": comp.sources,
            }
        )
        return sec(comp.anchor, comp.title, comp.html)

    def compose(items: list[tuple[str, str, str]]) -> tuple[str, list[tuple[str, str]]]:
        body = "".join(h for _, _, h in items)
        toc = [(a, t) for a, t, _ in items]
        return body, toc

    # ---- structured components (built once) ----
    snapshot = components.system_snapshot(inputs)
    provenance = components.provenance_table(inputs)
    coverage = components.coverage_matrix(inputs)
    legend = components.relationship_legend(inputs)
    cg_cov = components.callgraph_coverage_table(inputs)
    dm_cov = components.depmap_coverage_table(inputs)
    topo = components.topology_structured(inputs)
    externals = components.external_boundaries_table(inputs)
    datastores = components.data_stores_table(inputs)

    out_pages: list[pages.Page] = []
    P = pages.PAGE_FILES

    ch = pages.chrome(inputs.language)
    landing_labels = _landing_labels(inputs.language)

    # ---- main page ----
    # Lead with the report's authored diagnosis. The former index led with run
    # mechanics and reduced the diagnosis to an outline, which made the landing
    # page an audit dashboard rather than a project overview.
    main_items = []
    if primary_doc:
        primary_rendered = rendered[primary_doc.doc_id]
        authored_items, authored_destinations = _authored_section_items(
            primary_rendered,
            section_numbers=_LANDING_SECTION_ORDER,
            page_file=P["index"],
        )
        main_items.extend(authored_items)
        for doc_id, anchor, dest in authored_destinations:
            extra_dests.setdefault((doc_id, anchor), []).append(dest)
    else:
        main_items.append(sec(
            "diagnosis-unavailable", landing_labels["diagnosis"],
            f'<p class="muted">{landing_labels["diagnosis_unavailable"]}</p>',
        ))
    status_summary = _run_status_summary(inputs)
    if status_summary:
        main_items.insert(0, sec(
            "run-status", landing_labels["run_status"], status_summary
        ))
    main_items.extend([
        place(snapshot, P["index"]),
        sec("coverage-status", "Coverage status", _coverage_status_summary(inputs)),
    ])
    main_body, main_toc = compose(main_items)
    main_title = (
        rendered[primary_doc.doc_id].sections[0].text
        if primary_doc and rendered[primary_doc.doc_id].sections
        else inputs.project_ref
    )
    out_pages.append(pages.Page(
        "index", P["index"],
        pages.shell(inputs, "index", main_title, ch["subtitle"],
                    main_body, main_toc),
    ))

    # ---- findings & diagnosis (diagnostic sections only) ----
    if primary_doc:
        diagnosis_items, diagnosis_destinations = _authored_section_items(
            rendered[primary_doc.doc_id],
            section_numbers=_DIAGNOSIS_SECTION_ORDER,
            page_file=P["findings"],
        )
        for doc_id, anchor, dest in diagnosis_destinations:
            extra_dests.setdefault((doc_id, anchor), []).append(dest)
        findings_body, findings_toc = compose(diagnosis_items)
        if not diagnosis_items:
            findings_body = '<p class="muted">No diagnostic sections present.</p>'
    else:
        findings_body, findings_toc = '<p class="muted">No narrative document present.</p>', []
    out_pages.append(pages.Page(
        "findings", P["findings"],
        pages.shell(inputs, "findings", ch["findings"],
                    primary_doc.filename if primary_doc else "",
                    findings_body, findings_toc),
    ))

    # ---- evidence & coverage ----
    coverage_body, coverage_toc = compose([
        place(coverage, P["coverage"]),
        place(legend, P["coverage"]),
        place(cg_cov, P["coverage"]),
        place(dm_cov, P["coverage"]),
        place(provenance, P["coverage"]),
    ])
    out_pages.append(pages.Page(
        "coverage", P["coverage"],
        pages.shell(inputs, "coverage", ch["coverage"],
                    "observed · inferred · unresolved · unavailable",
                    coverage_body, coverage_toc),
    ))

    # ---- system topology ----
    authored = _authored_diagrams_section(doc_entries)
    topo_items = [place(topo, P["topology"])]
    if authored:
        topo_items.append(sec("authored-topology",
                              "Authored topology (synthesis narrative)", authored))
    topo_items.append(place(externals, P["topology"]))
    topo_items.append(place(datastores, P["topology"]))
    topo_body, topo_toc = compose(topo_items)
    out_pages.append(pages.Page(
        "topology", P["topology"],
        pages.shell(inputs, "topology", ch["topology"],
                    "structured edges · authored diagrams · boundaries",
                    topo_body, topo_toc),
    ))

    # ---- modules entrance ----
    module_block = narrative.module_entrance(
        inputs,
        rendered.get("project-map"),
        pages.doc_page("project-map") if inputs.doc("project-map") else None,
        module_page_for=module_page_for if module_docs else None,
    )
    out_pages.append(pages.Page(
        "modules", P["modules"],
        pages.shell(inputs, "modules", ch["modules"],
                    "PM PRD + developer health per module",
                    module_block.html, module_block.toc),
    ))

    # ---- documents hub ----
    doc_items = [
        sec(
            f"doc-{entry.doc_id}-outline",
            entry.rendered.sections[0].text if entry.rendered.sections else entry.filename,
            f'<p class="muted">{esc(entry.filename)} · '
            f'<a href="{attr(pages.doc_page(entry.doc_id))}">open full document →</a></p>'
            + narrative.document_outline(entry.rendered, pages.doc_page(entry.doc_id)),
        )
        for entry in doc_entries
    ]
    docs_body, docs_toc = compose(doc_items)
    if not doc_items:
        docs_body = '<p class="muted">No canonical documents present.</p>'
    out_pages.append(pages.Page(
        "documents", P["documents"],
        pages.shell(inputs, "documents", ch["documents"],
                    "lossless full-document views", docs_body, docs_toc),
    ))

    # ---- full-document lossless views ----
    for d in inputs.docs:
        out_pages.append(pages.full_document_page(inputs, d, rendered[d.doc_id]))
    for _m, _kind, ds, md in module_docs:
        out_pages.append(pages.full_document_page(inputs, ds, md))

    # ---- content map + diagram manifest ----
    cmap = content_map.build_content_map(doc_entries, extra_dests, structured_registry)
    missing = content_map.verify_completeness(cmap)
    if missing:
        raise RuntimeError(f"content map incomplete (lossless parity violated): {missing}")

    structured_diagrams = []
    if topo.mermaid_source is not None:
        sm = inputs.system_model or {}
        cov = sm.get("coverage", {})
        route_status = cov.get("routes", {}).get("status", "unknown")
        structured_diagrams.append({
            "id": "system-model:topology",
            "type": "graph",
            "source_kind": "system-model",
            "model_refs": [
                "system-model.json#edges:route-linkage",
                "system-model.json#edges:data",
            ],
            "source_sha256": sha256(topo.mermaid_source.encode("utf-8")).hexdigest(),
            "status": route_status,
        })
    dmanifest = content_map.build_diagram_manifest(doc_entries, structured_diagrams)

    # ---- write (clean, deterministic) ----
    if report_dir.exists():
        shutil.rmtree(report_dir)
    report_dir.mkdir(parents=True)
    written_assets = assets.copy_assets(report_dir)
    for page in out_pages:
        (report_dir / page.filename).write_text(page.html, encoding="utf-8")
    _write_json(report_dir / "content-map.json", cmap)
    _write_json(report_dir / "diagram-manifest.json", dmanifest)

    _leak_check(report_dir, inputs.run_dir)

    section_count = sum(len(e.rendered.sections) for e in doc_entries)
    return GenerateResult(
        report_dir=report_dir,
        pages=sorted(p.filename for p in out_pages),
        assets=written_assets,
        documents=[e.filename for e in doc_entries],
        section_count=section_count,
        diagram_count=len(dmanifest["diagrams"]),
        missing_artifacts=inputs.missing_artifacts(),
        drilldown_available=inputs.drilldown_available,
    )
