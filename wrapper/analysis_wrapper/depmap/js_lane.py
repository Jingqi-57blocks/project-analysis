"""JS/TS dependency-map lane — the analyzer-owned dependency-cruiser coupling map.

Thin driver over :func:`analysis_wrapper.depcruise_lane.dependency_cruiser`: that
ToolDef already owns everything hard (pinned node_tools binary — never a global or
target-resolved depcruise, TS alias resolution + generated config, safe
``--no-config`` flags, exclusions, corepack/NODE_OPTIONS env isolation, the
TS-support fail-closed guard). We reuse it verbatim through its executor-facing
API and only capture the FULL ``--output-type json`` module graph, which the
executor's bounded view would otherwise truncate.

The captured map is written to ``imports/<artifact-key>.depcruise.json`` in the RAW
dependency-cruiser shape (a ``modules`` array) the existing normalizer
(:mod:`analysis_wrapper.system_model.from_imports`) already consumes — only the
module/dependency lists are sorted so the file is byte-deterministic. depcruise
reports repo-relative source/resolved paths (cwd is the target), so the map is
leak-free; it is passed through the shared sanitizer on write regardless.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from .. import depcruise_lane
from ..exclusions import is_excluded_relative
from ..targetspec import RepoTarget
from .contract import RepoDepCoverage

TOOL = "dependency-cruiser"


def _sorted_map(payload: dict, internal_sources: set[str] | None = None) -> dict:
    """Project the depcruise payload to exactly the ``modules`` graph the
    normalizer (:mod:`from_imports`) consumes — ``source`` + each dependency's
    ``module``/``resolved``/``couldNotResolve``/``circular`` — sorted for
    byte-determinism. Dropping the rest (the ``summary`` block) removes both a
    determinism hazard and a leak vector: depcruise echoes the generated
    ``--config`` absolute path into ``summary.optionsUsed``, which varies per run
    dir. Source/resolved paths are repo-relative (cwd is the target). ``inRepo``
    is computed from an existing file inside the exact analysis roots; pseudo
    modules for built-ins and packages therefore cannot become internal files."""
    modules = []
    for module in sorted(payload.get("modules", []),
                         key=lambda m: m.get("source", "")):
        if internal_sources is not None and module.get("source", "") not in internal_sources:
            continue
        deps = sorted(module.get("dependencies", []),
                      key=lambda d: (d.get("resolved", ""), d.get("module", "")))
        modules.append({
            "source": module.get("source", ""),
            "dependencies": [{
                "module": d.get("module", ""),
                "resolved": d.get("resolved", ""),
                "couldNotResolve": bool(d.get("couldNotResolve", False)),
                "circular": bool(d.get("circular", False)),
                "dependencyTypes": sorted(set(d.get("dependencyTypes", []) or [])),
                "inRepo": (str(d.get("resolved", "")) in internal_sources
                           and "local" in (d.get("dependencyTypes", []) or [])
                           if internal_sources is not None else
                           "local" in (d.get("dependencyTypes", []) or [])),
            } for d in deps],
        })
    return {"modules": modules,
            "internal_sources": sorted(internal_sources) if internal_sources is not None
            else [module["source"] for module in modules if module.get("source")]}


def analyze(target: RepoTarget, out_dir: Path, *, repository_ref: str,
            artifact_key: str,
            run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
            timeout_s: int | None = None) -> tuple[dict | None, RepoDepCoverage]:
    """Run dependency-cruiser for one repo. Returns ``(payload, cov)``; ``payload``
    is None when no usable map was produced (disclosed in ``cov``)."""
    tooldef = depcruise_lane.dependency_cruiser(target)

    def cov(status: str, *, reason: str = "", units: int = 0,
            version: str = "", reference_counts: dict | None = None) -> RepoDepCoverage:
        return RepoDepCoverage(
            repository_ref=repository_ref, lane="js", status=status, reason=reason,
            tool=TOOL, tool_version=version,
            map_file=f"{artifact_key}.depcruise.json" if status == "complete" else "",
            units=units,
            reference_counts=dict(reference_counts or {}),
            notes="dependency-cruiser module graph; edges kept SEPARATE from the "
                  "language call-edge type by the normalizer.")

    binary = tooldef.resolved_binary()
    if binary is None:
        return None, cov("unavailable", reason=(
            "analyzer node_tools depcruise env not installed — follow the manual "
            "JS/TS prerequisite step in README.md"))
    guard = tooldef.check_guards(target)
    if guard:
        return None, cov("unavailable", reason=guard)
    prep = tooldef.run_prepare(target, out_dir, artifact_key)
    if not prep.ok:
        return None, cov("unavailable", reason=prep.reason or "prepare step failed")

    version = tooldef.probe_version(binary) or ""
    argv = tooldef.build_argv(target)
    try:
        proc = run(argv, cwd=str(Path(target.path).expanduser().resolve()),
                   env=tooldef.merged_env(), capture_output=True, text=True,
                   timeout=timeout_s or tooldef.timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, cov("failed", reason=f"depcruise did not complete: {exc}",
                         version=version)
    if proc.returncode not in tooldef.normal_exits:
        detail = (proc.stderr or proc.stdout or "(no output)").strip()[:300]
        return None, cov("failed", reason=f"depcruise exit {proc.returncode}: {detail}",
                         version=version)
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        return None, cov("failed", reason=f"depcruise output invalid JSON: {exc}",
                         version=version)
    repo_root = Path(target.path).expanduser().resolve()
    scan_roots = target.root_paths()
    internal_sources = set()
    for module in payload.get("modules", []):
        source = str(module.get("source", ""))
        if not source or is_excluded_relative(target, source):
            continue
        resolved = (repo_root / source).resolve()
        if (resolved.is_file() and resolved.is_relative_to(repo_root)
                and any(resolved == scan_root or resolved.is_relative_to(scan_root)
                        for scan_root in scan_roots)):
            internal_sources.add(source)
    payload = _sorted_map(payload, internal_sources)
    counts = {"resolved_internal": 0, "resolved_external": 0,
              "could_not_resolve": 0, "total": 0}
    internal_sources = {str(module.get("source", ""))
                        for module in payload.get("modules", [])
                        if module.get("source")}
    for module in payload.get("modules", []):
        for dependency in module.get("dependencies", []):
            counts["total"] += 1
            if dependency.get("couldNotResolve") or not dependency.get("resolved"):
                counts["could_not_resolve"] += 1
            elif dependency.get("inRepo"):
                counts["resolved_internal"] += 1
            else:
                counts["resolved_external"] += 1
    return payload, cov("complete", units=len(payload.get("modules", [])),
                        version=version, reference_counts=counts)
