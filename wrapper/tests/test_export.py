"""Tests for the pluggable export framework (57B-45).

Reuses the domain-neutral synthetic run builder from the report tests.
"""

import pytest

from analysis_wrapper import export as export_pkg
from analysis_wrapper.export.base import ExporterUnavailable
from analysis_wrapper.report_html.generate import generate
from test_report_html import make_run


def test_registry_lists_html():
    assert "html" in export_pkg.available_formats()


def test_get_unknown_format_raises():
    with pytest.raises(ValueError):
        export_pkg.get_exporter("pdf")


def test_project_name_strips_trailing_hash():
    assert export_pkg.project_name("WCP-1cc51f1d") == "WCP"
    assert export_pkg.project_name("my-service-deadbeef") == "my-service"
    assert export_pkg.project_name("no-hash") == "no-hash"      # short segment kept
    assert export_pkg.project_name("") == "project"


def test_export_output_dir_layout(tmp_path):
    d = export_pkg.export_output_dir(tmp_path, "WCP-1cc51f1d", "html")
    assert d == tmp_path / "exported" / "WCP-analysis" / "html"


def test_export_writes_to_exported_location(tmp_path):
    run = make_run(tmp_path)
    skill_root = tmp_path / "skill"
    result = export_pkg.export(run, "html", skill_root=skill_root)
    # DEMO-1 has no hash suffix, so the name is preserved verbatim.
    assert result.out_dir == skill_root / "exported" / "DEMO-1-analysis" / "html"
    assert (result.out_dir / "index.html").is_file()
    assert result.format == "html"


def test_export_defaults_to_html(tmp_path):
    run = make_run(tmp_path)
    result = export_pkg.export(run, skill_root=tmp_path / "skill")  # no fmt given
    assert result.format == "html"


def test_export_unknown_format_raises(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(ValueError):
        export_pkg.export(run, "pdf", skill_root=tmp_path / "skill")


def test_export_unavailable_fails_closed(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    exporter = export_pkg.get_exporter("html")
    monkeypatch.setattr(
        exporter, "check_available",
        lambda: (False, "markdown-it-py not installed"),
    )
    with pytest.raises(ExporterUnavailable):
        export_pkg.export(run, "html", skill_root=tmp_path / "skill")


def test_export_needs_out_dir_or_skill_root(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(ValueError):
        export_pkg.export(run, "html")  # neither out_dir nor skill_root


def test_export_html_matches_direct_generate(tmp_path):
    """Regression: the export path reproduces the direct generator, byte-for-byte."""
    run = make_run(tmp_path)
    exported = export_pkg.export(run, "html", out_dir=tmp_path / "a").out_dir
    generated = generate(run, out_dir=tmp_path / "b").report_dir
    files = sorted(p.relative_to(exported) for p in exported.rglob("*") if p.is_file())
    assert files == sorted(
        p.relative_to(generated) for p in generated.rglob("*") if p.is_file()
    )
    for rel in files:
        assert (exported / rel).read_bytes() == (generated / rel).read_bytes(), rel
