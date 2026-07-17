"""The authoritative signal-status contract (plan §17.3, 57B-10 description).

Per signal:
  COMPLETE — tool ran, native exit within its normal-findings semantics, output
             shape validated.
  PARTIAL  — ran but coverage is materially incomplete (bounded-view failure,
             high unresolved-edge rate, compile/load failures among findings,
             PM-fallback approximation, single-language scan of a polyglot root).
  FAILED   — invoked but no valid result: native error exit, malformed output,
             mid-run network/auth errors, timeout. An ATTEMPTED run that hits a
             network error is FAILED, never skipped.
  SKIPPED  — never invoked: safety-guard refusal with no fallback, preflight-
             detected offline, tool not installed.

Severity order: FAILED > PARTIAL > SKIPPED > COMPLETE.
Aggregate = worst status present. Wrapper exit: any FAILED -> 3, else 0.
"""

from __future__ import annotations

import enum


class Status(enum.Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __str__(self) -> str:  # manifests print the bare value
        return self.value


# Higher = worse. The one place the ordering is defined.
_SEVERITY = {
    Status.COMPLETE: 0,
    Status.SKIPPED: 1,
    Status.PARTIAL: 2,
    Status.FAILED: 3,
}

FAILED_EXIT_CODE = 3


def severity(status: Status) -> int:
    return _SEVERITY[status]


def aggregate(statuses: list[Status]) -> Status:
    """Worst status present. FAIL-CLOSED on empty: an execution that produced
    zero signals must never look successful — "nothing ran" is a failure."""
    if not statuses:
        return Status.FAILED
    return max(statuses, key=severity)


def wrapper_exit_code(statuses: list[Status]) -> int:
    """Any FAILED -> nonzero; partial/skipped are disclosed, not fatal."""
    return FAILED_EXIT_CODE if aggregate(statuses) is Status.FAILED else 0
