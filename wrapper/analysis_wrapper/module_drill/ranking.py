"""Candidate-ranking task preparation for Module Drill.

This module makes one deliberately narrow LLM task.  It does not form a
candidate, traverse a dependency, or recover business behaviour: the task can
only select an existing deterministic candidate ID, or leave the selector
unresolved.  Scope materialization is a later, separate stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..orchestrator.contracts import TaskPacket
from .candidate_universe import load as load_universe
from .context import SourceContext
from .driver import ModuleDriver
from .exact_selector import write as write_exact_resolution
from .validation import ContractError, sha256_json

TASK_ID = "module-candidate-ranking"
TASK_TYPE = "module-candidate-ranking"
TEMPLATE_ID = "module-candidate-ranking"
TEMPLATE_VERSION = "v2"
OUTPUT_SCHEMA_ID = "module-candidate-ranking/v2"
CONTEXT_BUDGET_TOKENS = 24_000

_INSTRUCTIONS = """Resolve the user's selector against only the supplied deterministic candidates.

Return exactly one JSON object matching module-candidate-ranking/v2.
Never create, rename, merge, or infer a candidate ID. Do not claim any source
fact in this response. For a whole feature, select every supplied candidate
directly supported by the selector; do not silently discard a directly
supported candidate. If the packet cannot distinguish competing feature
interpretations, return decision=ambiguous with those existing IDs. If it
provides no supporting candidate, return decision=no-match.
"""


def _load_selector(context: SourceContext) -> str:
    try:
        provenance = json.loads((context.module_run / "provenance.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("module provenance is invalid for candidate ranking") from exc
    selector = provenance.get("selector") if isinstance(provenance, dict) else None
    if not isinstance(selector, str) or not selector.strip():
        raise ContractError("module provenance has no non-empty selector")
    return selector


def _load_feature_evidence(context: SourceContext, universe: dict[str, Any]) -> list[dict[str, Any]]:
    path = context.module_run / "evidence" / "feature-evidence.json"
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("feature evidence is invalid for candidate ranking") from exc
    if not isinstance(document, dict) or sha256_json(document) != universe["feature_evidence_digest"]:
        raise ContractError("candidate universe and feature evidence disagree")
    items = document.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ContractError("feature evidence items are invalid for candidate ranking")
    return items


def _candidate_evidence(universe: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item.get("evidence_id"): item for item in items if isinstance(item.get("evidence_id"), str)}
    result: list[dict[str, Any]] = []
    for candidate in universe["candidates"]:
        evidence_ids = candidate["evidence_ids"]
        evidence = [by_id.get(evidence_id) for evidence_id in evidence_ids]
        if any(item is None for item in evidence):
            raise ContractError("candidate universe references missing feature evidence")
        result.append({
            "candidate_id": candidate["candidate_id"],
            "seed_ids": candidate["seed_ids"],
            "repository_refs": candidate["repository_refs"],
            "reason": candidate["reason"],
            "evidence": evidence,
        })
    return result


def build_packet(context: SourceContext) -> TaskPacket:
    """Build one packet whose candidate IDs are fully bound to canonical evidence."""
    universe = load_universe(context)
    candidates = _candidate_evidence(universe, _load_feature_evidence(context, universe))
    return TaskPacket.create(
        task_id=TASK_ID,
        task_type=TASK_TYPE,
        template_id=TEMPLATE_ID,
        template_version=TEMPLATE_VERSION,
        instructions=_INSTRUCTIONS,
        inputs={
            "selector.json": json.dumps({"selector": _load_selector(context)}, sort_keys=True),
            "candidate-universe.json": json.dumps(universe, sort_keys=True),
            "candidate-evidence.json": json.dumps(candidates, sort_keys=True),
        },
        output_schema_id=OUTPUT_SCHEMA_ID,
        context_budget_tokens=CONTEXT_BUDGET_TOKENS,
    )


def register(module_run: str | Path) -> list[str]:
    """Register ranking only when exact evidence cannot resolve the selector.

    ``write_exact_resolution`` records the deterministic alternative before
    this function returns, so callers can continue directly to finalization
    without minting a pretend model task.
    """
    driver = ModuleDriver(module_run)
    if write_exact_resolution(driver.context) is not None:
        return []
    return driver.register((build_packet(driver.context),))
