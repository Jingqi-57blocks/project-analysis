"""Dependency-map emit — bundled providers + coverage assembler (57B-81 PR2).

Production path: :mod:`analysis_wrapper.profiles.providers` wraps the Go and
JS/TS lanes as bundled ``CapabilityProvider``s (``depmap-go`` / ``depmap-js``)
selected by FACET, not by stack strings or manifest sniffing. Each provider
writes the FINAL per-repo/lane map file directly under ``<out>/imports/``
(``<artifact-key>.golist.json`` / ``<artifact-key>.depcruise.json`` — a
repo's Go and JS/TS maps never collide, so there is nothing to merge) plus a
coverage-only fragment under ``<out>/imports/.fragments/``. :func:`assemble`
rolls every fragment's coverage row into ``<out>/imports/depmap-coverage.json``.
This module never branches on a language name itself: "go"/"js" only ever
appear here as DATA read back out of a fragment.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..executor import create_stage_dir, write_new_text
from ..sanitize import sanitize_text
from ..status import Status, aggregate
from .contract import DepMapReport, RepoDepCoverage

_STATUS_MAP = {
    "complete": Status.COMPLETE,
    "partial": Status.PARTIAL,
    "failed": Status.FAILED,
    "unavailable": Status.SKIPPED,
}


# ---------------------------------------------------------------------------
# Coverage assembler — the production path. Map files are already final when
# a provider writes them; only the per-repo/lane coverage doc is merged here
# from the fragments providers wrote. Technology-neutral: "go"/"js" only ever
# appear as DATA inside a fragment, never as a branching literal here.
# ---------------------------------------------------------------------------

def _repo_dep_coverage_from_row(row: dict) -> RepoDepCoverage:
    """Reconstruct a :class:`RepoDepCoverage` from a fragment's
    ``coverage_row`` — contract.py has no ``from_dict`` for this type, so the
    round trip is done here rather than widening the contract."""
    return RepoDepCoverage(
        repository_ref=row["repository_ref"], lane=row["lane"], status=row["status"],
        tool=row.get("tool", ""), tool_version=row.get("tool_version", ""),
        reason=row.get("reason", ""), map_file=row.get("map_file", ""),
        units=row.get("units", 0), warm_cache=row.get("warm_cache", "n/a"),
        notes=row.get("notes", ""),
        reference_counts=dict(row.get("reference_counts") or {}),
    )


def assemble(out_dir: str | Path, scan_date: str) -> DepMapReport:
    """Merge every ``imports/.fragments/*.json`` coverage fragment a provider
    wrote into the run's final ``imports/depmap-coverage.json`` — the map
    files themselves are already final when a provider writes them, so only
    coverage is assembled here. Zero fragments still produce the empty,
    legacy-shaped coverage doc.
    """
    out = Path(out_dir).expanduser().resolve()
    imports_dir = create_stage_dir(out / "imports")
    fragments_dir = imports_dir / ".fragments"
    fragment_paths = sorted(fragments_dir.glob("*.json")) if fragments_dir.is_dir() else []
    coverages = [
        _repo_dep_coverage_from_row(json.loads(path.read_text("utf-8"))["coverage_row"])
        for path in fragment_paths
    ]
    report = DepMapReport(scan_date=scan_date, repos=coverages)
    write_new_text(imports_dir / "depmap-coverage.json",
                   sanitize_text(report.to_json()))
    return report


def aggregate_status(report: DepMapReport) -> Status:
    """Worst per-lane status, for the CLI exit code (empty -> nothing ran)."""
    return aggregate([_STATUS_MAP[c.status] for c in report.repos])
