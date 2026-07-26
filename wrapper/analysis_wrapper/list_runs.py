"""Read-only inventory of prior runs (57B-109).

Runs are immutable and live under the (now external, since 57B-89) data root,
so there was no way to browse them short of poking around on disk. ``list``
walks ``<data-root>/output/<project-key>/{overview,drilldown}/<run-id>`` and
``<data-root>/state/<project-key>/pointers.json`` and reports what it finds.

Strictly read-only, like ``doctor``: this module must never create the data
root or any directory, and must never write anything. It uses
``paths.resolved_data_root()`` (the non-mutating resolver) rather than
``paths.data_root()``/``output_root()``/``state_root()`` — every one of those
mkdirs the data root as a side effect, which ``list`` must not do (a caller
who has never run anything yet must still be able to run ``list`` and see the
"no runs yet" message without a data root springing into existence).

A run directory that is missing its ``run-state.json`` (interrupted before
that write — ``write_stage1`` itself is atomic via a staging dir + rename,
but the later ``run-state.json`` write is a separate step) or whose
``run-state.json`` is corrupt/malformed is reported as ``"unreadable"``
rather than raising: this is an inventory tool, and one bad run directory
must never hide every other (readable) run from the report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import identity, paths
from .lifecycle import Pointers, RunState

SCHEMA_VERSION = "1.0.0"

EXIT_OK = 0
EXIT_INTERNAL_FAILURE = 1

_RUN_KINDS = ("overview", "drilldown")

# Errors tolerated while reading a single run's state: anything on this list
# means "this one run is unreadable", never "crash the whole report".
_UNREADABLE_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError)


def _decode_project_name(project_key: str) -> str:
    """Best-effort human-readable project name from its artifact-safe key."""
    try:
        return identity.decode_artifact_key(project_key)
    except ValueError:
        return project_key


def _iter_child_dirs(root: Path):
    """Sorted, non-dotfile child directories of ``root``; empty if absent or
    unreadable (never raises)."""
    if not root.is_dir():
        return
    try:
        children = sorted(root.iterdir())
    except OSError:
        return
    for entry in children:
        if entry.is_dir() and not entry.name.startswith("."):
            yield entry


def _iter_run_entries(root: Path):
    """Sorted, non-dotfile children of ``root`` -- directories AND plain
    files; empty if absent or unreadable (never raises). Unlike
    ``_iter_child_dirs`` this does NOT filter out non-directory entries: a
    run slot that turns out to be a plain file is exactly the kind of
    surprise this module exists to report, not hide, so it is handed to
    ``_describe_run`` to come back as an ``unreadable`` run instead of
    silently vanishing from the listing."""
    if not root.is_dir():
        return
    try:
        children = sorted(root.iterdir())
    except OSError:
        return
    for entry in children:
        if not entry.name.startswith("."):
            yield entry


def _describe_run(run_dir: Path, kind: str, current: str | None,
                   latest_completed: str | None) -> dict:
    run_id = run_dir.name
    base = {
        "run_id": run_id,
        "kind": kind,
        "date": "",
        "language": "",
        "status": "unreadable",
        "resume_stage": "",
        "is_current": kind == "overview" and run_id == current,
        "is_latest_completed": kind == "overview" and run_id == latest_completed,
        "location": str(run_dir),
        "error": "",
    }
    # The whole body -- not just the RunState.load() call -- runs under this
    # guard. `state_path.is_file()` alone can raise PermissionError (EACCES):
    # on Python 3.11 pathlib only swallows ENOENT-class stat() failures, not
    # permission errors, so a single `chmod 000` run directory used to blow
    # past this function entirely and crash the ENTIRE report (list: internal
    # failure, zero runs shown) instead of degrading just this one run to
    # "unreadable" like every other per-run failure mode here.
    try:
        if not run_dir.is_dir():
            base["error"] = (f"expected a run directory at {run_dir}, found "
                             "a non-directory entry")
            return base
        state_path = run_dir / RunState.FILENAME
        if not state_path.is_file():
            base["error"] = (f"{RunState.FILENAME} missing (partially written "
                             "or interrupted run)")
            return base
        state = RunState.load(run_dir)
        next_stage = state.next_stage()

        if next_stage:
            status, resume_stage = "incomplete", next_stage
        elif state.inspection_only:
            status, resume_stage = "inspection-only", ""
        else:
            status, resume_stage = "complete", ""

        base.update({
            "date": state.analyzed_at,
            "language": state.language,
            "status": status,
            "resume_stage": resume_stage,
        })
        return base
    except _UNREADABLE_ERRORS as exc:
        base["error"] = f"could not read run directory: {exc}"
        return base


def build_report(project: str | None = None) -> dict:
    """Assemble the full inventory. Never mutates the filesystem.

    ``project``, when given, restricts the report to the one project whose
    directory basename (the "project key" shown in the report) matches
    exactly — the same key ``new-drilldown --project`` already accepts.
    An unmatched ``project`` is not an error: it simply yields an empty
    ``projects`` list, exactly like a data root with no runs at all.
    """
    data_root = paths.resolved_data_root()
    output_root = data_root / "output"
    state_root = data_root / "state"

    projects: list[dict] = []
    for project_dir in _iter_child_dirs(output_root):
        if project and project_dir.name != project:
            continue
        pointers = Pointers(state_root / project_dir.name).read()
        current = pointers.get("current")
        latest_completed = pointers.get("latest_completed")

        runs = [
            _describe_run(run_dir, kind, current, latest_completed)
            for kind in _RUN_KINDS
            for run_dir in _iter_run_entries(project_dir / kind)
        ]
        if not runs:
            continue
        runs.sort(key=lambda r: (r["date"], r["run_id"]), reverse=True)
        projects.append({
            "project_key": project_dir.name,
            "project_name": _decode_project_name(project_dir.name),
            "runs": runs,
        })

    projects.sort(key=lambda p: (p["runs"][0]["date"], p["project_key"]), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "data_root": str(data_root),
        "project_filter": project,
        "projects": projects,
    }


def render_human(report: dict) -> str:
    lines: list[str] = []
    if not report["projects"]:
        if report["project_filter"]:
            lines.append(f"no runs found for project {report['project_filter']!r}")
        else:
            lines.append("no runs yet")
        lines.append(f"data root: {report['data_root']}")
        return "\n".join(lines) + "\n"

    lines.append(f"data root: {report['data_root']}")
    lines.append("")
    for project in report["projects"]:
        lines.append(f"[{project['project_name']}] (project key: {project['project_key']})")
        for r in project["runs"]:
            markers = []
            if r["is_current"]:
                markers.append("current")
            if r["is_latest_completed"]:
                markers.append("latest_completed")
            marker_bit = f"  [{', '.join(markers)}]" if markers else ""
            status_bit = (f"incomplete (resume: {r['resume_stage']})"
                          if r["status"] == "incomplete" else r["status"])
            date_bit = r["date"] or "(unknown date)"
            lang_bit = r["language"] or "-"
            # Date width 25: an ISO-8601 timestamp with seconds precision and
            # a UTC/zone offset ("YYYY-MM-DDTHH:MM:SS+HH:MM") is 25 chars --
            # the previous width of 22 misaligned every populated row.
            lines.append(f"  {r['run_id']:<40} {r['kind']:<10} {date_bit:<25} "
                         f"{lang_bit:<6} {status_bit}{marker_bit}")
            lines.append(f"      location: {r['location']}")
            if r["error"]:
                lines.append(f"      note: {r['error']}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def run(project: str | None, *, as_json: bool) -> int:
    """Implements the ``list`` subcommand. Always returns a documented exit
    code; never raises to its caller."""
    try:
        report = build_report(project)
    except Exception as exc:  # pragma: no cover - last-resort guard
        print(f"list: internal failure — {exc!r}", file=sys.stderr)
        return EXIT_INTERNAL_FAILURE

    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_human(report), end="")
    return EXIT_OK
