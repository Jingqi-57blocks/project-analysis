import json
import os
import argparse
from pathlib import Path

from analysis_wrapper.cli import PROVIDER_OWNED_SIGNAL_TOOLS, _record_summary, _sweep, main
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
    # clobber its own prior output on a re-run. The standalone subcommand now
    # drives the provider loop + assembler (57B-81 PR2); stub both so this test
    # stays about the CLI's layering/resume contract, not the providers' own
    # behavior (which has its own dedicated coverage elsewhere).
    run = tmp_path / "run"
    run.mkdir()
    TargetSpec([target], produced_by="cli-test").save(run / "targets.json")

    def stub_run_capability_providers(spec, out, scan_date, *, capability_id, allow_network):
        pass

    def stub_assemble(out, scan_date):
        imports = Path(out) / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        (imports / "depmap-coverage.json").write_text("{}\n")
        return DepMapReport(scan_date=scan_date, repos=[])

    monkeypatch.setattr("analysis_wrapper.cli._run_capability_providers",
                        stub_run_capability_providers)
    monkeypatch.setattr(dm_emit, "assemble", stub_assemble)
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


def test_sweep_exclude_tool_names_is_additive_and_standalone_path_ignores_it(
        tmp_path, target):
    """57B-82 A2: ``exclude_tool_names`` (only ``cli._prepare_overview``
    passes it, as ``PROVIDER_OWNED_SIGNAL_TOOLS``) strips exactly those tool
    names from the sweep's own selection; omitting it (every OTHER call
    site, including the standalone ``sweep``/``run`` CLI subcommands) keeps
    running every applicable tool directly, unchanged."""
    args = argparse.Namespace(since="2020-01-01", coupling_sample_cap=0,
                              scan_date="2026-07-24", include_network=False)
    spec = TargetSpec([target])
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))

    excluded = _sweep(args, spec, tmp_path / "signals-a", identities,
                      exclude_tool_names=PROVIDER_OWNED_SIGNAL_TOOLS)
    assert "git-history" not in {r.tool for r in excluded}

    full = _sweep(args, spec, tmp_path / "signals-b", identities)
    assert "git-history" in {r.tool for r in full}


def test_prepare_overview_signals_merge_includes_provider_owned_tools(tmp_path, target):
    """The PRODUCT of the A2 restructuring: run-summary.json still carries a
    row for every signal tool that ran this pass, including git-history/
    outdated — now executed via their own capability providers rather than
    the sweep — merged in by ``cli._prepare_overview``, not silently
    dropped. Exercises the REAL sweep + REAL provider stage (not stubbed),
    proving the merge end to end on a genuine git+JS fixture repo (the
    shared ``target``/``synthetic_repo`` conftest fixture — real commit,
    real head, ``language.javascript`` facet so dependency-risk selects
    ``outdated`` too)."""
    from analysis_wrapper.profiles.execution import run_provider_stage

    spec = TargetSpec([target])
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))
    run_dir = tmp_path / "run"
    signals = run_dir / "signals"
    signals.mkdir(parents=True)

    args = argparse.Namespace(since="2020-01-01", coupling_sample_cap=0,
                              scan_date="2026-07-24", include_network=False)
    sweep_results = _sweep(args, spec, signals, identities,
                          exclude_tool_names=PROVIDER_OWNED_SIGNAL_TOOLS)
    assert not any(r.tool in PROVIDER_OWNED_SIGNAL_TOOLS for r in sweep_results)

    collected: list = []
    run_provider_stage(
        run_dir, spec, identities, scan_date="2026-07-24", network_authorized=False,
        provenance={"preparation": {"history_since": "2020-01-01",
                                    "coupling_sample_cap": 0}},
        signal_results=collected)
    assert {"git-history", "outdated"} <= {r.tool for r in collected}

    combined = sweep_results + collected
    _record_summary(signals, combined)
    payload = json.loads((signals / "run-summary.json").read_text("utf-8"))
    tools = {row["tool"] for row in payload["signals"]}
    assert "git-history" in tools
    assert "outdated" in tools
    # Every (tool, repository_ref) pair appears exactly once — no duplicate
    # execution between the sweep and the providers.
    keys = [(row["tool"], row["repository_ref"]) for row in payload["signals"]]
    assert len(keys) == len(set(keys))


