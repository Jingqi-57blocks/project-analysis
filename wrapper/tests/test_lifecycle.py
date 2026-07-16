"""57B-14: run IDs, stage checkpoints/resume, pointers, staleness."""

from datetime import datetime, timezone

import pytest

from doctor_wrapper import lifecycle
from doctor_wrapper.cli import main
from doctor_wrapper.lifecycle import Pointers, RunState, mint_run_id
from doctor_wrapper.targetspec import TargetSpec


WHEN = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_run_id_labels_plus_never_reuse_suffix():
    heads = ["r-1:abc:no", "r-2:def:no"]
    first = mint_run_id(heads, "en", when=WHEN)
    assert first.startswith("20260716T120000Z-") and len(first.split("-")[1]) == 6
    # Same inputs -> same label; collision -> -2 suffix, then -3.
    taken = {first}
    second = mint_run_id(heads, "en", when=WHEN, exists=lambda r: r in taken)
    assert second == f"{first}-2"
    taken.add(second)
    assert mint_run_id(heads, "en", when=WHEN, exists=lambda r: r in taken) == f"{first}-3"
    # Language changes the digest.
    assert mint_run_id(heads, "zh-CN", when=WHEN) != first


def test_run_state_stages_resume_point(tmp_path, target):
    spec = TargetSpec(repos=[target])
    state = RunState.create("rid-1", "proj-1", spec, when=WHEN)
    assert state.next_stage() == "discovery"
    state.mark("discovery")
    state.mark("signals")
    state.save(tmp_path)
    resumed = RunState.load(tmp_path)
    assert resumed.next_stage() == "findings"
    assert resumed.inspection_only is False
    with pytest.raises(ValueError):
        resumed.mark("nonsense")


def test_dirty_target_makes_run_inspection_only(target):
    target.git.dirty_detail = "yes (1 files: M x.js)"
    state = RunState.create("rid-1", "proj-1", TargetSpec(repos=[target]), when=WHEN)
    assert state.inspection_only is True


def test_staleness_names_moved_repos(tmp_path, target, synthetic_repo):
    state = RunState.create("rid-1", "proj-1", TargetSpec(repos=[target]), when=WHEN)
    assert state.staleness() == []
    (synthetic_repo / "new.js").write_text("1;\n")
    import subprocess
    subprocess.run(["git", "-C", str(synthetic_repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(synthetic_repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "move"], check=True)
    problems = state.staleness()
    assert any("->" in p for p in problems), problems
    assert any(target.repo_id in p for p in problems)


def test_rollback_cascades_to_later_stages(tmp_path, target, capsys):
    spec = TargetSpec(repos=[target])
    state = RunState.create("rid-r", "proj-1", spec, when=WHEN)
    for stage in lifecycle.STAGES:
        state.mark(stage)
    reopened = state.rollback("findings")
    assert reopened == ["findings", "map", "overview"]
    assert state.next_stage() == "findings"
    assert state.stages["discovery"] == "done" and state.stages["signals"] == "done"
    with pytest.raises(ValueError):
        state.rollback("nonsense")
    # CLI surface round-trips through run-state.json (from a fully-done state).
    for stage in lifecycle.STAGES:
        state.mark(stage)
    state.save(tmp_path)
    assert main(["rollback", "--run", str(tmp_path), "--stage", "map"]) == 0
    assert "re-opened: map, overview" in capsys.readouterr().out
    assert RunState.load(tmp_path).next_stage() == "map"


def test_pointers_accept_rules(tmp_path, target):
    spec = TargetSpec(repos=[target])
    pointers = Pointers(tmp_path / "state" / "proj-1")

    incomplete = RunState.create("rid-1", "proj-1", spec, when=WHEN)
    with pytest.raises(ValueError, match="incomplete"):
        pointers.accept(incomplete)

    done = RunState.create("rid-2", "proj-1", spec, when=WHEN)
    for stage in lifecycle.STAGES:
        done.mark(stage)
    pointers.accept(done)
    assert pointers.read()["current"] == "rid-2"

    dirty = RunState.create("rid-3", "proj-1", spec, when=WHEN)
    dirty.inspection_only = True
    for stage in lifecycle.STAGES:
        dirty.mark(stage)
    with pytest.raises(ValueError, match="never be"):
        pointers.accept(dirty)
    assert pointers.read()["current"] == "rid-2"  # unchanged


def test_new_run_default_language_is_zh_cn(tmp_path, synthetic_repo, capsys):
    code = main(["new-run", "--workspace", str(synthetic_repo.parent),
                 "--skill-root", str(tmp_path / "skill")])
    assert code == 0
    run_dir = capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1]
    assert RunState.load(run_dir).language == "zh-CN"


def test_cli_full_lifecycle_flow(tmp_path, target, synthetic_repo, capsys):
    skill_root = tmp_path / "skill"
    code = main(["new-run", "--workspace", str(synthetic_repo.parent),
                 "--skill-root", str(skill_root)])
    assert code == 0
    out = capsys.readouterr().out
    run_dir = out.splitlines()[0].split("run: ", 1)[1]
    assert "next stage: signals" in out

    assert main(["status", "--run", run_dir]) == 0  # fresh
    for stage in ("signals", "findings", "map", "overview"):
        assert main(["mark-stage", "--run", run_dir, "--stage", stage]) == 0
    out = capsys.readouterr().out
    assert "latest_completed" in out

    assert main(["accept", "--run", run_dir]) == 0
    from pathlib import Path
    import json
    state_dirs = list((skill_root / "state").iterdir())
    pointers = json.loads((state_dirs[0] / "pointers.json").read_text())
    assert pointers["current"] and pointers["current"] == pointers["latest_completed"]

    # Move the repo -> status flags staleness with exit 5.
    (synthetic_repo / "later.js").write_text("2;\n")
    import subprocess
    subprocess.run(["git", "-C", str(synthetic_repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(synthetic_repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "later"], check=True)
    assert main(["status", "--run", run_dir]) == 5
