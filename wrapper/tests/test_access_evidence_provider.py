"""57B-84: the access-evidence capability provider.

Two concerns, mirroring ``test_datastore_evidence_provider.py``'s structure:

1. ``AccessEvidenceProvider`` passes the shared conformance battery via
   ``run_provider_conformance``'s zero-profile ``profile=None`` shape
   (``profiles/registry.py`` and ``tests/provider_conformance.py`` both
   gained a carve-out for exactly this in 57B-82 A1 — a ``universal``
   provider with empty ``profile_ids``, since no detected facet predicts
   access-control-shaped code's presence): ``test_conformance.py`` already
   pins that MECHANISM generically with a synthetic provider; the test below
   proves the REAL ``AccessEvidenceProvider`` passes it too, using
   ``repo_setup`` to populate the battery's own repo directory with the
   existing ``rules/fixtures/access`` content.

2. The provider is a THIN adapter over
   :func:`analysis_wrapper.discovery.access_model.generate` (unmodified):
   its coverage/artifact must be provably equivalent to a direct call —
   exercised against the same ``rules/fixtures/access`` fixture (role
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
import shutil
from pathlib import Path

from analysis_wrapper import astgrep, identity
from analysis_wrapper.discovery import access_model
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import AccessEvidenceProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id
from provider_conformance import run_provider_conformance

FIXACCESS = astgrep.RULES_DIR / "fixtures" / "access"


def _populate_from_fixaccess(repo: Path) -> None:
    """``repo_setup`` callback (the zero-profile shape
    ``run_provider_conformance`` expects): copy the existing
    ``rules/fixtures/access`` content into the battery's own prepared repo
    directory rather than inventing new fixture content."""
    shutil.copytree(FIXACCESS, repo, dirs_exist_ok=True)


def test_access_evidence_provider_conforms_via_zero_profile_battery_shape(tmp_path):
    run_provider_conformance(
        None, AccessEvidenceProvider(), tmp_path=tmp_path,
        repo_setup=_populate_from_fixaccess)


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
