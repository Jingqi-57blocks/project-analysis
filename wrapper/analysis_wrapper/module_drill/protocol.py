"""Module Drill-owned task vocabulary and output-schema identifiers."""

from __future__ import annotations

from dataclasses import dataclass

from .validation import ContractError, enum

MODULE_TASK_TYPES = frozenset({
    "module-candidate-ranking",
    "module-frontier-expansion",
    "module-sync-recovery",
    "module-async-recovery",
    "module-model-merge",
    "module-claim-verification",
    "module-section-generate",
})

_SCHEMA_BY_TASK = {
    "module-candidate-ranking": "module-candidate-ranking/v1",
    "module-frontier-expansion": "module-frontier-expansion/v1",
    "module-sync-recovery": "module-sync-recovery/v1",
    "module-async-recovery": "module-async-recovery/v1",
    "module-model-merge": "module-model-merge/v1",
    "module-claim-verification": "module-claim-verification/v1",
    "module-section-generate": "module-section-generate/v1",
}


def schema_for_task_type(task_type: str) -> str:
    try:
        return _SCHEMA_BY_TASK[task_type]
    except KeyError as exc:
        raise ContractError(f"unknown Module Drill task type: {task_type!r}") from exc


@dataclass(frozen=True)
class ModuleTaskDefinition:
    task_type: str
    schema_id: str
    packet_crosscheck: str

    def __post_init__(self) -> None:
        enum(self.task_type, MODULE_TASK_TYPES, "module task_type")
        if self.schema_id != schema_for_task_type(self.task_type):
            raise ContractError("module task schema_id does not match task_type")
        if not self.packet_crosscheck:
            raise ContractError("module task packet_crosscheck must be non-empty")


MODULE_TASK_DEFINITIONS = (
    ModuleTaskDefinition("module-candidate-ranking", "module-candidate-ranking/v1",
                         "only supplied candidate IDs may be selected; ambiguity must stay unresolved"),
    ModuleTaskDefinition("module-frontier-expansion", "module-frontier-expansion/v1",
                         "every supplied frontier receives exactly one disposition"),
    ModuleTaskDefinition("module-sync-recovery", "module-sync-recovery/v1",
                         "claims and flows must remain inside supplied graph anchors"),
    ModuleTaskDefinition("module-async-recovery", "module-async-recovery/v1",
                         "claims and flows must remain inside supplied graph anchors"),
    ModuleTaskDefinition("module-model-merge", "module-model-merge/v1",
                         "all supplied shard outputs are accounted for"),
    ModuleTaskDefinition("module-claim-verification", "module-claim-verification/v1",
                         "every supplied claim receives one verdict"),
    ModuleTaskDefinition("module-section-generate", "module-section-generate/v1",
                         "rendered factual references must name supplied claim IDs"),
)
