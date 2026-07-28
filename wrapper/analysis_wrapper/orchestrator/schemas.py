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

import json
import re
from typing import Any, Callable, Mapping

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
# The six changeability questions synthesis.md's overview diagnosis (section
# 11) and module-changeability table (section 13) are built from, plus
# `none` for a finding that sits outside all six (57B-116, M2). Order/names
# mirror synthesis.md's "## The six changeability questions" list exactly:
# boundary clarity, change spread, rule locality, hidden coupling,
# duplication & evolution debt, verification difficulty.
CHANGEABILITY_QUESTIONS = {
    "boundary-clarity", "change-spread", "rule-locality", "hidden-coupling",
    "duplication-evolution", "verification-difficulty", "none",
}

# The three citation grammars used throughout Project Analysis evidence:
# ``repo@revision:path:line``, ``signals/<view>:<line>``, ``metric:<ref>``.
# Defined once here and reused by ``validators.py`` for the structural half
# of its citation check, so the grammar itself never drifts between the two
# modules — ``validators.py`` alone resolves a ref against a real run dir.
#
# These MIRROR findings.py's own private grammar exactly (``_SIGNAL``,
# ``_METRIC``, ``_source_parts``) — verified byte-for-byte by
# test_orchestrator_schemas.py's drift-lock test, which imports those
# findings.py privates directly (test-only; production code here never
# imports from findings.py). Do not "improve" these independently of
# findings.py without updating that test.
SIGNAL_REF = re.compile(r"^signals/([^:]+):(\d+)$")
METRIC_REF = re.compile(r"^(?:metric:|workspace-metrics\.json#metric:)(.+)$")


def signal_ref_parts(ref: str) -> tuple[str, str] | None:
    """``(relative_view_path, line_text)`` — mirrors findings.py's ``_SIGNAL``
    exactly: the view-path segment allows any character except a colon
    (including whitespace and additional ``/`` separators)."""
    match = SIGNAL_REF.fullmatch(ref)
    return match.groups() if match else None


def metric_ref_id(ref: str) -> str | None:
    """The bare ``metric_ref`` id, accepting either the short ``metric:`` form
    or the long ``workspace-metrics.json#metric:`` form — mirrors findings.py's
    ``_METRIC`` exactly."""
    match = METRIC_REF.fullmatch(ref)
    return match.group(1) if match else None


def source_ref_parts(ref: str) -> tuple[str, str, str, str] | None:
    """``(repository_ref, revision, relative_path, line_text)`` — mirrors
    findings.py's ``_source_parts`` exactly (rpartition/partition based, NOT
    a single regex): the repository ref may itself contain ``@`` (only the
    LAST ``@`` splits repo from revision+position), and the path may contain
    colons or whitespace (only the LAST ``:`` before the run splits path from
    line — everything after it must be all digits)."""
    repository_ref, marker, tail = ref.rpartition("@")
    revision, separator, position = tail.partition(":")
    relative, line_separator, line = position.rpartition(":")
    if not marker or not separator or not line_separator or not repository_ref \
            or not revision or not relative or not line.isdigit():
        return None
    return repository_ref, revision, relative, line


def citation_grammar_kind(ref: str) -> str | None:
    """``"source" | "signal" | "metric" | None`` (unrecognized grammar).

    Dispatch priority mirrors findings.py's own ``_validate_ref``: a
    ``metric:``/``workspace-metrics.json#metric:`` prefix is checked first;
    a ``signals/`` prefix then COMMITS to signal-ref grammar (it is never
    retried as a source ref, even if the signal grammar itself fails) —
    otherwise the ref is checked as a source ref.
    """
    if not isinstance(ref, str) or not ref:
        return None
    if METRIC_REF.fullmatch(ref):
        return "metric"
    if ref.startswith("signals/"):
        return "signal" if SIGNAL_REF.fullmatch(ref) else None
    return "source" if source_ref_parts(ref) is not None else None


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
    failures.require(row.get("changeability_question") in CHANGEABILITY_QUESTIONS,
                      "finding-changeability-question",
                      "changeability_question must be one of "
                      + ", ".join(sorted(CHANGEABILITY_QUESTIONS)),
                      f"{location}.changeability_question")
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


_TERMINAL_FINDING_DISPOSITIONS = {
    "consumed", "evidence-backed-no-finding", "partial", "failed",
}


