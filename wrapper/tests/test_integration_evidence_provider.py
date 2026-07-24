"""57B-84: the integration-evidence capability provider.

Same shape as ``test_access_evidence_provider.py``: no dedicated profile,
conformance via ``run_provider_conformance``'s zero-profile ``profile=None``
shape (57B-82 A1's carve-out in ``profiles/registry.py`` and
``tests/provider_conformance.py`` — ``test_conformance.py`` already pins that
MECHANISM generically; the test below proves the REAL
``IntegrationEvidenceProvider`` passes it too), a thin unmodified wrapper
over :func:`analysis_wrapper.discovery.integrations.generate`, no Facts this
slice. There is no dedicated ``rules/fixtures`` directory for integrations
(unlike access/db), so the equality fixture (and the conformance battery's
``repo_setup``) both reuse the exact synthetic repo content already proven
in ``test_integrations.py::test_integration_evidence_on_synthetic_repo``
rather than inventing new content.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper import astgrep, identity
from analysis_wrapper.discovery import integrations
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import IntegrationEvidenceProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id
from provider_conformance import run_provider_conformance


def _populate_synthetic_repo(repo_path: Path) -> None:
    """Mirrors test_integrations.py::test_integration_evidence_on_synthetic_repo:
    a distinctively-named package (``acme``) making an HTTP call, a
    host-fragment constant, and a generic-named package with no HTTP call
    (must NOT be reported). Also the ``repo_setup`` callback shape
    ``run_provider_conformance`` expects for a zero-profile provider."""
    acme = repo_path / "internal" / "handlers" / "acme"
    acme.mkdir(parents=True)
    (acme / "service.go").write_text(
        'package acme\nconst (\n\tscheme = "https"\n\thost = "api.acme.io"\n)\n')
    (acme / "http.go").write_text(
        'package acme\nimport "net/http"\nfunc call(u string) { http.Get(u) }\n')
    common = repo_path / "internal" / "handlers" / "common"
    common.mkdir(parents=True)
    (common / "util.go").write_text("package common\nfunc noop() {}\n")


def test_integration_evidence_provider_conforms(tmp_path):
    run_provider_conformance(
        None, IntegrationEvidenceProvider(), tmp_path=tmp_path,
        repo_setup=_populate_synthetic_repo)


def test_bundled_integration_provider_is_universal_with_no_profile():
    provider = IntegrationEvidenceProvider()
    assert provider.profile_ids == ()
    assert provider.universal is True
    assert provider.capability_id == "integration-evidence"
    assert provider in bundled_registry().providers


# ---------------------------------------------------------------------------
# Unit equality vs. a direct discovery.integrations.generate() call.
# ---------------------------------------------------------------------------


def _synthetic_repo(tmp_path: Path) -> RepoTarget:
    repo_path = tmp_path / "repo"
    _populate_synthetic_repo(repo_path)
    return RepoTarget(repo_id=stable_repo_id(str(repo_path)), path=str(repo_path))


def _run_context(spec: TargetSpec, run_dir: Path, identities) -> RunContext:
    access = ExecutorToolAccess(
        spec, identities, run_dir, "2026-07-23", network_authorized=False)
    return RunContext(
        targets=spec, output_dir=run_dir, scan_date="2026-07-23",
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )


def test_provider_matches_direct_integrations_generate_on_a_synthetic_repo(tmp_path):
    repo = _synthetic_repo(tmp_path)
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)

    result = IntegrationEvidenceProvider().run(context, repo)
    direct = integrations.generate(
        repo.path, repo.repo_id, tier2_exclusions=repo.tier2_exclusions).to_dict()

    # Artifact is the full, unmodified IntegrationEvidence.to_dict() payload.
    artifact_path = run_dir / result.artifact_refs[0].path
    assert json.loads(artifact_path.read_text("utf-8")) == direct

    # No Facts this slice — coverage + artifact only.
    assert result.facts == ()
    assert result.facet_provenance == ()

    if astgrep.available():
        assert direct["available"] is True
        assert "api.acme.io" in {h["value"] for h in direct["host_fragments"]}
        packages = {p["package"] for p in direct["integration_packages"]}
        assert "acme" in packages
        assert "common" not in packages
        assert result.coverage.applicability == "applicable"
        assert result.coverage.status == "complete"
    else:
        assert result.coverage.applicability == "applicable"
        assert result.coverage.status == "unavailable"


def test_coverage_fails_closed_when_astgrep_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(astgrep, "binary", lambda: None)
    astgrep._reset_probe_cache()

    repo = _synthetic_repo(tmp_path)
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)

    result = IntegrationEvidenceProvider().run(context, repo)
    artifact_path = run_dir / result.artifact_refs[0].path
    payload = json.loads(artifact_path.read_text("utf-8"))

    assert payload["available"] is False
    assert any("SKIPPED" in note for note in payload["notes"])
    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == "unavailable"
    assert "SKIPPED" in result.coverage.detail

    astgrep._reset_probe_cache()


def test_provider_writes_no_raw_internal_repo_id(tmp_path):
    repo = _synthetic_repo(tmp_path)
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)

    result = IntegrationEvidenceProvider().run(context, repo)
    artifact_path = run_dir / result.artifact_refs[0].path
    assert repo.repo_id not in artifact_path.read_text("utf-8")
