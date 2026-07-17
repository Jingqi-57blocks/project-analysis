"""Run provenance (57B-11 S5).

Two products:
- `git_provenance(repo)` — the TargetSpec `GitProvenance` fields (contract
  owned by targetspec.py).
- `repo_provenance(repo)` — the richer report-header block: credential-redacted
  remote URL, HEAD timestamp, `git describe`, submodule pins, history
  completeness. Non-git folders yield an empty-provenance block (reduced
  coverage downstream, never a crash).

All git invocations use the wrapper's isolated git environment and read-only
commands; remote URLs pass through the sanitizer (whole-userinfo redaction).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .. import gitinfo
from ..sanitize import redact
from ..targetspec import GitProvenance


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            gitinfo.git_command(repo, *args), env=gitinfo.safe_git_env(),
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


@dataclass
class RepoProvenance:
    repo_id: str = ""
    path: str = ""
    is_git: bool = False
    head: str = ""
    branch: str = ""
    dirty_detail: str = "no"
    remote_redacted: str = ""      # first remote, credentials removed
    head_timestamp: str = ""       # committer date of HEAD, ISO-8601
    describe: str = ""             # git describe --tags --always
    shallow: bool = False
    commit_count: int = 0
    oldest_commit_date: str = ""
    submodule_pins: list[str] = field(default_factory=list)  # "path sha (state)"

    def to_dict(self) -> dict:
        return asdict(self)


def git_provenance(repo_path: str | Path) -> GitProvenance:
    repo = Path(repo_path).expanduser().resolve()
    head = gitinfo.head(repo)
    if not head:
        return GitProvenance()  # non-git: empty provenance, is_git False
    count = _git(repo, "rev-list", "--count", "HEAD")
    oldest = _git(repo, "log", "--reverse", "--format=%as")
    return GitProvenance(
        head=head,
        branch=gitinfo.branch(repo),
        dirty_detail=gitinfo.dirty_detail(repo),
        shallow=_git(repo, "rev-parse", "--is-shallow-repository") == "true",
        commit_count=int(count) if count.isdigit() else 0,
        oldest_commit_date=oldest.splitlines()[0] if oldest else "",
    )


def repo_provenance(repo_path: str | Path, repo_id: str) -> RepoProvenance:
    repo = Path(repo_path).expanduser().resolve()
    base = git_provenance(repo)
    if not base.is_git:
        return RepoProvenance(repo_id=repo_id, path=str(repo), is_git=False)

    remote = _git(repo, "remote", "get-url", "origin")
    if not remote:
        remotes = _git(repo, "remote")
        first = remotes.splitlines()[0] if remotes else ""
        remote = _git(repo, "remote", "get-url", first) if first else ""

    pins: list[str] = []
    status = _git(repo, "submodule", "status", "--cached")
    for line in status.splitlines():
        line = line.strip()
        if not line:
            continue
        state = {"-": "uninitialized", "+": "out-of-sync", "U": "conflict"}.get(line[0], "ok") \
            if line[0] in "-+U" else "ok"
        body = line.lstrip("-+U ").split()
        if len(body) >= 2:
            pins.append(f"{body[1]} {body[0][:12]} ({state})")

    return RepoProvenance(
        repo_id=repo_id,
        path=str(repo),
        is_git=True,
        head=base.head,
        branch=base.branch,
        dirty_detail=base.dirty_detail,
        remote_redacted=redact(remote) if remote else "(no remote configured)",
        head_timestamp=_git(repo, "log", "-1", "--format=%cI"),
        describe=_git(repo, "describe", "--tags", "--always"),
        shallow=base.shallow,
        commit_count=base.commit_count,
        oldest_commit_date=base.oldest_commit_date,
        submodule_pins=pins,
    )
