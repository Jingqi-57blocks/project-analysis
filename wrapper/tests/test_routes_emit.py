"""57B-84 B2: ``routes.emit.assemble`` — the fragment+assemble cross-repo join.

Fragments are written directly here (the shape ``RouteInventoryProvider``/
``UiRouteLinkageProvider`` write), so the join logic is exercised in
isolation from provider/discovery machinery — fast, and pins the exact
canonical-doc shape independent of how a fragment got there. One test at
the bottom proves the join is EQUIVALENT to the original
``discovery.liveness.liveness()`` function on real scanned content, which
is the property this migration must preserve (the join used to live inside
that function, called once per frontend and re-scanning every backend each
time; ``assemble`` now runs the same classification once, reusing each
backend's already-scanned fragment across every frontend).
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper.discovery import liveness
from analysis_wrapper.routes import emit as routes_emit


def _write_backend_fragment(routes_dir: Path, artifact_key: str, repository_ref: str,
                            *, applicable: bool, rows: list[dict],
                            notes: list[str] | None = None) -> None:
    fragments = routes_dir / ".fragments"
    fragments.mkdir(parents=True, exist_ok=True)
    (fragments / f"{artifact_key}.routes.json").write_text(json.dumps({
        "artifact_key": artifact_key, "repository_ref": repository_ref,
        "applicable": applicable, "rows": rows, "notes": notes or [],
    }), "utf-8")


def _write_frontend_fragment(routes_dir: Path, artifact_key: str, repository_ref: str,
                             *, applicable: bool, calls: list[dict],
                             notes: list[str] | None = None) -> None:
    fragments = routes_dir / ".fragments"
    fragments.mkdir(parents=True, exist_ok=True)
    (fragments / f"{artifact_key}.uicalls.json").write_text(json.dumps({
        "artifact_key": artifact_key, "repository_ref": repository_ref,
        "applicable": applicable, "calls": calls, "notes": notes or [],
    }), "utf-8")


def _row(method: str, path: str, evidence: str, kind: str = "endpoint") -> dict:
    return {"method": method, "path": path, "route_evidence": evidence,
            "registration_kind": kind}


def _call(base: str, path: str, evidence: str, method: str = "") -> dict:
    return {"base": base, "path": path, "evidence": evidence, "method": method}


def test_zero_fragments_writes_only_the_coverage_marker(tmp_path):
    result = routes_emit.assemble(tmp_path)

    assert result == routes_emit.RouteAssembly(
        backends=0, frontends=0, present=False, route_rows=0, linkage_rows=0)
    assert not (tmp_path / "routes" / "route-inventory.json").exists()
    assert not (tmp_path / "routes" / "ui-route-linkage.json").exists()
    coverage = json.loads((tmp_path / "routes" / "route-coverage.json").read_text("utf-8"))
    assert coverage == {"present": False, "backends": 0, "frontends": 0}


def test_non_applicable_fragments_are_excluded_same_as_absent(tmp_path):
    routes_dir = tmp_path / "routes"
    _write_backend_fragment(routes_dir, "api", "api", applicable=False,
                            rows=[_row("GET", "/x", "a.go:1")])
    _write_frontend_fragment(routes_dir, "web", "web", applicable=False,
                             calls=[_call("api", "/x", "a.ts:1")])

    result = routes_emit.assemble(tmp_path)

    assert result.present is False
    assert result.backends == 0
    assert result.frontends == 0
    assert not (routes_dir / "route-inventory.json").exists()
    assert not (routes_dir / "ui-route-linkage.json").exists()


def test_backends_only_writes_inventory_and_an_empty_but_present_linkage(tmp_path):
    """Legacy ``discover()``'s own nested-if shape: ui_route_linkage is
    written whenever >=1 backend is applicable, REGARDLESS of whether any
    frontend exists — never left ``None`` just because frontends is empty."""
    routes_dir = tmp_path / "routes"
    _write_backend_fragment(routes_dir, "api", "api", applicable=True,
                            rows=[_row("GET", "/items", "h.go:1")])

    result = routes_emit.assemble(tmp_path)

    assert result.present is True
    assert result.backends == 1
    assert result.frontends == 0
    inventory = json.loads((routes_dir / "route-inventory.json").read_text("utf-8"))
    assert inventory["rows"] == [{"repository_ref": "api", **_row("GET", "/items", "h.go:1")}]
    linkage = json.loads((routes_dir / "ui-route-linkage.json").read_text("utf-8"))
    assert linkage["frontends"] == []
    assert linkage["rows"] == []
    assert linkage["calls_by_frontend_repository"] == {}


def test_join_classifies_the_full_status_ladder(tmp_path):
    """Base resolution needs >=2 DISTINCT concrete call paths landing on a
    backend before it credits that base to it (``base_map.MIN_MATCHES`` —
    a single coincidental path match is deliberately too thin) — the
    frontend below calls two distinct paths (``/items``, ``/health``) so
    "api" resolves, then each backend route is checked against that
    resolved base."""
    routes_dir = tmp_path / "routes"
    _write_backend_fragment(routes_dir, "api", "api", applicable=True, rows=[
        _row("GET", "/items", "h.go:1"),   # matched, method agrees -> ui-called
        _row("POST", "/items", "h.go:2"),  # matched, method disagrees -> method-unresolved
        _row("GET", "/health", "h.go:3"),  # matched, method agrees -> ui-called
        _row("GET", "/orphan", "h.go:4"),  # no call matches shape -> no-direct-path-match
        _row("GET", "/:id", "h.go:5"),     # all-wildcard -> match-ambiguous
    ])
    _write_frontend_fragment(routes_dir, "web", "web", applicable=True, calls=[
        _call("api", "/items", "a.ts:1", method="GET"),
        _call("api", "/health", "a.ts:2", method="GET"),
    ])

    routes_emit.assemble(tmp_path)

    linkage = json.loads((routes_dir / "ui-route-linkage.json").read_text("utf-8"))
    statuses = {(row["method"], row["path"]): row["status"] for row in linkage["rows"]}
    # Only ui-called and method-unresolved rows are ever materialized as
    # linkage rows (the legacy discover() block's own filter) — no-direct-
    # path-match/match-ambiguous never appear here, only in the inventory.
    assert statuses == {
        ("GET", "/items"): "ui-called",
        ("POST", "/items"): "method-unresolved",
        ("GET", "/health"): "ui-called",
    }
    inventory = json.loads((routes_dir / "route-inventory.json").read_text("utf-8"))
    assert len(inventory["rows"]) == 5  # every row, including the two filtered above


def test_mount_registrations_appear_in_inventory_never_in_the_join(tmp_path):
    """The legacy ``liveness()`` join always scanned WITHOUT
    ``include_mounts`` (mounts filtered out structurally); the inventory-
    only scan used ``include_mounts=True``. Same asymmetry here: a mount
    row is listed in route-inventory.json (topology) but never classified
    or credited in the base-resolution/join (ui-route-linkage.json)."""
    # Base resolution needs >=2 DISTINCT concrete call paths (base_map.
    # MIN_MATCHES) — /items/detail and /items/other give the join enough
    # evidence to credit base "api" to this one backend.
    routes_dir = tmp_path / "routes"
    _write_backend_fragment(routes_dir, "api", "api", applicable=True, rows=[
        _row("USE", "/items", "h.go:1", kind="mount"),
        _row("GET", "/items/detail", "h.go:2"),
        _row("GET", "/items/other", "h.go:3"),
    ])
    _write_frontend_fragment(routes_dir, "web", "web", applicable=True, calls=[
        _call("api", "/items/detail", "a.ts:1", method="GET"),
        _call("api", "/items/other", "a.ts:2", method="GET"),
    ])

    routes_emit.assemble(tmp_path)

    inventory = json.loads((routes_dir / "route-inventory.json").read_text("utf-8"))
    kinds = {row["path"]: row["registration_kind"] for row in inventory["rows"]}
    assert kinds == {"/items": "mount", "/items/detail": "endpoint",
                     "/items/other": "endpoint"}
    linkage = json.loads((routes_dir / "ui-route-linkage.json").read_text("utf-8"))
    paths_in_linkage = {row["path"] for row in linkage["rows"]}
    assert "/items" not in paths_in_linkage  # the mount never enters the join
    assert "/items/detail" in paths_in_linkage
    assert "/items/other" in paths_in_linkage


def test_multiple_frontends_are_classified_independently(tmp_path):
    """Adding a second frontend must not change the first frontend's own
    rows or its calls_by_frontend_repository entry — the exact regression
    the fragment+assemble migration exists to prevent (the retired
    ``liveness()``-per-frontend call used to re-scan every backend once per
    frontend; this join reuses one scan across every frontend instead)."""
    # Base resolution needs >=2 DISTINCT concrete call paths (base_map.
    # MIN_MATCHES) — each frontend calls /items and /health so its own base
    # resolves on its own evidence.
    routes_dir = tmp_path / "routes"
    _write_backend_fragment(routes_dir, "api", "api", applicable=True, rows=[
        _row("GET", "/items", "h.go:1"),
        _row("GET", "/health", "h.go:2"),
    ])
    _write_frontend_fragment(routes_dir, "web-a", "web-a", applicable=True, calls=[
        _call("api", "/items", "a.ts:1", method="GET"),
        _call("api", "/health", "a.ts:2", method="GET"),
    ])

    solo = routes_emit.assemble(tmp_path)
    solo_linkage = json.loads((routes_dir / "ui-route-linkage.json").read_text("utf-8"))

    _write_frontend_fragment(routes_dir, "web-b", "web-b", applicable=True, calls=[
        _call("api", "/items", "b.ts:1", method="GET"),
        _call("api", "/health", "b.ts:2", method="GET"),
    ])
    (routes_dir / "route-inventory.json").unlink()
    (routes_dir / "ui-route-linkage.json").unlink()
    (routes_dir / "route-coverage.json").unlink()
    both = routes_emit.assemble(tmp_path)
    both_linkage = json.loads((routes_dir / "ui-route-linkage.json").read_text("utf-8"))

    web_a_rows_solo = [r for r in solo_linkage["rows"] if r["frontend_repository_ref"] == "web-a"]
    web_a_rows_both = [r for r in both_linkage["rows"] if r["frontend_repository_ref"] == "web-a"]
    assert web_a_rows_solo == web_a_rows_both
    assert solo.linkage_rows == 2
    assert both.linkage_rows == 4
    assert both_linkage["calls_by_frontend_repository"]["web-a"] == \
        solo_linkage["calls_by_frontend_repository"]["web-a"]


# ---------------------------------------------------------------------------
# Equality vs. the original discovery.liveness.liveness() classification.
# ---------------------------------------------------------------------------


def _write_source(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_join_is_equivalent_to_direct_liveness_call_on_real_scanned_content(tmp_path):
    """Scans a real backend + frontend on disk with liveness.py's OWN
    functions (unchanged this slice), feeds the results through fragments,
    and confirms ``routes.emit.assemble``'s classification matches what
    ``liveness.liveness()`` itself would produce directly on the same
    inputs — the equality proof that the join was faithfully migrated, not
    reimplemented differently."""
    # Base resolution needs >=2 DISTINCT concrete call paths (base_map.
    # MIN_MATCHES) — the frontend calls both /items and /health so base
    # "api" resolves to this one backend.
    api = tmp_path / "api"
    _write_source(api / "app.js",
                  "app.get('/items', h); app.post('/items', h); "
                  "app.get('/health', h); app.get('/orphan', h);\n")
    web = tmp_path / "web"
    _write_source(web / "src" / "api.ts",
                  "get(`${api}/items`); post(`${api}/items`); get(`${api}/health`);\n")

    route_hits = liveness.route_registrations(str(api), include_mounts=True)
    call_hits = liveness.ui_call_sites(str(web))

    # Direct oracle: the original, unmodified liveness() join.
    direct = liveness.liveness(str(web), [("api", str(api), [])])
    direct_by_path = {
        row.path: row.status for row in direct.rows
        if row.status in {"ui-called", "method-unresolved"} and row.caller_evidence
    }

    # Fragment + assemble path: same scanned data, fed as fragments.
    routes_dir = tmp_path / "run" / "routes"
    _write_backend_fragment(routes_dir, "api", "api", applicable=True, rows=[{
        "method": hit.method, "path": hit.path, "route_evidence": hit.evidence,
        "registration_kind": (
            "mount" if hit.method.upper() in liveness._MOUNTS else "endpoint"),
    } for hit in route_hits])
    _write_frontend_fragment(routes_dir, "web", "web", applicable=True, calls=[
        {"base": hit.base, "path": hit.path, "evidence": hit.evidence, "method": hit.method}
        for hit in call_hits])

    routes_emit.assemble(tmp_path / "run")
    linkage = json.loads((routes_dir / "ui-route-linkage.json").read_text("utf-8"))
    assembled_by_path = {row["path"]: row["status"] for row in linkage["rows"]}

    assert assembled_by_path == direct_by_path
    assert assembled_by_path.get("/items") == "ui-called"
    assert "/orphan" not in assembled_by_path
