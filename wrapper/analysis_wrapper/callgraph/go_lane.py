"""Go call-graph lane — golang.org/x/tools/cmd/callgraph (VTA) as a pure CLI.

No custom Go helper: the ``-format`` 9-column TSV template already emits both
endpoints (symbol + package + declaration position), the call-site position, and
a static/dynamic flag. We invoke the pinned analyzer-owned binary under the
HARDENED OFFLINE env, slice to in-module edges (caller AND callee packages carry
the module prefix), map static->observed / dynamic->inferred, and cite each
position against the repo HEAD from provenance.

Whole-program by nature: a package load failure fails the lane CLOSED (a
disclosed ``failed`` coverage state), never a silently empty graph. A cold module
cache is the common failure; :mod:`analysis_wrapper.go_cache` warms it under
approval (the same warm-then-offline pattern as the go-list lane).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

from .. import go_cache, go_tools
from ..targetspec import RepoTarget
from . import contract, sources
from .contract import CallEdge, CallSiteCounts, RepoCoverage

# Hardened analysis env (identical to registry.SAFE_GO_ENV): offline, read-only,
# local toolchain, no workspace, CGO off. GOPROXY/GOSUMDB off => zero network.
ANALYSIS_ENV = {
    "GOFLAGS": "-mod=readonly",
    "GOTOOLCHAIN": "local",
    "GOWORK": "off",
    "CGO_ENABLED": "0",
    "GOPROXY": "off",
    "GOSUMDB": "off",
}
GO_ENV_REMOVALS = ("GOFLAGS", "GOTOOLCHAIN", "GOWORK")

# 9-col TSV: dynamic | description | caller_sym | caller_pkg | caller_decl |
# callsite | callee_sym | callee_pkg | callee_decl. `posn` is nil-safe and `.Pkg`
# is nil-guarded (synthetic funcs such as init have no *ssa.Package).
FORMAT = (
    "{{.Dynamic}}\t{{.Description}}\t{{.Caller}}\t"
    "{{if .Caller.Pkg}}{{.Caller.Pkg.Pkg.Path}}{{end}}\t"
    "{{(posn .Caller).Filename}}:{{(posn .Caller).Line}}\t"
    "{{.Filename}}:{{.Line}}:{{.Column}}\t{{.Callee}}\t"
    "{{if .Callee.Pkg}}{{.Callee.Pkg.Pkg.Path}}{{end}}\t"
    "{{(posn .Callee).Filename}}:{{(posn .Callee).Line}}"
)

_MODULE_RE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
_COLD_CACHE = re.compile(
    r"cannot find module|missing go\.sum|no required module|"
    r"GOPROXY=off|not in std|cannot query module", re.IGNORECASE)

_GO_NOTE = ("analyzed = production .go in loaded packages; files excluded by build "
            "constraints (GOOS/GOARCH/build tags) are outside scope, as in the "
            "go-list lane. Unresolved dynamic dispatch that VTA could not target "
            "yields no edge and is not separately counted (Go ambiguous/unresolved "
            "call sites are not observable from callgraph output).")


def module_path(repo_root: str | Path) -> str | None:
    """The Go module path from the repo-root go.mod ``module`` directive, or
    None when there is no module there."""
    gomod = Path(repo_root) / "go.mod"
    if not gomod.is_file():
        return None
    try:
        text = gomod.read_text("utf-8", errors="replace")
    except OSError:
        return None
    match = _MODULE_RE.search(text)
    return match.group(1).strip().strip('"') if match else None


def _in_module(pkg: str, module: str) -> bool:
    return pkg == module or pkg.startswith(module + "/")


def _clean_symbol(sym: str, module: str) -> str:
    """Drop the module prefix so symbols read repo-relative:
    ``mod/internal/h.F`` -> ``internal/h.F`` and ``(*mod/p.T).M`` -> ``(*p.T).M``."""
    return sym.replace(module + "/", "").replace(module + ".", "") if module else sym


def _cite(pos: str, repo_id: str, commit: str, repo_root: Path) -> str:
    return contract.citation_from_position(pos, repo_id, commit, repo_root)


def parse_tsv(text: str, *, module: str, repo_id: str, commit: str,
              repo_root: Path, prod_files: set[str]) -> tuple[list[CallEdge], CallSiteCounts]:
    """Parse the 9-col TSV into sorted, de-duplicated in-module call edges.

    ``callgraph ./...`` compiles every in-module package including generated and
    mock files, so an edge is dropped from EMISSION when the call site or the
    callee declaration lives in an in-repo file the production boundary EXCLUDES
    (``prod_files`` is the exact production set from :func:`sources.walk`) — this
    makes the Go lane classify identically to the JS lane's prodSet emission
    (57B-30 boundary). A decl with no source position (a synthetic function such
    as a package initializer or a bound-method thunk) is NOT an excluded file, so
    such edges are kept (unchanged from before this gate). A production call site
    with any production in-module callee is ``resolved``; one whose callees are
    all external OR excluded-internal (generated/mock) is ``external`` and never
    emitted. Call sites in excluded files are not counted at all. Static-flagged
    edges -> ``observed``; dynamic -> ``inferred``.
    """
    cache: dict[str, bool] = {}

    def excluded(path: str) -> bool:
        """True only for a REAL in-repo file the production boundary drops. An
        empty position (synthetic func, no source) is not an exclusion."""
        if not path:
            return False
        if path not in cache:
            try:
                cache[path] = str(Path(path).resolve()) not in prod_files
            except OSError:
                cache[path] = True
        return cache[path]

    edges: set[CallEdge] = set()
    internal_sites: set[str] = set()
    external_sites: set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 9:
            continue
        dyn, desc, caller_sym, caller_pkg, caller_decl, callsite, callee_sym, callee_pkg, callee_decl = cols[:9]
        if not module or not _in_module(caller_pkg, module):
            continue
        if not (caller_sym and callee_sym and callsite):
            continue
        if excluded(contract.position_file(callsite)):
            continue                                  # call site is in an excluded file
        callsite_cit = _cite(callsite, repo_id, commit, repo_root)
        if _in_module(callee_pkg, module) and not excluded(contract.position_file(callee_decl)):
            internal_sites.add(callsite_cit)
            edges.add(CallEdge(
                lang="go",
                resolution="inferred" if dyn == "dynamic" else "observed",
                kind="method-dispatch" if "method" in desc else "static-call",
                caller_symbol=_clean_symbol(caller_sym, module),
                caller_citation=_cite(caller_decl, repo_id, commit, repo_root),
                callee_symbol=_clean_symbol(callee_sym, module),
                callee_citation=_cite(callee_decl, repo_id, commit, repo_root),
                callsite_citation=callsite_cit,
            ))
        else:
            external_sites.add(callsite_cit)
    counts = CallSiteCounts(
        resolved=len(internal_sites),
        external=len(external_sites - internal_sites),
    )
    return sorted(edges, key=lambda e: e.sort_key()), counts


def analyze(target: RepoTarget, *, repository_ref: str,
            allow_network: bool = False,
            run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
            warm: Callable[..., tuple[bool, str]] = go_cache.warm,
            bin_dir: Path | None = None,
            timeout_s: int = 900) -> tuple[list[CallEdge], RepoCoverage]:
    """Run the Go lane for one repo. Returns ``(edges, coverage)``."""
    # Resolved lazily (never a module-level/default-parameter constant): see
    # go_tools.default_bin_dir()'s docstring for why that matters.
    bin_dir = bin_dir if bin_dir is not None else go_tools.default_bin_dir()
    repo_root = Path(target.path).expanduser().resolve()
    commit = target.git.head
    module = module_path(repo_root)
    # analysis_roots is intentionally NOT applied here (it IS in the JS lane):
    # `callgraph ./...` loads the whole Go module from the repo root, so the
    # candidate set is the module's production .go — scoping the walk to a subset
    # of roots would desync candidate/analyzed counts from what the tool loads.
    prod, cand_ext, excl = sources.walk(
        repo_root, exts=sources.GO_EXTS,
        tier2_dirs=frozenset(target.tier2_exclusions))
    prod_files = {str(p) for p in prod}
    binary, note = go_tools.resolve(bin_dir)
    version = go_tools.installed_version(binary, run=run) if binary else ""

    def cov(status: str, *, reason: str = "", warm_cache: str = "n/a",
            call_sites: CallSiteCounts | None = None, edges: int = 0,
            analyzed: dict | None = None) -> RepoCoverage:
        return RepoCoverage(
            repository_ref=repository_ref,
            lang="go", status=status, reason=reason,
            tool=go_tools.CALLGRAPH_PKG, tool_version=version, algorithm="vta",
            warm_cache=warm_cache, candidates_by_ext=cand_ext,
            analyzed_by_ext=analyzed or {}, excluded_by_reason=excl,
            call_sites=call_sites or CallSiteCounts(), edges_emitted=edges,
            notes=_GO_NOTE + (f" {note}" if note else ""))

    if binary is None:
        return [], cov("unavailable", reason=note)
    if module is None:
        return [], cov("failed", reason="no `module` directive in go.mod at repo "
                                        "root; the call graph needs a Go module")
    if not cand_ext:
        return [], cov("complete", reason="no production Go source")

    warm_cache = "n/a"
    if allow_network:
        ok, detail = warm(str(repo_root), run=run)
        warm_cache = "warm" if ok else "cold"
        if not ok:
            note_warm = f"; module-cache warm failed: {detail}"
        else:
            note_warm = ""
    else:
        note_warm = ""

    env = {k: v for k, v in os.environ.items() if k not in GO_ENV_REMOVALS}
    env.update(ANALYSIS_ENV)
    argv = [str(binary), "-algo=vta", "-format=" + FORMAT, "./..."]
    try:
        proc = run(argv, cwd=str(repo_root), env=env, capture_output=True,
                   text=True, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], cov("failed", reason=f"callgraph did not complete: {exc}",
                       warm_cache=warm_cache)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "(no output)").strip()[:300]
        if _COLD_CACHE.search(detail) and warm_cache != "warm":
            detail += ("  [likely a cold module cache — warm it through your "
                       "developer-managed Go workflow or pass --include-network, "
                       "then rerun]")
        return [], cov("failed", reason=detail, warm_cache=warm_cache)

    edges, counts = parse_tsv(proc.stdout or "", module=module,
                              repo_id=repository_ref, commit=commit,
                              repo_root=repo_root, prod_files=prod_files)
    analyzed = dict(cand_ext)          # loader parses all production .go in-module
    status = contract.coverage_status(cand_ext, analyzed, 0)
    # An offline run that succeeded without an explicit warm step proves the
    # module cache was already sufficient — record that rather than a bare "n/a".
    if warm_cache == "n/a":
        warm_cache = "already-warm"
    return edges, cov(status, warm_cache=warm_cache, call_sites=counts,
                      edges=len(edges), analyzed=analyzed,
                      reason=note_warm.lstrip("; ") if note_warm else "")
