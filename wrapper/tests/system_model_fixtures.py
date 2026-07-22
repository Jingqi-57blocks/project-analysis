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

_API = "api-11111111"
_WEB = "web-22222222"
_HA = "a" * 40
_HB = "b" * 40


def _targets() -> dict:
    return {
        "repos": [
            {"repo_id": _API, "path": "/ws/api", "stacks": ["go"],
             "pm": {"name": "go"},
             "git": {"head": _HA, "branch": "main", "commit_count": 3,
                     "oldest_commit_date": "2024-01-01"}},
            {"repo_id": _WEB, "path": "/ws/web", "stacks": ["ts"],
             "pm": {"name": "npm"},
             "git": {"head": _HB, "branch": "main", "commit_count": 2}},
        ],
        "integration_candidates": [
            {"candidate_id": "c1", "repo_id": _API, "signal_kind": "dependency",
             "value": "stripe", "evidence": ["package.json: dependency stripe"]},
        ],
    }


_CAP_NOTE = "COVERAGE CAP: per-(table, access-type) evidence capped at 8 sites"
_BOUNDARY_CAP_NOTE = "COVERAGE CAP: per-host / per-package evidence capped at 5 sites"
_ROUTE_CAP_NOTE = "COVERAGE CAP: source scan stopped after 6000 files"


def _api_block(*, capped_routes: bool, table_available: bool,
               sql_complete: bool, deploy_capped: bool = False,
               tables_capped: bool = False, boundaries_capped: bool = False) -> dict:
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
        "table_evidence": {
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
                "evidence": {}, "errors": []}},
        "access_model": {
            "available": True, "role_catalog": [{"name": "Admin"}],
            "role_catalog_names": ["Admin"],
            "authz_checks": {"count": 2, "sample": ["a.go:1"]},
            "middleware": {"count": 1, "sample": []},
            "route_guards": {"count": 0, "sample": []},
            "contextual_identity": {"count": 0, "sample": []},
            "policy_artifacts": [{"path": "casbin/model.conf", "kind": "casbin-model"}],
            "notes": []},
        "integration_evidence": {
            "available": True,
            "host_fragments": [{"value": "api.stripe.com",
                                "evidence": ["internal/pay/client.go:5"]}],
            "integration_packages": [{"package": "stripe", "dirs": ["internal/pay"],
                                      "http_calls": 3,
                                      "evidence": ["internal/pay/client.go:9"]}],
            "notes": [_BOUNDARY_CAP_NOTE] if boundaries_capped else []},
        "deployable_units": {
            "status": "inferred",
            "units": [{"kind": "go-main-binary", "name": "cmd/api",
                       "evidence": "cmd/api/main.go"},
                      {"kind": "container-image", "name": ".", "evidence": "Dockerfile"}],
            "artifacts": ["Dockerfile"],
            "notes": (["COVERAGE CAP: stopped after 6000 files — deploy artifacts "
                       "beyond the cap were NOT scanned (incomplete)."]
                      if deploy_capped else [])},
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
        "table_evidence": {"available": True, "distinct_table_count": 0,
                           "tables": {}, "unresolved": [], "registry_coverage": {},
                           "sql_coverage": {"available": True, "complete": True},
                           "detector_coverage": {
                               "complete": True, "detected_families": [],
                               "supported_families": [], "unsupported_families": [],
                               "extracted_families": [], "evidence": {}, "errors": []},
                           "notes": []},
        "access_model": {"available": True, "role_catalog": [],
                         "role_catalog_names": [],
                         "authz_checks": {"count": 0, "sample": []},
                         "middleware": {"count": 0, "sample": []},
                         "route_guards": {"count": 1, "sample": ["src/guard.tsx:3"]},
                         "contextual_identity": {"count": 0, "sample": []},
                         "policy_artifacts": [], "notes": []},
        "integration_evidence": {"available": True, "host_fragments": [],
                                 "integration_packages": [], "notes": []},
        "deployable_units": {"status": "unknown", "units": [], "artifacts": [],
                             "notes": []},
    }


