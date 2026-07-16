"""Repo inventory: find git repositories under a workspace root.

Finds `.git` DIRECTORIES and `.git` FILES (linked worktrees / submodules use a
gitfile whose single line is `gitdir: <path>` — read as declarative data, never
executed). Vendored trees (node_modules, vendor, ...) are pruned but any repo
found at a pruned boundary is DISCLOSED as skipped, never silently dropped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..targetspec import stable_repo_id

# Never descend into these (vendored/generated trees can embed dependency
# repos); mirrors the wrapper's Tier-1 exclusions plus the doctor's own
# runtime directories in case the workspace overlaps a skill checkout.
PRUNE_DIRS = frozenset(
    {"node_modules", "vendor", "dist", "build", "coverage", "state", "output"}
)
MAX_DEPTH = 8  # levels below the workspace root; deeper repos are disclosed


@dataclass
class RepoHit:
    path: str            # absolute repo root (the directory containing .git)
    git_kind: str        # "dir" | "file"
    gitdir: str = ""     # raw gitdir target for .git files ("" for dirs)
    linked_kind: str = ""  # "submodule" | "worktree" | "" (mechanical guess from gitdir path)
    nested_in: str = ""  # enclosing repo root ("" when top-level)

    @property
    def repo_id(self) -> str:
        return stable_repo_id(self.path)


@dataclass
class Inventory:
    workspace_root: str
    project_id: str
    repos: list[RepoHit] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # disclosed, never silent


def _read_gitfile(gitfile: Path) -> str:
    try:
        first = gitfile.read_text("utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return ""
    return first.split(":", 1)[1].strip() if first.startswith("gitdir:") else ""


def _classify_gitdir(gitdir: str) -> str:
    normalized = gitdir.replace(os.sep, "/")
    if "/.git/modules/" in normalized or normalized.startswith(".git/modules"):
        return "submodule"
    if "/.git/worktrees/" in normalized or "/worktrees/" in normalized:
        return "worktree"
    return ""


def _git_entry(directory: Path) -> Path | None:
    candidate = directory / ".git"
    return candidate if candidate.exists() else None


def _contains_repo_shallow(directory: Path, levels: int = 2) -> bool:
    """Bounded probe inside a pruned tree (node_modules/dep, vendor/x/y) so a
    skipped repo can be disclosed without walking the whole vendored forest."""
    if _git_entry(directory):
        return True
    if levels <= 0:
        return False
    try:
        children = [p for p in directory.iterdir() if p.is_dir() and not p.is_symlink()]
    except OSError:
        return False
    return any(_contains_repo_shallow(c, levels - 1) for c in children)


def find_repos(workspace_root: str | os.PathLike) -> Inventory:
    """Walk the workspace and inventory every git repository.

    Deterministic: children are visited in sorted order and results are sorted
    by path. Symlinked directories are never followed (cycle safety) — a
    symlink that hides a repo is disclosed as skipped.
    """
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace root is not a directory: {root}")
    result = Inventory(
        workspace_root=str(root), project_id=stable_repo_id(str(root))
    )

    def walk(directory: Path, depth: int, enclosing: str) -> None:
        git = _git_entry(directory)
        inside = enclosing
        if git is not None:
            if git.is_dir():
                hit = RepoHit(path=str(directory), git_kind="dir", nested_in=enclosing)
            else:
                gitdir = _read_gitfile(git)
                hit = RepoHit(
                    path=str(directory), git_kind="file", gitdir=gitdir,
                    linked_kind=_classify_gitdir(gitdir), nested_in=enclosing,
                )
            result.repos.append(hit)
            inside = str(directory)
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if child.name == ".git":
                continue
            if child.is_symlink():
                if _git_entry(child):
                    result.skipped.append(f"{child} (symlinked directory, not followed)")
                continue
            if child.name in PRUNE_DIRS:
                if _contains_repo_shallow(child):
                    result.skipped.append(
                        f"{child} (repo(s) inside pruned '{child.name}' tree)"
                    )
                continue
            if depth >= MAX_DEPTH:
                if _git_entry(child):
                    result.skipped.append(f"{child} (beyond depth limit {MAX_DEPTH})")
                continue
            walk(child, depth + 1, inside)

    walk(root, 0, "")
    result.repos.sort(key=lambda hit: hit.path)
    result.skipped.sort()
    return result
