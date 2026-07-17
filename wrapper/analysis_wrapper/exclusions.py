"""Shared exclusion helpers for the tool-definition layer.

Tier-1 universal dirs/globs are safe on ANY repo. Per-project (Tier-2)
exclusions come ONLY from ``TargetSpec.tier2_exclusions`` (derived by discovery,
disclosed in every manifest) — never baked in here. Extracted from registry so
both the registry and the per-lane modules (depcruise) share one definition
without a circular import.
"""

from __future__ import annotations

import re

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
