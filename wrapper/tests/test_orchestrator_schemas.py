"""Per-task-type output SHAPE tests (57B-114 M0) — pure structural checks,
no run directory involved (that is validators.py's job)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator import contracts as C
from analysis_wrapper.orchestrator import schemas


def _checks(failures):
    return {row["check"] for row in failures}


def test_every_task_type_has_exactly_one_schema():
    assert set(schemas._VALIDATORS) == C.TASK_TYPES


def test_validate_output_reports_unknown_task_type():
    failures = schemas.validate_output("not-a-real-task-type", {})
    assert len(failures) == 1 and failures[0]["check"] == "task-type"


def test_citation_grammar_kind_recognizes_all_three_forms():
    assert schemas.citation_grammar_kind("api@" + "a" * 40 + ":internal/x.go:12") == "source"
    assert schemas.citation_grammar_kind("web@WORKTREE:src/index.ts:3") == "source"
    assert schemas.citation_grammar_kind("signals/structure-api.view.txt:5") == "signal"
    assert schemas.citation_grammar_kind("metric:code.analyzed-scope.total") == "metric"
    assert schemas.citation_grammar_kind("not a citation") is None
    assert schemas.citation_grammar_kind("") is None


# Drift-lock corpus: (ref, expected_kind_or_None). Every entry below exercises
# one of the three findings.py divergences a review caught (long-form metric
# prefix; source refs whose repo contains "@" or whose path contains a colon
# or a space; signal refs whose view name contains whitespace) plus a set of
# ordinary valid/invalid refs. The test below asserts schemas.py's verdict
# against findings.py's OWN parsing functions for every entry, so agreement
# is proven fresh each run rather than merely re-asserted by a second
# independent implementation that could quietly drift again later.
_CITATION_GRAMMAR_CORPUS: list[tuple[str, str | None]] = [
    # metric — both the short and the long (source-of-truth) forms.
    ("metric:code.analyzed-scope.total", "metric"),
    ("workspace-metrics.json#metric:code.analyzed-scope.total", "metric"),
    ("metric:", None),
    ("notmetric:code.x", None),
    # signal — a view path may contain "/" and even whitespace (only ":" is
    # excluded); missing/non-digit line numbers are rejected.
    ("signals/x.view.txt:5", "signal"),
    ("signals/imports/x.view.txt:2", "signal"),
    ("signals/my view.txt:1", "signal"),
    ("signals/x.view.txt", None),
    ("signals/x.view.txt:abc", None),
    # a "signals/"-prefixed string COMMITS to signal grammar and is never
    # retried as a source ref, even though it happens to parse as one.
    ("signals/foo@bar:baz:5", None),
    # source — repo may contain "@" (only the LAST "@" splits it off); the
    # path may contain colons and spaces (only the LAST ":" before a
    # digits-only tail splits path from line).
    ("api@" + "a" * 40 + ":internal/my file.go:5", "source"),
    ("api@REV:internal/weird:name.go:9", "source"),
    ("weird@repo@REV:path/to/file.go:3", "source"),
    ("api@WORKTREE:src/index.ts:10", "source"),
    ("api@NON-GIT:src/index.ts:1", "source"),
    ("api@REV:path/to/file.go", None),           # no line number
    ("api@REV:path/to/file.go:abc", None),       # non-digit line
    ("api@:path/to/file.go:5", None),            # empty revision
    ("path/to/file.go:5", None),                 # no "@" at all
    ("@REV:path/to/file.go:5", None),            # empty repository_ref
]


def test_citation_grammar_matches_findings_py_exactly_on_a_tricky_corpus():
    # Test-only: production code in this package never imports findings.py.
    from analysis_wrapper import findings as findings_module

    for ref, expected in _CITATION_GRAMMAR_CORPUS:
        assert schemas.citation_grammar_kind(ref) == expected, ref

        metric_match = findings_module._METRIC.fullmatch(ref)
        if metric_match:
            findings_kind = "metric"
        elif ref.startswith("signals/"):
            findings_kind = "signal" if findings_module._SIGNAL.fullmatch(ref) else None
        else:
            findings_kind = "source" if findings_module._source_parts(ref) else None

        assert findings_kind == expected, ref
        assert schemas.citation_grammar_kind(ref) == findings_kind, ref


# --------------------------------------------------------------------------- #
# lens-findings
# --------------------------------------------------------------------------- #

def _valid_finding(**overrides):
    row = {
        "finding_id": "finding-sample-boundary",
        "claim": "The sample boundary has observable change friction.",
        "lens": "structure-inventory",
        "affected_modules": ["mc-abc123"],
        "evidence": [
            {"fact": "The bounded structure signal contains one observed item.",
             "refs": ["signals/x.view.txt:1"], "basis": "static-reference"},
        ],
        "evidence_basis": ["static-reference"],
        "impact": "A change crosses the observed boundary.",
        "priority": "medium", "confidence": "medium",
        "limitations": "Static evidence does not establish runtime frequency.",
        "suggested_direction": "Clarify the boundary before changing it.",
        "changeability_question": "boundary-clarity",
    }
    row.update(overrides)
    return row


def _lens_output(**overrides):
    doc = {"findings": [_valid_finding()],
          "coverage": [{"signal": "structure", "status": "complete", "note": ""}]}
    doc.update(overrides)
    return doc


def test_lens_findings_accepts_a_valid_document():
    assert schemas.validate_output("lens-findings", _lens_output()) == []


def test_lens_findings_rejects_bad_finding_id_and_unsupported_basis():
    finding = _valid_finding(finding_id="not-a-finding-id")
    failures = schemas.validate_output("lens-findings", _lens_output(findings=[finding]))
    assert "finding-id" in _checks(failures)

    finding = _valid_finding(evidence=[{"fact": "x", "refs": ["metric:m"],
                                       "basis": "runtime-observation"}],
                             evidence_basis=["runtime-observation"])
    failures = schemas.validate_output("lens-findings", _lens_output(findings=[finding]))
    assert "evidence-basis" in _checks(failures)


def test_lens_findings_rejects_duplicate_finding_ids():
    findings = [_valid_finding(), _valid_finding()]
    failures = schemas.validate_output("lens-findings", _lens_output(findings=findings))
    assert "finding-id-unique" in _checks(failures)


def test_lens_findings_high_confidence_requires_two_evidence_rows():
    finding = _valid_finding(confidence="high")
    failures = schemas.validate_output("lens-findings", _lens_output(findings=[finding]))
    assert "finding-confidence-high-evidence" in _checks(failures)


def test_lens_findings_rejects_unrecognized_ref_grammar():
    finding = _valid_finding(evidence=[
        {"fact": "x", "refs": ["not-a-citation"], "basis": "static-reference"}])
    failures = schemas.validate_output("lens-findings", _lens_output(findings=[finding]))
    assert "evidence-ref-grammar" in _checks(failures)


def test_lens_findings_rejects_bad_coverage_status():
    failures = schemas.validate_output(
        "lens-findings",
        _lens_output(coverage=[{"signal": "structure", "status": "unknown", "note": ""}]))
    assert "coverage-status" in _checks(failures)


def test_lens_findings_accepts_every_changeability_question_value():
    for value in schemas.CHANGEABILITY_QUESTIONS:
        finding = _valid_finding(changeability_question=value)
        assert schemas.validate_output("lens-findings", _lens_output(findings=[finding])) == []


def test_lens_findings_rejects_missing_or_unknown_changeability_question():
    finding = dict(_valid_finding())
    del finding["changeability_question"]
    failures = schemas.validate_output("lens-findings", _lens_output(findings=[finding]))
    assert "finding-changeability-question" in _checks(failures)

    finding = _valid_finding(changeability_question="not-a-real-question")
    failures = schemas.validate_output("lens-findings", _lens_output(findings=[finding]))
    assert "finding-changeability-question" in _checks(failures)


# --------------------------------------------------------------------------- #
# formation-proposal / boundary-resolution
# --------------------------------------------------------------------------- #

def test_formation_proposal_accepts_a_minimal_valid_document():
    doc = {"modules": [{"module_id": "sample-capability", "name": "Sample capability",
                       "classification": "business", "confidence": "medium", "aliases": []}]}
    assert schemas.validate_output("formation-proposal", doc) == []


def test_formation_proposal_rejects_bad_module_row_fields():
    doc = {"modules": [
        {"module_id": "Not_A_Slug", "name": "", "classification": "nonsense",
         "confidence": "extreme", "aliases": "not-a-list"},
    ]}
    failures = _checks(schemas.validate_output("formation-proposal", doc))
    assert {"module-id", "module-name", "module-classification",
           "module-confidence", "module-aliases"} <= failures


def test_formation_proposal_rejects_duplicate_module_ids():
    module = {"module_id": "sample-capability", "name": "Sample capability",
             "classification": "business", "confidence": "medium", "aliases": []}
    doc = {"modules": [module, dict(module)]}
    assert "module-id-unique" in _checks(schemas.validate_output("formation-proposal", doc))


def test_formation_proposal_validates_candidate_rules_and_dispositions():
    doc = {
        "modules": [{"module_id": "sample-capability", "name": "Sample",
                    "classification": "business", "confidence": "high", "aliases": []}],
        "candidate_rules": [{
            "rule_id": "all-candidates", "selectors": [{"candidate_ids": ["mc-1"]}],
            "disposition": "merged", "module_ids": ["sample-capability", "extra-module"],
            "reason": "evidence-backed",
        }],
        "candidate_dispositions": [{
            "candidate_id": "mc-2", "disposition": "excluded",
            "module_ids": ["sample-capability"], "reason": "proven non-integration",
        }],
    }
    failures = _checks(schemas.validate_output("formation-proposal", doc))
    assert "rule-module-ids" not in failures  # module_ids is a valid string list
    assert "disposition-arity" in failures  # "merged" with 2 module_ids, "excluded" with 1


def test_formation_proposal_rejects_remaining_rule_with_selectors_or_module():
    doc = {
        "modules": [{"module_id": "sample-capability", "name": "Sample",
                    "classification": "business", "confidence": "high", "aliases": []}],
        "candidate_rules": [{
            "rule_id": "leftover", "remaining": True, "selectors": [{"values": ["x"]}],
            "disposition": "unresolved", "module_ids": [], "reason": "unknown",
        }],
    }
    assert "rule-remaining-selectors" in _checks(schemas.validate_output("formation-proposal", doc))


def test_boundary_resolution_accepts_valid_dispositions_and_rejects_duplicates():
    doc = {"dispositions": [
        {"candidate_id": "mc-1", "disposition": "standalone", "module_ids": ["mod-a"],
         "reason": "clear boundary"},
    ]}
    assert schemas.validate_output("boundary-resolution", doc) == []
    doc["dispositions"].append(dict(doc["dispositions"][0]))
    assert "disposition-candidate-unique" in _checks(
        schemas.validate_output("boundary-resolution", doc))


# --------------------------------------------------------------------------- #
# dedup-rank
# --------------------------------------------------------------------------- #

def _dedup_output(**overrides):
    doc = {
        "input_finding_ids": ["finding-a", "finding-b"],
        "merge_map": {
            "finding-a": {"status": "surviving", "absorbed_into": None, "reason": "primary"},
            "finding-b": {"status": "absorbed", "absorbed_into": "finding-a",
                         "reason": "same root cause"},
        },
        "rank": [{"finding_id": "finding-a", "reason": "highest blast radius"}],
    }
    doc.update(overrides)
    return doc


def test_dedup_rank_accepts_a_valid_document():
    assert schemas.validate_output("dedup-rank", _dedup_output()) == []


def test_dedup_rank_rejects_incomplete_merge_map():
    doc = _dedup_output()
    del doc["merge_map"]["finding-b"]
    failures = _checks(schemas.validate_output("dedup-rank", doc))
    assert "merge-map-completeness" in failures


def test_dedup_rank_rejects_absorbed_into_non_surviving_target():
    doc = _dedup_output()
    doc["merge_map"]["finding-b"]["absorbed_into"] = "finding-a"
    doc["merge_map"]["finding-a"]["status"] = "absorbed"
    doc["merge_map"]["finding-a"]["absorbed_into"] = "finding-b"
    failures = _checks(schemas.validate_output("dedup-rank", doc))
    assert "merge-map-absorbed-into-surviving" in failures


def test_dedup_rank_rejects_incomplete_or_duplicated_rank():
    doc = _dedup_output()
    doc["rank"] = []
    assert "rank-completeness" in _checks(schemas.validate_output("dedup-rank", doc))
    doc = _dedup_output()
    doc["rank"] = [{"finding_id": "finding-a", "reason": "x"},
                   {"finding_id": "finding-a", "reason": "y"}]
    failures = _checks(schemas.validate_output("dedup-rank", doc))
    assert "rank-unique" in failures


# --------------------------------------------------------------------------- #
# section-generate
# --------------------------------------------------------------------------- #

def test_section_generate_accepts_correct_word_count():
    content = "one two three four"
    doc = {"section_id": "overview-section-2", "content_md": content, "word_count": 4}
    assert schemas.validate_output("section-generate", doc) == []


def test_section_generate_rejects_wrong_word_count():
    doc = {"section_id": "overview-section-2", "content_md": "one two three", "word_count": 99}
    assert "word-count" in _checks(schemas.validate_output("section-generate", doc))


# --------------------------------------------------------------------------- #
# repair-edit-ops / coherence-check
# --------------------------------------------------------------------------- #

def test_repair_edit_ops_accepts_valid_edits_and_rejects_empty_locate():
    doc = {"edits": [{"locate": "old text", "replace": "new text", "fixes": "check-a"}]}
    assert schemas.validate_output("repair-edit-ops", doc) == []
    doc = {"edits": [{"locate": "", "replace": "new text", "fixes": "check-a"}]}
    assert "edit-op-locate" in _checks(schemas.validate_output("repair-edit-ops", doc))


def test_coherence_check_requires_edit_ops_to_match_consistency_flag():
    doc = {"consistent": True, "edit_ops": []}
    assert schemas.validate_output("coherence-check", doc) == []
    doc = {"consistent": True,
          "edit_ops": [{"locate": "a", "replace": "b", "fixes": "check-a"}]}
    assert "coherence-consistent-no-edits" in _checks(
        schemas.validate_output("coherence-check", doc))
    doc = {"consistent": False, "edit_ops": []}
    assert "coherence-inconsistent-needs-edits" in _checks(
        schemas.validate_output("coherence-check", doc))


# --------------------------------------------------------------------------- #
# selection-fetch
# --------------------------------------------------------------------------- #

def test_selection_fetch_accepts_valid_selections_and_rejects_bad_ref():
    doc = {"selections": [{
        "selection_id": "journey-entry-1", "purpose": "user-journey UI label",
        "ref": "web@" + "b" * 40 + ":src/pages/Login.tsx:10",
        "quoted_text": "Sign in",
    }]}
    assert schemas.validate_output("selection-fetch", doc) == []
    doc["selections"][0]["ref"] = "not-a-citation"
    assert "selection-ref" in _checks(schemas.validate_output("selection-fetch", doc))


def test_selection_fetch_accepts_both_quoted_text_states():
    """57B-116: quoted_text carries two valid states -- "" (a REQUEST, not
    yet fetched) and non-empty (FETCHED). Only a non-string is rejected."""
    def _doc(quoted_text):
        return {"selections": [{
            "selection_id": "journey-entry-1", "purpose": "user-journey UI label",
            "ref": "web@" + "b" * 40 + ":src/pages/Login.tsx:10",
            "quoted_text": quoted_text,
        }]}
    assert schemas.validate_output("selection-fetch", _doc("")) == []  # request state
    assert schemas.validate_output("selection-fetch", _doc("Sign in")) == []  # fetched state
    failures = schemas.validate_output("selection-fetch", _doc(None))
    assert "selection-quoted-text" in _checks(failures)
