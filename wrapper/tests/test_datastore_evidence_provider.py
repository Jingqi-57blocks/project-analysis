"""57B-80 PR2: the datastore-evidence capability provider.

Two concerns, mirroring ``test_lane_providers.py``'s structure:

1. ``DatastoreEvidenceProvider`` passes the shared conformance battery against
   its REAL bundled profiles — one test per DISTINCT fingerprint kind among
   the five supported ``datastore.*`` profiles (``package-dependency`` via
   ``datastore.sequelize``, ``go-require`` via ``datastore.gorm``,
   ``source-extension`` via ``datastore.sql``); mongodb-native/mongoose share
   the exact same package-dependency code path as sequelize, so a dedicated
   conformance test for each would be redundant.

2. The provider is a THIN adapter over
   :func:`analysis_wrapper.discovery.tables.generate` (unmodified): its
   facts/coverage/artifact must be provably equivalent to a direct call —
   exercised against the existing rich ``rules/fixtures/db`` fixture (mixed
   Sequelize/Mongo/GORM/raw-SQL content, including the Go typed-constant
   registry join) rather than inventing new fixtures.

Note: unlike the four callgraph/depmap providers, ``DatastoreEvidenceProvider``
never touches ``context.tool_access`` — ``tables.generate()`` has no such seam
(it calls ``astgrep``/``sqlglot`` directly) — so its execution-record "tools"
log is always empty and it cannot honor ``network_authorized``. This is an
intentional consequence of wrapping the producer UNCHANGED, not a gap.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper import astgrep, identity
from analysis_wrapper.datastore_coverage import classify as classify_data_model
from analysis_wrapper.discovery import tables
from analysis_wrapper.evidence.coverage import from_datastore_coverage
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import DatastoreEvidenceProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id
from provider_conformance import run_provider_conformance

FIXDB = astgrep.RULES_DIR / "fixtures" / "db"

_SEQUELIZE = bundled_registry().profile("datastore.sequelize")
_GORM = bundled_registry().profile("datastore.gorm")
_MONGODB_NATIVE = bundled_registry().profile("datastore.mongodb-native")
_MONGOOSE = bundled_registry().profile("datastore.mongoose")
_SQL = bundled_registry().profile("datastore.sql")
_ALL_DATASTORE_PROFILES = (_SEQUELIZE, _GORM, _MONGODB_NATIVE, _MONGOOSE, _SQL)


def _others(primary) -> tuple:
    return tuple(profile for profile in _ALL_DATASTORE_PROFILES if profile is not primary)


# ---------------------------------------------------------------------------
# Conformance — one distinct fingerprint kind per test.
# ---------------------------------------------------------------------------


def test_datastore_evidence_provider_conforms_via_sequelize_profile(tmp_path):
    run_provider_conformance(_SEQUELIZE, DatastoreEvidenceProvider(), tmp_path=tmp_path,
                             extra_profiles=_others(_SEQUELIZE))


def test_datastore_evidence_provider_conforms_via_gorm_profile(tmp_path):
    run_provider_conformance(_GORM, DatastoreEvidenceProvider(), tmp_path=tmp_path,
                             extra_profiles=_others(_GORM))


def test_datastore_evidence_provider_conforms_via_sql_profile(tmp_path):
    run_provider_conformance(_SQL, DatastoreEvidenceProvider(), tmp_path=tmp_path,
                             extra_profiles=_others(_SQL))


def test_bundled_datastore_provider_matches_its_five_supported_profiles():
    provider = DatastoreEvidenceProvider()
    assert set(provider.profile_ids) == {profile.profile_id
                                         for profile in _ALL_DATASTORE_PROFILES}
    for profile in _ALL_DATASTORE_PROFILES:
        assert provider.capability_id in profile.capability_ids


# ---------------------------------------------------------------------------
# Unit equality vs. a direct discovery.tables.generate() call.
# ---------------------------------------------------------------------------


def _fixdb_repo() -> RepoTarget:
    return RepoTarget(repo_id=stable_repo_id(str(FIXDB)), path=str(FIXDB))


def _run_context(spec: TargetSpec, run_dir: Path, identities) -> RunContext:
    access = ExecutorToolAccess(
        spec, identities, run_dir, "2026-07-23", network_authorized=False)
    return RunContext(
        targets=spec, output_dir=run_dir, scan_date="2026-07-23",
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )


def test_provider_matches_direct_tables_generate_on_the_mixed_fixture(tmp_path):
    repo = _fixdb_repo()
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=FIXDB, project_id=stable_repo_id(str(FIXDB)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)

    result = DatastoreEvidenceProvider().run(context, repo)
    direct = tables.generate(
        str(FIXDB), repo.repo_id, tier2_exclusions=repo.tier2_exclusions).to_dict()

    # Artifact is the full, unmodified TableEvidence.to_dict() payload.
    artifact_path = run_dir / result.artifact_refs[0].path
    assert json.loads(artifact_path.read_text("utf-8")) == direct

    # One fact per distinct table, no duplicates.
    fact_names = [fact.data["physical_name"] for fact in result.facts]
    assert sorted(fact_names) == sorted(direct["tables"])
    assert len(fact_names) == len(set(fact_names))

    # Each fact's access buckets + store metadata mirror the direct result.
    for fact in result.facts:
        name = fact.data["physical_name"]
        buckets = direct["tables"][name]
        assert fact.data["access"] == {
            access: list(sites) for access, sites in sorted(buckets.items())}
        meta = direct["store_metadata"][name]
        assert fact.data["kind"] == meta["kind"]
        assert fact.data["families"] == meta["families"]
        assert fact.data["logical_names"] == meta["logical_names"]

    # Coverage is exactly the tested classify()/from_datastore_coverage() bridge,
    # fed the same single-repo block capabilities.py/system_model already build.
    expected_coverage = from_datastore_coverage(classify_data_model([{
        "repository_ref": identities.reference_for(repo.repo_id),
        "table_evidence": direct,
    }]))
    assert result.coverage == expected_coverage

    # Go typed-constant-registry join (orm.go/access.go in the fixture): when
    # ast-grep is available, the "widgets" write resolved through the
    # constant registry must be cited back to access.go, not lost.
    if astgrep.available():
        widgets = next(f for f in result.facts if f.data["physical_name"] == "widgets")
        assert any(ref.path.endswith("access.go") for ref in widgets.source_refs)


def test_sql_coverage_fails_closed_when_sqlglot_absent(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sqlglot":
            raise ImportError("simulated missing sqlglot")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    repo = _fixdb_repo()
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=FIXDB, project_id=stable_repo_id(str(FIXDB)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)

    result = DatastoreEvidenceProvider().run(context, repo)
    artifact_path = run_dir / result.artifact_refs[0].path
    payload = json.loads(artifact_path.read_text("utf-8"))

    assert payload["sql_coverage"]["available"] is False
    assert "NOT parsed" in payload["sql_coverage"]["reason"]
    assert result.coverage.applicability == "applicable"
    assert result.coverage.status in {"partial", "unavailable"}


def test_provider_writes_no_source_ref_with_a_raw_internal_repo_id(tmp_path):
    """The written artifact and every fact's citations must resolve through
    the run's IdentityMap, never leak the raw internal repo_id (mirrors the
    same pin the shared battery already applies to provider-execution.json /
    evidence-catalog.json)."""
    repo = _fixdb_repo()
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=FIXDB, project_id=stable_repo_id(str(FIXDB)))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = _run_context(spec, run_dir, identities)

    result = DatastoreEvidenceProvider().run(context, repo)
    for fact in result.facts:
        for ref in fact.source_refs:
            assert ref.repository_ref == identities.reference_for(repo.repo_id)
            assert repo.repo_id != ref.repository_ref
