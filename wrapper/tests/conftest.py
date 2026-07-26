import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.targetspec import (
    GitProvenance, RepoTarget, TechnologyFacet, stable_repo_id,
)
from analysis_wrapper import gitinfo


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path_factory, monkeypatch):
    """57B-89 Phase 2: persistent data (state/output/exported) and generated
    runtimes no longer follow ``--skill-root`` — they resolve through
    ``analysis_wrapper.paths.data_root()``, which honors
    ``$PROJECT_ANALYSIS_HOME``. Pin every test to its own throwaway directory
    so no test run ever touches a developer's real data root, regardless of
    whether that individual test still passes (now ignored-for-data)
    ``--skill-root`` arguments. This genuinely redirects EVERY derived path
    (venv, node_tools, go_tools, output/state/exported), not just
    ``data_root()`` itself, because those resolvers are lazy functions, never
    import-time-frozen constants -- see
    ``test_data_root.py::test_changing_project_analysis_home_moves_every_derived_path``,
    which asserts this directly.

    Deliberately ``tmp_path_factory``, not the per-test ``tmp_path``: tests
    that also use the ``synthetic_repo``/``target`` fixtures build their
    analysis WORKSPACE under that same per-test ``tmp_path``, so nesting the
    data root under it would make the data root resolve INSIDE the analysis
    target -- exactly what ``paths.validate_data_root(..., target=...)`` (and
    ``new-run``'s use of it) now correctly refuses (57B-89 Phase 2 review fix,
    FIX 2). ``tmp_path_factory.mktemp()`` allocates a sibling directory
    under pytest's shared base tmp dir instead, well outside any single
    test's own workspace tree."""
    home = tmp_path_factory.mktemp("pa-data-home")
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(home))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def synthetic_repo(tmp_path) -> Path:
    """A tiny committed git repo — generic fixture, no WCP data (acceptance #4)."""
    repo = tmp_path / "widget-api"
    repo.mkdir()
    (repo / "index.js").write_text("module.exports = 1;\n")
    (repo / "util.js").write_text("exports.x = () => 2;\n")
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


@pytest.fixture
def target(synthetic_repo) -> RepoTarget:
    return RepoTarget(
        repo_id=stable_repo_id(str(synthetic_repo)),
        path=str(synthetic_repo),
        facets=[TechnologyFacet(
            "language.javascript", "language", ["."], ["index.js"]
        )],
        git=GitProvenance(
            head=gitinfo.head(synthetic_repo),
            branch="main",
            dirty_detail="no",
            commit_count=1,
        ),
    )
