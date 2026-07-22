"""Regression tests for the deep-review fix batch (P1-1..P3-14)."""

import subprocess

from analysis_wrapper import cli
from analysis_wrapper.git_history import worker
from analysis_wrapper.git_history.identity import IdentityResolver
from analysis_wrapper.registry import (
    dependency_cruiser, jscpd, lizard, scc, _language_args,
)
from analysis_wrapper.sanitize import sanitize_text
from analysis_wrapper.targetspec import RepoTarget, TechnologyFacet, stable_repo_id


# --- P1-1: Tier-2 exclusions must reach every tool's argv ----------------------

def test_tier2_exclusions_are_applied_to_argv(target):
    target.tier2_exclusions = ["generated-docs", "static-site"]
    for build in (scc, lizard, jscpd, dependency_cruiser):
        td = build(target)
        # depcruise embeds the dir in a regex where re.escape adds backslashes.
        joined = " ".join(td.build_argv(target)).replace("\\", "")
        assert "generated-docs" in joined, f"{td.name}: tier2 dir missing from argv"
        assert any("generated-docs" in x for x in td.applied_exclusions), \
            f"{td.name}: tier2 dir missing from disclosed exclusions"


def test_no_project_specific_dirs_are_generic(target):
    """docs/public/migrations are Tier-2 (per-project) — never baked in."""
    target.tier2_exclusions = []
    for build in (scc, jscpd, dependency_cruiser):
        joined = " ".join(build(target).build_argv(target))
        for name in ("public", "docs", "migrations"):
            assert f"/{name}/" not in joined and f",{name}" not in joined, \
                f"{build.__name__}: {name!r} applied without Tier-2 evidence"


# --- P1-3: dependency-cruiser honors analysis roots ----------------------------

def test_depcruise_uses_analysis_roots(target, synthetic_repo):
    (synthetic_repo / "tsconfig.json").write_text("{}")
    (synthetic_repo / "app").mkdir()
    target.analysis_roots = ["app", "lib"]
    argv = dependency_cruiser(target).build_argv(target)
    assert any(a.startswith("app/**/*.") for a in argv)
    assert any(a.startswith("lib/**/*.") for a in argv)
    assert not any(a.startswith("src/") for a in argv)


def test_depcruise_src_fallback_is_disclosed(target, synthetic_repo):
    (synthetic_repo / "tsconfig.json").write_text("{}")
    (synthetic_repo / "src").mkdir()
    target.analysis_roots = []
    td = dependency_cruiser(target)
    assert any(a.startswith("src/**/*.") for a in td.build_argv(target))
    assert "NOT SCANNED" in td.extra_notes


# --- P3-13: typescript implies tsx ---------------------------------------------

def test_typescript_stack_implies_tsx(target):
    target.facets = [TechnologyFacet(
        "language.typescript", "language", ["."], ["tsconfig.json"]
    )]
    args = _language_args(target)
    assert args.count("-l") == 2 and "typescript" in args and "tsx" in args


# --- P2-8: no-email identities never merge silently -----------------------------

def test_no_email_same_name_identities_stay_distinct(synthetic_repo):
    observations = [("Alex Lee", ""), ("alex lee", ""), ("Alex Lee", "")]
    resolver = IdentityResolver(str(synthetic_repo), observations)
    labels = {resolver.resolve(n, e) for n, e in {("Alex Lee", ""), ("alex lee", "")}}
    assert len(labels) == 2, "distinct no-email identities merged silently"
    assert resolver.uncertain_name_matches == ["alex lee"]


# --- P2-9: empty-history repo is a repo state, not a crash ----------------------

def test_worker_handles_repo_with_no_commits(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    data = worker.analyze(str(repo), "2024-07-16", 20, 5, 50)
    assert data["coverage_status"] == "partial"
    assert data["commits_used"] == 0 and data["churn"] == []
    assert data["history_completeness"]["total_commits_head"] == 0


# --- P2-10: line-final rootless paths are relativized ----------------------------

def test_rootless_path_at_line_end_is_relativized(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    out = sanitize_text("first Users/someone/project\nsecond Users/someone/x end")
    assert "Users/someone" not in out
    assert "$HOME/project" in out and "$HOME/x end" in out


# --- P1-2 + P3-12: CLI since default + same-language cross grouping --------------

def test_cli_since_defaults_to_computed_window():
    args = cli.parser().parse_args(
        ["--targets", "t.json", "--out", "o", "sweep"]
    )
    assert args.since is None, "since must default to the computed 24-month window"


def test_family_groups_split_node_and_go(tmp_path):
    def repo(name, marker):
        d = tmp_path / name
        d.mkdir()
        (d / marker).write_text("{}" if marker == "package.json" else "module x\n")
        return RepoTarget(repo_id=stable_repo_id(str(d)), path=str(d))
    groups = cli._family_groups(
        [repo("a", "package.json"), repo("b", "package.json"), repo("c", "go.mod")]
    )
    assert len(groups["node"]) == 2 and len(groups["go"]) == 1


# --- P3-14: safety refusal is a distinct CLI outcome -----------------------------

def test_safety_refusal_exits_4_not_2(target, synthetic_repo, tmp_path, capsys):
    from analysis_wrapper.targetspec import TargetSpec
    spec_file = tmp_path / "targets.json"
    TargetSpec(repos=[target]).save(spec_file)
    inside = synthetic_repo / "output"
    code = cli.main([
        "--targets", str(spec_file), "--out", str(inside),
        "run", "--tool", "scc", "--repo", target.repo_id,
    ])
    assert code == 4
    assert "safety refusal" in capsys.readouterr().err
    assert not inside.exists()
