"""Per-producer coverage metadata for the system model (57B-31).

Every producer gets its OWN partition recording status
(``complete|partial|failed|unavailable|not-applicable``), the caps the upstream producer
applies (file/byte/row/evidence), the source universe it drew from and what it
deliberately omitted, and the unresolved relationships it preserved. This is
where the canonical-completeness rule is enforced in the open: a partition fed
only by a capped artifact is ``partial`` and says so; a missing analyzer is a
disclosed ``unavailable`` partition, never an empty graph reported as clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .builder import ModelBuilder
from ..datastore_coverage import classify as classify_data_model
from ..identity import IdentityMap

PARTITION_STATES = ("complete", "partial", "failed", "unavailable", "not-applicable")


@dataclass
class Partition:
    status: str
    producers: list[str] = field(default_factory=list)
    node_kinds: list[str] = field(default_factory=list)
    edge_types: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    caps: list[str] = field(default_factory=list)
    source_universe: str = ""
    unresolved: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in PARTITION_STATES:
            raise ValueError(f"Partition.status unsupported: {self.status!r}")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "producers": sorted(set(self.producers)),
            "node_kinds": self.node_kinds,
            "edge_types": self.edge_types,
            "counts": self.counts,
            "caps": self.caps,
            "source_universe": self.source_universe,
            "unresolved": self.unresolved,
            "notes": self.notes,
        }


def _node_count(builder: ModelBuilder, kind: str) -> int:
    return sum(1 for n in builder.nodes if n.kind == kind)


def _edge_count(builder: ModelBuilder, edge_type: str, status: str | None = None) -> int:
    return sum(1 for e in builder.edges if e.type == edge_type
               and (status is None or e.status == status))


def _worst(states: list[str]) -> str:
    if not states:
        return "unavailable"
    if any(s == "failed" for s in states):
        return "failed"
    active = [s for s in states if s != "not-applicable"]
    if not active:
        return "not-applicable"
    if all(s == "unavailable" for s in active):
        return "unavailable"
    if any(s in ("partial", "unavailable") for s in active):
        return "partial"
    return "complete"


def build(spec, report: dict, builder: ModelBuilder, cg: dict,
          disc: dict, imports: dict, modules: dict, *, identities: IdentityMap,
          scan_date: str = "") -> dict:
    """Assemble every coverage partition. ``cg`` is the from_callgraph summary,
    ``disc`` the from_discovery summary, ``imports`` the from_imports summary,
    ``scan_date`` the model's resolved scan date (empty when it could not be
    recorded — disclosed here rather than left as a silent blank field)."""
    blocks = {b["repository_ref"]: b for b in report.get("repos", [])}
    parts = {
        "repositories": _repositories(builder, spec).to_dict(),
        "files": _files(builder).to_dict(),
        "symbols_and_calls": _calls(builder, cg, spec, identities).to_dict(),
        "routes": _routes(builder, report, disc).to_dict(),
        "tables": _tables(builder, blocks).to_dict(),
        "access_model": _access(blocks).to_dict(),
        "external_boundaries": _boundaries(builder, blocks).to_dict(),
        "deployable_units": _deploy(builder, blocks).to_dict(),
        "dependency_imports": _imports(builder, imports).to_dict(),
        "modules": _modules(modules).to_dict(),
    }
    if not scan_date:
        parts["symbols_and_calls"]["notes"].append(
            "scan_date is empty — no callgraph-coverage.json scan_date to record "
            "it from; treat the model's timestamp as unknown.")
    return parts


def _repositories(builder: ModelBuilder, spec) -> Partition:
    return Partition(
        status="complete", producers=["discovery"], node_kinds=["repository"],
        edge_types=["containment"],
        counts={"repositories": _node_count(builder, "repository")},
        source_universe="every top-level target repo in the TargetSpec "
                        "(analyzer-owned checkout excluded upstream by discovery).")


def _files(builder: ModelBuilder) -> Partition:
    return Partition(
        status="complete", producers=["discovery", "callgraph"],
        node_kinds=["file"], edge_types=["containment"],
        counts={"files": _node_count(builder, "file")},
        source_universe="files are the UNION of paths cited by any observed "
                        "relationship (call/route/table/boundary/deploy). This is "
                        "an evidence-grounded set, not a full filesystem inventory.")


def _calls(builder: ModelBuilder, cg: dict, spec,
           identities: IdentityMap) -> Partition:
    eligible = [identities.reference_for(r.repo_id)
                for r in spec.repos if r.profiles_for_capability("callgraph")]
    if not eligible:
        return Partition(
            status="not-applicable", producers=["callgraph"],
            node_kinds=["symbol"], edge_types=["call"],
            counts={"symbols": 0, "call_edges": 0, "repos_eligible": 0},
            source_universe="no target repository has a supported call-graph lane.")
    if not cg.get("present"):
        return Partition(
            status="unavailable", producers=["callgraph"],
            node_kinds=["symbol"], edge_types=["call"],
            counts={"symbols": 0, "call_edges": 0},
            notes=["callgraph/ artifact absent from the run dir — the call graph "
                   "was not run or not colocated. Disclosed as unavailable; NOT "
                   "reported as a codebase with no calls."])
    repos = (cg.get("coverage") or {}).get("repos", [])
    states = [r.get("status", "unavailable") for r in repos]
    unresolved = {
        "ambiguous_call_sites": sum(r.get("call_sites", {}).get("ambiguous", 0) for r in repos),
        "external_call_sites": sum(r.get("call_sites", {}).get("external", 0) for r in repos),
        "unresolved_call_sites": sum(r.get("call_sites", {}).get("unresolved", 0) for r in repos),
    }
    return Partition(
        status=_worst(states) if repos else "partial",
        producers=["callgraph"], node_kinds=["symbol"], edge_types=["call"],
        counts={"symbols": _node_count(builder, "symbol"),
                "call_edges": _edge_count(builder, "call"),
                "observed_calls": _edge_count(builder, "call", "observed"),
                "inferred_calls": _edge_count(builder, "call", "inferred"),
                "per_repo": repos},
        caps=["production-source boundary: tests/mocks/generated/vendored/config "
              "excluded from the call graph (counted, never emitted).",
              "ambiguous/external/unresolved call sites emit NO edge; they are "
              "preserved as counts here, not as fabricated edges."],
        source_universe="function/method call edges from 57B-30 (Go VTA + pinned "
                        "TypeScript compiler); observed = proven, inferred = "
                        "dynamic-dispatch candidate.",
        unresolved=unresolved)


def _routes(builder: ModelBuilder, report: dict, disc: dict) -> Partition:
    if not disc.get("routes_present"):
        registered = sum(len(block.get("module_signals", {}).get("routes", []))
                         for block in report.get("repos", []))
        if registered == 0:
            return Partition(
                status="not-applicable", producers=["discovery/liveness"],
                node_kinds=["route"], edge_types=["route-linkage"],
                counts={"routes": 0},
                source_universe="complete discovery observed no registered route surface.")
        note = ("no canonical route_inventory artifact in the run dir. The "
                "capped module_signals.routes "
                "summary is deliberately NOT used as a canonical source, so no "
                "route nodes were emitted.")
        return Partition(
            status="partial", producers=["discovery/liveness"],
            node_kinds=["route"], edge_types=["route-linkage"],
            counts={"routes": 0}, notes=[note],
            source_universe="detailed route-registration inventory unavailable.")
    inventory = report.get("route_inventory") or {}
    rows = inventory.get("rows", [])
    mounts = sum(1 for row in rows if row.get("registration_kind") == "mount")
    unresolved = {
        "no_caller_found": sum(1 for r in rows if r.get("status") == "no-direct-path-match"),
        "match_ambiguous": sum(1 for r in rows if r.get("status") == "match-ambiguous"),
        "ui_method_unresolved": sum(
            1 for r in (report.get("ui_route_linkage") or {}).get("rows", [])
            if r.get("status") == "method-unresolved"),
    }
    notes = ["route registrations come from the DETAILED route_inventory rows, "
             "not the capped module_signals.routes summary."]
    if disc.get("route_summary_capped"):
        notes.append("module_signals.routes hit its 200-row cap in >=1 repo — "
                     "another reason the summary is unfit as the canonical source.")
    # A liveness scan-cap hit (6000-file / 262144-byte) or an ast-grep->regex
    # fallback silently shortens the route/linkage graph — degrade to partial and
    # say why (57B-31 canonical-completeness rule).
    liveness_notes = inventory.get("notes", [])
    status = "complete"
    if mounts:
        status = "partial"
        notes.append(f"{mounts} route mount/group registration(s) are preserved as "
                     "unresolved topology; mount-to-leaf composition is not guessed.")
    if any("COVERAGE CAP" in n for n in liveness_notes):
        status = "partial"
        notes.append("liveness scan hit a file/byte COVERAGE CAP — some call "
                     "sites / route registrations were NOT scanned (see "
                     "route_inventory.notes); route/linkage graph is incomplete.")
    if any("FALLBACK" in n for n in liveness_notes):
        status = "partial"
        notes.append("route registrations came from the regex FALLBACK (ast-grep "
                     "unavailable) — reduced robustness; disclosed.")
    return Partition(
        status=status, producers=["discovery/liveness"],
        node_kinds=["route"], edge_types=["route-linkage"],
        counts={"routes": _node_count(builder, "route"),
                "endpoint_registrations": len(rows) - mounts,
                "unresolved_mounts": mounts,
                "route_linkage_edges": _edge_count(builder, "route-linkage")},
        caps=["route-registration scan: 6000-file / 262144-byte producer caps "
              "(liveness.py); a route beyond them is not registered here.",
              "UI-call matching is prefix/wildcard heuristic; unmatched routes are "
              "preserved as unresolved counts, never labeled dead."],
        source_universe="every backend route registration found by the structural "
                        "ast-grep scan (regex fallback when ast-grep absent).",
        unresolved=unresolved, notes=notes)


def _tables(builder: ModelBuilder, blocks: dict) -> Partition:
    classified = classify_data_model(list(blocks.values()))
    status = classified.status
    evidence_capped = _capped(blocks, "table_evidence")
    notes = ["tables come from the UNCAPPED table_evidence map (deduped by name), "
             "not the capped module_signals.tables summary."]
    notes.extend(classified.notes)
    if classified.unresolved_bindings:
        notes.append("dynamic or unresolved datastore bindings remain; physical "
                     "names were not guessed.")
    if evidence_capped:
        notes.append("per-(table, access-type) evidence hit its 8-site cap in >=1 "
                     "repo — some data edges (access sites) were NOT recorded "
                     "(distinct table set is complete).")
    return Partition(
        status=status, producers=["discovery/tables"], node_kinds=["data-store"],
        edge_types=["data"],
        counts={"data_stores": _node_count(builder, "data-store"),
                "data_edges": _edge_count(builder, "data"),
                "detector_complete": classified.detector_complete,
                "detected_families": list(classified.detected_families),
                "supported_families": list(classified.supported_families),
                "extracted_families": list(classified.extracted_families),
                "unresolved_families": list(classified.unresolved_families),
                "per_repo": list(classified.details)},
        caps=["access evidence capped at 8 sites per (table, access-type) bucket.",
              "Go typed-constant registry: unreferenced constants capped at 40."],
        source_universe="ORM declarations + access sites (ast-grep) and raw-SQL "
                        "DDL (SQLGlot); name-match alone is never confirmed "
                        "shared persistence (access-type ladder preserved).",
        unresolved={"table_bindings": classified.unresolved_bindings,
                    "families": list(classified.unresolved_families)}, notes=notes)


def _capped(blocks: dict, section: str) -> bool:
    """True when a producer disclosed hitting a file/row COVERAGE CAP in any repo."""
    return any("COVERAGE CAP" in note
               for b in blocks.values()
               for note in b.get(section, {}).get("notes", []))


def _access(blocks: dict) -> Partition:
    available = sum(1 for b in blocks.values()
                    if b.get("access_model", {}).get("available"))
    status = "complete" if available == len(blocks) else (
        "partial" if available else "unavailable")
    if status == "complete" and _capped(blocks, "access_model"):
        status = "partial"
    return Partition(
        status=status, producers=["discovery/access"], node_kinds=[],
        edge_types=[],
        counts={"repos_with_access_model": available, "repos": len(blocks)},
        caps=["per-check sample capped at 8 sites; role catalog at 60; policy-file "
              "scan at 4000 files."],
        source_universe="authorization-shaped code LOCATED and COUNTED as repo "
                        "attributes (role catalogs, authz checks, middleware, "
                        "route guards, casbin policy files) — never interpreted.",
        notes=["access-model signals are attached to repository node attrs, not "
               "modeled as a separate node/edge type."])


def _boundaries(builder: ModelBuilder, blocks: dict) -> Partition:
    available = all(b.get("integration_evidence", {}).get("available")
                    for b in blocks.values()) if blocks else False
    evidence_capped = _capped(blocks, "integration_evidence")
    notes = ["CANDIDATES only — evidence code CAN reach a service, never proof "
             "one is active; cross-file constant propagation not attempted."]
    if not available:
        notes.append("ast-grep unavailable in >=1 repo: assembled-URL/package "
                     "evidence fell back or was skipped (disclosed).")
    if evidence_capped:
        notes.append("per-host / per-package evidence hit its 5-site cap in >=1 "
                     "repo — some boundary edges were NOT recorded (distinct "
                     "host/package set is complete).")
    return Partition(
        status="complete" if available and not evidence_capped else "partial",
        producers=["discovery/integrations", "discovery/candidates"],
        node_kinds=["external-boundary"], edge_types=["boundary"],
        counts={"external_boundaries": _node_count(builder, "external-boundary"),
                "boundary_edges": _edge_count(builder, "boundary")},
        caps=["host-fragment and integration-package evidence capped at 5 sites each."],
        source_universe="host-fragment string constants + integration-package "
                        "HTTP call dirs (ast-grep) and TargetSpec integration "
                        "candidates (dependency/client/oauth/endpoint/config/env).",
        notes=notes)


def _deploy(builder: ModelBuilder, blocks: dict) -> Partition:
    inferred = sum(1 for b in blocks.values()
                   if b.get("deployable_units", {}).get("status") == "inferred")
    unknown = [rid for rid, b in blocks.items()
               if b.get("deployable_units", {}).get("status") == "unknown"]
    return Partition(
        status="partial" if _capped(blocks, "deployable_units") else "complete",
        producers=["discovery/deploy"],
        node_kinds=["deployable-unit"], edge_types=["containment"],
        counts={"deployable_units": _node_count(builder, "deployable-unit"),
                "repos_with_units": inferred, "repos_without_units": len(unknown)},
        caps=["deploy-artifact scan: 6000-file / 262144-byte producer caps."],
        source_universe="static deploy artifacts (Dockerfile, compose services, "
                        "go package-main binaries, CI deploy steps) located and "
                        "parsed as data — never a claim a unit is deployed.",
        notes=[f"repos with no deploy artifact found (status unknown, not 'no "
               f"units'): {len(unknown)}."])


def _imports(builder: ModelBuilder, imports: dict) -> Partition:
    producers = ["dependency-cruiser", "go-list"]
    expected = imports.get("expected_repos", [])
    mapped = imports.get("mapped_repos", imports.get("repos", []))
    producer_states = [row.get("status", "unavailable")
                       for row in imports.get("coverage_repos", [])]
    if not expected:
        return Partition(
            status="not-applicable", producers=producers,
            node_kinds=["file"], edge_types=["dependency"],
            counts={"dependency_edges": 0, "repos_with_maps": 0,
                    "repos_eligible": 0},
            source_universe="no target repository has a supported dependency-map lane.")
    if not imports.get("present"):
        note = ("import/dependency edges omitted (not fabricated). Run the "
                "dependency-map stage to populate imports/<artifact_key>."
                "{depcruise,golist}.json.")
        if expected:
            note += (f" {len(expected)} dependency-map-eligible repo(s) produced "
                     f"no map: {', '.join(expected)}.")
        absent_status = _worst(producer_states) if producer_states else "partial"
        return Partition(
            status=absent_status, producers=producers,
            node_kinds=["file"], edge_types=["dependency"],
            counts={"dependency_edges": 0, "repos_with_maps": 0,
                    "repos_eligible": len(expected)},
            source_universe="no machine-readable import map (dependency-cruiser / "
                            "go list) present in this run dir.",
            notes=[note])
    observed = _edge_count(builder, "dependency", "observed")
    unresolved = _edge_count(builder, "dependency", "unresolved")
    missing = [r for r in expected if r not in mapped]
    notes = ["an import not resolvable to an in-repo file/package is kept as an "
             "unresolved dependency edge carrying the raw specifier; Go stdlib "
             "imports are counted, not emitted as edges.",
             "dependency-cruiser is file→file (JS/TS); go list is package→package "
             "(Go) — both the `dependency` type, kept SEPARATE from `call` edges."]
    status = _worst(producer_states) if producer_states else "complete"
    if missing:
        status = "failed" if status == "failed" else "partial"
        notes.append(f"{len(missing)} eligible repo(s) produced NO dependency map "
                     f"(partial): {', '.join(missing)}.")
    elif unresolved and status != "failed":
        status = "partial"
        notes.append("external/third-party specifiers remain unresolved (expected "
                     "wherever a repo has third-party imports) — disclosed as partial.")
    return Partition(
        status=status, producers=producers, node_kinds=["file"],
        edge_types=["dependency"],
        counts={"dependency_edges": observed, "unresolved_edges": unresolved,
                "repos_with_maps": len(mapped), "repos_eligible": len(expected),
                "stdlib_imports_omitted": imports.get("stdlib_omitted", 0)},
        source_universe="dependency-cruiser module graph (JS/TS, file-level) + go "
                        "list -deps package graph (Go, package-level); edges kept "
                        "SEPARATE from the language call-edge type.",
        unresolved={"external_or_unresolvable_specifiers": unresolved},
        notes=notes)


def _modules(summary: dict) -> Partition:
    if summary.get("present"):
        return Partition(
            status="complete", producers=["synthesis/module-map"],
            node_kinds=["module"], edge_types=["containment"],
            counts={"modules": summary.get("modules", 0),
                    "module_candidates": summary.get("candidates", 0),
                    "candidate_dispositions": summary.get("dispositions", {})},
            source_universe="every mechanically surfaced module candidate in "
                            "module-candidates.json is dispositioned exactly once; "
                            "module boundaries remain inferred, not mechanically proven.")
    return Partition(
        status="unavailable", producers=["synthesis"], node_kinds=["module"],
        edge_types=["containment"], counts={"modules": 0},
        source_universe="business module boundaries are synthesis/LLM-INFERRED "
                        "elsewhere, not computed in this deterministic assembler.",
        notes=["no machine-readable inferred module map present in the run dir. "
               "When synthesis emits one, modules enter as `inferred` nodes with "
               "evidence + confidence; they are never computed here (issue "
               "constraint: no graph-community clustering)."])
