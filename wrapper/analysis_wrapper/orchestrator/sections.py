"""The report section catalog (57B-113 / 57B-117, M3).

Report generation stops being "one agent writes a whole document" and becomes
a DAG of per-section units. This module is the single declaration of what
those units ARE — for each section: which document it belongs to, its exact
heading, how it is produced (deterministically rendered vs authored by a
judgment task), what it depends on, which evidence it needs, and what its
completeness floor is.

Three production kinds, and the split is the point:

``render``
    Produced mechanically from validated artifacts. No model judgment is
    involved, so the section cannot drift, cannot miscount, and cannot go
    missing — §13's changeability cells, the protected findings blocks, the
    topology diagram, the disposition/coverage tables and the table of
    contents are all facts already decided elsewhere. See ``renders.py``.

``author``
    Genuine judgment: the causal diagnosis, the executive summary, the
    journeys. A ``section-generate`` task with its own bounded packet.

``author_with_reads``
    Judgment that first needs bounded source excerpts (verbatim UI labels for
    a journey, a call site for a ui->api edge). Runs the same plan-then-fetch
    pair the lens tasks use — a ``selection-fetch`` task, ``fetch-selections``,
    then the authoring task with the excerpts in its packet.

WAVES exist because §2 must be written last: it is the read-this-and-stop
summary and may state no conclusion the rest of the report does not support,
so it depends on every other section of its document. §11 likewise consumes
the per-module cells §13 renders. Everything else is independent and runs
concurrently — that concurrency is the whole point of the DAG.

Nothing here decides CONTENT. Section prose rules live in synthesis.md and
reach a task through its instructions; this module only says which units
exist, in what order they can run, and what each must contain to count as
complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #

OVERVIEW = "overview.md"
TECHNICAL = "technical-overview.md"
PROJECT_MAP = "project-map.md"
DOCUMENTS = (OVERVIEW, TECHNICAL, PROJECT_MAP)

PRODUCTION_KINDS = ("render", "author", "author_with_reads")


@dataclass(frozen=True)
class Section:
    """One unit of report production.

    ``section_id`` is stable and is what a task, a budget row, a floor check
    and an assembled document all key on. ``heading`` is the exact markdown
    heading line the assembled document must carry (the templates and
    synthesis.md fix these; an assembled document is checked against them).
    """

    section_id: str
    document: str
    heading: str
    kind: str
    wave: int
    # Section ids within the same document that must be produced first.
    depends_on: tuple[str, ...] = ()
    # Named run artifacts this section's packet/renderer needs.
    inputs: tuple[str, ...] = ()
    # Share of the document's prose budget (author sections only; the
    # renderers' output is not prose and is not budgeted).
    budget_share: float = 0.0
    # Floor: the section is incomplete unless its body contains at least this
    # many words OR an explicit honest inapplicability line. Renders have no
    # floor of their own -- their completeness is the renderer's contract.
    min_words: int = 0
    # Floor: substrings the assembled section MUST contain (machine markers,
    # required column headers). Checked verbatim.
    must_contain: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if self.document not in DOCUMENTS:
            raise ValueError(f"{self.section_id}: unknown document {self.document!r}")
        if self.kind not in PRODUCTION_KINDS:
            raise ValueError(f"{self.section_id}: unknown kind {self.kind!r}")
        if self.wave < 0:
            raise ValueError(f"{self.section_id}: wave must be >= 0")
        if self.kind == "render" and self.budget_share:
            raise ValueError(
                f"{self.section_id}: a rendered section carries no prose budget")


# --------------------------------------------------------------------------- #
# overview.md -- the PRIMARY human-facing document, sixteen fixed sections
#
# Budget shares sum to 1.0 across the AUTHOR sections only and encode
# synthesis.md's own emphasis: §2 is a self-sufficient ~2-3 minute read and
# gets the largest slice; §11 carries the causal story; the inventory-shaped
# sections are deliberately thin because their detail belongs in
# technical-overview.md.
# --------------------------------------------------------------------------- #

_OVERVIEW_SECTIONS = (
    Section("overview.s1", OVERVIEW, "## 1. Analysis basis", "render", wave=0,
            inputs=("run-provenance.json", "discovery-report.json", "signals/run-summary.json"),
            must_contain=("|",),
            note="Run date, per-repo one-liners and the standing scope disclaimer are "
                 "provenance facts -- rendering them removes a whole class of drift "
                 "(a mis-transcribed HEAD or a softened disclaimer)."),
    Section("overview.s2", OVERVIEW, "## 2. Executive diagnosis", "author", wave=3,
            depends_on=("overview.s3", "overview.s4", "overview.s5", "overview.s6",
                        "overview.s7", "overview.s8", "overview.s9", "overview.s10",
                        "overview.s11", "overview.s12", "overview.s13", "overview.s14",
                        "overview.s15", "overview.s16"),
            inputs=("sections-so-far",), budget_share=0.20, min_words=250,
            note="Written LAST although it appears second: it must state every systemic "
                 "cause and remaining gap the rest of the document evidences, and may "
                 "introduce no conclusion those sections do not support."),
    Section("overview.s3", OVERVIEW, "## 3. Product snapshot", "author", wave=1,
            inputs=("capabilities.json", "module-map.json", "findings.json"),
            budget_share=0.09, min_words=120),
    Section("overview.s4", OVERVIEW, "## 4. Users, roles & access model", "author", wave=1,
            inputs=("access/", "synthesis-input.json"), budget_share=0.07, min_words=110),
    Section("overview.s5", OVERVIEW, "## 5. Representative user journeys",
            "author_with_reads", wave=2,
            inputs=("route-inventory", "ui-route-linkage", "capabilities.json"),
            budget_share=0.10, min_words=140,
            note="The one section that REQUIRES bounded source reads: a UI entry point "
                 "must be quoted verbatim from the file that renders it, never inferred "
                 "from a route name."),
    Section("overview.s6", OVERVIEW, "## 6. Runtime & system topology", "render", wave=1,
            depends_on=("projectmap.relationships",),
            inputs=("project-map-relationships", "system-model.json"),
            must_contain=("```mermaid",),
            note="Rendered FROM the relationship rows, so synthesis.md's 'every mermaid "
                 "edge is backed by a table row' holds by construction and a malformed "
                 "diagram becomes impossible rather than audited-for."),
    Section("overview.s7", OVERVIEW, "## 7. Interface & consumer boundaries", "author", wave=1,
            inputs=("route-inventory", "ui-route-linkage"), budget_share=0.07, min_words=90),
    Section("overview.s8", OVERVIEW, "## 8. Data ownership & lifecycle", "author", wave=1,
            inputs=("datastore/", "system-model.json"), budget_share=0.08, min_words=110),
    Section("overview.s9", OVERVIEW, "## 9. Background execution", "author", wave=1,
            inputs=("capabilities.json", "synthesis-input.json"),
            budget_share=0.04, min_words=60),
    Section("overview.s10", OVERVIEW, "## 10. External systems", "author", wave=1,
            inputs=("integrations/", "discovery-report.json"),
            budget_share=0.06, min_words=70),
    Section("overview.s11", OVERVIEW, "## 11. Overall changeability diagnosis", "author", wave=2,
            depends_on=("overview.s13",),
            inputs=("findings.json", "changeability-cells"), budget_share=0.12, min_words=180,
            note="The six changeability questions as ONE causal story; consumes the same "
                 "cells §13 renders so the narrative and the table cannot disagree."),
    Section("overview.s12", OVERVIEW, "## 12. Representative change-impact paths",
            "author", wave=2, depends_on=("overview.s13",),
            inputs=("findings.json", "graph", "changeability-cells"),
            budget_share=0.08, min_words=110),
    Section("overview.s13", OVERVIEW, "## 13. Module changeability table", "render", wave=1,
            inputs=("findings.json", "module-map.json", "signals/run-summary.json"),
            must_contain=("| module", "confirmed concern"),
            note="Every cell is derivable: a cited finding tagged with that changeability "
                 "question makes it a confirmed concern, a ran-but-silent signal makes it "
                 "'no concern observed', a signal that did not run makes it unknown. "
                 "Rendering it also enforces 'never healthy' and the per-gap unknown "
                 "mapping without relying on a writer to remember them."),
    Section("overview.s14", OVERVIEW, "## 14. Findings by observed impact", "render", wave=1,
            inputs=("findings-pm-summary.md",),
            must_contain=("<!-- BEGIN MACHINE PM FINDINGS -->",),
            note="A verbatim embed of the protected block finalize-findings produced."),
    Section("overview.s15", OVERVIEW, "## 15. Operational state", "author", wave=1,
            inputs=("deploy/", "capabilities.json", "test-ci-evidence"),
            budget_share=0.06, min_words=80),
    Section("overview.s16", OVERVIEW, "## 16. Coverage & unknowns", "author", wave=1,
            inputs=("signals/run-summary.json", "capabilities.json", "coverage-summary.md"),
            budget_share=0.03, min_words=70,
            note="Three honest categories (bounded here / producer missing / code cannot "
                 "answer) -- never converted into a recommendation."),
)

# --------------------------------------------------------------------------- #
# technical-overview.md -- the full-detail companion. Mostly rendered: this is
# where the machine-verified blocks, the complete accounting tables and the
# verbatim per-signal detail live, and every one of them is a projection of an
# artifact rather than a judgment.
# --------------------------------------------------------------------------- #

_TECHNICAL_SECTIONS = (
    Section("technical.contents", TECHNICAL, "## Contents", "render", wave=3,
            depends_on=("technical.provenance", "technical.summary", "technical.scope",
                        "technical.interfaces", "technical.access", "technical.health",
                        "technical.external", "technical.coverage", "technical.assumptions"),
            inputs=("sections-so-far",),
            note="Generated LAST from the assembled headings, per synthesis.md."),
    Section("technical.provenance", TECHNICAL, "## Run provenance", "render", wave=0,
            inputs=("run-provenance.json", "discovery-report.json"), must_contain=("|",)),
    Section("technical.summary", TECHNICAL, "## Executive summary", "author", wave=2,
            depends_on=("technical.health",), inputs=("findings.json", "findings-summary.md"),
            budget_share=0.30, min_words=200),
    Section("technical.scope", TECHNICAL, "## Analysis scope", "render", wave=0,
            inputs=("targets.json", "discovery-report.json", "signals/run-summary.json"),
            must_contain=("|",)),
    Section("technical.interfaces", TECHNICAL, "## Interfaces & consumers", "render", wave=1,
            inputs=("route-inventory", "ui-route-linkage"), must_contain=("|",),
            note="The endpoint-level inventory overview.md deliberately sheds."),
    Section("technical.access", TECHNICAL, "## Access model (backing)", "render", wave=1,
            inputs=("access/", "synthesis-input.json"), must_contain=("|",)),
    Section("technical.health", TECHNICAL, "## Module health table", "render", wave=1,
            inputs=("findings.json", "module-map.json", "workspace-metrics.json"),
            must_contain=("|",),
            note="Absence renders as 'no concern observed' scoped to the signals that "
                 "ran -- never as a wellness label."),
    Section("technical.external", TECHNICAL, "## External systems (candidate disposition)",
            "render", wave=1, inputs=("integrations/", "discovery-report.json"),
            must_contain=("|",),
            note="Counts must sum to the candidate total -- rendered, so they do."),
    Section("technical.coverage", TECHNICAL, "## Lens coverage", "render", wave=1,
            inputs=("signals/run-summary.json", "capabilities.json"), must_contain=("|",),
            note="Status computed over REQUIRED signals, plus the verbatim per-signal "
                 "detail rows from run-summary.json."),
    Section("technical.findings", TECHNICAL, "## Findings (machine verified)", "render", wave=1,
            inputs=("findings-summary.md",),
            must_contain=("<!-- BEGIN MACHINE VERIFIED FINDINGS -->",)),
    Section("technical.assumptions", TECHNICAL, "## Assumptions & open questions",
            "author", wave=2, inputs=("findings.json", "signals/run-summary.json"),
            budget_share=0.70, min_words=90),
)

# --------------------------------------------------------------------------- #
# project-map.md -- the reusable topology. Relationship rows are rendered
# first because overview.md §6's diagram is generated from them.
# --------------------------------------------------------------------------- #

_PROJECT_MAP_SECTIONS = (
    Section("projectmap.relationships", PROJECT_MAP, "## Relationships", "render", wave=0,
            inputs=("system-model.json", "module-map.json"), must_contain=("|",),
            note="The single source of truth for every edge any diagram may draw."),
    Section("projectmap.topology", PROJECT_MAP, "## Topology", "render", wave=1,
            depends_on=("projectmap.relationships",), inputs=("project-map-relationships",),
            must_contain=("```mermaid",)),
    Section("projectmap.persistence", PROJECT_MAP, "## Shared persistence", "render", wave=1,
            inputs=("datastore/", "system-model.json"), must_contain=("|",)),
    Section("projectmap.routes", PROJECT_MAP,
            "## Static frontend-to-backend route references", "render", wave=1,
            inputs=("ui-route-linkage", "route-inventory"), must_contain=("|",)),
    Section("projectmap.cochange", PROJECT_MAP, "## Co-change coupling (history signal)",
            "render", wave=1, inputs=("cohesion-bundle.json", "signals/run-summary.json")),
    Section("projectmap.external", PROJECT_MAP, "## External systems", "render", wave=1,
            inputs=("integrations/", "discovery-report.json")),
    Section("projectmap.unanalyzed", PROJECT_MAP, "## Referenced but NOT analyzed",
            "render", wave=1, inputs=("integrations/", "discovery-report.json", "targets.json"),
            note="Any configured endpoint whose serving source is not among the analyzed "
                 "repos -- derived from evidence, not only from operator exclusions."),
)

CATALOG: tuple[Section, ...] = (
    _OVERVIEW_SECTIONS + _TECHNICAL_SECTIONS + _PROJECT_MAP_SECTIONS)

BY_ID: dict[str, Section] = {section.section_id: section for section in CATALOG}


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #

def for_document(document: str) -> tuple[Section, ...]:
    """Catalog order for one document — the order its sections are assembled
    in, which is the order the template fixes (NOT wave order: §2 is written
    in the last wave but assembled second)."""
    if document not in DOCUMENTS:
        raise ValueError(f"unknown document: {document!r}")
    return tuple(section for section in CATALOG if section.document == document)


def authored(sections: tuple[Section, ...] | None = None) -> tuple[Section, ...]:
    return tuple(s for s in (sections or CATALOG) if s.kind != "render")


def rendered(sections: tuple[Section, ...] | None = None) -> tuple[Section, ...]:
    return tuple(s for s in (sections or CATALOG) if s.kind == "render")


def waves() -> dict[int, tuple[Section, ...]]:
    """Sections grouped by wave. Everything in one wave is independent and may
    run concurrently; a wave starts once the previous one has produced the
    sections it is declared to depend on."""
    grouped: dict[int, list[Section]] = {}
    for section in CATALOG:
        grouped.setdefault(section.wave, []).append(section)
    return {wave: tuple(rows) for wave, rows in sorted(grouped.items())}


def prose_budget(document: str, total_words: int) -> dict[str, int]:
    """Split a document's prose ceiling across its AUTHOR sections by share.

    Per-section budgets are what make synthesis.md's overflow rule
    enforceable at the place it is violated: a section that runs long is
    retried alone with relocation instructions, instead of the whole document
    being rewritten (and quietly condensed) at the end.
    """
    rows = [s for s in for_document(document) if s.budget_share > 0]
    if not rows:
        return {}
    scale = sum(s.budget_share for s in rows)
    return {s.section_id: max(1, round(total_words * s.budget_share / scale)) for s in rows}


def validate_catalog() -> list[str]:
    """Structural problems in the catalog itself (empty = sound). Called by
    the planner before it composes anything, so a bad edit here fails at plan
    time rather than halfway through a run."""
    problems: list[str] = []
    seen: set[str] = set()
    for section in CATALOG:
        if section.section_id in seen:
            problems.append(f"duplicate section_id: {section.section_id}")
        seen.add(section.section_id)
    for section in CATALOG:
        for dependency in section.depends_on:
            target = BY_ID.get(dependency)
            if target is None:
                problems.append(
                    f"{section.section_id} depends on unknown section {dependency!r}")
            elif target.wave >= section.wave:
                problems.append(
                    f"{section.section_id} (wave {section.wave}) depends on "
                    f"{dependency} (wave {target.wave}) -- a dependency must be "
                    "produced in an EARLIER wave")
    for document in DOCUMENTS:
        rows = [s for s in for_document(document) if s.budget_share > 0]
        total = sum(s.budget_share for s in rows)
        if rows and abs(total - 1.0) > 0.01:
            problems.append(
                f"{document}: author budget shares sum to {total:.2f}, expected 1.00")
    return problems
