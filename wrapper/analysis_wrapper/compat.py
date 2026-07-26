"""Upgrade/compatibility mechanism (57B-95).

Compat identity is **code version + artifact-schema version + runtime
contract** — explicitly NOT the absolute install path. The same code
installed at a different path (a fresh ``git clone``, a CLI-managed symlink,
a new version directory) must never be treated as incompatible or stale on
that basis alone. ``run_provenance.analyzer_observation()`` already records
an analyzer ``root`` for humans to read, but ``analyzer_staleness()``
deliberately never compares it (only ``version``/``git_head``/
``dirty_detail``/``source_state_sha256`` participate) — this module follows
that same discipline and never keys any decision on an absolute path either.

Four ideas live here:

1. A **declarative compat matrix** (``COMPAT_MATRIX``) mapping
   (code-version family, artifact-schema family) to a per-object outcome.
2. An **entry-point guard** (``guard_entry``) that refuses a real
   analysis/resume command when the installed runtime has drifted from this
   code's ``tools/manifest.json`` pins, while staying out of the way of
   read-only/informational commands.
3. **Runtime-vs-manifest-pin drift** (``runtime_reconciliation``), reusing
   ``doctor``'s own probing/drift logic rather than duplicating it.
4. An **outcome vocabulary** — ``readable | resumable | migratable |
   unsupported`` — applied per object (a completed run, an incomplete run
   being resumed, a brand-new overview, or a runtime).

v1 never migrates a schema automatically: it detects, guides (an actionable
message pointing at ``setup`` or "mint a new run instead"), and preserves
(nothing here ever rewrites or deletes an existing run directory).
``MIGRATABLE`` is part of the vocabulary for a future version that actually
implements a migration path; no function in this module returns it today.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from . import paths

# --------------------------------------------------------------------------
# Outcome vocabulary
# --------------------------------------------------------------------------

READABLE = "readable"
RESUMABLE = "resumable"
MIGRATABLE = "migratable"  # reserved for a future version; v1 never returns this
UNSUPPORTED = "unsupported"
OUTCOMES = (READABLE, RESUMABLE, MIGRATABLE, UNSUPPORTED)


class RuntimeCompatRefusal(Exception):
    """Raised by ``guard_entry`` when the installed runtime is out of sync
    with this code's manifest pins. Kept distinct from ``ValueError`` so
    ``cli.main`` can give it its own message and exit code instead of the
    generic input-error path."""


class CompatRefusal(Exception):
    """Raised when resuming an incomplete run would mix artifacts produced
    under incompatible artifact-schema families. The only remedy is a new
    run — nothing about the old one is ever rewritten or deleted."""


# --------------------------------------------------------------------------
# Code / artifact-schema family classification
# --------------------------------------------------------------------------

# The artifact-contract family boundary this module currently knows about.
# Context (57B-95): a separate workstream is landing a deliberate artifact
# contract break at 3.0.0 with NO migration by design — old runs are re-run,
# not migrated. When that lands, this tuple gains a THIRD family boundary
# (never a rewrite of the existing rows — "rebuild, never move", same
# discipline as ``paths.RUNTIME_CONTRACT``).
_BREAK_VERSION = (3, 0, 0)

_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)")


def _version_tuple(text: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(text or "")
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def skill_version() -> str:
    """The skill's own code version, read from ``VERSION`` at the skill
    root — resolved by package location (``paths.skill_root()``), never by
    an absolute-path identity check."""
    try:
        text = (paths.skill_root() / "VERSION").read_text("utf-8")
        return text.strip() or "unknown"
    except OSError:
        return "unknown"


def _family(version: tuple[int, int, int]) -> str:
    return "pre-3.0.0" if version < _BREAK_VERSION else "post-3.0.0"


def code_family() -> str:
    """This checkout's code family (pre/post the 3.0.0 artifact-contract
    break) — derived from ``ARTIFACT_CONTRACT_VERSION``, the contract THIS
    CODE actually reads and writes, never from the skill's ``VERSION`` file.

    (57B-95 review FIX 2): ``VERSION`` is a *release* version and moves on
    its own schedule; ``ARTIFACT_CONTRACT_VERSION`` is the umbrella artifact
    identity this module stamps into every fresh run (``compat_stamp``) and
    reads back out of an existing run (``run_schema_family``). Deriving the
    code axis from anything else would let the two drift apart -- exactly
    what happened before this fix: bumping only ``ARTIFACT_CONTRACT_VERSION``
    to a value ``VERSION`` had not caught up to made this code misclassify
    the very runs it had just minted as coming from "newer code". Because
    both this function and ``compat_stamp`` read the same
    ``ARTIFACT_CONTRACT_VERSION`` global, a run minted by this code is
    self-consistent under this code for ANY value of that constant -- see
    ``test_code_family_tracks_artifact_contract_version_not_skill_version``
    and ``test_self_minted_run_is_always_resumable_under_this_code``."""
    return _family(_version_tuple(ARTIFACT_CONTRACT_VERSION))


def artifact_contract_family(value: str | None) -> str:
    """Classify a stamped ``compat.artifact_contract_version`` STRING into a
    family. A run minted before this stamping existed (``value`` is falsy)
    necessarily predates the 3.0.0 break, so it is classified into the same
    family as every other pre-break run — never given its own special case."""
    if not value:
        return "pre-3.0.0"
    return _family(_version_tuple(value))


# The current stamped value (item 4: version stamping). This is DISTINCT from
# any single file's own ``schema_version`` field (e.g.
# ``run_provenance.SCHEMA_VERSION == 1``, ``doctor.SCHEMA_VERSION ==
# "1.0.0"`` — those govern one artifact file's own shape); it is the
# umbrella artifact-contract identity this module uses ONLY for cross-run
# compat decisions.
ARTIFACT_CONTRACT_VERSION = "2.0.0"


def compat_stamp(*, degraded_runtime_notice: str = "") -> dict[str, Any]:
    """The block recorded into a fresh run's ``run-provenance.json`` (see
    ``run_provenance.create_document``) so a LATER run/tool can make the
    compat decision from the artifact itself, without re-deriving it from
    the environment.

    ``degraded_runtime_notice`` (57B-95 review FIX 5): pass the non-empty
    string ``guard_entry`` returned when ``ACCEPT_DEGRADED_RUNTIME_ENV``
    actually suppressed a detected drift for THIS run's mint -- it is
    recorded verbatim so the run's own provenance discloses that it was
    produced under an accepted-degraded runtime, rather than being
    forensically indistinguishable from a clean one. Empty (the default)
    when no bypass occurred; the key is omitted entirely in that case."""
    stamp: dict[str, Any] = {
        "skill_version": skill_version(),
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "runtime_contract": paths.RUNTIME_CONTRACT,
    }
    if degraded_runtime_notice:
        stamp["degraded_runtime_accepted"] = degraded_runtime_notice
    return stamp


# --------------------------------------------------------------------------
# Declarative compat matrix
# --------------------------------------------------------------------------

# Each row is one (code_family, schema_family) combination with a per-object
# outcome. ``new_overview`` is READABLE/never-blocked in every row — not
# because anything here conditionally computes it, but because a fresh
# overview never consults another run's schema before minting itself (see
# ``new_overview_outcome`` below); the column is kept for a uniform,
# one-glance table rather than special-cased away.
COMPAT_MATRIX: tuple[dict[str, Any], ...] = (
    {
        "code_family": "pre-3.0.0", "schema_family": "pre-3.0.0",
        "completed_run": READABLE, "incomplete_run": RESUMABLE,
        "new_overview": READABLE,
        "note": "same artifact-contract family; resume proceeds normally",
    },
    {
        "code_family": "post-3.0.0", "schema_family": "pre-3.0.0",
        "completed_run": READABLE, "incomplete_run": UNSUPPORTED,
        "new_overview": READABLE,
        "note": (
            "3.0.0 artifact-contract break: NO migration by design. "
            "Completed reports made under the old contract remain readable "
            "as files. An incomplete run from before the break refuses "
            "resume — resuming would combine artifacts produced under "
            "different schemas — so mint a new overview instead; a new "
            "overview is never blocked by an old run's presence."
        ),
    },
    {
        "code_family": "post-3.0.0", "schema_family": "post-3.0.0",
        "completed_run": READABLE, "incomplete_run": RESUMABLE,
        "new_overview": READABLE,
        "note": "same artifact-contract family (post-break); resume proceeds normally",
    },
    {
        "code_family": "pre-3.0.0", "schema_family": "post-3.0.0",
        # (57B-95 review FIX 4): a completed run's FILES are always readable
        # as files -- nothing in this module ever rewrites or deletes a
        # finished run, regardless of which code family produced it. This
        # row used to say UNSUPPORTED here while ``completed_run_outcome``
        # unconditionally returned READABLE -- outcome and note contradicted
        # each other. READABLE is the single source of truth (matching
        # ``completed_run_outcome``); "forward compatibility is not
        # promised" below describes RENDERING/EXPORTING the run with older
        # code, which this module does not attempt to distinguish in v1 --
        # a future version that needs to gate export/render specifically
        # would introduce a distinct outcome for that, not repurpose this
        # column.
        "completed_run": READABLE, "incomplete_run": UNSUPPORTED,
        "new_overview": READABLE,
        "note": (
            "run was produced by NEWER code than this checkout; forward "
            "compatibility is not promised for resuming or re-processing "
            "it, but its already-written files remain readable as files"
        ),
    },
)


def matrix_row(code_fam: str, schema_fam: str) -> dict[str, Any]:
    for row in COMPAT_MATRIX:
        if row["code_family"] == code_fam and row["schema_family"] == schema_fam:
            return row
    # Unreachable today (every combination of the two known families is
    # listed above); a future third family that forgets to add its rows
    # fails conservatively rather than guessing an outcome.
    return {
        "code_family": code_fam, "schema_family": schema_fam,
        "completed_run": UNSUPPORTED, "incomplete_run": UNSUPPORTED,
        "new_overview": READABLE,
        "note": "no matrix rule for this combination; treated conservatively",
    }


# --------------------------------------------------------------------------
# Per-run outcome decisions
# --------------------------------------------------------------------------

def run_schema_family(run_dir: str | Path) -> str:
    """The artifact-contract family recorded in ``run_dir``'s provenance, or
    the pre-3.0.0 family for a run that predates this stamping (or lacks
    ``run-provenance.json`` entirely) — never derived from the run's
    absolute path."""
    from . import run_provenance  # lazy: avoid a run_provenance <-> compat import cycle
    try:
        document = run_provenance.load(run_dir)
    except (OSError, ValueError):
        return "pre-3.0.0"
    block = document.get("compat")
    value = block.get("artifact_contract_version") if isinstance(block, dict) else None
    return artifact_contract_family(value)


def completed_run_outcome(run_dir: str | Path) -> tuple[str, str]:
    """A completed run's reports are ALWAYS readable — a filesystem
    invariant (nothing here ever rewrites or deletes a finished run), not a
    matrix-conditioned decision. The matrix is still consulted for an
    explanatory note (e.g. "produced by newer code").

    (57B-95 review FIX 4): this is the chosen source of truth — every
    ``COMPAT_MATRIX`` row's ``completed_run`` column is READABLE to match,
    including the pre-3.0.0-code/post-3.0.0-schema row that used to say
    UNSUPPORTED there while this function unconditionally returned READABLE
    anyway. "Readable" means the run's already-written files stay on disk
    and can be opened/exported; it does NOT promise this code can RESUME or
    re-process the run under a different schema family — that is
    ``resume_outcome``'s job, and ``guard_run`` (see below) is what actually
    refuses further mutation of an incompatible run."""
    row = matrix_row(code_family(), run_schema_family(run_dir))
    return READABLE, row["note"]


def resume_outcome(run_dir: str | Path) -> tuple[str, str]:
    """Outcome for RESUMING an incomplete run: RESUMABLE when its schema
    family matches this code's current family, UNSUPPORTED (refuse)
    otherwise. Never MIGRATABLE in v1."""
    row = matrix_row(code_family(), run_schema_family(run_dir))
    return row["incomplete_run"], row["note"]


def refuse_incompatible_resume(run_dir: str | Path) -> None:
    """Raise ``CompatRefusal`` when resuming ``run_dir`` would mix artifacts
    produced under incompatible schema families. Callers should invoke this
    before advancing any EXISTING run's stages; a brand-new overview never
    needs to call it at all (see ``new_overview_outcome``)."""
    schema_fam = run_schema_family(run_dir)
    outcome, note = resume_outcome(run_dir)
    if outcome != RESUMABLE:
        raise CompatRefusal(
            f"run {Path(run_dir).name!r} cannot be resumed: it was produced "
            f"under the {schema_fam} artifact-contract family, but this code "
            f"expects {code_family()} ({note}; outcome: {outcome}). Mint a "
            "NEW overview run instead — nothing in the old run is ever "
            "rewritten or deleted."
        )


def guard_run(run_dir: str | Path) -> None:
    """The companion to ``guard_entry`` (57B-95 review FIX 1): refuse to
    ADVANCE, RESUME, or otherwise re-enter an EXISTING run whose stamped
    artifact-schema family is incompatible with this code's family.

    ``guard_entry`` only ever checks the installed RUNTIME against this
    code's manifest pins — it has no idea which run directory (if any) a
    gated command is about to write more into, so it can never catch the
    "resuming an old-schema run under new code would mix two artifact
    contracts in one run directory" hazard. That is what this function is
    for. Wire it into every command that reads an EXISTING run's artifacts
    and writes more into that same run directory: ``mark-stage``,
    ``rollback``, ``prepare-overview``, ``finalize-findings``,
    ``finalize-module-map``, ``audit-overview``, ``system-model``, and the
    top-level ``callgraph``/``dependency-map`` subcommands when they layer
    into an already-discovered run dir (``executor.use_existing_run_directory``).
    Never wire it into ``export``/``status``/``compare-runs``/``accept`` —
    those only read a run (or, for ``accept``, only ever apply to an already
    COMPLETE run and never write into the run directory itself; see
    ``completed_run_outcome``, which is always READABLE) and must stay
    reachable even for an incompatible run. Never wire it into ``new-run``/
    ``new-drilldown`` either — minting a run never consults another run's
    schema (``new_overview_outcome``).

    Deliberately family-only (does not special-case an already-complete run
    the way ``completed_run_outcome`` does): ``rollback`` can reopen a
    completed run for further mutation, which is exactly the same
    schema-mixing hazard as resuming an incomplete one, so the check applies
    uniformly regardless of the run's CURRENT stage state."""
    refuse_incompatible_resume(run_dir)


