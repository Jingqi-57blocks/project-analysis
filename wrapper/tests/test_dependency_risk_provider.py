"""57B-82 A2: the dependency-risk capability provider.

Covers: the exact ecosystem-gate replication of ``registry.network_tools``
(osv when a lockfile is declared OR the target is Go; outdated when the
target is Node; a repo can select both), the network-off SKIPPED reason
preserved verbatim (our parity runs are network-disabled — this is the
scenario the aggregate Coverage MUST surface honestly), the not-applicable
case, byte-identity against the legacy sweep's direct invocation, and
resume-safety against a REAL ``ExecutorToolAccess`` (not the shared
battery's stub, which never writes a real file and so can't prove this).
"""

from __future__ import annotations

from pathlib import Path

from analysis_wrapper import identity
from analysis_wrapper.executor import run_tool
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import DependencyRiskProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.registry import osv
from analysis_wrapper.targetspec import (PackageManager, RepoTarget, TargetSpec,
                                         TechnologyFacet, stable_repo_id)
from provider_conformance import run_provider_conformance


def _target(path: Path, *, facets: list[TechnologyFacet] | None = None,
           pm: PackageManager | None = None) -> RepoTarget:
    path.mkdir(parents=True, exist_ok=True)
    return RepoTarget(repo_id=stable_repo_id(str(path)), path=str(path),
                      facets=list(facets or []), pm=pm or PackageManager())


def _context(spec: TargetSpec, identities, run_dir: Path, *,
            network_authorized: bool = False) -> RunContext:
    access = ExecutorToolAccess(spec, identities, run_dir / "signals", "2026-07-24",
                                network_authorized=network_authorized)
    return RunContext(
        targets=spec, output_dir=run_dir, scan_date="2026-07-24",
        network_authorized=network_authorized, provenance={},
        tool_access=access, identities=identities,
    )


def test_no_ecosystem_is_not_applicable(tmp_path):
    target = _target(tmp_path / "lib-only")
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    context = _context(spec, identities, tmp_path / "run")

    result = DependencyRiskProvider().run(context, target)

    assert result.coverage.applicability == "not-applicable"
    assert result.coverage.status == "complete"
    assert "no declared lockfile" in result.coverage.detail
    assert result.artifact_refs == ()
    assert result.facts == ()


def test_go_target_without_lockfile_still_selects_osv(tmp_path):
    """``network_tools``'s exact gate: ``target.pm.lockfile or
    selection.is_go_target(target)`` — a Go facet alone, no lockfile at all,
    must still select osv (network-unauthorized here, so SKIPPED — the point
    is that it was SELECTED, not silently omitted)."""
    target = _target(tmp_path / "svc",
                     facets=[TechnologyFacet("language.go", "language", ["."], ["go.mod"])])
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    context = _context(spec, identities, tmp_path / "run", network_authorized=False)

    result = DependencyRiskProvider().run(context, target)

    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == "skipped"
    assert result.coverage.detail == "network-capable tool requires explicit authorization"
    assert len(result.artifact_refs) == 1
    assert result.artifact_refs[0].path.startswith("signals/osv-scanner-")


def test_node_target_selects_outdated(tmp_path):
    target = _target(tmp_path / "web",
                     facets=[TechnologyFacet("language.javascript", "language", ["."], [".js"])])
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    context = _context(spec, identities, tmp_path / "run", network_authorized=False)

    result = DependencyRiskProvider().run(context, target)

    assert result.coverage.status == "skipped"
    assert result.coverage.detail == "network-capable tool requires explicit authorization"
    assert result.artifact_refs[0].path.startswith("signals/outdated-")


def test_node_target_with_lockfile_selects_both_osv_and_outdated(tmp_path):
    target = _target(
        tmp_path / "web",
        facets=[TechnologyFacet("language.javascript", "language", ["."], [".js"])],
        pm=PackageManager(name="npm", lockfile="package-lock.json"))
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    context = _context(spec, identities, tmp_path / "run", network_authorized=False)

    result = DependencyRiskProvider().run(context, target)

    # Both sub-tools ran (both SKIPPED identically offline); the aggregate is
    # STILL exactly that one shared SKIPPED reason, not some merged/garbled
    # text, and BOTH artifacts are disclosed.
    assert result.coverage.status == "skipped"
    assert result.coverage.detail == "network-capable tool requires explicit authorization"
    refs = {ref.path.split("/")[1].rsplit("-", 1)[0] for ref in result.artifact_refs}
    assert {"osv-scanner", "outdated"} <= {r for r in refs}
    assert len(result.artifact_refs) == 2


def test_provider_matches_direct_sweep_invocation_byte_for_byte(tmp_path):
    """osv-scanner specifically (network-off -> SKIPPED, deterministic and
    toolchain-independent): the provider's manifest must be indistinguishable
    from the legacy sweep's direct ``registry.osv(...)`` + ``run_tool(...)``
    call."""
    target = _target(tmp_path / "svc",
                     facets=[TechnologyFacet("language.go", "language", ["."], ["go.mod"])])
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    artifact_key = identities.artifact_key_for(target.repo_id)

    run_a = tmp_path / "run-a"
    signals_a = run_a / "signals"
    signals_a.mkdir(parents=True)
    tooldef = osv(target)
    direct = run_tool(tooldef, target, signals_a, "2026-07-24",
                      identities.repository(target.repo_id), allow_network=False)

    run_b = tmp_path / "run-b"
    (run_b / "signals").mkdir(parents=True)
    context = _context(spec, identities, run_b, network_authorized=False)
    result = DependencyRiskProvider().run(context, target)

    name = f"osv-scanner-{artifact_key}"
    normalized_a = (signals_a / f"{name}.manifest.normalized.json").read_text("utf-8")
    normalized_b = (run_b / "signals" / f"{name}.manifest.normalized.json").read_text("utf-8")
    assert normalized_a == normalized_b
    assert result.coverage.status == direct.status.value
    assert result.coverage.detail == direct.reason


def test_provider_reuses_existing_manifests_on_a_resumed_pass_without_crashing(tmp_path):
    target = _target(
        tmp_path / "web",
        facets=[TechnologyFacet("language.javascript", "language", ["."], [".js"])],
        pm=PackageManager(name="npm", lockfile="package-lock.json"))
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    run_dir = tmp_path / "run"
    (run_dir / "signals").mkdir(parents=True)
    context = _context(spec, identities, run_dir)

    first = DependencyRiskProvider().run(context, target)
    second = DependencyRiskProvider().run(context, target)  # must not raise

    assert first.coverage == second.coverage
    assert first.artifact_refs == second.artifact_refs


def test_provider_conforms_via_zero_profile_battery_shape(tmp_path):
    """The battery's zero-profile ``RepoTarget`` never carries ``facets``/
    ``pm`` (only ``repo_id``/``path``), so this always exercises the
    not-applicable branch — still a real proof of the GENERIC battery
    guarantees (determinism, no repo_id leak, tool-access boundary, universal
    bare-repo selection); the ecosystem-gated branches are proven by the
    dedicated tests above instead."""
    run_provider_conformance(None, DependencyRiskProvider(), tmp_path=tmp_path)


def test_bundled_dependency_risk_provider_is_registered_zero_profile_universal():
    from analysis_wrapper.profiles.bundled import bundled_registry
    registry = bundled_registry()
    provider = registry.provider("dependency-risk")
    assert isinstance(provider, DependencyRiskProvider)
    assert provider.profile_ids == ()
    assert getattr(provider, "universal", False) is True
    assert provider.capability_id == "dependency-risk"
