"""57B-82 A1: the deploy-units capability provider.

Mirrors ``test_datastore_evidence_provider.py``'s "unit equality vs. a direct
call" section: ``DeployUnitsProvider`` is a thin adapter over
:func:`analysis_wrapper.discovery.deploy_units.generate` (unmodified), so its
coverage/artifact must be provably equivalent to a direct call — exercised
against a fixture repo carrying one of each detected artifact kind (Dockerfile,
compose service, Go ``package main`` entrypoint, CI deploy step) plus a bare
fixture with none, to prove ``status="unknown"`` is emitted rather than an
empty/absent result.

Also runs the shared conformance battery via ``run_provider_conformance``'s
zero-profile ``profile=None`` shape (``profiles/registry.py`` and
``tests/provider_conformance.py`` both gained a carve-out for exactly this —
a ``universal`` provider with empty ``profile_ids``, since no detected facet
predicts a deploy artifact's presence): ``test_conformance.py`` already pins
that MECHANISM generically with a synthetic provider; the test below proves
the REAL ``DeployUnitsProvider`` passes it too.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper import identity
from analysis_wrapper.discovery import deploy_units
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import DeployUnitsProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id
from provider_conformance import run_provider_conformance


def _run_context(spec: TargetSpec, run_dir: Path, identities) -> RunContext:
    access = ExecutorToolAccess(
        spec, identities, run_dir, "2026-07-23", network_authorized=False)
    return RunContext(
        targets=spec, output_dir=run_dir, scan_date="2026-07-23",
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )


def _populate_mixed_artifacts(repo: Path) -> None:
    """One of each artifact kind ``deploy_units.generate`` detects, written
    into an already-prepared repo directory (the ``repo_setup`` callback
    shape ``run_provider_conformance`` expects for a zero-profile provider)."""
    (repo / "deploy").mkdir(parents=True, exist_ok=True)
    (repo / "deploy" / "docker-compose.yml").write_text(
        "services:\n  api:\n    build: .\n  cache:\n    image: redis:7\n", "utf-8")
    (repo / "Dockerfile").write_text("FROM scratch\n", "utf-8")
    (repo / "cmd" / "svc").mkdir(parents=True, exist_ok=True)
    (repo / "cmd" / "svc" / "main.go").write_text(
        "package main\nfunc main() {}\n", "utf-8")
    (repo / "bitbucket-pipelines.yml").write_text(
        "pipelines:\n  default:\n    - step:\n        script:\n          - deploy\n",
        "utf-8")


def _mixed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "svc"
    repo.mkdir(parents=True)
    _populate_mixed_artifacts(repo)
    return repo


def _bare_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "lib-only"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "index.js").write_text("export const x = 1;\n", "utf-8")
    return repo


def _build(repo_path: Path, tmp_path: Path):
    target = RepoTarget(repo_id=stable_repo_id(str(repo_path)), path=str(repo_path))
    spec = TargetSpec([target])
    identities = identity.build(
        spec, workspace_root=repo_path, project_id=stable_repo_id(str(repo_path)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)
    return target, run_dir, context


def test_provider_matches_direct_generate_on_mixed_artifact_repo(tmp_path):
    repo = _mixed_repo(tmp_path / "target")
    target, run_dir, context = _build(repo, tmp_path)

    result = DeployUnitsProvider().run(context, target)
    direct = deploy_units.generate(str(repo), target.tier2_exclusions).to_dict()

    assert direct["status"] == "inferred"
    artifact_path = run_dir / result.artifact_refs[0].path
    assert json.loads(artifact_path.read_text("utf-8")) == direct
    assert result.artifact_refs[0].path.startswith("deploy/")

    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == "complete"
    assert result.coverage.reason_code == "deploy-units-inferred"

    # No Facts this slice (coverage + artifact_refs only).
    assert result.facts == ()
    # No linked profiles -> facet_provenance is always empty, honestly.
    assert result.facet_provenance == ()
    assert DeployUnitsProvider().profile_ids == ()
    assert getattr(DeployUnitsProvider(), "universal", False) is True


def test_provider_reports_unknown_not_empty_on_a_bare_repo(tmp_path):
    repo = _bare_repo(tmp_path / "target")
    target, run_dir, context = _build(repo, tmp_path)

    result = DeployUnitsProvider().run(context, target)
    direct = deploy_units.generate(str(repo), target.tier2_exclusions).to_dict()

    assert direct["status"] == "unknown"
    assert direct["units"] == [] and direct["artifacts"] == []
    artifact_path = run_dir / result.artifact_refs[0].path
    payload = json.loads(artifact_path.read_text("utf-8"))
    assert payload == direct
    assert payload["status"] == "unknown"          # scan completed; disclosed, not absent

    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == "complete"
    assert result.coverage.reason_code == "deploy-units-unknown"


def test_capped_scan_degrades_coverage_to_partial(tmp_path, monkeypatch):
    """A disclosed COVERAGE CAP note (deploy_units.generate's own file-count
    cap) must degrade the provider's coverage status to partial, mirroring
    the ``_deploy`` system-model partition's own cap handling."""
    monkeypatch.setattr(deploy_units, "_MAX_FILES", 0)
    repo = _mixed_repo(tmp_path / "target")
    target, run_dir, context = _build(repo, tmp_path)

    result = DeployUnitsProvider().run(context, target)
    direct = deploy_units.generate(str(repo), target.tier2_exclusions).to_dict()

    assert any("COVERAGE CAP" in note for note in direct["notes"])
    assert result.coverage.status == "partial"
    assert "COVERAGE CAP" in result.coverage.detail


def test_provider_writes_no_source_ref_with_a_raw_internal_repo_id(tmp_path):
    """Mirrors the same identity-leak pin ``test_datastore_evidence_provider.py``
    applies: the artifact key on disk must be the resolved artifact key, never
    the raw internal repo_id."""
    repo = _mixed_repo(tmp_path / "target")
    target, run_dir, context = _build(repo, tmp_path)

    result = DeployUnitsProvider().run(context, target)
    artifact_key = context.identities.artifact_key_for(target.repo_id)
    assert result.artifact_refs[0].path == f"deploy/{artifact_key}.json"
    assert target.repo_id not in result.artifact_refs[0].path


# ---------------------------------------------------------------------------
# Conformance battery (zero-profile universal shape) + registration.
# ---------------------------------------------------------------------------


def test_deploy_units_provider_conforms_via_zero_profile_battery_shape(tmp_path):
    run_provider_conformance(
        None, DeployUnitsProvider(), tmp_path=tmp_path, repo_setup=_populate_mixed_artifacts)


def test_bundled_deploy_units_provider_is_registered_zero_profile_universal():
    registry = bundled_registry()
    provider = registry.provider("deploy-units")
    assert isinstance(provider, DeployUnitsProvider)
    assert provider.profile_ids == ()
    assert getattr(provider, "universal", False) is True
    assert provider.capability_id == "deployable-units"
