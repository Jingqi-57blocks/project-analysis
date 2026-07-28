"""Deterministic ModuleModel finalization and fail-closed Module Drill audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, replace_artifact_text, write_new_text
from .context import SourceContext, load as load_context
from .coverage import Coverage, CoverageStatus
from .driver import ModuleDriver
from .model import FeatureClaim, FeatureEdge, FeatureFlow, FeatureNode, ModuleModel
from .run_state import AuditResult, RunStateProjection
from .scope import FrontierDisposition, ModuleScope
from .validation import ContractError, sha256_json

MODEL_SCHEMA = "module-model-artifact/v1"
MODEL_FILENAME = "module-model.json"
AUDIT_FILENAME = "module-audit.json"


def _load(context: SourceContext, filename: str, schema: str) -> dict[str, Any]:
    path = context.module_run / "evidence" / filename
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"{filename} is required for Module Drill finalization") from exc
    if not isinstance(document, dict) or document.get("schema_version") != schema:
        raise ContractError(f"{filename} has an unsupported schema")
    if document.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError(f"{filename} does not bind the current source manifest")
    return document


def _scope(context: SourceContext) -> ModuleScope:
    path = context.module_run / "evidence" / "module-scope.json"
    try:
        scope = ModuleScope.from_dict(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError) as exc:
        raise ContractError("validated module scope is required for finalization") from exc
    if scope.source_manifest_digest != sha256_json(context.manifest.to_dict()):
        raise ContractError("module scope does not bind the current source manifest")
    return scope


def _recovery(context: SourceContext, driver: ModuleDriver, *, task_id: str,
              filename: str, schema: str) -> dict[str, Any]:
    artifact = _load(context, filename, schema)
    packet, output = driver.validated_task(task_id)
    if artifact.get("task_id") != task_id or artifact.get("packet_input_digest") != packet.input_digest \
            or artifact.get("output") != output:
        raise ContractError(f"{filename} is not the current validated {task_id} output")
    return artifact


def _sync_recovery_outputs(context: SourceContext, driver: ModuleDriver) -> tuple[dict[str, Any], ...]:
    """Load every current, validated sync partition and prove full consumption."""
    artifact = _load(context, "sync-recovery.json", "sync-recovery/v2")
    tasks = artifact.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ContractError("sync-recovery.json has no partition outputs")
    expected_ids: set[str] = set()
    expected_requirements: set[str] = set()
    # Import locally to keep the Module Drill finalizer independent of the
    # packet construction implementation at module import time.
    from .sync_recovery import build_packets

    for packet in build_packets(context):
        expected_ids.add(packet.task_id)
        partition = json.loads(packet.inputs["partition.json"].content)
        expected_requirements.update(partition["requirement_ids"])
    outputs: list[dict[str, Any]] = []
    actual_ids: set[str] = set()
    actual_requirements: set[str] = set()
    for row in tasks:
        if not isinstance(row, dict) or set(row) != {"task_id", "packet_input_digest", "partition", "output"}:
            raise ContractError("sync recovery partition artifact has an invalid row")
        task_id = row["task_id"]
        if not isinstance(task_id, str) or task_id in actual_ids:
            raise ContractError("sync recovery partition artifact has duplicate task IDs")
        actual_ids.add(task_id)
        packet, output = driver.validated_task(task_id)
        if row["packet_input_digest"] != packet.input_digest or row["output"] != output:
            raise ContractError("sync recovery partition is not current validated output")
        partition = row["partition"]
        expected_partition = json.loads(packet.inputs["partition.json"].content)
        if partition != expected_partition:
            raise ContractError("sync recovery partition receipt does not match its validated packet")
        if not isinstance(partition, dict) or not isinstance(partition.get("requirement_ids"), list):
            raise ContractError("sync recovery partition lacks its requirement universe")
        ids = partition["requirement_ids"]
        if not all(isinstance(value, str) and value for value in ids) or len(ids) != len(set(ids)):
            raise ContractError("sync recovery partition has invalid requirement IDs")
        actual_requirements.update(ids)
        outputs.append(output)
    if actual_ids != expected_ids or actual_requirements != expected_requirements:
        raise ContractError("sync recovery partitions do not match the current complete task plan")
    # A validated output may not silently omit an input requirement even when
    # a malformed artifact copied a valid-looking partition receipt.
    disposition_ids = [
        row.get("requirement_id") for output in outputs
        for row in output.get("dispositions", []) if isinstance(row, dict)
    ]
    if set(disposition_ids) != expected_requirements or len(disposition_ids) != len(set(disposition_ids)):
        raise ContractError("sync recovery outputs do not disposition every planned requirement exactly once")
    return tuple(outputs)


def _claims(*outputs: dict[str, Any]) -> tuple[FeatureClaim, ...]:
    claims: list[FeatureClaim] = []
    consumed: set[str] = set()
    for output in outputs:
        if not isinstance(output, dict):
            raise ContractError("recovery task output must be an object")
        for disposition in output.get("dispositions", []):
            if isinstance(disposition, dict) and disposition.get("outcome") == "claimed":
                ids = disposition.get("claim_ids")
                if isinstance(ids, list):
                    consumed.update(value for value in ids if isinstance(value, str))
        for row in output.get("claims", []):
            if not isinstance(row, dict):
                raise ContractError("recovery claim must be an object")
            support = row.get("support")
            if not isinstance(support, list):
                raise ContractError("recovery claim support must be a list")
            refs = tuple(sorted({item.get("ref") for item in support if isinstance(item, dict) and isinstance(item.get("ref"), str)}))
            roles = tuple(sorted({item.get("role") for item in support if isinstance(item, dict) and isinstance(item.get("role"), str)}))
            claims.append(FeatureClaim(
                claim_id=row.get("claim_id"), kind=row.get("kind"),
                anchor_ids=tuple(row.get("anchor_ids", [])), evidence_refs=refs, support_roles=roles,
                subject=row.get("subject"), operation=row.get("operation"), value=row.get("value"),
            ))
    ids = [claim.claim_id for claim in claims]
    if len(ids) != len(set(ids)):
        raise ContractError("recovery outputs contain duplicate claim IDs")
    if set(ids) != consumed:
        missing = sorted(set(ids) - consumed)
        orphaned = sorted(consumed - set(ids))
        raise ContractError(f"claim disposition mismatch: missing={missing}; unknown={orphaned}")
    contradictions: set[tuple[str, str]] = set()
    by_semantic: dict[tuple[str, str], object] = {}
    for claim in claims:
        key = (claim.subject, claim.operation)
        prior = by_semantic.get(key)
        if prior is not None and prior != claim.value:
            contradictions.add(key)
        by_semantic[key] = claim.value
    if contradictions:
        raise ContractError("contradictory claim values: " + ", ".join("/".join(key) for key in sorted(contradictions)))
    return tuple(sorted(claims, key=lambda claim: claim.claim_id))


def _flows(*outputs: dict[str, Any]) -> tuple[FeatureFlow, ...]:
    flows: list[FeatureFlow] = []
    for output in outputs:
        if not isinstance(output, dict):
            raise ContractError("recovery output is invalid")
        flows.extend(FeatureFlow.from_dict(row, "recovery flow") for row in output.get("flows", []))
    ids = [flow.flow_id for flow in flows]
    if len(ids) != len(set(ids)):
        raise ContractError("recovery outputs contain duplicate flow IDs")
    return tuple(sorted(flows, key=lambda flow: flow.flow_id))


def _coverage(scope: ModuleScope, sync_outputs: tuple[dict[str, Any]], async_doc: dict[str, Any],
              closure: str, *, nodes: tuple[FeatureNode, ...],
              claims: tuple[FeatureClaim, ...]) -> dict[str, CoverageStatus]:
    """Derive dimension coverage from finalized feature facts, not one lane.

    Boundary recovery is intentionally asynchronous only for some dimensions.
    Treating its requirement list as the whole feature universe made an
    observed datastore look unavailable even after synchronous recovery had
    produced a persistence claim.  Each dimension therefore has explicit
    provider anchors and semantic-claim requirements below.
    """
    def status(outputs: tuple[dict[str, Any], ...]) -> str:
        rows = [row for output in outputs for row in output.get("dispositions", [])]
        if not rows:
            return "unavailable"
        return "partial" if any(isinstance(row, dict) and row.get("outcome") == "unknown" for row in rows) else "complete"

    sync_status = status(sync_outputs)
    async_status = status((async_doc["output"],))
    boundary_requirements = tuple(
        row for row in async_doc.get("requirements", {}).get("requirements", [])
        if isinstance(row, dict)
    )

    def boundary_refs(*, kinds: set[str] = set(), async_only: bool = False) -> tuple[str, ...]:
        """Use only non-excluded, feature-local boundary requirements.

        ``feature-boundary-closure`` intentionally retains every observed
        graph node for audit.  A node that was excluded from the bounded
        semantic frontier must not make configuration, integration, or async
        coverage look complete merely because it exists in that graph.
        """
        return tuple(sorted({ref for row in boundary_requirements
                             if (not kinds or row.get("boundary_kind") in kinds)
                             and (not async_only or row.get("async_role") != "not-applicable")
                             for ref in row.get("evidence_refs", []) if isinstance(ref, str) and ref}))
    node_refs_by_kind: dict[str, tuple[str, ...]] = {}
    for node in nodes:
        node_refs_by_kind.setdefault(node.kind, tuple())
        node_refs_by_kind[node.kind] = tuple(sorted(set(node_refs_by_kind[node.kind]) | set(node.evidence_refs)))

    def claim_refs(*, kinds: set[str] = set(), roles: set[str] = set(),
                   operations: set[str] = set()) -> tuple[str, ...]:
        return tuple(sorted({ref for claim in claims
                             if claim.kind in kinds or bool(set(claim.support_roles) & roles)
                             or claim.operation in operations
                             for ref in claim.evidence_refs}))

    def dimension(*, node_kinds: set[str], claim_kinds: set[str] = set(),
                  claim_roles: set[str] = set(), claim_operations: set[str] = set(),
                  lane_status: str) -> CoverageStatus:
        provider_refs = tuple(sorted({ref for kind in node_kinds for ref in node_refs_by_kind.get(kind, ())}))
        semantic_refs = claim_refs(kinds=claim_kinds, roles=claim_roles, operations=claim_operations)
        if not provider_refs:
            return CoverageStatus(Coverage(
                "unknown", "unavailable", (),
                ("no feature-local provider evidence was observed",)), closure, ())
        return CoverageStatus(Coverage("applicable", lane_status,
                                       tuple(sorted(set(provider_refs) | set(semantic_refs))), ()), closure, ())

    async_refs = boundary_refs(async_only=True)
    async_claim_refs = claim_refs(kinds={"async-effect", "scheduler", "event", "queue", "notification"})
    if not async_refs:
        async_coverage = Coverage(
            "unknown", "unavailable", (),
            ("no feature-local asynchronous boundary was observed",))
    elif not async_claim_refs:
        async_coverage = Coverage(
            "applicable", "partial", async_refs,
            ("feature-local provider evidence was observed but no source-verified semantic claim was recovered",))
    else:
        async_coverage = Coverage(
            "applicable", async_status, tuple(sorted(set(async_refs) | set(async_claim_refs))), ())
    dimensions: dict[str, CoverageStatus] = {
        "synchronous-behavior": CoverageStatus(Coverage("applicable", sync_status, (), ()), closure, ()),
        "asynchronous-behavior": CoverageStatus(async_coverage, closure, ()),
    }

    def boundary_dimension(*, kinds: set[str], claim_kinds: set[str] = set(),
                           claim_roles: set[str] = set()) -> CoverageStatus:
        refs = boundary_refs(kinds=kinds)
        semantic_refs = claim_refs(kinds=claim_kinds, roles=claim_roles)
        if not refs:
            return CoverageStatus(Coverage(
                "unknown", "unavailable", (),
                ("no feature-local provider evidence was observed",)), closure, ())
        if not semantic_refs:
            return CoverageStatus(Coverage(
                "applicable", "partial", refs,
                ("feature-local provider evidence was observed but no source-verified semantic claim was recovered",)), closure, ())
        return CoverageStatus(Coverage(
            "applicable", async_status, tuple(sorted(set(refs) | set(semantic_refs))), ()), closure, ())

    dimensions["configuration"] = boundary_dimension(
        kinds={"configuration"}, claim_kinds={"configuration"},
    )
    dimensions["integration"] = boundary_dimension(
        kinds={"integration-host", "integration-package"}, claim_kinds={"integration"},
        claim_roles={"integration"},
    )
    dimensions["data"] = dimension(
        node_kinds={"datastore"}, claim_kinds={"persistence-effect"}, claim_roles={"persistence"},
        claim_operations={"reads", "writes", "assigns", "increments", "decrements"}, lane_status=sync_status,
    )
    dimensions["authorization"] = dimension(
        node_kinds={"access-check", "access-role"}, claim_kinds={"authorization", "access"},
        claim_roles={"authorization"}, lane_status=sync_status,
    )
    selected_seed_ids = {
        seed_id for candidate in scope.candidates if candidate.disposition == "selected"
        for seed_id in candidate.seed_ids
    }
    selected_ui = any(seed.kind == "ui-action" and seed.seed_id in selected_seed_ids for seed in scope.seeds)
    # Only a UI-visibility claim can complete the UI dimension.  Generic
    # effect/trigger support may describe a route, configuration, or data
    # boundary and must not accidentally certify a UI entry.
    ui_claim_refs = claim_refs(kinds={"ui-visibility"})
    ui_node_refs = node_refs_by_kind.get("ui-action", ())
    if selected_ui and ui_node_refs and ui_claim_refs:
        dimensions["ui-entry"] = CoverageStatus(Coverage(
            "applicable", sync_status, tuple(sorted(set(ui_node_refs) | set(ui_claim_refs))), ()), closure, ())
    elif selected_ui and ui_node_refs:
        dimensions["ui-entry"] = CoverageStatus(Coverage(
            "applicable", "partial", ui_node_refs,
            ("UI action evidence was observed but no source-verified UI behavior claim was recovered",)), closure, ())
    else:
        dimensions["ui-entry"] = CoverageStatus(Coverage(
            "unknown", "unavailable", (),
            ("no selected source-verified UI entry anchor was observed",)), closure, ())
    return dimensions


def _require_authoritative_coverage(dimensions: dict[str, CoverageStatus]) -> None:
    """A failed required provider can yield a partial run, never completion."""
    incomplete = sorted(
        name for name, value in dimensions.items()
        if value.coverage.applicability == "applicable" and value.coverage.status != "complete"
    )
    if incomplete:
        raise ContractError(
            "mandatory feature dimensions are incomplete: " + ", ".join(incomplete))


def build(context: SourceContext) -> ModuleModel:
    """Merge only current validated artifacts into one canonical ModuleModel."""
    driver = ModuleDriver(context.module_run)
    scope = _scope(context)
    graph = _load(context, "feature-boundary-closure.json", "feature-boundary-closure/v1")
    evidence = _load(context, "feature-evidence.json", "feature-evidence/v1")
    relevant_kinds = {
        "async-boundary", "configuration", "datastore", "access-check",
        "integration-host", "integration-package",
    }
    expected_boundary_ids = {
        row.get("evidence_id") for row in evidence.get("items", [])
        if isinstance(row, dict) and row.get("kind") in relevant_kinds
    }
    boundary_rows = graph.get("boundary_dispositions")
    if not isinstance(boundary_rows, list):
        raise ContractError("feature boundary closure lacks boundary dispositions")
    actual_boundary_ids = {
        row.get("evidence_id") for row in boundary_rows if isinstance(row, dict)
    }
    if None in actual_boundary_ids or actual_boundary_ids != expected_boundary_ids \
            or len(actual_boundary_ids) != len(boundary_rows):
        raise ContractError("feature boundary closure must disposition every relevant provider item exactly once")
    sync_outputs = _sync_recovery_outputs(context, driver)
    async_doc = _recovery(context, driver, task_id="module-async-recovery", filename="async-recovery.json", schema="async-recovery/v1")
    nodes = tuple(FeatureNode.from_dict(row, "boundary graph node") for row in graph.get("nodes", []))
    edges = tuple(FeatureEdge.from_dict(row, "boundary graph edge") for row in graph.get("edges", []))
    initial = _load(context, "feature-graph.json", "feature-graph/v1")
    if initial.get("module_scope_digest") != sha256_json(scope.to_dict()):
        raise ContractError("feature graph does not bind the current module scope")
    graph_closure = _load(context, "feature-graph-closure.json", "feature-graph-closure/v1")
    rows = graph_closure.get("frontier_dispositions", [])
    if not isinstance(rows, list):
        raise ContractError("feature graph closure lacks frontier dispositions")
    dispositions = tuple(FrontierDisposition.from_dict(row, "feature frontier disposition") for row in rows)
    expected_frontiers = {item.frontier_id for item in scope.frontiers}
    if {item.frontier_id for item in dispositions} != expected_frontiers:
        raise ContractError("finalization requires exactly one disposition for every scope frontier")
    states = {item.state for item in dispositions}
    closure = "blocked" if "blocked" in states else "open" if "unresolved" in states else "closed"
    if closure != "closed":
        raise ContractError("mandatory feature frontiers remain unresolved or blocked")
    claims = _claims(*sync_outputs, async_doc["output"])
    dimensions = _coverage(scope, sync_outputs, async_doc, closure, nodes=nodes, claims=claims)
    _require_authoritative_coverage(dimensions)
    return ModuleModel(
        feature_id=scope.feature_id, nodes=tuple(sorted(nodes, key=lambda node: node.node_id)),
        edges=tuple(sorted(edges, key=lambda edge: edge.edge_id)), claims=claims,
        flows=_flows(*sync_outputs, async_doc["output"]), dispositions=tuple(sorted(dispositions, key=lambda item: item.frontier_id)),
        dimension_coverage=dimensions, closure_status=closure,
    )


def _projection(context: SourceContext, driver: ModuleDriver, audit: AuditResult, *, complete: bool) -> None:
    prior = RunStateProjection.from_dict(json.loads((context.module_run / "run-state.json").read_text("utf-8")))
    state = RunStateProjection(prior.run_id, prior.source_manifest_digest, driver._ledger_digest(), complete, audit)
    replace_artifact_text(context.module_run / "run-state.json", json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n")


def finalize(module_run: str | Path) -> tuple[Path | None, AuditResult]:
    """Audit independently, and only write an authoritative model on success."""
    context = load_context(module_run)
    driver = ModuleDriver(context.module_run)
    checks = ("source-integrity", "scope-frontier-dispositions", "validated-task-consumption", "claim-flow-lineage", "feature-coverage")
    try:
        model = build(context)
        audit = AuditResult(True, checks, ())
    except ContractError as exc:
        audit = AuditResult(False, checks, (str(exc),))
        _projection(context, driver, audit, complete=False)
        replace_artifact_text(context.module_run / AUDIT_FILENAME, json.dumps(audit.to_dict(), indent=2, sort_keys=True) + "\n")
        return None, audit
    out = create_stage_dir(context.module_run / "evidence") / MODEL_FILENAME
    document = {"schema_version": MODEL_SCHEMA, "source_manifest_digest": sha256_json(context.manifest.to_dict()),
                "model": model.to_dict()}
    write_new_text(out, json.dumps(document, indent=2, sort_keys=True) + "\n")
    replace_artifact_text(context.module_run / AUDIT_FILENAME, json.dumps(audit.to_dict(), indent=2, sort_keys=True) + "\n")
    _projection(context, driver, audit, complete=True)
    return out, audit
