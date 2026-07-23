"""Deterministic evidence catalog projection over capability results (57B-79).

Maps each ``capability_id`` to the repository-scoped rows of evidence it
produced: coverage, a bounded fact list, a deduped source-reference index, and
sanitized artifact views (path + sha256, disclosed even when the referenced
artifact is missing rather than crashing). Every repository reference is
resolved through :class:`~analysis_wrapper.identity.IdentityMap` — a raw
internal ``repo_id`` must never leak into this artifact.

This module intentionally accepts anything shaped like
:class:`~analysis_wrapper.profiles.contracts.CapabilityResult` (duck-typed,
``TYPE_CHECKING``-only import) rather than importing it at module load time:
``profiles.contracts`` imports :mod:`analysis_wrapper.evidence.coverage` and
``.facts``, so a runtime import back into ``profiles`` here would cycle.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from ..executor import replace_artifact_text
from ..identity import IdentityMap
from ..sanitize import sanitize_text
# synthesis_input.py's `_bounded` already defines the disclosure shape
# (total_count/included_count/truncated/items) this catalog reuses verbatim.
from ..synthesis_input import _bounded

if TYPE_CHECKING:
    from ..profiles.contracts import CapabilityResult

SCHEMA_VERSION = "1.0.0"
FILENAME = "evidence-catalog.json"


def _artifact_view(run_dir: Path, ref: Any) -> dict[str, Any]:
    path = run_dir / ref.path
    exists = path.is_file()
    return {
        "path": ref.path,
        "kind": ref.kind,
        "exists": exists,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if exists else "",
    }


def _result_entry(run_dir: Path, result: "CapabilityResult",
                  identities: IdentityMap) -> dict[str, Any]:
    fact_rows = [fact.to_dict() for fact in result.facts]
    source_refs = sorted({ref for row in fact_rows for ref in row["source_refs"]})
    return {
        "capability_id": result.capability_id,
        "provider_id": result.provider_id,
        "scope": identities.reference_for(result.repo_id),
        "coverage": result.coverage.to_dict(),
        "facet_provenance": sorted(result.facet_provenance),
        "facts": _bounded(fact_rows, key=lambda row: (row["kind"], row["fact_id"])),
        "source_refs": _bounded(
            [{"ref": ref} for ref in source_refs], key=lambda row: row["ref"]),
        "artifacts": sorted(
            (_artifact_view(run_dir, ref) for ref in result.artifact_refs),
            key=lambda row: row["path"]),
    }


def build(results: Iterable["CapabilityResult"], identities: IdentityMap,
         run_dir: str | Path) -> dict[str, Any]:
    """Project ``results`` into the deterministic evidence-catalog shape.

    Pure and repeatable: the same ``results``/``identities`` always produce
    byte-identical JSON via ``json.dumps(..., sort_keys=True)``.
    """
    run = Path(run_dir)
    entries: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        entries.setdefault(result.capability_id, []).append(
            _result_entry(run, result, identities))
    return {
        "schema_version": SCHEMA_VERSION,
        "project_ref": identities.project.reference,
        "capabilities": {
            capability_id: _bounded(rows, key=lambda row: row["scope"])
            for capability_id, rows in sorted(entries.items())
        },
    }


def write(run_dir: str | Path, results: Iterable["CapabilityResult"],
         identities: IdentityMap) -> Path:
    run = Path(run_dir).expanduser().resolve()
    out = run / FILENAME
    replace_artifact_text(
        out, sanitize_text(json.dumps(build(results, identities, run),
                                      indent=2, sort_keys=True) + "\n"))
    return out
