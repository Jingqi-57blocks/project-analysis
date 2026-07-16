"""Git provenance helpers: HEAD/branch/dirty detail + porcelain snapshots.

FAIL-CLOSED: a git error is never reported as "clean". `porcelain_snapshot`
returns None on error (distinct from "" = clean); `dirty_detail` reports
"unknown (git status unavailable)" — and the executor treats an unavailable
snapshot on a git target as FAILED.

Porcelain output is NEVER stripped: the leading space of a " M" marker is
significant (unstaged vs staged), and snapshot equality must compare exact
bytes. The dirty-detail representation keeps XY markers; a leading space is
rewritten to "_" purely for the "; "-joined list ("_M"=unstaged-modified,
"M "=staged); whole lines are kept so paths with spaces and "R old -> new"
survive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_DIRTY_CAP = 20
_GIT_CONFIG_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def safe_git_env() -> dict[str, str]:
    """Drop caller-controlled Git behavior while retaining the ordinary PATH."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(_GIT_CONFIG_ENV)
    return env


def git_command(repo: str | Path, *args: str) -> list[str]:
    requested = os.environ.get("PROJECT_DOCTOR_GIT_BINARY") or shutil.which("git") or ""
    binary = Path(requested).expanduser().resolve() if requested else None
    root = Path(repo).expanduser().resolve()
    if binary is None or not binary.is_file():
        raise ValueError("approved Git binary is unavailable")
    if binary == root or binary.is_relative_to(root):
        raise ValueError(f"Git binary resolves inside target repository: {binary}")
    return [
        str(binary), "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
        "-C", str(repo), *args,
    ]


def _git(repo: str | Path, *args: str) -> str | None:
    """Raw stdout on success (NOT stripped), None on any failure."""
    try:
        out = subprocess.run(
            git_command(repo, *args),
            capture_output=True, text=True, timeout=30,
            env=safe_git_env(),
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def head(repo: str | Path) -> str:
    out = _git(repo, "rev-parse", "HEAD")
    return out.strip() if out else ""


def branch(repo: str | Path) -> str:
    out = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if out else ""


def porcelain_snapshot(repo: str | Path) -> str | None:
    """Exact `git status --porcelain` output — the immutability comparand.

    "" = clean; None = git unavailable/errored (NEVER conflated with clean).
    Trailing newline is normalized off; leading spaces (status markers) are
    preserved byte-exactly."""
    out = _git(repo, "status", "--porcelain")
    if out is None:
        return None
    return out[:-1] if out.endswith("\n") else out


def dirty_detail(repo: str | Path) -> str:
    """"no" | "yes (N files: XY path; ...)" | "unknown (git status unavailable)"."""
    st = porcelain_snapshot(repo)
    if st is None:
        return "unknown (git status unavailable)"
    if not st:
        return "no"
    lines = st.splitlines()
    shown = ["_" + ln[1:] if ln.startswith(" ") else ln for ln in lines[:_DIRTY_CAP]]
    joined = "; ".join(shown)
    extra = " ..." if len(lines) > _DIRTY_CAP else ""
    return f"yes ({len(lines)} files: {joined}{extra})"


def matches_recorded_dirty(repo: str | Path, recorded: str) -> bool:
    """Compare discovery-time dirty state without inventing content fingerprints.

    Dirty runs are inspection-only by plan. We still reject an obvious state change
    between discovery and execution; the capped representation is the contract that
    discovery records.
    """
    return dirty_detail(repo) == recorded
