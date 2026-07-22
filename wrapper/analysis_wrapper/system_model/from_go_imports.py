"""Go package-dependency edges from ``go list`` maps under ``imports/``.

Sibling of :mod:`from_imports` (the dependency-cruiser/JS normalizer): consumes
``<run>/imports/<artifact-key>.golist.json`` (the leak-free projection the Go
dependency-map lane writes) into ``dependency`` edges — kept STRICTLY separate
from the ``call`` edge type. Granularity is the Go package (``go list`` is
package-level; the Phase-0 spike validated that), so an edge is
internal-package → internal-package, keyed by the package's repo-relative
directory.

Following the JS convention for externals: an internal → third-party import is
preserved as an ``unresolved`` dependency edge carrying the raw package path
(never dropped). Go stdlib imports are COUNTED but emit no edge — they are the
language runtime, not a project dependency, and emitting one per import would be
pure noise. A missing ``imports/`` directory yields ``present=False`` (the caller
discloses the partition, never a fabricated empty graph).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..identity import IdentityMap
from . import ids
from .builder import ModelBuilder

PRODUCER = "go-list"


def _short(import_path: str, module: str) -> str:
    """The package's repo-relative directory (module prefix trimmed); the module
    root itself reads as ``.`` (its main package)."""
    if import_path == module:
        return "."
    if import_path.startswith(module + "/"):
        return import_path[len(module) + 1:]
    return import_path


def _is_internal(import_path: str, module: str) -> bool:
    return import_path == module or import_path.startswith(module + "/")


def load(builder: ModelBuilder, run_dir: str | Path, heads: dict,
         identities: IdentityMap) -> dict:
    """Consume ``<run>/imports/*.golist.json`` if present.

    Returns ``{present, repos, edges, unresolved, stdlib_omitted}`` for coverage.
    ``present`` is False when the ``imports/`` directory is absent."""
    imports_dir = Path(run_dir) / "imports"
    summary = {"present": imports_dir.is_dir(), "repos": [], "edges": 0,
               "unresolved": 0, "stdlib_omitted": 0}
    if not imports_dir.is_dir():
        return summary
    for path in sorted(imports_dir.glob("*.golist.json")):
        artifact_key = path.name[: -len(".golist.json")]
        repo_id = identities.repository_by_artifact_key(artifact_key).reference
        summary["repos"].append(repo_id)
        _consume(builder, repo_id, heads.get(repo_id, ""), path, summary)
    return summary


def _consume(builder: ModelBuilder, repo_id: str, head: str, path: Path,
             summary: dict) -> None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return
    module = data.get("module", "")
    if not module:
        return
    stdlib = set(data.get("stdlib", []))
    for pkg in data.get("packages", []):
        source = pkg.get("import_path", "")
        if not _is_internal(source, module):
            continue
        src_dir = _short(source, module)
        src_citation = ids.make_citation(repo_id, head, src_dir)
        src_id = builder.note_file(repo_id, src_dir, producer=PRODUCER,
                                   evidence=src_citation)
        for dep in pkg.get("imports", []):
            _dependency(builder, repo_id, head, src_id, src_citation, module,
                        stdlib, dep, summary)


def _dependency(builder, repo_id, head, src_id, src_citation, module, stdlib,
                dep: str, summary: dict) -> None:
    if _is_internal(dep, module):
        dst_dir = _short(dep, module)
        dst_citation = ids.make_citation(repo_id, head, dst_dir)
        dst_id = builder.note_file(repo_id, dst_dir, producer=PRODUCER,
                                   evidence=dst_citation)
        builder.add_edge("dependency", src_id, dst_id, status="observed",
                         producer=PRODUCER, evidence=[src_citation],
                         attrs={"specifier": dep, "granularity": "package"})
        summary["edges"] += 1
    elif dep in stdlib:
        summary["stdlib_omitted"] += 1               # language runtime — counted, no edge
    else:
        builder.add_unresolved_edge(
            "dependency", src_id, {"specifier": dep}, producer=PRODUCER,
            evidence=[src_citation],
            attrs={"external": True, "granularity": "package"},
            discriminator=dep)
        summary["unresolved"] += 1
