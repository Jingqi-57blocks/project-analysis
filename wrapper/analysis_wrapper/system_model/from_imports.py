"""Optional import/dependency edges from dependency-cruiser output.

The canonical run dir (targets + discovery + callgraph) does not include an
import map, so this partition is normally ``partial`` (disclosed, never
fabricated). When a producer drops dependency-cruiser JSON into
``<run>/imports/<repo_id>.depcruise.json`` this module consumes it into
``dependency`` edges — kept STRICTLY separate from the ``call`` edge type.

An import that dependency-cruiser could not resolve to an in-repo file (external
npm package, broken specifier) is preserved as an ``unresolved`` dependency edge
carrying the raw specifier, rather than dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import ids
from .builder import ModelBuilder

PRODUCER = "dependency-cruiser"


def load(builder: ModelBuilder, run_dir: str | Path, heads: dict) -> dict:
    """Consume ``<run>/imports/*.depcruise.json`` if present.

    Returns ``{present, files, repos, edges, unresolved}`` for coverage.
    ``present`` is False when the ``imports/`` directory is absent — the caller
    then marks the dependency partition ``partial``."""
    imports_dir = Path(run_dir) / "imports"
    summary = {"present": imports_dir.is_dir(), "repos": [], "edges": 0,
               "unresolved": 0}
    if not imports_dir.is_dir():
        return summary
    for path in sorted(imports_dir.glob("*.depcruise.json")):
        repo_id = path.name[: -len(".depcruise.json")]
        summary["repos"].append(repo_id)
        _consume(builder, repo_id, heads.get(repo_id, ""), path, summary)
    return summary


def _consume(builder: ModelBuilder, repo_id: str, head: str, path: Path,
             summary: dict) -> None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return
    for module in data.get("modules", []):
        source = module.get("source", "")
        if not source:
            continue
        src_citation = ids.make_citation(repo_id, head, source)
        src_id = builder.note_file(repo_id, source, producer=PRODUCER,
                                   evidence=src_citation)
        for dep in module.get("dependencies", []):
            _dependency(builder, repo_id, head, src_id, dep, summary)


def _dependency(builder, repo_id, head, src_id, dep, summary) -> None:
    resolved = dep.get("resolved", "")
    specifier = dep.get("module", "")
    circular = bool(dep.get("circular"))
    in_repo = (resolved and not dep.get("couldNotResolve")
               and "node_modules" not in resolved)
    if in_repo:
        citation = ids.make_citation(repo_id, head, resolved)
        dst_id = builder.note_file(repo_id, resolved, producer=PRODUCER,
                                   evidence=citation)
        builder.add_edge("dependency", src_id, dst_id, status="observed",
                         producer=PRODUCER, evidence=[citation],
                         attrs={"specifier": specifier, "circular": circular})
        summary["edges"] += 1
    else:
        builder.add_unresolved_edge(
            "dependency", src_id, {"specifier": specifier}, producer=PRODUCER,
            attrs={"circular": circular, "external": True},
            discriminator=specifier)
        summary["unresolved"] += 1
