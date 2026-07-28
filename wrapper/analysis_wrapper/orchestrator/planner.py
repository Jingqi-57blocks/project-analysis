"""Judgment-DAG planner for the orchestrator (57B-113 / 57B-116, M2).

Four verbs, four CLI subcommands (``plan-judgment``, ``plan-dedup``,
``plan-lens-finalize``, and ``fetch-selections`` lives in the sibling
``selection.py`` module):

- ``plan_judgment`` composes and registers, for a PREPARED run directory
  (``targets.json`` + ``signals/run-summary.json`` + ``synthesis-input.json``
  + ``module-candidates.json`` already written by ``prepare-overview``): one
  ``lens-findings`` task per (repo-sharded lens x repo) + one per
  workspace-sharded lens, PLUS one independent ``formation-proposal`` task
  (task_id ``"formation"``; no ``depends_on`` -- it runs in parallel with
  every lens task). Its packet consumes the full candidate universe plus an
  OPTIONAL deterministic ``cohesion-bundle.json`` when a sibling workstream
  has already written one into the run dir; a still-valid packet is composed
  without it. Its validated output is the complete module-map.json shape
  (``modules`` + ``candidate_rules``/``candidate_dispositions`` +
  ``additional_candidates``) -- a sibling ``formation.py`` module
  materializes it at module-map.json's canonical path; the existing
  ``finalize-module-map`` command (unchanged) then expands/validates it.
  This task type is ``formation-proposal``, NOT ``boundary-resolution``:
  the latter's M0 output schema validates only a bare ``dispositions``
  list, which alone cannot produce module-map.json's required ``modules``
  rows (module_id/name/classification/confidence/aliases).
  ``boundary-resolution`` stays a defined task type in contracts.py/
  schemas.py (a distinct, real shape another caller may still compose) --
  this planner simply does not use it.

  For a lens whose frontmatter sets ``source_reads: true`` (structure-
  inventory, dependencies-cycles, safety-net, open-lens -- see each lens
  file's own frontmatter comment for why), ``plan_judgment`` composes ONLY
  its paired ``selection-fetch`` task (task_id ``<lens-task-id>-select``,
  same inputs the lens task itself would get) -- NOT the lens-findings task
  directly. That select task's own job is to REQUEST up to that lens's own
  ``max_selections`` source locations (``quoted_text`` left empty,
  schemas.py's request state; default 12, overridable per lens -- see
  ``LensTemplate.max_selections``) the lens needs read in full to verify a
  fact its bounded signal views only hint at. ``fetch-selections``
  (selection.py) then fetches bounded, revision-checked, sanitized excerpts
  for a validated select task, enforcing that SAME per-lens cap, and
  ``plan_lens_finalize`` (below) composes the REAL lens-findings task from
  the ORIGINAL lens inputs plus that fetched evidence -- mirroring
  ``plan_dedup``'s own two-phase pattern for the identical reason: a
  :class:`~.contracts.TaskPacket`'s inputs must be concrete TEXT at
  creation time, and the fetched evidence does not exist yet when the
  select task is first planned.

  Every lens task ALSO gets, deterministically read straight from the
  workspace at composition time (Part A, 57B-116): for safety-net and
  open-lens specifically, a ``test-ci-evidence.json`` input per repo (test-
  file inventory, CI config file contents, package.json scripts AND
  dependency blocks, go.mod module line) -- see ``_test_ci_evidence_row``'s own docstring. This is
  NOT a signal-sweep tool result; it is read fresh here because no signal
  tool covers it, but it is fully deterministic given a pinned workspace
  state, and citations into it are ordinary ``repo@revision:path:line``
  refs -- the exact grammar (and the exact finalize-findings citation
  check, which already re-reads the real repo file) every other source ref
  already uses, so no new validator is needed for it.

- ``plan_dedup`` is a SEPARATE, LATER call: it reads every VALIDATED
  lens-findings output already in the ledger (``results.validated_outputs``)
  and composes the ONE global ``dedup-rank`` task from their real finding
  ids/content. This cannot happen inside ``plan_judgment``: a
  :class:`~.contracts.TaskPacket`'s inputs must be concrete TEXT at creation
  time, and dedup-rank's whole job is to merge findings that do not exist
  yet when the lens tasks are first planned. The engine's digest-keyed
  generations (``engine.py``) make re-running either verb idempotent.
  It refuses (fails closed) while any lens-findings task is still pending
  -- including a source_reads lens whose select task has validated but
  whose plan_lens_finalize step has not run yet, which would otherwise be
  invisible here (nothing was ever created for it) and silently drop that
  lens's findings from the merge pool.

- ``plan_lens_finalize`` is the second phase of the select/finalize pair
  above: given the ORIGINAL lens task_id plan_judgment would have used (not
  the select task_id), it looks up that lens's own shard/repo via
  ``_lens_task_specs`` (never by parsing the task_id string -- a
  repository_ref's task_id fragment is an unreversible hash suffix, see
  ``_repo_fragment``), requires its select task to have validated and
  fetch-selections to have written its fetched-evidence.json, and composes
  the real lens-findings task from the same inputs the select task got plus
  that fetched evidence and an appended instructions addendum
  (``templates.SOURCE_VERIFIED_ADDENDUM``).

No per-lens or per-shard merge step exists anywhere in this module --
cross-shard AND cross-lens duplication is left entirely to the one global
dedup-rank task (schemas.py already makes its ``merge_map``/``rank`` shape
self-verifiably exactly-once); this module only ever creates INDEPENDENT
lens-findings tasks, never one that depends on another lens's output
(a select task's paired lens task is the one narrow exception: it depends
on ITS OWN select task only, never another lens's).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .. import identity
from ..exclusions import is_excluded_relative
from ..targetspec import RepoTarget, TargetSpec
from . import requirements, templates as tpl
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
    lens_id: str            # "" for formation / dedup-rank
    shard: str               # "repo" | "workspace" | "" for non-lens tasks
    repository_ref: str      # "" for workspace-shard / non-lens tasks
    packet_ids: tuple[str, ...]   # literal ledger task_ids after composer sharding
    estimated_tokens: int         # summed across every packet_id above
    created: bool                 # False = idempotent no-op re-create (unchanged digest)


# --------------------------------------------------------------------------- #
# _LensTaskSpec -- the one place a lens's (lens_id, repository_ref) maps to
# its task_id, shared by plan_judgment and plan_lens_finalize so the two can
# never derive a different task_id for the same pair. A repository_ref's
# task_id fragment is an UNREVERSIBLE hash suffix (see _repo_fragment), so a
# task_id string alone cannot be parsed back into its repository_ref --
# plan_lens_finalize looks it up here instead of parsing.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class _LensTaskSpec:
    lens_id: str
    template: tpl.LensTemplate
    repository_ref: str | None
    task_id: str
    semantic_partition: str = ""


def _lens_task_specs(lens_templates: Mapping[str, tpl.LensTemplate],
                     identities: identity.IdentityMap) -> list[_LensTaskSpec]:
    specs: list[_LensTaskSpec] = []
    for lens_id in sorted(lens_templates):
        template = lens_templates[lens_id]
        # Open-lens used to receive every global universe in one packet and
        # let the generic composer split only its largest input.  Plan stable
        # repository-local partitions plus one compact cross-repository
        # boundary partition instead.  These are semantic responsibilities,
        # not arbitrary token shards, and global dedup still owns overlap.
        if lens_id == "open-lens" and template.shard == "workspace":
            for index, repository_ref in enumerate(
                    sorted(repo.reference for repo in identities.repositories)):
                fragment = _repo_fragment(repository_ref)
                specs.append(_LensTaskSpec(
                    lens_id=lens_id, template=template, repository_ref=repository_ref,
                    # Keep the first stable partition's historical task id so
                    # in-progress runs/tests have a migration path; its packet
                    # is still repository-local, never the old global universe.
                    task_id="lens-open-lens" if index == 0
                    else f"lens-open-lens-partition-{fragment}",
                    semantic_partition=f"repository:{repository_ref}"))
            specs.append(_LensTaskSpec(
                lens_id=lens_id, template=template, repository_ref=None,
                task_id="lens-open-lens-cross-repository",
                semantic_partition="cross-repository"))
            continue
        repo_refs: list[str | None]
        if template.shard == "repo":
            repo_refs = sorted(repo.reference for repo in identities.repositories)
        else:
            repo_refs = [None]
        for repository_ref in repo_refs:
            task_id = (f"lens-{lens_id}-{_repo_fragment(repository_ref)}"
                      if repository_ref is not None else f"lens-{lens_id}")
            specs.append(_LensTaskSpec(lens_id=lens_id, template=template,
                                       repository_ref=repository_ref, task_id=task_id))
    return specs


def _select_task_id(lens_task_id: str) -> str:
    return f"{lens_task_id}-select"


def fetch_selections_output_path(run_dir: str | Path, select_task_id: str) -> Path:
    """The canonical path ``fetch-selections`` (selection.py) writes to and
    ``plan_lens_finalize`` reads from, for one select task --
    ``<run>/tasks/<select_task_id>-fetched-evidence.json``, alongside the
    ledger. Parameterized by select_task_id since multiple select tasks (one
    per source_reads lens x repo-shard) can exist in the same run at once."""
    return (Path(run_dir).expanduser().resolve() / "tasks"
           / f"{select_task_id}-fetched-evidence.json")


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


# --------------------------------------------------------------------------- #
# test/CI evidence (57B-116, Part A): bounded, deterministic evidence read
# straight from the workspace at packet-composition time, for safety-net and
# open-lens ONLY -- the two lenses whose own bodies most directly need it
# ("Assertion quality sampling... cite the specific test files sampled",
# "Type/migration nets... read tsconfig/migration files as data", and open-
# lens's own free-observation mandate). No signal tool covers this; it is
# read fresh HERE rather than through the signal-sweep pipeline because it
# is small, simple, and needs no subprocess/tool-version machinery. It is
# still fully DETERMINISTIC given a pinned workspace state (same git commit/
# dirty-state -> byte-identical evidence), and citations into it are
# ordinary `repo@revision:path:line` refs -- the SAME grammar, and the SAME
# finalize-findings citation check (which already re-reads the real repo
# file), every other source ref already goes through -- no new validator.
# --------------------------------------------------------------------------- #

_TEST_CI_EVIDENCE_LENSES = frozenset({"safety-net", "open-lens"})
_TEST_FILE_CAP = 200
_CI_CONFIG_LINE_CAP = 2000  # per-file safety truncation (mirrors synthesis_input.py's own caps)
_CI_CONFIG_FIXED_RELATIVE_PATHS = ("bitbucket-pipelines.yml", "Jenkinsfile", ".gitlab-ci.yml")


def _is_test_file(relative_posix: str) -> bool:
    """*_test.go, *.test.*, *.spec.*, or anywhere under a test/ or tests/
    directory (matching a "test?(s)/ dirs" glob literally: any path
    component named exactly "test" or "tests", not only the immediate
    parent) -- a file living under such a directory counts regardless of
    its own name."""
    path = PurePosixPath(relative_posix)
    name = path.name
    if name.endswith("_test.go") or ".test." in name or ".spec." in name:
        return True
    return any(part in ("test", "tests") for part in path.parts[:-1])


def _iter_repo_relative_files(target: RepoTarget) -> list[str]:
    """Every file under `target`'s analysis roots, Tier-1/Tier-2 excluded
    dirs skipped (`exclusions.is_excluded_relative` -- the SAME policy
    every signal-producing tool already applies), as paths relative to the
    REPO root (not the analysis root) so they match the `path` half of a
    `repo@revision:path:line` citation exactly."""
    root = Path(target.path).expanduser().resolve()
    found: list[str] = []
    for analysis_root in target.root_paths():
        for candidate in analysis_root.rglob("*"):
            if not candidate.is_file():
                continue
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            if is_excluded_relative(target, relative):
                continue
            found.append(relative)
    return found


def _ci_config_relative_paths(target: RepoTarget) -> list[str]:
    root = Path(target.path).expanduser().resolve()
    found = [rel for rel in _CI_CONFIG_FIXED_RELATIVE_PATHS if (root / rel).is_file()]
    workflows_dir = root / ".github" / "workflows"
    if workflows_dir.is_dir():
        found.extend(f".github/workflows/{path.name}"
                    for path in sorted(workflows_dir.glob("*.yml")))
    return found


def _package_json_evidence(target: RepoTarget) -> dict | None:
    """A repo's declared npm scripts AND its dependency blocks.

    Scripts alone were not enough, and the gap was measurable: a lens asked to
    judge installed-but-unused test tooling, or declared-vs-used dependencies,
    cannot see either without the declarations — it was reasoning about
    packages that were never in its packet, so those findings stayed
    structurally unreachable no matter how the lens prompt was worded
    (57B-116, round-3 acceptance evidence: two losses traced to this, not to
    methodology). Declared dependencies are exactly the committed declarative
    data this evidence input exists to carry, and the blocks are small enough
    to pass whole.
    """
    path = Path(target.path).expanduser().resolve() / "package.json"
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text("utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None

    def block(name: str) -> dict:
        value = doc.get(name)
        return value if isinstance(value, dict) else {}

    return {name: block(name) for name in (
        "scripts", "dependencies", "devDependencies",
        "peerDependencies", "optionalDependencies")}


def _go_mod_module_line(target: RepoTarget) -> str | None:
    path = Path(target.path).expanduser().resolve() / "go.mod"
    if not path.is_file():
        return None
    for line in path.read_text("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            return stripped
    return None


def _test_ci_evidence_row(target: RepoTarget, repository_ref: str) -> dict:
    root = Path(target.path).expanduser().resolve()
    test_paths = sorted({relative for relative in _iter_repo_relative_files(target)
                        if _is_test_file(relative)})
    capped = test_paths[:_TEST_FILE_CAP]
    ci_configs = []
    for rel in _ci_config_relative_paths(target):
        lines = (root / rel).read_text("utf-8", errors="replace").splitlines()
        ci_configs.append({
            "path": rel,
            "content": "\n".join(lines[:_CI_CONFIG_LINE_CAP]),
            "truncated": len(lines) > _CI_CONFIG_LINE_CAP,
        })
    return {
        "repository_ref": repository_ref,
        "test_files": {
            "total_count": len(test_paths), "included_count": len(capped),
            "truncated": len(capped) < len(test_paths), "cap": _TEST_FILE_CAP,
            "paths": capped,
        },
        "ci_configs": ci_configs,
        "package_json": _package_json_evidence(target),
        "go_mod_module": _go_mod_module_line(target),
    }


def _test_ci_evidence_rows(spec: TargetSpec, identities: identity.IdentityMap,
                          ) -> dict[str, dict]:
    """``{repository_ref: row}``, computed ONCE per plan_judgment/
    plan_lens_finalize call regardless of how many source_reads/
    _TEST_CI_EVIDENCE_LENSES tasks need it (safety-net's per-repo shards and
    open-lens's single workspace task all reuse the same rows -- a repo is
    walked at most once)."""
    return {repo.reference: _test_ci_evidence_row(
                spec.repo(identities.internal_id_for(repo.reference)), repo.reference)
           for repo in identities.repositories}


def _lens_inputs(run: Path, template: tpl.LensTemplate, synthesis_doc: Mapping[str, Any],
                 module_candidates_doc: Mapping[str, Any], run_summary: Mapping[str, Any],
                 repository_ref: str | None, *,
                 test_ci_rows: Mapping[str, dict] | None = None,
                 semantic_partition: str = "") -> dict[str, str]:
    inputs: dict[str, str] = {}
    # The cross-repository residual partition consumes only explicit boundary
    # summaries. Repository partitions account for all relevant raw views, so
    # copying the full view universe into this one would be duplicate context.
    signal_rows = [] if semantic_partition == "cross-repository" else _matching_signal_rows(
        run_summary, template.signals, repository_ref)
    for row in signal_rows:
        view_path = run / "signals" / str(row["view"])
        try:
            content = view_path.read_text("utf-8", errors="replace")
        except OSError as exc:
            raise PlannerError(
                f"signal view listed complete/partial in run-summary.json but "
                f"unreadable: {view_path} ({exc})") from exc
        # Named by its own CANONICAL citable path, `signals/<view-file>` --
        # exactly the prefix _shared.md's `signals/<view>:<line>` citation
        # grammar expects. A prior `view:<tool>:<repo>:<view-file>` naming
        # leaked into executors' own citations verbatim (they cite what they
        # see as the input's own name/header): a live run produced refs like
        # `view:lizard:wcp-auth:lizard-wcp-auth.view.txt:13`, which fails
        # citation_grammar_kind (neither "signal" -- it doesn't start with
        # exactly "signals/" -- nor any other recognized grammar). View
        # filenames are already unique per (tool, repository_ref) by
        # construction (executor.py's run_tool names each view
        # "<tool>-<repo-artifact-key>.view.txt"; a run invokes one tool
        # against one repo at most once), so the bare `signals/<view-file>`
        # key can never collide across two different rows in one packet.
        inputs[f"signals/{row['view']}"] = content

    candidates, candidates_meta = _split_module_candidates(module_candidates_doc, repository_ref)
    if semantic_partition == "cross-repository":
        group_counts: dict[tuple[str, str], int] = {}
        for row in module_candidates_doc.get("candidates", []):
            key = (str(row.get("repository_ref", "")), str(row.get("signal_kind", "")))
            group_counts[key] = group_counts.get(key, 0) + 1
        inputs["cross-partition-boundaries.json"] = json.dumps({
            "repository_signal_groups": [
                {"repository_ref": ref, "signal_kind": kind, "candidate_count": count}
                for (ref, kind), count in sorted(group_counts.items())],
            "graph_edge_summary": (synthesis_doc.get("graph") or {}).get("edges_by_type_and_status", {}),
            "route_linkage_summary": (synthesis_doc.get("ui_route_linkage") or {}).get(
                "calls_by_frontend_repository", {}),
        }, sort_keys=True)
        candidates, candidates_meta = [], {"partition": semantic_partition,
                                           "candidate_count": len(module_candidates_doc.get("candidates", [])),
                                           "content": "repository-local partitions carry candidate rows"}
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
            if semantic_partition.startswith("repository:"):
                # Every array-like evidence family has repository-ref rows.
                # Keep only local evidence and disclose the omitted universe
                # through counts/boundary metadata rather than copying it.
                full_count = len(array)
                array = [row for row in array if isinstance(row, dict)
                         and row.get("repository_ref") == repository_ref]
                meta = {"partition": semantic_partition, "included_count": len(array),
                        "omitted_count": full_count - len(array), "boundary": "other repositories omitted"}
            elif semantic_partition == "cross-repository":
                continue
            inputs[array_name] = json.dumps(array, sort_keys=True)
            inputs[meta_name] = json.dumps(meta, sort_keys=True)

    if template.lens_id in _TEST_CI_EVIDENCE_LENSES:
        rows_by_ref = test_ci_rows or {}
        if semantic_partition == "cross-repository":
            selected = []
        elif repository_ref is not None:
            selected = [rows_by_ref[repository_ref]] if repository_ref in rows_by_ref else []
        else:
            selected = [rows_by_ref[ref] for ref in sorted(rows_by_ref)]
        inputs["test-ci-evidence.json"] = json.dumps(selected, sort_keys=True)
    # Describe the bounded evidence immediately above with stable packet ids.
    # Submission validation uses this exact artifact rather than a separate,
    # manually maintained coverage list.
    inputs["requirements.json"] = json.dumps(
        requirements.lens_requirements(template.lens_id, inputs), sort_keys=True)
    if semantic_partition:
        inputs["semantic-partition.json"] = json.dumps({
            "partition_id": semantic_partition,
            "parent_plan": "open-lens",
            "repository_ref": repository_ref or "",
            "included_input_ids": sorted(name for name in inputs if name != "requirements.json"),
        }, sort_keys=True)
        inputs["requirements.json"] = json.dumps(
            requirements.lens_requirements(template.lens_id, inputs), sort_keys=True)
    return inputs


# --------------------------------------------------------------------------- #
# formation-proposal -- module-formation rules ported verbatim from
# synthesis.md step 4's numbered item 1 (extracted fresh from the live file
# on every call, mirroring rule_gate.py's own re-parse-don't-hand-copy
# pattern, so a future synthesis.md edit can never silently drift from what
# this task actually instructs). Deliberately task_type "formation-proposal",
# not "boundary-resolution": the latter's schema validates only a bare
# ``dispositions`` list, which cannot by itself produce module-map.json's
# required ``modules`` rows -- formation-proposal's schema already mirrors
# module-map.json's full shape (see schemas.py's own module docstring).
# --------------------------------------------------------------------------- #

_STEP4_ITEM1_START = "1. **Form modules from candidates.**"
_STEP4_ITEM2_START = "2. **Classify** each module:"

FORMATION_PREAMBLE = (
    "Return a single JSON object matching the formation-proposal output "
    'schema: {"modules": [...], "candidate_rules": [...] OR '
    '"candidate_dispositions": [...], "additional_candidates": [...] '
    "(optional)}. modules: module_id (stable kebab-case slug), name, "
    "classification (business|platform|shared-infra|unresolved), confidence "
    "(high|medium|low), aliases. Disposition EVERY candidate_id in the given "
    "candidate universe (module-candidates.json below; the optional "
    "cohesion-bundle.json, when present, groups candidates by an observed "
    "measure -- route-prefix, folder, import, co-change, or table-ownership "
    "-- to help judge when signals genuinely support one boundary) exactly "
    "once, either via compact candidate_rules (selectors + disposition + "
    "module_ids + reason; at most one final remaining:true rule for an "
    "honest unresolved leftover) or explicit candidate_dispositions rows "
    "(candidate_id, disposition, module_ids, reason). disposition is one of "
    "standalone|merged|platform|shared-infrastructure|excluded|unresolved -- "
    "the first four map to exactly one module_id, the last two to none. An "
    "evidence-backed boundary not surfaced mechanically may be added only "
    "through additional_candidates (a stable mc-added-<slug> id, "
    "repository_ref, value, and at least one citation). Follow the "
    "project-agnostic granularity contract below (ported verbatim from "
    "synthesis.md). Return ONLY this JSON object -- no prose outside it."
)


def module_formation_rules(source: str | None = None) -> str:
    """synthesis.md step 4's item 1 ("Form modules from candidates.") --
    verbatim, including its granularity-contract bullets -- up to (not
    including) item 2 ("Classify")."""
    text = source if source is not None else synthesis_md_text()
    start = text.index(_STEP4_ITEM1_START)
    end = text.index(_STEP4_ITEM2_START, start)
    return text[start:end].rstrip("\n")


def _formation_instructions() -> str:
    return "\n\n".join((FORMATION_PREAMBLE, module_formation_rules())) + "\n"


def _formation_version() -> str:
    return tpl.content_digest(FORMATION_PREAMBLE, module_formation_rules())


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
    target_spec = TargetSpec.load(run / "targets.json")
    test_ci_rows = _test_ci_evidence_rows(target_spec, identities)

    packets: list[TaskPacket] = []
    planned: list[PlannedTask] = []

    for spec in _lens_task_specs(lens_templates, identities):
        template = spec.template
        inputs = _lens_inputs(run, template, synthesis_doc, module_candidates_doc,
                              run_summary, spec.repository_ref, test_ci_rows=test_ci_rows,
                              semantic_partition=spec.semantic_partition)

        if template.source_reads:
            # ONLY the select task is composed here -- the real lens-findings
            # task (same task_id a non-source_reads lens would get directly)
            # is created later by plan_lens_finalize, once fetch-selections
            # has produced real evidence for it. See the module docstring.
            select_task_id = _select_task_id(spec.task_id)
            inputs["selection-requirements.json"] = json.dumps(
                requirements.selection_requirements(spec.lens_id), sort_keys=True)
            select_instructions = tpl.render_selection_instructions(template, shared_body)
            select_version = tpl.content_digest(
                tpl.selection_fetch_preamble(template.max_selections), shared_body,
                template.body_md)
            built = compose(
                task_id=select_task_id, template_id=f"lens-{spec.lens_id}-select",
                template_version=select_version, task_type="selection-fetch",
                instructions=select_instructions, inputs=inputs,
                output_schema_id=tpl.SELECTION_FETCH_OUTPUT_SCHEMA_ID,
                context_budget_tokens=context_budget_tokens)
            packets.extend(built)
            planned.append(PlannedTask(
                task_id=select_task_id, task_type="selection-fetch", lens_id=spec.lens_id,
                shard=template.shard, repository_ref=spec.repository_ref or "",
                packet_ids=tuple(packet.task_id for packet in built),
                estimated_tokens=sum(_packet_tokens(packet) for packet in built),
                created=False))
            continue

        instructions = tpl.render_instructions(template, shared_body)
        built = compose(
            task_id=spec.task_id, template_id=f"lens-{spec.lens_id}",
            template_version=template.version,
            task_type="lens-findings", instructions=instructions, inputs=inputs,
            output_schema_id=tpl.LENS_OUTPUT_SCHEMA_ID,
            context_budget_tokens=context_budget_tokens)
        packets.extend(built)
        planned.append(PlannedTask(
            task_id=spec.task_id, task_type="lens-findings", lens_id=spec.lens_id,
            shard=template.shard, repository_ref=spec.repository_ref or "",
            packet_ids=tuple(packet.task_id for packet in built),
            estimated_tokens=sum(_packet_tokens(packet) for packet in built),
            created=False))

    # formation: one global, INDEPENDENT task (no depends_on) -- runs in
    # parallel with every lens task above. It carries the FULL candidate
    # universe (repository_ref=None -> no per-repo trim) -- the single
    # biggest input in the whole DAG, so it gets the same array/meta split
    # every lens task's own candidates input gets above.
    all_candidates, all_candidates_meta = _split_module_candidates(module_candidates_doc, None)
    formation_inputs = {
        "module-candidates.json": json.dumps(all_candidates, sort_keys=True),
        "module-candidates-meta.json": json.dumps(all_candidates_meta, sort_keys=True),
    }
    cohesion_path = run / "cohesion-bundle.json"
    if cohesion_path.is_file():
        formation_inputs["cohesion-bundle.json"] = cohesion_path.read_text("utf-8")
    built = compose(
        task_id="formation", template_id="formation-proposal",
        template_version=_formation_version(), task_type="formation-proposal",
        instructions=_formation_instructions(), inputs=formation_inputs,
        output_schema_id="formation-proposal.v1",
        context_budget_tokens=context_budget_tokens)
    packets.extend(built)
    planned.append(PlannedTask(
        task_id="formation", task_type="formation-proposal", lens_id="",
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
    findings from being merged).

    A source_reads lens's select task validating does NOT by itself count
    as done: its lens-findings task_id does not exist in the ledger at all
    until ``plan_lens_finalize`` creates it (see the module docstring) --
    without this check, that lens's findings would silently never reach
    ``plan_dedup``'s merge pool simply because nothing was ever CREATED for
    it. Every CREATED ``<...>-select`` task_id whose paired lens-findings
    task_id has no "created" record yet is therefore ALSO reported pending,
    using the select task_id itself as the (informative) placeholder --
    UNLESS the select task itself is permanently FAILED, which gets the
    exact same fail-open treatment as a directly failed lens shard (its
    lens's findings can never be produced without it, so it is excluded
    from the merge pool rather than blocking every other lens forever)."""
    engine = Engine(run)
    if not engine.ledger_exists():
        return set()
    records = engine._read_records()
    states = engine.task_states()
    lens_task_ids = {record.task_id for record in records
                     if record.event == "created"
                     and record.detail["task"].get("task_type") == "lens-findings"}
    pending = {task_id for task_id in lens_task_ids if states.get(task_id) == "pending"}

    created_select_ids = {record.task_id for record in records
                         if record.event == "created"
                         and record.detail["task"].get("task_type") == "selection-fetch"}
    for select_task_id in created_select_ids:
        if not select_task_id.endswith("-select"):
            continue
        lens_task_id = select_task_id[:-len("-select")]
        if lens_task_id in lens_task_ids:
            continue  # already finalized -- its own lens-findings state is checked above
        if states.get(select_task_id) == "failed":
            continue  # permanently failed select -- fail-open, same as a failed lens shard
        pending.add(select_task_id)
    return pending


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


