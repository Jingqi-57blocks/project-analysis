"""57B-14: run IDs, stage checkpoints/resume, pointers, staleness."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from analysis_wrapper import lifecycle, paths
from analysis_wrapper.cli import main
from analysis_wrapper.lifecycle import Pointers, RunState, mint_run_id
from analysis_wrapper.targetspec import TargetSpec


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


def test_custom_run_id_keeps_digest_and_never_reuse_suffix():
    heads = ["r-1:abc:no"]
    first = mint_run_id(heads, "en", label="low-effort", when=WHEN)
    assert first.startswith("low-effort-")
    assert len(first.rsplit("-", 1)[1]) == 6
    assert mint_run_id(
        heads, "en", label="low-effort", when=WHEN,
        exists=lambda run_id: run_id == first,
    ) == f"{first}-2"


@pytest.mark.parametrize("label", ["../escape", "has spaces", ".hidden", "bad-"])
def test_custom_run_id_rejects_non_portable_labels(label):
    with pytest.raises(ValueError, match="invalid --run-id label"):
        mint_run_id(["r-1:abc:no"], "en", label=label, when=WHEN)


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
    from analysis_wrapper import run_provenance
    TargetSpec([target]).save(tmp_path / "targets.json")
    run_provenance.write(
        tmp_path,
        run_provenance.create_document(
            TargetSpec([target]), analyzer_root=tmp_path, language="en"),
    )
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


def test_new_run_default_language_auto_detects_zh_cn_from_host_locale(
        tmp_path, synthetic_repo, capsys, monkeypatch):
    """57B-97: `--language`'s default is no longer a hardcoded "zh-CN" — it is
    auto-detected from the host locale (`LC_ALL` > `LC_MESSAGES` > `LANG`),
    landing on "zh-CN" only when the host locale says so."""
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    code = main(["new-run", "--workspace", str(synthetic_repo.parent),
                 "--skill-root", str(tmp_path / "skill")])
    assert code == 0
    run_dir = capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1]
    assert RunState.load(run_dir).language == "zh-CN"
    from analysis_wrapper import run_provenance
    from analysis_wrapper import identity
    provenance = run_provenance.load(run_dir)
    assert provenance["generation"] == {
        "language": "zh-CN", "model": "unknown", "effort": "unknown"}
    mapping = identity.load(run_dir)
    assert mapping.source == "native"
    assert mapping.project.display_name == synthetic_repo.parent.name
    assert Path(run_dir).parent.parent.name == mapping.project.artifact_key
    assert mapping.project.internal_id not in Path(run_dir).parts


def test_new_run_default_language_falls_back_to_english_without_host_locale(
        tmp_path, synthetic_repo, capsys, monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    code = main(["new-run", "--workspace", str(synthetic_repo.parent),
                 "--skill-root", str(tmp_path / "skill")])
    assert code == 0
    run_dir = capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1]
    assert RunState.load(run_dir).language == "en"


def test_new_run_works_without_skill_root(tmp_path, synthetic_repo, capsys):
    """57B-89 Phase 2: ``--skill-root`` is no longer required for new-run —
    data always resolves through the data root."""
    code = main(["new-run", "--workspace", str(synthetic_repo.parent)])
    assert code == 0
    run_dir = Path(capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1])
    assert run_dir.is_relative_to(paths.output_root())


def test_new_run_ignores_skill_root_for_data_placement(tmp_path, synthetic_repo, capsys):
    """A caller still passing ``--skill-root`` (back-compat) must not redirect
    where the run is written — only $PROJECT_ANALYSIS_HOME does that."""
    misleading = tmp_path / "not-the-data-root"
    code = main(["new-run", "--workspace", str(synthetic_repo.parent),
                 "--skill-root", str(misleading)])
    assert code == 0
    run_dir = Path(capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1])
    assert run_dir.is_relative_to(paths.output_root())
    assert not run_dir.is_relative_to(misleading)
    assert not misleading.exists()


def test_new_run_refuses_data_root_inside_workspace(
        tmp_path, synthetic_repo, monkeypatch, capsys):
    """57B-89 Phase 2 review fix: ``new-run`` must fail closed, before writing
    anything, when ``$PROJECT_ANALYSIS_HOME`` resolves inside the analyzed
    workspace — the target tree must stay read-only."""
    workspace = synthetic_repo.parent
    inside = workspace / "would-be-data-root"
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(inside))
    code = main(["new-run", "--workspace", str(workspace)])
    assert code == 2
    err = capsys.readouterr().err
    assert "analysis target" in err
    assert not inside.exists()


def test_new_run_records_host_supplied_model_and_effort(tmp_path, synthetic_repo, capsys):
    from analysis_wrapper import run_provenance
    code = main([
        "new-run", "--workspace", str(synthetic_repo.parent),
        "--skill-root", str(tmp_path / "skill"),
        "--language", "zh-CN",  # pinned: 57B-97 made the default host-locale-detected
        "--model", "host-model", "--effort", "light",
    ])
    assert code == 0
    run_dir = capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1]
    assert run_provenance.load(run_dir)["generation"] == {
        "language": "zh-CN", "model": "host-model", "effort": "light"}


def test_new_run_cli_uses_custom_readable_id(tmp_path, synthetic_repo, capsys):
    skill_root = tmp_path / "skill"
    argv = ["new-run", "--workspace", str(synthetic_repo.parent),
            "--skill-root", str(skill_root), "--run-id", "comparison-low"]
    assert main(argv) == 0
    first_dir = capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1]
    first_id = RunState.load(first_dir).run_id
    assert first_id.startswith("comparison-low-")

    assert main(argv) == 0
    second_dir = capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1]
    assert RunState.load(second_dir).run_id == f"{first_id}-2"


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
    import json
    # `--skill-root` no longer anchors data (57B-89 Phase 2): pointers live
    # under the data root regardless of the (now ignored-for-data) skill_root
    # passed above.
    state_dirs = list(paths.state_root().iterdir())
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


def test_accept_refuses_analyzer_staleness(tmp_path, target, monkeypatch, capsys):
    from analysis_wrapper import run_provenance
    run = tmp_path / "skill" / "output" / "project" / "overview" / "run"
    run.mkdir(parents=True)
    recorded = {
        "root": str(tmp_path), "version": "0.3.0",
        "git_head": "a" * 40, "dirty_detail": "no",
    }
    state = RunState.create(
        "run", "project", TargetSpec([target]),
        analysis_identity={"analyzer": recorded})
    for stage in lifecycle.STAGES:
        state.mark(stage)
    state.save(run)
    TargetSpec([target]).save(run / "targets.json")
    run_provenance.write(
        run,
        run_provenance.create_document(
            TargetSpec([target]), analyzer_root=tmp_path, language="en"),
    )
    monkeypatch.setattr(run_provenance, "analyzer_observation", lambda _root: {
        **recorded, "version": "0.4.0"})
    assert main(["accept", "--run", str(run)]) == 2
    assert "stale" in capsys.readouterr().err


def test_incomplete_legacy_run_cannot_advance_without_provenance(
        tmp_path, target, capsys):
    state = RunState.create("legacy", "project", TargetSpec([target]), when=WHEN)
    state.mark("discovery")
    state.save(tmp_path)
    TargetSpec([target]).save(tmp_path / "targets.json")

    assert main([
        "mark-stage", "--run", str(tmp_path), "--stage", "signals"
    ]) == 2
    assert "must be regenerated" in capsys.readouterr().err
