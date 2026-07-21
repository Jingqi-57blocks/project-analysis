"""JS/TS call-graph lane — the pinned TypeScript compiler + a thin extractor.

Mirrors how :mod:`resolvers.ts_aliases` drives a node helper: node comes from
PATH, the compiler is the analyzer-owned pinned typescript (passed via
``ANALYSIS_TS_LIB`` — never one resolved from a target). The PRODUCTION boundary
is computed here in Python (:mod:`.sources`) and handed to the extractor as an
explicit file list, so the boundary lives in one place. Only concrete
resolved-INTERNAL callees become edges; external/ambiguous/unresolved sites are
counted in coverage and never emitted.

When node, the helper, or the analyzer typescript lib is unavailable, the lane
fails CLOSED to a disclosed ``unavailable`` coverage state.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .. import node_env
from ..targetspec import RepoTarget
from . import contract, sources
from .contract import CallEdge, CallSiteCounts, RepoCoverage

_HELPER = node_env.NODE_HELPERS_DIR / "extract-calls.mjs"
_TSCONFIG_NAMES = ("tsconfig.app.json", "tsconfig.json")


def _tsconfig(root: Path) -> str:
    for name in _TSCONFIG_NAMES:
        if (root / name).is_file():
            return name
    return ""


def _module_kind(root: Path) -> str:
    """CommonJS unless package.json declares ``"type": "module"`` (ESM)."""
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            if json.loads(pkg.read_text("utf-8")).get("type") == "module":
                return "esnext"
        except (OSError, ValueError):
            pass
    return "commonjs"


def _edges_from(payload: dict, target: RepoTarget, repo_root: Path) -> list[CallEdge]:
    commit = target.git.head
    edges: set[CallEdge] = set()
    for raw in payload.get("edges", []):
        try:
            edges.add(CallEdge(
                lang=raw["lang"],
                resolution="observed",     # JS edges are concrete resolved decls
                kind=raw["kind"],
                caller_symbol=raw["callerSymbol"] or "<module>",
                caller_citation=contract.citation_from_position(
                    raw["callerDecl"], target.repo_id, commit, repo_root),
                callee_symbol=raw["calleeSymbol"],
                callee_citation=contract.citation_from_position(
                    raw["calleeDecl"], target.repo_id, commit, repo_root),
                callsite_citation=contract.citation_from_position(
                    raw["callsite"], target.repo_id, commit, repo_root),
            ))
        except (KeyError, ValueError):
            continue
    return sorted(edges, key=lambda e: e.sort_key())


def _analyzed_by_ext(analyzed: list[str], repo_root: Path,
                     candidates: dict[str, int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in analyzed:
        ext = sources.ext_of(Path(path).name)
        if ext in candidates:
            counts[ext] = counts.get(ext, 0) + 1
    return counts


def analyze(target: RepoTarget, *,
            run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
            node: str | None = None,
            probe: Callable[[], node_env.NodeToolInfo] = node_env.probe,
            timeout_s: int = 600) -> tuple[list[CallEdge], RepoCoverage]:
    """Run the JS/TS lane for one repo. Returns ``(edges, coverage)``."""
    repo_root = Path(target.path).expanduser().resolve()
    tsconfig = _tsconfig(repo_root)
    mode = "tsconfig" if tsconfig else "inferred"
    prod, cand_ext, excl = sources.walk(
        repo_root, exts=sources.JS_EXTS,
        tier2_dirs=frozenset(target.tier2_exclusions),
        analysis_roots=list(target.analysis_roots))
    ts_lib = node_env.typescript_lib()
    info = probe()

    def cov(status: str, *, reason: str = "", version: str = "",
            call_sites: CallSiteCounts | None = None, edges: int = 0,
            analyzed: dict | None = None, failures: int = 0,
            notes: str = "") -> RepoCoverage:
        return RepoCoverage(
            repo_id=target.repo_id, lang="ts" if tsconfig else "js", status=status,
            reason=reason, tool="typescript", tool_version=version or info.typescript_version,
            algorithm=mode, candidates_by_ext=cand_ext, analyzed_by_ext=analyzed or {},
            excluded_by_reason=excl, parse_load_failures=failures,
            call_sites=call_sites or CallSiteCounts(), edges_emitted=edges, notes=notes)

    node_bin = node or shutil.which("node")
    if not node_bin or not _HELPER.is_file() or not ts_lib.exists():
        return [], cov("unavailable", reason=(
            "JS/TS call graph unavailable: node, the extractor helper, or the "
            "analyzer typescript lib is missing — follow the manual JS/TS "
            "prerequisite step in README.md"))
    if not cand_ext:
        return [], cov("complete", reason="no production JS/TS source")

    fd, listpath = tempfile.mkstemp(prefix="callgraph-files-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(str(p) for p in prod) + "\n")
        argv = [node_bin, str(_HELPER), "--repo", str(repo_root),
                "--mode", mode, "--files", listpath]
        if tsconfig:
            argv += ["--tsconfig", tsconfig]
        else:
            argv += ["--module", _module_kind(repo_root)]
        env = {**os.environ, "ANALYSIS_TS_LIB": str(ts_lib)}
        try:
            proc = run(argv, capture_output=True, text=True, timeout=timeout_s, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [], cov("failed", reason=f"extractor did not complete: {exc}")
    finally:
        Path(listpath).unlink(missing_ok=True)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "(no output)").strip()[:300]
        return [], cov("failed", reason=f"extractor exit {proc.returncode}: {detail}")
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        return [], cov("failed", reason=f"extractor output invalid JSON: {exc}")
    if "error" in payload:
        return [], cov("failed", reason=str(payload["error"]))

    edges = _edges_from(payload, target, repo_root)
    raw = payload.get("counts", {})
    counts = CallSiteCounts(
        resolved=int(raw.get("resolved", 0)), ambiguous=int(raw.get("ambiguous", 0)),
        external=int(raw.get("external", 0)), unresolved=int(raw.get("unresolved", 0)))
    failures = len(payload.get("failed", []))
    analyzed = _analyzed_by_ext(payload.get("analyzed", []), repo_root, cand_ext)
    status = contract.coverage_status(cand_ext, analyzed, failures)
    notes = (f"typescript {payload.get('tsVersion', '?')} mode={mode}; "
             "edges are concrete resolved-internal callees only; ambiguous "
             "(interface/signature) and unresolved (dynamic require/import) sites "
             "are counted, never emitted.")
    return edges, cov(status, version=payload.get("tsVersion", ""), call_sites=counts,
                      edges=len(edges), analyzed=analyzed, failures=failures, notes=notes)
