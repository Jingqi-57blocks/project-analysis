"""Dependency-map coverage contract — per-repo/lane status for ``imports/``.

Plain dataclasses + JSON, matching the rest of the wrapper. This records ONLY
what map was produced per repo (which lane, which output file, how many units);
the actual ``dependency`` edge counts live in the system-model's per-producer
coverage (derived from the assembled graph), so nothing is double-counted here.

Deterministic by construction: no wall time is generated, entries are sorted by
``(repository_ref, lane)``, and ``scan_date`` is a recorded input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..contract_version import CONTRACT_VERSION

# Per-repo/lane verdict — same vocabulary as the call-graph lane.
COVERAGE_STATES = ("complete", "partial", "failed", "unavailable")
# Lanes this stage drives.
LANES = ("go", "js")


@dataclass
class RepoDepCoverage:
    """One repo+lane dependency-map outcome."""

    repository_ref: str
    lane: str
    status: str                       # one of COVERAGE_STATES
    tool: str = ""
    tool_version: str = ""
    reason: str = ""
    map_file: str = ""                # basename written under imports/ ("" if none)
    units: int = 0                    # internal packages (go) / modules (js)
    warm_cache: str = "n/a"           # go: "warm"|"cold"|"already-warm"|"n/a"
    notes: str = ""
    reference_counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in COVERAGE_STATES:
            raise ValueError(f"RepoDepCoverage.status unsupported: {self.status!r}")
        if self.lane not in LANES:
            raise ValueError(f"RepoDepCoverage.lane unsupported: {self.lane!r}")

    def to_dict(self) -> dict:
        return {
            "repository_ref": self.repository_ref,
            "lane": self.lane, "status": self.status,
            "tool": self.tool, "tool_version": self.tool_version,
            "reason": self.reason, "map_file": self.map_file, "units": self.units,
            "warm_cache": self.warm_cache, "notes": self.notes,
            "reference_counts": dict(sorted(self.reference_counts.items())),
        }


@dataclass
class DepMapReport:
    """The ``imports/depmap-coverage.json`` payload: one entry per repo+lane."""

    scan_date: str
    repos: list[RepoDepCoverage] = field(default_factory=list)
    determinism: str = ("maps are projected leak-free and sorted before write; "
                        "identical inputs yield identical bytes")

    def to_dict(self) -> dict:
        ordered = sorted(self.repos, key=lambda c: (c.repository_ref, c.lane))
        return {
            "schema_version": CONTRACT_VERSION,
            "scan_date": self.scan_date,
            "determinism": self.determinism,
            "repos": [c.to_dict() for c in ordered],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
