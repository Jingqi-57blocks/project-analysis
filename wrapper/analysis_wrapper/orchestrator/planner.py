"""Judgment-DAG planner for the orchestrator (57B-113 / 57B-116, M2).

Two verbs, two CLI subcommands (``plan-judgment``, ``plan-dedup``):

- ``plan_judgment`` composes and registers, for a PREPARED run directory
  (``targets.json`` + ``signals/run-summary.json`` + ``synthesis-input.json``
  + ``module-candidates.json`` already written by ``prepare-overview``): one
  ``lens-findings`` task per (repo-sharded lens x repo) + one per
  workspace-sharded lens, PLUS one independent ``boundary-resolution`` task
  (no ``depends_on`` -- it runs in parallel with every lens task). Its
  packet consumes the full candidate universe plus an OPTIONAL deterministic
  ``cohesion-bundle.json`` when a sibling workstream has already written one
  into the run dir; a still-valid packet is composed without it.

- ``plan_dedup`` is a SEPARATE, LATER call: it reads every VALIDATED
  lens-findings output already in the ledger (``results.validated_outputs``)
  and composes the ONE global ``dedup-rank`` task from their real finding
  ids/content. This cannot happen inside ``plan_judgment``: a
  :class:`~.contracts.TaskPacket`'s inputs must be concrete TEXT at creation
  time, and dedup-rank's whole job is to merge findings that do not exist
  yet when the lens tasks are first planned. The engine's digest-keyed
  generations (``engine.py``) make re-running either verb idempotent.

No per-lens or per-shard merge step exists anywhere in this module --
cross-shard AND cross-lens duplication is left entirely to the one global
dedup-rank task (schemas.py already makes its ``merge_map``/``rank`` shape
self-verifiably exactly-once); this module only ever creates INDEPENDENT
lens-findings tasks, never one that depends on another lens's output.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import identity
from . import templates as tpl
from .composer import compose, estimate_tokens
from .contracts import TaskPacket
from .engine import Engine
from .results import validated_outputs
from .rule_gate import synthesis_md_text

DEFAULT_CONTEXT_BUDGET_TOKENS = 96_000


class PlannerError(ValueError):
    """A prepared run directory is missing an artifact `plan-judgment` /
    `plan-dedup` needs, or a two-phase-planning precondition failed (lens
    tasks still pending, colliding finding_ids, nothing to merge). Fail
    closed -- there is no reasonable partial packet to compose instead."""


# --------------------------------------------------------------------------- #
# small shared helpers
# --------------------------------------------------------------------------- #

def _load_json(path: Path) -> dict:
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError as exc:
        raise PlannerError(f"missing required run artifact: {path}") from exc
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise PlannerError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PlannerError(f"{path} must contain a JSON object")
    return value


def _packet_tokens(packet: TaskPacket) -> int:
    return estimate_tokens(packet.instructions) + sum(
        estimate_tokens(item.content) for item in packet.inputs.values())


_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def _repo_fragment(repository_ref: str, *, max_len: int = 20) -> str:
    """A stable, kebab-slug-safe task_id fragment for one repository_ref
    (which may itself contain slashes, dots, or uppercase letters that a
    ``task_id`` may not). The sha256 prefix guarantees no collision even
    when two very different references collapse to the same truncated
    slug -- mirrors module_map.py's own ``_candidate_id`` hash-suffix
    pattern for the same reason."""
    lowered = repository_ref.lower()
    collapsed = _SLUG_UNSAFE.sub("-", lowered).strip("-")
    fragment = collapsed[:max_len].strip("-") or "x"
    digest = hashlib.sha256(repository_ref.encode("utf-8")).hexdigest()[:8]
    return f"{fragment}-{digest}"


# --------------------------------------------------------------------------- #
# PlannedTask -- the planner's own stdout-facing summary row
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PlannedTask:
    task_id: str
    task_type: str
    lens_id: str            # "" for boundary-resolution / dedup-rank
    shard: str               # "repo" | "workspace" | "" for non-lens tasks
    repository_ref: str      # "" for workspace-shard / non-lens tasks
    packet_ids: tuple[str, ...]   # literal ledger task_ids after composer sharding
    estimated_tokens: int         # summed across every packet_id above
    created: bool                 # False = idempotent no-op re-create (unchanged digest)


# --------------------------------------------------------------------------- #
# lens-findings input selection
# --------------------------------------------------------------------------- #

# Per-lens EXTRA synthesis-input.json top-level sections beyond the
# universal baseline every lens gets automatically: module-candidates.json
# (trimmed to the task's own repo for a repo-sharded lens; full universe for
# a workspace-sharded one) so findings can cite real candidate_ids, and the
# matching `repositories` rows for citation revision markers
# (repo@<head|WORKTREE|NON-GIT>:path:line). See templates.py's per-lens
# frontmatter comments for the shard/signals half of this same reasoning;
# this table documents the INPUT-SELECTION half. Every extra section here is
# passed through via _SECTION_SPLITTERS below (array + small meta sibling,
# NOT verbatim -- see that table's own docstring for why) EXCEPT dead-code's
# route evidence, handled specially in _lens_inputs (trimmed per repo, since
# dead-code is the only repo-sharded lens needing route context).
_EXTRA_SECTIONS: dict[str, tuple[str, ...]] = {
    "structure-inventory": ("graph",),               # "route handlers outside the routing tree"
    "complexity": (),
    "dead-code": (),                                   # special-cased: _dead_code_route_nodes/_rows
    "duplication": (),
    "dependencies-cycles": ("graph", "route_inventory", "ui_route_linkage"),  # cross-stack contracts
    "hotspots-change-friction": (),
    "safety-net": (),
    "dependency-risk": ("integration_candidates",),    # "discovery-report candidates (dependency-only markers)"
    "open-lens": ("graph", "route_inventory", "ui_route_linkage",
                  "integration_candidates", "role_catalog_by_repository", "capabilities"),
}


def _matching_signal_rows(run_summary: Mapping[str, Any], signals: tuple[str, ...],
                          repository_ref: str | None) -> list[dict[str, Any]]:
    """complete/partial signal rows matching this lens's tool list (empty
    tuple = every tool, open-lens's own convention -- see
    ``templates.matches_signal``), further filtered to one repository_ref
    for a repo-sharded lens (``repository_ref=None`` = every repo, for a
    workspace-sharded lens)."""
    rows = []
    for row in run_summary.get("signals", []):
        if row.get("status") not in {"complete", "partial"}:
            continue
        view = str(row.get("view", ""))
        if not view.endswith(".view.txt"):
            continue
        if not tpl.matches_signal(str(row.get("tool", "")), signals):
            continue
        if repository_ref is not None and row.get("repository_ref") != repository_ref:
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: (
        str(row.get("tool", "")), str(row.get("repository_ref", "")), str(row.get("view", ""))))


# --------------------------------------------------------------------------- #
# input serialization: the composer can only shard a JSON ARRAY or
# line-oriented TEXT (composer.py's own documented limit) -- a JSON OBJECT
# wrapping the real list (e.g. {"candidates": [...]}) is neither, so the
# packet fails closed the moment that object becomes a packet's largest
# input. This bit a real run: lens-dependencies-cycles and lens-open-lens
# both hit "input 'module-candidates.json' is ... neither a JSON array nor
# line-oriented text" on a real prepared WCP run, at both the default 96k
# and a raised 180k budget. Every list-carrying input below is therefore
# split into (a) the bare array itself, sharded like any other array input,
# and (b) a small sibling "*-meta.json" input carrying whatever non-list
# bookkeeping the original section also had -- never silently dropped.
# --------------------------------------------------------------------------- #

def _split_module_candidates(module_candidates_doc: Mapping[str, Any],
                             repository_ref: str | None) -> tuple[list, dict]:
    candidates = list(module_candidates_doc.get("candidates", []))
    if repository_ref is not None:
        candidates = [row for row in candidates if row.get("repository_ref") == repository_ref]
    meta = {
        "schema_version": module_candidates_doc.get("schema_version", ""),
        "project_ref": module_candidates_doc.get("project_ref", ""),
        "candidate_count": len(candidates),
        "limitations": module_candidates_doc.get("limitations", []),
    }
    return candidates, meta


def _trim_repositories(synthesis_doc: Mapping[str, Any], repository_ref: str | None) -> list:
    items = list(synthesis_doc.get("repositories", {}).get("items", []))
    if repository_ref is not None:
        items = [row for row in items if row.get("repository_ref") == repository_ref]
    return items


def _dead_code_route_nodes(synthesis_doc: Mapping[str, Any], repository_ref: str) -> list:
    graph = synthesis_doc.get("graph") or {}
    route_bucket = graph.get("nodes", {}).get("route", {})
    return [row for row in route_bucket.get("items", [])
           if row.get("repository_ref") == repository_ref]


def _dead_code_route_inventory_rows(synthesis_doc: Mapping[str, Any],
                                    repository_ref: str) -> list:
    inventory_doc = synthesis_doc.get("route_inventory") or {}
    return [row for row in inventory_doc.get("rows", {}).get("items", [])
           if row.get("repository_ref") == repository_ref]


def _split_graph(graph: Mapping[str, Any]) -> tuple[list, dict]:
    """``graph.nodes`` is a dict of kind -> bounded {"items": [...]} buckets
    (repository/module/route/data-store/external-boundary/deployable-unit);
    flattened into ONE array (each row tagged with its own "kind", which the
    per-kind grouping had left implicit) so it can be sharded like any other
    JSON-array input. ``highest_degree_nodes`` stays inside meta: it is
    capped at 100 rows (synthesis_input.py's ``_HUB_LIMIT``), far smaller
    than the combined node list (up to 6 kinds x 200 rows each), so it is
    not a realistic sharding risk on its own -- not blindly restructured.
    ``coverage`` also keeps a per-partition, per-repo count breakdown nested
    inside meta (up to ~200 rows per partition) -- left alone for the same
    reason: aggregate COUNT rows only (no evidence/attrs payload per row
    the way a node carries), so its worst case stays well under a node
    list's, even at comparable row counts."""
    nodes_by_kind = graph.get("nodes", {}) or {}
    flattened = []
    nodes_summary = {}
    for kind in sorted(nodes_by_kind):
        bucket = nodes_by_kind[kind] or {}
        for row in bucket.get("items", []):
            flattened.append({**row, "kind": kind})
        nodes_summary[kind] = {key: bucket.get(key) for key in
                               ("total_count", "included_count", "truncated")}
    meta = {
        "stats": graph.get("stats", {}),
        "coverage": graph.get("coverage", {}),
        "edges_by_type_and_status": graph.get("edges_by_type_and_status", {}),
        "nodes_summary": nodes_summary,
        "highest_degree_nodes": graph.get("highest_degree_nodes", {}),
    }
    return flattened, meta


def _split_route_inventory(route_inventory: Mapping[str, Any]) -> tuple[list, dict]:
    rows = route_inventory.get("rows", {}) or {}
    meta = {key: rows.get(key) for key in ("total_count", "included_count", "truncated")}
    meta["notes"] = route_inventory.get("notes", [])
    return list(rows.get("items", [])), meta


def _split_ui_route_linkage(ui_route_linkage: Mapping[str, Any]) -> tuple[list, dict]:
    rows = ui_route_linkage.get("rows", {}) or {}
    meta = {
        "frontends": ui_route_linkage.get("frontends", []),
        "calls_by_frontend_repository": ui_route_linkage.get("calls_by_frontend_repository", {}),
        "notes": ui_route_linkage.get("notes", []),
    }
    meta.update({key: rows.get(key) for key in ("total_count", "included_count", "truncated")})
    return list(rows.get("items", [])), meta


def _split_bounded_list(section: Mapping[str, Any]) -> tuple[list, dict]:
    """A plain ``_bounded(...)``-shaped section (integration_candidates,
    role_catalog_by_repository): ``{"total_count", "included_count",
    "truncated", "items": [...]}`` directly at the top level -- split its
    items out from the small counts."""
    meta = {key: section.get(key) for key in ("total_count", "included_count", "truncated")}
    return list(section.get("items", [])), meta


def _split_capabilities(capabilities_doc: Mapping[str, Any]) -> tuple[list, dict]:
    meta = {key: value for key, value in capabilities_doc.items() if key != "capabilities"}
    return list(capabilities_doc.get("capabilities", [])), meta


# (array_input_name, meta_input_name, split_fn) per EXTRA synthesis-input.json
# section a lens may consume (see _EXTRA_SECTIONS below). Every section here
# has one dominant natural array worth hoisting out; a section not listed
# here (none currently) would stay a single whole-object input instead.
_SECTION_SPLITTERS: dict[str, tuple[str, str, Callable[[Mapping[str, Any]], tuple[list, dict]]]] = {
    "graph": ("graph-nodes.json", "graph-meta.json", _split_graph),
    "route_inventory": ("route-inventory.json", "route-inventory-meta.json",
                        _split_route_inventory),
    "ui_route_linkage": ("ui-route-linkage.json", "ui-route-linkage-meta.json",
                        _split_ui_route_linkage),
    "integration_candidates": ("integration-candidates.json",
                              "integration-candidates-meta.json", _split_bounded_list),
    "role_catalog_by_repository": ("role-catalog-by-repository.json",
                                   "role-catalog-by-repository-meta.json", _split_bounded_list),
    "capabilities": ("capabilities.json", "capabilities-meta.json", _split_capabilities),
}


def _lens_inputs(run: Path, template: tpl.LensTemplate, synthesis_doc: Mapping[str, Any],
                 module_candidates_doc: Mapping[str, Any], run_summary: Mapping[str, Any],
                 repository_ref: str | None) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for row in _matching_signal_rows(run_summary, template.signals, repository_ref):
        view_path = run / "signals" / str(row["view"])
        try:
            content = view_path.read_text("utf-8", errors="replace")
        except OSError as exc:
            raise PlannerError(
                f"signal view listed complete/partial in run-summary.json but "
                f"unreadable: {view_path} ({exc})") from exc
        name = f"view:{row.get('tool', '')}:{row.get('repository_ref', '')}:{row['view']}"
        inputs[name] = content

    candidates, candidates_meta = _split_module_candidates(module_candidates_doc, repository_ref)
    inputs["module-candidates.json"] = json.dumps(candidates, sort_keys=True)
    inputs["module-candidates-meta.json"] = json.dumps(candidates_meta, sort_keys=True)
    inputs["repositories.json"] = json.dumps(
        _trim_repositories(synthesis_doc, repository_ref), sort_keys=True)

    if template.lens_id == "dead-code" and repository_ref is not None:
        inputs["dead-code-graph-route-nodes.json"] = json.dumps(
            _dead_code_route_nodes(synthesis_doc, repository_ref), sort_keys=True)
        inputs["dead-code-route-inventory-rows.json"] = json.dumps(
            _dead_code_route_inventory_rows(synthesis_doc, repository_ref), sort_keys=True)
    else:
        for key in _EXTRA_SECTIONS.get(template.lens_id, ()):
            section = synthesis_doc.get(key)
            if section is None:
                continue
            array_name, meta_name, split = _SECTION_SPLITTERS[key]
            array, meta = split(section)
            inputs[array_name] = json.dumps(array, sort_keys=True)
            inputs[meta_name] = json.dumps(meta, sort_keys=True)
    return inputs


# --------------------------------------------------------------------------- #
# boundary-resolution -- module-formation rules ported verbatim from
# synthesis.md step 4's numbered item 1 (extracted fresh from the live file
# on every call, mirroring rule_gate.py's own re-parse-don't-hand-copy
# pattern, so a future synthesis.md edit can never silently drift from what
# this task actually instructs).
# --------------------------------------------------------------------------- #

_STEP4_ITEM1_START = "1. **Form modules from candidates.**"
_STEP4_ITEM2_START = "2. **Classify** each module:"

BOUNDARY_RESOLUTION_PREAMBLE = (
    "Return a single JSON object matching the boundary-resolution output "
    'schema: {"dispositions": [...]}. One row per candidate_id in the given '
    "candidate universe (module-candidates.json below; the optional "
    "cohesion-bundle.json, when present, groups candidates by an observed "
    "measure -- route-prefix, folder, import, co-change, or table-ownership "
    "-- to help judge when signals genuinely support one boundary), each "
    "with disposition, module_ids, and a short evidence-bounded reason, "
    "following the project-agnostic granularity contract below (ported "
    "verbatim from synthesis.md). Return ONLY this JSON object -- no prose "
    "outside it."
)


def module_formation_rules(source: str | None = None) -> str:
    """synthesis.md step 4's item 1 ("Form modules from candidates.") --
    verbatim, including its granularity-contract bullets -- up to (not
    including) item 2 ("Classify")."""
    text = source if source is not None else synthesis_md_text()
    start = text.index(_STEP4_ITEM1_START)
    end = text.index(_STEP4_ITEM2_START, start)
    return text[start:end].rstrip("\n")


def _boundary_resolution_instructions() -> str:
    return "\n\n".join((BOUNDARY_RESOLUTION_PREAMBLE, module_formation_rules())) + "\n"


def _boundary_resolution_version() -> str:
    return tpl.content_digest(BOUNDARY_RESOLUTION_PREAMBLE, module_formation_rules())


# --------------------------------------------------------------------------- #
# dedup-rank -- dedup/rank rules ported verbatim from synthesis.md step 4.5.
# --------------------------------------------------------------------------- #

_STEP45_START = "## Step 4.5 — dedup systemic findings and rank for change-friction"
_STEP5_START = "## Step 5 — assign findings to final module IDs"

DEDUP_RANK_PREAMBLE = (
    "Return a single JSON object matching the dedup-rank output schema: "
    '{"input_finding_ids": [...], "merge_map": {...}, "rank": [...]}. '
    "input-finding-ids.json below lists every finding_id from every "
    "validated lens task this run produced, across every repo shard and "
    "lens; merge_map must classify EACH one exactly once (surviving or "
    "absorbed); rank orders the surviving findings only, per the rules "
    "below (ported verbatim from synthesis.md). Return ONLY this JSON "
    "object -- no prose outside it."
)


def dedup_rank_rules(source: str | None = None) -> str:
    """synthesis.md step 4.5's two numbered rules -- verbatim, excluding the
    "## Step 4.5" heading line itself (a document label, not a rule)."""
    text = source if source is not None else synthesis_md_text()
    start = text.index(_STEP45_START)
    body_start = text.index("\n", start) + 1
    end = text.index(_STEP5_START, body_start)
    return text[body_start:end].rstrip("\n")


def _dedup_rank_instructions() -> str:
    return "\n\n".join((DEDUP_RANK_PREAMBLE, dedup_rank_rules())) + "\n"


def _dedup_rank_version() -> str:
    return tpl.content_digest(DEDUP_RANK_PREAMBLE, dedup_rank_rules())


# --------------------------------------------------------------------------- #
# plan_judgment
# --------------------------------------------------------------------------- #

def plan_judgment(run_dir: str | Path, *,
                  context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
                  skill_root: str | Path | None = None) -> list[PlannedTask]:
    run = Path(run_dir).expanduser().resolve()
    identities = identity.load(run)
    run_summary = _load_json(run / "signals" / "run-summary.json")
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    lens_templates = tpl.load_lens_templates(skill_root)
    shared_body = tpl.load_shared_body(skill_root)

    packets: list[TaskPacket] = []
    planned: list[PlannedTask] = []

    for lens_id in sorted(lens_templates):
        template = lens_templates[lens_id]
        instructions = tpl.render_instructions(template, shared_body)
        template_id = f"lens-{lens_id}"
        repo_refs: list[str | None]
        if template.shard == "repo":
            repo_refs = sorted(repo.reference for repo in identities.repositories)
        else:
            repo_refs = [None]

        for repository_ref in repo_refs:
            inputs = _lens_inputs(run, template, synthesis_doc, module_candidates_doc,
                                  run_summary, repository_ref)
            task_id = (f"lens-{lens_id}-{_repo_fragment(repository_ref)}"
                      if repository_ref is not None else f"lens-{lens_id}")
            built = compose(
                task_id=task_id, template_id=template_id, template_version=template.version,
                task_type="lens-findings", instructions=instructions, inputs=inputs,
                output_schema_id=tpl.LENS_OUTPUT_SCHEMA_ID,
                context_budget_tokens=context_budget_tokens)
            packets.extend(built)
            planned.append(PlannedTask(
                task_id=task_id, task_type="lens-findings", lens_id=lens_id,
                shard=template.shard, repository_ref=repository_ref or "",
                packet_ids=tuple(packet.task_id for packet in built),
                estimated_tokens=sum(_packet_tokens(packet) for packet in built),
                created=False))

    # boundary-resolution: one global, INDEPENDENT task (no depends_on) --
    # runs in parallel with every lens task above. It carries the FULL
    # candidate universe (repository_ref=None -> no per-repo trim) -- the
    # single biggest input in the whole DAG, so it gets the same
    # array/meta split every lens task's own candidates input gets above.
    all_candidates, all_candidates_meta = _split_module_candidates(module_candidates_doc, None)
    boundary_inputs = {
        "module-candidates.json": json.dumps(all_candidates, sort_keys=True),
        "module-candidates-meta.json": json.dumps(all_candidates_meta, sort_keys=True),
    }
    cohesion_path = run / "cohesion-bundle.json"
    if cohesion_path.is_file():
        boundary_inputs["cohesion-bundle.json"] = cohesion_path.read_text("utf-8")
    built = compose(
        task_id="boundary-resolution", template_id="boundary-resolution",
        template_version=_boundary_resolution_version(), task_type="boundary-resolution",
        instructions=_boundary_resolution_instructions(), inputs=boundary_inputs,
        output_schema_id="boundary-resolution.v1",
        context_budget_tokens=context_budget_tokens)
    packets.extend(built)
    planned.append(PlannedTask(
        task_id="boundary-resolution", task_type="boundary-resolution", lens_id="",
        shard="", repository_ref="",
        packet_ids=tuple(packet.task_id for packet in built),
        estimated_tokens=sum(_packet_tokens(packet) for packet in built), created=False))

    created_ids = set(Engine(run).create_tasks(packets))
    return [replace(task, created=any(pid in created_ids for pid in task.packet_ids))
           for task in planned]


# --------------------------------------------------------------------------- #
# plan_dedup
# --------------------------------------------------------------------------- #

def _pending_lens_task_ids(run: Path) -> set[str]:
    """Every ``lens-findings`` task_id this run's ledger has ever CREATED
    that has not (yet) reached a TERMINAL state ("validated" or "failed") --
    i.e. still claimed/outstanding or never claimed at all. A permanently
    FAILED lens task does not block ``plan_dedup``: dedup-rank proceeds with
    whatever DID validate, on the same fail-open-and-disclose footing as
    every lens's own coverage-honesty rule (that shard's absence is a gap
    for a later stage to surface, not a reason to block every other lens's
    findings from being merged)."""
    engine = Engine(run)
    if not engine.ledger_exists():
        return set()
    records = engine._read_records()
    states = engine.task_states()
    lens_task_ids = {record.task_id for record in records
                     if record.event == "created"
                     and record.detail["task"].get("task_type") == "lens-findings"}
    return {task_id for task_id in lens_task_ids if states.get(task_id) == "pending"}


def plan_dedup(run_dir: str | Path, *,
               context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS) -> PlannedTask:
    run = Path(run_dir).expanduser().resolve()
    engine = Engine(run)

    pending = _pending_lens_task_ids(run)
    if pending:
        raise PlannerError(
            "lens-findings task(s) still pending (not yet validated or "
            "permanently failed) -- wait for them before calling plan-dedup: "
            + ", ".join(sorted(pending)))

    lens_outputs = validated_outputs(run, task_type="lens-findings")
    if not lens_outputs:
        raise PlannerError(
            "no validated lens-findings task found -- run plan-judgment and "
            "its executor(s) to completion before plan-dedup")

    findings: list[dict[str, Any]] = []
    produced_by: dict[str, str] = {}
    for task_id in sorted(lens_outputs):
        for row in lens_outputs[task_id].get("findings", []):
            finding_id = row.get("finding_id")
            if finding_id in produced_by:
                raise PlannerError(
                    f"finding_id {finding_id!r} was produced by both "
                    f"{produced_by[finding_id]!r} and {task_id!r} -- lens "
                    "finding_ids must be globally unique across this run")
            produced_by[finding_id] = task_id
            findings.append(row)
    if not findings:
        raise PlannerError(
            "every validated lens-findings task produced zero findings -- "
            "nothing for dedup-rank to merge")

    findings.sort(key=lambda row: row["finding_id"])
    finding_ids = [row["finding_id"] for row in findings]
    inputs = {
        "input-finding-ids.json": json.dumps(finding_ids, sort_keys=True),
        "findings.json": json.dumps(findings, sort_keys=True),
    }
    built = compose(
        task_id="dedup-rank", template_id="dedup-rank",
        template_version=_dedup_rank_version(), task_type="dedup-rank",
        instructions=_dedup_rank_instructions(), inputs=inputs,
        output_schema_id="dedup-rank.v1", context_budget_tokens=context_budget_tokens,
        depends_on=tuple(sorted(lens_outputs)))
    created_ids = set(engine.create_tasks(built))
    return PlannedTask(
        task_id="dedup-rank", task_type="dedup-rank", lens_id="", shard="",
        repository_ref="", packet_ids=tuple(packet.task_id for packet in built),
        estimated_tokens=sum(_packet_tokens(packet) for packet in built),
        created=any(pid in created_ids for pid in (packet.task_id for packet in built)))
