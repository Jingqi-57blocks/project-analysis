"""Tests for the pre-57B-88 run compatibility resolver (57B-83 slice 2).

Pre-88 completed runs have no identity-map.json and an old-shaped
discovery-report.json (no schema_version, a completely different per-repo
layout — see identity.load_discovery_report()'s legacy-field rejection).
identity.load() always fails on them at its very first check. This module
builds a synthetic run modeled on the real pre-88 run read (read-only) at
output/WCP-1cc51f1d/overview/20260720T014138Z-b15376, and exercises the
read-only fallback: identity.derive_legacy() + run_inputs.load()'s use of it.

Domain-neutral: every fixture is built under tmp_path, so nothing here
depends on the real WCP run — that run was read only to learn its shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_wrapper import export as export_pkg
from analysis_wrapper import identity
from analysis_wrapper.export.base import Exporter, ExportResult
from analysis_wrapper.report_html import run_inputs
from analysis_wrapper.report_html.generate import generate
from test_report_html import MAP_MD, OVERVIEW_MD, TECH_MD


def _repo_row(repo_id: str, path: Path, *, head: str) -> dict:
    return {"repo_id": repo_id, "path": str(path), "head": head, "dirty_detail": "no"}


# Fake repo-id hash suffixes and commit SHAs use disjoint alphabets (hex
# digits vs. letters beyond "f") so a test asserting one doesn't leak can
# never accidentally match a substring of the other.
_HASH_SUFFIXES = ("aaaa1111", "bbbb2222", "cccc3333")
_FAKE_HEADS = ("11112222" + "3" * 32, "44445555" + "6" * 32, "77778888" + "9" * 32)


def make_legacy_run(
    tmp_path: Path,
    *,
    repo_names: tuple[str, ...] = ("svc-a", "web-b"),
    include_workspace_root: bool = True,
    corrupt_targets_json: bool = False,
) -> tuple[Path, Path, dict[str, Path]]:
    """A synthetic pre-57B-88 run: no identity-map.json, legacy targets.json /
    discovery-report.json shapes, and structured artifacts stamped with a
    schema_version the current contract doesn't recognize (mirroring the
    real run's system-model.json 1.0.0 / callgraph-coverage.json with none
    at all). Returns (run_dir, workspace_root, {name: repo_path}).
    """
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "DEMO"
    repo_paths: dict[str, Path] = {}
    for name in repo_names:
        path = workspace / name
        path.mkdir(parents=True, exist_ok=True)
        repo_paths[name] = path

    project_id = "DEMO-1a2b3c4d"
    provenance = [
        _repo_row(f"{name}-{_HASH_SUFFIXES[index]}", repo_paths[name],
                  head=_FAKE_HEADS[index])
        for index, name in enumerate(repo_names)
    ]
    run_state = {
        "project_id": project_id,
        "run_id": "20260101T000000Z-legacy",
        "language": "en",
        "analyzed_at": "2026-01-01T00:00:00+00:00",
        "inspection_only": False,
        "stage_order": ["discovery", "signals", "findings", "map", "overview"],
        "stages": {"discovery": "done", "signals": "done", "findings": "done",
                   "map": "done", "overview": "done"},
        "provenance": provenance,
    }
    (run / "run-state.json").write_text(json.dumps(run_state), encoding="utf-8")
    # Deliberately no identity-map.json: this is the whole point of the fixture.

    if corrupt_targets_json:
        (run / "targets.json").write_text("{not valid json", encoding="utf-8")
    else:
        # Old targets.json shape: no schema_version, a completely different
        # per-repo layout (git/pm/stacks inline) from the modern TargetSpec
        # contract. derive_legacy() must never open this file.
        (run / "targets.json").write_text(json.dumps({
            "produced_at": "", "produced_by": "",
            "repos": [
                {"repo_id": row["repo_id"], "path": row["path"], "git": {},
                 "pm": {}, "stacks": [], "tier2_exclusions": [], "analysis_roots": []}
                for row in provenance
            ],
            "integration_candidates": [],
        }), encoding="utf-8")

    discovery: dict = {
        "project_id": project_id,
        "repos": [{"repo_id": row["repo_id"]} for row in provenance],
        "role_catalog_by_repo": {},
        "route_liveness": {},
        "not_targeted": [],
        "integration_candidate_count": 0,
        "reduced_coverage_targets": [],
    }
    if include_workspace_root:
        discovery["workspace_root"] = str(workspace)
    (run / "discovery-report.json").write_text(json.dumps(discovery), encoding="utf-8")

    # Old-contract structured artifacts: a real 57B-88-era system-model.json
    # was schema_version "1.0.0"; callgraph/depmap coverage carried none.
    (run / "system-model.json").write_text(
        json.dumps({"schema_version": "1.0.0", "nodes": [], "edges": []}), encoding="utf-8")
    (run / "callgraph-coverage.json").write_text(
        json.dumps({"determinism": {}, "repos": []}), encoding="utf-8")
    (run / "imports").mkdir(exist_ok=True)
    (run / "imports" / "depmap-coverage.json").write_text(
        json.dumps({"determinism": {}, "repos": []}), encoding="utf-8")

    (run / "overview.md").write_text(OVERVIEW_MD, encoding="utf-8")
    (run / "technical-overview.md").write_text(TECH_MD, encoding="utf-8")
    (run / "project-map.md").write_text(MAP_MD, encoding="utf-8")
    return run, workspace, repo_paths


# --------------------------------------------------------------------------- #
# identity.derive_legacy()
# --------------------------------------------------------------------------- #

def test_derive_legacy_produces_clean_names_from_provenance(tmp_path):
    run, workspace, repo_paths = make_legacy_run(tmp_path)
    mapping = identity.derive_legacy(run)

    assert mapping.source == "legacy-derived"
    assert mapping.project.reference == "DEMO"          # workspace basename, no hash
    assert mapping.project.internal_id == "DEMO-1a2b3c4d"  # trusted as recorded
    references = sorted(item.reference for item in mapping.repositories)
    assert references == ["svc-a", "web-b"]              # clean basenames, no hash suffix
    for item in mapping.repositories:
        assert item.canonical_path == str(repo_paths[item.display_name])


def test_derive_legacy_falls_back_to_common_parent_without_workspace_root(tmp_path):
    run, workspace, _ = make_legacy_run(tmp_path, include_workspace_root=False)
    mapping = identity.derive_legacy(run)
    assert mapping.project.reference == "DEMO"  # derived via commonpath of repo paths


def test_derive_legacy_disambiguates_same_basename_repos(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    workspace = tmp_path / "DEMO"
    left = workspace / "group-a" / "api"
    right = workspace / "group-b" / "api"
    left.mkdir(parents=True)
    right.mkdir(parents=True)
    run_state = {
        "project_id": "DEMO-deadbeef",
        "provenance": [
            _repo_row("api-11111111", left, head="1" * 40),
            _repo_row("api-22222222", right, head="2" * 40),
        ],
    }
    (run / "run-state.json").write_text(json.dumps(run_state), encoding="utf-8")
    (run / "discovery-report.json").write_text(
        json.dumps({"workspace_root": str(workspace)}), encoding="utf-8")

    mapping = identity.derive_legacy(run)
    references = {item.internal_id: item.reference for item in mapping.repositories}
    assert references == {
        "api-11111111": "group-a/api",
        "api-22222222": "group-b/api",
    }


def test_derive_legacy_never_reads_targets_json(tmp_path):
    """Proof, not just a claim: targets.json is unreadable garbage and
    derive_legacy() still succeeds — it must never have opened the file.
    """
    run, _workspace, _ = make_legacy_run(tmp_path, corrupt_targets_json=True)
    mapping = identity.derive_legacy(run)
    assert mapping.project.reference == "DEMO"


def test_derive_legacy_rejects_run_without_provenance(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run-state.json").write_text(
        json.dumps({"project_id": "DEMO-x", "provenance": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="provenance"):
        identity.derive_legacy(run)


def test_derive_legacy_rejects_missing_run_state(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(ValueError):
        identity.derive_legacy(run)


def test_derive_legacy_is_reachable_only_from_run_inputs():
    """Boundary guard: no analysis-plane module (discovery, findings,
    system-model, providers, cli, ...) may call identity.derive_legacy() —
    only report_html/run_inputs.py's fallback branch may.
    """
    root = Path(__file__).resolve().parents[1] / "analysis_wrapper"
    allowed = {
        root / "identity.py",
        root / "report_html" / "run_inputs.py",
    }
    hits = []
    for path in root.rglob("*.py"):
        if path in allowed:
            continue
        if "derive_legacy" in path.read_text(encoding="utf-8"):
            hits.append(str(path))
    assert not hits, f"derive_legacy referenced outside its export-path fallback: {hits}"


# --------------------------------------------------------------------------- #
# run_inputs.load() fallback + full export
# --------------------------------------------------------------------------- #

def test_run_inputs_load_falls_back_for_a_legacy_run(tmp_path):
    run, _workspace, _ = make_legacy_run(tmp_path)
    inputs = run_inputs.load(run)

    assert inputs.project_ref == "DEMO"
    assert inputs.identity_map.source == "legacy-derived"
    assert sorted(p.repository_ref for p in inputs.provenance()) == ["svc-a", "web-b"]
    # Old-contract structured artifacts are honestly absent, not a crash.
    assert inputs.system_model is None
    assert inputs.discovery is None
    assert inputs.callgraph_coverage is None
    assert inputs.depmap_coverage is None
    missing = inputs.missing_artifacts()
    assert "system-model.json" in missing
    assert "discovery-report.json" in missing


def test_export_legacy_run_renders_clean_names_and_no_path_leak(tmp_path):
    run, workspace, repo_paths = make_legacy_run(tmp_path)
    out = tmp_path / "out"
    result = generate(run, out)

    assert (out / "index.html").is_file()
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "<h1>DEMO</h1>" in index
    for name, path in repo_paths.items():
        assert str(path) not in index          # no absolute repo path leaks
        assert str(path.parent) not in index
    assert str(workspace) not in index
    all_text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(out.rglob("*")) if p.suffix in (".html", ".json")
    )
    for suffix in _HASH_SUFFIXES[:len(repo_paths)]:
        assert suffix not in all_text  # internal repo_id hash suffix
    assert "system-model.json" in result.missing_artifacts


def test_export_legacy_run_via_export_framework(tmp_path):
    run, _workspace, _ = make_legacy_run(tmp_path)
    result = export_pkg.export(run, "html", out_dir=tmp_path / "out2")
    assert (result.out_dir / "index.html").is_file()


# --------------------------------------------------------------------------- #
# synthetic exporter registration (57B-83 C1 covered locale registration;
# this is the exporter-registry analog)
# --------------------------------------------------------------------------- #

class _MarkerExporter(Exporter):
    format_name = "test-marker"
    required_converter = ""

    def check_available(self) -> tuple[bool, str]:
        return True, ""

    def export(self, inputs, out_dir: Path) -> ExportResult:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "marker.txt").write_text(inputs.project_ref, encoding="utf-8")
        return ExportResult(self.format_name, out_dir, detail=None)


def test_synthetic_exporter_registers_and_exports_legacy_run(tmp_path):
    """register() is additive (mirrors locale.register_locale() from C1): a
    brand-new export format works end to end, including through the legacy
    resolver, with zero analysis-module changes — this test is the proof.
    """
    run, _workspace, _ = make_legacy_run(tmp_path)
    export_pkg.register(_MarkerExporter())
    try:
        assert "test-marker" in export_pkg.available_formats()
        result = export_pkg.export(run, "test-marker", out_dir=tmp_path / "out3")
        marker = (result.out_dir / "marker.txt").read_text(encoding="utf-8")
        assert marker == "DEMO"
    finally:
        del export_pkg._REGISTRY["test-marker"]  # test isolation only
