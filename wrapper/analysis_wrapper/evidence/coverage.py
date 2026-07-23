"""Canonical two-axis Coverage: applicability and status are separate facts.

Legacy vocabularies conflate them: ``capabilities.py`` and
``datastore_coverage.py`` both bake "not-applicable" into the same status
field as outcomes like "complete"/"failed", and ``capabilities.py`` further
collapses "skipped" into "unavailable", losing a real distinction.  This
module keeps ``applicability`` (is the capability even in scope for this
target?) and ``status`` (what happened when it ran?) as two explicit fields,
plus a machine ``reason_code`` and human ``detail`` carrying the evidence for
that judgment.

A ``not-applicable`` verdict must be backed by POSITIVE detection evidence —
mirroring ``datastore_coverage.py``'s discipline that a producer which never
scanned its source universe must not claim not-applicable — so construction
fails closed without a non-empty ``detail``.

Callers, not this module, are responsible for never mapping a legitimately
empty-but-successful scan to ``failed``: ``Coverage(applicability="applicable",
status="complete", ...)`` is valid even when the caller's fact list is empty,
and nothing here forbids that combination.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from ..datastore_coverage import DataModelCoverage

# Mirrors profiles/contracts.py's own `_SAFE_ID`; duplicated (not imported)
# because evidence/ must not import from analysis_wrapper.profiles.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

APPLICABILITY_VALUES = frozenset({"applicable", "not-applicable", "unknown"})
STATUS_VALUES = frozenset({"complete", "partial", "unavailable", "skipped", "failed"})

# Higher = worse. `skipped` and `unavailable` are tied on purpose: they are the
# same "nothing informative happened" outcome under two legacy vocabularies
# (status.py's skipped vs. capabilities.py's collapsed-to-unavailable), and
# neither is as bad as a producer that ran and hit trouble (`partial`) or
# outright failed. See aggregate() for how this ordering is used.
_STATUS_SEVERITY = {
    "complete": 0,
    "skipped": 1,
    "unavailable": 1,
    "partial": 2,
    "failed": 3,
}


@dataclass(frozen=True)
class Coverage:
    """A capability's applicability and outcome for one scope.

    ``applicability`` — is this capability in scope at all ("applicable"),
    positively confirmed out of scope ("not-applicable"), or not yet
    determined ("unknown")?

    ``status`` — what happened when (if) it ran: complete, partial,
    unavailable, skipped, or failed.

    ``reason_code`` — a short stable machine string a caller can branch or
    aggregate on.

    ``detail`` — free-text evidence for a human reader; may be empty UNLESS
    ``applicability`` is "not-applicable", in which case it must carry the
    positive evidence that justifies the verdict.
    """

    applicability: str
    status: str
    reason_code: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.applicability not in APPLICABILITY_VALUES:
            raise ValueError(
                f"Coverage.applicability must be one of {sorted(APPLICABILITY_VALUES)}; "
                f"got {self.applicability!r}"
            )
        if self.status not in STATUS_VALUES:
            raise ValueError(
                f"Coverage.status must be one of {sorted(STATUS_VALUES)}; got {self.status!r}"
            )
        if not isinstance(self.reason_code, str) or not _SAFE_ID.fullmatch(self.reason_code):
            raise ValueError(
                "Coverage.reason_code must use 1-128 letters, digits, dot, underscore, or hyphen"
            )
        if not isinstance(self.detail, str):
            raise ValueError("Coverage.detail must be a string")
        if self.applicability == "not-applicable" and not self.detail.strip():
            raise ValueError(
                "Coverage.applicability='not-applicable' requires a non-empty detail "
                "carrying positive evidence of detection (mirrors datastore_coverage.py's "
                "discipline: a producer that never scanned must not claim not-applicable)"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "applicability": self.applicability,
            "status": self.status,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }


def aggregate(coverages: Iterable[Coverage]) -> Coverage:
    """Worst-case ``Coverage`` across several values, without a complete
    facet masking a problem in another one.

    Ordering: coverages with ``applicability == "not-applicable"`` are
    excluded from the worst-case computation UNLESS every supplied coverage
    is not-applicable (in which case the aggregate is honestly
    not-applicable). Among the remaining ("in scope") coverages, the worst
    ``status`` wins by ``_STATUS_SEVERITY``; ties break on
    ``(status, reason_code, detail)`` so the result never depends on
    iteration order. The aggregate's own ``applicability`` is "applicable" if
    any considered coverage is applicable, else "unknown".
    """
    items = list(coverages)
    if not items:
        raise ValueError("aggregate() requires a non-empty sequence of Coverage values")
    if not all(isinstance(item, Coverage) for item in items):
        raise ValueError("aggregate() requires Coverage values")

    considered = [item for item in items if item.applicability != "not-applicable"]
    if not considered:
        return Coverage(
            applicability="not-applicable",
            status="complete",
            reason_code="all-facets-not-applicable",
            detail="every aggregated facet independently confirmed not-applicable",
        )

    ranked = sorted(
        considered,
        key=lambda item: (
            -_STATUS_SEVERITY[item.status], item.status, item.reason_code, item.detail
        ),
    )
    worst = ranked[0]
    applicability = ("applicable" if any(item.applicability == "applicable"
                                         for item in considered) else "unknown")
    return Coverage(
        applicability=applicability, status=worst.status,
        reason_code=worst.reason_code, detail=worst.detail,
    )


# ---------------------------------------------------------------------------
# Legacy adapters — conversion helpers for future migrations (57B-78/80-84).
# Nothing wires these into any live path; they exist so a later stage can
# translate an existing status value into the canonical two-axis Coverage
# without re-deriving the mapping.
# ---------------------------------------------------------------------------

_SIGNAL_STATUS_VALUES = {"complete", "partial", "failed", "skipped"}


def from_signal_status(status: str) -> Coverage:
    """Adapt status.py's 4-value signal vocabulary (complete/partial/failed/skipped).

    That vocabulary carries no applicability axis at all — a signal that ran
    is always, by definition, in scope — so every mapped value becomes
    ``applicability="applicable"``.
    """
    value = str(status)
    if value not in _SIGNAL_STATUS_VALUES:
        raise ValueError(f"unsupported legacy signal status: {value!r}")
    return Coverage(
        applicability="applicable", status=value,
        reason_code=f"signal-status-{value}",
        detail=f"mapped from legacy signal status {value!r}",
    )


_CAPABILITY_STATUS_VALUES = {"complete", "partial", "unavailable", "not-applicable", "failed"}


def from_capability_status(status: str, applicable: bool) -> Coverage:
    """Adapt capabilities.py's conflated 5-value status plus its separate
    ``applicable`` bool back into the two explicit axes.

    ``capabilities.py`` already carries an ``applicable`` flag alongside its
    status string (its status string can ALSO independently be the literal
    value "not-applicable" — the exact conflation this module replaces), so
    both are accepted here and cross-checked for consistency.
    """
    value = str(status)
    if value not in _CAPABILITY_STATUS_VALUES:
        raise ValueError(f"unsupported legacy capability status: {value!r}")
    if not applicable:
        return Coverage(
            applicability="not-applicable", status="complete",
            reason_code="capability-not-applicable",
            detail=f"legacy capability record marked not-applicable (status={value!r})",
        )
    if value == "not-applicable":
        raise ValueError(
            "legacy capability status 'not-applicable' is inconsistent with applicable=True"
        )
    return Coverage(
        applicability="applicable", status=value,
        reason_code=f"capability-status-{value}",
        detail=f"mapped from legacy capability status {value!r}",
    )


def from_datastore_coverage(coverage: "DataModelCoverage") -> Coverage:
    """Adapt ``datastore_coverage.DataModelCoverage``'s own conflated status.

    Same conflation as capabilities.py: ``DataModelCoverage.status`` can
    itself be the literal value "not-applicable".
    """
    value = coverage.status
    if value not in _CAPABILITY_STATUS_VALUES:
        raise ValueError(f"unsupported legacy datastore coverage status: {value!r}")
    detail = "; ".join(coverage.notes) if coverage.notes else ""
    if value == "not-applicable":
        return Coverage(
            applicability="not-applicable", status="complete",
            reason_code="datastore-coverage-not-applicable",
            detail=detail or "complete detector scan observed no datastore-family signals",
        )
    return Coverage(
        applicability="applicable", status=value,
        reason_code=f"datastore-coverage-{value}",
        detail=detail or f"mapped from legacy datastore coverage status {value!r}",
    )
