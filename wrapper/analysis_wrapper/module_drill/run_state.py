"""Derived run-state and final-audit contracts for Module Drill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .validation import ContractError, exact_object, sha256, slug, string_list

AUDIT_RESULT_VERSION = "module-audit/v1"
RUN_STATE_VERSION = "module-run-state/v2"


@dataclass(frozen=True)
class AuditResult:
    passed: bool
    checks: tuple[str, ...]
    failed_checks: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise ContractError("audit passed must be boolean")
        if self.passed and self.failed_checks:
            raise ContractError("a passing audit cannot have failed checks")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": AUDIT_RESULT_VERSION, "passed": self.passed,
                "checks": list(self.checks), "failed_checks": list(self.failed_checks)}

    @classmethod
    def from_dict(cls, value: Any, label: str = "audit result") -> "AuditResult":
        row = exact_object(value, {"schema_version", "passed", "checks", "failed_checks"}, label)
        if row["schema_version"] != AUDIT_RESULT_VERSION:
            raise ContractError(f"{label}.schema_version must be {AUDIT_RESULT_VERSION!r}")
        return cls(row["passed"], string_list(row["checks"], f"{label}.checks", allow_empty=True),
                   string_list(row["failed_checks"], f"{label}.failed_checks", allow_empty=True))


@dataclass(frozen=True)
class RunStateProjection:
    run_id: str
    source_manifest_digest: str
    ledger_digest: str
    complete: bool
    audit: AuditResult

    def __post_init__(self) -> None:
        slug(self.run_id, "run_id")
        sha256(self.source_manifest_digest, "source_manifest_digest")
        sha256(self.ledger_digest, "ledger_digest")
        if not isinstance(self.complete, bool):
            raise ContractError("run-state complete must be boolean")
        if self.complete and not self.audit.passed:
            raise ContractError("a completed Module Drill run requires a passing audit")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": RUN_STATE_VERSION, "run_id": self.run_id,
                "source_manifest_digest": self.source_manifest_digest,
                "ledger_digest": self.ledger_digest, "complete": self.complete,
                "audit": self.audit.to_dict()}

    @classmethod
    def from_dict(cls, value: Any, label: str = "module run-state") -> "RunStateProjection":
        row = exact_object(value, {
            "schema_version", "run_id", "source_manifest_digest", "ledger_digest", "complete", "audit",
        }, label)
        if row["schema_version"] != RUN_STATE_VERSION:
            raise ContractError(f"{label}.schema_version must be {RUN_STATE_VERSION!r}")
        return cls(slug(row["run_id"], f"{label}.run_id"),
                   sha256(row["source_manifest_digest"], f"{label}.source_manifest_digest"),
                   sha256(row["ledger_digest"], f"{label}.ledger_digest"),
                   row["complete"], AuditResult.from_dict(row["audit"], f"{label}.audit"))
