"""Tests for the offline HTML report generator (57B-35).

Domain-neutral: every fixture is a synthetic run directory built under tmp_path,
so nothing here depends on a particular target project.
"""

import json
from pathlib import Path

import pytest

from analysis_wrapper.report_html import content_map
from analysis_wrapper.report_html.generate import generate

DEEP_MARKER = "UNIQUEDEEPMARKERZZ"
TABLE_MARKER = "cellvaluexyz"

OVERVIEW_MD = f"""# Demo — Overview

## 1. Scope

A paragraph containing {DEEP_MARKER} for split checks.

- item one
- item two

| col a | col b |
| --- | --- |
| {TABLE_MARKER} | 2 |

## 2. Diagnosis

### Finding alpha

Detail for alpha.

## 3. Topology

```mermaid
graph LR
  A --> B
```
"""

TECH_MD = """# Demo — Technical Overview

## Executive summary

Some engineering prose.

## Lens coverage

More detail.
"""

MAP_MD = """# Demo — Project Map

## Modules

- module one
- module two

## Topology

```mermaid
graph TB
  UI --> API
```
"""


def _system_model() -> dict:
    return {
        "schema_version": "1.0.0",
        "project_id": "DEMO-1",
        "scan_date": "2026-01-01",
        "generator": "test",
        "nodes": [
            {"id": "repo:a", "kind": "repository", "repo_id": "svc-a", "label": "svc-a",
             "key": ["svc-a"], "status": "observed", "producers": ["discovery"], "evidence": [],
             "attrs": {"stacks": ["go"], "frameworks": ["gin"], "package_manager": "go",
                       "commit_count": 10, "head": "abc123"}},
            {"id": "repo:b", "kind": "repository", "repo_id": "web-b", "label": "web-b",
             "key": ["web-b"], "status": "observed", "producers": ["discovery"], "evidence": [],
             "attrs": {"stacks": ["ts"], "frameworks": ["react"], "package_manager": "yarn",
                       "commit_count": 20, "head": "def456"}},
            {"id": "file:b1", "kind": "file", "repo_id": "web-b", "label": "app.ts",
             "key": ["web-b", "app.ts"], "status": "observed", "producers": ["depmap"], "evidence": []},
            {"id": "file:a1", "kind": "file", "repo_id": "svc-a", "label": "db.go",
             "key": ["svc-a", "db.go"], "status": "observed", "producers": ["depmap"], "evidence": []},
            {"id": "route:1", "kind": "route", "repo_id": "svc-a", "label": "GET /x",
             "key": ["svc-a", "GET", "/x"], "status": "observed", "producers": ["discovery"],
             "evidence": [], "attrs": {"method": "GET", "path": "/x"}},
            {"id": "data:1", "kind": "data-store", "repo_id": "svc-a", "label": "t_orders",
             "key": ["svc-a", "t_orders"], "status": "observed", "producers": ["tables"],
             "evidence": [], "attrs": {"table": "t_orders", "access_types": ["read", "write"]}},
            {"id": "ext:1", "kind": "external-boundary", "repo_id": "", "label": "smtp.example.test",
             "key": ["host", "smtp.example.test"], "status": "observed", "producers": ["discovery"],
             "evidence": [], "attrs": {"kind": "host-fragment"}},
            {"id": "ext:2", "kind": "external-boundary", "repo_id": "", "label": "left-pad",
             "key": ["candidate", "left-pad"], "status": "observed", "producers": ["discovery"],
             "evidence": [], "attrs": {"kind": "integration-candidate"}},
        ],
        "edges": [
            {"id": "e:1", "type": "route-linkage", "src": "file:b1", "dst": "route:1",
             "status": "observed", "producer": "liveness", "evidence": [], "attrs": {}},
            {"id": "e:2", "type": "data", "src": "file:a1", "dst": "data:1",
             "status": "observed", "producer": "tables", "evidence": [], "attrs": {}},
        ],
        "coverage": {
            "repositories": {"status": "complete", "counts": {"repositories": 2},
                             "caps": [], "unresolved": {}},
            "modules": {"status": "unavailable", "counts": {"modules": 0},
                        "caps": [], "unresolved": {}},
            "routes": {"status": "partial", "counts": {"routes": 1},
                       "caps": ["a cap note"], "unresolved": {"no_caller_found": 3}},
            "tables": {"status": "partial", "counts": {"data_stores": 1},
                       "caps": [], "unresolved": {}},
        },
        "stats": {
            "node_count": 8, "edge_count": 2,
            "nodes_by_kind": {"repository": 2, "file": 2, "route": 1, "data-store": 1,
                              "external-boundary": 2},
            "edges_by_status": {"observed": 2, "inferred": 0, "unresolved": 0},
            "edges_by_type": {"route-linkage": 1, "data": 1},
            "nodes_by_status": {"observed": 8},
        },
    }


