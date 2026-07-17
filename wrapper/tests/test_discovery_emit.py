"""57B-11 S7: module signals + stage-1 emit (targets.json + discovery-report)."""

import json
import subprocess

from analysis_wrapper.cli import main
from analysis_wrapper.discovery import emit
from analysis_wrapper.discovery.modules import extract
from analysis_wrapper.targetspec import TargetSpec


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