def _report(*, route_liveness: bool, capped_routes: bool, table_available: bool,
            sql_complete: bool, deploy_capped: bool, routes_capped: bool,
            tables_capped: bool, boundaries_capped: bool) -> dict:
    liveness = None
    if route_liveness:
        liveness = {
            "frontend": _WEB, "calls_by_base": {},
            "notes": [_ROUTE_CAP_NOTE] if routes_capped else [],
            "rows": [
                {"repo_id": _API, "method": "GET", "path": "/users",
                 "route_evidence": "internal/handlers/users.go:12",
                 "status": "ui-called", "caller_evidence": ["src/api/users.ts:8"]},
                {"repo_id": _API, "method": "POST", "path": "/users",
                 "route_evidence": "internal/handlers/users.go:20",
                 "status": "no-direct-path-match", "caller_evidence": []},
                {"repo_id": _API, "method": "GET", "path": "/:id",
                 "route_evidence": "internal/handlers/users.go:30",
                 "status": "match-ambiguous", "caller_evidence": []},
            ],
            "tool": "ast-grep", "tool_path": "/x", "tool_version": "ast-grep 0.44.1",
            "version_drift": ""}
    return {
        "project_id": "PROJ-1", "workspace_root": "/ws",
        "repos": [_api_block(capped_routes=capped_routes,
                             table_available=table_available,
                             sql_complete=sql_complete,
                             deploy_capped=deploy_capped,
                             tables_capped=tables_capped,
                             boundaries_capped=boundaries_capped), _web_block()],
        "not_targeted": [], "reduced_coverage_targets": [],
        "integration_candidate_count": 1, "route_liveness": liveness,
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
              f"{_API}@{_HA}:internal/handlers/foo.go:5", "internal/service.Bar",
              f"{_API}@{_HA}:internal/service/bar.go:8",
              f"{_API}@{_HA}:internal/handlers/foo.go:6:3"),
        _edge("inferred", "method-dispatch", "internal/service.Bar",
              f"{_API}@{_HA}:internal/service/bar.go:8", "internal/service.Baz",
              f"{_API}@{_HA}:internal/service/baz.go:3",
              f"{_API}@{_HA}:internal/service/bar.go:12:5"),
    ]
    (cg / f"{_API}.jsonl").write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in edges), "utf-8")
    coverage = {
        "scan_date": "2026-02-02",
        "determinism": "edges sorted; identical inputs yield identical bytes",
        "repos": [{"repo_id": _API, "lang": "go", "status": "complete",
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
        ]}]}
    (imports / f"{_WEB}.depcruise.json").write_text(
        json.dumps(payload, sort_keys=True), "utf-8")


def write_run(run_dir, *, with_callgraph: bool = True, route_liveness: bool = True,
              capped_routes: bool = False, table_available: bool = True,
              sql_complete: bool = True, with_imports: bool = False,
              deploy_capped: bool = False, routes_capped: bool = False,
              tables_capped: bool = False, boundaries_capped: bool = False) -> Path:
    """Materialize a synthetic run dir; returns the run path."""
    run = Path(run_dir)
    run.mkdir(parents=True, exist_ok=True)
    (run / "targets.json").write_text(json.dumps(_targets(), indent=2), "utf-8")
    (run / "discovery-report.json").write_text(
        json.dumps(_report(route_liveness=route_liveness,
                           capped_routes=capped_routes,
                           table_available=table_available,
                           sql_complete=sql_complete,
                           deploy_capped=deploy_capped,
                           routes_capped=routes_capped,
                           tables_capped=tables_capped,
                           boundaries_capped=boundaries_capped), indent=2), "utf-8")
    if with_callgraph:
        _write_callgraph(run)
    if with_imports:
        _write_imports(run)
    return run
