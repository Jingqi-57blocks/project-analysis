"""Deterministic finding re-key: candidate IDs -> finalized module IDs
(57B-116, M2).

A pre-finalization lens/synthesis task writes a finding's
``affected_modules`` using CANDIDATE ids (the only universe that exists
before ``finalize-module-map`` runs — see ``orchestrator/schemas.py``'s own
note that the deep, run-dir-dependent candidate-universe cross-check is
deferred to the real modules). Once ``module-map.json`` carries finalized
module dispositions, this module re-keys those candidate ids onto real
module ids via a PURE LOOKUP over ``module_map.validate()``'s already-
expanded ``candidate_dispositions`` rows — it invents nothing and never
infers a module for a candidate that has none.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import module_map


def _lookup(run_dir: str | Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    """``(candidate_id -> finalized module_id list, candidate_id ->
    disposition)`` straight from module-map.json's validated,
    already-expanded ``candidate_dispositions``. ``module_map.validate``
    fails closed (raises) if ``candidate_rules`` were never expanded into
    dispositions, or if any candidate in the run's universe is missing a
    disposition row -- so a caller here never silently re-keys against a
    half-finished module map."""
    _candidates_doc, module_doc = module_map.validate(run_dir)
    modules_by_candidate: dict[str, list[str]] = {}
    disposition_by_candidate: dict[str, str] = {}
    for row in module_doc["candidate_dispositions"]:
        candidate_id = row["candidate_id"]
        modules_by_candidate[candidate_id] = list(row.get("module_ids", []))
        disposition_by_candidate[candidate_id] = row["disposition"]
    return modules_by_candidate, disposition_by_candidate


def rekey(run_dir: str | Path, findings_doc: Any) -> dict:
    """Re-key every finding's ``affected_modules`` (candidate IDs) to
    finalized module IDs.

    Returns ``{"rekeyed": [...], "tail": [...]}``: every input finding lands
    in EXACTLY ONE of the two lists (enforced below by raising, not
    asserting -- this guarantee must not be strippable by running Python
    with ``-O``).

    - A finding whose affected candidates resolve to at least one real
      module is REKEYED: ``affected_modules`` is replaced by the sorted,
      deduplicated set of resolved module IDs. A candidate among its own
      list that resolves to nothing (excluded/unresolved/unknown) is simply
      not counted -- never guessed onto a neighboring module.
    - A finding whose affected candidates resolve to NO module at all
      (every one is ``excluded``/``unresolved``, or not present in the run's
      candidate universe) goes to ``tail`` UNCHANGED, plus a
      ``candidate_dispositions`` field recording exactly why (the real
      disposition string per candidate, or ``"unknown-candidate"`` when the
      id is not in module-map.json at all) -- a small judgment pass decides
      what happens to it next; this function never guesses.
    """
    if not isinstance(findings_doc, dict):
        raise ValueError("findings document must be a JSON object")
    findings = findings_doc.get("findings")
    if not isinstance(findings, list):
        raise ValueError("findings document must contain a 'findings' list")

    modules_by_candidate, disposition_by_candidate = _lookup(run_dir)

    rekeyed: list[dict] = []
    tail: list[dict] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(findings):
        if not isinstance(row, dict):
            raise ValueError(f"findings[{index}] must be an object")
        finding_id = row.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id:
            raise ValueError(f"findings[{index}].finding_id must be a non-empty string")
        if finding_id in seen_ids:
            raise ValueError(f"duplicate finding_id: {finding_id}")
        seen_ids.add(finding_id)
        affected = row.get("affected_modules")
        if not isinstance(affected, list) or not affected or not all(
                isinstance(item, str) and item for item in affected):
            raise ValueError(f"{finding_id}.affected_modules must be a non-empty string list")

        module_ids: set[str] = set()
        for candidate_id in affected:
            module_ids.update(modules_by_candidate.get(candidate_id, []))

        if module_ids:
            rekeyed_row = dict(row)
            rekeyed_row["affected_modules"] = sorted(module_ids)
            rekeyed.append(rekeyed_row)
        else:
            tail_row = dict(row)
            tail_row["candidate_dispositions"] = {
                candidate_id: disposition_by_candidate.get(candidate_id, "unknown-candidate")
                for candidate_id in sorted(set(affected))
            }
            tail.append(tail_row)

    # Exactly-once invariant: raised, not asserted, so this guarantee cannot
    # be silently stripped by running Python with -O (assertions vanish
    # there; a contract this module is defined by must not).
    if len(rekeyed) + len(tail) != len(findings):
        raise ValueError("every input finding must land in exactly one of rekeyed/tail")
    if {row["finding_id"] for row in rekeyed} & {row["finding_id"] for row in tail}:
        raise ValueError("a finding cannot appear in both rekeyed and tail")
    return {"rekeyed": rekeyed, "tail": tail}
