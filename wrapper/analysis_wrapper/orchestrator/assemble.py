"""Deterministic findings assembler (57B-113 / 57B-116, M2).

Mechanically applies a VALIDATED ``dedup-rank`` output's ``merge_map``/
``rank`` (synthesis.md step 4.5's dedup/merge semantics, already decided by
that task's own judgment pass) to the flat pool of every VALIDATED
``lens-findings`` output already in the run's ledger (``results.py``'s
``validated_outputs``) -- producing ``findings.json``-shaped rows, still
keyed by CANDIDATE module ids (``rekey.py`` re-keys those onto finalized
module ids afterward -- a later, separate step; see synthesis.md step 5).

**Design invariant this module upholds and must never regress**: it performs
NO judgment.

- A survivor's ``claim``/``lens``/``impact``/``suggested_direction``/
  ``priority``/``confidence``/``changeability_question`` are carried over
  VERBATIM -- never rewritten, even though a survivor's claim may now read
  narrower than the evidence merged beneath it (that asymmetry is left
  visible for the narrative stage, not silently patched here).
- Nothing is DROPPED except an evidence row that exactly duplicates another
  (identical ``fact``+``basis``+``refs``) already kept for the same
  survivor -- a near-duplicate (same fact worded differently, same refs but
  a different fact, etc.) is always kept; information preservation beats
  tidy output.
- Every finding the pool contains is accounted for exactly once: emitted as
  a survivor, or absorbed into exactly one emitted survivor -- enforced
  below by raising (see ``_pool_ids_covered_by_output``), not asserting,
  mirroring ``rekey.py``'s own "raise, don't assert" contract discipline.

This module never reads or writes anything but the run's ledger (via
``results.validated_outputs``) and its own explicit ``--out`` path -- no
judgment call, no LLM, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import findings
from .results import validated_outputs


class AssembleError(ValueError):
    """The run's ledger does not (yet) hold what assemble() needs, or the
    validated dedup-rank output does not cover the validated lens-findings
    pool it must exactly match."""


def _collect_pool(run: Path) -> dict[str, dict]:
    """``{finding_id: finding_row}`` across EVERY validated ``lens-findings``
    output in the run, in deterministic (sorted task_id) iteration order.

    Mirrors ``planner.py``'s ``plan_dedup`` pool-building exactly (same
    fail-closed rule: a ``finding_id`` produced by two different lens tasks
    is a planning error, never silently merged or overwritten) -- this is
    deliberately the SAME rule applied a second time, since a lens task can
    validate in between ``plan_dedup`` composing the dedup-rank task and
    this module assembling its result.
    """
    lens_outputs = validated_outputs(run, task_type="lens-findings")
    pool: dict[str, dict] = {}
    produced_by: dict[str, str] = {}
    for task_id in sorted(lens_outputs):
        for row in lens_outputs[task_id].get("findings", []):
            finding_id = row["finding_id"]
            if finding_id in produced_by:
                raise AssembleError(
                    f"finding_id {finding_id!r} was produced by both "
                    f"{produced_by[finding_id]!r} and {task_id!r} -- lens "
                    "finding_ids must be globally unique across this run")
            produced_by[finding_id] = task_id
            # Stable lineage starts at the submitted lens finding.  It is
            # carried through dedup/rekey unchanged so final audit can trace
            # a canonical finding back to its exact ledger generation.
            pooled = dict(row)
            pooled["lineage"] = {
                "source_finding_ids": [finding_id],
                "source_task_ids": [task_id],
            }
            pool[finding_id] = pooled
    return pool


def _dedup_output(run: Path) -> dict:
    """The single validated ``dedup-rank`` output -- fails closed if zero or
    more than one exist (a run must compose exactly one global dedup-rank
    task; see ``planner.plan_dedup``)."""
    outputs = validated_outputs(run, task_type="dedup-rank")
    if not outputs:
        raise AssembleError(
            "no validated dedup-rank task found -- run plan-dedup and its "
            "executor to completion before assemble-findings")
    if len(outputs) > 1:
        raise AssembleError(
            "expected exactly one validated dedup-rank task, found "
            f"{len(outputs)}: {', '.join(sorted(outputs))}")
    return next(iter(outputs.values()))


def _absorbed_by_survivor(merge_map: dict[str, dict]) -> dict[str, list[str]]:
    """``{survivor_id: [absorbed_id, ...]}``, each list sorted by absorbed
    id -- the "merge_map iteration order sorted by absorbed id" the merged
    evidence sequence below is built from, so the result never depends on
    the merge_map's own (JSON object) key order."""
    grouped: dict[str, list[str]] = {}
    for finding_id, row in merge_map.items():
        if row["status"] == "absorbed":
            grouped.setdefault(row["absorbed_into"], []).append(finding_id)
    for absorbed_ids in grouped.values():
        absorbed_ids.sort()
    return grouped