def make_run(tmp_path: Path, *, full: bool = True, drilldown: bool = False,
             language: str = "en") -> Path:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "overview.md").write_text(OVERVIEW_MD, encoding="utf-8")
    (run / "technical-overview.md").write_text(TECH_MD, encoding="utf-8")
    (run / "project-map.md").write_text(MAP_MD, encoding="utf-8")
    run_state = {
        "project_id": "DEMO-1",
        "run_id": "20260101T000000Z-demo",
        "language": language,
        "analyzed_at": "2026-01-01T00:00:00+00:00",
        "stages": {"discovery": "done", "overview": "done"},
        # An absolute path in provenance must NEVER reach the rendered output.
        "provenance": [
            {"repo_id": "svc-a", "head": "abc123def456789", "dirty_detail": "no",
             "path": "/Users/demo/secret/svc-a"},
            {"repo_id": "web-b", "head": "def456abc789012", "dirty_detail": "no",
             "path": "/Users/demo/secret/web-b"},
        ],
    }
    (run / "run-state.json").write_text(json.dumps(run_state), encoding="utf-8")
    if full:
        (run / "system-model.json").write_text(json.dumps(_system_model()), encoding="utf-8")
        (run / "callgraph-coverage.json").write_text(json.dumps(
            {"repos": [{"repo_id": "svc-a", "status": "complete", "tool": "callgraph",
                        "edges_emitted": 5}]}), encoding="utf-8")
        (run / "imports").mkdir(exist_ok=True)
        (run / "imports" / "depmap-coverage.json").write_text(json.dumps(
            {"repos": [{"repo_id": "svc-a", "status": "complete", "tool": "go list",
                        "units": 3}]}), encoding="utf-8")
        (run / "discovery-report.json").write_text(json.dumps(
            {"project_id": "DEMO-1", "workspace_root": "/Users/demo/secret",
             "repos": [{"repo_id": "svc-a"}],
             "not_targeted": ["/Users/demo/secret/analyzer (owned checkout)"]}),
            encoding="utf-8")
    if drilldown:
        mod = run / "drilldown" / "module-one"
        mod.mkdir(parents=True, exist_ok=True)
        (mod / "prd.md").write_text("# Module One PRD\n\n## Purpose\n\nPRDBODYMARKER.\n",
                                    encoding="utf-8")
        (mod / "health.md").write_text("# Module One Health\n\n## Risks\n\nHEALTHBODYMARKER.\n",
                                       encoding="utf-8")
    return run


def _all_text(report_dir: Path, suffixes=(".html", ".json")) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(report_dir.rglob("*")) if p.suffix in suffixes
    )


def test_generates_pages_assets_and_manifests(tmp_path):
    run = make_run(tmp_path)
    result = generate(run)
    report = result.report_dir
    for name in ("index.html", "findings.html", "coverage.html", "topology.html",
                 "modules.html", "documents.html", "doc-overview.html",
                 "doc-technical-overview.html", "doc-project-map.html",
                 "content-map.json", "diagram-manifest.json"):
        assert (report / name).is_file(), name
    for asset in ("report.css", "report.js", "mermaid.min.js"):
        assert (report / "assets" / asset).is_file(), asset
    # index references bundled assets by relative path only (no remote refs).
    index = (report / "index.html").read_text(encoding="utf-8")
    assert "assets/report.css" in index
    assert "http://" not in index and "https://" not in index


def test_content_map_covers_every_source_section(tmp_path):
    run = make_run(tmp_path)
    result = generate(run)
    cmap = json.loads((result.report_dir / "content-map.json").read_text(encoding="utf-8"))
    assert content_map.verify_completeness(cmap) == []
    doc_ids = {d["doc_id"] for d in cmap["documents"]}
    assert {"overview", "technical-overview", "project-map"} <= doc_ids
    overview = next(d for d in cmap["documents"] if d["doc_id"] == "overview")
    headings = {s["heading"] for s in overview["sections"]}
    # every authored heading, including the nested finding, is mapped.
    assert "1. Scope" in headings and "Finding alpha" in headings
    for sec in overview["sections"]:
        modes = {d["mode"] for d in sec["destinations"]}
        assert "full-document" in modes


