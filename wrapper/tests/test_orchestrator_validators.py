"""Evidence/prose validator tests (57B-114 M0)."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper import identity, overview_audit
from analysis_wrapper.orchestrator import validators
from analysis_wrapper.targetspec import TargetSpec, stable_repo_id


def _build_run(tmp_path) -> Path:
    """A minimal, domain-neutral run directory with REAL repo files on disk
    (validate_citations reads actual file content), no real git required."""
    workspace = tmp_path / "ws"
    api_root = workspace / "api"
    web_root = workspace / "web"
    (api_root / "internal").mkdir(parents=True)
    (api_root / "internal" / "service.go").write_text(
        "package internal\nfunc Work() {}\nfunc Another() {}\n", "utf-8")
    web_root.mkdir(parents=True)

    head = "a" * 40
    targets = {
        "schema_version": "3.0.0",
        "repos": [
            {"repo_id": "api-11111111", "path": str(api_root),
             "git": {"head": head, "branch": "main", "commit_count": 1}},
            {"repo_id": "web-22222222", "path": str(web_root),
             "git": {"head": "", "branch": "", "commit_count": 0}},
        ],
    }
    run = tmp_path / "run"
    run.mkdir()
    (run / "targets.json").write_text(json.dumps(targets), "utf-8")
    spec = TargetSpec.from_dict(targets)
    project_id = stable_repo_id(str(workspace))
    mapping = identity.build(spec, workspace_root=workspace, project_id=project_id)
    identity.write_mapping(run, mapping)
    (run / "discovery-report.json").write_text(
        json.dumps({"project_ref": mapping.project.reference}), "utf-8")

    signals = run / "signals"
    signals.mkdir()
    (signals / "x.view.txt").write_text("first line\nsecond line has needle\n", "utf-8")
    (signals / "run-summary.json").write_text(json.dumps({
        "schema_version": "3.0.0", "aggregate_status": "complete",
        "signals": [{"tool": "structure", "repository_ref": "api", "status": "complete",
                    "reason": "", "view": "x.view.txt", "manifest": "x.manifest.json"}],
    }), "utf-8")
    (run / "workspace-metrics.json").write_text(json.dumps({
        "schema_version": "3.0.0",
        "metrics": [{"metric_ref": "code.analyzed-scope.total", "value": 100}],
    }), "utf-8")
    return run


def _checks(failures):
    return {row["check"] for row in failures}


# --------------------------------------------------------------------------- #
# validate_citations
# --------------------------------------------------------------------------- #

def test_validate_citations_accepts_every_grammar_kind(tmp_path):
    run = _build_run(tmp_path)
    head = "a" * 40
    refs = [
        f"api@{head}:internal/service.go:2",
        {"ref": "signals/x.view.txt:2", "quote": "needle"},
        "metric:code.analyzed-scope.total",
    ]
    assert validators.validate_citations(refs, run) == []


def test_validate_citations_rejects_unrecognized_grammar(tmp_path):
    run = _build_run(tmp_path)
    failures = validators.validate_citations(["not a citation at all"], run)
    assert _checks(failures) == {"citation-grammar"}


def test_validate_citations_rejects_revision_mismatch(tmp_path):
    run = _build_run(tmp_path)
    ref = f"api@{'b' * 40}:internal/service.go:2"
    failures = validators.validate_citations([ref], run)
    assert "citation-revision-mismatch" in _checks(failures)


def test_validate_citations_accepts_non_git_revision_marker(tmp_path):
    run = _build_run(tmp_path)
    # web-22222222 has an empty git.head -> NON-GIT is the expected marker.
    # web/ has no files, so the citation still fails on file-missing, not on
    # revision — this isolates the revision check specifically.
    failures = validators.validate_citations(["web@NON-GIT:missing.ts:1"], run)
    assert _checks(failures) == {"citation-file-missing"}


def test_validate_citations_rejects_missing_file_and_out_of_range_line(tmp_path):
    run = _build_run(tmp_path)
    head = "a" * 40
    failures = validators.validate_citations([f"api@{head}:internal/nope.go:1"], run)
    assert "citation-file-missing" in _checks(failures)
    failures = validators.validate_citations([f"api@{head}:internal/service.go:999"], run)
    assert "citation-line-range" in _checks(failures)


def test_validate_citations_rejects_unsafe_relative_path(tmp_path):
    run = _build_run(tmp_path)
    head = "a" * 40
    failures = validators.validate_citations([f"api@{head}:../../etc/passwd:1"], run)
    assert "citation-path-unsafe" in _checks(failures)


def test_validate_citations_rejects_quote_not_present_at_line(tmp_path):
    run = _build_run(tmp_path)
    head = "a" * 40
    failures = validators.validate_citations(
        [{"ref": f"api@{head}:internal/service.go:2", "quote": "not on this line"}], run)
    assert "citation-quote" in _checks(failures)


def test_validate_citations_accepts_quote_present_at_line(tmp_path):
    run = _build_run(tmp_path)
    head = "a" * 40
    failures = validators.validate_citations(
        [{"ref": f"api@{head}:internal/service.go:2", "quote": "func Work"}], run)
    assert failures == []


def test_validate_citations_rejects_signal_ref_not_indexed(tmp_path):
    run = _build_run(tmp_path)
    (run / "signals" / "raw").mkdir()
    (run / "signals" / "raw" / "secret.out").write_text("token=x\n", "utf-8")
    failures = validators.validate_citations(["signals/raw/secret.out:1"], run)
    assert "citation-signal-not-indexed" in _checks(failures)


def test_validate_citations_rejects_unknown_metric_and_quote_on_metric(tmp_path):
    run = _build_run(tmp_path)
    failures = validators.validate_citations(["metric:not-recorded"], run)
    assert "citation-metric-unknown" in _checks(failures)
    failures = validators.validate_citations(
        [{"ref": "metric:code.analyzed-scope.total", "quote": "100"}], run)
    assert "citation-quote-unsupported" in _checks(failures)


# --------------------------------------------------------------------------- #
# numeric_provenance
# --------------------------------------------------------------------------- #

def test_numeric_provenance_allows_cited_numbers_and_flags_uncited_ones():
    prose = "The workspace has 51.2% coverage across 100 files."
    failures = validators.numeric_provenance(prose, allowed_numbers=["51.2%", 100])
    assert failures == []
    failures = validators.numeric_provenance(prose, allowed_numbers=[100])
    assert len(failures) == 1
    assert "51.2%" in failures[0]["detail"]


def test_numeric_provenance_flags_both_sides_of_a_range_independently():
    prose = "Complexity ranges 12-34 across modules."
    failures = validators.numeric_provenance(prose, allowed_numbers=[12])
    assert len(failures) == 1
    assert "34" in failures[0]["detail"]


def test_numeric_provenance_respects_allowlist_patterns():
    prose = "See overview.md § 3 for details."
    failures = validators.numeric_provenance(
        prose, allowed_numbers=[], allowlist_patterns=[r"§\s*\d+"])
    assert failures == []


def test_numeric_provenance_normalizes_comma_grouping():
    prose = "There are 1,234 lines of code."
    assert validators.numeric_provenance(prose, allowed_numbers=[1234]) == []
    assert validators.numeric_provenance(prose, allowed_numbers=["1,234"]) == []


# --------------------------------------------------------------------------- #
# forbidden_vocabulary
# --------------------------------------------------------------------------- #

def test_forbidden_vocabulary_default_patterns_catch_wellness_and_composite_score():
    assert validators.forbidden_vocabulary("This module looks healthy overall.")
    assert not validators.forbidden_vocabulary("This dependency is unhealthy code smell free.")
    assert validators.forbidden_vocabulary("Score: 42/100 for this module.")
    assert validators.forbidden_vocabulary("该模块很健康。")
    assert not validators.forbidden_vocabulary(
        "部署状态: 健康检查 已配置。")


def test_forbidden_vocabulary_accepts_custom_patterns():
    failures = validators.forbidden_vocabulary(
        "Static analysis shows production traffic hitting this handler.",
        patterns=validators.STATIC_BASIS_OVERREACH_VOCABULARY)
    assert failures
    assert all(row["check"] == "forbidden-vocabulary" for row in failures)


# --------------------------------------------------------------------------- #
# relocation_invariant
# --------------------------------------------------------------------------- #

def test_relocation_invariant_passes_when_removed_content_moved_to_companion():
    before = "Intro paragraph.\n\nThe API has three known limitations documented here."
    after = "Intro paragraph.\n\nSee the companion document for detail."
    companion = "Detail section.\n\nThe API has three known limitations documented here."
    assert validators.relocation_invariant(before, after, companion) == []


def test_relocation_invariant_flags_content_dropped_without_a_trace():
    before = "Intro paragraph.\n\nThe API has three known limitations documented here."
    after = "Intro paragraph.\n\nSee the companion document for detail."
    companion = "Detail section with unrelated content only."
    failures = validators.relocation_invariant(before, after, companion)
    assert len(failures) == 1
    assert failures[0]["check"] == "relocation-invariant"


def test_relocation_invariant_treats_table_rows_as_one_block():
    before = "| a | b |\n| c | d |\n"
    after = "| c | d |\n"
    companion_ok = "| a | b |\n"
    companion_bad = "unrelated text\n"
    assert validators.relocation_invariant(before, after, companion_ok) == []
    assert validators.relocation_invariant(before, after, companion_bad) != []


# --------------------------------------------------------------------------- #
# reading_budget_report
# --------------------------------------------------------------------------- #

def _floors_spec():
    return {
        "required_headings": ["## 1. Analysis basis", "## 2. Executive diagnosis"],
        "machine_markers": [("<!-- BEGIN X -->", "<!-- END X -->")],
    }


def test_reading_budget_report_always_carries_both_ceiling_and_floors():
    overview = (
        "## 1. Analysis basis\nRun date and scope.\n\n"
        "## 2. Executive diagnosis\nThe system is a small API.\n\n"
        "<!-- BEGIN X -->\ncontent\n<!-- END X -->\n"
    )
    report = validators.reading_budget_report(overview, _floors_spec())
    assert set(report) == {"reading_minutes", "reading_ceiling_minutes",
                           "ceiling_exceeded", "floors", "failures"}
    assert report["failures"] == []
    assert report["ceiling_exceeded"] is False
    assert report["floors"]["missing_headings"] == []
    assert report["floors"]["empty_headings"] == []
    assert report["floors"]["marker_problems"] == []


def test_reading_budget_report_flags_missing_and_empty_headings():
    overview = "## 1. Analysis basis\n\n## 2. Executive diagnosis\n"
    report = validators.reading_budget_report(overview, _floors_spec())
    assert "## 1. Analysis basis" in report["floors"]["empty_headings"]
    assert any(row["check"] == "floor-heading-empty" for row in report["failures"])

    missing = validators.reading_budget_report(
        "## 1. Analysis basis\nSome text.\n", _floors_spec())
    assert "## 2. Executive diagnosis" in missing["floors"]["missing_headings"]
    assert any(row["check"] == "floor-heading-missing" for row in missing["failures"])


def test_reading_budget_report_flags_marker_integrity_problems():
    spec = _floors_spec()
    duplicated = "<!-- BEGIN X -->\na\n<!-- END X -->\n<!-- BEGIN X -->\nb\n<!-- END X -->\n"
    report = validators.reading_budget_report(duplicated, spec)
    assert report["floors"]["marker_problems"]
    empty_block = "<!-- BEGIN X -->\n<!-- END X -->\n"
    report = validators.reading_budget_report(empty_block, spec)
    assert report["floors"]["marker_problems"]


def test_reading_ceiling_stays_lockstep_with_overview_audits_pm_reading_budget():
    """validators.READING_CEILING_MINUTES duplicates a literal that lives
    inline in overview_audit.py's "pm-reading-budget" check (that module
    exposes no named constant for it). Extract the literal from its ACTUAL
    source rather than hand-copying the number a second time, so a future
    change to the audit's budget can't silently diverge from this module."""
    source = Path(overview_audit.__file__).read_text("utf-8")
    match = re.search(r"minutes <= (\d+(?:\.\d+)?)", source)
    assert match, "overview_audit.py's pm-reading-budget comparison literal was not found"
    assert float(match.group(1)) == validators.READING_CEILING_MINUTES


def test_reading_budget_report_flags_exceeded_ceiling():
    long_overview = "word " * 3000
    report = validators.reading_budget_report(long_overview, {"required_headings": [],
                                                              "machine_markers": []})
    assert report["ceiling_exceeded"] is True
    assert any(row["check"] == "reading-ceiling" for row in report["failures"])


# --------------------------------------------------------------------------- #
# apply_edit_ops
# --------------------------------------------------------------------------- #

def test_apply_edit_ops_applies_a_mapped_unique_edit():
    text = "The module has no tests."
    ops = [{"locate": "no tests", "replace": "a small test suite", "fixes": "check-a"}]
    new_text, failures = validators.apply_edit_ops(text, ops, failed_check_ids=["check-a"])
    assert new_text == "The module has a small test suite."
    assert failures == []


def test_apply_edit_ops_rejects_unmapped_fix():
    text = "The module has no tests."
    ops = [{"locate": "no tests", "replace": "tests", "fixes": "check-z"}]
    new_text, failures = validators.apply_edit_ops(text, ops, failed_check_ids=["check-a"])
    assert new_text == text
    assert _checks(failures) == {"edit-op-unmapped"}


def test_apply_edit_ops_rejects_non_unique_locate():
    text = "dup dup"
    ops = [{"locate": "dup", "replace": "x", "fixes": "check-a"}]
    new_text, failures = validators.apply_edit_ops(text, ops, failed_check_ids=["check-a"])
    assert new_text == text
    assert _checks(failures) == {"edit-op-locate"}


def test_apply_edit_ops_rejects_a_locate_with_no_match():
    text = "hello world"
    ops = [{"locate": "missing phrase", "replace": "x", "fixes": "check-a"}]
    new_text, failures = validators.apply_edit_ops(text, ops, failed_check_ids=["check-a"])
    assert new_text == text
    assert _checks(failures) == {"edit-op-locate"}


def test_apply_edit_ops_enforces_default_diff_guard():
    text = "a short sentence stays here"
    huge_replacement = " ".join(["word"] * 100)
    ops = [{"locate": "a short sentence stays here", "replace": huge_replacement,
           "fixes": "check-a"}]
    new_text, failures = validators.apply_edit_ops(text, ops, failed_check_ids=["check-a"])
    assert new_text == text
    assert _checks(failures) == {"edit-op-diff-guard"}


def test_apply_edit_ops_requires_relocation_bypasses_diff_guard():
    text = "a short sentence stays here"
    huge_replacement = " ".join(["word"] * 100)
    ops = [{"locate": "a short sentence stays here", "replace": huge_replacement,
           "fixes": "check-a"}]
    new_text, failures = validators.apply_edit_ops(
        text, ops, failed_check_ids=["check-a"],
        policy={"check-a": {"requires_relocation": True}})
    assert new_text == huge_replacement
    assert failures == []


def test_apply_edit_ops_applies_edits_in_order_and_isolates_a_rejected_op():
    text = "alpha beta gamma"
    ops = [
        {"locate": "alpha", "replace": "ALPHA", "fixes": "check-a"},
        {"locate": "beta", "replace": "BETA", "fixes": "check-unmapped"},
        {"locate": "gamma", "replace": "GAMMA", "fixes": "check-a"},
    ]
    new_text, failures = validators.apply_edit_ops(text, ops, failed_check_ids=["check-a"])
    assert new_text == "ALPHA beta GAMMA"
    assert _checks(failures) == {"edit-op-unmapped"}
