"""57B-11 S7: module signals + stage-1 emit (targets.json + discovery-report)."""

import json
import subprocess
from pathlib import Path

from analysis_wrapper.cli import main
from analysis_wrapper.discovery import emit
from analysis_wrapper.discovery.modules import extract
from analysis_wrapper.targetspec import TargetSpec, path_contains


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _workspace(tmp_path):
    """One committed JS repo with routes/tables/env/API config + one Go repo."""
    app = tmp_path / "ws" / "billing-app"
    _write(app / "package.json", json.dumps(
        {"dependencies": {"express": "4", "pay-sdk": "1"}}))
    _write(app / "app.js",
           "const express = require('express');\n"
           "const app = express();\n"
           "app.use('/invoices', require('./routes/invoices'));\n")
    _write(app / "routes" / "invoices.js",
           "const sdk = require('pay-sdk');\n"
           "const client = sdk.createClient({key: process.env.PAY_API_KEY});\n"
           "router.get('/invoices/:id', h);\nrouter.post('/invoices', h);\n")
    _write(app / "migrations" / "20240101-create-invoices.js",
           "module.exports = { up: (q) => q.createTable('invoices', {}) };\n")
    _write(app / "openapi.yaml", "openapi: 3.0.0\n")
    _write(app / ".env.example", "PAY_API_KEY=fake-secret-value\n")
    subprocess.run(["git", "-C", str(app), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(app), "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(app), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)

    svc = tmp_path / "ws" / "audit-svc"
    _write(svc / "go.mod", "module example.com/audit\n")
    _write(svc / "main.go",
           'package main\nimport "github.com/gin-gonic/gin"\n'
           'func main() { r := gin.Default(); r.GET("/audit", nil) }\n')
    return tmp_path / "ws"


def test_module_signals_extraction(tmp_path):
    ws = _workspace(tmp_path)
    signals = extract(ws / "billing-app")
    routes = {r["path"] for r in signals.routes}
    assert {"/invoices", "/invoices/:id"} <= routes
    assert any(t["name"] == "invoices" for t in signals.tables)
    assert "openapi.yaml" in signals.api_configs
    assert "routes" in signals.folders and "migrations" in signals.folders


def test_discover_emits_valid_targetspec_with_candidates(tmp_path):
    ws = _workspace(tmp_path)
    spec, report = emit.discover(ws)
    assert {r.path.split("/")[-1] for r in spec.repos} == {"billing-app", "audit-svc"}
    kinds = {c.value: c.signal_kind for c in spec.integration_candidates}
    assert kinds["pay-sdk"] == "client_init+dependency+import"
    assert kinds["PAY_API_KEY"] == "env"
    assert report["project_id"].startswith("ws-")
    # Non-git Go repo: empty provenance, disclosed via provenance block.
    audit = next(r for r in report["repos"] if "audit" in r["repo_id"])
    assert audit["provenance"]["is_git"] is False


def test_scan_derived_signals_record_astgrep_version(tmp_path):
    # Every ast-grep scan()-derived signal in the discovery report carries the
    # runtime version/path/drift, using the executor path's field names (57B-37).
    ws = _workspace(tmp_path)
    _, report = emit.discover(ws)
    for repo in report["repos"]:
        for key in ("integration_evidence", "table_evidence", "access_model"):
            sig = repo[key]
            assert sig["tool"] == "ast-grep"
            assert {"tool_version", "tool_path", "version_drift"} <= set(sig)


def test_stage1_checkpoint_roundtrip_and_no_secret_values(tmp_path):
    ws = _workspace(tmp_path)
    run_dir = tmp_path / "run"
    spec, report = emit.discover(ws)
    targets_path, report_path = emit.write_stage1(run_dir, spec, report)
    # The stage-1 artifact is the executor's input: it must round-trip.
    loaded = TargetSpec.load(targets_path)
    assert len(loaded.repos) == 2
    assert len(loaded.integration_candidates) == len(spec.integration_candidates)
    # env values (even example ones) never persist in the report.
    assert "fake-secret-value" not in report_path.read_text()


def test_nested_repo_disclosed_not_targeted(tmp_path):
    ws = _workspace(tmp_path)
    embedded = ws / "billing-app" / "embedded"
    _write(embedded / "x.txt", "x")
    subprocess.run(["git", "-C", str(embedded), "init", "-q"], check=True)
    spec, report = emit.discover(ws)
    assert {r.path.split("/")[-1] for r in spec.repos} == {"billing-app", "audit-svc"}
    assert any("nested in" in line for line in report["not_targeted"])


def test_non_git_workspace_container_targets_children_once(tmp_path):
    ws = tmp_path / "container"
    _write(ws / "web" / "package.json", "{}")
    _write(ws / "web" / "index.js", "export const web = true\n")
    _write(ws / "api" / "go.mod", "module example.com/api\n")
    _write(ws / "api" / "main.go", "package main\n")

    spec, report = emit.discover(ws)

    assert {Path(repo.path).name for repo in spec.repos} == {"api", "web"}
    assert all(Path(repo.path).resolve() != ws.resolve() for repo in spec.repos)
    assert len(report["repos"]) == len(spec.repos) == 2
    assert any("workspace container" in line for line in report["not_targeted"])


def test_direct_non_git_root_subsumes_child_projects(tmp_path):
    ws = tmp_path / "root-project"
    _write(ws / "package.json", "{}")
    _write(ws / "index.js", "export const root = true\n")
    _write(ws / "packages" / "child" / "package.json", "{}")
    _write(ws / "packages" / "child" / "index.js", "export const child = true\n")

    spec, report = emit.discover(ws)

    assert [Path(repo.path).resolve() for repo in spec.repos] == [ws.resolve()]
    # Only direct child projects are independently discovered in v1; the
    # nested package remains covered by the root scan without becoming a
    # second target.
    assert len(report["repos"]) == 1


def test_direct_root_discloses_contained_first_level_project(tmp_path):
    ws = tmp_path / "root-project"
    _write(ws / "package.json", "{}")
    _write(ws / "index.js", "export const root = true\n")
    _write(ws / "child" / "package.json", "{}")
    _write(ws / "child" / "index.js", "export const child = true\n")

    spec, report = emit.discover(ws)

    assert [Path(repo.path).resolve() for repo in spec.repos] == [ws.resolve()]
    assert any("contained in root project" in line
               for line in report["not_targeted"])


def test_non_git_project_owns_nested_git_repo(tmp_path):
    ws = tmp_path / "mixed"
    project = ws / "platform"
    nested = project / "plugin"
    _write(project / "package.json", "{}")
    _write(project / "index.js", "export const platform = true\n")
    _write(nested / "go.mod", "module example.com/plugin\n")
    _write(nested / "main.go", "package main\n")
    subprocess.run(["git", "-C", str(nested), "init", "-q", "-b", "main"], check=True)

    spec, report = emit.discover(ws)

    assert [Path(repo.path).resolve() for repo in spec.repos] == [project.resolve()]
    assert any(str(nested) in line and "canonical non-git project" in line
               for line in report["not_targeted"])


def test_path_containment_is_segment_aware(tmp_path):
    app = tmp_path / "app"
    application = tmp_path / "application"
    assert path_contains(app, app / "src")
    assert not path_contains(app, application)


def test_cli_discover_subcommand(tmp_path, capsys):
    ws = _workspace(tmp_path)
    run_dir = tmp_path / "run"
    code = main(["--out", str(run_dir), "discover",
                 "--workspace", str(ws), "--exclude", "audit-svc"])
    assert code == 0
    out = capsys.readouterr().out
    assert "1 target repo(s)" in out
    assert "excluded by operator flag" in out
    assert (run_dir / "targets.json").is_file()
    assert (run_dir / "discovery-report.json").is_file()


def test_cli_run_without_targets_is_input_error(tmp_path, capsys):
    code = main(["--out", str(tmp_path / "o"), "sweep"])
    assert code == 2
    assert "--targets is required" in capsys.readouterr().err
