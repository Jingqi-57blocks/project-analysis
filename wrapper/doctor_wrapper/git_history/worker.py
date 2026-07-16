"""Machine worker for the history lane; emits one deterministic JSON object."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import PurePosixPath

from ..gitinfo import git_command, safe_git_env
from .identity import IdentityResolver, is_bot

EXCLUDED_DIRS = {"node_modules", "vendor", "dist", "build"}
EXCLUDED_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}


def excluded(path: str) -> bool:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    name = parts[-1] if parts else ""
    return any(x in EXCLUDED_DIRS for x in parts) or name in EXCLUDED_NAMES or \
        bool(re.search(r"(?:\.min\.[^/]+|\.map)$", name))


def git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        git_command(repo, *args), capture_output=True, text=True, timeout=60,
        env=safe_git_env(),
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def completeness(repo: str, since: str) -> dict:
    def value(*args: str, default: str | None = None) -> str:
        # A repository with no commits makes HEAD-dependent commands fail; that
        # is a repo state, not a tool error — report zeros/empties, not a crash.
        try:
            return git(repo, *args)
        except RuntimeError:
            if default is None:
                raise
            return default
    log_dates = value("log", "--reverse", "--date=short", "--format=%ad", default="").splitlines()
    return {
        "shallow": value("rev-parse", "--is-shallow-repository") == "true",
        "oldest_commit_date": log_dates[0] if log_dates else "",
        "newest_commit_date": value("log", "-1", "--date=short", "--format=%ad", default=""),
        "total_commits_all": int(value("rev-list", "--count", "--all", default="0")),
        "total_commits_head": int(value("rev-list", "--count", "HEAD", default="0")),
        "commits_in_window": int(value("rev-list", "--count", f"--since={since}", "HEAD", default="0")),
        "nonmerge_in_window": int(value("rev-list", "--count", "--no-merges", f"--since={since}", "HEAD", default="0")),
        "merges_in_window": int(value("rev-list", "--count", "--merges", f"--since={since}", "HEAD", default="0")),
    }


@dataclass
class Change:
    old: str | None
    new: str | None
    added: int
    deleted: int


@dataclass
class Commit:
    name: str
    email: str
    changes: list[Change]


def collect_pydriller(repo: str, since: str) -> tuple[list[Commit], str]:
    from datetime import datetime
    from pydriller import Repository

    commits: list[Commit] = []
    for commit in Repository(repo, since=datetime.strptime(since, "%Y-%m-%d")).traverse_commits():
        if commit.merge or is_bot(commit.author.name or "", commit.author.email or ""):
            continue
        changes = []
        for item in commit.modified_files:
            old = item.old_path.replace("\\", "/") if item.old_path else None
            new = item.new_path.replace("\\", "/") if item.new_path else None
            path = new or old or ""
            if not path or excluded(path):
                continue
            changes.append(Change(old, new, item.added_lines or 0, item.deleted_lines or 0))
        commits.append(Commit(commit.author.name or "", commit.author.email or "", changes))
    return commits, f"pydriller {importlib.metadata.version('pydriller')}"


def _numstat_path(value: str) -> tuple[str | None, str | None]:
    # Git's compact rename form: dir/{old => new}/file or old => new.
    if " => " not in value:
        return value, value
    match = re.search(r"\{([^{}]*) => ([^{}]*)\}", value)
    if match:
        return value[:match.start()] + match.group(1) + value[match.end():], \
            value[:match.start()] + match.group(2) + value[match.end():]
    old, new = value.split(" => ", 1)
    return old, new


def collect_plain_git(repo: str, since: str) -> tuple[list[Commit], str]:
    format_marker = "@@DOCTOR_COMMIT@@%x09%an%x09%ae"
    proc = subprocess.run(
        git_command(repo, "log", "--no-merges", "--full-history", f"--since={since}",
         "--numstat", f"--format={format_marker}", "HEAD", "--", ":/",
         ":(glob,exclude)**/node_modules/**", ":(glob,exclude)**/vendor/**",
         ":(glob,exclude)**/dist/**", ":(glob,exclude)**/build/**",
         ":(glob,exclude)**/package-lock.json", ":(glob,exclude)**/pnpm-lock.yaml",
         ":(glob,exclude)**/yarn.lock", ":(glob,exclude)**/*.min.*", ":(glob,exclude)**/*.map"),
        capture_output=True, text=True, timeout=600, env=safe_git_env(),
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "plain git history failed")
    commits: list[Commit] = []
    current: Commit | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("@@DOCTOR_COMMIT@@\t"):
            _, name, email = (line.split("\t", 2) + ["", ""])[:3]
            current = Commit(name, email, [])
            if not is_bot(name, email):
                commits.append(current)
            else:
                current = None
        elif current and re.match(r"^(?:\d+|-)\t(?:\d+|-)\t", line):
            added, deleted, path = line.split("\t", 2)
            old, new = _numstat_path(path)
            if not excluded(new or old or ""):
                current.changes.append(Change(old, new,
                                              int(added) if added.isdigit() else 0,
                                              int(deleted) if deleted.isdigit() else 0))
    return commits, "plain-git fallback"


def analyze(repo: str, since: str, top: int, min_shared: int, bulk_limit: int) -> dict:
    comp = completeness(repo, since)
    if comp["total_commits_head"] == 0:
        # No reachable commits: a repo state, reported as reduced coverage.
        return {
            "backend": "none (no commits reachable from HEAD)",
            "coverage_status": "partial", "since": since,
            "history_completeness": comp, "commits_used": 0,
            "bulk_changesets_excluded_from_coupling": 0,
            "uncertain_name_matches": [], "rename_aliases": [],
            "churn": [], "coupling": [], "ownership": [],
        }
    try:
        commits, backend = collect_pydriller(repo, since)
    except (ImportError, importlib.metadata.PackageNotFoundError):
        commits, backend = collect_plain_git(repo, since)

    observations = [(c.name, c.email) for c in commits]  # bots were removed first
    resolver = IdentityResolver(repo, observations)

    # Rename aliases are assembled before aggregation, so pre-rename history is
    # attributed to the final path in a chain.
    successor: dict[str, str] = {}
    for commit in commits:
        for change in commit.changes:
            if change.old and change.new and change.old != change.new:
                successor[change.old] = change.new

    def canonical(path: str) -> str:
        seen: set[str] = set()
        while path in successor and path not in seen:
            seen.add(path); path = successor[path]
        return path

    commits_by_file: Counter[str] = Counter()
    added: Counter[str] = Counter(); deleted: Counter[str] = Counter()
    author_commits: dict[str, Counter[str]] = defaultdict(Counter)
    author_churn: dict[str, Counter[str]] = defaultdict(Counter)
    pair_shared: Counter[tuple[str, str]] = Counter()
    bulk_excluded = 0
    for commit in commits:
        author = resolver.resolve(commit.name, commit.email)
        touched: dict[str, int] = defaultdict(int)
        for change in commit.changes:
            path = canonical(change.new or change.old or "")
            if not path:
                continue
            touched[path] += change.added + change.deleted
            added[path] += change.added; deleted[path] += change.deleted
        for path, churn in touched.items():
            commits_by_file[path] += 1
            author_commits[path][author] += 1
            author_churn[path][author] += churn
        paths = sorted(touched)
        if len(paths) > bulk_limit:
            bulk_excluded += 1
        else:
            for pair in combinations(paths, 2):
                pair_shared[pair] += 1

    churn = sorted(({
        "file": path, "commits": count, "added": added[path], "deleted": deleted[path],
        "total_lines": added[path] + deleted[path],
    } for path, count in commits_by_file.items()),
        key=lambda x: (-x["commits"], -x["total_lines"], x["file"]))
    coupling = []
    for (a, b), shared in pair_shared.items():
        if shared < min_shared:
            continue
        degree = round(100 * shared / ((commits_by_file[a] + commits_by_file[b]) / 2), 1)
        coupling.append({"file_a": a, "file_b": b, "shared_commits": shared,
                         "revs_a": commits_by_file[a], "revs_b": commits_by_file[b],
                         "coupling_pct": degree})
    coupling.sort(key=lambda x: (-x["coupling_pct"], -x["shared_commits"], x["file_a"], x["file_b"]))
    ownership = []
    for row in churn:
        path = row["file"]
        commit_counts = author_commits[path]; churn_counts = author_churn[path]
        dominant_commit = sorted(commit_counts.items(), key=lambda x: (-x[1], x[0]))[0]
        dominant_churn = sorted(churn_counts.items(), key=lambda x: (-x[1], x[0]))[0]
        total_churn = sum(churn_counts.values())
        ownership.append({
            "file": path, "distinct_committers": len(commit_counts),
            "dominant_commit_author": dominant_commit[0],
            "dominant_commit_share": round(dominant_commit[1] / sum(commit_counts.values()), 4),
            "dominant_churn_author": dominant_churn[0],
            "dominant_churn_share": round(dominant_churn[1] / total_churn, 4) if total_churn else 0.0,
        })
    aliases = sorted(
        ({"old": old, "final": canonical(old)} for old in successor if canonical(old) != old),
        key=lambda x: (x["final"], x["old"]),
    )
    status = "partial" if comp["shallow"] or backend.startswith("plain-git") else "complete"
    return {
        "backend": backend, "coverage_status": status, "since": since,
        "history_completeness": comp, "commits_used": len(commits),
        "bulk_changesets_excluded_from_coupling": bulk_excluded,
        "uncertain_name_matches": resolver.uncertain_name_matches,
        "rename_aliases": aliases, "churn": churn[:top], "coupling": coupling[:top],
        "ownership": ownership[:top],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--repo")
    parser.add_argument("--since")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-shared", type=int, default=5)
    parser.add_argument("--bulk-limit", type=int, default=50)
    args = parser.parse_args()
    if args.version:
        try:
            print(f"pydriller {importlib.metadata.version('pydriller')}")
        except importlib.metadata.PackageNotFoundError:
            print("plain-git fallback")
        return
    if not args.repo or not args.since:
        parser.error("--repo and --since are required")
    print(json.dumps(analyze(args.repo, args.since, args.top, args.min_shared, args.bulk_limit),
                     sort_keys=True))


if __name__ == "__main__":
    main()
