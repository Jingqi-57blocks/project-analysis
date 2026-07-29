"""Report planning, assembly and floors (57B-113 / 57B-117, M3).

Three jobs, all keyed on the section catalog:

``plan_reports``
    Registers the authored sections of a document as ``section-generate``
    tasks in wave order — each with only the evidence its own section needs
    and its OWN slice of the prose budget. Rendered sections are not tasks;
    they are produced at assembly time.

``assemble_document``
    Walks the catalog in template order, taking each section's body from its
    renderer or its validated task, and writes the document. Assembly is the
    only writer: a section body never reaches a document except through here,
    so heading text, ordering and the protected blocks cannot drift.

``document_floors``
    The never-simplify guard, enforced where it is violated. Overflow is a
    per-SECTION event with a relocation remedy, and every floor is reported
    alongside the ceiling — a caller can never see "too long" without also
    seeing what must not be dropped to fix it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import coverage_render, module_render
from ..sanitize import sanitize_text
from . import renders, schemas, sections as catalog
from .composer import compose
from .engine import Engine
from .results import validated_outputs
from .templates import content_digest

# synthesis.md's universal PM prose ceiling (tables and mermaid excluded).
OVERVIEW_PROSE_CEILING = 2500
# technical-overview.md is the full-detail companion and is deliberately not
# ceilinged the same way; its authored sections still get floors.
TECHNICAL_PROSE_BUDGET = 4000

SECTION_OUTPUT_SCHEMA_ID = "section-generate.v1"

_PREAMBLE = """\
You are producing EXACTLY ONE section of a Project Analysis report.

Return JSON: {"section_id": "<the section id given below>", "content_md":
"<the section body>", "word_count": <words in content_md>}.

Hard rules for this section:
- Write the BODY ONLY. Do not repeat the section heading -- assembly adds it.
- Stay within your prose budget (given below). It is a real constraint: if the
  material does not fit, MOVE the remainder to the companion document by
  naming what belongs there, and say so in one line. NEVER meet the budget by
  dropping a required category, hiding a coverage gap, omitting a module that
  has a finding, or compressing two facts into one vaguer sentence.
- Every claim carries a citation to the evidence in your inputs. A claim you
  cannot cite becomes an explicit open question instead.
- An inapplicable or unavailable category is ONE honest line, never silence.
- Absence of evidence is never rendered as health: write "no concern observed"
  scoped to the signals that ran, or "unknown" when a signal did not run.
- Never emit a generic "unknown" fallback when the packet contains evidence.
  A short unknown line is valid only when it names the unavailable input and
  cites its exact source reference; "host judgment lacks evidence" is not an
  evidence statement and will be rejected. Synthesize the supplied facts
  instead, even when their confidence is low or their coverage is partial.
- Never expose an internal repository identifier. Use the corresponding
  repository reference from identity-boundary.json in prose and citations;
  that packet names the identifiers which are forbidden in final reports.