def _validate_rekey_resolution(obj: Any) -> list[Failure]:
    """Finite terminal outcomes for findings that cannot be mechanically re-keyed."""
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "rekey-resolution output must be an object")
        return failures.rows
    rows = obj.get("dispositions")
    if not failures.require(isinstance(rows, list) and bool(rows), "rekey-dispositions-shape",
                            "dispositions must be a non-empty list", "dispositions"):
        return failures.rows
    seen: set[str] = set()
    for index, row in enumerate(rows):
        location = f"dispositions[{index}]"
        if not isinstance(row, dict):
            failures.add("rekey-disposition-shape", "disposition must be an object", location)
            continue
        finding_id = row.get("finding_id")
        failures.require(_one_line_str(finding_id), "rekey-finding-id",
                         "finding_id must be one non-empty line", f"{location}.finding_id")
        if isinstance(finding_id, str):
            failures.require(finding_id not in seen, "rekey-finding-id-unique",
                             f"duplicate finding_id: {finding_id}", f"{location}.finding_id")
            seen.add(finding_id)
        disposition = row.get("disposition")
        failures.require(disposition in _TERMINAL_FINDING_DISPOSITIONS,
                         "rekey-terminal-disposition",
                         "disposition must be one of " + ", ".join(sorted(_TERMINAL_FINDING_DISPOSITIONS)),
                         f"{location}.disposition")
        module_ids = row.get("module_ids")
        if failures.require(_string_list(module_ids), "rekey-module-ids",
                            "module_ids must be a string list", f"{location}.module_ids"):
            if disposition == "consumed":
                failures.require(bool(module_ids), "rekey-consumed-module",
                                 "consumed finding needs at least one finalized module", f"{location}.module_ids")
            elif module_ids:
                failures.add("rekey-terminal-module-ids",
                             "only consumed finding may list module_ids", f"{location}.module_ids")
        failures.require(_one_line_str(row.get("reason_code")), "rekey-reason-code",
                         "reason_code must be one non-empty line", f"{location}.reason_code")
        refs = row.get("evidence_refs")
        if failures.require(_string_list(refs, allow_empty=False), "rekey-evidence-refs",
                            "evidence_refs must be a non-empty string list", f"{location}.evidence_refs"):
            if any(citation_grammar_kind(ref) is None for ref in refs):
                failures.add("rekey-evidence-ref-grammar", "evidence_refs contain an invalid citation",
                             f"{location}.evidence_refs")
        impact = row.get("coverage_impact", "")
        if disposition in {"partial", "failed"}:
            failures.require(_one_line_str(impact), "rekey-coverage-impact",
                             "partial/failed finding needs a Coverage impact", f"{location}.coverage_impact")
        elif impact and not _one_line_str(impact):
            failures.add("rekey-coverage-impact", "coverage_impact must be one non-empty line",
                         f"{location}.coverage_impact")
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
    # quoted_text carries TWO states (57B-116): empty ("") is a REQUEST -- the
    # location is named but not yet fetched (planner.py's source_reads select
    # tasks always emit this state; a later, separate fetch step fills it
    # in) -- non-empty is FETCHED, the quoted text itself. Both are valid
    # here; only a non-string is rejected.
    failures.require(isinstance(row.get("quoted_text"), str),
                      "selection-quoted-text",
                      "quoted_text must be a string (\"\" for a REQUEST not yet "
                      "fetched, the quoted text itself once FETCHED)",
                      f"{location}.quoted_text")


def _validate_selection_fetch(obj: Any) -> list[Failure]:
    failures = _Failures()
    if not isinstance(obj, dict):
        failures.add("output-shape", "selection-fetch output must be an object")
        return failures.rows
    selections = obj.get("selections")
    if not failures.require(isinstance(selections, list), "selections-shape",
                             "selections must be a list", "selections"):
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