def new_overview_outcome() -> tuple[str, str]:
    """A new overview is never blocked by any existing run, compatible or
    not — minting one never consults another run's schema at all. A real
    function (not just a docstring claim) so tests can assert against it
    directly."""
    return READABLE, "a new overview never inspects prior runs before minting"


# --------------------------------------------------------------------------
# Runtime-vs-manifest-pin drift (item 3)
# --------------------------------------------------------------------------

def runtime_reconciliation(workspace: str | Path | None = None) -> dict[str, Any]:
    """Installed venv/node_tools/go-tool versions vs ``tools/manifest.json``
    pins, computed OFFLINE by reusing ``doctor``'s own probing/drift logic
    (never duplicated here) via ``doctor.build_report``/``doctor.read_manifest``.
    Only PINNED tools are probed at all (57B-95 review FIX 6: restricts
    ``doctor.build_report``'s subprocess probing to just this handful of
    analyzer-managed tools instead of the full manifest, cutting the cost of
    every gated CLI invocation roughly in half without touching a persistent
    cache — see ``guard_entry``'s docstring for why no on-disk cache is used).

    Returns ``{"rows": [...], "any_drift": bool, "partial_install": bool}``.
    An unpinned tool (e.g. ``ast-grep``) is expected to drift and already
    discloses that in its own ``doctor`` report line, so it never appears
    here at all.

    (57B-95 review FIX 3): "nothing installed yet" (every pinned tool
    absent) is the ordinary pre-``setup`` path and must never be refused —
    but SOME pinned tools present while OTHERS are absent (``partial_install``)
    is a half-reconciled runtime: new code needs a pinned tool this
    checkout's ``setup`` has never provisioned, which is exactly the
    upgrade-compat hazard this guard exists to catch, even though no single
    tool's OWN version disagrees with its pin. Both a present-and-mismatched
    tool and an absent tool during a partial install count as drift; only
    the "every pinned tool absent" shape is exempt.
    """
    from . import doctor as doctor_mod

    manifest_tools = {tool["id"]: tool for tool in doctor_mod.read_manifest()["tools"]}
    pinned_ids = frozenset(
        tool_id for tool_id, tool in manifest_tools.items() if tool.get("pinned"))
    report = doctor_mod.build_report(workspace, tool_ids=pinned_ids)
    rows: list[dict[str, Any]] = []
    for tool in report["tools"]:
        manifest_tool = manifest_tools.get(tool["id"], {})
        if not manifest_tool.get("pinned"):
            continue
        rows.append({
            "id": tool["id"],
            "name": tool["name"],
            "present": tool["state"] == "present",
            "installed_version": tool["detected_version"],
            "manifest_pin": manifest_tool.get("validated_version"),
            "reconcile_action": manifest_tool.get("reconcile", ""),
            "drift": tool["drift"],
        })
    present_rows = [row for row in rows if row["present"]]
    absent_rows = [row for row in rows if not row["present"]]
    partial_install = bool(present_rows) and bool(absent_rows)
    if partial_install:
        for row in absent_rows:
            row["drift"] = (
                "drift: pinned tool absent while other pinned tools are "
                f"already installed (code expects {row['manifest_pin']})"
            )
    any_drift = any(row["drift"] for row in rows)
    return {"rows": rows, "any_drift": any_drift, "partial_install": partial_install}


