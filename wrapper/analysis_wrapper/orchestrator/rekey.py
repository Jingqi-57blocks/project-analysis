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
from .results import validated_outputs

TERMINAL_DISPOSITIONS = {
    "consumed", "evidence-backed-no-finding", "partial", "failed",
}


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


def apply_resolution(run_dir: str | Path, *, rekeyed: list[dict],
                     tail: list[dict]) -> dict | None:
    """Apply validated terminal dispositions to an exact rekey tail.

    This is deliberately mechanical. A model task decides only the finite
    disposition and, when consumed, the already-finalized module ids. The
    source finding id, claim and evidence survive unchanged; a partial or
    failed mandatory tail remains canonical in the terminal ledger and is
    returned to the caller so it can block authoritative completion.
    """
    run = Path(run_dir).expanduser().resolve()
    outputs = validated_outputs(run, task_type="rekey-resolution")
    if not outputs:
        return None
    tail_by_id = {row.get("finding_id"): row for row in tail if isinstance(row, dict)}
    if len(tail_by_id) != len(tail):
        raise ValueError("rekey tail must have unique finding_id rows")
    rows: list[dict] = []
    for task_id, output in sorted(outputs.items()):
        task_rows = output.get("dispositions") if isinstance(output, dict) else None
        if not isinstance(task_rows, list) or not all(isinstance(row, dict) for row in task_rows):
            raise ValueError(f"rekey-resolution {task_id} has no dispositions list")
        rows.extend(task_rows)
    by_id = {row.get("finding_id"): row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != set(tail_by_id):
        raise ValueError("rekey-resolution must disposition every tail finding exactly once")
    _candidates, module_doc = module_map.validate(run)
    module_ids = {row.get("module_id") for row in module_doc.get("modules", [])}
    consumed: list[dict] = []
    terminal: list[dict] = []
    for finding_id in sorted(by_id):
        disposition = by_id[finding_id]
        status = disposition.get("disposition")
        if status not in TERMINAL_DISPOSITIONS:
            raise ValueError(f"{finding_id}: unsupported terminal disposition {status!r}")
        refs = disposition.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"{finding_id}: terminal disposition needs evidence_refs")
        terminal_row = {
            "finding_id": finding_id,
            "disposition": status,
            "reason_code": disposition.get("reason_code", ""),
            "evidence_refs": list(refs),
            "coverage_impact": disposition.get("coverage_impact", ""),
        }
        if status == "consumed":
            target_ids = disposition.get("module_ids")
            if not isinstance(target_ids, list) or not target_ids or not set(target_ids) <= module_ids:
                raise ValueError(f"{finding_id}: consumed disposition references unknown module")
            finding = dict(tail_by_id[finding_id])
            finding.pop("candidate_dispositions", None)
            finding["affected_modules"] = sorted(set(target_ids))
            finding["lineage"] = {
                "rekey_resolution": "consumed",
                "terminal_evidence_refs": list(refs),
                "reason_code": terminal_row["reason_code"],
            }
            consumed.append(finding)
            terminal_row["consumer"] = "findings.json"
        else:
            terminal_row["consumer"] = "finding-terminal-dispositions.json"
        terminal.append(terminal_row)
    finding_ids = [row.get("finding_id") for row in rekeyed + consumed]
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("rekey resolution duplicated a canonical finding_id")
    return {
        "findings": rekeyed + consumed,
        "terminal_dispositions": terminal,
        "blocking": [row for row in terminal if row["disposition"] in {"partial", "failed"}],
    }