def _crosscheck_selection_requirements(obj: Any,
                                       packet_inputs: Mapping[str, str]) -> list[Failure]:
    """Ensure source requests answer every typed role in the packet.

    A role may truthfully be unavailable, not applicable, or unresolved, but
    that is a declared coverage condition rather than permission to omit the
    role or substitute a keyword-similar source.
    """
    contract, parse_failures = _load_requirement_object(
        packet_inputs.get("selection-requirements.json"), filename="selection-requirements")
    if contract is None:
        # Legacy/synthetic packets retain the historical non-empty selection
        # contract. New planned packets always have explicit roles below.
        if packet_inputs.get("selection-requirements.json") is None \
                and isinstance(obj, dict) and not obj.get("selections"):
            return [{"check": "selections-shape", "detail": "selections must be non-empty without requirements",
                     "location": "selections"}]
        return parse_failures
    failures = _Failures()
    expected_ids = _unique_requirement_ids(contract.get("roles"), "role_id",
                                           filename="selection-requirements.roles", failures=failures)
    packet_evidence_ids = set(packet_inputs) - {
        "requirements.json", "selection-requirements.json", "sharding",
    }
    for role in contract.get("roles", []):
        if not isinstance(role, dict):
            continue
        evidence_ids = role.get("evidence_input_ids")
        inventory_paths = role.get("inventory_paths")
        if not _string_list(evidence_ids, allow_empty=False) \
                or not set(evidence_ids).issubset(packet_evidence_ids):
            failures.add("selection-role-typed-evidence",
                         "role evidence_input_ids must name packet-backed typed inputs",
                         f"selection-requirements.roles[{role.get('role_id', '')}]")
        if not _string_list(inventory_paths):
            failures.add("selection-role-inventory-paths",
                         "role inventory_paths must be a string list",
                         f"selection-requirements.roles[{role.get('role_id', '')}]")
    rows = obj.get("role_dispositions") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        failures.add("role-dispositions-shape", "role_dispositions must be a list", "role_dispositions")
        return failures.rows
    selections = obj.get("selections", []) if isinstance(obj, dict) else []
    selection_refs = {row.get("selection_id"): row.get("ref") for row in selections
                      if isinstance(row, dict) and isinstance(row.get("selection_id"), str)}
    found: dict[str, dict] = {}
    linked_selection_ids: set[str] = set()
    for index, row in enumerate(rows):
        location = f"role_dispositions[{index}]"
        if not isinstance(row, dict):
            failures.add("role-disposition-shape", "role disposition must be an object", location)
            continue
        role_id = row.get("role_id")
        if not _one_line_str(role_id):
            failures.add("role-disposition-id", "role_id must be one non-empty line", location)
            continue
        if role_id in found:
            failures.add("role-disposition-duplicate", f"duplicate role_id: {role_id}", location)
            continue
        found[role_id] = row
        status = row.get("status")
        if status not in _ROLE_DISPOSITION_STATUSES:
            failures.add("role-disposition-status",
                         "status must be selected, unavailable, not-applicable, or unresolved", location)
        ids = row.get("selection_ids")
        if not _string_list(ids):
            failures.add("role-disposition-selection-ids", "selection_ids must be a string list",
                         f"{location}.selection_ids")
            ids = []
        if not _one_line_str(row.get("note")):
            failures.add("role-disposition-note", "note must be one non-empty line", f"{location}.note")
        if isinstance(ids, list):
            unknown = set(ids) - set(selection_refs)
            if unknown:
                failures.add("role-disposition-selection-ids",
                             f"selection_ids do not exist: {sorted(unknown)}",
                             f"{location}.selection_ids")
            if status == "selected":
                if not ids:
                    failures.add("role-disposition-selected-evidence",
                                 "selected role requires at least one selection_id", location)
                for selection_id in ids:
                    if citation_grammar_kind(selection_refs.get(selection_id, "")) != "source":
                        failures.add("role-disposition-source-ref",
                                     "selected role must be backed by a source-ref selection", location)
            elif ids:
                failures.add("role-disposition-empty-selection-ids",
                             "unavailable/not-applicable/unresolved roles cannot claim selection_ids", location)
            linked_selection_ids.update(ids)
    if set(found) != set(expected_ids):
        failures.add("role-disposition-exact-accounting",
                     f"missing={sorted(set(expected_ids) - set(found))}; "
                     f"unknown={sorted(set(found) - set(expected_ids))}", "role_dispositions")
    if set(selection_refs) != linked_selection_ids:
        failures.add("selection-role-selection-accounting",
                     f"unlinked={sorted(set(selection_refs) - linked_selection_ids)}", "selections")
    return failures.rows


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

_VALIDATORS: dict[str, Callable[[Any], list[Failure]]] = {
    "lens-findings": _validate_lens_findings,
    "formation-proposal": _validate_formation_proposal,
    "boundary-resolution": _validate_boundary_resolution,
    "rekey-resolution": _validate_rekey_resolution,
    "dedup-rank": _validate_dedup_rank,
    "section-generate": _validate_section_generate,
    "repair-edit-ops": _validate_repair_edit_ops,
    "coherence-check": _validate_coherence_check,
    "selection-fetch": _validate_selection_fetch,
}

assert set(_VALIDATORS) == TASK_TYPES  # every task type has exactly one schema


# --------------------------------------------------------------------------- #
# packet cross-checks
#
# A schema check can only prove an output is SELF-consistent. Some outputs
# also have to be consistent with the PACKET THEY ANSWER, and an executor
# that quietly narrows the question would otherwise pass: dedup-rank echoes
# the id universe it was given, and its merge_map is checked against that
# echo -- so an executor that drops ids from BOTH stays internally
# consistent and validates cleanly, silently deciding those findings out of
# existence. (Observed on a live acceptance run, 57B-116: five findings went
# missing this way and were only caught downstream by the assembler.)
# Cross-checks close that gap at the gate; they are skipped when the caller
# has no packet to check against, so a bare schema call is unchanged.
# --------------------------------------------------------------------------- #

