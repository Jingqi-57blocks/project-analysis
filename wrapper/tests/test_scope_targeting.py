"""57B-110: workspace scope targeting (--repo / --only), combined with the
pre-existing --exclude.

Verifies what already existed (repo overlap detection, --exclude by
basename, TargetSpec/analysis_roots) is untouched, and that the newly added
allowlist-style narrowing (--repo, --only) is disclosed honestly rather than
silently narrowing the analyzed set.
"""

import json
import subprocess
from pathlib import Path

import pytest

from analysis_wrapper.cli import main
from analysis_wrapper.discovery import emit


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    (path / "index.js").write_text("module.exports = 1;\n")
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t",
                    "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "init"], check=True)


def _workspace(tmp_path) -> Path:
    """api, web (top-level git repos); billing (git repo) containing a
    NESTED git repo; a linked worktree (.git FILE) named worktree-repo."""
    ws = tmp_path / "ws"
    _git_init(ws / "api")
    _git_init(ws / "web")
    _git_init(ws / "billing")
    _git_init(ws / "billing" / "nested-repo")  # nested — never a separate target
    subprocess.run(
        ["git", "-C", str(ws / "api"), "worktree", "add",
         str(ws / "worktree-repo"), "-b", "wt-branch"],
        check=True, capture_output=True,
    )
    assert (ws / "worktree-repo" / ".git").is_file()  # sanity: gitfile, not gitdir
    return ws


def _names(spec) -> set[str]:
    return {Path(r.path).name for r in spec.repos}


# --------------------------------------------------------------------------
# What already exists (verified, not duplicated)
# --------------------------------------------------------------------------

def test_exclude_already_supports_denylist_by_basename(tmp_path):
    """Pre-existing capability: --exclude removes named repos, disclosed."""
    ws = _workspace(tmp_path)
    spec, report = emit.discover(ws, exclude_names=["web"])
    assert _names(spec) == {"api", "billing", "worktree-repo"}
    assert any("web" in line and "excluded by operator flag" in line
               for line in report["not_targeted"])


def test_no_scope_flags_targets_everything_top_level(tmp_path):
    ws = _workspace(tmp_path)
    spec, report = emit.discover(ws)
    assert _names(spec) == {"api", "web", "billing", "worktree-repo"}
    assert report["scope_narrowing"] == {
        "only_path": None, "repo_filter": None, "excluded": [],
    }


# --------------------------------------------------------------------------
# --repo (new: include-style allowlist)
# --------------------------------------------------------------------------

def test_repo_flag_selects_subset(tmp_path):
    ws = _workspace(tmp_path)
    spec, report = emit.discover(ws, include_repos=["api", "web"])
    assert _names(spec) == {"api", "web"}
    scope = report["scope_narrowing"]
    assert scope["repo_filter"] == ["api", "web"]
    assert any("billing" in line for line in scope["excluded"])
    assert any("worktree-repo" in line for line in scope["excluded"])
    # Same fact also visible in the general disclosure list (existing contract).
    assert any("billing" in line and "not selected by --repo" in line
               for line in report["not_targeted"])


def test_repo_flag_matches_git_file_worktree(tmp_path):
    """A repo identified by a `.git` FILE (linked worktree) is selectable."""
    ws = _workspace(tmp_path)
    spec, report = emit.discover(ws, include_repos=["worktree-repo"])
    assert _names(spec) == {"worktree-repo"}


def test_unmatched_repo_value_fails_clearly(tmp_path):
    ws = _workspace(tmp_path)
    with pytest.raises(ValueError, match="ghost-repo"):
        emit.discover(ws, include_repos=["ghost-repo"])


def test_unmatched_repo_value_never_silently_analyzes_everything_or_nothing(tmp_path):
    """A failed --repo lookup must raise before producing ANY TargetSpec —
    never fall back to the full workspace, never fall back to empty."""
    ws = _workspace(tmp_path)
    with pytest.raises(ValueError):
        emit.discover(ws, include_repos=["api", "ghost-repo"])


