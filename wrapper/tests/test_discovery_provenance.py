"""57B-11 S5: provenance — TargetSpec fields + redacted report block."""

import subprocess

from doctor_wrapper.discovery.provenance import git_provenance, repo_provenance


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "protocol.file.allow=always", *args],
        check=True, capture_output=True,
    )


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    (path / "a.txt").write_text("a\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return path


def test_targetspec_provenance_fields(tmp_path):
    repo = _repo(tmp_path / "r")
    prov = git_provenance(repo)
    assert prov.is_git and len(prov.head) == 40
    assert prov.branch == "main"
    assert prov.dirty_detail == "no"
    assert prov.commit_count == 1
    assert prov.oldest_commit_date  # ISO date of first commit
    assert prov.shallow is False


def test_non_git_folder_is_empty_provenance_not_a_crash(tmp_path):
    prov = git_provenance(tmp_path)
    assert not prov.is_git
    block = repo_provenance(tmp_path, "x-1234")
    assert block.is_git is False and block.repo_id == "x-1234"


def test_remote_url_credentials_redacted(tmp_path):
    repo = _repo(tmp_path / "r")
    _git(repo, "remote", "add", "origin", "https://user:ghp_tok3n@github.example/org/repo.git")
    block = repo_provenance(repo, "r-1")
    assert "ghp_tok3n" not in block.remote_redacted
    assert "user" not in block.remote_redacted.split("@")[0].replace("https://", "") or \
        "<REDACTED>" in block.remote_redacted
    assert "github.example/org/repo.git" in block.remote_redacted


def test_describe_and_head_timestamp_present(tmp_path):
    repo = _repo(tmp_path / "r")
    _git(repo, "tag", "v1.0.0")
    block = repo_provenance(repo, "r-1")
    assert block.describe == "v1.0.0"
    assert block.head_timestamp.startswith("20")


def test_dirty_state_recorded(tmp_path):
    repo = _repo(tmp_path / "r")
    (repo / "b.txt").write_text("b\n")
    block = repo_provenance(repo, "r-1")
    assert block.dirty_detail.startswith("yes")


def test_submodule_pin_recorded(tmp_path):
    child = _repo(tmp_path / "child")
    parent = _repo(tmp_path / "parent")
    _git(parent, "submodule", "add", str(child), "libs/child")
    _git(parent, "commit", "-qm", "add submodule")
    block = repo_provenance(parent, "p-1")
    assert len(block.submodule_pins) == 1
    assert block.submodule_pins[0].startswith("libs/child ")