def test_run_provider_stage_resumes_over_real_signal_artifacts_without_crashing(
        tmp_path, target):
    """The highest-risk scenario 57B-82 A2 introduces: signal-tool artifacts
    are write-once (``run_tool``'s own collision refusal), but the provider
    stage runs UNCONDITIONALLY on every ``prepare-overview`` pass — including
    one that resumes an already-signals-complete run. A SECOND
    ``run_provider_stage`` call over the SAME real ``run/signals/`` output
    (git-history executes for real; dependency-risk selects ``outdated`` for
    this JS fixture) must not crash, must keep reporting the SAME
    executions/failed counts and the SAME per-row coverage, and must leave
    evidence-catalog.json byte-identical (it is built purely from
    CapabilityResult.coverage/facts/artifact_refs, none of which differ
    between a fresh and a resumed pass — see
    ``providers.py``'s ``_run_or_reuse_signal`` docstring).

    provider-execution.json's git-history/dependency-risk ROWS are NOT
    expected to be fully byte-identical across the two calls: each row's
    ``tools`` field honestly discloses whether THIS pass actually called
    ``context.tool_access.execute(...)`` — populated on the fresh pass,
    empty on the resumed one, since reusing an existing manifest never
    touches tool_access. Every OTHER field of every row (including
    ``coverage``) is identical — asserted explicitly below, since that is
    the part that actually matters."""
    from analysis_wrapper.evidence import catalog
    from analysis_wrapper.profiles.execution import FILENAME, run_provider_stage

    spec = TargetSpec([target])
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))
    run_dir = tmp_path / "run"
    (run_dir / "signals").mkdir(parents=True)
    kwargs = dict(
        scan_date="2026-07-24", network_authorized=False,
        provenance={"preparation": {"history_since": "2020-01-01",
                                    "coupling_sample_cap": 0}})

    summary_one = run_provider_stage(run_dir, spec, identities, **kwargs)
    rows_one = json.loads((run_dir / FILENAME).read_text("utf-8"))["executions"]
    catalog_one = (run_dir / catalog.FILENAME).read_bytes()

    summary_two = run_provider_stage(run_dir, spec, identities, **kwargs)  # must not raise
    rows_two = json.loads((run_dir / FILENAME).read_text("utf-8"))["executions"]
    catalog_two = (run_dir / catalog.FILENAME).read_bytes()

    assert summary_one == summary_two
    assert catalog_one == catalog_two
    assert [{k: v for k, v in row.items() if k != "tools"} for row in rows_one] == \
        [{k: v for k, v in row.items() if k != "tools"} for row in rows_two]
    resumed_providers = {"git-history", "dependency-risk"}
    for row in rows_two:
        if row["provider_id"] in resumed_providers:
            assert row["tools"] == [], (
                "a resumed pass must not re-invoke tool_access for a provider "
                "that reused an existing signal manifest")


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

    def sweep(_args, _spec, out, _identities, **_kwargs):
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

    def callgraph_assemble(out, scan_date):
        (Path(out) / "callgraph").mkdir()
        (Path(out) / "callgraph" / f"{repository.artifact_key}.jsonl").write_text("", "utf-8")
        report = CoverageReport(scan_date=scan_date, repos=[RepoCoverage(
            repository_ref=repository.reference, lang="js", status="complete",
            tool="fixture", candidates_by_ext={}, analyzed_by_ext={})])
        (Path(out) / "callgraph-coverage.json").write_text(report.to_json(), "utf-8")
        return report

    def depmap_assemble(out, scan_date):
        imports = Path(out) / "imports"
        imports.mkdir()
        map_name = f"{repository.artifact_key}.depcruise.json"
        (imports / map_name).write_text('{"modules": []}\n', "utf-8")
        report = DepMapReport(scan_date=scan_date, repos=[RepoDepCoverage(
            repository_ref=repository.reference, lane="js", status="complete",
            tool="fixture", map_file=map_name, units=0)])
        (imports / "depmap-coverage.json").write_text(report.to_json(), "utf-8")
        return report

    def fake_run_provider_stage(run_dir, spec, identities, *, scan_date,
                                network_authorized, provenance, signal_results=None):
        # The provider loop itself has its own dedicated coverage (57B-78/86)
        # plus the real providers' own coverage (57B-81/82 fixtures); here it
        # must not require a real go/node toolchain, so it's stubbed to the
        # same behavior-neutral no-op the empty bundled registry gave before
        # 57B-81 — this test is only about canonical paths + resume.
        return {"executions": 0, "failed": 0}

    monkeypatch.setattr("analysis_wrapper.callgraph.emit.assemble", callgraph_assemble)
    monkeypatch.setattr("analysis_wrapper.depmap.emit.assemble", depmap_assemble)
    monkeypatch.setattr("analysis_wrapper.profiles.execution.run_provider_stage",
                        fake_run_provider_stage)
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
