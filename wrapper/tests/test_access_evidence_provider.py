"""57B-84: the access-evidence capability provider.

Two concerns, mirroring ``test_datastore_evidence_provider.py``'s structure:

1. ``AccessEvidenceProvider`` passes the shared conformance battery — but
   unlike the datastore/callgraph/depmap providers, it has NO dedicated
   profile at all (``profile_ids == ()``), so the battery entry point is
   ``run_universal_provider_conformance`` (57B-84), not the profile-linked
   ``run_provider_conformance``.

2. The provider is a THIN adapter over
   :func:`analysis_wrapper.discovery.access_model.generate` (unmodified):
   its coverage/artifact must be provably equivalent to a direct call —
   exercised against the existing ``rules/fixtures/access`` fixture (role
   catalog, authz checks, middleware, contextual identity, casbin policy)
   rather than inventing new fixtures. This slice emits NO Facts (coverage +
   artifact only).

Note: like ``DatastoreEvidenceProvider``, this provider never touches
``context.tool_access`` — ``access_model.generate()`` has no such seam (it
calls ``astgrep`` directly) — so its execution-record "tools" log is always
empty and it cannot honor ``network_authorized``. Intentional, not a gap.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper import astgrep, identity
from analysis_wrapper.discovery import access_model
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import AccessEvidenceProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id
from provider_conformance import run_universal_provider_conformance

FIXACCESS = astgrep.RULES_DIR / "fixtures" / "access"


def test_access_evidence_provider_conforms(tmp_path):
    run_universal_provider_conformance(AccessEvidenceProvider(), tmp_path=tmp_path)


def test_bundled_access_provider_is_universal_with_no_profile():
    provider = AccessEvidenceProvider()
    assert provider.profile_ids == ()
    assert provider.universal is True
    assert provider.capability_id == "access-model"
    assert provider in bundled_registry().providers


# ---------------------------------------------------------------------------
# Unit equality vs. a direct discovery.access_model.generate() call.
# ---------------------------------------------------------------------------


def _fixaccess_repo() -> RepoTarget:
    return RepoTarget(repo_id=stable_repo_id(str(FIXACCESS)), path=str(FIXACCESS))


def _run_context(spec: TargetSpec, run_dir: Path, identities) -> RunContext:
    access = ExecutorToolAccess(
        spec, identities, run_dir, "2026-07-23", network_authorized=False)
    return RunContext(
        targets=spec, output_dir=run_dir, scan_date="2026-07-23",
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )


def test_provider_matches_direct_access_model_generate_on_the_fixture(tmp_path):
    repo = _fixaccess_repo()
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=FIXACCESS, project_id=stable_repo_id(str(FIXACCESS)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)

    result = AccessEvidenceProvider().run(context, repo)
    direct = access_model.generate(
        str(FIXACCESS), repo.repo_id, tier2_exclusions=repo.tier2_exclusions).to_dict()

    # Artifact is the full, unmodified AccessModel.to_dict() payload.
    artifact_path = run_dir / result.artifact_refs[0].path
    assert json.loads(artifact_path.read_text("utf-8")) == direct

    # No Facts this slice — coverage + artifact only.
    assert result.facts == ()
    assert result.facet_provenance == ()

    if astgrep.available():
        assert direct["available"] is True
        assert result.coverage.applicability == "applicable"
        assert result.coverage.status == "complete"
    else:
        assert result.coverage.applicability == "applicable"
        assert result.coverage.status == "unavailable"


def test_coverage_fails_closed_when_astgrep_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(astgrep, "binary", lambda: None)
    astgrep._reset_probe_cache()

    repo = _fixaccess_repo()
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=FIXACCESS, project_id=stable_repo_id(str(FIXACCESS)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)

    result = AccessEvidenceProvider().run(context, repo)
    artifact_path = run_dir / result.artifact_refs[0].path
    payload = json.loads(artifact_path.read_text("utf-8"))

    assert payload["available"] is False
    assert any("SKIPPED" in note for note in payload["notes"])
    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == "unavailable"
    assert "SKIPPED" in result.coverage.detail

    astgrep._reset_probe_cache()


def test_provider_writes_no_raw_internal_repo_id(tmp_path):
    repo = _fixaccess_repo()
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=FIXACCESS, project_id=stable_repo_id(str(FIXACCESS)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)

    result = AccessEvidenceProvider().run(context, repo)
    artifact_path = run_dir / result.artifact_refs[0].path
    assert repo.repo_id not in artifact_path.read_text("utf-8")