_INPUT_DISPOSITION_STATUSES = {"examined", "unavailable", "failed", "not-applicable"}
_CHECKLIST_OUTCOMES = {
    "finding", "positive-evidence", "no-concern-observed", "unknown", "not-applicable",
}
_ROLE_DISPOSITION_STATUSES = {"selected", "unavailable", "not-applicable", "unresolved"}


def _load_requirement_object(raw: str | None, *, filename: str) -> tuple[dict[str, Any] | None, list[Failure]]:
    if raw is None:
        return None, []
    try:
        value = json.loads(raw)
    except ValueError:
        return None, [{"check": f"{filename}-json", "location": filename,
                       "detail": f"{filename} is not valid JSON"}]
    if not isinstance(value, dict):
        return None, [{"check": f"{filename}-shape", "location": filename,
                       "detail": f"{filename} must be an object"}]
    return value, []


def _unique_requirement_ids(rows: Any, field: str, *, filename: str,
                            failures: _Failures) -> list[str]:
    if not isinstance(rows, list):
        failures.add(f"{filename}-shape", f"{filename} must be a list", filename)
        return []
    values = [row.get(field) for row in rows if isinstance(row, dict)]
    if len(values) != len(rows) or not all(_one_line_str(value) for value in values) \
            or len(set(values)) != len(values):
        failures.add(f"{filename}-ids", f"{filename} needs unique non-empty {field} values", filename)
        return []
    return values


