"""57B-11 S7: module signals + stage-1 emit (targets.json + discovery-report)."""

import json
import subprocess
from pathlib import Path

from analysis_wrapper.cli import main
from analysis_wrapper.discovery import emit
from analysis_wrapper.discovery.modules import extract
from analysis_wrapper import module_map
from analysis_wrapper.system_model import assemble as system_model
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
    by_name = {Path(repo.path).name: repo for repo in spec.repos}
    assert {facet.profile_id for facet in by_name["billing-app"].facets} >= {
        "ecosystem.node", "framework.express", "language.javascript",
    }
    assert {facet.profile_id for facet in by_name["audit-svc"].facets} >= {
        "ecosystem.go-module", "language.go",
    }
    assert "stacks" not in json.loads(spec.to_json())["repos"][0]
    kinds = {c.value: c.signal_kind for c in spec.integration_candidates}
    assert kinds["pay-sdk"] == "client_init+dependency+import"
    assert kinds["PAY_API_KEY"] == "env"
    assert report["project_id"].startswith("ws-")
    # Non-git Go repo: empty provenance, disclosed via provenance block.
    audit = next(r for r in report["repos"] if "audit" in r["repo_id"])
    assert audit["provenance"]["is_git"] is False


def test_datastore_facet_is_additive_only_and_never_leaks_into_legacy_stacks_block(tmp_path):
    """57B-80 PR1: technology_facets gains datastore.* facets additively;
    the legacy stacks block (frozen to the pre-PR language/ecosystem/
    framework/repository-trait facet kinds — see
    ``discovery.stacks.STACK_REPORT_FACET_KINDS``) must never see them,
    since deterministic parity compares that block byte-for-byte."""
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"sequelize": "6"}}))
    _write(tmp_path / "index.js", "module.exports = 1;\n")

    _, report = emit.discover(tmp_path)

    repo_report = report["repos"][0]
    facet_ids = {facet["profile_id"] for facet in repo_report["technology_facets"]}
    assert "datastore.sequelize" in facet_ids
    assert not any("datastore" in item for item in repo_report["stacks"]["evidence"])


# test_scan_derived_signals_record_astgrep_version (57B-37) checked that
# every ast-grep scan()-derived discovery-report signal (table_evidence,
# integration_evidence, access_model) carried the runtime version/path/drift.
# All three have now moved off the discovery report onto their own capability
# provider's artifact (57B-80 PR3, 57B-84 this slice) — each one's OWN
# astgrep provenance is pinned by the exact to_dict() equality check in
# test_datastore_evidence_provider.py / test_access_evidence_provider.py /
# test_integration_evidence_provider.py instead, so nothing is left to assert
# about the discovery report itself.


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


def test_non_git_container_discloses_unrecognized_source_folder(tmp_path):
    """57B-112 §3: a non-git folder with source files but no recognized
    manifest (e.g. only Package.swift — no bundled Swift profile) used to be
    silently neither inventoried nor disclosed; the workspace-container note
    was the only trace, and it never named the folder. It must now appear
    in ``not_targeted`` with a factual reason, without becoming a target."""
    ws = tmp_path / "container"
    _write(ws / "web" / "package.json", "{}")
    _write(ws / "web" / "index.js", "export const web = true\n")
    _write(ws / "unsupported-lib" / "Package.swift",
           "// swift-tools-version:5.9\n")
    _write(ws / "unsupported-lib" / "Sources" / "Lib" / "Lib.swift",
           "public struct Lib {}\n")
    (ws / "empty-dir").mkdir(parents=True)  # truly empty: no content at all

    spec, report = emit.discover(ws)

    assert {Path(repo.path).name for repo in spec.repos} == {"web"}
    assert any(
        "unsupported-lib" in line and "no supported manifest" in line
        for line in report["not_targeted"]
    )
    # A folder already targeted, or one with no discoverable content at all,
    # is unaffected — no spurious extra row.
    assert not any("web" in line and "no supported manifest" in line
                   for line in report["not_targeted"])
    assert not any("empty-dir" in line for line in report["not_targeted"])


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


