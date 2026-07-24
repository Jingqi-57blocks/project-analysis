"""Shared exclusion helpers for the tool-definition layer.

Tier-1 universal dirs/globs are safe on ANY repo. Per-project (Tier-2)
exclusions come ONLY from ``TargetSpec.tier2_exclusions`` (derived by discovery,
disclosed in every manifest) — never baked in here. Extracted from registry so
both the registry and the per-lane modules (depcruise) share one definition
without a circular import.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .targetspec import RepoTarget

# Tier 1 — universal, safe on ANY repo.
TIER1_DIRS = ["node_modules", "vendor", ".git", "dist", "build", "coverage"]
# Universal generated-FILE markers (naming conventions, not project claims).
TIER1_FILE_GLOBS = [
    "**/package-lock.json", "**/yarn.lock", "**/pnpm-lock.yaml",
    "**/*.min.js", "**/*.min.css", "**/*.gen.go", "**/*_gen.go",
    "**/swagger.json", "**/swagger.yaml",
]

NODE_ENV_REMOVALS = ["NODE_OPTIONS"]

# Shared stage-1 source-file extension set (57B-85): the exact value three
# discovery/*.py scanners (candidates.py, generated.py, liveness.py) each
# hand-duplicated identically. Extracted verbatim, not "corrected" — it is
# NOT the same as profiles/bundled.py's or callgraph/sources.py's own
# (broader) extension lists, which also include .mts/.cts; widening this set
# to match would change which files those three scanners walk on a real
# repo using .mts/.cts, a behavior change out of scope for a pure dedup.
SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go"}


def is_excluded_relative(target: RepoTarget, relative_path: str) -> bool:
    """Whether a repository-relative path is outside the analyzable source universe."""
    path = PurePosixPath(relative_path)
    parts = path.parts
    if any(name in parts for name in TIER1_DIRS):
        return True
    if any(path.match(pattern) or
           (pattern.startswith("**/") and path.match(pattern[3:]))
           for pattern in TIER1_FILE_GLOBS):
        return True
    for value in target.tier2_exclusions:
        normalized = value.strip("/")
        if normalized and (relative_path == normalized
                           or relative_path.startswith(normalized + "/")
                           or normalized in parts):
            return True
    return False


def _excluded_dirs(target: RepoTarget) -> list[str]:
    """Tier-1 universal dirs + this target's derived Tier-2 dirs, deduped."""
    seen: list[str] = []
    for name in TIER1_DIRS + list(target.tier2_exclusions):
        if name and name not in seen:
            seen.append(name)
    return seen


def _jscpd_ignores(targets: list[RepoTarget]) -> list[str]:
    dirs: list[str] = list(TIER1_DIRS)
    for target in targets:
        for name in target.tier2_exclusions:
            if name not in dirs:
                dirs.append(name)
    return [f"**/{d}/**" for d in dirs] + list(TIER1_FILE_GLOBS)


def _js_exclude_re(target: RepoTarget) -> str:
    names = [re.escape(d) for d in _excluded_dirs(target) if d not in {".git", "node_modules"}]
    return "^(" + "|".join(names) + ")"
