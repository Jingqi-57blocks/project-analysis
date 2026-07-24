"""57B-84 B2: the route-inventory capability provider.

Three concerns, mirroring ``test_access_evidence_provider.py``'s structure:

1. ``RouteInventoryProvider`` passes the shared conformance battery via
   ``run_provider_conformance``'s zero-profile ``profile=None`` shape. No
   ``repo_setup`` is populated here: this provider's applicability gate
   (``has_routes`` read back from ``discovery-report.json``, OR a
   ``route-inventory`` profile match) can never fire inside the battery's
   own harness — a zero-profile registry gives the repo no facet to match,
   and the battery never writes a discovery-report.json — so real route
   content in ``repo_setup`` would be silently ignored (an empty fragment
   either way). The battery still fully proves the generic provider-loop
   mechanics (registration, deterministic execution, no-raw-id leakage,
   network-flag plumbing, universal bare-repo selection). Real gate/scan
   content correctness is proven separately below, through the actual
   ``discovery.emit.discover()`` pipeline.

2. The legacy backend gate (``has_routes or target.profiles_for_capability(
   "route-inventory")``) is replicated exactly from the retired
   ``discovery/emit.py`` block — proven against a real Express fixture run
   through the real discovery pipeline, not a hand-built stand-in.

3. ``_has_module_signal_routes`` degrades to ``False`` (never raises) when
   ``discovery-report.json`` is absent/unreadable — the exact accommodation
   that makes concern #1 above safe, pinned directly.
"""

from __future__ import annotations

import json

from analysis_wrapper import identity
from analysis_wrapper.discovery import emit, liveness
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import RouteInventoryProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from provider_conformance import run_provider_conformance


def test_route_inventory_provider_conforms_via_zero_profile_battery_shape(tmp_path):
    run_provider_conformance(None, RouteInventoryProvider(), tmp_path=tmp_path)


def test_bundled_route_inventory_provider_is_universal_with_no_profile():
    provider = RouteInventoryProvider()
    assert provider.profile_ids == ()
    assert provider.universal is True
    assert provider.capability_id == "route-inventory"
    assert provider in bundled_registry().providers


# ---------------------------------------------------------------------------
# Real gate + scan content, through the actual discovery pipeline.
# ---------------------------------------------------------------------------


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _run_context(spec, run_dir, identities) -> RunContext:
    access = ExecutorToolAccess(
        spec, identities, run_dir, "2026-07-23", network_authorized=False)
    return RunContext(
        targets=spec, output_dir=run_dir, scan_date="2026-07-23",
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )


def test_provider_gate_and_rows_match_direct_liveness_scan_on_a_real_backend(tmp_path):
    """The exact legacy gate (``has_routes`` from ``module_signals.routes``)
    fires for a real Express repo discovery found routes in, and the
    fragment's rows match a direct ``route_registrations()`` call —
    proving both the gate replication and the thin-adapter scan are
    correct, not merely "some fragment got written"."""
    ws = tmp_path / "workspace"
    api = ws / "api"
    _write(api / "package.json", '{"dependencies":{"express":"1"}}')
    _write(api / "app.js", "app.get('/items', h); app.post('/items', h);\n")
    run = tmp_path / "run"
    spec, report = emit.discover(ws)
    emit.write_stage1(run, spec, report)
    identities = identity.load(run)
    target = next(t for t in spec.repos if t.repo_id == spec.repos[0].repo_id)
    context = _run_context(spec, run, identities)

    result = RouteInventoryProvider().run(context, target)

    artifact_path = run / result.artifact_refs[0].path
    fragment = json.loads(artifact_path.read_text("utf-8"))
    assert fragment["applicable"] is True
    direct_hits = liveness.route_registrations(
        target.path, target.tier2_exclusions, include_mounts=True)
    expected_rows = sorted(({
        "method": hit.method, "path": hit.path, "route_evidence": hit.evidence,
        "registration_kind": (
            "mount" if hit.method.upper() in liveness._MOUNTS else "endpoint"),
    } for hit in direct_hits), key=lambda row: (
        row["method"], row["path"], row["route_evidence"]))
    assert fragment["rows"] == expected_rows
    assert len(fragment["rows"]) == 2
    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == "complete"


def test_provider_gate_is_false_with_no_route_signal_and_no_profile(tmp_path):
    """A repo discovery found NO route signal in, and that carries no
    route-inventory-capability profile (a bare Go repo with no framework),
    is NOT a backend: an empty, disclosed (not omitted) fragment."""
    ws = tmp_path / "workspace"
    svc = ws / "svc"
    _write(svc / "go.mod", "module example.com/svc\n")
    _write(svc / "main.go", "package main\nfunc main() {}\n")
    run = tmp_path / "run"
    spec, report = emit.discover(ws)
    emit.write_stage1(run, spec, report)
    identities = identity.load(run)
    target = spec.repos[0]
    context = _run_context(spec, run, identities)

    result = RouteInventoryProvider().run(context, target)

    artifact_path = run / result.artifact_refs[0].path
    fragment = json.loads(artifact_path.read_text("utf-8"))
    assert fragment["applicable"] is False
    assert fragment["rows"] == []
    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == "complete"


def test_has_module_signal_routes_degrades_gracefully_without_discovery_report(tmp_path):
    """No discovery-report.json at ``context.output_dir`` (a provider
    invoked outside the full discovery pipeline — the conformance battery,
    or ``run_provider_stage`` exercised directly in isolation) must never
    fail this provider's execution: the module_signals.routes half of the
    gate degrades to "unknown" (False), not a raised exception."""
    from analysis_wrapper.profiles.providers import _has_module_signal_routes

    # Build identities the same lightweight way test_provider_execution.py does.
    from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = RepoTarget(repo_id=stable_repo_id(str(repo_path)), path=str(repo_path))
    identities = identity.build(
        TargetSpec([repo]), workspace_root=tmp_path,
        project_id=stable_repo_id(str(tmp_path)))

    assert not (tmp_path / "discovery-report.json").exists()
    assert _has_module_signal_routes(
        tmp_path, identities, identities.reference_for(repo.repo_id)) is False
