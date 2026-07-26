"""Per-task-type OUTPUT schemas for the orchestrator (57B-113 / 57B-114, M0).

Every function here is a PURE structural check: it inspects the shape of a
JSON value an executor returned for one task type and returns a list of
structured failures (``{"check", "detail", "location"}``; empty = valid). None
of it opens a run directory or resolves a citation against real evidence —
that is ``validators.py``'s job (``validate_citations`` and friends), which an
orchestrator runs AFTER a packet passes its schema check here.

Where a task type's output mirrors an existing wrapper artifact (module
formation mirrors ``module_map.py``'s ``module-map.json``; findings mirror
``lenses/_shared.md``'s atomic shape and what ``findings.py`` expects
structurally), the enum/field vocabulary is imported from that module so the
two stay in lockstep — but the deep, run-dir-dependent checks those modules
perform (candidate-universe membership, citation resolution, independent-
signal counting) are NOT repeated here; they still happen later, against the
real run, via the existing modules.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .. import module_map
from .contracts import TASK_TYPES

Failure = dict[str, str]

# --------------------------------------------------------------------------- #
# Shared vocabulary (kept in one place so schema checks below cannot drift
# from each other; independent from — and deliberately not imported from —
# findings.py/module_map.py's own PRIVATE helpers).
# --------------------------------------------------------------------------- #

FINDING_ID = re.compile(r"^finding-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

EVIDENCE_BASES = {
    "static-reference", "declaration", "configuration", "history",
    "inferred-linkage", "runtime-observation", "user-confirmed",
}
# A lens/synthesis task producing a STATIC overview never has runtime or
# human-confirmed evidence available to it (mirrors findings.py's identical
# restriction on the finding-evidence basis it accepts).
STATIC_EVIDENCE_BASES = EVIDENCE_BASES - {"runtime-observation", "user-confirmed"}

PRIORITIES = {"critical", "high", "medium", "low"}
CONFIDENCES = {"high", "medium", "low"}
SIGNAL_STATUSES = {"complete", "partial", "failed", "skipped"}

# The three citation grammars used throughout Project Analysis evidence:
# ``repo@revision:path:line``, ``signals/<view>:<line>``, ``metric:<ref>``.
# Defined once here and reused by ``validators.py`` for the structural half
# of its citation check, so the grammar itself never drifts between the two
# modules — ``validators.py`` alone resolves a ref against a real run dir.
SOURCE_REF = re.compile(r"^[^@\s]+@[^:\s]+:[^:\s]+(?:/[^:\s]+)*:[0-9]+$")
SIGNAL_REF = re.compile(r"^signals/[^:\s]+:[0-9]+$")
METRIC_REF = re.compile(r"^metric:.+$")


def citation_grammar_kind(ref: str) -> str | None:
    """``"source" | "signal" | "metric" | None`` (unrecognized grammar)."""
    if not isinstance(ref, str) or not ref:
        return None
    if METRIC_REF.fullmatch(ref):
        return "metric"
    if SIGNAL_REF.fullmatch(ref):
        return "signal"
    if SOURCE_REF.fullmatch(ref):
        return "source"
    return None


class _Failures:
    """Small ergonomic collector for the ``{check, detail, location}`` shape
    every validator in this package returns."""

    def __init__(self) -> None:
        self._rows: list[Failure] = []

    def add(self, check: str, detail: str, location: str = "") -> None:
        self._rows.append({"check": check, "detail": detail, "location": location})

    def require(self, condition: bool, check: str, detail: str, location: str = "") -> bool:
        if not condition:
            self.add(check, detail, location)
        return condition

    @property
    def rows(self) -> list[Failure]:
        return list(self._rows)


def _one_line_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value and "\r" not in value


def _string_list(value: Any, *, allow_empty: bool = True) -> bool:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return False
    return allow_empty or bool(value)


# --------------------------------------------------------------------------- #
# lens-findings
# --------------------------------------------------------------------------- #

def _validate_evidence_row(row: Any, location: str, failures: _Failures) -> set[str]:
    bases: set[str] = set()
    if not isinstance(row, dict):
        failures.add("evidence-shape", "evidence row must be an object", location)
        return bases
    failures.require(_one_line_str(row.get("fact")),
                      "evidence-fact", "fact must be one non-empty line", f"{location}.fact")
    basis = row.get("basis")
    if failures.require(basis in STATIC_EVIDENCE_BASES,
                         "evidence-basis",
                         "basis must be one of " + ", ".join(sorted(STATIC_EVIDENCE_BASES)),
                         f"{location}.basis"):
        bases.add(basis)
    refs = row.get("refs")
    if not failures.require(_string_list(refs, allow_empty=False),
                             "evidence-refs", "refs must be a non-empty string list",
                             f"{location}.refs"):
        return bases
    for index, ref in enumerate(refs):
        failures.require(citation_grammar_kind(ref) is not None,
                          "evidence-ref-grammar",
                          f"ref does not match a recognized citation grammar: {ref!r}",
                          f"{location}.refs[{index}]")
    return bases


def _validate_finding(row: Any, location: str, failures: _Failures) -> None:
    if not isinstance(row, dict):
        failures.add("finding-shape", "finding must be an object", location)
        return
    failures.require(isinstance(row.get("finding_id"), str)
                      and bool(FINDING_ID.fullmatch(row["finding_id"])),
                      "finding-id", "finding_id must match finding-<kebab-case>",
                      f"{location}.finding_id")
    for key in ("claim", "lens", "impact", "limitations", "suggested_direction"):
        failures.require(_one_line_str(row.get(key)), f"finding-{key}",
                          f"{key} must be one non-empty line", f"{location}.{key}")
    failures.require(_string_list(row.get("affected_modules"), allow_empty=False),
                      "finding-affected-modules",
                      "affected_modules must be a non-empty string list",
                      f"{location}.affected_modules")
    failures.require(row.get("priority") in PRIORITIES, "finding-priority",
                      "priority must be one of " + ", ".join(sorted(PRIORITIES)),
                      f"{location}.priority")
    failures.require(row.get("confidence") in CONFIDENCES, "finding-confidence",
                      "confidence must be one of " + ", ".join(sorted(CONFIDENCES)),
                      f"{location}.confidence")
    evidence = row.get("evidence")
    if not failures.require(isinstance(evidence, list) and bool(evidence),
                             "finding-evidence", "evidence must be a non-empty list",
                             f"{location}.evidence"):
        return
    bases: set[str] = set()
    for index, item in enumerate(evidence):
        bases |= _validate_evidence_row(item, f"{location}.evidence[{index}]", failures)
    declared_bases = row.get("evidence_basis")
    failures.require(isinstance(declared_bases, list) and set(declared_bases) == bases,
                      "finding-evidence-basis",
                      "evidence_basis must equal the set of bases used in evidence",
                      f"{location}.evidence_basis")
    # Structural half only of findings.py's "high confidence needs >=2
    # independent signals" rule: independence itself requires resolving refs
    # against a run dir (validators.validate_citations' job); here we can only
    # check the evidence-row COUNT.
    if row.get("confidence") == "high":
        failures.require(len(evidence) >= 2, "finding-confidence-high-evidence",
                          "confidence high requires at least two evidence rows",
                          f"{location}.evidence")


def _validate_coverage_row(row: Any, location: str, failures: _Failures) -> None:
    if not isinstance(row, dict):
        failures.add("coverage-shape", "coverage row must be an object", location)
        return
    failures.require(_one_line_str(row.get("signal")), "coverage-signal",
                      "signal must be one non-empty line", f"{location}.signal")
    failures.require(row.get("status") in SIGNAL_STATUSES, "coverage-status",
                      "status must be one of " + ", ".join(sorted(SIGNAL_STATUSES)),
                      f"{location}.status")
    failures.require(isinstance(row.get("note"), str), "coverage-note",
                      "note must be a string", f"{location}.note")


def _validate_lens_findings(obj: Any) -> list[Failure]:
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "lens-findings output must be an object")
        return failures.rows
    findings = obj.get("findings")
    if failures.require(isinstance(findings, list), "findings-shape",
                         "findings must be a list", "findings"):
        seen_ids: set[str] = set()
        for index, row in enumerate(findings):
            _validate_finding(row, f"findings[{index}]", failures)
            finding_id = row.get("finding_id") if isinstance(row, dict) else None
            if isinstance(finding_id, str):
                failures.require(finding_id not in seen_ids, "finding-id-unique",
                                  f"duplicate finding_id: {finding_id}",
                                  f"findings[{index}].finding_id")
                seen_ids.add(finding_id)
    coverage = obj.get("coverage")
    if failures.require(isinstance(coverage, list), "coverage-shape",
                         "coverage must be a list", "coverage"):
        for index, row in enumerate(coverage):
            _validate_coverage_row(row, f"coverage[{index}]", failures)
    return failures.rows


# --------------------------------------------------------------------------- #
# formation-proposal / boundary-resolution
# --------------------------------------------------------------------------- #

# Mirrors module_map.py's own per-row field sets structurally; the actual
# candidate-universe cross-check (does this candidate_id/module_id exist,
# is every candidate covered exactly once) happens later, against the real
# run, via module_map.validate()/expand_candidate_rules().
_RULE_SELECTOR_FIELDS = {
    "candidate_ids", "repository_refs", "signal_kinds", "values",
    "value_prefixes", "evidence_path_prefixes", "node_ids",
}
_RULE_FIELDS = {"rule_id", "selectors", "remaining", "disposition", "module_ids", "reason"}
_DISPOSITION_FIELDS = {"candidate_id", "disposition", "module_ids", "reason"}
_ADDED_CANDIDATE_FIELDS = {"candidate_id", "repository_ref", "value", "evidence", "node_ids"}


def _validate_module_row(row: Any, location: str, failures: _Failures) -> None:
    if not isinstance(row, dict):
        failures.add("module-shape", "module must be an object", location)
        return
    failures.require(isinstance(row.get("module_id"), str)
                      and bool(SLUG.fullmatch(row["module_id"])),
                      "module-id", "module_id must be a stable kebab-case slug",
                      f"{location}.module_id")
    failures.require(_one_line_str(row.get("name")), "module-name",
                      "name must be one non-empty line", f"{location}.name")
    failures.require(row.get("classification") in module_map.CLASSIFICATIONS,
                      "module-classification",
                      "classification must be one of " + ", ".join(module_map.CLASSIFICATIONS),
                      f"{location}.classification")
    failures.require(row.get("confidence") in CONFIDENCES, "module-confidence",
                      "confidence must be one of " + ", ".join(sorted(CONFIDENCES)),
                      f"{location}.confidence")
    failures.require(_string_list(row.get("aliases")), "module-aliases",
                      "aliases must be a string list", f"{location}.aliases")


def _validate_disposition_arity(disposition: str, module_ids: list[str],
                                location: str, failures: _Failures) -> None:
    if disposition in {"standalone", "merged", "platform", "shared-infrastructure"}:
        failures.require(len(module_ids) == 1, "disposition-arity",
                          f"disposition {disposition!r} must map to exactly one module",
                          location)
    elif module_ids:
        failures.add("disposition-arity",
                      f"disposition {disposition!r} cannot map to a module", location)


def _validate_disposition_row(row: Any, location: str, failures: _Failures) -> None:
    if not isinstance(row, dict):
        failures.add("disposition-shape", "disposition row must be an object", location)
        return
    failures.require(isinstance(row.get("candidate_id"), str) and bool(row["candidate_id"]),
                      "disposition-candidate-id", "candidate_id must be a non-empty string",
                      f"{location}.candidate_id")
    disposition = row.get("disposition")
    if not failures.require(disposition in module_map.DISPOSITIONS,
                             "disposition-value",
                             "disposition must be one of " + ", ".join(module_map.DISPOSITIONS),
                             f"{location}.disposition"):
        return
    module_ids = row.get("module_ids")
    if not failures.require(_string_list(module_ids), "disposition-module-ids",
                             "module_ids must be a string list", f"{location}.module_ids"):
        return
    _validate_disposition_arity(disposition, module_ids, f"{location}.module_ids", failures)
    failures.require(_one_line_str(row.get("reason")), "disposition-reason",
                      "reason must be one non-empty line", f"{location}.reason")


def _validate_rule_row(row: Any, location: str, failures: _Failures) -> None:
    if not isinstance(row, dict):
        failures.add("rule-shape", "candidate rule must be an object", location)
        return
    unknown = set(row) - _RULE_FIELDS
    failures.require(not unknown, "rule-unknown-fields",
                      f"unsupported fields: {sorted(unknown)}", location)
    failures.require(isinstance(row.get("rule_id"), str) and bool(SLUG.fullmatch(row["rule_id"])),
                      "rule-id", "rule_id must be a stable kebab-case slug",
                      f"{location}.rule_id")
    is_remaining = row.get("remaining") is True
    if is_remaining:
        failures.require("selectors" not in row, "rule-remaining-selectors",
                          "a remaining rule cannot have selectors", location)
        failures.require(row.get("disposition") == "unresolved" and not row.get("module_ids"),
                          "rule-remaining-shape",
                          "a remaining rule must be unresolved with no module_ids", location)
    else:
        selectors = row.get("selectors")
        if failures.require(isinstance(selectors, list) and bool(selectors),
                             "rule-selectors", "selectors must be a non-empty list",
                             f"{location}.selectors"):
            for index, selector in enumerate(selectors):
                sel_location = f"{location}.selectors[{index}]"
                if not isinstance(selector, dict) or not selector:
                    failures.add("rule-selector-shape",
                                  "selector must be a non-empty object", sel_location)
                    continue
                unknown_selector = set(selector) - _RULE_SELECTOR_FIELDS
                failures.require(not unknown_selector, "rule-selector-fields",
                                  f"unsupported selector fields: {sorted(unknown_selector)}",
                                  sel_location)
        disposition = row.get("disposition")
        if failures.require(disposition in module_map.DISPOSITIONS, "rule-disposition",
                             "disposition must be one of " + ", ".join(module_map.DISPOSITIONS),
                             f"{location}.disposition"):
            module_ids = row.get("module_ids")
            if failures.require(_string_list(module_ids), "rule-module-ids",
                                 "module_ids must be a string list", f"{location}.module_ids"):
                _validate_disposition_arity(disposition, module_ids,
                                            f"{location}.module_ids", failures)
    failures.require(_one_line_str(row.get("reason")), "rule-reason",
                      "reason must be one non-empty line", f"{location}.reason")


def _validate_added_candidate_row(row: Any, location: str, failures: _Failures) -> None:
    if not isinstance(row, dict):
        failures.add("added-candidate-shape", "additional candidate must be an object", location)
        return
    unknown = set(row) - _ADDED_CANDIDATE_FIELDS
    failures.require(not unknown, "added-candidate-fields",
                      f"unsupported fields: {sorted(unknown)}", location)
    candidate_id = row.get("candidate_id")
    failures.require(isinstance(candidate_id, str) and candidate_id.startswith("mc-added-"),
                      "added-candidate-id", "candidate_id must start with mc-added-",
                      f"{location}.candidate_id")
    failures.require(isinstance(row.get("repository_ref"), str) and bool(row["repository_ref"]),
                      "added-candidate-repository-ref",
                      "repository_ref must be a non-empty string", f"{location}.repository_ref")
    failures.require(isinstance(row.get("value"), str) and bool(row["value"]),
                      "added-candidate-value", "value must be a non-empty string",
                      f"{location}.value")
    failures.require(_string_list(row.get("evidence"), allow_empty=False),
                      "added-candidate-evidence",
                      "evidence must be a non-empty string list", f"{location}.evidence")
    failures.require(_string_list(row.get("node_ids")), "added-candidate-node-ids",
                      "node_ids must be a string list", f"{location}.node_ids")


def _validate_formation_proposal(obj: Any) -> list[Failure]:
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "formation-proposal output must be an object")
        return failures.rows
    modules = obj.get("modules")
    if failures.require(isinstance(modules, list) and bool(modules), "modules-shape",
                         "modules must be a non-empty list", "modules"):
        seen_ids: set[str] = set()
        for index, row in enumerate(modules):
            _validate_module_row(row, f"modules[{index}]", failures)
            module_id = row.get("module_id") if isinstance(row, dict) else None
            if isinstance(module_id, str):
                failures.require(module_id not in seen_ids, "module-id-unique",
                                  f"duplicate module_id: {module_id}",
                                  f"modules[{index}].module_id")
                seen_ids.add(module_id)
    for key, validate_row in (("candidate_dispositions", _validate_disposition_row),
                              ("additional_candidates", _validate_added_candidate_row)):
        if key not in obj:
            continue
        rows = obj[key]
        if failures.require(isinstance(rows, list), f"{key}-shape", f"{key} must be a list", key):
            for index, row in enumerate(rows):
                validate_row(row, f"{key}[{index}]", failures)
    if "candidate_rules" in obj:
        rules = obj["candidate_rules"]
        if failures.require(isinstance(rules, list) and bool(rules), "candidate-rules-shape",
                             "candidate_rules must be a non-empty list", "candidate_rules"):
            for index, row in enumerate(rules):
                _validate_rule_row(row, f"candidate_rules[{index}]", failures)
    return failures.rows


def _validate_boundary_resolution(obj: Any) -> list[Failure]:
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "boundary-resolution output must be an object")
        return failures.rows
    dispositions = obj.get("dispositions")
    if not failures.require(isinstance(dispositions, list) and bool(dispositions),
                             "dispositions-shape",
                             "dispositions must be a non-empty list", "dispositions"):
        return failures.rows
    seen: set[str] = set()
    for index, row in enumerate(dispositions):
        _validate_disposition_row(row, f"dispositions[{index}]", failures)
        candidate_id = row.get("candidate_id") if isinstance(row, dict) else None
        if isinstance(candidate_id, str):
            failures.require(candidate_id not in seen, "disposition-candidate-unique",
                              f"duplicate candidate_id: {candidate_id}",
                              f"dispositions[{index}].candidate_id")
            seen.add(candidate_id)
    return failures.rows


# --------------------------------------------------------------------------- #
# dedup-rank
# --------------------------------------------------------------------------- #

def _validate_dedup_rank(obj: Any) -> list[Failure]:
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "dedup-rank output must be an object")
        return failures.rows
    input_ids = obj.get("input_finding_ids")
    if not failures.require(_string_list(input_ids, allow_empty=False)
                             and len(input_ids) == len(set(input_ids)),
                             "input-finding-ids",
                             "input_finding_ids must be a non-empty, duplicate-free string list",
                             "input_finding_ids"):
        return failures.rows
    input_id_set = set(input_ids)

    merge_map = obj.get("merge_map")
    if not failures.require(isinstance(merge_map, dict), "merge-map-shape",
                             "merge_map must be an object", "merge_map"):
        return failures.rows
    failures.require(set(merge_map) == input_id_set, "merge-map-completeness",
                      "merge_map must contain exactly the declared input_finding_ids, "
                      "each exactly once", "merge_map")

    surviving: set[str] = set()
    for finding_id, row in merge_map.items():
        location = f"merge_map[{finding_id!r}]"
        if not isinstance(row, dict):
            failures.add("merge-map-row-shape", "merge_map row must be an object", location)
            continue
        status = row.get("status")
        if failures.require(status in {"surviving", "absorbed"}, "merge-map-status",
                             "status must be surviving or absorbed", f"{location}.status"):
            if status == "surviving":
                failures.require(row.get("absorbed_into") is None, "merge-map-survivor-target",
                                  "a surviving finding must have absorbed_into: null",
                                  f"{location}.absorbed_into")
                surviving.add(finding_id)
            else:
                target = row.get("absorbed_into")
                failures.require(isinstance(target, str) and target != finding_id,
                                  "merge-map-absorbed-target",
                                  "an absorbed finding must name a different absorbed_into id",
                                  f"{location}.absorbed_into")
        failures.require(_one_line_str(row.get("reason")), "merge-map-reason",
                          "reason must be one non-empty line", f"{location}.reason")

    # absorbed_into must resolve to a SURVIVING id, checked after the pass
    # above so a forward reference to a not-yet-seen survivor still resolves.
    for finding_id, row in merge_map.items():
        if isinstance(row, dict) and row.get("status") == "absorbed":
            target = row.get("absorbed_into")
            if isinstance(target, str):
                failures.require(target in surviving, "merge-map-absorbed-into-surviving",
                                  f"absorbed_into {target!r} is not a surviving finding",
                                  f"merge_map[{finding_id!r}].absorbed_into")

    rank = obj.get("rank")
    if failures.require(isinstance(rank, list), "rank-shape", "rank must be a list", "rank"):
        ranked_ids: list[str] = []
        for index, row in enumerate(rank):
            location = f"rank[{index}]"
            if not isinstance(row, dict):
                failures.add("rank-row-shape", "rank row must be an object", location)
                continue
            finding_id = row.get("finding_id")
            failures.require(isinstance(finding_id, str) and finding_id in surviving,
                              "rank-finding-id",
                              "finding_id must name a surviving finding", f"{location}.finding_id")
            failures.require(_one_line_str(row.get("reason")), "rank-reason",
                              "reason must be one non-empty line", f"{location}.reason")
            if isinstance(finding_id, str):
                ranked_ids.append(finding_id)
        failures.require(len(ranked_ids) == len(set(ranked_ids)), "rank-unique",
                          "rank must not repeat a finding_id", "rank")
        failures.require(set(ranked_ids) == surviving, "rank-completeness",
                          "rank must include every surviving finding exactly once", "rank")
    return failures.rows


# --------------------------------------------------------------------------- #
# section-generate
# --------------------------------------------------------------------------- #

def _validate_section_generate(obj: Any) -> list[Failure]:
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "section-generate output must be an object")
        return failures.rows
    failures.require(_one_line_str(obj.get("section_id")), "section-id",
                      "section_id must be one non-empty line", "section_id")
    content = obj.get("content_md")
    if failures.require(isinstance(content, str) and bool(content.strip()),
                         "content-md", "content_md must be a non-empty string", "content_md"):
        word_count = obj.get("word_count")
        expected = len(content.split())
        failures.require(word_count == expected, "word-count",
                          f"word_count ({word_count!r}) must equal the actual word "
                          f"count of content_md ({expected})", "word_count")
    else:
        failures.require(isinstance(obj.get("word_count"), int)
                          and not isinstance(obj.get("word_count"), bool)
                          and obj.get("word_count") >= 0,
                          "word-count", "word_count must be a non-negative integer", "word_count")
    return failures.rows


# --------------------------------------------------------------------------- #
# repair-edit-ops / coherence-check
# --------------------------------------------------------------------------- #

def _validate_edit_op_row(row: Any, location: str, failures: _Failures) -> None:
    if not isinstance(row, dict):
        failures.add("edit-op-shape", "edit op must be an object", location)
        return
    unknown = set(row) - {"locate", "replace", "fixes"}
    failures.require(not unknown, "edit-op-fields",
                      f"unsupported fields: {sorted(unknown)}", location)
    failures.require(isinstance(row.get("locate"), str) and bool(row["locate"]),
                      "edit-op-locate", "locate must be a non-empty string", f"{location}.locate")
    failures.require(isinstance(row.get("replace"), str), "edit-op-replace",
                      "replace must be a string", f"{location}.replace")
    failures.require(_one_line_str(row.get("fixes")), "edit-op-fixes",
                      "fixes must name one non-empty check id", f"{location}.fixes")


def _validate_edit_ops_list(obj: Any, failures: _Failures, *, location: str = "edits") -> None:
    if failures.require(isinstance(obj, list), f"{location}-shape",
                         f"{location} must be a list", location):
        for index, row in enumerate(obj):
            _validate_edit_op_row(row, f"{location}[{index}]", failures)


def _validate_repair_edit_ops(obj: Any) -> list[Failure]:
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "repair-edit-ops output must be an object")
        return failures.rows
    if not failures.require(isinstance(obj.get("edits"), list) and bool(obj.get("edits")),
                             "edits-shape", "edits must be a non-empty list", "edits"):
        return failures.rows
    _validate_edit_ops_list(obj["edits"], failures, location="edits")
    return failures.rows


def _validate_coherence_check(obj: Any) -> list[Failure]:
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "coherence-check output must be an object")
        return failures.rows
    consistent = obj.get("consistent")
    if not failures.require(isinstance(consistent, bool), "consistent-shape",
                             "consistent must be a boolean", "consistent"):
        return failures.rows
    edit_ops = obj.get("edit_ops")
    _validate_edit_ops_list(edit_ops, failures, location="edit_ops")
    if isinstance(edit_ops, list):
        if consistent:
            failures.require(not edit_ops, "coherence-consistent-no-edits",
                              "consistent: true requires an empty edit_ops list", "edit_ops")
        else:
            failures.require(bool(edit_ops), "coherence-inconsistent-needs-edits",
                              "consistent: false requires at least one edit op", "edit_ops")
    return failures.rows


# --------------------------------------------------------------------------- #
# selection-fetch
# --------------------------------------------------------------------------- #

def _validate_selection_row(row: Any, location: str, failures: _Failures) -> None:
    if not isinstance(row, dict):
        failures.add("selection-shape", "selection must be an object", location)
        return
    failures.require(isinstance(row.get("selection_id"), str)
                      and bool(SLUG.fullmatch(row["selection_id"])),
                      "selection-id", "selection_id must be a stable kebab-case slug",
                      f"{location}.selection_id")
    failures.require(_one_line_str(row.get("purpose")), "selection-purpose",
                      "purpose must be one non-empty line", f"{location}.purpose")
    ref = row.get("ref")
    failures.require(isinstance(ref, str) and citation_grammar_kind(ref) is not None,
                      "selection-ref", "ref must match a recognized citation grammar",
                      f"{location}.ref")
    failures.require(isinstance(row.get("quoted_text"), str) and bool(row["quoted_text"]),
                      "selection-quoted-text", "quoted_text must be a non-empty string",
                      f"{location}.quoted_text")


def _validate_selection_fetch(obj: Any) -> list[Failure]:
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "selection-fetch output must be an object")
        return failures.rows
    selections = obj.get("selections")
    if not failures.require(isinstance(selections, list) and bool(selections),
                             "selections-shape",
                             "selections must be a non-empty list", "selections"):
        return failures.rows
    seen: set[str] = set()
    for index, row in enumerate(selections):
        _validate_selection_row(row, f"selections[{index}]", failures)
        selection_id = row.get("selection_id") if isinstance(row, dict) else None
        if isinstance(selection_id, str):
            failures.require(selection_id not in seen, "selection-id-unique",
                              f"duplicate selection_id: {selection_id}",
                              f"selections[{index}].selection_id")
            seen.add(selection_id)
    return failures.rows


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

_VALIDATORS: dict[str, Callable[[Any], list[Failure]]] = {
    "lens-findings": _validate_lens_findings,
    "formation-proposal": _validate_formation_proposal,
    "boundary-resolution": _validate_boundary_resolution,
    "dedup-rank": _validate_dedup_rank,
    "section-generate": _validate_section_generate,
    "repair-edit-ops": _validate_repair_edit_ops,
    "coherence-check": _validate_coherence_check,
    "selection-fetch": _validate_selection_fetch,
}

assert set(_VALIDATORS) == TASK_TYPES  # every task type has exactly one schema


def validate_output(task_type: str, obj: Any) -> list[Failure]:
    """Structurally validate one task's output against its schema.

    Returns a list of ``{"check", "detail", "location"}`` failures (empty =
    valid). An unknown ``task_type`` is itself reported as a failure rather
    than raising, so a caller validating a batch of heterogeneous results
    never has to special-case this function with a try/except.
    """
    if task_type not in _VALIDATORS:
        return [{"check": "task-type", "detail": f"unknown task_type: {task_type!r}",
                 "location": "task_type"}]
    return _VALIDATORS[task_type](obj)