def _crosscheck_lens_requirements(obj: Any, packet_inputs: Mapping[str, str]) -> list[Failure]:
    """Exact accounting for the input, checklist and coverage contract.

    These checks run while an output is still retryable.  A successful empty
    finding list is therefore meaningful only when every requested input and
    checklist dimension carries evidence for its scoped negative conclusion.
    """
    contract, parse_failures = _load_requirement_object(
        packet_inputs.get("requirements.json"), filename="requirements")
    if contract is None:
        return parse_failures
    failures = _Failures()
    input_ids = _unique_requirement_ids(contract.get("input_requirements"), "input_id",
                                        filename="requirements.input_requirements", failures=failures)
    dimension_ids = _unique_requirement_ids(contract.get("checklist_requirements"), "dimension_id",
                                            filename="requirements.checklist_requirements", failures=failures)
    coverage_ids = _unique_requirement_ids(contract.get("coverage_requirements"), "coverage_id",
                                           filename="requirements.coverage_requirements", failures=failures)
    packet_evidence_ids = set(packet_inputs) - {
        "requirements.json", "selection-requirements.json", "sharding",
    }
    if set(input_ids) != packet_evidence_ids:
        failures.add("requirements-packet-input-accounting",
                     f"missing={sorted(packet_evidence_ids - set(input_ids))[:8]}; "
                     f"unknown={sorted(set(input_ids) - packet_evidence_ids)[:8]}",
                     "requirements.input_requirements")
    role_ids = _unique_requirement_ids(contract.get("selection_role_requirements"), "role_id",
                                       filename="requirements.selection_role_requirements",
                                       failures=failures)
    for row in contract.get("selection_role_requirements", []):
        if not isinstance(row, dict):
            continue
        evidence_ids = row.get("evidence_input_ids")
        inventory_paths = row.get("inventory_paths")
        if not _string_list(evidence_ids, allow_empty=False) \
                or not set(evidence_ids).issubset(packet_evidence_ids):
            failures.add("selection-role-typed-evidence",
                         "every source-selection role needs packet-backed typed evidence_input_ids",
                         f"requirements.selection_role_requirements[{row.get('role_id', '')}]")
        if not _string_list(inventory_paths):
            failures.add("selection-role-inventory-paths",
                         "inventory_paths must be a string list",
                         f"requirements.selection_role_requirements[{row.get('role_id', '')}]")
    expected_coverage_ids = {
        input_id for input_id in input_ids if input_id.startswith("signals/")
    } | {f"source-selection/{role_id}" for role_id in role_ids}
    if set(coverage_ids) != expected_coverage_ids:
        failures.add("requirements-coverage-accounting",
                     f"missing={sorted(expected_coverage_ids - set(coverage_ids))[:8]}; "
                     f"unknown={sorted(set(coverage_ids) - expected_coverage_ids)[:8]}",
                     "requirements.coverage_requirements")

    def collect_rows(key: str, id_key: str, expected: list[str], statuses: set[str]) -> dict[str, dict]:
        rows = obj.get(key) if isinstance(obj, dict) else None
        if not isinstance(rows, list):
            failures.add(f"{key}-shape", f"{key} must be a list", key)
            return {}
        found: dict[str, dict] = {}
        status_key = "status" if key == "input_dispositions" else "outcome"
        for index, row in enumerate(rows):
            location = f"{key}[{index}]"
            if not isinstance(row, dict):
                failures.add(f"{key}-row", "disposition must be an object", location)
                continue
            item_id = row.get(id_key)
            if not _one_line_str(item_id):
                failures.add(f"{key}-id", f"{id_key} must be one non-empty line", location)
                continue
            if item_id in found:
                failures.add(f"{key}-duplicate", f"duplicate {id_key}: {item_id}", location)
                continue
            found[item_id] = row
            if row.get(status_key) not in statuses:
                failures.add(f"{key}-{status_key}",
                             f"{status_key} must be one of {', '.join(sorted(statuses))}", location)
            refs = row.get("evidence_refs")
            if not _string_list(refs):
                failures.add(f"{key}-evidence-refs", "evidence_refs must be a string list",
                             f"{location}.evidence_refs")
            elif any(citation_grammar_kind(ref) is None for ref in refs):
                failures.add(f"{key}-evidence-ref-grammar", "evidence_refs contain an invalid citation",
                             f"{location}.evidence_refs")
            text_key = "note" if key == "input_dispositions" else "limitation"
            if not _one_line_str(row.get(text_key)):
                failures.add(f"{key}-{text_key}", f"{text_key} must be one non-empty line",
                             f"{location}.{text_key}")
        if set(found) != set(expected):
            failures.add(f"{key}-exact-accounting",
                         f"missing={sorted(set(expected) - set(found))[:8]}; "
                         f"unknown={sorted(set(found) - set(expected))[:8]}", key)
        return found

    inputs = collect_rows("input_dispositions", "input_id", input_ids,
                          _INPUT_DISPOSITION_STATUSES)
    checklist = collect_rows("checklist_dispositions", "dimension_id", dimension_ids,
                             _CHECKLIST_OUTCOMES)
    for input_id, row in inputs.items():
        status = row.get("status")
        refs = row.get("evidence_refs")
        if status in {"examined", "not-applicable"} and not refs:
            failures.add("input-disposition-evidence",
                         "examined/not-applicable requires evidence_refs",
                         f"input_dispositions[{input_id}]")
        if status in {"unavailable", "failed"} and refs:
            failures.add("input-disposition-failure-refs",
                         "unavailable/failed must disclose the failure in note, not cite absent evidence",
                         f"input_dispositions[{input_id}]")

    finding_ids = {row.get("finding_id") for row in obj.get("findings", [])
                   if isinstance(row, dict) and isinstance(row.get("finding_id"), str)} \
        if isinstance(obj, dict) else set()
    referenced_findings: set[str] = set()
    for dimension_id, row in checklist.items():
        ids = row.get("finding_ids")
        if not _string_list(ids):
            failures.add("checklist-disposition-finding-ids", "finding_ids must be a string list",
                         f"checklist_dispositions[{dimension_id}].finding_ids")
            continue
        outcome = row.get("outcome")
        refs = row.get("evidence_refs")
        if outcome == "finding":
            if not ids or not refs:
                failures.add("checklist-disposition-finding-evidence",
                             "finding outcome needs finding_ids and evidence_refs",
                             f"checklist_dispositions[{dimension_id}]")
            referenced_findings.update(ids)
        elif ids:
            failures.add("checklist-disposition-nonfinding-ids",
                         "only finding outcome may name finding_ids",
                         f"checklist_dispositions[{dimension_id}].finding_ids")
        if outcome in {"positive-evidence", "no-concern-observed", "not-applicable"} and not refs:
            failures.add("checklist-disposition-negative-evidence",
                         "positive/no-concern/not-applicable outcome requires evidence_refs",
                         f"checklist_dispositions[{dimension_id}]")
    if finding_ids != referenced_findings:
        failures.add("checklist-finding-lineage",
                     f"findings={sorted(finding_ids)}; referenced={sorted(referenced_findings)}",
                     "checklist_dispositions")

    coverage = obj.get("coverage") if isinstance(obj, dict) else None
    coverage_by_id: dict[str, dict] = {}
    if isinstance(coverage, list):
        for index, row in enumerate(coverage):
            if not isinstance(row, dict) or not _one_line_str(row.get("signal")):
                continue
            coverage_id = row["signal"]
            if coverage_id in coverage_by_id:
                failures.add("coverage-duplicate", f"duplicate coverage id: {coverage_id}",
                             f"coverage[{index}]")
            coverage_by_id[coverage_id] = row
        if set(coverage_by_id) != set(coverage_ids):
            failures.add("coverage-exact-accounting",
                         f"missing={sorted(set(coverage_ids) - set(coverage_by_id))[:8]}; "
                         f"unknown={sorted(set(coverage_by_id) - set(coverage_ids))[:8]}",
                         "coverage")

    raw_role_results = packet_inputs.get("selection-role-results.json")
    role_results, role_parse_failures = _load_requirement_object(
        raw_role_results, filename="selection-role-results")
    for failure in role_parse_failures:
        failures.add(failure["check"], failure["detail"], failure["location"])
    if role_results is not None:
        rows = role_results.get("roles")
        role_ids = _unique_requirement_ids(
            contract.get("selection_role_requirements"), "role_id",
            filename="requirements.selection_role_requirements", failures=failures)
        result_ids = _unique_requirement_ids(rows, "role_id",
                                             filename="selection-role-results.roles", failures=failures)
        if set(role_ids) != set(result_ids):
            failures.add("selection-role-results-exact-accounting",
                         f"missing={sorted(set(role_ids) - set(result_ids))}; "
                         f"unknown={sorted(set(result_ids) - set(role_ids))}",
                         "selection-role-results.roles")
        for row in rows if isinstance(rows, list) else []:
            role_id = row.get("role_id") if isinstance(row, dict) else ""
            source_coverage = coverage_by_id.get(f"source-selection/{role_id}")
            status = row.get("coverage_status") if isinstance(row, dict) else None
            if status not in SIGNAL_STATUSES:
                failures.add("selection-role-coverage-status",
                             "selection role result needs a valid coverage_status", role_id)
            elif source_coverage is not None and source_coverage.get("status") != status:
                failures.add("selection-role-coverage-projection",
                             "source-selection coverage must exactly project the deterministic role result",
                             f"source-selection/{role_id}")
            if isinstance(row, dict) and row.get("fetch_status") in {"partial", "failed"}:
                fetched = inputs.get("fetched-evidence.json")
                if fetched is not None and fetched.get("status") == "examined":
                    failures.add("fetched-evidence-coverage-gap",
                                 "failed/partial source fetch cannot be recorded as fully examined",
                                 "input_dispositions[fetched-evidence.json]")
    return failures.rows


