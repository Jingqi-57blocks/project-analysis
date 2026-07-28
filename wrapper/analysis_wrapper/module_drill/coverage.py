"""Two-axis Coverage and feature-closure vocabulary for Module Drill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .validation import ContractError, enum, exact_object, ref_list, string_list

APPLICABILITY = frozenset({"applicable", "not-applicable", "unknown"})
EXECUTION_STATUS = frozenset({"complete", "partial", "unavailable", "skipped", "failed"})
CLOSURE_STATUS = frozenset({"closed", "open", "blocked"})


@dataclass(frozen=True)
class Coverage:
    """Whether a dimension applies is independent from whether its lane ran."""

    applicability: str
    status: str
    positive_evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        enum(self.applicability, APPLICABILITY, "coverage.applicability")
        enum(self.status, EXECUTION_STATUS, "coverage.status")
        if self.applicability == "not-applicable" and not self.positive_evidence_refs:
            raise ContractError("not-applicable coverage requires positive evidence refs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "applicability": self.applicability,
            "status": self.status,
            "positive_evidence_refs": list(self.positive_evidence_refs),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, value: Any, label: str = "coverage") -> "Coverage":
        row = exact_object(value, {
            "applicability", "status", "positive_evidence_refs", "limitations",
        }, label)
        return cls(
            applicability=enum(row["applicability"], APPLICABILITY, f"{label}.applicability"),
            status=enum(row["status"], EXECUTION_STATUS, f"{label}.status"),
            positive_evidence_refs=ref_list(
                row["positive_evidence_refs"], f"{label}.positive_evidence_refs", allow_empty=True),
            limitations=string_list(row["limitations"], f"{label}.limitations", allow_empty=True),
        )


@dataclass(frozen=True)
class CoverageStatus:
    """Feature-level coverage adds closure without collapsing the two axes."""

    coverage: Coverage
    closure_status: str
    unresolved_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        enum(self.closure_status, CLOSURE_STATUS, "closure_status")
        if self.closure_status == "closed" and self.unresolved_reasons:
            raise ContractError("closed coverage must not carry unresolved reasons")

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage.to_dict(),
            "closure_status": self.closure_status,
            "unresolved_reasons": list(self.unresolved_reasons),
        }

    @classmethod
    def from_dict(cls, value: Any, label: str = "coverage status") -> "CoverageStatus":
        row = exact_object(value, {"coverage", "closure_status", "unresolved_reasons"}, label)
        return cls(
            coverage=Coverage.from_dict(row["coverage"], f"{label}.coverage"),
            closure_status=enum(row["closure_status"], CLOSURE_STATUS,
                                f"{label}.closure_status"),
            unresolved_reasons=string_list(
                row["unresolved_reasons"], f"{label}.unresolved_reasons", allow_empty=True),
        )
