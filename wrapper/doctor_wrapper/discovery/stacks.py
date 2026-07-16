"""Stack + analysis-root detection (57B-11 S2).

Mechanical, evidence-backed: stacks come from manifest files (package.json,
go.mod, tsconfig*) and source-file presence; analysis roots come from where
those manifests and sources actually live. Polyglot repos emit multiple roots.
Framework fingerprints are recorded as evidence for later stages (module
candidates) — frameworks are structural facts, not integration name lists.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Dirs never scanned for manifests/sources (mirrors Tier-1).
_SKIP = {"node_modules", "vendor", ".git", "dist", "build", "coverage"}
# Conventional single-source-tree dir: used as the analysis root when present.
_SRC_DIR = "src"
_JS_EXT = {".js", ".jsx", ".mjs", ".cjs"}
_TS_EXT = {".ts", ".tsx", ".mts", ".cts"}
# Framework fingerprints — structural evidence only (web framework, UI library,
# build tool); NEVER an integration list (plan §17.7).
_JS_FRAMEWORKS = {
    "react", "vue", "@angular/core", "svelte", "next", "nuxt",
    "express", "koa", "fastify", "@nestjs/core", "hapi",
    "vite", "webpack",
}
_GO_FRAMEWORKS = {
    "github.com/gin-gonic/gin", "github.com/labstack/echo",
    "github.com/gofiber/fiber", "github.com/go-chi/chi",
    "github.com/gorilla/mux", "gorm.io/gorm",
}
_GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?([\w./-]+)\s+v[\w.+-]+", re.M)


@dataclass
class StackReport:
    stacks: list[str] = field(default_factory=list)          # subset of {js, ts, go}; empty = no first-class stack detected (reduced coverage downstream)
    analysis_roots: list[str] = field(default_factory=list)  # repo-relative; [] = repo root
    frameworks: list[str] = field(default_factory=list)      # fingerprints, evidence-backed
    evidence: list[str] = field(default_factory=list)        # one line per conclusion


def _iter_dirs(root: Path, max_depth: int = 2):
    """Yield root and shallow subdirectories (manifest hosts), pruned + sorted."""
    yield root
    if max_depth <= 0:
        return
    stack = [(root, 0)]
    while stack:
        base, depth = stack.pop()
        try:
            children = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            continue
        for child in children:
            if child.name in _SKIP or child.name.startswith("."):
                continue
            yield child
            if depth + 1 < max_depth:
                stack.append((child, depth + 1))


def _has_source(directory: Path, extensions: set[str], limit: int = 400) -> bool:
    seen = 0
    try:
        stack = [directory]
        while stack:
            base = stack.pop()
            for entry in sorted(base.iterdir()):
                if entry.is_dir():
                    if entry.name not in _SKIP and not entry.name.startswith("."):
                        stack.append(entry)
                elif entry.suffix in extensions:
                    return True
                seen += 1
                if seen >= limit:
                    return False
    except OSError:
        pass
    return False


def _js_frameworks(manifest: Path) -> list[str]:
    try:
        data = json.loads(manifest.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    deps: set[str] = set()
    for section in ("dependencies", "devDependencies"):
        deps.update((data.get(section) or {}).keys())
    return sorted(deps & _JS_FRAMEWORKS)


def _go_frameworks(gomod: Path) -> list[str]:
    try:
        text = gomod.read_text("utf-8", errors="replace")
    except OSError:
        return []
    modules = set(_GO_REQUIRE.findall(text))
    return sorted(modules & _GO_FRAMEWORKS)


def _relative(root: Path, directory: Path) -> str:
    return "" if directory == root else directory.relative_to(root).as_posix()


def detect(repo_path: str | Path) -> StackReport:
    root = Path(repo_path).expanduser().resolve()
    report = StackReport()
    stacks: set[str] = set()
    roots: set[str] = set()

    for directory in _iter_dirs(root):
        rel = _relative(root, directory)
        label = rel or "."

        gomod = directory / "go.mod"
        if gomod.is_file():
            stacks.add("go")
            roots.add(rel)
            report.evidence.append(f"go: go.mod at {label}")
            for framework in _go_frameworks(gomod):
                report.frameworks.append(framework)
                report.evidence.append(f"framework {framework}: required in {label}/go.mod")

        manifest = directory / "package.json"
        if manifest.is_file():
            node_root = directory
            # Conventional src/ tree wins as the analysis root for this app.
            src = directory / _SRC_DIR
            uses_src = src.is_dir() and (
                _has_source(src, _JS_EXT | _TS_EXT)
            )
            chosen = _relative(root, src) if uses_src else rel
            has_ts = any((directory / name).is_file() for name in
                         ("tsconfig.json", "tsconfig.app.json", "tsconfig.base.json")) \
                or _has_source(src if uses_src else directory, _TS_EXT)
            has_js = _has_source(src if uses_src else directory, _JS_EXT)
            if has_ts:
                stacks.add("ts")
                report.evidence.append(f"ts: tsconfig/ts sources under {chosen or '.'}")
            if has_js or not has_ts:
                stacks.add("js")
                report.evidence.append(f"js: package.json at {label}")
            roots.add(chosen)
            for framework in _js_frameworks(manifest):
                report.frameworks.append(framework)
                report.evidence.append(f"framework {framework}: dependency in {label}/package.json")

    # Roots: "" (repo root) subsumes everything — scanning the root already
    # covers subdirectories, so only emit named roots when the root itself
    # is NOT a root (pure sub-app / polyglot layouts).
    if "" in roots:
        named = sorted(r for r in roots if r)
        report.analysis_roots = []
        if named:
            report.evidence.append(
                "analysis roots collapsed to repo root (sub-roots "
                + ", ".join(named) + " are covered by the root scan)"
            )
    else:
        report.analysis_roots = sorted(r for r in roots if r)

    report.stacks = sorted(stacks)
    report.frameworks = sorted(set(report.frameworks))
    report.evidence.sort()
    return report
