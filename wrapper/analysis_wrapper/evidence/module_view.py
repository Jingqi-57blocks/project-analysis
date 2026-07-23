"""Module-scoped evidence view (57B-79) — no capability providers exist yet.

Projects a set of :class:`~analysis_wrapper.profiles.contracts.CapabilityResult`
values onto each ``module`` node of a ``SystemModel``-shaped document (the
node/edge shape module_map.py's ``load_into`` already produces: a ``module``
node plus ``containment`` edges to the nodes it owns).

There is no per-node fact linkage yet (no real provider has been wired in), so
a fact is attributed to a module by repository scope only: it "belongs" to a
module when one of its source references names a repository that module owns
at least one node in. This is a deliberately coarse, technology-neutral
placeholder — precise per-node linkage is a later stage's job, not this one's.

This module does not import :mod:`analysis_wrapper.profiles` at runtime (only
under ``TYPE_CHECKING``, matching :mod:`analysis_wrapper.evidence.catalog`'s
own cycle-avoidance) and does not call any system-model or profile production
code — it is a pure projection over data already in memory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from .coverage import Coverage, aggregate
# synthesis_input.py's `_bounded` defines the disclosure shape
# (total_count/included_count/truncated/items) this view reuses verbatim.
from ..synthesis_input import _bounded

if TYPE_CHECKING:
    from ..profiles.contracts import CapabilityResult


def _owned_repository_refs(model: dict[str, Any], module_id: str) -> set[str]:
    by_id = {node["id"]: node for node in model.get("nodes", [])}
    owned_ids = {
        edge["dst"] for edge in model.get("edges", [])
        if edge.get("type") == "containment" and edge.get("src") == module_id
    }
    return {
        by_id[node_id].get("repository_ref", "")
        for node_id in owned_ids
        if node_id in by_id and by_id[node_id].get("repository_ref")
    }


def _module_row(module: dict[str, Any], model: dict[str, Any],
                results: list["CapabilityResult"]) -> dict[str, Any]:
    module_id = module["id"]
    owned_refs = _owned_repository_refs(model, module_id)
    linked_facts: list[dict[str, Any]] = []
    linked_coverages: list[Coverage] = []
    for result in results:
        result_refs = {
            source_ref.repository_ref
            for fact in result.facts
            for source_ref in fact.source_refs
        }
        if not (result_refs & owned_refs):
            continue
        linked_coverages.append(result.coverage)
        for fact in result.facts:
            fact_refs = {source_ref.repository_ref for source_ref in fact.source_refs}
            if fact_refs & owned_refs:
                linked_facts.append(fact.to_dict())

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for row in linked_facts:
        by_kind.setdefault(row["kind"], []).append(row)

    source_refs = sorted({ref for row in linked_facts for ref in row["source_refs"]})

    coverage = (
        aggregate(linked_coverages) if linked_coverages else
        Coverage(applicability="unknown", status="unavailable",
                reason_code="no-linked-evidence",
                detail="no capability result's facts referenced a repository "
                       "owned by this module")
    )

    return {
        "module_id": module_id,
        "name": module.get("label", module_id),
        "classification": module.get("attrs", {}).get("classification", ""),
        "coverage": coverage.to_dict(),
        "evidence_by_kind": {
            kind: _bounded(rows, key=lambda row: row["fact_id"])
            for kind, rows in sorted(by_kind.items())
        },
        "facts": _bounded(linked_facts, key=lambda row: (row["kind"], row["fact_id"])),
        "source_refs": _bounded(
            [{"ref": ref} for ref in source_refs], key=lambda row: row["ref"]),
    }


def build(model: dict[str, Any], results: Iterable["CapabilityResult"]) -> dict[str, Any]:
    """Project ``results`` onto each ``module`` node in ``model``.

    ``model`` is the ``SystemModel.to_dict()`` shape (module nodes plus the
    ``containment`` edges ``module_map.load_into`` produces). ``results`` is
    any iterable of ``CapabilityResult`` values. Deterministic: module rows
    are sorted by ``module_id`` and every nested list is bounded and sorted.
    """
    results = list(results)
    modules = [node for node in model.get("nodes", []) if node.get("kind") == "module"]
    rows = [_module_row(module, model, results) for module in modules]
    return {"modules": sorted(rows, key=lambda row: row["module_id"])}
