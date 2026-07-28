"""Canonical feature-evidence indexing tests for 57B-143."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from analysis_wrapper.module_drill.context import load
from analysis_wrapper.module_drill.feature_evidence import build, write
from analysis_wrapper.module_drill.runtime import initialize_from_overview
from analysis_wrapper.module_drill.context import SourceContext
from analysis_wrapper.module_drill.source import ArtifactRecord, RepositorySnapshot, SourceManifest
from analysis_wrapper.module_drill.validation import ContractError
from analysis_wrapper.cli import main
from analysis_wrapper import identity
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id
from test_module_drill_runtime import _prepared_overview


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _run(tmp_path: Path, *, route_count: int = 1,
         call_edges: list[dict] | None = None,
         integration_path: str = "src/service.ts") -> tuple[Path, Path]:
    files = {
        "src/routes.ts": "\n" * 6 + "registerRoute();\n",
        "src/ui.ts": "\n" * 11 + "submitRecord();\n",
        "src/schema.ts": "\n" * 2 + "defineStore();\n",
        "src/service.ts": "\n" * 14 + (
            "function createRecord() { setInterval(() => {}, 1); "
            "const enabled = process.env.RECORDS_ENABLED; callRemote(); }\n"),
        "src/access.ts": "\n" * 4 + "checkAccess();\n",
        "src/integration.ts": "\n" * 14 + "callRemote();\n",
    }
    overview = _prepared_overview(tmp_path, files)
    routes = [
        {"repository_ref": "service", "method": "POST", "path": f"/records/{index}",
         "route_evidence": "src/routes.ts:7", "status": "ui-called"}
        for index in range(route_count)
    ]
    _json(overview / "routes" / "route-inventory.json", {"rows": routes})
    _json(overview / "routes" / "ui-route-linkage.json", {
        "rows": [{"frontend_repository_ref": "service", "repository_ref": "service",
                  "method": "POST", "path": "/records/0", "route_evidence": "src/routes.ts:7",
                  "caller_evidence": ["src/ui.ts:12"], "status": "ui-called"}],
    })
    _json(overview / "datastore" / "service.json", {
        "tables": {"records": {"schema_write": ["src/schema.ts:3"],
                                  "write": ["src/service.ts:15"]}},
        "store_metadata": {"records": {"physical_name": "records", "kind": "collection"}},
    })
    _json(overview / "access" / "service.json", {
        "role_catalog": [{"name": "Operator", "kind": "ts-enum", "evidence": "src/access.ts:5"}],
        "authz_checks": {"count": 1, "sample": ["src/access.ts:5"]},
        "middleware": {"count": 0, "sample": []},
        "route_guards": {"count": 0, "sample": []},
        "contextual_identity": {"count": 0, "sample": []},
    })
    _json(overview / "integrations" / "service.json", {
        "host_fragments": [{"value": "api.example.com", "evidence": [f"{integration_path}:15"]}],
        "integration_packages": [{"package": "remote-client", "evidence": [f"{integration_path}:15"]}],
    })
    _json(overview / "feature-boundaries" / "service.json", {
        "async_boundaries": [{"category": "timer", "operation": "setInterval",
                                "evidence": "src/service.ts:15"}],
        "configuration_references": [{"name": "RECORDS_ENABLED", "evidence": "src/service.ts:15"}],
        "test_files": [{"path": "src/service.spec.ts", "evidence": "src/service.ts:15"}],
        "test_links": [{"path": "src/service.spec.ts", "specifier": "./service",
                        "evidence": "src/service.ts:15"}],
    })
    if call_edges is not None:
        path = overview / "callgraph" / "service.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in call_edges), encoding="utf-8")
    initialized = initialize_from_overview(
        overview, output_root=tmp_path / "output", project_key="workspace",
        selector="record", language="en", run_label="feature-evidence")
    return overview, initialized.run_dir


def test_index_uses_complete_canonical_artifacts_and_emits_typed_seeds(tmp_path):
    _, module_run = _run(tmp_path)
    document = build(load(module_run))

    assert document["schema_version"] == "feature-evidence/v1"
    assert len(document["items"]) == 11
    assert {item["kind"] for item in document["items"]} == {
        "route", "ui-action", "datastore", "access-role", "access-check",
        "integration-host", "integration-package", "async-boundary", "configuration", "test-file", "test-link",
    }
    assert {seed["kind"] for seed in document["seeds"]} == {
        "route", "ui-action", "datastore", "job-event", "symbol", "package",
    }
    assert all(item["source_refs"] for item in document["items"])
    assert all(ref.startswith("service@NON-GIT:") for item in document["items"]
               for ref in item["source_refs"])
    assert document == build(load(module_run))


def test_index_never_applies_overview_bounding_to_complete_route_inventory(tmp_path):
    _, module_run = _run(tmp_path, route_count=205)
    document = build(load(module_run))
    assert len([item for item in document["items"] if item["kind"] == "route"]) == 205


def test_index_refuses_a_tampered_source_artifact(tmp_path):
    overview, module_run = _run(tmp_path)
    (overview / "routes" / "route-inventory.json").write_text('{"rows": []}', encoding="utf-8")
    with pytest.raises(ContractError, match="digest changed"):
        build(load(module_run))


def test_index_is_written_once_inside_the_module_run(tmp_path):
    _, module_run = _run(tmp_path)
    context = load(module_run)
    out = write(context)
    assert out == module_run / "evidence" / "feature-evidence.json"
    assert json.loads(out.read_text(encoding="utf-8"))["items"]
    with pytest.raises(FileExistsError):
        write(context)


def test_public_cli_builds_the_canonical_feature_evidence_index(tmp_path, capsys):
    _, module_run = _run(tmp_path)
    assert main(["module-build-evidence", "--run", str(module_run)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert Path(printed["evidence"]).is_file()


def test_cross_repository_ui_link_keeps_the_frontend_as_the_seed_owner(tmp_path):
    workspace = tmp_path / "workspace"
    web, api = workspace / "web", workspace / "api"
    web.mkdir(parents=True)
    api.mkdir()
    spec = TargetSpec([
        RepoTarget(repo_id=stable_repo_id(str(web)), path=str(web)),
        RepoTarget(repo_id=stable_repo_id(str(api)), path=str(api)),
    ])
    identities = identity.build(spec, workspace_root=workspace,
                                project_id=stable_repo_id(str(workspace)))
    source = tmp_path / "source"
    (source / "routes").mkdir(parents=True)
    document = {"rows": [{"frontend_repository_ref": "web", "repository_ref": "api",
                           "method": "POST", "path": "/records",
                           "route_evidence": "internal/routes.go:9",
                           "caller_evidence": ["src/submit.ts:4"], "status": "ui-called"}]}
    path = source / "routes" / "ui-route-linkage.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    manifest = SourceManifest(
        "standalone", None, "a" * 64,
        (RepositorySnapshot("web", "NON-GIT", "non-git"),
         RepositorySnapshot("api", "NON-GIT", "non-git")), {}, (),
        (ArtifactRecord("artifact-ui-route", "routes/ui-route-linkage.json", "v1",
                        hashlib.sha256(path.read_bytes()).hexdigest(), "canonical", "verified"),),
        (),
    )
    context = SourceContext(tmp_path / "module-run", source, manifest, spec, identities)
    document = build(context)
    action = next(item for item in document["items"] if item["kind"] == "ui-action")
    assert action["repository_refs"] == ["web", "api"]
    assert next(seed for seed in document["seeds"] if seed["kind"] == "ui-action")["repository_ref"] == "web"
