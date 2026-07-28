"""Deterministic split Markdown report projection tests."""

import json

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.finalize import finalize
from analysis_wrapper.module_drill.report import CATALOG, render
from test_module_drill_finalize import _ready


def test_render_projects_each_finalized_category_into_stable_markdown(tmp_path):
    module_run = _ready(tmp_path)
    _, audit = finalize(module_run)
    assert audit.passed
    paths = render(module_run)
    assert tuple(paths) == CATALOG
    assert all(path.is_file() for path in paths.values())
    behavior = paths["details/behavior.md"].read_text()
    evidence = paths["details/evidence-and-unknowns.md"].read_text()
    architecture = paths["details/architecture.md"].read_text()
    assert "Claims" in behavior and "Coverage" in evidence
    assert "flowchart LR" in architecture


def test_cli_renders_the_complete_catalog(tmp_path, capsys):
    module_run = _ready(tmp_path)
    assert finalize(module_run)[1].passed
    assert main(["module-render-report", "--run", str(module_run)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result["reports"]) == set(CATALOG)
