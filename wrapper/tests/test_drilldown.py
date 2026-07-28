"""57B-17: drill-down lifecycle — resolution, staleness, stages, linkage."""

import json
import subprocess
from pathlib import Path

from analysis_wrapper import lifecycle
from analysis_wrapper.cli import main
from analysis_wrapper.lifecycle import DRILLDOWN_STAGES, Pointers, RunState


def _overview(tmp_path, target, capsys, *, complete=True):
    """Mint a real overview run via the CLI; optionally mark it complete."""
    skill_root = tmp_path / "skill"
    assert main(["new-run", "--workspace", str(Path(target.path).parent),
                 "--skill-root", str(skill_root)]) == 0
    run_dir = Path(capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1])
    if complete:
        for stage in ("signals", "findings", "map"):
            assert main(["mark-stage", "--run", str(run_dir), "--stage", stage]) == 0
        (run_dir / "tasks").mkdir(exist_ok=True)
        (run_dir / "consistency-audit.json").write_text('{"status": "passed"}', "utf-8")
        (run_dir / "tasks" / "pipeline-timing.json").write_text('{"complete": true}', "utf-8")
        assert main(["mark-stage", "--run", str(run_dir), "--stage", "overview"]) == 0
        capsys.readouterr()
    return skill_root, run_dir


def test_refuses_without_from_run_or_current(tmp_path, target, capsys):
    skill_root, run_dir = _overview(tmp_path, target, capsys)
    code = main(["new-drilldown", "--skill-root", str(skill_root), "--module", "leave"])
    err = capsys.readouterr().err
    assert code == 2
    assert "no run has been ACCEPTED" in err
    assert run_dir.name in err  # completed runs are listed in the refusal


def test_from_run_mints_linked_drilldown(tmp_path, target, capsys):
    skill_root, run_dir = _overview(tmp_path, target, capsys)
    code = main(["new-drilldown", "--skill-root", str(skill_root),
                 "--module", "leave", "--from-run", run_dir.name])
    assert code == 0
    out = capsys.readouterr().out
    drill_dir = Path(out.splitlines()[0].split("run: ", 1)[1])
    assert drill_dir.parent.name == "drilldown"
    state = RunState.load(drill_dir)
    assert state.ordered_stages() == DRILLDOWN_STAGES
    assert state.next_stage() == "prd"  # resolve marked at mint time
    assert state.analysis_identity["module"] == "leave"
    assert state.analysis_identity["source_overview_run"] == run_dir.name
    link = (drill_dir / "source_overview_run").read_text().splitlines()
    assert link[0] == run_dir.name
    assert state.language == "zh-CN"  # inherited from the source run default


def test_current_pointer_resolution_after_accept(tmp_path, target, capsys):
    skill_root, run_dir = _overview(tmp_path, target, capsys)
    assert main(["accept", "--run", str(run_dir)]) == 0
    capsys.readouterr()
    code = main(["new-drilldown", "--skill-root", str(skill_root), "--module", "leave"])
    assert code == 0
    assert "source: " + run_dir.name in capsys.readouterr().out


def test_incomplete_source_refused(tmp_path, target, capsys):
    skill_root, run_dir = _overview(tmp_path, target, capsys, complete=False)
    code = main(["new-drilldown", "--skill-root", str(skill_root),
                 "--module", "leave", "--from-run", run_dir.name])
    assert code == 2
    assert "incomplete" in capsys.readouterr().err


def test_stale_source_refused_naming_repos(tmp_path, target, synthetic_repo, capsys):
    skill_root, run_dir = _overview(tmp_path, target, capsys)
    (synthetic_repo / "drift.js").write_text("1;\n")
    subprocess.run(["git", "-C", str(synthetic_repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(synthetic_repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "drift"], check=True)
    code = main(["new-drilldown", "--skill-root", str(skill_root),
                 "--module", "leave", "--from-run", run_dir.name])
    err = capsys.readouterr().err
    assert code == 5
    assert "STALE" in err and "->" in err


def test_drilldown_rollback_uses_its_own_stage_order(tmp_path, target, capsys):
    skill_root, run_dir = _overview(tmp_path, target, capsys)
    assert main(["new-drilldown", "--skill-root", str(skill_root),
                 "--module", "leave", "--from-run", run_dir.name]) == 0
    drill_dir = Path(capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1])
    for stage in ("prd", "health"):
        assert main(["mark-stage", "--run", str(drill_dir), "--stage", stage]) == 0
    assert main(["rollback", "--run", str(drill_dir), "--stage", "prd"]) == 0
    capsys.readouterr()
    assert RunState.load(drill_dir).next_stage() == "prd"
    assert RunState.load(drill_dir).stages["resolve"] == "done"
