"""Deterministic section renderers (57B-113 / 57B-117, M3).

Every function here produces one report section's body from validated
artifacts alone — no model, no judgment, no network. What that buys is not
speed (these are milliseconds) but GUARANTEES: a rendered section cannot
miscount, cannot soften a disclaimer, cannot quietly omit a module, and
cannot draw a diagram edge no table backs. Several synthesis.md rules that
were previously "the writer must remember this" become true by construction
here:

* §13 never says "healthy" and never leaves a module out, because the cells
  are computed from the finding set and the signal statuses rather than
  recalled;
* §6 / project-map topology edges are generated FROM the relationship rows,
  so "every mermaid edge is backed by a relationship-table row" cannot be
  violated;
* the machine-verified findings blocks are embedded verbatim, markers and
  all, so the protected projection cannot be paraphrased;
* disposition counts are summed, not asserted.

Each renderer returns the section BODY (no heading line — assembly adds the
heading from the catalog) and raises :class:`RenderError` when a required
artifact is missing, rather than emitting a plausible-looking empty section.
Absence that is a real finding about the project (no external candidates, no
co-change signal) renders as an explicit honest line instead.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import findings as findings_module
from .. import module_render
from .schemas import CHANGEABILITY_QUESTIONS

# The six changeability questions, in synthesis.md's own order, mapped to the
# §13 column each one drives. `none` is deliberately absent: a finding tagged
# `none` is a real finding that simply does not speak to changeability, and
# it must not silently become a concern in some column.
CHANGEABILITY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("boundary-clarity", "responsibility clarity"),
    ("change-spread", "change spread"),
    ("hidden-coupling", "hidden coupling"),
    ("verification-difficulty", "safety net"),
)

# Which signal families evidence which changeability column. A column whose
# signals did not run is `unknown` -- never "no concern observed", which
# would read absence of measurement as absence of a problem.
COLUMN_SIGNALS: dict[str, tuple[str, ...]] = {
    "boundary-clarity": ("scc", "dependency-cruiser", "go-list"),
    "change-spread": ("git-history", "jscpd", "jscpd-cross"),
    "hidden-coupling": ("dependency-cruiser", "go-list", "staticcheck"),
    "verification-difficulty": ("git-history", "scc"),
}

CONFIRMED = "confirmed concern"
NO_CONCERN = "no concern observed"
UNKNOWN = "unknown"

_NOT_APPLICABLE = "_No evidence of this category was found in the analyzed sources._"


class RenderError(ValueError):
    """A renderer's required artifact is missing or malformed. Fails closed:
    a section that cannot be rendered from real evidence must not be filled
    with a plausible empty shell."""


# --------------------------------------------------------------------------- #
# artifact access
# --------------------------------------------------------------------------- #

def _load(run: Path, relative: str, *, required: bool = True) -> Any:
    path = run / relative
    if not path.is_file():
        if required:
            raise RenderError(f"missing required artifact: {relative}")
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except ValueError as exc:
        raise RenderError(f"{relative}: invalid JSON: {exc}") from exc


def _text(run: Path, relative: str, *, required: bool = True) -> str | None:
    path = run / relative
    if not path.is_file():
        if required:
            raise RenderError(f"missing required artifact: {relative}")
        return None
    return path.read_text("utf-8")


def _table(header: Iterable[str], rows: Iterable[Iterable[str]]) -> str:
    header = list(header)
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    body = ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    if not body:
        return "\n".join(lines + ["| " + " | ".join(["—"] * len(header)) + " |"])
    return "\n".join(lines + body)


def _cell(value: object) -> str:
    text = "—" if value in (None, "", [], {}) else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


# --------------------------------------------------------------------------- #
# §13 module changeability table -- every cell derived, never recalled
# --------------------------------------------------------------------------- #

def _signal_status(run: Path) -> dict[str, str]:
    """tool -> worst status observed across its rows. A tool that never
    appears is absent entirely, which the callers read as `unknown`."""
    summary = _load(run, "signals/run-summary.json")
    severity = {"complete": 0, "partial": 1, "skipped": 2, "failed": 3}
    worst: dict[str, str] = {}
    for row in summary.get("signals", []):
        if not isinstance(row, dict):
            continue
        tool, status = str(row.get("tool", "")), str(row.get("status", ""))
        if not tool or status not in severity:
            continue
        if tool not in worst or severity[status] > severity[worst[tool]]:
            worst[tool] = status
    return worst


def _column_ran(column: str, statuses: dict[str, str]) -> bool:
    """A column counts as measured when at least one of its signals produced
    evidence (complete or partial). Skipped/failed/absent do not."""
    return any(statuses.get(tool) in {"complete", "partial"}
               for tool in COLUMN_SIGNALS.get(column, ()))


def changeability_table(run: Path) -> str:
    module_doc = _load(run, "module-map.json")
    findings_doc = _load(run, "findings.json")
    statuses = _signal_status(run)

    modules = [row for row in module_doc.get("modules", []) if isinstance(row, dict)]
    if not modules:
        raise RenderError("module-map.json contains no modules to tabulate")

    # module_id -> question -> [finding_id, ...]
    concerns: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for finding in findings_doc.get("findings", []):
        if not isinstance(finding, dict):
            continue
        question = finding.get("changeability_question")
        if question not in CHANGEABILITY_QUESTIONS or question == "none":
            continue
        for module_id in finding.get("affected_modules", []) or []:
            concerns[str(module_id)][str(question)].append(str(finding.get("finding_id", "")))

    header = ["module"] + [label for _, label in CHANGEABILITY_COLUMNS] + ["confidence"]
    rows = []
    for module in sorted(modules, key=lambda row: str(row.get("module_id", ""))):
        module_id = str(module.get("module_id", ""))
        cells = [module_id]
        for question, _label in CHANGEABILITY_COLUMNS:
            hits = concerns.get(module_id, {}).get(question, [])
            if hits:
                cells.append(f"{CONFIRMED} ({', '.join(sorted(hits)[:3])})")
            elif _column_ran(question, statuses):
                signals = ", ".join(
                    tool for tool in COLUMN_SIGNALS[question]
                    if statuses.get(tool) in {"complete", "partial"})
                cells.append(f"{NO_CONCERN} (basis: {signals})")
            else:
                cells.append(f"{UNKNOWN} (signal did not run)")
        cells.append(str(module.get("confidence", UNKNOWN)))
        rows.append(cells)

    gaps = sorted(question for question, _ in CHANGEABILITY_COLUMNS
                  if not _column_ran(question, statuses))
    note = ("\n\n_Per-gap unknown mapping: "
            + ("; ".join(f"`{question}` — its signals did not run in this analysis"
                         for question in gaps)
               if gaps else "every changeability signal produced evidence in this run")
            + "._")
    return _table(header, rows) + note


# --------------------------------------------------------------------------- #
# protected machine blocks -- embedded verbatim, markers and all
# --------------------------------------------------------------------------- #

def pm_findings_block(run: Path) -> str:
    text = _text(run, findings_module.PM_FILE)
    if findings_module.PM_BEGIN not in text:
        raise RenderError(f"{findings_module.PM_FILE} is missing its machine markers")
    return text.strip()


def technical_findings_block(run: Path) -> str:
    text = _text(run, findings_module.TECHNICAL_FILE)
    if findings_module.TECHNICAL_BEGIN not in text:
        raise RenderError(f"{findings_module.TECHNICAL_FILE} is missing its machine markers")
    return text.strip()


# --------------------------------------------------------------------------- #
# relationships + topology -- the diagram is generated FROM the rows
# --------------------------------------------------------------------------- #

_EDGE_STATUS_ARROW = {"observed": "-->", "inferred": "-.->", "unresolved": "-.->",
                      "user-confirmed": "==>"}


def _node_index(model: dict) -> dict[str, dict]:
    return {str(node.get("id", "")): node for node in model.get("nodes", [])
            if isinstance(node, dict)}


def _node_label(node: dict) -> str:
    attrs = node.get("attrs", {}) if isinstance(node.get("attrs"), dict) else {}
    for key in ("module_id", "name", "value", "path", "table", "label"):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            return value
    return str(node.get("id", "—"))


def _module_owned_nodes(run: Path, model: dict) -> dict[str, str]:
    """graph node id -> owning module_id.

    Ownership runs through the AUTHORITATIVE chain the module map already
    establishes: a module node carries the candidate ids merged into it, and
    each candidate carries the graph node ids it was derived from. The
    model's own sparse ``module -> file`` edges are NOT that chain and using
    them would silently under-report (in a real run only one module carried
    such edges at all), so the candidate mapping is what this walks.
    """
    candidates_doc = _load(run, "module-candidates.json", required=False) or {}
    by_candidate = {str(row.get("candidate_id", "")): row
                    for row in candidates_doc.get("candidates", [])
                    if isinstance(row, dict)}
    owner: dict[str, str] = {}
    for node in model.get("nodes", []):
        if not isinstance(node, dict) or node.get("kind") != "module":
            continue
        attrs = node.get("attrs", {}) if isinstance(node.get("attrs"), dict) else {}
        module_id = str(attrs.get("module_id", "")) or str(node.get("id", ""))
        for candidate_id in attrs.get("candidate_ids", []) or []:
            for node_id in by_candidate.get(str(candidate_id), {}).get("node_ids", []) or []:
                owner[str(node_id)] = module_id
    return owner


def _relationship_rows(run: Path) -> list[dict[str, str]]:
    """Module-to-module relationships, derived through what actually connects
    them, with the connector named.

    The system model carries no direct module-to-module edge: modules own
    routes, files and data stores, and two modules are related when an edge
    crosses from one module's owned node to another's. The row says which
    node it crossed at — "these two meet at this data store" is inspectable
    and bounded, where a bare A->B edge would hide the reason and invite a
    reader to assume a runtime call that was never observed.
    """
    model = _load(run, "system-model.json")
    nodes = _node_index(model)
    owner = _module_owned_nodes(run, model)
    if not owner:
        return []

    rows: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for edge in model.get("edges", []):
        if not isinstance(edge, dict):
            continue
        source_owner = owner.get(str(edge.get("src", "")))
        target_node_id = str(edge.get("dst", ""))
        target_owner = owner.get(target_node_id)
        if not source_owner or not target_owner or source_owner == target_owner:
            continue
        target_node = nodes.get(target_node_id, {})
        via = _node_label(target_node) if target_node else target_node_id
        kind = f"shared {target_node.get('kind', 'node')}"
        left, right = sorted((source_owner, target_owner))
        rows[(left, right, kind, via)] = {
            "source": left, "target": right, "kind": kind, "via": via,
            # A static co-reference in this snapshot -- both modules reach the
            # same node. Not evidence of a call between the two modules.
            "status": "observed"}
    return sorted(rows.values(),
                  key=lambda row: (row["source"], row["target"], row["kind"], row["via"]))


def relationships(run: Path) -> str:
    rows = _relationship_rows(run)
    if not rows:
        return ("_No module-level relationship was resolved from the analyzed sources: no "
                "route, data store or external boundary is touched by more than one "
                "module._")
    return "\n".join([
        _table(("from", "to", "kind", "via", "status"),
               ([row["source"], row["target"], row["kind"], row["via"], row["status"]]
                for row in rows)), "",
        "_`via` names the shared node through which the two modules meet. A row is a "
        "STATIC co-reference in this snapshot — both modules touch that node — never "
        "evidence of a runtime call between them._"])


def _mermaid_id(value: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in value)
    return safe or "node"


def topology(run: Path) -> str:
    """A mermaid diagram whose every edge comes from a relationship row.

    Generating rather than authoring is what makes synthesis.md's
    edge-must-be-backed rule structural: there is no path by which an
    unbacked edge can appear, and the syntax cannot be malformed.
    """
    rows = _relationship_rows(run)
    if not rows:
        return _NOT_APPLICABLE
    # One edge per module PAIR: the relationship rows carry one row per shared
    # node, which would otherwise draw the same pair a dozen times and make the
    # diagram unreadable. The grouped label states the count and names the
    # shared nodes, so the aggregation is disclosed rather than silent -- which
    # is exactly the condition synthesis.md puts on aggregating rows into one
    # rendered edge.
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        grouped[(row["source"], row["target"])].append(row["via"])

    lines = ["```mermaid", "flowchart LR"]
    for node in sorted({row["source"] for row in rows} | {row["target"] for row in rows}):
        lines.append(f'    {_mermaid_id(node)}["{node}"]')
    for (source, target), vias in sorted(grouped.items()):
        shown = ", ".join(sorted(vias)[:2])
        more = f" +{len(vias) - 2}" if len(vias) > 2 else ""
        label = f"shares {len(vias)}: {shown}{more}".replace('"', "'")
        lines.append(f"    {_mermaid_id(source)} -. {label} .-> {_mermaid_id(target)}")
    lines.append("```")
    lines.append("")
    lines.append(
        f"_Generated from the relationships table: {len(grouped)} module pair(s) drawn "
        f"from {len(rows)} relationship row(s); each edge aggregates the shared nodes "
        "named in its label. Every edge is backed by table rows by construction — the "
        "diagram is rendered from them, not drawn alongside them. Dotted edges denote "
        "static co-reference, not an observed call between the two modules._")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# provenance / scope / basis
# --------------------------------------------------------------------------- #

SCOPE_DISCLAIMER = (
    "> This report is derived from **repository evidence only** — code, configuration, "
    "and git history in the analyzed snapshot (clean commit, dirty worktree, or non-git "
    "folder, as recorded in the provenance block). It does not observe production "
    "traffic, runtime performance, or incident history; it cannot confirm whether a "
    "configured integration is live in production; and it is not a comprehensive "
    "security or license audit. Findings labeled `status unresolved` need human "
    "confirmation.")


def _repo_rows(run: Path) -> list[dict[str, Any]]:
    provenance = _load(run, "run-provenance.json")
    repos = provenance.get("repositories") or provenance.get("repos") or []
    return [row for row in repos if isinstance(row, dict)]


def analysis_basis(run: Path) -> str:
    provenance = _load(run, "run-provenance.json")
    rows = []
    for repo in _repo_rows(run):
        head = str(repo.get("head", ""))
        dirty = repo.get("dirty_detail", "no")
        state = "clean" if dirty in ("no", "", None) else f"dirty ({dirty})"
        rows.append([repo.get("reference") or repo.get("repository_ref") or repo.get("path"),
                     head[:12] or "—", repo.get("head_timestamp", "—"), state])
    analyzed_at = provenance.get("analyzed_at", "—")
    parts = [f"**Analysis run:** {analyzed_at}", "",
             _table(("repository", "short HEAD", "HEAD date", "state"), rows), "",
             "Full provenance, tool versions and coverage detail: "
             "[technical overview](technical-overview.md).", "", SCOPE_DISCLAIMER]
    return "\n".join(parts)


def run_provenance(run: Path) -> str:
    provenance = _load(run, "run-provenance.json")
    generation = provenance.get("generation", {}) if isinstance(
        provenance.get("generation"), dict) else {}
    rows = [["analyzed at", provenance.get("analyzed_at", "—")],
            ["language", provenance.get("language", "—")],
            ["model", generation.get("model", "unknown")],
            ["effort", generation.get("effort", "unknown")]]
    preparation = provenance.get("preparation", {})
    if isinstance(preparation, dict):
        for key in sorted(preparation):
            rows.append([f"preparation.{key}", preparation[key]])
    repo_rows = [[repo.get("reference") or repo.get("repository_ref"),
                  str(repo.get("head", ""))[:12],
                  repo.get("branch", "—"), repo.get("dirty_detail", "—"),
                  repo.get("commit_count", "—")]
                 for repo in _repo_rows(run)]
    return "\n".join([
        _table(("field", "value"), rows), "",
        _table(("repository", "HEAD", "branch", "dirty", "commits"), repo_rows), "",
        SCOPE_DISCLAIMER])


def analysis_scope(run: Path) -> str:
    spec = _load(run, "targets.json")
    discovery = _load(run, "discovery-report.json", required=False) or {}
    rows = [[repo.get("repository_ref") or repo.get("repo_id"), repo.get("path", "—")]
            for repo in spec.get("repos", []) if isinstance(repo, dict)]
    not_targeted = discovery.get("not_targeted") or []
    lines = [_table(("analyzed repository", "path (workspace-relative label)"), rows)]
    if not_targeted:
        lines += ["", "**Present in the workspace but NOT analyzed:**", ""]
        lines += [f"- {entry}" for entry in not_targeted]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# coverage / health / disposition -- counts summed, never asserted
# --------------------------------------------------------------------------- #

def lens_coverage(run: Path) -> str:
    summary = _load(run, "signals/run-summary.json")
    rows = [[row.get("tool"), row.get("repository_ref", "—"), row.get("status"),
             row.get("reason", "—")]
            for row in summary.get("signals", []) if isinstance(row, dict)]
    aggregate = summary.get("aggregate_status", "—")
    return "\n".join([
        f"**Aggregate signal status:** `{aggregate}`", "",
        "Per-signal detail, verbatim from the run summary:", "",
        _table(("tool", "repository", "status", "reason"), rows)])


def module_health(run: Path) -> str:
    module_doc = _load(run, "module-map.json")
    findings_doc = _load(run, "findings.json")
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for finding in findings_doc.get("findings", []):
        if not isinstance(finding, dict):
            continue
        priority = str(finding.get("priority", "—"))
        for module_id in finding.get("affected_modules", []) or []:
            counts[str(module_id)][priority] += 1
    rows = []
    for module in sorted(module_doc.get("modules", []),
                         key=lambda row: str(row.get("module_id", ""))):
        module_id = str(module.get("module_id", ""))
        per = counts.get(module_id, {})
        total = sum(per.values())
        rows.append([module_id, module.get("classification", "—"),
                     module.get("confidence", "—"), total or NO_CONCERN,
                     per.get("critical", 0), per.get("high", 0),
                     per.get("medium", 0), per.get("low", 0)])
    return "\n".join([
        _table(("module", "classification", "confidence", "findings",
                "critical", "high", "medium", "low"), rows), "",
        f"_A module with no findings reads `{NO_CONCERN}`, scoped to the signals that "
        "ran in this analysis — it is never a statement of health._"])


def _bounded_items(section: Any) -> list[dict[str, Any]]:
    """Rows out of a synthesis-input bounded section.

    These sections are wrappers -- ``{total_count, included_count, truncated,
    items}`` -- and sometimes nest one wrapper inside another key (``rows``).
    Reading them wrongly is silent: a renderer sees no list and emits an
    honest-looking "no evidence" line for a section that has hundreds of rows,
    which is worse than crashing. So unwrap explicitly, at every level.
    """
    while isinstance(section, dict):
        for key in ("items", "rows"):
            if key in section:
                section = section[key]
                break
        else:
            return []
    return [row for row in section if isinstance(row, dict)] if isinstance(section, list) else []


def _truncation_note(section: Any) -> str:
    if isinstance(section, dict) and section.get("truncated"):
        return (f"\n\n_Bounded projection: {section.get('included_count', '?')} of "
                f"{section.get('total_count', '?')} row(s) shown; the full set is in the "
                "run's own artifacts._")
    return ""


def _integration_rows(run: Path) -> list[dict[str, Any]]:
    discovery = _load(run, "discovery-report.json", required=False) or {}
    candidates = discovery.get("integration_candidates")
    if isinstance(candidates, list):
        return [row for row in candidates if isinstance(row, dict)]
    synthesis = _load(run, "synthesis-input.json", required=False) or {}
    return _bounded_items(synthesis.get("integration_candidates"))


def external_disposition(run: Path) -> str:
    rows = _integration_rows(run)
    if not rows:
        return _NOT_APPLICABLE
    per_disposition: dict[str, int] = defaultdict(int)
    for row in rows:
        per_disposition[str(row.get("disposition", "unresolved"))] += 1
    table = _table(("disposition", "candidates"),
                   ([name, per_disposition[name]] for name in sorted(per_disposition)))
    total = sum(per_disposition.values())
    return "\n".join([
        table, "", f"**Total candidates: {total}** — the per-disposition counts above sum "
        "to this total by construction.", "",
        "_Complete disposition accounting is not integration completeness: it means every "
        "mechanically surfaced candidate was accounted for, not that every real "
        "integration was found._"])


def external_systems(run: Path) -> str:
    rows = _integration_rows(run)
    if not rows:
        return _NOT_APPLICABLE
    shown = [row for row in rows
             if str(row.get("disposition", "")) in {"included", "unresolved"}]
    if not shown:
        return ("_Every mechanically surfaced external candidate was dispositioned "
                "`excluded`; see the technical overview for the full accounting._")
    return _table(("system", "disposition", "evidence"),
                  ([row.get("value") or row.get("host") or row.get("name"),
                    row.get("disposition"),
                    (row.get("evidence") or [""])[0] if isinstance(row.get("evidence"), list)
                    else row.get("evidence", "—")]
                   for row in sorted(shown, key=lambda row: str(row.get("value", "")))))


def referenced_not_analyzed(run: Path) -> str:
    rows = [row for row in _integration_rows(run)
            if str(row.get("disposition", "")) == "unresolved"]
    if not rows:
        return ("_No configured endpoint was found whose serving source is outside the "
                "analyzed repositories._")
    return _table(("reference", "why unresolved"),
                  ([row.get("value") or row.get("host"),
                    row.get("reason", "serving source not among the analyzed repositories")]
                   for row in sorted(rows, key=lambda row: str(row.get("value", "")))))


def shared_persistence(run: Path) -> str:
    """Data stores reached by more than one MODULE, with the access
    distinction each side actually reached.

    Module attribution runs through the same authoritative candidate chain
    ``_module_owned_nodes`` uses. The access level is carried verbatim from
    the edge (declaration / schema-write / write / read / join-reference):
    synthesis.md is explicit that a bare name match is never confirmed shared
    persistence, so the distinction reached is stated rather than flattened
    into "shared".
    """
    model = _load(run, "system-model.json")
    nodes = _node_index(model)
    owner = _module_owned_nodes(run, model)
    stores: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    # A store node is itself owned by whichever module its candidate merged
    # into; that module counts as reaching it. Counting only INBOUND edges
    # would report a store written by two modules as touched by one, which is
    # precisely the shared-persistence case the reader needs.
    for node_id, node in nodes.items():
        if node.get("kind") == "data-store" and node_id in owner:
            stores[node_id][owner[node_id]].add("declaration")
    for edge in model.get("edges", []):
        if not isinstance(edge, dict):
            continue
        target_id = str(edge.get("dst", ""))
        if nodes.get(target_id, {}).get("kind") != "data-store":
            continue
        module_id = owner.get(str(edge.get("src", "")))
        if not module_id:
            continue
        attrs = edge.get("attrs", {}) if isinstance(edge.get("attrs"), dict) else {}
        stores[target_id][module_id].add(str(attrs.get("access", "unresolved")))
    shared = {store: touching for store, touching in stores.items() if len(touching) > 1}
    if not shared:
        return ("_No data store in the analyzed sources is reached by more than one "
                "module. Single-module stores are listed per module in the technical "
                "overview._")
    rows = []
    for store_id, touching in sorted(shared.items(), key=lambda item: _node_label(nodes[item[0]])):
        rows.append([
            _node_label(nodes[store_id]),
            "; ".join(f"{module} ({', '.join(sorted(access))})"
                      for module, access in sorted(touching.items())),
            len(touching)])
    return "\n".join([
        _table(("data store", "modules reaching it (access reached)", "modules"), rows), "",
        "_Access levels are the distinction each module's evidence actually reached — a "
        "shared name alone is not confirmed shared persistence, and none of these rows "
        "establishes a source of truth._"])


def route_references(run: Path) -> str:
    synthesis = _load(run, "synthesis-input.json", required=False) or {}
    section = synthesis.get("ui_route_linkage")
    rows = _bounded_items(section)
    if not rows:
        return _NOT_APPLICABLE
    inner = section.get("rows") if isinstance(section, dict) else None
    table = _table(
        ("frontend reference", "resolved backend route", "status"),
        ([row.get("call") or row.get("ui_ref") or row.get("source") or "—",
          row.get("route") or row.get("target") or row.get("resolved") or "—",
          row.get("status") or row.get("resolution") or "unresolved"]
         for row in rows[:200]))
    return table + _truncation_note(inner if isinstance(inner, dict) else section)


def interfaces(run: Path) -> str:
    synthesis = _load(run, "synthesis-input.json", required=False) or {}
    section = synthesis.get("route_inventory")
    rows = _bounded_items(section)
    if not rows:
        return _NOT_APPLICABLE
    inner = section.get("rows") if isinstance(section, dict) else None
    table = _table(
        ("method", "path", "repository", "handler"),
        ([row.get("method") or "—", row.get("path") or row.get("value") or "—",
          row.get("repository_ref") or "—", row.get("handler") or row.get("symbol") or "—"]
         for row in rows[:400]))
    note = (f"\n\n_{len(rows)} endpoint row(s) available; the first 400 are listed._"
            if len(rows) > 400 else "")
    return table + note + _truncation_note(inner if isinstance(inner, dict) else section)


def access_model(run: Path) -> str:
    synthesis = _load(run, "synthesis-input.json", required=False) or {}
    entries = _bounded_items(synthesis.get("role_catalog_by_repository"))
    rows: list[list[Any]] = []
    for entry in sorted(entries, key=lambda row: str(row.get("repository_ref", ""))):
        roles = entry.get("roles")
        if isinstance(roles, list) and roles:
            rows.append([entry.get("repository_ref", "—"),
                         ", ".join(str(role) for role in roles[:20])])
    if not rows:
        return ("_No static role catalog was resolved from the analyzed sources; "
                "enforcement evidence is reported per finding._")
    return _table(("repository", "roles declared"), rows)


def cochange(run: Path) -> str:
    bundle = _load(run, "cohesion-bundle.json", required=False)
    if not bundle:
        return _NOT_APPLICABLE
    clusters = [row for row in bundle.get("clusters", [])
                if isinstance(row, dict) and row.get("kind") == "co-change"]
    if not clusters:
        return ("_The history signal produced no co-change clusters in this analysis._")
    return _table(("cluster", "members", "measure"),
                  ([str(index + 1), ", ".join(str(m) for m in cluster.get("members", [])[:8]),
                    cluster.get("measure", "—")]
                   for index, cluster in enumerate(clusters[:50])))


def module_map_block(run: Path) -> str:
    """The machine-rendered module summary finalize-module-map produced."""
    return module_render.render(run).strip()


def contents(run: Path, *, headings: Iterable[str]) -> str:
    """A table of contents generated LAST from the assembled headings."""
    lines = []
    for heading in headings:
        title = heading.lstrip("#").strip()
        anchor = "".join(char.lower() if char.isalnum() else "-" for char in title).strip("-")
        while "--" in anchor:
            anchor = anchor.replace("--", "-")
        lines.append(f"- [{title}](#{anchor})")
    return "\n".join(lines) if lines else _NOT_APPLICABLE


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

RENDERERS: dict[str, Callable[..., str]] = {
    "overview.s1": analysis_basis,
    "overview.s6": topology,
    "overview.s13": changeability_table,
    "overview.s14": pm_findings_block,
    "technical.provenance": run_provenance,
    "technical.scope": analysis_scope,
    "technical.interfaces": interfaces,
    "technical.access": access_model,
    "technical.health": module_health,
    "technical.external": external_disposition,
    "technical.coverage": lens_coverage,
    "technical.findings": technical_findings_block,
    "technical.contents": contents,
    "projectmap.relationships": relationships,
    "projectmap.topology": topology,
    "projectmap.persistence": shared_persistence,
    "projectmap.routes": route_references,
    "projectmap.cochange": cochange,
    "projectmap.external": external_systems,
    "projectmap.unanalyzed": referenced_not_analyzed,
}


def render_section(section_id: str, run_dir: str | Path, **kwargs: Any) -> str:
    """Render one section's body. Raises :class:`RenderError` for an unknown
    section id — a rendered section with no renderer is a catalog bug, not
    something to paper over with an empty body."""
    renderer = RENDERERS.get(section_id)
    if renderer is None:
        raise RenderError(f"no deterministic renderer for section {section_id!r}")
    return renderer(Path(run_dir).expanduser().resolve(), **kwargs)