def test_route_inventory_is_independent_of_multiple_frontends(tmp_path):
    """57B-84 B2: route_inventory/ui_route_linkage are now written by
    RouteInventoryProvider/UiRouteLinkageProvider + routes.emit.assemble
    (routes/route-inventory.json, routes/ui-route-linkage.json), not
    computed inline by ``emit.discover()`` — this test's own name is exactly
    the property that migration is meant to preserve: adding a second
    frontend must not perturb the first frontend's own route-linkage rows
    (the retired inline block used to re-scan every backend once per
    frontend; the fragment+assemble shape scans each backend once, total,
    and joins every frontend against that single scan)."""
    ws = tmp_path / "workspace"
    api = ws / "api"
    _write(api / "package.json", '{"dependencies":{"express":"1"}}')
    _write(api / "app.js", "app.get('/items', h); app.get('/health', h);\n")
    for name in ("web-a", "web-b"):
        web = ws / name
        _write(web / "package.json", "{}")
        _write(web / "src" / "api.ts",
               "get(`${api}/items`); get(`${api}/health`);\n")

    run = tmp_path / "run"
    spec, report = emit.discover(ws)
    emit.write_stage1(run, spec, report)
    from analysis_wrapper import identity
    from analysis_wrapper.profiles.execution import run_provider_stage
    from analysis_wrapper.routes import emit as routes_emit
    identities = identity.load(run)
    run_provider_stage(run, spec, identities, scan_date="2026-07-23",
                       network_authorized=False, provenance={})
    routes_emit.assemble(run)

    inventory = json.loads((run / "routes" / "route-inventory.json").read_text("utf-8"))
    linkage = json.loads((run / "routes" / "ui-route-linkage.json").read_text("utf-8"))
    web_a_ref = identities.reference_for(
        next(t.repo_id for t in spec.repos if "web-a" in t.repo_id))
    web_b_ref = identities.reference_for(
        next(t.repo_id for t in spec.repos if "web-b" in t.repo_id))

    assert len(inventory["rows"]) == 2
    assert linkage["frontends"] == sorted([web_a_ref, web_b_ref])
    assert {row["frontend_repository_ref"] for row in linkage["rows"]} == set(
        linkage["frontends"])


def test_canonical_routes_and_datastores_bypass_summary_cap(tmp_path):
    ws = tmp_path / "workspace"
    api = ws / "api"
    _write(api / "package.json", '{"dependencies":{"express":"1"}}')
    _write(api / "app.js", "\n".join(
        f"app.get('/resource-{index}', h);" for index in range(230)))
    _write(api / "migrations" / "schema.js", "\n".join(
        f"queryInterface.createTable('t_{index:03d}', {{}});"
        for index in range(230)))
    run = tmp_path / "run"
    spec, report = emit.discover(ws)
    emit.write_stage1(run, spec, report)
    # 57B-80 PR3: data-store nodes now come from the datastore-evidence
    # capability provider's own artifacts, not straight from discovery-report
    # — run the provider stage (exactly what prepare-overview does) before
    # assembling, so system_model has something to read. 57B-84 B2: routes
    # are the same shape now — RouteInventoryProvider's own fragment +
    # routes.emit.assemble, not an inline discovery-report field.
    from analysis_wrapper import identity
    from analysis_wrapper.profiles.execution import run_provider_stage
    from analysis_wrapper.routes import emit as routes_emit
    run_provider_stage(run, spec, identity.load(run), scan_date="2026-07-23",
                       network_authorized=False, provenance={})
    routes_emit.assemble(run)
    model = system_model.assemble(run).to_dict()
    candidates = module_map.build_candidates(run, model)
    inventory = json.loads((run / "routes" / "route-inventory.json").read_text("utf-8"))

    assert len(report["repos"][0]["module_signals"]["routes"]) == 200
    assert len(inventory["rows"]) == 230
    assert sum(node["kind"] == "route" for node in model["nodes"]) == 230
    assert sum(node["kind"] == "data-store" for node in model["nodes"]) == 230
    assert sum(row["signal_kind"] == "route"
               for row in candidates["candidates"]) == 230
    assert sum(row["signal_kind"] == "data-store"
               for row in candidates["candidates"]) == 230


def test_route_candidate_identity_includes_method(tmp_path):
    ws = tmp_path / "workspace"
    api = ws / "api"
    _write(api / "package.json", '{"dependencies":{"express":"1"}}')
    _write(api / "app.js", "app.get('/items', h); app.post('/items', h);\n")
    run = tmp_path / "run"
    spec, report = emit.discover(ws)
    emit.write_stage1(run, spec, report)
    # 57B-84 B2: route nodes come from RouteInventoryProvider's own fragment
    # + routes.emit.assemble now (routes/route-inventory.json), not an
    # inline discovery-report field — both must run before system_model has
    # anything to read.
    from analysis_wrapper import identity
    from analysis_wrapper.profiles.execution import run_provider_stage
    from analysis_wrapper.routes import emit as routes_emit
    run_provider_stage(run, spec, identity.load(run), scan_date="2026-07-23",
                       network_authorized=False, provenance={})
    routes_emit.assemble(run)
    model = system_model.assemble(run).to_dict()
    values = {row["value"] for row in module_map.build_candidates(
        run, model)["candidates"] if row["signal_kind"] == "route"}
    assert values == {"GET /items", "POST /items"}
