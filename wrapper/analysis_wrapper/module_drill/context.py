"""Verified source context shared by Module Drill capabilities.

Every Module Drill reader must establish this context before consuming an
overview artifact or target source.  Keeping source-manifest, provenance, and
snapshot freshness checks here prevents individual providers from drifting
into subtly different reuse rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .. import identity, lifecycle, run_provenance
from ..targetspec import TargetSpec
from .run_state import RunStateProjection
from .source import SourceManifest
from .validation import ContractError, sha256_json


@dataclass(frozen=True)
class SourceContext:
    """A fresh overview-backed source snapshot available to one Module Drill run."""

    module_run: Path
    source_run: Path
    manifest: SourceManifest
    source_spec: TargetSpec
    identities: identity.IdentityMap


def load(module_run: str | Path) -> SourceContext:
    """Load and fail closed unless this Module Drill run binds a fresh source.

    Standalone recovery obtains a context through its own preparation path in
    a later phase.  It is deliberately not treated as overview-backed here:
    guessing a source run would undermine the manifest's provenance contract.
    """
    run = Path(module_run).expanduser().resolve()
    try:
        manifest_raw = json.loads((run / "source-manifest.json").read_text("utf-8"))
        manifest = SourceManifest.from_dict(manifest_raw)
        state = RunStateProjection.from_dict(
            json.loads((run / "run-state.json").read_text("utf-8")))
        provenance = json.loads((run / "provenance.json").read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"module run context is invalid: {exc}") from exc
    if state.source_manifest_digest != sha256_json(manifest.to_dict()):
        raise ContractError("module run source manifest does not match its run-state")
    if manifest.source_mode != "overview-backed":
        raise ContractError("operation requires an overview-backed source context")
    source_value = provenance.get("source_run")
    if not isinstance(source_value, str):
        raise ContractError("module provenance has no source run path")
    source = Path(source_value).expanduser().resolve()
    if source.name != manifest.source_overview_run:
        raise ContractError("module provenance and source manifest disagree on overview run")
    try:
        overview_state = lifecycle.RunState.load(source)
        spec = TargetSpec.load(source / "targets.json")
        provenance_document = run_provenance.load(source)
        identities = identity.load(source)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"overview source context is invalid: {exc}") from exc
    problems = overview_state.staleness()
    problems.extend(run_provenance.target_source_staleness(provenance_document, spec))
    if problems:
        raise ContractError("source snapshot is stale: " + "; ".join(problems))
    return SourceContext(run, source, manifest, spec, identities)