"""


class ReportError(ValueError):
    """A report step's precondition failed — fail closed rather than emit a
    partial document."""


@dataclass(frozen=True)
class PlannedSection:
    section_id: str
    task_id: str
    document: str
    wave: int
    budget_words: int
    estimated_tokens: int
    created: bool


def _load(run: Path, relative: str, *, required: bool = False) -> Any:
    path = run / relative
    if not path.is_file():
        if required:
            raise ReportError(f"missing required artifact: {relative}")
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except ValueError as exc:
        raise ReportError(f"{relative}: invalid JSON: {exc}") from exc


def section_task_id(section_id: str) -> str:
    return "section-" + section_id.replace(".", "-")


# --------------------------------------------------------------------------- #
# packet inputs
# --------------------------------------------------------------------------- #

def _section_inputs(run: Path, section: catalog.Section,
                    produced: dict[str, str]) -> dict[str, str]:
    """Evidence for one authored section — its own declared inputs, nothing
    else. A section that only needs the finding set never carries the graph;
    that narrowness IS the token win, and it also keeps a section's judgment
    anchored to the evidence it is supposed to be about."""
    inputs: dict[str, str] = {}
    for name in section.inputs:
        if name == "sections-so-far":
            # For §2 / the ToC: what the rest of the document already says.
            # It is the whole point of those sections that they summarize
            # material rather than introduce it.
            body = "\n\n".join(
                f"{catalog.BY_ID[section_id].heading}\n\n{text}"
                for section_id, text in produced.items()
                if catalog.BY_ID[section_id].document == section.document)
            inputs["sections-so-far.md"] = body or "(no sections produced yet)"
            continue
        if name == "changeability-cells":
            inputs["changeability-cells.md"] = renders.changeability_table(run)
            continue
        if name == "test-ci-evidence":
            from . import planner as _planner
            from .. import identity
            from ..targetspec import TargetSpec
            rows = _planner._test_ci_evidence_rows(
                TargetSpec.load(run / "targets.json"), identity.load(run))
            inputs["test-ci-evidence.json"] = json.dumps(rows, sort_keys=True)
            continue
        if name in ("route-inventory", "ui-route-linkage", "graph",
                    "project-map-relationships"):
            if name == "project-map-relationships":
                inputs["relationships.md"] = renders.relationships(run)
                continue
            synthesis = _load(run, "synthesis-input.json") or {}
            key = name.replace("-", "_")
            section_doc = synthesis.get(key)
            if section_doc is not None:
                inputs[f"{name}.json"] = json.dumps(section_doc, sort_keys=True)
            continue
        if name == "system-model.json":
            # NEVER pass this file whole: it is the raw graph (tens of MB,
            # every symbol/file/edge in the workspace) and no author section
            # needs more than a bounded slice of it. §8's own question --
            # data ownership -- is exactly what `renders.shared_persistence`
            # already answers for the analogous RENDERED section
            # (projectmap.persistence); reuse it rather than hand the model
            # the graph to re-derive the same projection itself. A future
            # author section needing a different graph slice should add its
            # own bounded case here, never fall through to a raw file read
            # (57B-117 M3 acceptance: this file dumped whole made an 8.16M
            # estimated-token packet needing 56 shards for a 110-word floor).
            inputs["shared-persistence.md"] = renders.shared_persistence(run)
            continue
        if name.endswith("/"):
            # A capability artifact directory: pass its files' contents,
            # bounded by the composer if large.
            directory = run / name.rstrip("/")
            if directory.is_dir():
                merged = {}
                for path in sorted(directory.glob("*.json")):
                    try:
                        merged[path.name] = json.loads(path.read_text("utf-8"))
                    except ValueError:
                        continue
                if merged:
                    inputs[f"{name.rstrip('/')}.json"] = json.dumps(merged, sort_keys=True)
            continue
        path = run / name
        if path.is_file():
            inputs[name.replace("/", "__")] = path.read_text("utf-8")
    # A final-report boundary, supplied to every author task and validated at
    # submit time.  The task ledger is not a shipped artifact, so it may carry
    # the internal join keys solely to prevent them leaking into assembled MD.
    from .. import identity
    mapping = identity.load(run)
    restricted = [item for item in (mapping.project, *mapping.repositories)
                  if item.internal_id != item.reference]
    if restricted:
        inputs["identity-boundary.json"] = json.dumps({
            "forbidden_internal_ids": sorted(item.internal_id for item in restricted),
            "repository_references": sorted(item.reference for item in mapping.repositories),
        }, sort_keys=True)
    if section.document == catalog.OVERVIEW:
        # The primary PM overview may describe observed capabilities, but must
        # not expose implementation filenames, source paths, source citations,
        # or code-style signal-tool identifiers.  Compute the exact labels the
        # final audit uses and make a leak retryable at task submission rather
        # than discoverable only after all report waves have completed.
        from .. import overview_audit
        model = _load(run, "system-model.json") or {}
        file_labels = sorted({str(node.get("label", ""))
                              for node in model.get("nodes", [])
                              if isinstance(node, dict) and node.get("kind") == "file"
                              and overview_audit._is_source_path_label(
                                  str(node.get("label", "")))})
        summary = _load(run, "signals/run-summary.json") or {}
        tool_identifiers = sorted({str(row.get("tool"))
                                   for row in summary.get("signals", [])
                                   if isinstance(row, dict) and row.get("tool")})
        inputs["pm-abstraction-boundary.json"] = json.dumps({
            "forbidden_source_labels": file_labels,
            "forbidden_tool_identifiers": tool_identifiers,
        }, sort_keys=True)
    return {name: sanitize_text(content) for name, content in inputs.items()}


def _instructions(section: catalog.Section, budget_words: int) -> str:
    lines = [_PREAMBLE, "",
             f"Section id: `{section.section_id}`",
             f"Document: `{section.document}`",
             f"Heading (do NOT repeat it in your body): `{section.heading}`",
             f"Prose budget: about {budget_words} words.",
             f"Completeness floor: at least {section.min_words} words of substance, "
             "or one honest line stating why the category is inapplicable or "
             "unavailable in this analysis."]
    if section.note:
        lines += ["", f"What this section is for: {section.note}"]
    if section.document == catalog.OVERVIEW:
        lines += ["", "PM abstraction boundary: describe product capabilities and "
                  "operational consequences only. Do not use source paths, source "
                  "citations, implementation filenames, or backticked signal-tool "
                  "identifiers; pm-abstraction-boundary.json lists the forbidden values."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #

def plan_reports(run_dir: str | Path, *, document: str | None = None,
                 wave: int | None = None,
                 context_budget_tokens: int = 96_000) -> list[PlannedSection]:
    """Register the authored sections that are ready to run.

    Called once per wave: a wave's tasks are composed from the sections
    EARLIER waves already produced, so a section that summarizes the document
    (§2, the ToC) sees real text rather than a placeholder — the same
    two-phase reason ``plan_dedup`` exists.
    """
    run = Path(run_dir).expanduser().resolve()
    problems = catalog.validate_catalog()
    if problems:
        raise ReportError("section catalog is unsound: " + "; ".join(problems))

    produced = collected_sections(run)
    documents = [document] if document else list(catalog.DOCUMENTS)
    packets, planned = [], []
    for document_name in documents:
        budgets = catalog.prose_budget(
            document_name,
            OVERVIEW_PROSE_CEILING if document_name == catalog.OVERVIEW
            else TECHNICAL_PROSE_BUDGET)
        for section in catalog.for_document(document_name):
            if section.kind == "render":
                continue
            if wave is not None and section.wave != wave:
                continue
            missing = [dependency for dependency in section.depends_on
                       if dependency not in produced
                       and catalog.BY_ID[dependency].kind != "render"]
            if missing:
                continue  # its wave has not come yet
            budget_words = budgets.get(section.section_id, section.min_words)
            inputs = _section_inputs(run, section, produced)
            # The floor is enforced at SUBMIT time via schemas.py's
            # section-generate crosscheck, reading this same structured
            # value -- not just stated in prose and hoped for. Carrying it as
            # an input (rather than parsing it back out of `instructions`)
            # also means changing the floor changes the packet's digest, so
            # a floor edit correctly starts a new task generation.
            inputs["floor.json"] = json.dumps({"min_words": section.min_words},
                                              sort_keys=True)
            instructions = _instructions(section, budget_words)
            built = compose(
                task_id=section_task_id(section.section_id),
                template_id=f"section-{section.section_id}",
                template_version=content_digest(_PREAMBLE, section.heading,
                                                section.note, str(budget_words),
                                                str(section.min_words)),
                task_type="section-generate", instructions=instructions,
                inputs=inputs, output_schema_id=SECTION_OUTPUT_SCHEMA_ID,
                context_budget_tokens=context_budget_tokens)
            packets.extend(built)
            planned.append(PlannedSection(
                section_id=section.section_id, task_id=built[0].task_id,
                document=document_name, wave=section.wave,
                budget_words=budget_words,
                estimated_tokens=sum(
                    len(packet.instructions) // 4
                    + sum(len(item.content) // 4 for item in packet.inputs.values())
                    for packet in built),
                created=False))
    created = set(Engine(run).create_tasks(packets)) if packets else set()
    return [PlannedSection(**{**vars(row), "created": row.task_id in created})
            for row in planned]


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #

def collected_sections(run: Path) -> dict[str, str]:
    """section_id -> body, for every authored section already validated."""
    outputs = validated_outputs(run, task_type="section-generate")
    bodies: dict[str, str] = {}
    for output in outputs.values():
        if not isinstance(output, dict):
            continue
        section_id = str(output.get("section_id", ""))
        if section_id in catalog.BY_ID:
            bodies[section_id] = str(output.get("content_md", ""))
    return bodies


def write_views_manifest(run_dir: str | Path) -> Path:
    """Additive observability artifact (57B-118 M4 / 57B-112 §6): catalogs
    every distinct BOUNDED view name ``_section_inputs`` produces across the
    whole section catalog, with a content digest and the section(s) that
    consume it.

    This does not retire ``synthesis-input.json`` or introduce a second data
    source — it calls the SAME ``_section_inputs`` every section's packet
    already goes through (using whatever sections have validated so far, via
    ``collected_sections``), so the manifest can never drift from what a
    section actually received. Nothing reads this file; it exists so a human
    (or a future audit check) can see which bounded projection backed a
    section's evidence without re-deriving ``_section_inputs``' own logic.
    """
    run = Path(run_dir).expanduser().resolve()
    produced = collected_sections(run)
    views: dict[str, dict[str, Any]] = {}
    for section in catalog.authored():
        inputs = _section_inputs(run, section, produced)
        for name, content in inputs.items():
            entry = views.setdefault(
                name, {"content_digest": content_digest(content), "consumers": []})
            entry["consumers"].append(section.section_id)
    for entry in views.values():
        entry["consumers"] = sorted(entry["consumers"])
    manifest = {"views": views}
    path = run / "views-manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
    return path


def assemble_document(run_dir: str | Path, document: str, *,
                      out: str | Path | None = None) -> Path:
    """Write one document from its sections, in template order.

    Rendered sections are produced here and now (they are pure functions of
    artifacts); authored ones come from their validated task. A missing
    authored section fails closed — a document silently short a section is
    exactly the failure the floors exist to prevent.
    """
    run = Path(run_dir).expanduser().resolve()
    bodies = collected_sections(run)
    rows = catalog.for_document(document)

    parts: list[str] = []
    headings: list[str] = []
    contents_section: catalog.Section | None = None
    missing: list[str] = []
    for section in rows:
        if section.section_id == "technical.contents":
            contents_section = section
            parts.append(None)  # placeholder, filled once headings are known
            headings.append(section.heading)
            continue
        if section.kind == "render":
            body = renders.render_section(section.section_id, run)
        else:
            body = bodies.get(section.section_id)
            if body is None:
                missing.append(section.section_id)
                continue
        parts.append(f"{section.heading}\n\n{body.strip()}")
        headings.append(section.heading)
    if missing:
        raise ReportError(
            f"{document}: authored section(s) not yet validated: {', '.join(missing)}")
    if contents_section is not None:
        index = parts.index(None)
        parts[index] = (f"{contents_section.heading}\n\n"
                        + renders.contents(run, headings=[
                            heading for heading in headings
                            if heading != contents_section.heading]))

    project = (_load(run, "run-provenance.json") or {}).get("project_ref", "Project")
    title = {catalog.OVERVIEW: f"# {project} — Project Overview",
             catalog.TECHNICAL: f"# {project} — Technical Overview & Diagnosis",
             catalog.PROJECT_MAP: f"# {project} — Project Map"}[document]
    # These projections are deliberately rendered at final assembly.  The
    # audit compares them byte-for-byte with their canonical artifacts, so a
    # model cannot omit, paraphrase, or duplicate either accountability table.
    machine_blocks: tuple[str, ...] = ()
    if document == catalog.TECHNICAL:
        machine_blocks = (coverage_render.render(run).strip(),)
    elif document == catalog.PROJECT_MAP:
        machine_blocks = (module_render.render(run).strip(),)
    text = title + "\n\n" + "\n\n".join(parts + list(machine_blocks)) + "\n"
    path = Path(out).expanduser().resolve() if out else run / document
    path.write_text(sanitize_text(text), "utf-8")
    return path


# --------------------------------------------------------------------------- #
# floors + ceiling, always reported together
# --------------------------------------------------------------------------- #

def _prose_words(body: str) -> int:
    """Words of PROSE: table rows and fenced blocks are excluded, matching
    synthesis.md's own ceiling definition (tables and mermaid do not count
    against a reader's ~10 minutes the way sentences do)."""
    words, fenced = 0, False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced or stripped.startswith("|") or not stripped:
            continue
        words += len(stripped.split())
    return words


