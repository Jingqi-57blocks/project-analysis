"""Canonical capability accounting for overview preparation.

The wrapper, not the synthesis model, decides which deterministic producers are
applicable, where they write, and what state they reached.  The manifest is a
small stable hand-off: effort may change interpretation, but cannot change the
facts that were available to interpretation.
"""

from __future__ import annotations

import json
from pathlib import Path

from .datastore_coverage import classify as classify_data_model
from .executor import replace_artifact_text
from . import identity
from .profiles.selection import is_node_target
from .sanitize import sanitize_text
from .targetspec import TargetSpec

SCHEMA_VERSION = "2.0.0"
STATES = ("complete", "partial", "unavailable", "not-applicable", "failed")
_SEVERITY = {
    "complete": 0,
    "not-applicable": 0,
    "unavailable": 1,
    "partial": 2,
    "failed": 3,
}


def _read_json(path: Path, default: dict | None = None) -> dict:
    try:
        value = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return dict(default or {})
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _read_json_or_none(path: Path) -> dict | None:
    """Like ``_read_json``, but a genuinely absent artifact stays ``None``
    rather than becoming ``{}`` (57B-84 B2): ``route-inventory.json`` /
    ``ui-route-linkage.json`` are legitimately and PERMANENTLY absent for a
    workspace with zero route backends (mirroring the retired
    ``discover()`` block's own ``route_inventory = None`` case), and the
    downstream ``route_doc is not None`` / ``linkage_doc is not None``
    status checks below need to tell that apart from "present but empty"."""
    try:
        value = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _status(rows: list[dict], *, applicable: bool) -> str:
    if not applicable:
        return "not-applicable"
    if not rows:
        return "failed"
    values = [str(row.get("status", "failed")) for row in rows]
    normalized = ["unavailable" if value == "skipped" else value for value in values]
    unknown = [value for value in normalized if value not in STATES]
    if unknown:
        raise ValueError(f"unsupported producer status: {unknown[0]!r}")
    if all(value == "unavailable" for value in normalized):
        return "unavailable"
    if any(value == "failed" for value in normalized):
        return "failed"
    if any(value in {"partial", "unavailable"} for value in normalized):
        return "partial"
    return "complete"


def _artifact_rows(run: Path, paths: list[str]) -> tuple[list[str], list[str]]:
    observed = [path for path in paths if (run / path).exists()]
    missing = [path for path in paths if path not in observed]
    return observed, missing


def _record(capability_id: str, *, status: str, applicable: bool,
            expected: list[str], run: Path, details: list[dict] | None = None,
            reason: str = "") -> dict:
    if status not in STATES:
        raise ValueError(f"unsupported capability status: {status!r}")
    observed, missing = _artifact_rows(run, expected)
    return {
        "capability_id": capability_id,
        "applicable": applicable,
        "status": status,
        "reason": reason,
        "expected_artifacts": expected,
        "observed_artifacts": observed,
        "missing_artifacts": missing,
        "details": sorted(details or [], key=lambda row: (
            str(row.get("repository_ref", "")),
            str(row.get("lang", row.get("lane", ""))),
            str(row.get("tool", "")))),
    }