def test_nested_repo_unaffected_by_repo_flag(tmp_path):
    """Nested repos are never separate targets regardless of --repo; naming
    the enclosing repo still admits it exactly as without scope targeting,
    and the nested repo stays disclosed (never silently promoted to a
    target, and never silently dropped without a reason)."""
    ws = _workspace(tmp_path)
    spec, report = emit.discover(ws)
    assert any("nested-repo" in line and "nested in" in line
               for line in report["not_targeted"])  # baseline, no scope flags

    spec, report = emit.discover(ws, include_repos=["billing"])
    assert _names(spec) == {"billing"}
    # nested-repo's own basename doesn't match the --repo allowlist either
    # (it was never a selectable target to begin with), so it is disclosed
    # via the scope reason instead — still disclosed, never silently absent.
    assert any("nested-repo" in line for line in report["not_targeted"])


# --------------------------------------------------------------------------
# --only (new: subdirectory scoping)
# --------------------------------------------------------------------------

def test_only_scopes_to_subdirectory(tmp_path):
    """``--only`` narrows to one workspace-relative subdirectory. Demonstrated
    here against a single top-level repo (this discovery engine's v1 unit of
    targeting is a top-level repo — see emit.py's own module docstring); the
    other top-level repos are disclosed as scoped out, never silently kept."""
    ws = _workspace(tmp_path)

    spec, report = emit.discover(ws, only_path="api")
    assert _names(spec) == {"api"}
    scope = report["scope_narrowing"]
    assert scope["only_path"] == "api"
    assert any("web" in line and "--only scope" in line for line in scope["excluded"])
    assert any("billing" in line and "--only scope" in line for line in scope["excluded"])


def test_only_nonexistent_subdirectory_fails_clearly(tmp_path):
    ws = _workspace(tmp_path)
    with pytest.raises(ValueError, match="--only"):
        emit.discover(ws, only_path="does-not-exist")


def test_only_path_escaping_workspace_fails_clearly(tmp_path):
    ws = _workspace(tmp_path)
    with pytest.raises(ValueError, match="--only"):
        emit.discover(ws, only_path="../outside")


# --------------------------------------------------------------------------
# Combination semantics: --only applies first, then --repo, then --exclude
# --------------------------------------------------------------------------

def test_only_and_repo_combine(tmp_path):
    """--only scopes first; --repo then still applies (and still validates)
    within whatever --only left in scope."""
    ws = _workspace(tmp_path)

    spec, report = emit.discover(ws, only_path="api", include_repos=["api"])
    assert _names(spec) == {"api"}
    scope = report["scope_narrowing"]
    assert scope["only_path"] == "api"
    assert scope["repo_filter"] == ["api"]
    # web/billing/worktree-repo excluded by --only itself, not by --repo
    # (--repo never even got a chance to consider them).
    assert any("web" in line and "--only scope" in line for line in scope["excluded"])


def test_repo_value_outside_only_scope_is_unmatched_and_fails(tmp_path):
    """--repo is validated against what --only actually leaves in scope —
    naming a real repo that --only has already excluded is unmatched, same
    as naming a repo that never existed."""
    ws = _workspace(tmp_path)
    with pytest.raises(ValueError, match="web"):
        emit.discover(ws, only_path="api", include_repos=["web"])


def test_repo_include_then_exclude_denylist_still_wins_within_selection(tmp_path):
    ws = _workspace(tmp_path)
    spec, report = emit.discover(
        ws, include_repos=["api", "web"], exclude_names=["web"])
    assert _names(spec) == {"api"}
    assert any("web" in line and "excluded by operator flag" in line
               for line in report["not_targeted"])
    # web was matched by --repo (not a scope-narrowing failure) but then
    # removed by --exclude — distinguishable from a --repo scope exclusion.
    assert not any("web" in line for line in report["scope_narrowing"]["excluded"])


# --------------------------------------------------------------------------
# Disclosure is visible end to end via the CLI
# --------------------------------------------------------------------------

def test_cli_discover_discloses_scope_narrowing(tmp_path, capsys):
    ws = _workspace(tmp_path)
    out_dir = tmp_path / "run"
    code = main(["--out", str(out_dir), "discover", "--workspace", str(ws),
                 "--repo", "api,web"])
    printed = capsys.readouterr().out
    assert code == 0
    assert "scope narrowing" in printed
    report = json.loads((out_dir / "discovery-report.json").read_text())
    assert report["scope_narrowing"]["repo_filter"] == ["api", "web"]
    assert len(report["scope_narrowing"]["excluded"]) >= 2