def _crosscheck_formation_partitions(obj: Any,
                                     packet_inputs: Mapping[str, str]) -> list[Failure]:
    """Formation work items must account for their assigned candidate slice."""
    raw_context = packet_inputs.get("formation-partition-context.json")
    if raw_context is None:
        return []
    failures = _Failures()
    try:
        context = json.loads(raw_context)
        candidates = json.loads(packet_inputs.get("module-candidates.json", "[]"))
    except ValueError:
        return [{"check": "formation-partition-context-json", "detail":
                 "formation partition context and candidate universe must be valid JSON",
                 "location": "formation-partition-context.json"}]
    if not isinstance(context, dict) or not isinstance(candidates, list):
        return [{"check": "formation-partition-context-shape", "detail":
                 "formation partition context must be an object and candidates a list",
                 "location": "formation-partition-context.json"}]
    candidate_ids = {row.get("candidate_id") for row in candidates if isinstance(row, dict)}
    partition = context.get("partition")
    merge_order = context.get("merge_order")
    global_identity = context.get("global_identity")
    partition_id = partition.get("partition_id") if isinstance(partition, dict) else None
    if not isinstance(partition, dict) or not _one_line_str(partition_id) \
            or not _string_list(partition.get("candidate_ids"), allow_empty=False):
        failures.add("formation-partition-context-partition",
                     "context needs one partition with non-empty id and candidate_ids",
                     "formation-partition-context.json.partition")
        assigned_ids: set[str] = set()
    else:
        assigned_ids = set(partition["candidate_ids"])
        if not candidate_ids <= assigned_ids:
            failures.add("formation-partition-context-ownership",
                         "packet candidates must belong to its assigned partition",
                         "module-candidates.json")
    if not isinstance(global_identity, dict) or not isinstance(merge_order, list) \
            or partition_id not in merge_order:
        failures.add("formation-partition-context-global",
                     "context needs global identity and a merge order containing this partition",
                     "formation-partition-context.json")

    rows = obj.get("candidate_dispositions") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        failures.add("formation-disposition-accounting", "candidate_dispositions must be a list",
                     "candidate_dispositions")
    else:
        additions = obj.get("additional_candidates", []) if isinstance(obj, dict) else []
        added_ids = {row.get("candidate_id") for row in additions if isinstance(row, dict)} \
            if isinstance(additions, list) else set()
        output_ids = [row.get("candidate_id") for row in rows if isinstance(row, dict)]
        if len(output_ids) != len(rows) or len(set(output_ids)) != len(output_ids) \
                or set(output_ids) != candidate_ids | added_ids:
            failures.add("formation-disposition-accounting",
                         "candidate_dispositions must account for every packet and added candidate exactly once",
                         "candidate_dispositions")
    if "candidate_rules" in obj:
        failures.add("formation-explicit-dispositions",
                     "partitioned formation packets must use explicit candidate_dispositions",
                     "candidate_rules")
    return failures.rows


