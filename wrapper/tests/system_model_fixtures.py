"""Synthetic run-dir builder for system-model tests — domain-neutral, no WCP.

Writes a minimal but structurally faithful run directory (targets.json,
discovery-report.json, callgraph/, optional imports/) so the assembler can be
exercised end to end without any real repository. Every knob toggles one
coverage condition (capped route summary, unavailable tables, incomplete SQL,
missing call graph, present import map) so a single builder covers the matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper import identity
from analysis_wrapper.targetspec import TargetSpec, stable_repo_id

_API = "api-11111111"
_WEB = "web-22222222"
_HA = "a" * 40
_HB = "b" * 40
_PROJECT = stable_repo_id("/ws")


def _targets() -> dict:
    return {
        "schema_version": "3.0.0",
        "repos": [
            {"repo_id": _API, "path": "/ws/api", "facets": [
                {"profile_id": "ecosystem.go-module", "kind": "ecosystem",
                 "scope_roots": ["."], "evidence": ["go.mod"],
                 "confidence": "high", "state": "resolved"},
                {"profile_id": "language.go", "kind": "language",
                 "scope_roots": ["."], "evidence": ["go.mod"],
                 "confidence": "high", "state": "resolved"},
                {"profile_id": "framework.gin", "kind": "framework",
                 "scope_roots": ["."],
                 "evidence": ["go.mod#require:github.com/gin-gonic/gin"],
                 "confidence": "high", "state": "resolved"},
             ],
             "pm": {"name": "go"},
             "git": {"head": _HA, "branch": "main", "commit_count": 3,
                     "oldest_commit_date": "2024-01-01"}},
            {"repo_id": _WEB, "path": "/ws/web", "facets": [
                {"profile_id": "ecosystem.node", "kind": "ecosystem",
                 "scope_roots": ["."], "evidence": ["package.json"],
                 "confidence": "high", "state": "resolved"},
                {"profile_id": "language.typescript", "kind": "language",
                 "scope_roots": ["src"], "evidence": ["tsconfig.json"],
                 "confidence": "high", "state": "resolved"},
             ],
             "pm": {"name": "npm"},
             "git": {"head": _HB, "branch": "main", "commit_count": 2}},
        ],
        "integration_candidates": [
            {"candidate_id": f"{_API}:c1", "repo_id": _API,
             "signal_kind": "dependency",
             "value": "stripe", "evidence": ["package.json: dependency stripe"]},
        ],
    }


_CAP_NOTE = "COVERAGE CAP: per-(table, access-type) evidence capped at 8 sites"
_BOUNDARY_CAP_NOTE = "COVERAGE CAP: per-host / per-package evidence capped at 5 sites"
_ROUTE_CAP_NOTE = "COVERAGE CAP: source scan stopped after 6000 files"


def _api_table_evidence(*, table_available: bool, sql_complete: bool,
                        tables_capped: bool) -> dict:
    """The datastore-evidence provider's own artifact shape (57B-80 PR3) —
    written to ``datastore/<artifact_key>.json``, NOT embedded in the
    discovery-report block anymore (see ``_write_datastore_artifacts``)."""
    return {
        "available": table_available, "distinct_table_count": 1,
        "tables": ({"users": {"declaration": ["internal/model/user.go:10"],
                              "write": ["internal/repo/user.go:20"]}}
                   if table_available else {}),
        "unresolved": ([{"kind": "gorm-access", "evidence": "internal/x.go:3"}]
                       if not sql_complete else []),
        "registry_coverage": {},
        "notes": [_CAP_NOTE] if tables_capped else [],
        "sql_coverage": {"available": sql_complete, "complete": sql_complete},
        "detector_coverage": {
            "complete": True,
            "detected_families": ["gorm", "sql"],
            "supported_families": ["gorm", "sql"],
            "unsupported_families": [],
            "extracted_families": (["gorm", "sql"] if table_available and sql_complete
                                   else ["gorm"] if table_available else []),
            "evidence": {}, "errors": []},
    }


def _web_table_evidence() -> dict:
    return {"available": True, "distinct_table_count": 0,
            "tables": {}, "unresolved": [], "registry_coverage": {},
            "sql_coverage": {"available": True, "complete": True},
            "detector_coverage": {
                "complete": True, "detected_families": [],
                "supported_families": [], "unsupported_families": [],
                "extracted_families": [], "evidence": {}, "errors": []},
            "notes": []}


def _api_deploy_units(*, deploy_capped: bool) -> dict:
    """The deploy-units provider's own artifact shape (57B-82 A1) — written to
    ``deploy/<artifact_key>.json``, NOT embedded in the discovery-report block
    anymore (see ``_write_deploy_artifacts``)."""
    return {
        "status": "inferred",
        "units": [{"kind": "go-main-binary", "name": "cmd/api",
                   "evidence": "cmd/api/main.go"},
                  {"kind": "container-image", "name": ".", "evidence": "Dockerfile"}],
        "artifacts": ["Dockerfile"],
        "notes": (["COVERAGE CAP: stopped after 6000 files — deploy artifacts "
                   "beyond the cap were NOT scanned (incomplete)."]
                  if deploy_capped else []),
    }


def _web_deploy_units() -> dict:
    return {"status": "unknown", "units": [], "artifacts": [], "notes": []}


def _api_access_model() -> dict:
    """The access-evidence provider's own artifact shape (57B-84) — written
    to ``access/<artifact_key>.json``, NOT embedded in the discovery-report
    block anymore (see ``_write_access_artifacts``)."""
    return {
        "available": True, "role_catalog": [{"name": "Admin"}],
        "role_catalog_names": ["Admin"],
        "authz_checks": {"count": 2, "sample": ["a.go:1"]},
        "middleware": {"count": 1, "sample": []},
        "route_guards": {"count": 0, "sample": []},
        "contextual_identity": {"count": 0, "sample": []},
        "policy_artifacts": [{"path": "casbin/model.conf", "kind": "casbin-model"}],
        "notes": []}


def _web_access_model() -> dict:
    return {"available": True, "role_catalog": [],
            "role_catalog_names": [],
            "authz_checks": {"count": 0, "sample": []},
            "middleware": {"count": 0, "sample": []},
            "route_guards": {"count": 1, "sample": ["src/guard.tsx:3"]},
            "contextual_identity": {"count": 0, "sample": []},
            "policy_artifacts": [], "notes": []}


def _api_integration_evidence(*, boundaries_capped: bool) -> dict:
    """The integration-evidence provider's own artifact shape (57B-84) —
    written to ``integrations/<artifact_key>.json`` (see
    ``_write_integration_artifacts``)."""
    return {
        "available": True,
        "host_fragments": [{"value": "api.stripe.com",
                            "evidence": ["internal/pay/client.go:5"]}],
        "integration_packages": [{"package": "stripe", "dirs": ["internal/pay"],
                                  "http_calls": 3,
                                  "evidence": ["internal/pay/client.go:9"]}],
        "notes": [_BOUNDARY_CAP_NOTE] if boundaries_capped else []}


def _web_integration_evidence() -> dict:
    return {"available": True, "host_fragments": [],
            "integration_packages": [], "notes": []}


def _api_block(*, capped_routes: bool) -> dict:
    return {
        "repo_id": _API,
        "provenance": {"is_git": True, "head": _HA, "branch": "main",
                       "remote_redacted": "git@example.com:api.git",
                       "commit_count": 3, "oldest_commit_date": "2024-01-01"},
        "stacks": {"stacks": ["go"], "frameworks": ["gin"],
                   "analysis_roots": [], "evidence": []},
        "module_signals": {
            "folders": ["internal"], "routes": [{"path": "/users", "evidence": "x:1"}],
            "tables": [{"name": "users", "evidence": "x:1"}], "api_configs": [],
            "notes": (["route cap hit at 200: further signals not recorded"]
                      if capped_routes else [])},
    }


def _web_block() -> dict:
    return {
        "repo_id": _WEB,
        "provenance": {"is_git": True, "head": _HB, "branch": "main",
                       "remote_redacted": "git@example.com:web.git",
                       "commit_count": 2, "oldest_commit_date": "2024-02-01"},
        "stacks": {"stacks": ["ts"], "frameworks": [], "analysis_roots": [],
                   "evidence": []},
        "module_signals": {"folders": ["src"], "routes": [], "tables": [],
                           "api_configs": [], "notes": []},
    }


def _report(*, capped_routes: bool) -> dict:
    return {
        "project_id": _PROJECT, "workspace_root": "/ws",
        "repos": [_api_block(capped_routes=capped_routes), _web_block()],
        "not_targeted": [], "reduced_coverage_targets": [],
        "integration_candidate_count": 1,
        "role_catalog_by_repo": {},
    }


def _edge(resolution: str, kind: str, caller: str, caller_c: str, callee: str,
          callee_c: str, site: str) -> dict:
    return {"lang": "go", "resolution": resolution, "kind": kind,
            "caller_symbol": caller, "caller_citation": caller_c,
            "callee_symbol": callee, "callee_citation": callee_c,
            "callsite_citation": site}


def _write_callgraph(run: Path) -> None:
    cg = run / "callgraph"
    cg.mkdir()
    edges = [
        _edge("observed", "static-call", "internal/handlers.Foo",
              f"api@{_HA}:internal/handlers/foo.go:5", "internal/service.Bar",
              f"api@{_HA}:internal/service/bar.go:8",
              f"api@{_HA}:internal/handlers/foo.go:6:3"),
        _edge("inferred", "method-dispatch", "internal/service.Bar",
              f"api@{_HA}:internal/service/bar.go:8", "internal/service.Baz",
              f"api@{_HA}:internal/service/baz.go:3",
              f"api@{_HA}:internal/service/bar.go:12:5"),
    ]
    (cg / "api.jsonl").write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in edges), "utf-8")
    coverage = {
        "scan_date": "2026-02-02",
        "determinism": "edges sorted; identical inputs yield identical bytes",
        "schema_version": "3.0.0",
        "repos": [{"repository_ref": "api", "lang": "go", "status": "complete",
                   "tool": "callgraph", "tool_version": "v0.48.0",
                   "call_sites": {"resolved": 2, "ambiguous": 0, "external": 1,
                                  "unresolved": 0, "total": 3},
                   "edges_emitted": 2}]}
    (run / "callgraph-coverage.json").write_text(
        json.dumps(coverage, indent=2, sort_keys=True) + "\n", "utf-8")


def _write_imports(run: Path) -> None:
    imports = run / "imports"
    imports.mkdir()
    payload = {"modules": [
        {"source": "src/a.ts", "dependencies": [
            {"module": "./b", "resolved": "src/b.ts", "couldNotResolve": False,
             "dependencyTypes": ["local"]},
            {"module": "lodash", "resolved": "node_modules/lodash/index.js",
             "couldNotResolve": False, "dependencyTypes": ["npm"]},
            {"module": "./missing", "couldNotResolve": True},
        ]},
        {"source": "src/b.ts", "dependencies": []},
    ]}
    (imports / "web.depcruise.json").write_text(
        json.dumps(payload, sort_keys=True), "utf-8")


def _write_datastore_artifacts(run: Path, identities, *, table_available: bool,
                               sql_complete: bool, tables_capped: bool) -> None:
    """Write the datastore-evidence provider's own per-repo artifacts (57B-80
    PR3) — ``discovery-report.json`` no longer carries ``table_evidence``
    inline; consumers read it from here instead (see
    ``identity.load_table_evidence_by_repo``)."""
    datastore_dir = run / "datastore"
    datastore_dir.mkdir(parents=True, exist_ok=True)
    api_key = identities.artifact_key_for(_API)
    web_key = identities.artifact_key_for(_WEB)
    (datastore_dir / f"{api_key}.json").write_text(json.dumps(_api_table_evidence(
        table_available=table_available, sql_complete=sql_complete,
        tables_capped=tables_capped), indent=2, sort_keys=True), "utf-8")
    (datastore_dir / f"{web_key}.json").write_text(
        json.dumps(_web_table_evidence(), indent=2, sort_keys=True), "utf-8")


def _write_deploy_artifacts(run: Path, identities, *, deploy_capped: bool) -> None:
    """Write the deploy-units provider's own per-repo artifacts (57B-82 A1) —
    ``discovery-report.json`` no longer carries ``deployable_units`` inline;
    consumers read it from here instead (see
    ``identity.load_deploy_units_by_repo``)."""
    deploy_dir = run / "deploy"
    deploy_dir.mkdir(parents=True, exist_ok=True)
    api_key = identities.artifact_key_for(_API)
    web_key = identities.artifact_key_for(_WEB)
    (deploy_dir / f"{api_key}.json").write_text(json.dumps(
        _api_deploy_units(deploy_capped=deploy_capped), indent=2, sort_keys=True), "utf-8")
    (deploy_dir / f"{web_key}.json").write_text(
        json.dumps(_web_deploy_units(), indent=2, sort_keys=True), "utf-8")


def _write_access_artifacts(run: Path, identities) -> None:
    """Write the access-evidence provider's own per-repo artifacts (57B-84) —
    ``discovery-report.json`` no longer carries ``access_model`` inline."""
    access_dir = run / "access"
    access_dir.mkdir(parents=True, exist_ok=True)
    (access_dir / f"{identities.artifact_key_for(_API)}.json").write_text(
        json.dumps(_api_access_model(), indent=2, sort_keys=True), "utf-8")
    (access_dir / f"{identities.artifact_key_for(_WEB)}.json").write_text(
        json.dumps(_web_access_model(), indent=2, sort_keys=True), "utf-8")


def _write_integration_artifacts(run: Path, identities, *,
                                 boundaries_capped: bool) -> None:
    """Write the integration-evidence provider's own per-repo artifacts
    (57B-84) — ``discovery-report.json`` no longer carries
    ``integration_evidence`` inline."""
    integrations_dir = run / "integrations"
    integrations_dir.mkdir(parents=True, exist_ok=True)
    (integrations_dir / f"{identities.artifact_key_for(_API)}.json").write_text(
        json.dumps(_api_integration_evidence(boundaries_capped=boundaries_capped),
                   indent=2, sort_keys=True), "utf-8")
    (integrations_dir / f"{identities.artifact_key_for(_WEB)}.json").write_text(
        json.dumps(_web_integration_evidence(), indent=2, sort_keys=True), "utf-8")


def _write_route_artifacts(run: Path, identities, *, with_routes: bool,
                           routes_capped: bool) -> None:
    """Write ``routes.emit.assemble``'s own run-level docs directly (57B-84
    B2) — ``discovery-report.json`` no longer carries ``route_inventory``/
    ``ui_route_linkage`` inline; consumers read ``routes/route-
    inventory.json``/``routes/ui-route-linkage.json`` instead. Already
    EXTERNALIZED (``repository_ref``-keyed), matching what the real
    assembler produces from provider fragments — bypasses running the real
    providers, the same shortcut ``_write_datastore_artifacts`` etc. above
    already take."""
    routes_dir = run / "routes"
    routes_dir.mkdir(parents=True, exist_ok=True)
    (routes_dir / "route-coverage.json").write_text(json.dumps(
        {"present": with_routes, "backends": (1 if with_routes else 0),
         "frontends": (1 if with_routes else 0)}, indent=2, sort_keys=True), "utf-8")
    if not with_routes:
        return
    api_ref = identities.reference_for(_API)
    web_ref = identities.reference_for(_WEB)
    rows = [
        {"repository_ref": api_ref, "method": "GET", "path": "/users",
         "route_evidence": "internal/handlers/users.go:12",
         "registration_kind": "endpoint",
         "status": "ui-called", "caller_evidence": ["src/api/users.ts:8"]},
        {"repository_ref": api_ref, "method": "POST", "path": "/users",
         "route_evidence": "internal/handlers/users.go:20",
         "registration_kind": "endpoint",
         "status": "no-direct-path-match", "caller_evidence": []},
        {"repository_ref": api_ref, "method": "GET", "path": "/:id",
         "route_evidence": "internal/handlers/users.go:30",
         "registration_kind": "endpoint",
         "status": "match-ambiguous", "caller_evidence": []},
    ]
    inventory = {
        "notes": [_ROUTE_CAP_NOTE] if routes_capped else [],
        "rows": rows,
        "tool": "ast-grep", "tool_path": "/x", "tool_version": "ast-grep 0.44.1",
        "version_drift": ""}
    linkage = {
        "frontends": [web_ref], "calls_by_frontend_repository": {web_ref: {}},
        "notes": [],
        "rows": [dict(rows[0], frontend_repository_ref=web_ref)],
    }
    (routes_dir / "route-inventory.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True), "utf-8")
    (routes_dir / "ui-route-linkage.json").write_text(
        json.dumps(linkage, indent=2, sort_keys=True), "utf-8")


def write_run(run_dir, *, with_callgraph: bool = True, with_routes: bool = True,
              capped_routes: bool = False, table_available: bool = True,
              sql_complete: bool = True, with_imports: bool = False,
              deploy_capped: bool = False, routes_capped: bool = False,
              tables_capped: bool = False, boundaries_capped: bool = False) -> Path:
    """Materialize a synthetic run dir; returns the run path."""
    run = Path(run_dir)
    run.mkdir(parents=True, exist_ok=True)
    targets = _targets()
    (run / "targets.json").write_text(json.dumps(targets, indent=2), "utf-8")
    spec = TargetSpec.from_json(json.dumps(targets))
    identities = identity.build(
        spec, workspace_root="/ws", project_id=_PROJECT)
    identity.write_mapping(run, identities)
    report = _report(capped_routes=capped_routes)
    (run / "discovery-report.json").write_text(
        json.dumps(identity.externalize_discovery_report(report, identities), indent=2),
        "utf-8")
    _write_deploy_artifacts(run, identities, deploy_capped=deploy_capped)
    _write_datastore_artifacts(run, identities, table_available=table_available,
                              sql_complete=sql_complete, tables_capped=tables_capped)
    _write_access_artifacts(run, identities)
    _write_integration_artifacts(run, identities, boundaries_capped=boundaries_capped)
    _write_route_artifacts(run, identities, with_routes=with_routes,
                           routes_capped=routes_capped)
    if with_callgraph:
        _write_callgraph(run)
    if with_imports:
        _write_imports(run)
    return run
