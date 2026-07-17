"""57B-11 S1: repo inventory — .git dirs AND files, disclosure, determinism."""

import subprocess

import pytest

from analysis_wrapper.discovery.inventory import MAX_DEPTH, Inventory, find_repos
from analysis_wrapper.targetspec import stable_repo_id


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    return path


def test_finds_multiple_repos_sorted_and_project_id_is_deterministic(tmp_path):
    _init_repo(tmp_path / "beta")
    _init_repo(tmp_path / "alpha")
    inv = find_repos(tmp_path)
    assert [r.path for r in inv.repos] == sorted(r.path for r in inv.repos)
    assert len(inv.repos) == 2
    assert inv.project_id == stable_repo_id(str(tmp_path.resolve()))
    assert find_repos(tmp_path).project_id == inv.project_id


def test_workspace_root_itself_can_be_the_repo(tmp_path):
    _init_repo(tmp_path)
    inv = find_repos(tmp_path)
    assert [r.path for r in inv.repos] == [str(tmp_path.resolve())]
    assert inv.repos[0].git_kind == "dir" and inv.repos[0].nested_in == ""


def test_gitfile_repo_found_and_submodule_classified(tmp_path):
    parent = _init_repo(tmp_path / "parent")
    sub = parent / "libs" / "child"
    sub.mkdir(parents=True)
    (sub / ".git").write_text("gitdir: ../../.git/modules/libs/child\n")
    inv = find_repos(tmp_path)
    child = next(r for r in inv.repos if r.path.endswith("child"))
    assert child.git_kind == "file"
    assert child.linked_kind == "submodule"
    assert child.gitdir == "../../.git/modules/libs/child"
    assert child.nested_in == str(parent.resolve())


def test_worktree_gitfile_classified(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /somewhere/main/.git/worktrees/wt\n")
    inv = find_repos(tmp_path)
    assert inv.repos[0].git_kind == "file"
    assert inv.repos[0].linked_kind == "worktree"


def test_vendored_repo_is_skipped_but_disclosed(tmp_path):
    _init_repo(tmp_path / "app")
    vendored = tmp_path / "app" / "node_modules" / "dep"
    _init_repo(vendored)
    inv = find_repos(tmp_path)
    assert [r.path for r in inv.repos] == [str((tmp_path / "app").resolve())]
    assert any("node_modules" in s for s in inv.skipped), "pruned repo must be disclosed"


def test_symlinked_repo_not_followed_but_disclosed(tmp_path):
    real = _init_repo(tmp_path / "real")
    outside = _init_repo(tmp_path.parent / f"{tmp_path.name}-outside")
    (tmp_path / "link").symlink_to(outside)
    inv = find_repos(tmp_path)
    assert [r.path for r in inv.repos] == [str(real.resolve())]
    assert any("symlink" in s for s in inv.skipped)


def test_depth_limited_repo_disclosed(tmp_path):
    deep = tmp_path
    for i in range(MAX_DEPTH + 1):
        deep = deep / f"d{i}"
    _init_repo(deep)
    inv = find_repos(tmp_path)
    assert inv.repos == []
    assert any("depth limit" in s for s in inv.skipped)


def test_nested_plain_repo_records_enclosure(tmp_path):
    outer = _init_repo(tmp_path / "outer")
    inner = _init_repo(outer / "embedded")
    inv = find_repos(tmp_path)
    by_path = {r.path: r for r in inv.repos}
    assert by_path[str(inner.resolve())].nested_in == str(outer.resolve())
    assert by_path[str(outer.resolve())].nested_in == ""


def test_non_directory_root_is_an_input_error(tmp_path):
    with pytest.raises(ValueError):
        find_repos(tmp_path / "does-not-exist")