def _crosscheck_boundary_resolution(obj: Any,
                                    packet_inputs: Mapping[str, str]) -> list[Failure]:
    """Targeted boundary resolution must consume every contextual remainder."""
    raw = packet_inputs.get("unresolved-candidates.json")
    if raw is None:
        return []
    try:
        expected_rows = json.loads(raw)
    except ValueError:
        return [{"check": "boundary-context-json", "detail":
                 "unresolved-candidates.json is not valid JSON", "location": "unresolved-candidates.json"}]
    if not isinstance(expected_rows, list):
        return [{"check": "boundary-context-shape", "detail":
                 "unresolved-candidates.json must be a list", "location": "unresolved-candidates.json"}]
    expected_ids = {row.get("candidate_id") for row in expected_rows if isinstance(row, dict)}
    expected_by_id = {row.get("candidate_id"): row for row in expected_rows if isinstance(row, dict)}
    failures = _Failures()
    rows = obj.get("dispositions") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        failures.add("boundary-disposition-accounting", "dispositions must be a list", "dispositions")
        return failures.rows
    actual_ids = [row.get("candidate_id") for row in rows if isinstance(row, dict)]
    if len(actual_ids) != len(rows) or len(set(actual_ids)) != len(actual_ids) \
            or set(actual_ids) != expected_ids:
        failures.add("boundary-disposition-accounting",
                     "dispositions must account for every unresolved candidate exactly once",
                     "dispositions")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        refs = row.get("evidence_refs")
        if not _string_list(refs, allow_empty=False):
            failures.add("boundary-evidence-refs", "resolution needs non-empty evidence_refs",
                         f"dispositions[{index}].evidence_refs")
        elif any(citation_grammar_kind(ref) is None for ref in refs):
            failures.add("boundary-evidence-ref-grammar", "resolution has an invalid evidence ref",
                         f"dispositions[{index}].evidence_refs")
        if row.get("disposition") == "unresolved":
            expected_refs = expected_by_id.get(row.get("candidate_id"), {}).get("evidence_refs", [])
            if not isinstance(refs, list) or not set(refs) & set(expected_refs):
                failures.add("boundary-evidence-provenance",
                             "retained unresolved candidate must cite its supplied exact evidence",
                             f"dispositions[{index}].evidence_refs")
            coverage_impact = row.get("coverage_impact")
            if not _one_line_str(coverage_impact):
                failures.add("boundary-coverage-impact",
                             "retained unresolved candidate needs a non-empty Coverage impact",
                             f"dispositions[{index}].coverage_impact")
    modules = obj.get("modules") if isinstance(obj, dict) else None
    if modules is not None:
        if not isinstance(modules, list):
            failures.add("boundary-modules-shape", "modules must be a list", "modules")
        else:
            seen: set[str] = set()
            for index, row in enumerate(modules):
                _validate_module_row(row, f"modules[{index}]", failures)
                module_id = row.get("module_id") if isinstance(row, dict) else None
                if isinstance(module_id, str):
                    if module_id in seen:
                        failures.add("boundary-module-id-unique", f"duplicate module_id: {module_id}",
                                     f"modules[{index}].module_id")
                    seen.add(module_id)
    return failures.rows


def _crosscheck_rekey_resolution(obj: Any,
                                 packet_inputs: Mapping[str, str]) -> list[Failure]:
    """A repair packet must disposition exactly its supplied rekey tail."""
    raw = packet_inputs.get("rekey-tail.json")
    if raw is None:
        return []
    try:
        tail = json.loads(raw)
    except ValueError:
        return [{"check": "rekey-tail-json", "detail": "rekey-tail.json is not valid JSON",
                 "location": "rekey-tail.json"}]
    if not isinstance(tail, list):
        return [{"check": "rekey-tail-shape", "detail": "rekey-tail.json must be a list",
                 "location": "rekey-tail.json"}]
    expected = {row.get("finding_id") for row in tail if isinstance(row, dict)}
    rows = obj.get("dispositions") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        return []
    actual = [row.get("finding_id") for row in rows if isinstance(row, dict)]
    failures = _Failures()
    if len(actual) != len(rows) or len(set(actual)) != len(actual) or set(actual) != expected:
        failures.add("rekey-tail-exact-accounting",
                     "dispositions must account for every supplied tail finding exactly once",
                     "dispositions")
    tail_by_id = {row.get("finding_id"): row for row in tail if isinstance(row, dict)}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        refs = row.get("evidence_refs", [])
        source_refs = {
            ref for evidence in tail_by_id.get(row.get("finding_id"), {}).get("evidence", [])
            if isinstance(evidence, dict) for ref in evidence.get("refs", [])
        }
        if source_refs and not set(refs) & source_refs:
            failures.add("rekey-evidence-provenance",
                         "terminal disposition must retain exact evidence from the tail finding",
                         f"dispositions[{index}].evidence_refs")
    return failures.rows

