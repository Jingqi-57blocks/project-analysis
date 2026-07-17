"""dependency-cruiser parsers (split from parsers.py to keep it under the
~500-line size signal). Structural summaries only: internal-vs-total edge
resolution, prod/dev/test external partition, and distinct dependency-cycle
member files. Re-exported from ``parsers`` so callers keep one import surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .targetspec import RepoTarget


def _json(text: str) -> Any:
    return json.loads(text)


def validate_depcruise(text: str, _exit: int) -> str:
    try:
        data = _json(text)
    except Exception as exc:
        return f"invalid dependency-cruiser JSON: {exc}"
    return "" if isinstance(data, dict) and isinstance(data.get("modules"), list) \
        else "expected an object with a modules list"


def _is_internal_spec(module: str) -> bool:
    """A relative/alias import that SHOULD resolve inside the repo. External
    npm specifiers (bare/scoped package names) are not internal — their
    non-resolution under `--do-not-follow node_modules` is a classification
    limitation, not a broken internal graph."""
    if not module:
        return False
    if module.startswith((".", "/")):
        return True
    # Common tsconfig path-alias roots for in-repo imports.
    first = module.split("/", 1)[0]
    return first in {"src", "app", "lib", "@", "@src", "@app"} or module.startswith("~/")


def depcruise_stats(text: str):
    """Returns (modules, edges, unresolved, circular, externals,
    internal_edges, internal_unresolved)."""
    data = _json(text)
    modules = data.get("modules", [])
    edges = unresolved = circular = 0
    internal_edges = internal_unresolved = 0
    externals: set[str] = set()
    for module in modules:
        for dep in module.get("dependencies", []):
            edges += 1
            spec = str(dep.get("module", ""))
            internal = _is_internal_spec(spec)
            if internal:
                internal_edges += 1
            if dep.get("couldNotResolve"):
                unresolved += 1
                if internal:
                    internal_unresolved += 1
            if dep.get("circular"):
                circular += 1
            if any(str(kind).startswith("npm") for kind in dep.get("dependencyTypes", [])):
                externals.add(spec)
    return (len(modules), edges, unresolved, circular,
            sorted(x for x in externals if x), internal_edges, internal_unresolved)


def depcruise_degraded(_target: RepoTarget, combined: str, _exit: int) -> str:
    # stdout is placed before the sentinel by the executor.
    stdout = combined.split("\n### STDERR ###\n", 1)[0]
    try:
        _, _, _, _, _, internal_edges, internal_unresolved = depcruise_stats(stdout)
    except Exception:
        return ""
    # Coverage is measured over INTERNAL edges only — unresolved external npm
    # subpaths (antd/es/*, @dnd-kit/*) are a known limit of the safe
    # `--no-config`/`--do-not-follow` run, not a hole in the coupling graph.
    if internal_edges and internal_unresolved / internal_edges > 0.15:
        return ("dependency coverage partial: "
                f"{internal_unresolved}/{internal_edges} INTERNAL edges unresolved (>15%)")
    return ""


def depcruise_resolution_note(stdout: str) -> str:
    """Manifest metric line — BOTH total and internal edge resolution, never a
    mixed denominator (item 2). Empty when stdout is not parseable."""
    try:
        (modules, edges, unresolved, circular, _externals,
         internal_edges, internal_unresolved) = depcruise_stats(stdout)
    except Exception:
        return ""
    total_pct = round(100 * (edges - unresolved) / edges, 1) if edges else 100.0
    internal_pct = round(100 * (internal_edges - internal_unresolved)
                         / internal_edges, 1) if internal_edges else 100.0
    return (f"edge resolution: total {edges - unresolved}/{edges} ({total_pct}%), "
            f"internal {internal_edges - internal_unresolved}/{internal_edges} "
            f"({internal_pct}%); modules {modules}, circular edges {circular}")


def _depcruise_cycles(data: Any, limit: int = 40) -> tuple[list[tuple[str, ...]], int]:
    """Distinct directed dependency cycles as member-file tuples (item 3).

    depcruise reports circular edges per entry point; the same loop recurs from
    each member. We canonicalize each loop by rotating to its smallest member so
    the bounded view lists each cycle once, shortest first."""
    seen: set[tuple[str, ...]] = set()
    for module in data.get("modules", []):
        src = str(module.get("source", ""))
        for dep in module.get("dependencies", []):
            if not dep.get("circular"):
                continue
            steps = dep.get("cycle") or []
            names = [src] + [str(s.get("name", "")) if isinstance(s, dict) else str(s)
                             for s in steps]
            names = [n for n in names if n]
            if names[-1:] == names[:1]:
                names = names[:-1]
            if len(names) < 2:
                continue
            pivot = min(range(len(names)), key=lambda i: names[i])
            seen.add(tuple(names[pivot:] + names[:pivot]))
    ordered = sorted(seen, key=lambda c: (len(c), c))
    return ordered[:limit], len(seen)


def depcruise_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    target = _target
    (modules, edges, unresolved, circular, externals,
     internal_edges, internal_unresolved) = depcruise_stats(stdout)
    production: list[str] = []
    dev_test: list[str] = []
    unclassified: list[str] = []
    package_file = Path(target.path) / "package.json"
    package_data = _json(package_file.read_text("utf-8")) if package_file.is_file() else {}
    deps = set(package_data.get("dependencies", {}))
    dev_deps = set(package_data.get("devDependencies", {}))

    def package_root(value: str) -> str:
        parts = value.split("/")
        return "/".join(parts[:2]) if value.startswith("@") else parts[0]

    for value in externals:
        root = package_root(value)
        if root in deps:
            production.append(value)
        elif root in dev_deps:
            dev_test.append(value)
        else:
            unclassified.append(value)
    out = [
        f"modules: {modules}", f"edges: {edges}", f"unresolved_edges: {unresolved}",
        f"internal_edges: {internal_edges}",
        f"internal_unresolved_edges: {internal_unresolved} "
        f"(coupling-graph coverage; external npm subpaths excluded)",
        f"circular_edges: {circular}",
        depcruise_resolution_note(stdout),
        "", "external_imports_production:", *production,
        "", "external_imports_dev_test:", *dev_test,
        "", "external_imports_unclassified:", *unclassified,
    ]
    cycles, distinct = _depcruise_cycles(_json(stdout))
    if distinct:
        out += ["", f"dependency_cycles (distinct: {distinct}; showing "
                    f"{len(cycles)} shortest — member files of each directed cycle):"]
        for members in cycles:
            shown = list(members[:20]) + (["…"] if len(members) > 20 else [])
            out.append(" -> ".join(shown) + " -> (loop)")
    if stderr.strip():
        out += ["", "stderr:", *stderr.splitlines()[:30]]
    return "\n".join(out)
