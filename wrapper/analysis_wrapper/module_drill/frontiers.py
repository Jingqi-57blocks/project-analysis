"""Deterministic initial frontier planning from selected feature anchors."""

from __future__ import annotations

import hashlib
from typing import Iterable

from .scope import FeatureSeed, FrontierWorkItem

_EDGE_FOR_SEED_KIND = {
    "ui-action": ("ui-route", "outbound"),
    "route": ("route-handler", "outbound"),
    "datastore": ("datastore-access", "inbound"),
    "job-event": ("async-consumer", "outbound"),
    "symbol": ("symbol-reference", "outbound"),
    "package": ("integration-boundary", "outbound"),
    "path": ("path-reference", "outbound"),
    "module": ("module-reference", "outbound"),
}


def _token(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def initial(selected_seed_ids: Iterable[str], seeds: Iterable[FeatureSeed]) -> tuple[FrontierWorkItem, ...]:
    """Create wave-zero frontier work without inferring a business path.

    Each frontier is a deterministic request to inspect one bounded edge type
    from one selected evidence anchor.  It intentionally does not follow the
    edge; 57B-138 expansion owns that later operation and its receipts.
    """
    selected = frozenset(selected_seed_ids)
    rows: list[FrontierWorkItem] = []
    for seed in sorted(seeds, key=lambda row: row.seed_id):
        if seed.seed_id not in selected:
            continue
        edge_kind, direction = _EDGE_FOR_SEED_KIND[seed.kind]
        identity = _token(seed.seed_id, edge_kind, direction, "0")
        rows.append(FrontierWorkItem(
            frontier_id=f"frontier-{identity}", anchor_id=seed.seed_id,
            edge_kind=edge_kind, direction=direction, wave=0,
            cycle_key=f"cycle-{_token(seed.seed_id, edge_kind)}",
            evidence_refs=seed.evidence_refs,
            reason="deterministic selected feature anchor requires bounded structural tracing",
        ))
    return tuple(rows)
