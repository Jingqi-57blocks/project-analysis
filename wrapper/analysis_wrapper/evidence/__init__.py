"""Canonical evidence, coverage, and consumer-view types (57B-79).

This package defines the technology-neutral vocabulary later capability
providers converge on: a two-axis :class:`~.coverage.Coverage` (applicability
x status) instead of one conflated status enum, a citation-grounded
:class:`~.facts.Fact` / :class:`~.facts.SourceRef` pair, and deterministic
projections (:mod:`.catalog`, :mod:`.module_view`) over collections of
provider results.

Nothing here imports :mod:`analysis_wrapper.profiles`: the dependency runs the
other way, so profile contracts adopt these types without creating a cycle.
"""

from .coverage import (
    APPLICABILITY_VALUES,
    STATUS_VALUES,
    Coverage,
    aggregate,
    from_capability_status,
    from_datastore_coverage,
    from_signal_status,
)
from .facts import Fact, SourceRef, make_fact_id

__all__ = [
    "APPLICABILITY_VALUES",
    "STATUS_VALUES",
    "Coverage",
    "Fact",
    "SourceRef",
    "aggregate",
    "from_capability_status",
    "from_datastore_coverage",
    "from_signal_status",
    "make_fact_id",
]