# --------------------------------------------------------------------------- #
# targeted remainders: formation and rekey are explicit judgment work, never
# a log-only tail that later phases are allowed to ignore.
# --------------------------------------------------------------------------- #

_BOUNDARY_RESOLUTION_INSTRUCTIONS = """\
Resolve the explicitly listed unresolved module candidates after first-pass
formation. Return only JSON matching boundary-resolution.v1:
{"modules": [...], "dispositions": [...]}. Disposition every supplied
candidate exactly once. You may add an evidence-supported module, assign a
candidate to an existing/new module, classify it platform/shared-infrastructure/
excluded, or retain it unresolved only with a specific evidence-bounded reason.
Do not rewrite accepted candidates or make a blanket remaining rule.\n"""

_FINDING_RESOLUTION_INSTRUCTIONS = """\
Resolve every finding that could not be re-keyed to a finalized module. Return
only JSON matching finding-resolution.v1: {"dispositions": [...]}. Disposition
every supplied finding exactly once as assigned, duplicate, unsupported, or
unresolved. Assigned rows name existing module ids. Every outcome needs source
or signal evidence. Unresolved is an explicit non-authoritative remainder,
never permission to drop the finding.\n"""


def plan_boundary_resolution(run_dir: str | Path, *,
                             context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS) \
        -> PlannedTask | None:
    """Create the one targeted pass required after any first-pass remainder."""
    from . import formation

    run = Path(run_dir).expanduser().resolve()
    if validated_outputs(run, task_type="boundary-resolution"):
        return None
    unresolved = formation.unresolved_rows(run)
    if not unresolved:
        return None
    document = json.loads((run / "module-map.json").read_text("utf-8"))
    by_group: dict[tuple[str, str], int] = {}
    for row in unresolved:
        key = (str(row.get("repository_ref", "")), str(row.get("signal_kind", "")))
        by_group[key] = by_group.get(key, 0) + 1
    inputs = {
        "unresolved-candidates.json": json.dumps(unresolved, sort_keys=True),
        "module-map.json": json.dumps({"modules": document.get("modules", []),
                                        "candidate_dispositions": document.get("candidate_dispositions", [])},
                                       sort_keys=True),
        "unresolved-groups.json": json.dumps(
            [{"repository_ref": repo, "signal_kind": kind, "candidate_count": count}
             for (repo, kind), count in sorted(by_group.items())], sort_keys=True),
    }
    cohesion = run / "cohesion-bundle.json"
    if cohesion.is_file():
        inputs["cohesion-bundle.json"] = cohesion.read_text("utf-8")
    built = compose(
        task_id="boundary-resolution", template_id="boundary-resolution",
        template_version=tpl.content_digest(_BOUNDARY_RESOLUTION_INSTRUCTIONS),
        task_type="boundary-resolution", instructions=_BOUNDARY_RESOLUTION_INSTRUCTIONS,
        inputs=inputs, output_schema_id="boundary-resolution.v1",
        context_budget_tokens=context_budget_tokens, depends_on=("formation",))
    created_ids = set(Engine(run).create_tasks(built))
    return PlannedTask(task_id="boundary-resolution", task_type="boundary-resolution",
                       lens_id="", shard="", repository_ref="",
                       packet_ids=tuple(packet.task_id for packet in built),
                       estimated_tokens=sum(_packet_tokens(packet) for packet in built),
                       created=any(packet.task_id in created_ids for packet in built))