def test_deterministic_byte_identical(tmp_path):
    run = make_run(tmp_path)
    a = generate(run, out_dir=tmp_path / "a")
    b = generate(run, out_dir=tmp_path / "b")
    files_a = sorted(p.relative_to(a.report_dir) for p in a.report_dir.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b.report_dir) for p in b.report_dir.rglob("*") if p.is_file())
    assert files_a == files_b
    for rel in files_a:
        assert (a.report_dir / rel).read_bytes() == (b.report_dir / rel).read_bytes(), rel


def test_missing_optional_artifacts_render_unavailable(tmp_path):
    run = make_run(tmp_path, full=False)
    result = generate(run)
    assert "system-model.json" in result.missing_artifacts
    index = (result.report_dir / "index.html").read_text(encoding="utf-8")
    coverage = (result.report_dir / "coverage.html").read_text(encoding="utf-8")
    assert "unavailable" in index.lower()
    assert "unavailable" in coverage.lower()
    # honest, not broken: pages still exist and the docs still render losslessly.
    assert (result.report_dir / "doc-overview.html").is_file()
    assert DEEP_MARKER in (result.report_dir / "doc-overview.html").read_text(encoding="utf-8")


def test_no_absolute_path_leak(tmp_path):
    run = make_run(tmp_path)
    result = generate(run)
    blob = _all_text(result.report_dir)
    assert "/Users/demo/secret" not in blob
    assert "/Users/" not in blob


def test_main_page_key_data_only_subpages_carry_detail(tmp_path):
    run = make_run(tmp_path)
    result = generate(run)
    index = (result.report_dir / "index.html").read_text(encoding="utf-8")
    findings = (result.report_dir / "findings.html").read_text(encoding="utf-8")
    doc = (result.report_dir / "doc-overview.html").read_text(encoding="utf-8")
    # main page shows structured key data...
    assert "system-snapshot" in index and "Coverage status" in index
    # ...but NOT the deep narrative body (that lives on sub-pages).
    assert DEEP_MARKER not in index
    assert TABLE_MARKER not in index
    # sub-pages carry the full detail (section-aware + lossless full document).
    assert DEEP_MARKER in findings
    assert DEEP_MARKER in doc and TABLE_MARKER in doc


def test_modules_entrance_stub_without_drilldown(tmp_path):
    run = make_run(tmp_path)
    result = generate(run)
    assert result.drilldown_available is False
    modules = (result.report_dir / "modules.html").read_text(encoding="utf-8")
    assert "note-stub" in modules
    assert "not yet available" in modules.lower()


def test_modules_entrance_lights_up_with_drilldown(tmp_path):
    run = make_run(tmp_path, drilldown=True)
    result = generate(run)
    assert result.drilldown_available is True
    modules = (result.report_dir / "modules.html").read_text(encoding="utf-8")
    assert "note-live" in modules
    assert "module-one" in modules
    # per-module PRD/health render as lossless full documents.
    prd = result.report_dir / "doc-module-module-one-prd.html"
    health = result.report_dir / "doc-module-module-one-health.html"
    assert prd.is_file() and health.is_file()
    assert "PRDBODYMARKER" in prd.read_text(encoding="utf-8")
    assert "HEALTHBODYMARKER" in health.read_text(encoding="utf-8")
    # and are folded into the content map (lossless parity).
    cmap = json.loads((result.report_dir / "content-map.json").read_text(encoding="utf-8"))
    assert content_map.verify_completeness(cmap) == []
    assert any(d["doc_id"] == "module-module-one-prd" for d in cmap["documents"])


def test_mermaid_verbatim_and_structured_topology_in_manifest(tmp_path):
    run = make_run(tmp_path)
    result = generate(run)
    dm = json.loads((result.report_dir / "diagram-manifest.json").read_text(encoding="utf-8"))
    ids = {d["id"] for d in dm["diagrams"]}
    kinds = {d["source_kind"] for d in dm["diagrams"]}
    assert "system-model:topology" in ids            # structured, from edges
    assert "markdown-mermaid" in kinds and "system-model" in kinds
    # authored source rendered verbatim in the full document.
    doc = (result.report_dir / "doc-overview.html").read_text(encoding="utf-8")
    assert 'pre class="mermaid"' in doc and "graph LR" in doc


def test_zh_cn_chrome_and_source_verbatim(tmp_path):
    run = make_run(tmp_path, language="zh-CN")
    result = generate(run)
    index = (result.report_dir / "index.html").read_text(encoding="utf-8")
    # UI chrome localizes...
    assert "报告总览" in index
    assert 'lang="zh-CN"' in index
    # ...but source labels are never translated.
    doc = (result.report_dir / "doc-overview.html").read_text(encoding="utf-8")
    assert "Demo — Overview" in doc


def test_report_dir_may_not_be_run_dir(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(ValueError):
        generate(run, out_dir=run)
