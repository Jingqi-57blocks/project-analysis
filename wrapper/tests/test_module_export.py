"""Module Drill HTML export: lossless, isolated, offline presentation."""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper.evidence.coverage import Coverage
from analysis_wrapper.cli import main
from analysis_wrapper.export import export, module_export_output_dir
from analysis_wrapper.module_drill import (
    MODULE_SCOPE_VERSION,
    Boundary,
    ModuleCoverage,
    ModuleIdentity,
    ModuleRunLayout,
    ModuleScope,
    OwnedLocation,
    ProjectSnapshot,
    RepositorySnapshot,
    Selector,
    build_module_evidence,
    create_module_run,
    write_module_evidence,
    write_module_health,
    write_module_prd,
)
from analysis_wrapper.targetspec import TargetSpec


SHA = "a" * 40


def _scope() -> ModuleScope:
    project = ProjectSnapshot("sample-project", (RepositorySnapshot("api", SHA, "git"),))
    return ModuleScope(
        MODULE_SCOPE_VERSION, "standalone", project, Selector("billing", "name"),
        ModuleIdentity("billing", "Billing", (), "business", "high"),
        (OwnedLocation("api", "internal/billing", ("internal/billing/service.go",), (),
                       (f"api@{SHA}:internal/billing/service.go:1",)),),
        ("candidate.billing",),
        (Boundary("outbound", "api", "payments", "api",
                  (f"api@{SHA}:internal/billing/service.go:2",)),),
        ModuleCoverage((("scope", Coverage("applicable", "complete", "complete")),),
                       limitations=("A deterministic export limitation.",)),
    )


def _completed_module_run(tmp_path: Path, target, *, run_id: str = "module-run", language: str = "en") -> tuple[Path, Path]:
    source = Path(target.path) / "internal" / "billing"
    source.mkdir(parents=True, exist_ok=True)
    (source / "service.go").write_text("package billing\n", "utf-8")
    skill = tmp_path / "skill"
    scope = _scope()
    layout = ModuleRunLayout(skill, "sample-project", "billing", run_id)
    create_module_run(layout, TargetSpec([target]), scope, language=language)
    evidence = build_module_evidence(scope, {"api": Path(target.path)})
    write_module_evidence(layout.evidence_path, evidence)
    write_module_prd(layout.scope_path, layout.evidence_path, layout.prd_path, language=language)
    write_module_health(layout.scope_path, layout.evidence_path, layout.health_path, language=language)
    # A diagram belongs to authored Markdown.  The exporter must preserve it
    # rather than derive one from its business prose.
    layout.prd_path.write_text(layout.prd_path.read_text("utf-8") + "\n```mermaid\ngraph LR\n  Billing --> Payments\n```\n", "utf-8")
    state = json.loads(layout.run_state_path.read_text("utf-8"))
    state["stages"].update({"evidence": "done", "prd": "done", "health": "done"})
    layout.run_state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", "utf-8")
    return skill, layout.run_dir


def test_module_html_export_is_lossless_isolated_and_deterministic(tmp_path, target):
    skill, run = _completed_module_run(tmp_path, target)
    result = export(run, skill_root=skill)
    expected = module_export_output_dir(skill, "sample-project", "billing", "module-run", "html")
    assert result.out_dir == expected
    assert {"index.html", "prd.html", "health.html"} <= set(result.detail.pages)
    assert (expected / "assets" / "mermaid.min.js").is_file()
    index = (expected / "index.html").read_text("utf-8")
    prd = (expected / "prd.html").read_text("utf-8")
    health = (expected / "health.html").read_text("utf-8")
    assert 'href="prd.html"' in index and 'href="health.html"' in index
    assert "Module As-Is PRD" in prd and "Module Health Report" in health
    assert "diagram-expand" in prd and "assets/report.js" in prd
    assert str(tmp_path) not in index + prd + health
    cmap = json.loads((expected / "content-map.json").read_text("utf-8"))
    assert {doc["doc_id"] for doc in cmap["documents"]} == {"prd", "health"}
    assert all("full-document" in {dest["mode"] for dest in section["destinations"]}
               for doc in cmap["documents"] for section in doc["sections"])
    first = {path.relative_to(expected).as_posix(): path.read_bytes()
             for path in expected.rglob("*") if path.is_file()}
    export(run, skill_root=skill)
    second = {path.relative_to(expected).as_posix(): path.read_bytes()
              for path in expected.rglob("*") if path.is_file()}
    assert second == first


def test_module_export_keeps_zh_content_and_does_not_overwrite_another_run(tmp_path, target):
    skill, first_run = _completed_module_run(tmp_path, target, run_id="first", language="zh-CN")
    _, second_run = _completed_module_run(tmp_path, target, run_id="second", language="zh-CN")
    first = export(first_run, skill_root=skill).out_dir
    second = export(second_run, skill_root=skill).out_dir
    assert first != second and first.is_dir() and second.is_dir()
    html = (first / "health.html").read_text("utf-8")
    assert "模块健康报告" in html
    assert "payments" in html


def test_cli_exports_a_completed_module_run(tmp_path, target):
    skill, run = _completed_module_run(tmp_path, target)
    assert main(["export", "--run", str(run), "--skill-root", str(skill)]) == 0
    assert (skill / "exported" / "sample-project-analysis" / "modules" / "billing"
            / "module-run" / "html" / "index.html").is_file()