def document_floors(run_dir: str | Path, document: str, *,
                    path: str | Path | None = None) -> dict[str, Any]:
    """Report the ceiling AND every floor for one document, in one dict.

    Never returns the ceiling alone. A caller that only learns "too long"
    will shorten, and shortening is exactly the failure mode being guarded
    against — so the same report always carries what must not be dropped, and
    names relocation as the remedy.
    """
    run = Path(run_dir).expanduser().resolve()
    document_path = Path(path).expanduser().resolve() if path else run / document
    text = document_path.read_text("utf-8") if document_path.is_file() else ""
    bodies = collected_sections(run)

    section_rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    budgets = catalog.prose_budget(
        document, OVERVIEW_PROSE_CEILING if document == catalog.OVERVIEW
        else TECHNICAL_PROSE_BUDGET)

    for section in catalog.for_document(document):
        present = section.heading in text
        body = bodies.get(section.section_id, "")
        words = _prose_words(body) if section.kind != "render" else 0
        budget = budgets.get(section.section_id, 0)
        row = {"section_id": section.section_id, "kind": section.kind,
               "present": present, "prose_words": words,
               "budget_words": budget, "min_words": section.min_words}
        if not present:
            failures.append({
                "check": "floor-section-missing", "location": section.section_id,
                "detail": f"{document} does not contain {section.heading!r}"})
        if section.kind != "render" and body and words < section.min_words:
            # A short section is only acceptable when it is honestly saying
            # the category is inapplicable or unavailable -- the SAME marker
            # list schemas.py's submit-time floor crosscheck accepts, so a
            # section cannot pass one check and fail the other on identical
            # wording.
            honest = any(marker in body.lower()
                         for marker in schemas.HONEST_INAPPLICABILITY_MARKERS)
            if not honest:
                failures.append({
                    "check": "floor-section-thin", "location": section.section_id,
                    "detail": (f"{words} words of prose is below this section's floor of "
                               f"{section.min_words}, and it does not state an honest "
                               "inapplicability line")})
        if budget and words > budget * 1.25:
            failures.append({
                "check": "ceiling-section-overflow", "location": section.section_id,
                "detail": (f"{words} words exceeds this section's {budget}-word budget; "
                           "RELOCATE the remainder to the companion document — do not "
                           "shorten by dropping or compressing content")})
        for required in section.must_contain:
            if present and required not in text:
                failures.append({
                    "check": "floor-required-content", "location": section.section_id,
                    "detail": f"required content {required!r} is absent"})
        section_rows.append(row)

    total_prose = sum(row["prose_words"] for row in section_rows)
    ceiling = OVERVIEW_PROSE_CEILING if document == catalog.OVERVIEW else TECHNICAL_PROSE_BUDGET
    if total_prose > ceiling * 1.25:
        failures.append({
            "check": "ceiling-document-overflow", "location": document,
            "detail": (f"{total_prose} words of prose exceeds the {ceiling}-word ceiling; "
                       "relocate detail to the companion document")})
    return {
        "document": document,
        "prose_words": total_prose,
        "prose_ceiling": ceiling,
        "sections": section_rows,
        "sections_expected": len(section_rows),
        "sections_present": sum(1 for row in section_rows if row["present"]),
        "failures": failures,
        "remedy": ("Overflow is resolved by RELOCATING content to the companion "
                   "document, never by shortening: no fact, coverage gap, or module "
                   "with a finding may be dropped to fit."),
    }
