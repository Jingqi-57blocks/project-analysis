"""Go dependency-map lane — ``go list -deps -json ./...`` as a pure CLI.

Phase-0 validated (spike ``go-deps-validation.md``): ``go list -deps -json`` gives
a usable INTERNAL package dependency graph (``go mod graph`` is NOT suitable — it
exposes only the module requirement graph). We run the pinned system ``go`` under
the SAME hardened offline env as the call-graph Go lane (GOPROXY/GOSUMDB off ⇒
zero network) and, behind ``--include-network``, warm the module cache once via
:func:`analysis_wrapper.go_cache.warm` (an explicitly authorized Go network operation).

The raw ``go list`` stream carries absolute machine paths (``Dir``/``Root``) — a
leak — and non-deterministic object order, so this lane NEVER writes it. It emits
a leak-free, byte-deterministic PROJECTION: the module path, each internal
package's imports, and the referenced-stdlib set. The internal/external split and
edge emission belong to the normalizer
(:mod:`analysis_wrapper.system_model.from_go_imports`), which the projection
carries just enough for. Read-only: ``-mod=readonly`` — go.mod/go.sum are never
modified.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .. import go_cache
from ..callgraph.go_lane import ANALYSIS_ENV, GO_ENV_REMOVALS, module_path
from ..targetspec import RepoTarget
from .contract import RepoDepCoverage

TOOL = "go list -deps -json"
_COLD_CACHE = ("cannot find module", "missing go.sum", "no required module",
               "GOPROXY=off", "cannot query module")


def parse_stream(text: str) -> list[dict]:
    """Parse the concatenated-JSON stream ``go list -json`` emits (a sequence of
    objects, NOT a JSON array)."""
    decoder = json.JSONDecoder()
    objs: list[dict] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        obj, i = decoder.raw_decode(text, i)
        objs.append(obj)
    return objs


def _is_internal(import_path: str, module: str) -> bool:
    return import_path == module or import_path.startswith(module + "/")


def project(text: str, module: str) -> dict:
    """Project the raw ``go list`` stream to a leak-free, deterministic map.

    Keeps only import-path identifiers (never filesystem paths): every internal
    package with its sorted imports, plus the set of stdlib packages those
    imports reference (``Standard: true`` in ``go list``'s own output) so the
    normalizer can tell stdlib from third-party without the raw stream."""
    internal: dict[str, list[str]] = {}
    stdlib: set[str] = set()
    for obj in parse_stream(text):
        import_path = obj.get("ImportPath", "")
        if not import_path:
            continue
        if obj.get("Standard"):
            stdlib.add(import_path)
        if _is_internal(import_path, module):
            internal[import_path] = sorted({d for d in obj.get("Imports", []) or [] if d})
    referenced_std = sorted(
        {d for imports in internal.values() for d in imports if d in stdlib})
    return {
        "module": module,
        "packages": [{"import_path": ip, "imports": internal[ip]}
                     for ip in sorted(internal)],
        "stdlib": referenced_std,
    }


def analyze(target: RepoTarget, *, repository_ref: str, artifact_key: str,
            allow_network: bool = False,
            run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
            warm: Callable[..., tuple[bool, str]] = go_cache.warm,
            go_binary: str | None = None,
            timeout_s: int = 300) -> tuple[dict | None, RepoDepCoverage]:
    """Run the Go dependency-map lane for one repo. Returns ``(payload, cov)``;
    ``payload`` is None when nothing usable was produced (disclosed in ``cov``)."""
    repo_root = Path(target.path).expanduser().resolve()
    module = module_path(repo_root)
    binary = go_binary or shutil.which("go")

    def cov(status: str, *, reason: str = "", units: int = 0,
            warm_cache: str = "n/a", reference_counts: dict | None = None
            ) -> RepoDepCoverage:
        return RepoDepCoverage(
            repository_ref=repository_ref, lane="go", status=status, reason=reason,
            tool=TOOL, map_file=f"{artifact_key}.golist.json" if status == "complete" else "",
            units=units, warm_cache=warm_cache,
            reference_counts=dict(reference_counts or {}),
            notes="internal package import graph; stdlib/third-party imports "
                  "counted by the normalizer, never conflated with call edges.")

    if binary is None:
        return None, cov("unavailable", reason="go not found on PATH")
    if module is None:
        return None, cov("failed", reason="no `module` directive in go.mod at repo "
                                          "root; the dependency map needs a Go module")

    warm_cache = "n/a"
    if allow_network:
        ok, detail = warm(str(repo_root), run=run)
        warm_cache = "warm" if ok else "cold"

    env = {k: v for k, v in os.environ.items() if k not in GO_ENV_REMOVALS}
    env.update(ANALYSIS_ENV)
    argv = [binary, "list", "-deps", "-json", "./..."]
    try:
        proc = run(argv, cwd=str(repo_root), env=env, capture_output=True,
                   text=True, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, cov("failed", reason=f"go list did not complete: {exc}",
                         warm_cache=warm_cache)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "(no output)").strip()[:300]
        if any(s in detail for s in _COLD_CACHE) and warm_cache != "warm":
            detail += ("  [likely a cold module cache — pass --include-network to "
                       "warm it, then rerun]")
        return None, cov("failed", reason=detail, warm_cache=warm_cache)

    try:
        payload = project(proc.stdout or "", module)
    except ValueError as exc:
        return None, cov("failed", reason=f"go list output not parseable: {exc}",
                         warm_cache=warm_cache)
    if warm_cache == "n/a":
        warm_cache = "already-warm"
    internal = external = stdlib_count = 0
    stdlib = set(payload.get("stdlib", []))
    for package in payload.get("packages", []):
        for dependency in package.get("imports", []):
            if _is_internal(dependency, module):
                internal += 1
            elif dependency in stdlib:
                stdlib_count += 1
            else:
                external += 1
    return payload, cov("complete", units=len(payload["packages"]),
                        warm_cache=warm_cache, reference_counts={
                            "internal": internal, "third_party": external,
                            "stdlib": stdlib_count,
                            "total": internal + external + stdlib_count,
                        })