def runtime_outcome(workspace: str | Path | None = None) -> tuple[str, str]:
    """``"reconcile"`` when a pinned, PRESENT tool's version has drifted
    from the manifest (an upgraded checkout whose runtime was never
    rebuilt), or when the runtime is a PARTIAL install (see
    ``runtime_reconciliation``); ``"ok"`` otherwise. "Accept-as-degraded" is
    never auto-detected here — it is an explicit, opt-in override (see
    ``ACCEPT_DEGRADED_RUNTIME_ENV``), not a state this function infers."""
    report = runtime_reconciliation(workspace)
    if not report["any_drift"]:
        return "ok", ""
    drifted = [row for row in report["rows"] if row["drift"]]
    detail = "; ".join(
        f"{row['id']}: installed {row['installed_version'] or '?'}, "
        f"code expects {row['manifest_pin']}" for row in drifted)
    return "reconcile", detail


# --------------------------------------------------------------------------
# Entry-point guard (item 2)
# --------------------------------------------------------------------------

# Explicit per-command classification: GATED (refused when the runtime has
# drifted -- it executes analysis, or creates/advances/mutates a run) vs. not
# gated (read-only/informational, or the remedy this very guard's refusal
# message points at). Every subcommand registered in ``cli.parser()`` MUST
# have an entry here -- ``test_every_subcommand_is_classified`` in
# test_compat.py fails the moment a new subcommand is added without one, so a
# repeat of the original bug (a deny-all-but-four exempt list that silently
# swallowed every future command) cannot happen unnoticed again.
#
# ``--version`` needs no entry: argparse's ``action="version"`` exits inside
# ``parser().parse_args()``, before ``cli.main`` ever reaches this guard.
COMMAND_CLASSIFICATION: dict[str, bool] = {
    # --- Never gated: read-only/informational, or the remedy path itself ---
    "doctor": False,        # diagnoses the very drift this guard reports -- must stay reachable
    "setup": False,         # THE remedy this guard's own refusal message points at; gating it would make drift unrecoverable
    "migrate": False,       # one-time data-root relocation, not an analysis run
    "list": False,          # informational, read-only run inventory (57B-109) -- never gated
    "help": False,          # informational, read-only command tour (57B-120) -- never gated
    "status": False,        # read-only inspection of an existing run's stage/staleness (never mutates)
    "export": False,        # renders an EXISTING (possibly completed) run's artifacts to files -- the compat matrix promises a completed run's reports stay `readable`
    "compare-runs": False,  # read-only diff between two existing runs' artifacts

    # --- Gated: executes analysis, or creates/advances/mutates a run ---
    "new-run": True,             # mints a new run
    "new-drilldown": True,       # mints a new drilldown run
    "prepare-overview": True,    # advances a run toward an overview
    "discover": True,            # executes repo discovery
    "callgraph": True,           # executes the callgraph analysis tool
    "dependency-map": True,      # executes the dependency-map analysis tool
    "system-model": True,        # builds/mutates the system-model artifact
    "finalize-findings": True,   # mutates a run's findings stage
    "finalize-module-map": True, # mutates a run's module-map stage
    "audit-overview": True,      # mutates a run's overview-audit stage
    "mark-stage": True,          # advances a run's recorded stage checkpoint
    "rollback": True,            # reopens/mutates a run's recorded stages
    "accept": True,              # mutates the "current" pointer
    "run": True,                 # executes one analysis tool
    "sweep": True,               # executes every applicable validated tool
}