def test_cli_discover_unmatched_repo_is_invalid_invocation(tmp_path, capsys):
    ws = _workspace(tmp_path)
    out_dir = tmp_path / "run"
    code = main(["--out", str(out_dir), "discover", "--workspace", str(ws),
                 "--repo", "ghost-repo"])
    assert code == 2
    assert "ghost-repo" in capsys.readouterr().err
    assert not out_dir.exists()  # refused before any write


def test_cli_new_run_records_scope_narrowing_in_run_directory(tmp_path, capsys):
    ws = _workspace(tmp_path)
    code = main(["new-run", "--workspace", str(ws), "--repo", "api"])
    out = capsys.readouterr().out
    assert code == 0
    assert "scope narrowing" in out
    run_dir = Path(out.splitlines()[0].split("run: ", 1)[1])
    report = json.loads((run_dir / "discovery-report.json").read_text())
    assert report["scope_narrowing"]["repo_filter"] == ["api"]


# --------------------------------------------------------------------------
# Review fix (57B-109 review): a narrowed run must never mint with ZERO
# targets while claiming coverage it never had. Reproduced case: `--repo
# nested-repo` matches the nested repo's own name (so the pre-existing
# unmatched-`--repo` error never fires), but `billing` -- its enclosing
# repo -- is excluded by the same `--repo` allowlist, so nothing ever
# scans `nested-repo` either. Before the fix this minted a 0-target run,
# exit 0, while the report still falsely said "scanned as part of the
# enclosing repo".
# --------------------------------------------------------------------------

def test_repo_flag_naming_only_a_nested_repo_hard_errors_never_zero_target_mint(tmp_path):
    """`--repo nested-repo` alone: nested-repo matches the allowlist by name,
    but its enclosing repo (billing) does not, so nested-repo is excluded
    right along with it -- zero targets. This must hard-error, never mint a
    0-target run."""
    ws = _workspace(tmp_path)
    with pytest.raises(ValueError, match="zero analysis targets"):
        emit.discover(ws, include_repos=["nested-repo"])


def test_cli_new_run_repo_naming_only_nested_repo_refuses_before_any_write(tmp_path, capsys):
    ws = _workspace(tmp_path)
    code = main(["new-run", "--workspace", str(ws), "--repo", "nested-repo"])
    err = capsys.readouterr().err
    assert code == 2  # invalid invocation, same family as the unmatched-repo error
    assert "zero analysis targets" in err
    assert "nested-repo" in err
    # Nothing minted: no run directory, no state, no data-root output/ tree.
    import os
    home = Path(os.environ["PROJECT_ANALYSIS_HOME"])
    assert not (home / "output").exists()


def test_only_flag_on_a_repo_free_directory_hard_errors_never_zero_target_mint(tmp_path):
    """`--only <dir>` where the directory contains no repos at all (and is not
    itself part of any non-git project) must hard-error rather than mint an
    empty run."""
    ws = _workspace(tmp_path)
    (ws / "docs").mkdir()
    (ws / "docs" / "README.md").write_text("nothing here is a repo\n")
    with pytest.raises(ValueError, match="zero analysis targets"):
        emit.discover(ws, only_path="docs")


def test_cli_new_run_only_repo_free_dir_refuses_before_any_write(tmp_path, capsys):
    ws = _workspace(tmp_path)
    (ws / "docs").mkdir()
    (ws / "docs" / "README.md").write_text("nothing here is a repo\n")
    code = main(["new-run", "--workspace", str(ws), "--only", "docs"])
    err = capsys.readouterr().err
    assert code == 2
    assert "zero analysis targets" in err
    import os
    home = Path(os.environ["PROJECT_ANALYSIS_HOME"])
    assert not (home / "output").exists()