def plan_finding_resolution(run_dir: str | Path, tail: list[dict], *,
                            context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS) \
        -> PlannedTask | None:
    """Create a bounded disposition task for a non-empty rekey tail."""
    run = Path(run_dir).expanduser().resolve()
    if validated_outputs(run, task_type="finding-resolution"):
        return None
    if not tail:
        return None
    module_doc = json.loads((run / "module-map.json").read_text("utf-8"))
    inputs = {
        "rekey-tail.json": json.dumps(tail, sort_keys=True),
        "module-map.json": json.dumps({"modules": module_doc.get("modules", []),
                                        "candidate_dispositions": module_doc.get("candidate_dispositions", [])},
                                       sort_keys=True),
    }
    built = compose(
        task_id="finding-resolution", template_id="finding-resolution",
        template_version=tpl.content_digest(_FINDING_RESOLUTION_INSTRUCTIONS),
        task_type="finding-resolution", instructions=_FINDING_RESOLUTION_INSTRUCTIONS,
        inputs=inputs, output_schema_id="finding-resolution.v1",
        context_budget_tokens=context_budget_tokens, depends_on=("dedup-rank",))
    created_ids = set(Engine(run).create_tasks(built))
    return PlannedTask(task_id="finding-resolution", task_type="finding-resolution",
                       lens_id="", shard="", repository_ref="",
                       packet_ids=tuple(packet.task_id for packet in built),
                       estimated_tokens=sum(_packet_tokens(packet) for packet in built),
                       created=any(packet.task_id in created_ids for packet in built))


