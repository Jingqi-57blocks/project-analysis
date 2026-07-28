"""Route-inventory / UI-route-linkage fragment assembler (57B-84 B2).

Production path: :mod:`analysis_wrapper.profiles.providers`'s
``RouteInventoryProvider``/``UiRouteLinkageProvider`` each replicate ONE of
discovery's legacy per-repo gates (backend: has registered routes or a
route-inventory-capability profile; frontend: a ui-route-linkage-capability
profile, or the ts/js + ``src/`` + no-own-routes fallback) and write ONE
fragment under ``routes/.fragments/<artifact_key>.{routes,uicalls}.json`` —
a backend's route registrations (unscanned again by anything else) or a
frontend's raw UI call sites. :func:`assemble` is the technology-neutral
second half: it reads every fragment and performs the cross-repo join that
used to live inside :func:`analysis_wrapper.discovery.liveness.liveness`,
called once PER FRONTEND and internally RE-SCANNING every backend's routes
each time (an undisclosed O(frontends x backends) recomputation). The join
now runs once, reusing each backend's already-scanned fragment across every
frontend, instead of re-deriving it.

Two canonical artifacts are written ONLY when at least one applicable
backend fragment exists (mirroring the legacy ``if backends:`` gate exactly
— a workspace with frontends but zero backends gets NEITHER file, same as
the retired ``discover()`` block leaving both report fields ``None``):

- ``routes/route-inventory.json``    — every applicable backend's route rows.
- ``routes/ui-route-linkage.json``   — every applicable frontend's UI-call
  matches against those routes (present, but with empty ``frontends``/
  ``rows``, when there ARE backends but zero frontends — never ``None``
  itself in that case, exactly mirroring the legacy nested-``if`` shape).

``routes/route-coverage.json`` is ALWAYS written (present/absent backend and
frontend counts) — the reuse marker ``cli._prepare_overview`` gates on,
mirroring ``callgraph-coverage.json``/``imports/depmap-coverage.json``'s own
always-present role for their stages.

Every row already carries EXTERNALIZED identity (``repository_ref``/
``frontend_repository_ref``) — providers resolve it via ``context.identities``
before writing their fragment, so this assembler (which runs entirely
post-identity) never touches an internal repo_id and never needs a
``identity.externalize_discovery_report``-style rewrite pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..discovery import liveness
from ..discovery.base_map import resolve_base_backends
from ..executor import create_stage_dir, write_new_text
from ..sanitize import sanitize_text

FRAGMENTS_SUBDIR = ".fragments"
ROUTE_INVENTORY_FILE = "route-inventory.json"
UI_ROUTE_LINKAGE_FILE = "ui-route-linkage.json"
COVERAGE_FILE = "route-coverage.json"

# Copied verbatim from liveness.LivenessReport's own constructor call inside
# liveness.liveness() (liveness.py itself: unchanged this slice, so these
# cannot be promoted to a shared constant there without touching it). Legacy
# emitted this text ONCE PER FRONTEND, "<frontend>: <note>"-prefixed, purely
# as a side effect of calling liveness() once per frontend — N identical
# copies of static methodology prose was never a disclosed fact about N
# frontends, just duplicate work. Written ONCE, unprefixed, here instead.
_METHODOLOGY_NOTES = (
    "RELIABLE output = the ui_calls inventory (every frontend→backend call "
    "with base + path + citation) and the `ui-called` rows (matches sharing "
    "a concrete path segment).",
    "LIMITATION: leaf route registrations lack their router MOUNT PREFIX "
    "(Express `app.use('/x', r)` / gin `r.Group(...)`), so `no-direct-path-"
    "match` is NOT an orphan/dead list — many such routes are live under a "
    "mount prefix this pass does not resolve. `match-ambiguous` = route "
    "normalized to all-wildcard (e.g. leaf `/:id`), unmatchable without the "
    "prefix. `base-unresolved` = a frontend call matches the route's path "
    "shape, but the caller's resolved base binds to a DIFFERENT backend or "
    "to none, so this backend is not credited (path shape alone never "
    "implies a caller). Nothing here is ever labeled 'dead': mobile/external/"
    "ops callers are invisible to repository evidence (standing disclaimer).",
    "match heuristic: version-prefix-tolerant, param wildcards, route is a "
    "prefix of the call, at least one concrete segment must agree.",
    "HTTP method must also be structurally observed and compatible before a "
    "UI call is credited; unknown or conflicting methods remain method-unresolved.",
)


@dataclass
class RouteAssembly:
    """Small summary the CLI prints and the reuse-marker check reads back."""

    backends: int = 0
    frontends: int = 0
    present: bool = False
    route_rows: int = 0
    linkage_rows: int = 0


def _write_json(path: Path, payload: dict) -> None:
    write_new_text(path, sanitize_text(json.dumps(payload, indent=2, sort_keys=True) + "\n"))


def _load_fragments(fragments_dir: Path, suffix: str) -> list[dict]:
    if not fragments_dir.is_dir():
        return []
    return [json.loads(path.read_text("utf-8"))
            for path in sorted(fragments_dir.glob(f"*.{suffix}.json"))]


def _route_inventory_notes(backends: list[dict]) -> list[str]:
    """Per-backend coverage-cap notes (already externalized, already
    repository_ref-prefixed by the provider) plus ONE global ast-grep-fallback
    note — computed once here, not once per backend fragment, so N backends
    never produce N duplicate copies of the same global fact."""
    notes = [note for backend in backends for note in backend.get("notes", [])]
    if not liveness.astgrep.available():
        notes.append("ROUTE EXTRACTION FALLBACK: ast-grep unavailable")
    return notes


def _concrete_route_segments(rows: list[dict]) -> list[list[str]]:
    """Concrete-bearing segments for base->backend RESOLUTION only. Mount
    registrations are excluded — the legacy ``liveness()`` built its own
    ``backend_routes`` from a route_registrations() call with no
    ``include_mounts`` argument (default False), so mounts never entered
    base resolution OR classification there either; only the separate,
    inventory-only scan in the old ``discover()`` block used
    ``include_mounts=True``. Same asymmetry, reproduced here from ONE
    already-scanned fragment instead of a second scan."""
    concrete = []
    for row in rows:
        if row.get("registration_kind") == "mount":
            continue
        segs = liveness._norm_segments(_route_path(row))
        if any(s != "*" for s in segs):
            concrete.append(segs)
    return concrete


def _route_path(row: dict) -> str:
    """Use an adapter-proven composed path when one is available."""
    full = row.get("full_path")
    return full if isinstance(full, str) and full.startswith("/") else row["path"]


def _paths_by_base_from_calls(calls: list[dict]) -> dict:
    """Mirrors ``liveness._paths_by_base`` exactly (concrete-bearing
    normalized call paths per base) — reimplemented here rather than
    imported since it is a two-line reduction over ``_norm_segments``, not a
    second copy of any matching HEURISTIC (``_norm_segments``/``_matches``
    themselves are imported, never re-derived, below)."""
    by_base: dict[str, set] = {}
    for call in calls:
        segs = liveness._norm_segments(call["path"])
        if any(s != "*" for s in segs):
            by_base.setdefault(call["base"], set()).add(tuple(segs))
    return by_base


def _classify_backend_rows(backend: dict, calls: list[dict],
                           base_backend: dict) -> list[dict]:
    """Pass 2 of the legacy ``liveness()`` join, reimplemented over an
    ALREADY-SCANNED backend fragment's rows instead of a fresh
    ``route_registrations()`` call. One row in, one status-tagged row out —
    same ladder liveness.py's own ``LivenessRow`` uses. Mount registrations
    are skipped entirely (never classified, never emitted here) — see
    ``_concrete_route_segments``; the inventory doc still lists them, the
    linkage join never did."""
    repository_ref = backend["repository_ref"]
    norm_calls = [(liveness._norm_segments(c["path"]), c) for c in calls]
    rows = []
    for row in backend.get("rows", []):
        if row.get("registration_kind") == "mount":
            continue
        route_path = _route_path(row)
        rsegs = liveness._norm_segments(route_path)
        if not any(s != "*" for s in rsegs):
            rows.append({**row, "repository_ref": repository_ref,
                        "status": "match-ambiguous", "caller_evidence": []})
            continue
        matching = [c for segs, c in norm_calls if liveness._matches(rsegs, segs)]
        bound_here = [c for c in matching if base_backend.get(c["base"]) == repository_ref]
        here = sorted(c["evidence"] for c in bound_here
                      if c.get("method") and c["method"] == row["method"].upper())
        if here:
            rows.append({**row, "path": route_path, "repository_ref": repository_ref,
                        "status": "ui-called", "caller_evidence": here[:3]})
            continue
        unknown_or_mismatch = sorted(c["evidence"] for c in bound_here)
        if unknown_or_mismatch:
            rows.append({**row, "path": route_path, "repository_ref": repository_ref,
                        "status": "method-unresolved",
                        "caller_evidence": unknown_or_mismatch[:3]})
            continue
        # internal_callers (same-service internal calls) is a liveness()
        # feature the legacy discover() call never populated (its own
        # `liveness.liveness(frontend.path, backends)` call always leaves it
        # at the default None/{}), so there is nothing to replicate here.
        if matching:
            rows.append({**row, "path": route_path, "repository_ref": repository_ref,
                        "status": "base-unresolved", "caller_evidence": []})
            continue
        rows.append({**row, "path": route_path, "repository_ref": repository_ref,
                    "status": "no-direct-path-match", "caller_evidence": []})
    return rows


def assemble(out_dir: str | Path) -> RouteAssembly:
    """Read every ``routes/.fragments/*.{routes,uicalls}.json`` fragment a
    provider wrote and produce the two canonical run-level docs (only when
    >=1 applicable backend exists) plus the always-present coverage marker.
    """
    out = Path(out_dir).expanduser().resolve()
    routes_dir = create_stage_dir(out / "routes")
    fragments_dir = routes_dir / FRAGMENTS_SUBDIR

    all_backends = _load_fragments(fragments_dir, "routes")
    all_frontends = _load_fragments(fragments_dir, "uicalls")
    backends = sorted((b for b in all_backends if b.get("applicable")),
                      key=lambda b: b["repository_ref"])
    frontends = sorted((f for f in all_frontends if f.get("applicable")),
                       key=lambda f: f["repository_ref"])

    result = RouteAssembly(backends=len(backends), frontends=len(frontends))

    if backends:
        result.present = True
        inventory_rows = sorted(
            ({**row, "repository_ref": backend["repository_ref"]}
             for backend in backends for row in backend.get("rows", [])),
            key=lambda row: (row["repository_ref"], row["method"], row["path"],
                            row["route_evidence"]))
        result.route_rows = len(inventory_rows)
        _write_json(routes_dir / ROUTE_INVENTORY_FILE, {
            "notes": _route_inventory_notes(backends),
            "rows": inventory_rows,
            **liveness.astgrep.probe().provenance(),
        })

        backend_routes = [(b["repository_ref"], _concrete_route_segments(b.get("rows", [])))
                          for b in backends]
        linkage_rows: list[dict] = []
        calls_by_frontend_repository: dict[str, dict] = {}
        linkage_notes: list[str] = list(_METHODOLOGY_NOTES)
        if not liveness.astgrep.available():
            linkage_notes.append(
                "ROUTE EXTRACTION FALLBACK: ast-grep unavailable — route "
                "registrations came from the transparent regex scan, not the "
                "structural rule (reduced robustness; disclosed).")
        for frontend in frontends:
            calls = frontend.get("calls", [])
            report = liveness.LivenessReport()
            report.ui_calls = [liveness.CallHit(**call) for call in calls]
            calls_by_frontend_repository[frontend["repository_ref"]] = report.calls_by_base()
            base_backend, base_notes = resolve_base_backends(
                _paths_by_base_from_calls(calls), backend_routes, liveness._matches)
            linkage_notes.extend(
                f"{frontend['repository_ref']}: {note}" for note in base_notes)
            # The frontend's own scan-cap notes (its ui_call_sites() walk) —
            # attributed to the repo they actually occurred in, unlike legacy
            # (which smeared each backend-rescan's cap notes under every
            # calling frontend's prefix — an artifact of the per-frontend
            # backend rescan this slice removes, disclosed as a deviation).
            linkage_notes.extend(
                f"{frontend['repository_ref']}: {note}"
                for note in frontend.get("notes", []))
            for backend in backends:
                for row in _classify_backend_rows(backend, calls, base_backend):
                    if row["status"] not in {"ui-called", "method-unresolved"} \
                            or not row["caller_evidence"]:
                        continue
                    linkage_row = {
                        "frontend_repository_ref": frontend["repository_ref"],
                        "repository_ref": row["repository_ref"],
                        "method": row["method"], "path": row["path"],
                        "route_evidence": row["route_evidence"],
                        "status": row["status"],
                        "caller_evidence": row["caller_evidence"],
                    }
                    # Preserve the legacy linkage shape for ordinary
                    # registrations. This chain exists only when the full
                    # path was deterministically composed from literal groups.
                    if row.get("composition_evidence"):
                        linkage_row["composition_evidence"] = row["composition_evidence"]
                    linkage_rows.append(linkage_row)
        result.linkage_rows = len(linkage_rows)
        _write_json(routes_dir / UI_ROUTE_LINKAGE_FILE, {
            "frontends": [f["repository_ref"] for f in frontends],
            "calls_by_frontend_repository": calls_by_frontend_repository,
            "rows": sorted(linkage_rows, key=lambda row: (
                row["frontend_repository_ref"], row["repository_ref"],
                row["method"], row["path"], row["route_evidence"])),
            "notes": sorted(set(linkage_notes)),
        })

    _write_json(routes_dir / COVERAGE_FILE, {
        "present": result.present,
        "backends": result.backends,
        "frontends": result.frontends,
    })
    return result
