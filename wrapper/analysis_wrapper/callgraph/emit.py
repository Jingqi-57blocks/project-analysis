"""Call-graph emit — bundled providers + fragment assembler (57B-81 PR2).

Production path: :mod:`analysis_wrapper.profiles.providers` wraps the Go and
JS/TS lanes as bundled ``CapabilityProvider``s (``callgraph-go`` /
``callgraph-js``) selected by FACET, not by stack strings or manifest
sniffing. Each provider writes one per-repo/lane FRAGMENT under
``<out>/callgraph/.fragments/<artifact-key>.<lane>.json`` (a coverage row +
that lane's edges); :func:`assemble` is the technology-neutral second half —
it merges every fragment for one artifact key into
``<out>/callgraph/<artifact-key>.jsonl`` (sorted, de-duplicated, exactly the
byte shape the legacy single-pass emitter produced for identical inputs) and
rolls every fragment's coverage row into ``<out>/callgraph-coverage.json``.
This module never branches on a language name itself: "go"/"js" only ever
appear here as DATA read back out of a fragment.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..executor import create_stage_dir, write_new_text
from ..sanitize import sanitize_text
from ..status import Status, aggregate
from .contract import CallEdge, CallSiteCounts, CoverageReport, RepoCoverage

_STATUS_MAP = {
    "complete": Status.COMPLETE,
    "partial": Status.PARTIAL,
    "failed": Status.FAILED,
    "unavailable": Status.SKIPPED,
}


def _write_jsonl(path: Path, edges: list[CallEdge]) -> None:
    ordered = sorted(set(edges), key=lambda e: e.sort_key())
    body = "".join(edge.to_json_line() + "\n" for edge in ordered)
    write_new_text(path, sanitize_text(body))


# ---------------------------------------------------------------------------
# Fragment assembler — the production path. Providers write the fragments;
# this merges them. Technology-neutral: "go"/"js" only ever appear as DATA
# read back from a fragment file, never as a branching literal here.
# ---------------------------------------------------------------------------

def _repo_coverage_from_row(row: dict) -> RepoCoverage:
    """Reconstruct a :class:`RepoCoverage` from a fragment's ``coverage_row``
    (the dict a provider got from ``RepoCoverage.to_dict()``) — contract.py
    intentionally has no ``from_dict`` for this type, so the round trip is
    done here rather than widening the contract."""
    call_sites_row = row.get("call_sites") or {}
    call_sites = CallSiteCounts(
        resolved=call_sites_row.get("resolved", 0),
        ambiguous=call_sites_row.get("ambiguous", 0),
        external=call_sites_row.get("external", 0),
        unresolved=call_sites_row.get("unresolved", 0),
    )
    return RepoCoverage(
        repository_ref=row["repository_ref"], lang=row["lang"], status=row["status"],
        tool=row.get("tool", ""), tool_version=row.get("tool_version", ""),
        algorithm=row.get("algorithm", ""), warm_cache=row.get("warm_cache", "n/a"),
        reason=row.get("reason", ""),
        candidates_by_ext=dict(row.get("candidates_by_ext") or {}),
        analyzed_by_ext=dict(row.get("analyzed_by_ext") or {}),
        excluded_by_reason=dict(row.get("excluded_by_reason") or {}),
        parse_load_failures=row.get("parse_load_failures", 0),
        call_sites=call_sites, edges_emitted=row.get("edges_emitted", 0),
        notes=row.get("notes", ""),
    )


def assemble(out_dir: str | Path, scan_date: str) -> CoverageReport:
    """Merge every ``callgraph/.fragments/*.json`` fragment a provider wrote
    into the run's final ``callgraph/<artifact-key>.jsonl`` files and
    ``callgraph-coverage.json`` — byte-identical to the legacy single-pass
    shape for identical inputs (same sort/dedup, same sanitized write).

    ``artifact_key``/``lane`` are read from EACH fragment's own JSON body,
    never parsed from its filename: an artifact key can itself contain a
    literal dot (identity.py's portable-but-literal encoding leaves dots
    intact), so a filename-stem split would be ambiguous. Zero fragments
    still produce the empty, legacy-shaped coverage doc — nothing having run
    is disclosed, never silently omitted.
    """
    out = Path(out_dir).expanduser().resolve()
    cg_dir = create_stage_dir(out / "callgraph")
    fragments_dir = cg_dir / ".fragments"
    edges_by_key: dict[str, list[CallEdge]] = {}
    coverages: list[RepoCoverage] = []
    fragment_paths = sorted(fragments_dir.glob("*.json")) if fragments_dir.is_dir() else []
    for fragment_path in fragment_paths:
        payload = json.loads(fragment_path.read_text("utf-8"))
        artifact_key = payload["artifact_key"]
        edges_by_key.setdefault(artifact_key, []).extend(
            CallEdge.from_dict(row) for row in payload.get("edges", []))
        coverages.append(_repo_coverage_from_row(payload["coverage_row"]))
    for artifact_key in sorted(edges_by_key):
        _write_jsonl(cg_dir / f"{artifact_key}.jsonl", edges_by_key[artifact_key])
    report = CoverageReport(scan_date=scan_date, repos=coverages)
    write_new_text(out / "callgraph-coverage.json", sanitize_text(report.to_json()))
    return report


def aggregate_status(report: CoverageReport) -> Status:
    """Worst per-lane status, for the CLI exit code (empty -> nothing ran)."""
    return aggregate([_STATUS_MAP[c.status] for c in report.repos])