# --------------------------------------------------------------------------- #
# plan_lens_finalize -- phase 2 of the source_reads select/finalize pair
# --------------------------------------------------------------------------- #

def _select_shard_task_ids(run: Path, select_task_id: str) -> list[str]:
    """Every CREATED ledger task_id for this select task's own packet --
    either ``select_task_id`` itself (the composer never needed to shard
    it) or its ``<select_task_id>-shard-<N>`` siblings (it did; see
    composer.py's own sharding), discovered from the ledger rather than
    assumed, sorted by shard number. Empty when nothing was ever created
    for this select task_id at all."""
    engine = Engine(run)
    if not engine.ledger_exists():
        return []
    created_ids = {record.task_id for record in engine._read_records()
                   if record.event == "created"}
    if select_task_id in created_ids:
        return [select_task_id]
    prefix = f"{select_task_id}-shard-"
    shard_ids = [task_id for task_id in created_ids if task_id.startswith(prefix)
                and task_id[len(prefix):].isdigit()]
    return sorted(shard_ids, key=lambda task_id: int(task_id[len(prefix):]))


def plan_lens_finalize(run_dir: str | Path, lens_task_id: str, *,
                       context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
                       skill_root: str | Path | None = None) -> PlannedTask:
    """Composes the REAL lens-findings task for a source_reads lens --
    ``plan_judgment`` created only its paired select task (see the module
    docstring). ``lens_task_id`` is the ORIGINAL lens task_id
    ``plan_judgment`` would have used directly for a non-source_reads lens
    (never the ``-select`` id) -- looked up via ``_lens_task_specs``, not
    parsed, since a repository_ref's task_id fragment is an unreversible
    hash suffix.

    A select task's own packet may itself have been composer-sharded (its
    inputs are the SAME size as the lens task's own, so it is just as
    liable to need sharding) into ``<select_task_id>-shard-1..K`` --
    ``_select_shard_task_ids`` discovers however many shards actually exist;
    EVERY one must be validated and separately ``fetch-selections``-ed
    (once per shard task_id) before this call proceeds, and their fetched-
    evidence arrays are concatenated in shard order into one
    ``fetched-evidence.json`` input. ``depends_on`` names every real shard
    task_id (never the possibly-fictional bare ``select_task_id`` -- when
    sharded, that id was never itself created, and depends_on must
    reference an existing task_id or ``Engine.create_tasks`` fails closed).

    Fails closed when: ``lens_task_id`` names no known lens task; that lens
    is not source_reads (nothing to finalize -- plan_judgment already
    created it directly); its select task (or any of its shards) has not
    been created or validated yet; or ``fetch-selections`` has not written
    fetched-evidence.json for every shard yet.

    ``context_budget_tokens`` should match whatever was passed to
    ``plan_judgment`` for this same run: the select task's packet size is a
    function of that budget (composer sharding), and a MISMATCHED budget
    here changes this task's own packet composition (a new digest -> a new
    generation) without changing the select task it depends on. The CLI
    default (96000) fits typical runs; the real workspace this fix was
    written against needed 180000 -- pass ``--context-budget`` consistently
    across every ``plan-judgment``/``plan-lens-finalize`` call in one run.
    """
    run = Path(run_dir).expanduser().resolve()
    identities = identity.load(run)
    lens_templates = tpl.load_lens_templates(skill_root)
    shared_body = tpl.load_shared_body(skill_root)
    specs_by_id = {spec.task_id: spec for spec in _lens_task_specs(lens_templates, identities)}
    spec = specs_by_id.get(lens_task_id)
    if spec is None:
        raise PlannerError(f"unknown lens task_id: {lens_task_id!r}")
    if not spec.template.source_reads:
        raise PlannerError(
            f"{lens_task_id!r} is not a source_reads lens task -- plan_judgment already "
            "created it directly; there is nothing for plan_lens_finalize to do")

    select_task_id = _select_task_id(lens_task_id)
    shard_task_ids = _select_shard_task_ids(run, select_task_id)
    if not shard_task_ids:
        raise PlannerError(
            f"{select_task_id!r} has not been created yet -- run plan-judgment "
            "before plan-lens-finalize")

    select_outputs = validated_outputs(run, task_type="selection-fetch")
    not_validated = [task_id for task_id in shard_task_ids if task_id not in select_outputs]
    if not_validated:
        raise PlannerError(
            f"select task(s) not yet validated: {', '.join(not_validated)} -- run "
            "their executor(s) to completion before plan-lens-finalize")

    fetched_evidence: list[Any] = []
    for task_id in shard_task_ids:
        fetched_path = fetch_selections_output_path(run, task_id)
        if not fetched_path.is_file():
            raise PlannerError(
                f"no fetched evidence at {fetched_path} -- run "
                f"'fetch-selections --run <dir> --task {task_id}' before "
                "plan-lens-finalize")
        try:
            shard_evidence = json.loads(fetched_path.read_text("utf-8"))
        except ValueError as exc:
            raise PlannerError(f"{fetched_path}: invalid JSON: {exc}") from exc
        if not isinstance(shard_evidence, list):
            raise PlannerError(f"{fetched_path} must contain a JSON array")
        fetched_evidence.extend(shard_evidence)

    run_summary = _load_json(run / "signals" / "run-summary.json")
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    target_spec = TargetSpec.load(run / "targets.json")
    test_ci_rows = _test_ci_evidence_rows(target_spec, identities)

    inputs = _lens_inputs(run, spec.template, synthesis_doc, module_candidates_doc,
                          run_summary, spec.repository_ref, test_ci_rows=test_ci_rows,
                          semantic_partition=spec.semantic_partition)
    inputs["fetched-evidence.json"] = json.dumps(fetched_evidence, sort_keys=True)
    # _lens_inputs writes requirements before fetched evidence exists. Rebuild
    # it so the final analysis task must account for the source excerpts too.
    inputs["requirements.json"] = json.dumps(
        requirements.lens_requirements(spec.template.lens_id, inputs), sort_keys=True)
    instructions = tpl.render_instructions(spec.template, shared_body, source_verified=True)

    built = compose(
        task_id=lens_task_id, template_id=f"lens-{spec.lens_id}",
        template_version=spec.template.version, task_type="lens-findings",
        instructions=instructions, inputs=inputs, output_schema_id=tpl.LENS_OUTPUT_SCHEMA_ID,
        context_budget_tokens=context_budget_tokens, depends_on=tuple(shard_task_ids))
    created_ids = set(Engine(run).create_tasks(built))
    return PlannedTask(
        task_id=lens_task_id, task_type="lens-findings", lens_id=spec.lens_id,
        shard=spec.template.shard, repository_ref=spec.repository_ref or "",
        packet_ids=tuple(packet.task_id for packet in built),
        estimated_tokens=sum(_packet_tokens(packet) for packet in built),
        created=any(pid in created_ids for pid in (packet.task_id for packet in built)))
