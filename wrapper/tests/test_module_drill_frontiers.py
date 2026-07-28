"""Deterministic frontier-plan contract tests for 57B-138."""

from analysis_wrapper.module_drill.coverage import Coverage
from analysis_wrapper.module_drill.frontiers import initial
from analysis_wrapper.module_drill.scope import FeatureSeed


def _seed(seed_id, kind):
    return FeatureSeed(
        seed_id, kind, "service", ("service@NON-GIT:src/example.ts:1",),
        Coverage("unknown", "unavailable", (), ("deferred",)),
    )


def test_initial_frontiers_are_stable_and_only_expand_selected_seeds():
    seeds = (_seed("seed-ui", "ui-action"), _seed("seed-route", "route"),
             _seed("seed-other", "datastore"))
    first = initial(("seed-ui", "seed-route"), seeds)
    assert first == initial(("seed-route", "seed-ui"), reversed(seeds))
    assert [row.anchor_id for row in first] == ["seed-route", "seed-ui"]
    assert [row.edge_kind for row in first] == ["route-handler", "ui-route"]
    assert all(row.wave == 0 and row.cycle_key.startswith("cycle-") for row in first)
    assert all(row.evidence_refs for row in first)
