"""57B-84 B2: the ui-route-linkage capability provider.

See ``test_route_inventory_provider.py``'s module docstring for why the
shared conformance battery runs with no ``repo_setup`` content here (the
applicability gate can never fire inside that harness) and why real
gate/scan content correctness is proven separately below, through the
actual ``discovery.emit.discover()`` pipeline.
"""

from __future__ import annotations

import json

from analysis_wrapper import identity
from analysis_wrapper.discovery import emit, liveness
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import UiRouteLinkageProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from provider_conformance import run_provider_conformance


def test_ui_route_linkage_provider_conforms_via_zero_profile_battery_shape(tmp_path):
    run_provider_conformance(None, UiRouteLinkageProvider(), tmp_path=tmp_path)


def test_bundled_ui_route_linkage_provider_is_universal_with_no_profile():
    provider = UiRouteLinkageProvider()
    assert provider.profile_ids == ()
    assert provider.universal is True
    assert provider.capability_id == "ui-route-linkage"
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


def test_provider_gate_and_calls_match_direct_liveness_scan_on_a_real_frontend(tmp_path):
    """The exact legacy frontend gate (ts/js/tsx stack + src/ dir + no own
    routes, no route-inventory profile match) fires for a real SPA-shaped
    repo, and the fragment's calls match a direct ``ui_call_sites()`` call."""
    ws = tmp_path / "workspace"
    web = ws / "web"
    _write(web / "package.json", "{}")
    _write(web / "src" / "api.ts", "get(`${api}/items`); get(`${api}/health`);\n")
    run = tmp_path / "run"
    spec, report = emit.discover(ws)
    emit.write_stage1(run, spec, report)
    identities = identity.load(run)
    target = spec.repos[0]
    context = _run_context(spec, run, identities)

    result = UiRouteLinkageProvider().run(context, target)

    artifact_path = run / result.artifact_refs[0].path
    fragment = json.loads(artifact_path.read_text("utf-8"))
    assert fragment["applicable"] is True
    direct_hits = liveness.ui_call_sites(target.path)
    expected_calls = [{"base": hit.base, "path": hit.path,
                      "evidence": hit.evidence, "method": hit.method}
                     for hit in direct_hits]
    assert fragment["calls"] == expected_calls
    assert len(fragment["calls"]) == 2
    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == "complete"


def test_provider_gate_is_false_for_a_backend_repo_with_its_own_routes(tmp_path):
    """A repo discovery found ITS OWN registered routes in is a backend, not
    a frontend, even if it happens to carry a ts/js stack and a src/ layout
    — the legacy ``not has_routes`` guard, replicated exactly."""
    ws = tmp_path / "workspace"
    api = ws / "api"
    _write(api / "package.json", '{"dependencies":{"express":"1"}}')
    _write(api / "src" / "app.js", "app.get('/items', h);\n")
    run = tmp_path / "run"
    spec, report = emit.discover(ws)
    emit.write_stage1(run, spec, report)
    identities = identity.load(run)
    target = spec.repos[0]
    context = _run_context(spec, run, identities)

    result = UiRouteLinkageProvider().run(context, target)

    artifact_path = run / result.artifact_refs[0].path
    fragment = json.loads(artifact_path.read_text("utf-8"))
    assert fragment["applicable"] is False
    assert fragment["calls"] == []


def test_provider_gate_is_false_for_a_go_only_repo(tmp_path):
    """A bare Go repo matches neither the profile-match nor the ts/js/tsx
    stack half of the frontend gate."""
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

    result = UiRouteLinkageProvider().run(context, target)

    artifact_path = run / result.artifact_refs[0].path
    fragment = json.loads(artifact_path.read_text("utf-8"))
    assert fragment["applicable"] is False
    assert fragment["calls"] == []
    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == "complete"
