"""Production-source boundary for the call graph (57B-30 canonical rules).

The call graph covers production EXECUTABLE source, not every parseable file.
This module is the single source of truth for that boundary so the Go and JS/TS
lanes classify identically; the JS lane also feeds the resulting file list to the
node extractor, so the boundary is implemented ONCE, here.

Included extensions (eligible candidates):
  JS  .js .jsx .mjs .cjs      TS  .ts .tsx .mts .cts      Go  .go

An eligible file is EXCLUDED (counted, never emitted) when it is a test, mock,
fixture/example/demo/sample/benchmark, generated, vendored, build output,
migration/seed, or tooling/build/lint configuration. Templates, styles, and
static assets fall outside the included extensions and so are never candidates.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

JS_EXTS = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})
GO_EXTS = frozenset({".go"})
ALL_EXTS = JS_EXTS | GO_EXTS

# Directory component -> exclusion reason. A path is excluded if ANY of its
# directory components matches.
_DIR_REASONS: dict[str, str] = {}
for _d in ("__tests__", "test", "tests", "testdata", "__snapshots__"):
    _DIR_REASONS[_d] = "test"
for _d in ("__mocks__", "mock", "mocks"):
    _DIR_REASONS[_d] = "mock"
for _d in ("fixture", "fixtures", "example", "examples", "demo", "demos",
           "sample", "samples", "benchmark", "benchmarks", "bench",
           "stories", "__stories__"):
    _DIR_REASONS[_d] = "fixture-or-example"
for _d in ("vendor", "node_modules"):
    _DIR_REASONS[_d] = "vendored"
for _d in ("dist", "build", "coverage", "out", ".next", ".nuxt", ".turbo", ".output"):
    _DIR_REASONS[_d] = "build-output"
for _d in ("generated", "__generated__"):
    _DIR_REASONS[_d] = "generated"
for _d in ("migration", "migrations", "seed", "seeds", "seeders"):
    _DIR_REASONS[_d] = "migration-or-seed"

# Config-file basenames (or basename stems) -> "config". `.config.<ext>` and the
# common tool configs are matched dynamically below; this set is the exact-name
# tail (dotfile-style JS configs).
_CONFIG_STEMS = frozenset({
    "vite", "vitest", "jest", "rollup", "webpack", "babel", "tailwind",
    "postcss", "cypress", "playwright", "next", "nuxt", "svelte", "astro",
    "commitlint", "stylelint", "prettier", "eslint", "karma", "metro",
    "jasmine", "nyc", "release", "lint-staged", "size-limit",
})
_CONFIG_DOTFILES = frozenset({
    ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.mjs",
    ".prettierrc.js", ".prettierrc.cjs",
    ".stylelintrc.js", ".stylelintrc.cjs",
    ".babelrc.js", ".mocharc.js", ".mocharc.cjs", ".lintstagedrc.js",
})
_CONFIG_EXACT = frozenset({
    "gulpfile.js", "gruntfile.js", "gulpfile.ts", "gruntfile.ts",
})
_CONFIG_EXTS = frozenset({".js", ".ts", ".mjs", ".cjs", ".mts", ".cts"})

_GENERATED_GO_MARKER = re.compile(r"Code generated .* DO NOT EDIT", re.IGNORECASE)
_GENERATED_MARKER_BYTES = 4096


def ext_of(name: str) -> str:
    """Included extension for ``name`` (handling multi-dot ``.d.ts``/``.pb.go``),
    or ``""`` when the file is not an eligible candidate at all."""
    lower = name.lower()
    if lower.endswith(".d.ts") or lower.endswith(".d.mts") or lower.endswith(".d.cts"):
        return ".d.ts"          # declaration file — eligible ext family, excluded below
    dot = lower.rfind(".")
    if dot < 0:
        return ""
    suffix = lower[dot:]
    return suffix if suffix in ALL_EXTS else ""


def path_exclusion_reason(rel_path: str, *, tier2_dirs: frozenset[str] = frozenset()) -> str:
    """Path-only exclusion classification (no file I/O). Returns the reason a
    candidate is excluded, or ``""`` when it is production source.

    ``tier2_dirs`` are discovery-derived generated/excluded directories carried
    on the TargetSpec; a hit there is disclosed as ``tier2-excluded``.
    """
    parts = Path(rel_path).parts
    name = parts[-1] if parts else rel_path
    lower = name.lower()

    for comp in parts[:-1]:
        if comp in tier2_dirs:
            return "tier2-excluded"
        reason = _DIR_REASONS.get(comp)
        if reason:
            return reason

    if lower.endswith(".d.ts") or lower.endswith(".d.mts") or lower.endswith(".d.cts"):
        return "declaration"
    if re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", lower):
        return "test"
    if re.search(r"\.stories\.[cm]?[jt]sx?$", lower):
        return "fixture-or-example"
    # Go naming conventions.
    if lower.endswith("_test.go"):
        return "test"
    if lower.endswith(".gen.go") or lower.endswith("_gen.go") \
            or lower.endswith(".pb.go") or lower.endswith(".pb.gw.go") \
            or lower.endswith("_generated.go"):
        return "generated"
    if lower.endswith("_mock.go") or lower.startswith("mock_") or lower.startswith("mocks_"):
        return "mock"
    # Tooling / build / lint configuration.
    if name in _CONFIG_DOTFILES or name in _CONFIG_EXACT:
        return "config"
    m = re.match(r"^(.+?)\.config\.([cm]?[jt]s)$", lower)
    if m:
        return "config"
    stem_dot = lower.find(".")
    stem = lower[:stem_dot] if stem_dot > 0 else lower
    suffix = lower[lower.rfind("."):] if "." in lower else ""
    if stem in _CONFIG_STEMS and suffix in _CONFIG_EXTS and (".config." in lower or ".conf." in lower):
        return "config"
    return ""


def _is_generated_go(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return bool(_GENERATED_GO_MARKER.search(stream.read(_GENERATED_MARKER_BYTES)))
    except OSError:
        return False


# Directories descended into but whose eligible files are counted as excluded.
# Directories PRUNED entirely (never descended, never counted): vendored trees,
# build output, and version control — counting every file inside a vendored
# dependency would be meaningless and slow.
_PRUNE_REASONS = frozenset({"vendored", "build-output"})


def _prune_dir(name: str, tier2_dirs: frozenset[str]) -> bool:
    if name == ".git" or name in tier2_dirs:
        return True
    return _DIR_REASONS.get(name) in _PRUNE_REASONS


def walk(root: Path, *, exts: frozenset[str], tier2_dirs: frozenset[str] = frozenset(),
         analysis_roots: list[str] | None = None) -> tuple[list[Path], dict[str, int], dict[str, int]]:
    """Walk ``root`` for eligible source files and split them into production
    candidates and excluded-with-reason.

    Returns ``(production_files, candidates_by_ext, excluded_by_reason)``:
    ``candidates_by_ext`` counts the returned production files;
    ``excluded_by_reason`` counts eligible-but-excluded IN-TREE files (tests,
    mocks, generated, config, ...). Vendored/build/VCS directories are pruned
    outright — not descended and not counted.
    """
    root = root.resolve()
    bases = [root / r for r in analysis_roots] if analysis_roots else [root]
    production: list[Path] = []
    candidates_by_ext: dict[str, int] = {}
    excluded_by_reason: dict[str, int] = {}

    def bump(table: dict[str, int], key: str) -> None:
        table[key] = table.get(key, 0) + 1

    seen: set[Path] = set()
    for base in bases:
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not _prune_dir(d, tier2_dirs))
            for fname in sorted(filenames):
                ext = ext_of(fname)
                if not ext:
                    continue
                if ext == ".d.ts":
                    if exts is GO_EXTS:
                        continue                    # .d.ts is a JS-family artifact
                elif ext not in exts:
                    continue
                path = Path(dirpath) / fname
                try:
                    rel = path.resolve().relative_to(root)
                except (OSError, ValueError):
                    continue
                if path in seen:
                    continue
                seen.add(path)
                reason = path_exclusion_reason(str(rel), tier2_dirs=tier2_dirs)
                if not reason and ext == ".go" and _is_generated_go(path):
                    reason = "generated"
                if reason:
                    bump(excluded_by_reason, reason)
                    continue
                production.append(path.resolve())
                bump(candidates_by_ext, ext)
    production.sort()
    return production, candidates_by_ext, excluded_by_reason
