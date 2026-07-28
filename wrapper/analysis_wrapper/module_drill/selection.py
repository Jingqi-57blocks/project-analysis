"""Mechanical finalization of one validated Module Drill selector ranking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from ..orchestrator.schemas import validate_output
from .candidate_universe import load as load_universe
from .context import SourceContext, load as load_context
from .driver import ModuleDriver
from .exact_selector import ExactSelectorResolution, load as load_exact_resolution
from .frontiers import initial as initial_frontiers
from .ranking import TASK_TYPE, build_packets, load_plan
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


def _validated_ranking(driver: ModuleDriver) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Merge every validated ranking partition without silently dropping one.

    Each model call only receives its compact candidate partition.  The
    persisted deterministic plan records the complete universe, and any local
    ambiguity is propagated rather than guessed away at merge time.
    """
    plan = load_plan(driver.context)
    expected_packets = build_packets(driver.context)
    expected_by_id = {packet.task_id: packet for packet in expected_packets}
    planned_ids = [row.get("task_id") for row in plan.get("partitions", [])]
    if planned_ids != [packet.task_id for packet in expected_packets] or len(set(planned_ids)) != len(planned_ids):
        raise ContractError("candidate ranking plan does not match the expected partition tasks")

    selected: set[str] = set()
    ambiguous: set[str] = set()
    outputs: list[dict[str, Any]] = []
    for task_id in planned_ids:
        if not isinstance(task_id, str) or task_id not in expected_by_id:
            raise ContractError("candidate ranking plan names an unknown task")
        expected = expected_by_id[task_id]
        packet, output = driver.validated_task(task_id)
        if packet.to_dict() != expected.to_dict():
            raise ContractError("validated ranking task does not match its candidate partition")
        failures = validate_output(
            TASK_TYPE, output,
            packet_inputs={name: item.content for name, item in expected.inputs.items()},
        )
        if failures:
            raise ContractError("validated ranking output no longer satisfies its candidate partition: "
                                + failures[0]["detail"])
        outputs.append({"task_id": task_id, "output": output})
        if output["decision"] == "selected":
            selected.update(output["candidate_ids"])
        elif output["decision"] == "ambiguous":
            ambiguous.update(output["candidate_ids"])

    if ambiguous:
        merged = {"decision": "ambiguous", "candidate_ids": sorted(ambiguous),
                  "reason_code": "equally-supported"}
    elif selected:
        merged = {"decision": "selected", "candidate_ids": sorted(selected),
                  "reason_code": "clear-dominant"}
    else:
        merged = {"decision": "no-match", "candidate_ids": [],
                  "reason_code": "insufficient-evidence"}
    universe = load_universe(driver.context)
    return (merged, universe, "validated-ranking-partitions", sha256_json({
        "plan": plan, "outputs": outputs,
    }))


def _exact_output(resolution: ExactSelectorResolution) -> dict[str, Any]:
    """Adapt a deterministic receipt to the same selection envelope.

    The task schema's reason codes describe model decisions.  The source of
    this result is persisted separately as ``selection_mode`` instead of
    pretending that a model supplied it.
    """
    return {
        "decision": resolution.decision,
        "candidate_ids": list(resolution.candidate_ids),
        "reason_code": (
            "clear-dominant" if resolution.decision == "selected" else
            "equally-supported" if resolution.decision == "ambiguous" else
            "insufficient-evidence"
        ),
    }


def _user_choice(context: SourceContext, candidate_id: str) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    universe = load_universe(context)
    candidate_ids = {row["candidate_id"] for row in universe["candidates"]}
    if candidate_id not in candidate_ids:
        raise ContractError("selected candidate is outside the current candidate universe")
    return ({"decision": "selected", "candidate_ids": [candidate_id],
             "reason_code": "clear-dominant"}, universe,
            "user-selected-candidate", sha256_json({
                "selector": json.loads((context.module_run / "provenance.json").read_text("utf-8"))["selector"],
                "candidate_id": candidate_id,
                "candidate_universe_digest": sha256_json(universe),
            }))


def _selection_input(module_run: str | Path, selected_candidate_id: str | None) \
        -> tuple[SourceContext, dict[str, Any], dict[str, Any], str, str]:
    context = load_context(module_run)
    if selected_candidate_id is not None:
        output, universe, mode, digest = _user_choice(context, selected_candidate_id)
    else:
        exact = load_exact_resolution(context)
        if exact is not None:
            output, universe, mode, digest = (
                _exact_output(exact), load_universe(context), "deterministic-exact", sha256_json(exact.to_dict()))
        else:
            output, universe, mode, digest = _validated_ranking(ModuleDriver(module_run))
    failures = validate_output(
        TASK_TYPE, output,
        packet_inputs={"candidate-universe.json": json.dumps(universe, sort_keys=True)},
    )
    if failures:
        raise ContractError("selection output no longer satisfies its candidate universe: " + failures[0]["detail"])
    return context, output, universe, mode, digest


def _scope(context: SourceContext, universe: dict[str, Any], output: dict[str, Any]) -> ModuleScope:
    selected_ids = tuple(output["candidate_ids"])
    candidates = tuple(
        ScopeCandidate(
            candidate_id=row["candidate_id"], seed_ids=tuple(row["seed_ids"]),
            repository_refs=tuple(row["repository_refs"]),
            disposition="selected" if row["candidate_id"] in selected_ids else "alternative",
            reason=row["reason"],
        )
        for row in universe["candidates"]
    )
    selected = tuple(candidate for candidate in candidates if candidate.candidate_id in selected_ids)
    seeds = _feature_seeds(context)
    return ModuleScope(
        feature_id="feature-" + selected_ids[0].removeprefix("candidate-"),
        selector=json.loads((context.module_run / "provenance.json").read_text("utf-8"))["selector"],
        source_manifest_digest=sha256_json(context.manifest.to_dict()),
        selected_candidate_ids=selected_ids,
        candidates=candidates,
        seeds=seeds,
        frontiers=initial_frontiers(tuple(seed_id for candidate in selected for seed_id in candidate.seed_ids), seeds),
        closure_status="open",
    )


def finalize(module_run: str | Path, *, selected_candidate_id: str | None = None) -> FinalizedSelection:
    """Write scope only after a bounded, evidence-bound candidate selection.

    The selection may be a validated ranking task, a deterministic exact
    selector receipt, or an explicit reviewed candidate ID. Ambiguous and
    no-match decisions are persisted without a scope; later phases must not
    treat either as a feature.
    """
    context, output, universe, selection_mode, selection_input_digest = _selection_input(
        module_run, selected_candidate_id)
    # Revalidate the persisted universe against canonical source evidence,
    # rather than trusting only the copy embedded in a task or receipt.
    current_universe = load_universe(context)
    if current_universe != universe:
        raise ContractError("selection input no longer matches current candidate universe")
    evidence_dir = create_stage_dir(context.module_run / "evidence")
    scope = _scope(context, universe, output) if output["decision"] == "selected" else None
    resolution = {
        "schema_version": RESOLUTION_VERSION,
        "source_manifest_digest": sha256_json(context.manifest.to_dict()),
        "selection_mode": selection_mode,
        "selection_input_digest": selection_input_digest,
        "decision": output["decision"],
        "candidate_ids": output["candidate_ids"],
        "selected_candidate_ids": output["candidate_ids"],
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
