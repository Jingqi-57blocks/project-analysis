"""Derived legacy-shaped stack view over bundled technology facets.

Discovery owns only the facet detector. This module remains temporarily because
current consumers still expect ``StackReport`` while their provider migration
is completed in later architecture issues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..profiles.bundled import bundled_registry
from ..profiles.detection import detect as detect_facets
from ..profiles.detection import gomod_requires

# This legacy display block's evidence surface is FROZEN to these facet
# kinds. New facet kinds (``datastore`` in 57B-80; more to come in later
# migrations, e.g. deployment/protocol) are additive in the bundled
# ``technology_facets`` list ONLY — they must never alter this legacy
# stacks/evidence block, which deterministic parity compares byte-for-byte.
STACK_REPORT_FACET_KINDS = frozenset({
    "language", "ecosystem", "framework", "repository-trait",
})


@dataclass
class StackReport:
    stacks: list[str] = field(default_factory=list)
    analysis_roots: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def detect(repo_path: str | Path) -> StackReport:
    detected = detect_facets(repo_path)
    registry = bundled_registry()
    languages = [
        registry.profile(facet.profile_id).display_name
        for facet in detected.facets if facet.kind == "language"
    ]
    frameworks = [
        registry.profile(facet.profile_id).display_name
        for facet in detected.facets if facet.kind == "framework"
    ]
    evidence = sorted({
        f"{facet.profile_id}: {item}"
        for facet in detected.facets if facet.kind in STACK_REPORT_FACET_KINDS
        for item in facet.evidence
    })
    evidence.extend(detected.notes)
    return StackReport(
        stacks=sorted(languages),
        analysis_roots=list(detected.analysis_roots),
        frameworks=sorted(frameworks),
        evidence=evidence,
    )
