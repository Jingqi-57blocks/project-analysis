"""Structural output schemas for the Module Drill task protocol.

Semantic cross-checks are added with the phase that owns the input universe;
these checks only establish a strict, executor-independent envelope now.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .protocol import MODULE_TASK_TYPES

Failure = dict[str, str]

_REQUIRED_FIELDS = {
    "module-candidate-ranking": {"decision", "candidate_ids", "selected_candidate_id", "reason_code"},
    "module-frontier-expansion": {"dispositions"},
    "module-sync-recovery": {"claims", "flows"},
    "module-async-recovery": {"claims", "flows"},
    "module-model-merge": {"module_model"},
    "module-claim-verification": {"verdicts"},
    "module-section-generate": {"sections"},
}


def _failure(check: str, detail: str, location: str = "") -> list[Failure]:
    return [{"check": check, "detail": detail, "location": location}]


_RANKING_DECISIONS = frozenset({"selected", "ambiguous", "no-match"})
_RANKING_REASON_CODES = frozenset({
    "clear-dominant", "equally-supported", "insufficient-evidence",
})


def _validate_candidate_ranking(output: Any) -> list[Failure]:
    if not isinstance(output, dict):
        return _failure("output-shape", "task output must be an object")
    expected = _REQUIRED_FIELDS["module-candidate-ranking"]
    if set(output) != expected:
        missing = sorted(expected - set(output))
        extras = sorted(set(output) - expected)
        return _failure("output-fields", f"missing={missing}; unexpected={extras}")

    decision = output["decision"]
    candidate_ids = output["candidate_ids"]
    selected = output["selected_candidate_id"]
    reason_code = output["reason_code"]
    if decision not in _RANKING_DECISIONS:
        return _failure("ranking-decision", "decision must be selected, ambiguous, or no-match", "decision")
    if not isinstance(candidate_ids, list) or not all(
            isinstance(value, str) and value for value in candidate_ids):
        return _failure("ranking-candidate-ids", "candidate_ids must be a string list", "candidate_ids")
    if len(candidate_ids) != len(set(candidate_ids)):
        return _failure("ranking-candidate-ids", "candidate_ids must not contain duplicates", "candidate_ids")
    if selected is not None and (not isinstance(selected, str) or not selected):
        return _failure("selected-candidate", "selected_candidate_id must be a string or null", "selected_candidate_id")
    if reason_code not in _RANKING_REASON_CODES:
        return _failure("ranking-reason-code", "reason_code is not recognized", "reason_code")
    if decision == "selected":
        if len(candidate_ids) != 1 or selected != candidate_ids[0] or reason_code != "clear-dominant":
            return _failure(
                "ranking-selected-shape",
                "selected requires one candidate, the same selected_candidate_id, and clear-dominant",
            )
    elif decision == "ambiguous":
        if len(candidate_ids) < 2 or selected is not None or reason_code != "equally-supported":
            return _failure(
                "ranking-ambiguous-shape",
                "ambiguous requires at least two candidates, null selected_candidate_id, and equally-supported",
            )
    elif candidate_ids or selected is not None or reason_code != "insufficient-evidence":
        return _failure(
            "ranking-no-match-shape",
            "no-match requires no candidates, null selected_candidate_id, and insufficient-evidence",
        )
    return []


def _crosscheck_candidate_ranking(output: Any, packet_inputs: Mapping[str, str]) -> list[Failure]:
    """Ensure ranking can only choose IDs actually supplied in the packet."""
    raw = packet_inputs.get("candidate-universe.json")
    if raw is None or not isinstance(output, dict):
        return []
    try:
        universe = json.loads(raw)
        rows = universe["candidates"]
        expected_ids = {
            row["candidate_id"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("candidate_id"), str)
        }
    except (TypeError, ValueError, KeyError):
        # A malformed packet is a packet-construction failure, not an executor
        # result failure. The command that creates it owns that validation.
        return []
    supplied = set(output.get("candidate_ids", []))
    unknown = sorted(supplied - expected_ids)
    if unknown:
        return _failure(
            "ranking-candidate-universe",
            "candidate_ids must be chosen from candidate-universe.json: " + ", ".join(unknown),
            "candidate_ids",
        )
    return []


def validate_output(task_type: str, output: Any, *,
                    packet_inputs: Mapping[str, str] | None = None) -> list[Failure]:
    """Validate an envelope; phase-specific code validates its inner records."""
    if task_type not in MODULE_TASK_TYPES:
        return _failure("task-type", f"unknown Module Drill task type: {task_type!r}", "task_type")
    if task_type == "module-candidate-ranking":
        failures = _validate_candidate_ranking(output)
        if not failures and packet_inputs is not None:
            failures += _crosscheck_candidate_ranking(output, packet_inputs)
        return failures
    if not isinstance(output, dict):
        return _failure("output-shape", "task output must be an object")
    missing = sorted(_REQUIRED_FIELDS[task_type] - set(output))
    if missing:
        return _failure("output-required-fields", f"missing required fields: {missing}")
    extras = sorted(set(output) - _REQUIRED_FIELDS[task_type])
    if extras:
        return _failure("output-fields", f"unexpected fields: {extras}")
    for name, value in output.items():
        if name == "selected_candidate_id":
            if value is not None and (not isinstance(value, str) or not value):
                return _failure("selected-candidate", "selected_candidate_id must be a string or null", name)
        elif not isinstance(value, (list, dict)):
            return _failure("output-field-shape", f"{name} must be a list or object", name)
    return []
