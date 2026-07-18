"""Deterministic content map + diagram manifest.

The content map records, for every source document section, where it is rendered
in the report — always at least its lossless ``full-document`` destination, plus
any designed section-aware placements. It exists so lossless parity can be
checked mechanically: a source heading missing a ``full-document`` destination is
a content-loss bug, no visual inspection required.

The diagram manifest records every rendered diagram with its source provenance
(``system-model`` structured vs ``markdown-mermaid`` authored) and a source hash.
"""

from __future__ import annotations

from dataclasses import dataclass

from .markdown_render import MarkdownDoc

GENERATOR = "analysis-report-html/1.0.0"


@dataclass(frozen=True)
class Destination:
    page: str
    anchor: str
    mode: str  # "structured" | "markdown-section" | "full-document"


@dataclass(frozen=True)
class DocEntry:
    doc_id: str
    filename: str
    full_page: str
    rendered: MarkdownDoc


def build_content_map(
    docs: list[DocEntry],
    extra_destinations: dict[tuple[str, str], list[Destination]],
    structured_components: list[dict],
) -> dict:
    """Assemble the content map.

    ``extra_destinations`` maps ``(doc_id, section_anchor)`` to the designed
    (non-full-document) places that section is *also* surfaced. Every section is
    unconditionally given its ``full-document`` destination first, guaranteeing
    completeness independent of the designed views.
    """
    documents = []
    for entry in sorted(docs, key=lambda d: d.doc_id):
        sections = []
        for sec in entry.rendered.sections:
            dests = [
                Destination(entry.full_page, sec.anchor, "full-document"),
                *extra_destinations.get((entry.doc_id, sec.anchor), []),
            ]
            sections.append(
                {
                    "anchor": sec.anchor,
                    "heading": sec.text,
                    "level": sec.level,
                    "destinations": [
                        {"page": d.page, "anchor": d.anchor, "mode": d.mode}
                        for d in dests
                    ],
                }
            )
        documents.append(
            {
                "doc_id": entry.doc_id,
                "filename": entry.filename,
                "full_document_page": entry.full_page,
                "section_count": len(sections),
                "sections": sections,
            }
        )

    return {
        "generator": GENERATOR,
        "documents": documents,
        "structured_components": sorted(
            structured_components, key=lambda c: (c.get("page", ""), c.get("anchor", ""))
        ),
    }


def build_diagram_manifest(
    docs: list[DocEntry],
    structured_diagrams: list[dict],
) -> dict:
    """Assemble the diagram manifest (structured + authored-mermaid diagrams)."""
    diagrams: list[dict] = list(structured_diagrams)
    for entry in docs:
        for block in entry.rendered.mermaid_blocks:
            diagrams.append(
                {
                    "id": f"{entry.doc_id}:mermaid:{block.index}",
                    "type": block.diagram_type,
                    "source_kind": "markdown-mermaid",
                    "source_document": entry.filename,
                    "source_anchor": block.nearest_anchor,
                    "source_sha256": block.source_sha256,
                    "status": "presentation-evidence",
                    "note": (
                        "authored mermaid rendered verbatim; presentation "
                        "evidence, not a machine-verifiable workflow model"
                    ),
                }
            )
    return {
        "generator": GENERATOR,
        "diagrams": sorted(diagrams, key=lambda d: d["id"]),
    }


def verify_completeness(content_map: dict) -> list[str]:
    """Return a list of sections lacking a ``full-document`` destination.

    Empty list == lossless parity holds for every recorded section. Used by the
    generator's self-check and by tests.
    """
    missing: list[str] = []
    for doc in content_map.get("documents", []):
        for sec in doc.get("sections", []):
            modes = {d["mode"] for d in sec.get("destinations", [])}
            if "full-document" not in modes:
                missing.append(f"{doc['doc_id']}#{sec['anchor']}")
    return missing
