"""Tests for the pluggable export framework (57B-45).

Reuses the domain-neutral synthetic run builder from the report tests.
"""

import json

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


def test_project_name_preserves_exact_reference():
    assert export_pkg.project_name("WCP") == "WCP"
    assert export_pkg.project_name("my-service-deadbeef") == "my-service-deadbeef"
    assert export_pkg.project_name("no-hash") == "no-hash"
    assert export_pkg.project_name("") == "project"


def test_export_output_dir_layout(tmp_path):
    d = export_pkg.export_output_dir(
        tmp_path, "WCP", "low-effort-b15376", "html"
    )
    assert d == (
        tmp_path / "exported" / "WCP-analysis" / "low-effort-b15376" / "html"
    )


@pytest.mark.parametrize(
    "project_ref,run_id", [("../project", "safe-run"), ("project-123abc", "../run")]
)
def test_export_output_dir_rejects_path_traversal(tmp_path, project_ref, run_id):
    with pytest.raises(ValueError, match="invalid .* for export path"):
        export_pkg.export_output_dir(tmp_path, project_ref, run_id, "html")


def test_export_writes_to_exported_location(tmp_path):
    run = make_run(tmp_path)
    skill_root = tmp_path / "skill"
    result = export_pkg.export(run, "html", skill_root=skill_root)
    # DEMO-1 has no hash suffix, so the name is preserved verbatim.
    assert result.out_dir == (
        skill_root / "exported" / "DEMO-1-analysis"
        / "20260101T000000Z-demo" / "html"
    )
    assert (result.out_dir / "index.html").is_file()
    assert result.format == "html"


def test_export_preserves_collision_free_run_namespace(tmp_path):
    source = make_run(tmp_path / "source")
    run = (tmp_path / "skill" / "output" / "client-b%2Fapp" / "overview"
           / "20260101T000000Z-demo")
    run.parent.mkdir(parents=True)
    source.rename(run)

    result = export_pkg.export(run, "html", skill_root=tmp_path / "skill")

    assert result.out_dir == (
        tmp_path / "skill" / "exported" / "client-b%2Fapp-analysis"
        / "20260101T000000Z-demo" / "html"
    )


def test_export_defaults_to_html(tmp_path):
    run = make_run(tmp_path)
    result = export_pkg.export(run, skill_root=tmp_path / "skill")  # no fmt given
    assert result.format == "html"


def test_exports_from_two_runs_do_not_overwrite_each_other(tmp_path):
    run_a = make_run(tmp_path / "a")
    run_b = make_run(tmp_path / "b")
    state_path = run_b / "run-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["run_id"] = "comparison-high-b15376"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    skill_root = tmp_path / "skill"
    result_a = export_pkg.export(run_a, "html", skill_root=skill_root)
    result_b = export_pkg.export(run_b, "html", skill_root=skill_root)
    assert result_a.out_dir != result_b.out_dir
    assert result_a.out_dir.name == result_b.out_dir.name == "html"
    assert result_a.out_dir.parent.name == "20260101T000000Z-demo"
    assert result_b.out_dir.parent.name == "comparison-high-b15376"
    assert (result_a.out_dir / "index.html").is_file()
    assert (result_b.out_dir / "index.html").is_file()


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
