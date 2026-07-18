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
import shutil
from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from . import assets, components, content_map, narrative, pages, run_inputs
from .content_map import DocEntry
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


def _diagnosis_outline(inputs: run_inputs.RunInputs, primary, primary_doc_id: str) -> str:
    note = (
        '<div class="note"><p>Findings, changeability and journeys are authored '
        "narrative. They are presented below by the analysis document's own "
        "section structure (verbatim headings) — impact/lens grouping needs a "
        "structured findings artifact this run does not provide.</p></div>"
    )
    outline = narrative.document_outline(
        primary, pages.PAGE_FILES["findings"], max_level=2
    )
    return note + outline


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
    inputs = run_inputs.load(run_dir)
    report_dir = Path(out_dir) if out_dir else inputs.run_dir / "report"
    if report_dir.resolve() == inputs.run_dir.resolve():
        raise ValueError("report output directory must not be the run directory")

    rendered = {d.doc_id: render_document(d.doc_id, d.text) for d in inputs.docs}
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
            md = render_document(did, text)
            module_docs.append((m, kind, ds, md))
            doc_entries.append(DocEntry(did, rel, pages.doc_page(did), md))

    def module_page_for(module, kind: str) -> str:
        return pages.doc_page(narrative.drilldown_page_id(module, kind))

    structured_registry: list[dict] = []
    extra_dests: dict[tuple[str, str], list] = {}

    def place(comp, page_file: str) -> str:
        structured_registry.append(
            {
                "component": comp.key,
                "page": page_file,
                "anchor": comp.anchor,
                "mode": "structured",
                "sources": comp.sources,
            }
        )
        return pages.section(comp.anchor, comp.title, comp.html)

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

    # ---- main page ----
    main_body = "".join([
        place(provenance, P["index"]),
        place(snapshot, P["index"]),
        pages.section("coverage-status", "Coverage status",
                      _coverage_status_summary(inputs)),
        pages.section(
            "diagnosis",
            "Findings & diagnosis",
            _diagnosis_outline(inputs, rendered.get(primary_doc.doc_id), primary_doc.doc_id)
            if primary_doc else '<p class="muted">No narrative document present.</p>',
        ),
    ])
    out_pages.append(pages.Page(
        "index", P["index"],
        pages.shell(inputs, "index", inputs.project_id,
                    pages.chrome(inputs.language)["subtitle"], main_body),
    ))

    # ---- findings & diagnosis (section-aware narrative) ----
    if primary_doc:
        block = narrative.narrative_cards(
            rendered[primary_doc.doc_id], pages.doc_page(primary_doc.doc_id), P["findings"]
        )
        for doc_id, anchor, dest in block.destinations:
            extra_dests.setdefault((doc_id, anchor), []).append(dest)
        findings_body = block.html
    else:
        findings_body = '<p class="muted">No narrative document present.</p>'
    out_pages.append(pages.Page(
        "findings", P["findings"],
        pages.shell(inputs, "findings",
                    pages.chrome(inputs.language)["findings"],
                    primary_doc.filename if primary_doc else "",
                    findings_body),
    ))

    # ---- evidence & coverage ----
    coverage_body = "".join([
        place(coverage, P["coverage"]),
        place(legend, P["coverage"]),
        place(cg_cov, P["coverage"]),
        place(dm_cov, P["coverage"]),
        place(provenance, P["coverage"]),
    ])
    out_pages.append(pages.Page(
        "coverage", P["coverage"],
        pages.shell(inputs, "coverage", pages.chrome(inputs.language)["coverage"],
                    "observed · inferred · unresolved · unavailable", coverage_body),
    ))

    # ---- system topology ----
    authored = _authored_diagrams_section(doc_entries)
    topo_body = place(topo, P["topology"])
    if authored:
        topo_body += pages.section("authored-topology",
                                   "Authored topology (synthesis narrative)", authored)
    topo_body += place(externals, P["topology"])
    topo_body += place(datastores, P["topology"])
    out_pages.append(pages.Page(
        "topology", P["topology"],
        pages.shell(inputs, "topology", pages.chrome(inputs.language)["topology"],
                    "structured edges · authored diagrams · boundaries", topo_body),
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
        pages.shell(inputs, "modules", pages.chrome(inputs.language)["modules"],
                    "PM PRD + developer health per module", module_block.html),
    ))

    # ---- documents hub ----
    doc_hub = []
    for entry in doc_entries:
        doc_hub.append(pages.section(
            f"doc-{entry.doc_id}-outline",
            entry.rendered.sections[0].text if entry.rendered.sections else entry.filename,
            f'<p class="muted">{esc(entry.filename)} · '
            f'<a href="{attr(pages.doc_page(entry.doc_id))}">open full document →</a></p>'
            + narrative.document_outline(entry.rendered, pages.doc_page(entry.doc_id)),
        ))
    docs_body = "".join(doc_hub) or '<p class="muted">No canonical documents present.</p>'
    out_pages.append(pages.Page(
        "documents", P["documents"],
        pages.shell(inputs, "documents", pages.chrome(inputs.language)["documents"],
                    "lossless full-document views", docs_body),
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
