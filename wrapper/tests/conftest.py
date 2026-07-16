import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from doctor_wrapper.targetspec import GitProvenance, RepoTarget, stable_repo_id
from doctor_wrapper import gitinfo


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
        stacks=["js"],
        git=GitProvenance(
            head=gitinfo.head(synthetic_repo),
            branch="main",
            dirty_detail="no",
            commit_count=1,
        ),
    )
