"""Delivered-language completeness gate (57B-111).

A first run in a target language must deliver ALL reading-facing report
content in that language, natively -- no silent per-key fallback to English.
Post-hoc translation is a separate future feature and must never be how a
run's primary language is delivered. These tests cover:

  1. ``locale.missing_keys``/``is_delivered`` -- catalog completeness.
  2. Refusing to start a run in a non-delivered language.
  3. The overview audit failing on English leakage (fallback / missing
     disclaimer), and NOT false-failing on verbatim-cited source tokens
     (code identifiers, citations, file paths, quoted UI labels) in an
     otherwise well-formed zh-CN run.
"""

from __future__ import annotations

import json

import pytest

from analysis_wrapper import (coverage_render, locale, module_render,
                              overview_audit, run_provenance, synthesis_input)
from analysis_wrapper.system_model import assemble as sm
from analysis_wrapper.targetspec import TargetSpec
from system_model_fixtures import _HA, write_run
from test_overview_contracts import _complete_findings, _complete_map, _prepared


# --------------------------------------------------------------------------- #
# 1. locale.py: missing_keys / is_delivered / delivered_languages
# --------------------------------------------------------------------------- #

def test_complete_catalog_is_delivered():
    assert locale.missing_keys("en") == []
    assert locale.missing_keys("zh-CN") == []
    assert locale.is_delivered("en")
    assert locale.is_delivered("zh-CN")
    assert "en" in locale.delivered_languages()
    assert "zh-CN" in locale.delivered_languages()


def test_incomplete_registered_catalog_is_not_delivered_and_lists_the_gap():
    locale.register_locale("xx-gate-test", {"chrome.nav.index": "XX-INDEX"})
    try:
        missing = locale.missing_keys("xx-gate-test")
        assert "findings.top" in missing
        assert "chrome.nav.index" not in missing
        assert not locale.is_delivered("xx-gate-test")
        assert "xx-gate-test" not in locale.delivered_languages()
    finally:
        del locale._registry["xx-gate-test"]  # test isolation only


def test_unregistered_language_is_not_delivered_and_missing_everything():
    assert not locale.is_delivered("qq-never-registered")
    assert locale.missing_keys("qq-never-registered") == sorted(
        locale.BUNDLED_LOCALES["en"])


# --------------------------------------------------------------------------- #
# 2. Refusing a non-delivered run language
# --------------------------------------------------------------------------- #

def test_require_delivered_language_passes_through_delivered_languages():
    assert run_provenance.require_delivered_language("en") == "en"
    assert run_provenance.require_delivered_language("zh-CN") == "zh-CN"


def test_starting_a_run_in_a_non_delivered_language_is_refused(tmp_path):
    spec = TargetSpec([])
    with pytest.raises(ValueError) as excinfo:
        run_provenance.create_document(
            spec, analyzer_root=tmp_path, language="fr-FR")
    message = str(excinfo.value)
    assert "fr-FR" in message
    assert "en" in message and "zh-CN" in message  # names the delivered set
    assert "not a delivered language" in message


def test_incomplete_registered_locale_is_refused_at_run_creation(tmp_path):
    locale.register_locale("xx-gate-test", {"chrome.nav.index": "XX-INDEX"})
    try:
        spec = TargetSpec([])
        with pytest.raises(ValueError, match="xx-gate-test"):
            run_provenance.create_document(
                spec, analyzer_root=tmp_path, language="xx-gate-test")
    finally:
        del locale._registry["xx-gate-test"]


# --------------------------------------------------------------------------- #
# 3. Overview audit: language leakage gate
# --------------------------------------------------------------------------- #

def _write_provenance(run, language):
    run_provenance.write(run, {
        "schema_version": run_provenance.SCHEMA_VERSION,
        "analyzed_at": "2026-01-01T00:00:00+00:00",
        "analyzer": {"package": "analysis-wrapper"},
        "targets": [],
        "generation": {"language": language, "model": "unknown", "effort": "unknown"},
        "preparation": None,
        "tool_versions": [],
    })