def _dedupe_evidence(rows: list[dict]) -> list[dict]:
    """Order-preserving dedup on EXACT row equality (``fact``+``basis``+
    ``refs``, refs order included) -- a near-duplicate that differs in any
    of those is a DIFFERENT row and is always kept."""
    seen: set[str] = set()
    result: list[dict] = []
    for row in rows:
        key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _merge_trace(absorbed_ids: list[str], merge_map: dict[str, dict]) -> str:
    """One mechanical, single-line sentence recording what was merged and
    why -- so the merge is visible in the artifact itself, never silent."""
    reasons = "; ".join(f"{aid}: {merge_map[aid]['reason']}" for aid in absorbed_ids)
    return f"merged: absorbed {', '.join(absorbed_ids)} per dedup-rank ({reasons})."


def _assembled_row(survivor_id: str, pool: dict[str, dict],
                   absorbed_ids: list[str], merge_map: dict[str, dict]) -> dict:
    survivor = pool[survivor_id]
    evidence = _dedupe_evidence(
        list(survivor["evidence"])
        + [item for aid in absorbed_ids for item in pool[aid]["evidence"]])
    affected_modules = sorted(
        set(survivor["affected_modules"]).union(
            *(set(pool[aid]["affected_modules"]) for aid in absorbed_ids)))
    evidence_basis = sorted({item["basis"] for item in evidence})
    limitations = survivor["limitations"]
    if absorbed_ids:
        limitations = f"{limitations} {_merge_trace(absorbed_ids, merge_map)}"
    return {
        "finding_id": survivor_id,
        "claim": survivor["claim"],
        "lens": survivor["lens"],
        "affected_modules": affected_modules,
        "evidence": evidence,
        "evidence_basis": evidence_basis,
        "impact": survivor["impact"],
        "priority": survivor["priority"],
        "confidence": survivor["confidence"],
        "limitations": limitations,
        "suggested_direction": survivor["suggested_direction"],
        "changeability_question": survivor["changeability_question"],
        "lineage": {
            "source_finding_ids": [survivor_id, *absorbed_ids],
            "source_task_ids": sorted({task_id for finding_id in [survivor_id, *absorbed_ids]
                                        for task_id in pool[finding_id].get("lineage", {}).get(
                                            "source_task_ids", [])}),
            "dedup_survivor_id": survivor_id,
        },
    }


def assemble(run_dir: str | Path) -> dict:
    """Pure, deterministic ``findings.json``-shaped document (``{finding_id:
    row}`` union of the run's lens-findings pool, merged/ranked exactly as
    the run's own validated dedup-rank output says) -- rows ordered by rank.

    Fails closed (:class:`AssembleError`) when: no validated dedup-rank
    output exists; more than one does; its ``merge_map`` does not cover
    EXACTLY the current lens-findings pool's finding_ids (a lens task
    validated -- or re-validated under a new generation -- after dedup-rank
    ran, or vice versa); or the pool itself has a cross-lens duplicate
    finding_id (see ``_collect_pool``).
    """
    run = Path(run_dir).expanduser().resolve()
    pool = _collect_pool(run)
    dedup = _dedup_output(run)
    merge_map = dedup["merge_map"]

    pool_ids = set(pool)
    merge_ids = set(merge_map)
    if merge_ids != pool_ids:
        detail = []
        missing = pool_ids - merge_ids
        extra = merge_ids - pool_ids
        if missing:
            detail.append(f"in the lens-findings pool but not in merge_map: "
                          f"{sorted(missing)}")
        if extra:
            detail.append(f"in merge_map but not in the lens-findings pool: "
                          f"{sorted(extra)}")
        raise AssembleError(
            "dedup-rank's merge_map does not cover exactly the current "
            "lens-findings pool -- " + "; ".join(detail))

    absorbed_by_survivor = _absorbed_by_survivor(merge_map)
    rows = [_assembled_row(rank_row["finding_id"], pool,
                           absorbed_by_survivor.get(rank_row["finding_id"], []), merge_map)
           for rank_row in dedup["rank"]]

    # Belt-and-suspenders accounting check (raised, not asserted -- see this
    # module's docstring): every pool finding must be either an emitted
    # survivor or absorbed into exactly one of them. schemas.py's dedup-rank
    # validator already guarantees this INTERNALLY (merge_map's declared
    # "surviving" set equals rank's ids exactly, and every "absorbed" row's
    # absorbed_into names one of those survivors) and the id-set check just
    # above ties that internal guarantee to THIS run's real pool -- so this
    # can only fire if a future schema change loosens one of those, not on
    # any input this module currently accepts.
    accounted = {row["finding_id"] for row in rows} | {
        aid for absorbed_ids in absorbed_by_survivor.values() for aid in absorbed_ids}
    if accounted != pool_ids:
        raise AssembleError(
            "internal invariant violated: not every lens-findings pool "
            "finding was emitted as a survivor or absorbed into one")

    return {"schema_version": findings.SCHEMA_VERSION, "findings": rows}
