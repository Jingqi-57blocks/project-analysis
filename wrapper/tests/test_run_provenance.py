import json

import pytest

from analysis_wrapper import lifecycle, run_provenance
from analysis_wrapper.targetspec import GitProvenance, RepoTarget, TargetSpec


def _document(target, tmp_path, **kwargs):
    return run_provenance.create_document(
        TargetSpec([target]), analyzer_root=tmp_path,
        language="en", analyzed_at="2026-07-22T00:00:00+00:00", **kwargs)


def test_generation_identity_is_truthful_or_explicitly_unknown(target, tmp_path):
    known = _document(target, tmp_path, model="gpt-5.5", effort="medium")
    assert known["generation"] == {
        "language": "en", "model": "gpt-5.5", "effort": "medium"}
    unknown = _document(target, tmp_path)
    assert unknown["generation"]["model"] == "unknown"
    assert unknown["generation"]["effort"] == "unknown"
    with pytest.raises(ValueError, match="printable"):
        run_provenance.metadata_value("bad\nvalue", "model")


def test_preparation_options_bind_once_and_changed_inputs_fail(tmp_path, target):
    run_provenance.write(tmp_path, _document(target, tmp_path))
    options = {
        "scan_date": "2026-07-22", "history_since": "2024-07-22",
        "coupling_sample_cap": 0, "network_authorized": False,
        "allowed_hosts": ["b.example", "a.example", "a.example"],
    }
    first = run_provenance.bind_preparation(tmp_path, options)
    assert first["preparation"]["allowed_hosts"] == ["a.example", "b.example"]
    assert run_provenance.bind_preparation(tmp_path, options) == first
    for key, value in (
        ("scan_date", "2026-07-23"),
        ("history_since", "2025-01-01"),
        ("coupling_sample_cap", 10),
        ("network_authorized", True),
        ("allowed_hosts", ["different.example"]),
    ):
        changed = dict(options)
        changed[key] = value
        with pytest.raises(ValueError, match=key):
            run_provenance.bind_preparation(tmp_path, changed)


def test_tool_versions_are_collected_from_manifests_and_coverage(tmp_path, target):
    run_provenance.write(tmp_path, _document(target, tmp_path))
    signals = tmp_path / "signals"
    signals.mkdir()
    (signals / "scc.manifest.json").write_text(json.dumps({
        "tool": "scc", "tool_version": "3.7.0", "version_drift": ""}))
    (tmp_path / "callgraph-coverage.json").write_text(json.dumps({"repos": [
        {"tool": "typescript", "tool_version": "5.9.3"},
        {"tool": "typescript", "tool_version": "5.9.3"},
    ]}))
    imports = tmp_path / "imports"
    imports.mkdir()
    (imports / "depmap-coverage.json").write_text(json.dumps({"repos": [
        {"tool": "dependency-cruiser", "tool_version": "18.1.0"},
        {"tool": "go list", "tool_version": ""},
    ]}))
    (tmp_path / "discovery-report.json").write_text(json.dumps({
        "repos": [{"route_inventory": {
            "producer": {"tool": "ast-grep", "tool_version": "0.40.5"}
        }}]
    }))

    refreshed = run_provenance.refresh_tool_versions(tmp_path)
    assert [(row["tool"], row["version"]) for row in refreshed["tool_versions"]] == [
        ("ast-grep", "0.40.5"), ("dependency-cruiser", "18.1.0"),
        ("scc", "3.7.0"),
        ("typescript", "5.9.3")]
    assert len(refreshed["tool_versions"][3]["sources"]) == 1


def test_non_git_source_state_detects_changes_and_ignores_tier1(tmp_path):
    repo = tmp_path / "plain-source"
    repo.mkdir()
    (repo / "app.js").write_text("one\n")
    ignored = repo / "node_modules"
    ignored.mkdir()
    (ignored / "dependency.js").write_text("old\n")
    target = RepoTarget(
        "plain-source", str(repo), stacks=["js"], git=GitProvenance())
    spec = TargetSpec([target])
    document = _document(target, tmp_path)

    assert run_provenance.target_source_staleness(document, spec) == []
    (ignored / "dependency.js").write_text("new\n")
    assert run_provenance.target_source_staleness(document, spec) == []
    (repo / "app.js").write_text("two\n")
    assert run_provenance.target_source_staleness(document, spec) == [
        "plain-source: NON-GIT source files changed"]


def test_analyzer_identity_participates_in_run_staleness(monkeypatch, target, tmp_path):
    recorded = {
        "root": str(tmp_path), "version": "0.3.0",
        "git_head": "a" * 40, "dirty_detail": "no",
    }
    state = lifecycle.RunState.create(
        "run", "project", TargetSpec([target]),
        analysis_identity={"analyzer": recorded})
    monkeypatch.setattr(run_provenance, "analyzer_observation", lambda _root: {
        **recorded, "git_head": "b" * 40})
    assert any("analyzer changed" in item for item in state.staleness())


def test_analyzer_source_state_detects_same_dirty_path_content_change(tmp_path):
    analyzer = tmp_path / "analyzer"
    analyzer.mkdir()
    source = analyzer / "wrapper.py"
    source.write_text("first\n")
    recorded = run_provenance.analyzer_observation(analyzer)
    source.write_text("second\n")

    problems = run_provenance.analyzer_staleness(recorded)
    assert problems and "source_state_sha256" in problems[0]


def test_legacy_run_state_remains_loadable_without_new_provenance(tmp_path, target):
    state = lifecycle.RunState.create("run", "project", TargetSpec([target]))
    state.save(tmp_path)
    assert lifecycle.RunState.load(tmp_path).run_id == "run"
    with pytest.raises(ValueError, match="legacy run cannot resume"):
        run_provenance.load(tmp_path)