def _crosscheck_dedup_rank(obj: Any, packet_inputs: Mapping[str, str]) -> list[Failure]:
    raw = packet_inputs.get("input-finding-ids.json")
    if raw is None:
        return []
    try:
        expected = set(json.loads(raw))
    except ValueError:
        return []  # a malformed packet input is not this output's failure
    echoed = obj.get("input_finding_ids") if isinstance(obj, dict) else None
    if not isinstance(echoed, list):
        return []  # the schema check already reported the missing/bad field
    failures: list[Failure] = []
    missing = sorted(expected - set(echoed))
    if missing:
        failures.append({
            "check": "dedup-input-coverage",
            "detail": ("input_finding_ids omits ids present in the packet -- every "
                       f"finding must be accounted for: {missing[:20]}"),
            "location": "input_finding_ids"})
    unknown = sorted(set(echoed) - expected)
    if unknown:
        failures.append({
            "check": "dedup-input-coverage",
            "detail": f"input_finding_ids invents ids absent from the packet: {unknown[:20]}",
            "location": "input_finding_ids"})
    return failures


# The same honest-inapplicability markers ``reports.document_floors`` accepts
# in place of real substance -- kept here, in sync, rather than re-derived,
# so a section cannot pass one check and fail the other on the same wording.
HONEST_INAPPLICABILITY_MARKERS = (
    "not applicable", "no evidence", "unavailable", "did not run", "unknown",
    "not found in the analyzed")


def _crosscheck_section_generate(obj: Any, packet_inputs: Mapping[str, str]) -> list[Failure]:
    """A section's completeness FLOOR, enforced at submit time.

    Before this existed, a section could pass its (self-consistency-only)
    schema check while sitting below the floor ``report-floors`` checks
    later -- and once a task is ``validated`` the ledger's digest-keyed
    generations mean it is never silently revised, so the gap surfaced only
    at document assembly, with no path back to the section short of a new
    generation. Checking the floor HERE closes it at the place a thin
    submission can still be retried through the engine's own attempt/retry
    path, the same as any other schema failure (57B-117 M3 acceptance:
    overview.s5 validated at 117 words against its 140-word floor and was
    only caught by ``report-floors``, by which point it was un-revisable).
    """
    raw = packet_inputs.get("floor.json")
    if raw is None:
        return []
    try:
        floor = json.loads(raw)
        min_words = int(floor["min_words"])
    except (ValueError, KeyError, TypeError):
        return []  # a malformed packet input is not this output's failure
    content = obj.get("content_md") if isinstance(obj, dict) else None
    if not isinstance(content, str):
        return []  # the schema check already reported this
    if len(content.split()) >= min_words:
        return []
    lowered = content.lower()
    if any(marker in lowered for marker in HONEST_INAPPLICABILITY_MARKERS):
        return []  # short is fine when it is an honest inapplicability line
    return [{
        "check": "floor-section-thin", "location": "content_md",
        "detail": (f"{len(content.split())} words is below this section's floor of "
                   f"{min_words}, and it does not state an honest inapplicability "
                   "line (not applicable / no evidence / unavailable / did not run / "
                   "unknown / not found in the analyzed)")}]


_CROSSCHECKS: dict[str, Callable[[Any, Mapping[str, str]], list[Failure]]] = {
    "lens-findings": _crosscheck_lens_requirements,
    "selection-fetch": _crosscheck_selection_requirements,
    "formation-proposal": _crosscheck_formation_partitions,
    "boundary-resolution": _crosscheck_boundary_resolution,
    "rekey-resolution": _crosscheck_rekey_resolution,
    "dedup-rank": _crosscheck_dedup_rank,
    "section-generate": _crosscheck_section_generate,
}


def validate_output(task_type: str, obj: Any, *,
                    packet_inputs: Mapping[str, str] | None = None) -> list[Failure]:
    """Structurally validate one task's output against its schema.

    Returns a list of ``{"check", "detail", "location"}`` failures (empty =
    valid). An unknown ``task_type`` is itself reported as a failure rather
    than raising, so a caller validating a batch of heterogeneous results
    never has to special-case this function with a try/except.

    ``packet_inputs`` (name -> content, as the answered packet carried them)
    additionally runs any cross-check registered for this task type — see
    the note above :func:`_crosscheck_dedup_rank` for why self-consistency
    alone is not enough. Omitting it keeps the pure-schema behaviour, so a
    caller holding only an output (a fixture, a conformance golden) is
    unaffected.
    """
    if task_type not in _VALIDATORS:
        return [{"check": "task-type", "detail": f"unknown task_type: {task_type!r}",
                 "location": "task_type"}]
    failures = _VALIDATORS[task_type](obj)
    crosscheck = _CROSSCHECKS.get(task_type)
    if crosscheck is not None and packet_inputs is not None:
        failures = failures + crosscheck(obj, packet_inputs)
    return failures
