"""Structural output schemas for the Module Drill task protocol.

Semantic cross-checks are added with the phase that owns the input universe;
these checks only establish a strict, executor-independent envelope now.
"""

from __future__ import annotations

from typing import Any

from .protocol import MODULE_TASK_TYPES

Failure = dict[str, str]

_REQUIRED_FIELDS = {
    "module-candidate-ranking": {"candidate_ids", "selected_candidate_id"},
    "module-frontier-expansion": {"dispositions"},
    "module-sync-recovery": {"claims", "flows"},
    "module-async-recovery": {"claims", "flows"},
    "module-model-merge": {"module_model"},
    "module-claim-verification": {"verdicts"},
    "module-section-generate": {"sections"},
}


def _failure(check: str, detail: str, location: str = "") -> list[Failure]:
    return [{"check": check, "detail": detail, "location": location}]


def validate_output(task_type: str, output: Any) -> list[Failure]:
    """Validate an envelope; phase-specific code validates its inner records."""
    if task_type not in MODULE_TASK_TYPES:
        return _failure("task-type", f"unknown Module Drill task type: {task_type!r}", "task_type")
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
