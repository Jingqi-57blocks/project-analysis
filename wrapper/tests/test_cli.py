import json
import os
import argparse
from pathlib import Path

from analysis_wrapper.cli import _sweep, main
from analysis_wrapper.callgraph.contract import CoverageReport, RepoCoverage
from analysis_wrapper.depmap import emit as dm_emit
from analysis_wrapper.depmap.contract import DepMapReport, RepoDepCoverage
from analysis_wrapper.executor import SignalResult
from analysis_wrapper import identity, lifecycle
from analysis_wrapper.status import Status
from analysis_wrapper.targetspec import TargetSpec, stable_repo_id
from analysis_wrapper.tooldefs import ToolDef


def _fake_scc(tmp_path: Path, body: str) -> Path:
    binary = tmp_path / "bin" / "scc"
    binary.parent.mkdir()
    binary.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'scc version 3.7.0'; exit 0; fi\n"
        + body + "\n"
    )
    binary.chmod(0o755)
    return binary


def _targets(tmp_path: Path, target) -> Path:
    stage1 = tmp_path / "stage1"
    spec = TargetSpec([target], produced_by="cli-test")
    mapping = identity.build(
        spec, workspace_root=tmp_path,
        project_id=stable_repo_id(str(tmp_path)))
    stage1.mkdir()
    spec.save(stage1 / "targets.json")
    identity.write_mapping(stage1, mapping)
    (stage1 / "discovery-report.json").write_text(json.dumps({
        "schema_version": "2.0.0",
        "project_ref": mapping.project.reference,
    }), "utf-8")
    return stage1 / "targets.json"


def _reference(target) -> str:
    return Path(target.path).name


def test_cli_runs_one_tool_one_repo(monkeypatch, tmp_path, target):
    fake = _fake_scc(tmp_path, "echo '[{\"Name\":\"JavaScript\",\"Code\":1}]'")
    monkeypatch.setenv("PATH", str(fake.parent) + os.pathsep + os.environ["PATH"])
    out = tmp_path / "signals"
    rc = main(["--targets", str(_targets(tmp_path, target)), "--out", str(out),
               "run", "--repo", _reference(target), "--tool", "scc"])
    assert rc == 0
    summary = json.loads((out / "run-summary.json").read_text())
    assert summary["aggregate_status"] == "complete"
    assert summary["signals"][0]["view"].endswith(".view.txt")
    assert "/" not in summary["signals"][0]["view"]


def test_killed_tool_makes_cli_exit_three(monkeypatch, tmp_path, target):
    fake = _fake_scc(tmp_path, "kill -9 $$")
    monkeypatch.setenv("PATH", str(fake.parent) + os.pathsep + os.environ["PATH"])
    out = tmp_path / "signals"
    rc = main(["--targets", str(_targets(tmp_path, target)), "--out", str(out),
               "run", "--repo", _reference(target), "--tool", "scc"])
    assert rc == 3
    summary = json.loads((out / "run-summary.json").read_text())
    assert summary["aggregate_status"] == "failed"
    manifest = next(out.glob("*.manifest.json"))
    assert json.loads(manifest.read_text())["status"] == "failed"


def test_single_network_tool_requires_explicit_opt_in(tmp_path, target):
    # go-list/staticcheck are offline-first (network=False) — the network
    # opt-in gate now applies to the package lane (outdated) and osv.
    (Path(target.path) / "package.json").write_text("{}\n")
    out = tmp_path / "signals"
    rc = main(["--targets", str(_targets(tmp_path, target)), "--out", str(out),
               "run", "--repo", _reference(target), "--tool", "outdated"])
    assert rc == 0  # skipped is disclosed but non-fatal by the status contract
    summary = json.loads((out / "run-summary.json").read_text())
    assert summary["signals"][0]["status"] == "skipped"
    assert "explicit authorization" in summary["signals"][0]["reason"]


def test_cli_refuses_existing_output_without_overwriting(tmp_path, target):
    out = tmp_path / "signals"
    out.mkdir()
    marker = out / "keep.txt"
    marker.write_text("keep\n")
    rc = main(["--targets", str(_targets(tmp_path, target)), "--out", str(out),
               "run", "--repo", target.repo_id, "--tool", "scc"])
    assert rc == 2
    assert marker.read_text() == "keep\n"


def test_dependency_map_layers_into_existing_run_dir(monkeypatch, tmp_path, target):
    # Discovery already created the run dir + targets.json; the dependency-map
    # stage must layer into it (not refuse it like run/sweep do), then refuse to
    # clobber its own prior output on a re-run.
    run = tmp_path / "run"
    run.mkdir()
    TargetSpec([target], produced_by="cli-test").save(run / "targets.json")

    def stub_run_depmap(spec, out, scan_date, allow_network=False):
        imports = Path(out) / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        (imports / "depmap-coverage.json").write_text("{}\n")
        return DepMapReport(scan_date=scan_date, repos=[])

    monkeypatch.setattr(dm_emit, "run_depmap", stub_run_depmap)
    rc = main(["--targets", str(run / "targets.json"), "--out", str(run),
               "dependency-map"])
    assert rc == 0
    assert (run / "imports" / "depmap-coverage.json").is_file()

    # Re-running the same stage into the same dir refuses (stage marker present).
    rc2 = main(["--targets", str(run / "targets.json"), "--out", str(run),
                "dependency-map"])
    assert rc2 == 2


