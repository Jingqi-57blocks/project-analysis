import json
import os
import argparse
from pathlib import Path

from analysis_wrapper.cli import _sweep, main
from analysis_wrapper.status import Status
from analysis_wrapper.targetspec import TargetSpec
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
    path = tmp_path / "targets.json"
    TargetSpec([target], produced_by="cli-test").save(path)
    return path


def test_cli_runs_one_tool_one_repo(monkeypatch, tmp_path, target):
    fake = _fake_scc(tmp_path, "echo '[{\"Name\":\"JavaScript\",\"Code\":1}]'")
    monkeypatch.setenv("PATH", str(fake.parent) + os.pathsep + os.environ["PATH"])
    out = tmp_path / "signals"
    rc = main(["--targets", str(_targets(tmp_path, target)), "--out", str(out),
               "run", "--repo", target.repo_id, "--tool", "scc"])
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
               "run", "--repo", target.repo_id, "--tool", "scc"])
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
               "run", "--repo", target.repo_id, "--tool", "outdated"])
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
    results = _sweep(args, TargetSpec([target]), tmp_path / "signals")
    assert len(results) == 1
    assert results[0].status is Status.SKIPPED
    assert "explicit authorization" in results[0].reason
