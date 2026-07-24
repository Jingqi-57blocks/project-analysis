"""Tests for the bundled locale catalog (57B-83 slice 1).

Reuses the domain-neutral synthetic run builder from the report tests (same
convention as test_export.py's ``from test_report_html import make_run``).
"""

from __future__ import annotations

import json
import re

import pytest

from analysis_wrapper import locale
from analysis_wrapper.report_html.generate import generate
from test_report_html import make_run

COMPONENTS_PREFIX = "components."


# --------------------------------------------------------------------------- #
# (a) catalog completeness
# --------------------------------------------------------------------------- #

def test_bundled_locales_share_the_same_key_set():
    en_keys = set(locale.BUNDLED_LOCALES["en"])
    zh_keys = set(locale.BUNDLED_LOCALES["zh-CN"])
    assert en_keys == zh_keys
    assert en_keys  # sanity: catalog is not empty


def test_components_mirror_keys_exist_in_both_locales_with_equal_values():
    """components.* has no human zh-CN translation yet (documented in locale.py):
    those keys must still exist in zh-CN, mirroring en verbatim, so rendering
    stays byte-identical (English in both locales) until translated for real.
    """
    en = locale.BUNDLED_LOCALES["en"]
    zh = locale.BUNDLED_LOCALES["zh-CN"]
    mirror_keys = [key for key in en if key.startswith(COMPONENTS_PREFIX)]
    assert mirror_keys, "expected components.* keys to exist in the catalog"
    for key in mirror_keys:
        assert key in zh, f"{key} missing from zh-CN catalog"
        assert zh[key] == en[key], f"{key} must mirror en (no translation yet)"


def test_non_component_keys_have_a_real_distinct_zh_cn_translation():
    """The five pre-existing bilingual sites already had human translations;
    consolidation must not silently collapse them to English mirrors.
    """
    en = locale.BUNDLED_LOCALES["en"]
    zh = locale.BUNDLED_LOCALES["zh-CN"]
    translated_keys = [key for key in en if not key.startswith(COMPONENTS_PREFIX)]
    assert translated_keys
    for key in translated_keys:
        assert zh[key] != en[key], f"{key} expected a real zh-CN translation"


# --------------------------------------------------------------------------- #
# labels(): per-key fallback semantics
# --------------------------------------------------------------------------- #

def test_labels_en_returns_the_bundled_english_catalog():
    assert locale.labels("en") == locale.BUNDLED_LOCALES["en"]


def test_labels_unknown_language_falls_back_to_english_entirely():
    assert locale.labels("fr-FR") == locale.BUNDLED_LOCALES["en"]


def test_labels_zh_cn_matches_bundled_catalog():
    assert dict(locale.labels("zh-CN")) == locale.BUNDLED_LOCALES["zh-CN"]


# --------------------------------------------------------------------------- #
# (c) synthetic locale: additive registration, zero analysis-module changes
# --------------------------------------------------------------------------- #

def test_register_locale_is_additive_and_falls_back_per_key():
    locale.register_locale("xx-synthetic", {"chrome.nav.index": "XX-INDEX"})
    try:
        cat = locale.labels("xx-synthetic")
        assert cat["chrome.nav.index"] == "XX-INDEX"
        # Every other key is missing from the partial catalog -> falls back to en.
        assert cat["findings.top"] == locale.BUNDLED_LOCALES["en"]["findings.top"]
        assert cat["chrome.nav.coverage"] == locale.BUNDLED_LOCALES["en"]["chrome.nav.coverage"]
        # Bundled locales are untouched by the additive registration.
        assert "xx-synthetic" not in locale.BUNDLED_LOCALES
    finally:
        del locale._registry["xx-synthetic"]  # test isolation only


