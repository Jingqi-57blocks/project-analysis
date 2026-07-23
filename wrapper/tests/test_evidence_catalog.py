"""57B-79: the deterministic evidence catalog projection."""

import json
from pathlib import Path

import pytest

from analysis_wrapper import identity
from analysis_wrapper.evidence import Coverage, Fact, SourceRef
from analysis_wrapper.evidence import catalog
from analysis_wrapper.profiles.contracts import ArtifactRef, CapabilityResult
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id


def _target(path: Path) -> RepoTarget:
    path.mkdir(parents=True, exist_ok=True)
    return RepoTarget(repo_id=stable_repo_id(str(path)), path=str(path.resolve()))


@pytest.fixture
def duplicate_basename_identities(tmp_path):
    """Two repositories sharing a basename ("api") at different paths."""
    workspace = tmp_path / "workspace"
    app_api = _target(workspace / "apps" / "api")
    service_api = _target(workspace / "services" / "api")
    identities = identity.build(
        TargetSpec([app_api, service_api]), workspace_root=workspace,
        project_id=stable_repo_id(str(workspace)))
    return identities, app_api, service_api


def _coverage(**overrides):
    fields = {"applicability": "applicable", "status": "complete",
              "reason_code": "ok", "detail": ""}
    fields.update(overrides)
    return Coverage(**fields)


def _result(repo_id, repository_ref, *, capability_id="route-inventory",
           facts=(), artifact_refs=()):
    return CapabilityResult(
        capability_id=capability_id, provider_id="synthetic-provider", repo_id=repo_id,
        coverage=_coverage(), facts=facts, artifact_refs=artifact_refs)


def test_evidence_catalog_resolves_repository_refs_through_identity_map(
        tmp_path, duplicate_basename_identities):
    identities, app_api, service_api = duplicate_basename_identities
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ref = SourceRef(repository_ref="apps/api", revision="a" * 40,
                    path="internal/h.go", line=3)
    fact = Fact(fact_id="fact:001", kind="route", data={"method": "GET", "path": "/x"},
               source_refs=(ref,))
    results = [
        _result(app_api.repo_id, "apps/api", facts=(fact,)),
        _result(service_api.repo_id, "services/api"),
    ]

    document = catalog.build(results, identities, run_dir)

    serialized = json.dumps(document, ensure_ascii=False)
    internal_ids = {app_api.repo_id, service_api.repo_id}
    assert all(internal_id not in serialized for internal_id in internal_ids)
    scopes = {row["scope"] for row in
             document["capabilities"]["route-inventory"]["items"]}
    assert scopes == {"apps/api", "services/api"}


def test_evidence_catalog_bounds_large_lists_with_disclosure(
        tmp_path, duplicate_basename_identities):
    identities, app_api, _ = duplicate_basename_identities
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    many_facts = tuple(
        Fact(fact_id=f"fact:{index:04d}", kind="route", data={"path": f"/x{index}"})
        for index in range(250)
    )
    result = _result(app_api.repo_id, "apps/api", facts=many_facts)

    document = catalog.build([result], identities, run_dir)

    facts_view = document["capabilities"]["route-inventory"]["items"][0]["facts"]
    assert facts_view["total_count"] == 250
    assert facts_view["included_count"] == 200
    assert facts_view["truncated"] is True


def test_evidence_catalog_deduplicates_and_sorts_source_refs(
        tmp_path, duplicate_basename_identities):
    identities, app_api, _ = duplicate_basename_identities
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ref = SourceRef(repository_ref="apps/api", revision="a" * 40, path="a.go", line=1)
    fact_one = Fact(fact_id="fact:1", kind="route", data={}, source_refs=(ref,))
    fact_two = Fact(fact_id="fact:2", kind="route", data={}, source_refs=(ref,))
    result = _result(app_api.repo_id, "apps/api", facts=(fact_one, fact_two))

    document = catalog.build([result], identities, run_dir)

    source_refs = document["capabilities"]["route-inventory"]["items"][0]["source_refs"]
    assert source_refs["total_count"] == 1
    assert source_refs["items"] == [{"ref": ref.to_string()}]


def test_evidence_catalog_reports_missing_artifact_gracefully(
        tmp_path, duplicate_basename_identities):
    identities, app_api, _ = duplicate_basename_identities
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = _result(app_api.repo_id, "apps/api",
                     artifact_refs=(ArtifactRef("evidence/missing.json"),))

    document = catalog.build([result], identities, run_dir)

    artifacts = document["capabilities"]["route-inventory"]["items"][0]["artifacts"]
    assert artifacts == [{"path": "evidence/missing.json", "kind": "artifact",
                          "exists": False, "sha256": ""}]


def test_evidence_catalog_hashes_present_artifacts(
        tmp_path, duplicate_basename_identities):
    identities, app_api, _ = duplicate_basename_identities
    run_dir = tmp_path / "run"
    (run_dir / "evidence").mkdir(parents=True)
    (run_dir / "evidence" / "present.json").write_text('{"ok": true}', "utf-8")
    result = _result(app_api.repo_id, "apps/api",
                     artifact_refs=(ArtifactRef("evidence/present.json"),))

    document = catalog.build([result], identities, run_dir)

    artifacts = document["capabilities"]["route-inventory"]["items"][0]["artifacts"]
    assert artifacts[0]["exists"] is True
    assert len(artifacts[0]["sha256"]) == 64


def test_evidence_catalog_is_byte_deterministic_and_writes_sanitized_artifact(
        tmp_path, duplicate_basename_identities):
    identities, app_api, service_api = duplicate_basename_identities
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    results = [_result(app_api.repo_id, "apps/api"),
              _result(service_api.repo_id, "services/api")]

    first = catalog.write(run_dir, results, identities).read_bytes()
    second = catalog.write(run_dir, results, identities).read_bytes()

    assert first == second
    assert json.loads(first)["schema_version"] == catalog.SCHEMA_VERSION


def test_evidence_catalog_keeps_independent_results_per_facet(
        tmp_path, duplicate_basename_identities):
    """Mixed-language target: several independent CapabilityResults, no merge."""
    identities, app_api, service_api = duplicate_basename_identities
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    js_fact = Fact(fact_id="fact:js", kind="dependency", data={"name": "left-pad"})
    go_fact = Fact(fact_id="fact:go", kind="dependency", data={"name": "gorm"})
    results = [
        _result(app_api.repo_id, "apps/api", capability_id="dependency-map",
               facts=(js_fact,)),
        _result(app_api.repo_id, "apps/api", capability_id="dependency-map",
               facts=(go_fact,)),
    ]

    document = catalog.build(results, identities, run_dir)

    items = document["capabilities"]["dependency-map"]["items"]
    assert len(items) == 2
    all_data = {tuple(sorted(row["data"].items()))
               for entry in items for row in entry["facts"]["items"]}
    assert all_data == {(("name", "gorm"),), (("name", "left-pad"),)}
