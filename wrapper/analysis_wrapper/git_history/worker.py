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
from .identity import IdentityResolver, is_bot, shortlog_authors

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
    format_marker = "@@ANALYSIS_COMMIT@@%x09%an%x09%ae"
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
        if line.startswith("@@ANALYSIS_COMMIT@@\t"):
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


def analyze(repo: str, since: str, top: int, min_shared: int, bulk_limit: int,
            coupling_sample_cap: int = 0) -> dict:
    comp = completeness(repo, since)
    if comp["total_commits_head"] == 0:
        # No reachable commits: a repo state, reported as reduced coverage.
        return {
            "backend": "none (no commits reachable from HEAD)",
            "coverage_status": "partial", "since": since,
            "history_completeness": comp, "commits_used": 0,
            "bulk_changesets_excluded_from_coupling": 0,
            "coupling_sample_cap": coupling_sample_cap,
            "coupling_commits_used": 0, "coupling_sampled": False,
            "author_roster": [], "distinct_authors_strong": 0,
            "git_shortlog_author_count": 0,
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
    # Repo roster keyed by strong-merge GROUP (email/.mailmap), not display name,
    # so identical-name / different-email identities are never silently merged.
    group_totals: Counter[object] = Counter()
    group_display: dict[object, str] = {}
    for commit in commits:
        author = resolver.resolve(commit.name, commit.email)
        display, group_key = resolver.group(commit.name, commit.email)
        group_totals[group_key] += 1
        group_display[group_key] = display
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

    # Co-change coupling pass. The sampling cap bounds cost on very long
    # histories: cap 0 (default) uses every commit — identical to the
    # churn/ownership universe; when set and exceeded, an evenly-spaced sample of
    # that many commits is used, and BOTH the shared count and its rev
    # denominators come from that SAME sample so coupling% stays self-consistent.
    coupling_sampled = bool(coupling_sample_cap) and len(commits) > coupling_sample_cap
    if coupling_sampled:
        step = len(commits) / coupling_sample_cap
        keep = sorted({min(len(commits) - 1, int(i * step))
                       for i in range(coupling_sample_cap)})
        coupling_commits = [commits[i] for i in keep]
    else:
        coupling_commits = commits
    coupling_revs: Counter[str] = Counter() if coupling_sampled else commits_by_file
    pair_shared: Counter[tuple[str, str]] = Counter()
    bulk_excluded = 0
    for commit in coupling_commits:
        touched_paths = {canonical(c.new or c.old or "") for c in commit.changes}
        touched_paths.discard("")
        if len(touched_paths) > bulk_limit:
            bulk_excluded += 1
            continue
        if coupling_sampled:
            for path in touched_paths:
                coupling_revs[path] += 1
        for pair in combinations(sorted(touched_paths), 2):
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
        degree = round(100 * shared / ((coupling_revs[a] + coupling_revs[b]) / 2), 1)
        coupling.append({"file_a": a, "file_b": b, "shared_commits": shared,
                         "revs_a": coupling_revs[a], "revs_b": coupling_revs[b],
                         "coupling_pct": degree})
    coupling.sort(key=lambda x: (-x["coupling_pct"], -x["shared_commits"], x["file_a"], x["file_b"]))

    def _top_dir(path: str) -> str:
        parts = path.split("/")
        return "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if parts else path)

    # Cross-directory pairs are the change-friction / ripple signal (a change in
    # one area co-changes with another). They lose to intra-dir pairs in the
    # global top-N, so they are kept as a separate ranked list.
    cross_dir_coupling = [c for c in coupling if _top_dir(c["file_a"]) != _top_dir(c["file_b"])]
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
    # Strong-merged repo roster (exact-email/.mailmap via git check-mailmap) vs a
    # git-native corroboration count (git shortlog -sne, mailmap-applied but
    # bot-inclusive); name-only collisions stay in uncertain_name_matches, never
    # silently merged.
    author_roster = [{"author": group_display[key], "commits": count}
                     for key, count in sorted(group_totals.items(),
                                              key=lambda x: (-x[1], group_display[x[0]]))]
    shortlog = shortlog_authors(repo, since)
    status = "partial" if comp["shallow"] or backend.startswith("plain-git") else "complete"
    return {
        "backend": backend, "coverage_status": status, "since": since,
        "history_completeness": comp, "commits_used": len(commits),
        "bulk_changesets_excluded_from_coupling": bulk_excluded,
        "coupling_sample_cap": coupling_sample_cap,
        "coupling_commits_used": len(coupling_commits),
        "coupling_sampled": coupling_sampled,
        "author_roster": author_roster,
        "distinct_authors_strong": len(author_roster),
        "git_shortlog_author_count": len(shortlog),
        "uncertain_name_matches": resolver.uncertain_name_matches,
        "rename_aliases": aliases, "churn": churn[:top], "coupling": coupling[:top],
        "cross_dir_coupling": cross_dir_coupling[:top],
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
    parser.add_argument("--coupling-sample-cap", type=int, default=0,
                        help="cap the number of commits fed into the co-change "
                             "pass (0 = no cap; evenly-spaced sample when exceeded)")
    args = parser.parse_args()
    if args.version:
        try:
            print(f"pydriller {importlib.metadata.version('pydriller')}")
        except importlib.metadata.PackageNotFoundError:
            print("plain-git fallback")
        return
    if not args.repo or not args.since:
        parser.error("--repo and --since are required")
    print(json.dumps(
        analyze(args.repo, args.since, args.top, args.min_shared, args.bulk_limit,
                args.coupling_sample_cap),
        sort_keys=True))


if __name__ == "__main__":
    main()