def test_synthetic_locale_renders_through_the_full_report_pipeline(tmp_path):
    """register_locale() from a test (i.e. from outside analysis_wrapper) is
    enough to make a brand-new language render correctly end to end — no
    analysis-plane module needs to change. This test is the proof.
    """
    locale.register_locale("xx-synthetic", {
        "chrome.nav.index": "XX-INDEX-LABEL",
        "findings.top": "XX-TOP-LABEL",
    })
    try:
        run = make_run(tmp_path, language="xx-synthetic")
        result = generate(run)
        index = (result.report_dir / "index.html").read_text(encoding="utf-8")
        assert 'lang="xx-synthetic"' in index
        assert "XX-INDEX-LABEL" in index
        # Keys absent from the partial synthetic catalog fall back to English
        # (html-escaped, like all chrome labels: esc('Evidence & coverage')).
        assert "Evidence &amp; coverage" in index
    finally:
        del locale._registry["xx-synthetic"]  # test isolation only


# --------------------------------------------------------------------------- #
# (b) en / zh-CN render parity on a synthetic run
# --------------------------------------------------------------------------- #

def _ids(html: str) -> list[str]:
    return re.findall(r'id="([^"]+)"', html)


def test_en_zh_cn_render_parity(tmp_path):
    run_en = make_run(tmp_path / "en", language="en")
    run_zh = make_run(tmp_path / "zh", language="zh-CN")
    result_en = generate(run_en)
    result_zh = generate(run_zh)

    # Same set of generated pages regardless of language.
    assert result_en.pages == result_zh.pages
    assert result_en.section_count == result_zh.section_count
    assert result_en.diagram_count == result_zh.diagram_count

    # The content map records every section's anchor/heading/destinations —
    # entirely derived from source Markdown and structured data, never from
    # chrome strings — so it must be byte-identical across languages.
    cmap_en = (result_en.report_dir / "content-map.json").read_text(encoding="utf-8")
    cmap_zh = (result_zh.report_dir / "content-map.json").read_text(encoding="utf-8")
    assert cmap_en == cmap_zh

    dmanifest_en = (result_en.report_dir / "diagram-manifest.json").read_text(encoding="utf-8")
    dmanifest_zh = (result_zh.report_dir / "diagram-manifest.json").read_text(encoding="utf-8")
    assert dmanifest_en == dmanifest_zh

    index_en = (result_en.report_dir / "index.html").read_text(encoding="utf-8")
    index_zh = (result_zh.report_dir / "index.html").read_text(encoding="utf-8")

    # Identical DOM shape (numbered-heading contract): the exact same anchors,
    # in the same order, on both language renders of the same run.
    assert _ids(index_en) == _ids(index_zh)

    # ... but they are not byte-identical to each other: chrome strings differ.
    assert index_en != index_zh

    # Pin real translated output, not just the lang attribute (previously only
    # `lang="zh-CN"` was asserted anywhere; nothing pinned actual zh-CN text).
    assert 'lang="zh-CN"' in index_zh
    assert "报告总览" in index_zh          # chrome.nav.index (zh-CN)
    assert "Report overview" in index_en   # chrome.nav.index (en)
    assert "报告总览" not in index_en
    assert "Report overview" not in index_zh


def test_en_zh_cn_run_status_banner_uses_catalog_message(tmp_path):
    """generate.py's former hand-inlined bilingual run-status HTML is now
    table-driven through the catalog (landing.inspection_only_message).
    """
    run_en = make_run(tmp_path / "en", language="en")
    run_zh = make_run(tmp_path / "zh", language="zh-CN")
    for run in (run_en, run_zh):
        state_path = run / "run-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["inspection_only"] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")

    index_en = (generate(run_en).report_dir / "index.html").read_text(encoding="utf-8")
    index_zh = (generate(run_zh).report_dir / "index.html").read_text(encoding="utf-8")

    assert 'id="run-status"' in index_en
    assert 'id="run-status"' in index_zh
    assert "Inspection-only:" in index_en
    assert "clean worktree" in index_en
    assert "仅供检查" in index_zh
    assert "此运行不能被接受为 current" in index_zh