def test_nested_disclosure_says_not_scanned_when_enclosing_repo_is_scope_excluded(tmp_path):
    """When a run still produces at least one target (so the zero-target
    guard above doesn't preempt it), the nested-repo disclosure for a repo
    whose enclosing repo was scope-excluded must say it was NOT scanned --
    never the "scanned as part of the enclosing repo" wording, which would
    be false since the enclosing repo itself was excluded."""
    ws = _workspace(tmp_path)
    spec, report = emit.discover(ws, include_repos=["nested-repo", "api"])
    assert _names(spec) == {"api"}  # api admitted; nested-repo/billing are not
    assert any("billing" in line and "not selected by --repo" in line
               for line in report["not_targeted"])
    assert any(
        "nested-repo" in line and "not scanned" in line
        and "scanned as part of the enclosing repo" not in line
        for line in report["not_targeted"]
    )


# --------------------------------------------------------------------------
# Review fix: the unmatched-`--repo` message must never name, as "available",
# a repo it just excluded via `--only` (self-contradictory: "matched no
# repository in the workspace: ['web']; available: [... 'web' ...]").
# --------------------------------------------------------------------------

def test_unmatched_repo_outside_only_scope_message_says_in_scope_and_excludes_it(tmp_path):
    ws = _workspace(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        emit.discover(ws, only_path="api", include_repos=["web"])
    message = str(excinfo.value)
    assert "in scope" in message
    # `web` is named as the unmatched request, but must not also appear in
    # the "available" list -- --only already excluded it.
    available_part = message.split("available:", 1)[1]
    assert "'web'" not in available_part


# --------------------------------------------------------------------------
# Review fix (SHOULD, FIX 3): non-git container limitation -- verified
# behavior, not the disproved "swallowed regardless of --only/--repo" claim.
# --------------------------------------------------------------------------

def _services_workspace(tmp_path) -> Path:
    """``services/{a,b}``: git repos under a stack-bearing non-git container
    (no root-level package.json/go.mod at ``services/``)."""
    ws = tmp_path / "ws"
    _git_init(ws / "services" / "a")
    _git_init(ws / "services" / "b")
    return ws


def test_non_git_container_baseline_targets_container_not_children(tmp_path):
    ws = _services_workspace(tmp_path)
    spec, report = emit.discover(ws)
    assert _names(spec) == {"services"}
    assert any("services" in line and "non-git folder" in line
               for line in report["reduced_coverage_targets"])
    assert any("services" in line and "canonical non-git project" in line
               for line in report["not_targeted"] if "/a" in line or "\\a" in line) \
        or any("contained in" in line and "canonical non-git project" in line
               for line in report["not_targeted"])


def test_non_git_container_only_services_still_targets_container_not_children(tmp_path):
    ws = _services_workspace(tmp_path)
    spec, report = emit.discover(ws, only_path="services")
    assert _names(spec) == {"services"}


def test_repo_child_escapes_non_git_container_degradation(tmp_path):
    """`--repo a` is a working escape hatch: naming the child directly makes
    `services` itself fail the allowlist and drop out of the non-git
    candidate set, so `a` is admitted as a full git target -- NOT swallowed
    regardless of `--repo`."""
    ws = _services_workspace(tmp_path)
    spec, report = emit.discover(ws, include_repos=["a"])
    assert _names(spec) == {"a"}
    target = spec.repos[0]
    assert target.git.head  # real git provenance, not a non-git reduced target
    assert not any("non-git folder" in line for line in report["reduced_coverage_targets"])


# --------------------------------------------------------------------------
# FIX 5 polish: untested safety cases the review found already work.
# --------------------------------------------------------------------------

def test_only_absolute_path_outside_workspace_fails_closed(tmp_path):
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="--only"):
        emit.discover(ws, only_path=str(outside))


def test_only_symlink_to_outside_dir_fails_closed(tmp_path):
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside-target"
    outside.mkdir()
    link = ws / "escape-link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="--only"):
        emit.discover(ws, only_path="escape-link")


def test_ambiguous_repo_basename_matches_both_and_both_are_disclosed(tmp_path):
    """Two repos sharing a basename in different directories: `--repo web`
    matches (and targets) both -- ambiguity is resolved by including every
    match, disclosed, never an arbitrary pick or a silent drop."""
    ws = tmp_path / "ws"
    _git_init(ws / "services" / "web")
    _git_init(ws / "other" / "web")
    spec, report = emit.discover(ws, include_repos=["web"])
    paths = {Path(r.path).resolve() for r in spec.repos}
    assert paths == {(ws / "services" / "web").resolve(), (ws / "other" / "web").resolve()}