GATED_COMMANDS = frozenset(
    cmd for cmd, gated in COMMAND_CLASSIFICATION.items() if gated)

ACCEPT_DEGRADED_RUNTIME_ENV = "PROJECT_ANALYSIS_ACCEPT_DEGRADED_RUNTIME"


def _degraded_runtime_env_accepted() -> bool:
    return os.environ.get(
        ACCEPT_DEGRADED_RUNTIME_ENV, "").strip().lower() in ("1", "true", "yes")


def guard_entry(command: str, *, workspace: str | Path | None = None) -> str:
    """Refuse ``command`` when the installed runtime has drifted from this
    code's manifest pins, UNLESS ``command`` is not gated
    (``COMMAND_CLASSIFICATION``) or the user has explicitly opted into
    running in a degraded state (``ACCEPT_DEGRADED_RUNTIME_ENV``). Cheap and
    offline: it only reuses ``doctor``'s already-offline, no-network probing,
    and does no heavy work of its own; stays quiet whenever everything
    matches. An unclassified command (should be unreachable once
    ``test_every_subcommand_is_classified`` passes) defaults to GATED --
    fail closed rather than silently exempt.

    Returns the drift ``detail`` string when the accept-degraded env var
    actually suppressed a DETECTED drift this call, or ``""`` otherwise
    (nothing gated, nothing drifted, or the command isn't gated at all).
    (57B-95 review FIX 5): the old ordering checked the env var BEFORE
    computing whether there was anything to suppress, so an inherited
    ``PROJECT_ANALYSIS_ACCEPT_DEGRADED_RUNTIME=1`` (a shell profile, CI env)
    silently no-opped this guard on every gated call -- including the
    overwhelmingly common case where the runtime was already fine and there
    was nothing to accept. The drift is now always computed first; the env
    var is consulted ONLY as a last resort once refusal is actually on the
    table, a one-line stderr warning is printed so the bypass is visible in
    the terminal, and the caller can pass the returned detail into
    ``run_provenance.create_document(..., degraded_runtime_notice=...)`` so a
    freshly minted run's ``compat`` stamp records that it was produced under
    an accepted-degraded runtime -- forensically visible later, instead of
    being indistinguishable from a clean run."""
    if not COMMAND_CLASSIFICATION.get(command, True):
        return ""
    outcome, detail = runtime_outcome(workspace)
    if outcome == "ok":
        return ""
    if _degraded_runtime_env_accepted():
        print(
            "wrapper warning: proceeding with a degraded/out-of-sync runtime "
            f"({detail}) -- accepted via {ACCEPT_DEGRADED_RUNTIME_ENV}=1",
            file=sys.stderr,
        )
        return detail
    raise RuntimeCompatRefusal(
        "the installed runtime is out of sync with this code version "
        f"({detail}). Run `setup` to reconcile it, or re-run with "
        f"{ACCEPT_DEGRADED_RUNTIME_ENV}=1 to explicitly accept running "
        "in a degraded state."
    )