def _well_formed_zh_run(tmp_path, *, disclaimer=True, extra_prose=""):
    """Build a fully-formed overview run whose reports are rendered in
    zh-CN, including the machine blocks the audit byte-compares, plus
    verbatim-cited English tokens of every intended-exception kind: a code
    identifier, a citation (repo@commit:path:line), a file path, and a
    quoted verbatim UI label -- none of which should ever be flagged as
    leakage.
    """
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    sm.dump(sm.assemble(run), run)
    module_render.write(run)
    synthesis_input.write(run)
    technical_findings, pm_findings = _complete_findings(run)

    disclaimer_zh = (
        "> 本报告完全基于**代码仓库证据**：代码、配置与已分析快照中的 Git 历史；"
        "不代表运行时观测。\n\n" if disclaimer else ""
    )

    (run / "project-map.md").write_text(
        "# 系统地图\n\n" + disclaimer_zh + module_render.render(run), "utf-8")

    # overview.md (PM report) -- no source citations/paths/tool identifiers
    # allowed here (pre-existing pm-abstraction-boundary rule); a quoted
    # verbatim UI label is fine and must not be flagged as leaked prose.
    overview_body = (
        "# 总览\n\n" + disclaimer_zh
        + "产品界面中的按钮标签为“This report is for internal reference only”，"
          "该文本保持产品原文，不做翻译。\n\n"
        + extra_prose
        + pm_findings
    )
    (run / "overview.md").write_text(overview_body, "utf-8")

    citation = f"api@{_HA}:internal/service.go:2"
    technical_body = (
        "# 技术总览\n\n" + disclaimer_zh
        + f"函数 `getUserById` 处理该请求，参见 {citation}，"
          f"实现位于 `internal/service.go`。更多信息见 https://example.com/docs 。\n\n"
        + coverage_render.render(run) + technical_findings
    )
    (run / "technical-overview.md").write_text(technical_body, "utf-8")

    _write_provenance(run, "zh-CN")
    return run


def test_audit_passes_for_a_well_formed_zh_cn_run_with_verbatim_tokens(tmp_path):
    """The most important test: verbatim-cited English tokens (code
    identifiers, citations, file paths, quoted UI labels) in an otherwise
    fully-translated zh-CN run must NOT trigger a false failure.
    """
    run = _well_formed_zh_run(tmp_path)
    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    failed = [row for row in result["checks"] if row["status"] == "fail"]
    assert result["status"] == "passed", failed
    codes = {row["check"] for row in result["checks"]}
    assert "language-catalog-completeness" in codes
    assert "language-standing-disclaimer" in codes
    assert "language-stray-english-prose" in codes
    prose_row = next(row for row in result["checks"]
                     if row["check"] == "language-stray-english-prose")
    assert prose_row["status"] == "pass", prose_row["detail"]


def test_audit_fails_when_the_disclaimer_is_missing_in_a_zh_cn_run(tmp_path):
    run = _well_formed_zh_run(tmp_path, disclaimer=False)
    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    assert result["status"] == "failed"
    row = next(r for r in result["checks"] if r["check"] == "language-standing-disclaimer")
    assert row["status"] == "fail"
    assert "overview.md" in row["detail"] or "technical-overview.md" in row["detail"] \
        or "project-map.md" in row["detail"]


def test_audit_fails_on_catalog_fallback_leakage_in_a_zh_cn_run(tmp_path, monkeypatch):
    """Simulate a regression where the zh-CN catalog loses a key (e.g. a
    partial re-registration in a future process) -- the audit must fail on
    the precise catalog signal, not rely on scanning text.
    """
    run = _well_formed_zh_run(tmp_path)
    incomplete_zh = {k: v for k, v in locale._registry["zh-CN"].items()
                     if k != "chrome.nav.index"}
    monkeypatch.setitem(locale._registry, "zh-CN", incomplete_zh)
    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    assert result["status"] == "failed"
    row = next(r for r in result["checks"] if r["check"] == "language-catalog-completeness")
    assert row["status"] == "fail"
    assert "chrome.nav.index" in row["detail"]


