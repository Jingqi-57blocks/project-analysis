"""Feature-boundaries provider tests for 57B-143."""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper import identity
from analysis_wrapper.discovery import feature_boundaries
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import FeatureBoundariesProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id


def _repo(tmp_path: Path) -> RepoTarget:
    repo = tmp_path / "service"
    (repo / "src").mkdir(parents=True)
    (repo / "test").mkdir()
    (repo / "src" / "worker.ts").write_text(
        "const enabled = process.env.WORKER_ENABLED;\n"
        "setInterval(runWork, 1000);\n"
        "bus.emit('done');\n", encoding="utf-8")
    (repo / "src" / "worker.go").write_text(
        "go runWorker()\n"
        "name := os.Getenv(\"WORKER_NAME\")\n"
        "events.Publish(name)\n", encoding="utf-8")
    (repo / "test" / "worker.spec.ts").write_text("it('runs', () => {});\n", encoding="utf-8")
    return RepoTarget(repo_id=stable_repo_id(str(repo)), path=str(repo))


def _context(tmp_path: Path, repo: RepoTarget) -> RunContext:
    spec = TargetSpec([repo])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    run = tmp_path / "run"
    run.mkdir()
    return RunContext(
        targets=spec, output_dir=run, scan_date="2026-07-28", network_authorized=False,
        provenance={}, tool_access=ExecutorToolAccess(spec, identities, run / "signals", "2026-07-28"),
        identities=identities)


def test_feature_boundary_discovery_records_mechanical_anchors_only(tmp_path):
    repo = _repo(tmp_path)
    result = feature_boundaries.generate(repo)
    assert {row["operation"] for row in result.async_boundaries} >= {
        "setInterval", "emit", "go", "Publish"}
    assert {row["name"] for row in result.configuration_references} == {
        "WORKER_ENABLED", "WORKER_NAME"}
    assert result.test_files == [{"path": "test/worker.spec.ts", "evidence": "test/worker.spec.ts:1"}]
    assert all("value" not in row for row in result.configuration_references)


def test_provider_writes_full_artifact_and_cited_facts(tmp_path):
    repo = _repo(tmp_path)
    context = _context(tmp_path, repo)
    provider = FeatureBoundariesProvider()
    result = provider.run(context, repo)
    artifact = context.output_dir / result.artifact_refs[0].path
    assert json.loads(artifact.read_text("utf-8")) == feature_boundaries.generate(repo).to_dict()
    assert {fact.kind for fact in result.facts} == {
        "async-boundary", "configuration-reference", "test-file"}
    assert all(fact.source_refs for fact in result.facts)
    assert result.coverage.applicability == "unknown"
    assert result.coverage.status == "complete"
    assert provider in bundled_registry().providers
