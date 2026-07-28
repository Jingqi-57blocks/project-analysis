"""Mechanical finalization of one validated Module Drill selector ranking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from ..orchestrator.schemas import validate_output
from .candidate_universe import load as load_universe
from .context import SourceContext
from .driver import ModuleDriver
from .frontiers import initial as initial_frontiers
from .ranking import TASK_ID, TASK_TYPE, build_packet
from .scope import FeatureSeed, ModuleScope, ScopeCandidate
from .validation import ContractError, sha256_json

RESOLUTION_VERSION = "selector-resolution/v1"
RESOLUTION_FILENAME = "selector-resolution.json"
SCOPE_FILENAME = "module-scope.json"


@dataclass(frozen=True)
class FinalizedSelection:
    decision: str
    resolution_path: Path
    scope_path: Path | None


def _feature_seeds(context: SourceContext) -> tuple[FeatureSeed, ...]:
    path = context.module_run / "evidence" / "feature-evidence.json"
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("feature evidence is invalid for scope finalization") from exc
    seeds = document.get("seeds") if isinstance(document, dict) else None
    if not isinstance(seeds, list):
        raise ContractError("feature evidence has no seed list")
    return tuple(FeatureSeed.from_dict(row, f"feature evidence seeds[{index}]")
                 for index, row in enumerate(seeds))


def _validated_ranking(driver: ModuleDriver) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a current ranking only when its packet equals this run's plan."""
    expected = build_packet(driver.context)
    packet, output = driver.validated_task(TASK_ID)
    if packet.to_dict() != expected.to_dict():
        raise ContractError("validated ranking task does not match this run's candidate packet")
    failures = validate_output(
        TASK_TYPE, output,
        packet_inputs={name: item.content for name, item in expected.inputs.items()},
    )
    if failures:
        raise ContractError("validated ranking output no longer satisfies its packet: " + failures[0]["detail"])
    return output, json.loads(expected.inputs["candidate-universe.json"].content)


def _scope(context: SourceContext, universe: dict[str, Any], output: dict[str, Any]) -> ModuleScope:
    selected_id = output["selected_candidate_id"]
    assert isinstance(selected_id, str)  # established by the schema gate
    candidates = tuple(
        ScopeCandidate(
            candidate_id=row["candidate_id"], seed_ids=tuple(row["seed_ids"]),
            repository_refs=tuple(row["repository_refs"]),
            disposition="selected" if row["candidate_id"] == selected_id else "alternative",
            reason=row["reason"],
        )
        for row in universe["candidates"]
    )
    selected = next(candidate for candidate in candidates if candidate.candidate_id == selected_id)
    seeds = _feature_seeds(context)
    return ModuleScope(
        feature_id="feature-" + selected_id.removeprefix("candidate-"),
        selector=json.loads((context.module_run / "provenance.json").read_text("utf-8"))["selector"],
        source_manifest_digest=sha256_json(context.manifest.to_dict()),
        selected_candidate_id=selected_id,
        candidates=candidates,
        seeds=seeds,
        frontiers=initial_frontiers(selected.seed_ids, seeds),
        closure_status="open",
    )


def finalize(module_run: str | Path) -> FinalizedSelection:
    """Write scope only after a uniquely selected, packet-bound candidate.

    Ambiguous and no-match decisions are persisted as a receipt and return
    without a scope.  Later phases must not treat either as a feature.
    """
    driver = ModuleDriver(module_run)
    output, universe = _validated_ranking(driver)
    # Revalidate the persisted universe against canonical source evidence,
    # rather than trusting only the copy embedded in the task packet.
    current_universe = load_universe(driver.context)
    if current_universe != universe:
        raise ContractError("validated ranking packet no longer matches current candidate universe")
    evidence_dir = create_stage_dir(driver.run / "evidence")
    scope = _scope(driver.context, universe, output) if output["decision"] == "selected" else None
    resolution = {
        "schema_version": RESOLUTION_VERSION,
        "source_manifest_digest": sha256_json(driver.context.manifest.to_dict()),
        "ranking_packet_digest": build_packet(driver.context).input_digest,
        "decision": output["decision"],
        "candidate_ids": output["candidate_ids"],
        "selected_candidate_id": output["selected_candidate_id"],
        "reason_code": output["reason_code"],
        "module_scope_digest": sha256_json(scope.to_dict()) if scope is not None else "",
    }
    resolution_path = evidence_dir / RESOLUTION_FILENAME
    write_new_text(resolution_path, json.dumps(resolution, indent=2, sort_keys=True) + "\n")
    if scope is None:
        return FinalizedSelection(output["decision"], resolution_path, None)
    scope_path = evidence_dir / SCOPE_FILENAME
    write_new_text(scope_path, json.dumps(scope.to_dict(), indent=2, sort_keys=True) + "\n")
    return FinalizedSelection("selected", resolution_path, scope_path)