def build(run_dir: str | Path) -> dict:
    run = Path(run_dir).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    identities = identity.load(run)
    report = identity.load_discovery_report(run, identities)
    signal_summary = _read_json(run / "signals" / "run-summary.json")
    callgraph = _read_json(run / "callgraph-coverage.json")
    depmap = _read_json(run / "imports" / "depmap-coverage.json")
    route_inventory_doc = _read_json_or_none(run / "routes" / "route-inventory.json")
    ui_route_linkage_doc = _read_json_or_none(run / "routes" / "ui-route-linkage.json")

    signal_rows = list(signal_summary.get("signals", []))
    call_rows = list(callgraph.get("repos", []))
    dep_rows = list(depmap.get("repos", []))
    call_applicable = any(repo.profiles_for_capability("callgraph") for repo in spec.repos)
    dep_applicable = any(repo.profiles_for_capability("dependency-map") for repo in spec.repos)

    records = [
        _record(
            "discovery", status="complete", applicable=True,
            expected=["targets.json", "discovery-report.json"], run=run,
            details=[{"repository_ref": identities.reference_for(repo.repo_id),
                      "status": "complete"}
                     for repo in spec.repos],
        ),
        _record(
            "signals", status=_status(signal_rows, applicable=bool(spec.repos)),
            applicable=bool(spec.repos),
            expected=["signals/run-summary.json"], run=run,
            details=signal_rows,
            reason="no target repositories" if not spec.repos else "",
        ),
        _record(
            "callgraph", status=_status(call_rows, applicable=call_applicable),
            applicable=call_applicable,
            expected=["callgraph-coverage.json", "callgraph"], run=run,
            details=call_rows,
            reason="no supported language lane" if not call_applicable else "",
        ),
        _record(
            "dependency-map", status=_status(dep_rows, applicable=dep_applicable),
            applicable=dep_applicable,
            expected=["imports/depmap-coverage.json", "imports"], run=run,
            details=dep_rows,
            reason="no supported language lane" if not dep_applicable else "",
        ),
        _record(
            "system-model", status=("complete" if (run / "system-model.json").is_file()
                                    else "failed"),
            applicable=True, expected=["system-model.json"], run=run,
        ),
    ]

    # These capability rows describe project shape, not mandatory framework
    # assumptions.  Legitimate zero results become not-applicable only when the
    # corresponding producer completed its source universe.
    repos = report.get("repos", [])
    route_doc = route_inventory_doc
    route_rows = (route_doc or {}).get("rows", [])
    backend_ids = {str(row.get("repository_ref", "")) for row in route_rows
                   if row.get("repository_ref")}
    if not backend_ids:
        backend_ids = {block.get("repository_ref", "") for block in repos
                       if block.get("module_signals", {}).get("routes")}
    any_registered_routes = bool(backend_ids)
    frontend_ids = set()
    blocks_by_ref = {block.get("repository_ref", ""): block for block in repos}
    for target in spec.repos:
        repository_ref = identities.reference_for(target.repo_id)
        block = blocks_by_ref.get(repository_ref, {})
        folders = set(block.get("module_signals", {}).get("folders", []))
        # A Node/TS repository may be a frontend, a backend, or a full-stack
        # unit.  Route registrations do not prove it has no UI, so retain it as
        # UI-capable and report unavailable linkage when discovery could not
        # establish the pair.  Go-only/backend-only workspaces remain genuinely
        # not-applicable.
        #
        # 57B-85: migrated off a hand-rolled ``stacks & {"js","ts",...}``
        # check over the legacy discovery-report "stacks" block, onto the
        # canonical facet predicate. ``profiles/selection.py``'s own
        # docstring documents that its facet-driven gates are STRICTLY
        # BROADER than the old stack/manifest-sniffing probes (e.g. a
        # JS-source repo with no committed package.json now facet-matches
        # where the old probe would not have) — the same widening 57B-81 PR3
        # already accepted for ``registry.network_tools``.
        if is_node_target(target) and "src" in folders:
            frontend_ids.add(repository_ref)
    unresolved_mounts = sum(1 for row in route_rows
                            if row.get("registration_kind") == "mount")
    route_status = ("partial" if route_doc is not None and unresolved_mounts else
                    "complete" if route_doc is not None else
                    "unavailable" if any_registered_routes else "not-applicable")
    records.append(_record(
        "route-inventory", status=route_status,
        applicable=route_status != "not-applicable",
        expected=["routes/route-inventory.json"], run=run,
        details=[{"repository_ref": str(row.get("repository_ref", "")),
                  "status": row.get("status", "")}
                 for row in route_rows],
        reason=("route mounts are retained as unresolved topology; composed endpoint "
                "paths are not guessed" if unresolved_mounts else
                "no registered route surface was discovered" if route_status == "not-applicable"
                else "no canonical detailed route inventory" if route_status == "unavailable"
                else ""),
    ))
    ui_applicable = bool(frontend_ids and backend_ids)
    linkage_doc = ui_route_linkage_doc
    ui_status = ("not-applicable" if not ui_applicable else
                 "complete" if linkage_doc is not None else "unavailable")
    records.append(_record(
        "ui-route-linkage", status=ui_status, applicable=ui_applicable,
        expected=["routes/ui-route-linkage.json"], run=run,
        details=[{"repository_ref": str(row.get("repository_ref", "")),
                  "status": row.get("status", "")}
                 for row in route_rows],
        reason=("project shape has no UI/backend pair" if not ui_applicable else
                "canonical UI-to-route linkage artifact unavailable"
                if ui_status == "unavailable" else ""),
    ))
    table_evidence_by_repo = identity.load_table_evidence_by_repo(run, identities)
    repos_with_tables = [
        {**block, "table_evidence": table_evidence_by_repo.get(
            block.get("repository_ref", ""), {})}
        for block in repos
    ]
    data_model = classify_data_model(repos_with_tables)
    table_status = data_model.status
    records.append(_record(
        "data-model", status=table_status,
        applicable=table_status != "not-applicable", expected=[], run=run,
        details=list(data_model.details),
        reason=("complete detector scan observed no datastore-family signals"
                if table_status == "not-applicable" else
                "; ".join(data_model.notes)),
    ))

    aggregate_rows = [r for r in records if r["status"] != "not-applicable"]
    aggregate_status = max((r["status"] for r in aggregate_rows),
                           key=lambda value: _SEVERITY[value], default="failed")
    return {
        "schema_version": SCHEMA_VERSION,
        "project_ref": identities.project.reference,
        "scan_date": callgraph.get("scan_date") or depmap.get("scan_date") or "",
        "aggregate_status": aggregate_status,
        "capabilities": sorted(records, key=lambda row: row["capability_id"]),
    }


def write(run_dir: str | Path) -> Path:
    run = Path(run_dir).expanduser().resolve()
    out = run / "capabilities.json"
    replace_artifact_text(
        out, sanitize_text(json.dumps(build(run), indent=2, sort_keys=True) + "\n"))
    return out