def test_sweep_records_unauthorized_network_lanes_as_skipped(monkeypatch, tmp_path, target):
    definition = ToolDef(
        name="network-fixture", binary="bash", network=True,
        version_argv=["bash", "--version"],
        argv_builder=lambda _target: ["bash", "-c", "echo should-not-run"],
    )
    monkeypatch.setattr("analysis_wrapper.cli.local_tools", lambda _target: [])
    monkeypatch.setattr("analysis_wrapper.cli.network_tools", lambda _target: [definition])
    args = argparse.Namespace(since="2024-01-01", include_network=False,
                              scan_date="2026-07-16")
    spec = TargetSpec([target])
    identities = identity.build(
        spec, workspace_root=tmp_path,
        project_id=stable_repo_id(str(tmp_path)))
    results = _sweep(
        args, spec, tmp_path / "signals", identities)
    assert len(results) == 1
    assert results[0].status is Status.SKIPPED
    assert "explicit authorization" in results[0].reason


def test_prepare_overview_owns_canonical_paths_and_resumes(monkeypatch, tmp_path, target):
    from analysis_wrapper import run_provenance
    project_id = stable_repo_id(str(tmp_path))
    run = tmp_path / "skill" / "output" / "sample" / "overview" / "run-1"
    run.mkdir(parents=True)
    TargetSpec([target], produced_by="cli-test").save(run / "targets.json")
    discovery = {
        "project_id": project_id, "workspace_root": str(tmp_path),
        "repos": [{"repo_id": target.repo_id,
                   "provenance": {"is_git": True, "head": target.git.head},
                   "stacks": {"stacks": ["js"], "frameworks": [],
                              "analysis_roots": [], "evidence": []},
                   "package_manager": {"name": "npm", "lockfile": "", "evidence": ""},
                   "module_signals": {"folders": [], "routes": [], "tables": [],
                                      "api_configs": [], "notes": []},
                   "table_evidence": {"available": False, "tables": {}, "notes": [],
                                      "sql_coverage": {}},
                   "access_model": {"available": False},
                   "integration_evidence": {"available": False},
                   "deployable_units": {"status": "unknown", "units": [], "notes": []}}],
        "not_targeted": [], "reduced_coverage_targets": [],
        "route_inventory": None, "ui_route_linkage": None,
        "role_catalog_by_repo": {},
    }
    spec = TargetSpec([target], produced_by="cli-test")
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=project_id)
    identity.write_mapping(run, identities)
    (run / "discovery-report.json").write_text(json.dumps(
        identity.externalize_discovery_report(discovery, identities)), "utf-8")
    repository = identities.repository(target.repo_id)
    state = lifecycle.RunState.create("run-1", project_id, TargetSpec([target]))
    state.mark("discovery")
    state.save(run)
    run_provenance.write(run, run_provenance.create_document(
        TargetSpec([target]), analyzer_root=tmp_path,
        language="en", analyzed_at=state.analyzed_at))

    def sweep(_args, _spec, out, _identities):
        view = Path(out) / f"fixture-{repository.artifact_key}.view.txt"
        view.write_text("items: 0\n", "utf-8")
        (Path(out) / f"fixture-{repository.artifact_key}.manifest.json").write_text(json.dumps({
            "schema_version": "2.0.0",
            "tool": "fixture", "status": "complete",
            "repos": [{"repository_ref": repository.reference}],
        }), "utf-8")
        return [SignalResult(
            tool="fixture", repo_id=target.repo_id,
            repository_ref=repository.reference, status=Status.COMPLETE,
            reason="", manifest=None, raw_path=None, view_path=view)]
    monkeypatch.setattr("analysis_wrapper.cli._sweep", sweep)

    def callgraph(_spec, out, scan_date, allow_network=False, identities=None):
        (Path(out) / "callgraph").mkdir()
        (Path(out) / "callgraph" / f"{repository.artifact_key}.jsonl").write_text("", "utf-8")
        report = CoverageReport(scan_date=scan_date, repos=[RepoCoverage(
            repository_ref=repository.reference, lang="js", status="complete",
            tool="fixture", candidates_by_ext={}, analyzed_by_ext={})])
        (Path(out) / "callgraph-coverage.json").write_text(report.to_json(), "utf-8")
        return report

    def depmap(_spec, out, scan_date, allow_network=False, identities=None):
        imports = Path(out) / "imports"
        imports.mkdir()
        map_name = f"{repository.artifact_key}.depcruise.json"
        (imports / map_name).write_text('{"modules": []}\n', "utf-8")
        report = DepMapReport(scan_date=scan_date, repos=[RepoDepCoverage(
            repository_ref=repository.reference, lane="js", status="complete",
            tool="fixture", map_file=map_name, units=0)])
        (imports / "depmap-coverage.json").write_text(report.to_json(), "utf-8")
        return report

    monkeypatch.setattr("analysis_wrapper.callgraph.emit.run_callgraph", callgraph)
    monkeypatch.setattr("analysis_wrapper.depmap.emit.run_depmap", depmap)
    argv = ["--scan-date", "2026-07-20", "prepare-overview", "--run", str(run)]
    assert main(argv) == 0
    expected = {"signals/run-summary.json", "callgraph-coverage.json",
                "imports/depmap-coverage.json", "system-model.json",
                "capabilities.json", "module-candidates.json",
                "coverage-summary.md", "workspace-metrics.json", "synthesis-input.json",
                "consistency-audit.json"}
    assert all((run / rel).exists() for rel in expected)
    assert not (run / "signals" / "callgraph-coverage.json").exists()
    first = (run / "synthesis-input.json").read_bytes()
    assert main(argv) == 0
    assert (run / "synthesis-input.json").read_bytes() == first
    assert lifecycle.RunState.load(run).stages["signals"] == "done"