def test_audit_warns_but_does_not_fail_on_real_stray_english_prose(tmp_path):
    """(c) is implemented as a non-blocking WARNING (see module docstring in
    overview_audit.py): a false failure on a good run is worse than a missed
    heuristic. This test proves the heuristic DOES catch genuine leaked
    English prose, while the overall audit still passes (warn != fail).
    """
    leaked_prose = (
        "This section was not translated into the target language today "
        "and should be rewritten before the report ships to a reader.\n\n"
    )
    run = _well_formed_zh_run(tmp_path, extra_prose=leaked_prose)
    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    row = next(r for r in result["checks"] if r["check"] == "language-stray-english-prose")
    assert row["status"] == "warn", row["detail"]
    assert "overview.md" in row["detail"]
    # A warning must never fail the run.
    assert result["status"] == "passed"


def test_audit_is_unaffected_for_an_en_run(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    sm.dump(sm.assemble(run), run)
    module_render.write(run)
    synthesis_input.write(run)
    technical_findings, pm_findings = _complete_findings(run)
    (run / "project-map.md").write_text("# Map\n\n" + module_render.render(run), "utf-8")
    (run / "overview.md").write_text("# Overview\n\n" + pm_findings, "utf-8")
    (run / "technical-overview.md").write_text(
        "# Technical\n\n" + coverage_render.render(run) + technical_findings, "utf-8")
    _write_provenance(run, "en")

    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    assert result["status"] == "passed"
    codes = {row["check"] for row in result["checks"]}
    assert "language-catalog-completeness" not in codes
    assert "language-standing-disclaimer" not in codes
    assert "language-stray-english-prose" not in codes


def test_audit_fails_closed_when_run_provenance_is_present_but_unreadable(tmp_path):
    """A present-but-unparseable/schema-mismatched run-provenance.json must
    FAIL the audit rather than silently default to "en" and skip every
    language check (57B-111 review fix). A future SCHEMA_VERSION bump must
    not be able to disable this gate for every prior run.
    """
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    sm.dump(sm.assemble(run), run)
    module_render.write(run)
    synthesis_input.write(run)
    technical_findings, pm_findings = _complete_findings(run)
    (run / "project-map.md").write_text("# Map\n\n" + module_render.render(run), "utf-8")
    (run / "overview.md").write_text("# Overview\n\n" + pm_findings, "utf-8")
    (run / "technical-overview.md").write_text(
        "# Technical\n\n" + coverage_render.render(run) + technical_findings, "utf-8")
    (run / run_provenance.FILENAME).write_text(
        json.dumps({"schema_version": 999, "generation": {"language": "en"}}), "utf-8")

    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    assert result["status"] == "failed"
    row = next(r for r in result["checks"] if r["check"] == "run-provenance-readable")
    assert row["status"] == "fail"
    assert "unreadable" in row["detail"]


def test_audit_is_inert_when_run_provenance_is_absent(tmp_path):
    """Pre-57B-111 fixtures/runs have no run-provenance.json; the new gate
    must default to "en" behavior (inert) rather than raising.
    """
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    sm.dump(sm.assemble(run), run)
    module_render.write(run)
    synthesis_input.write(run)
    technical_findings, pm_findings = _complete_findings(run)
    (run / "project-map.md").write_text("# Map\n\n" + module_render.render(run), "utf-8")
    (run / "overview.md").write_text("# Overview\n\n" + pm_findings, "utf-8")
    (run / "technical-overview.md").write_text(
        "# Technical\n\n" + coverage_render.render(run) + technical_findings, "utf-8")
    assert not (run / run_provenance.FILENAME).exists()

    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    assert result["status"] == "passed"
