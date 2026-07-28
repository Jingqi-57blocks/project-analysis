"""Frozen overview-acceptance inputs and honest executor provenance.

The normal run provenance records discovery and preparation.  This module
adds the separate contract needed to answer a different question: whether two
model executions received the same deterministic evidence and task packets.
It deliberately records no prompt bodies, target source, credentials, or API
headers.  Missing provider telemetry is represented explicitly instead of
being replaced with ``unknown`` or a made-up zero.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from . import identity, run_provenance
from .executor import replace_artifact_text, write_new_text
from .targetspec import TargetSpec
from .orchestrator.engine import Engine


SCHEMA_VERSION = 1
FILENAME = "acceptance-manifest.json"
EXECUTION_FILENAME = "execution-provenance.json"

# These are the deterministic inputs an overview preparation owns.  A missing
# entry is still frozen as an explicit unavailable state: a controlled A/B
# must never turn a missing provider or partition plan into a shared implicit
# assumption.
_DETERMINISTIC_ARTIFACTS = (
    "targets.json",
    "identity-map.json",
    "run-provenance.json",
    "discovery-report.json",
    "signals/run-summary.json",
    "provider-execution.json",
    "evidence-catalog.json",
    "callgraph-coverage.json",
    "imports/depmap-coverage.json",
    "routes/route-coverage.json",
    "routes/route-inventory.json",
    "routes/ui-route-linkage.json",
    "system-model.json",
    "module-candidates.json",
    "cohesion-bundle.json",
    "capabilities.json",
    "synthesis-input.json",
    "workspace-metrics.json",
    "consistency-audit.json",
    "tasks/lens-semantic-partitions.json",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def availability(value: Any, unavailable_reason: str) -> dict[str, Any]:
    """Return the one availability shape used by every acceptance field."""
    if value is None or value == "" or value == "unknown":
        return {"status": "unavailable", "value": None, "reason": unavailable_reason}
    return {"status": "recorded", "value": value, "reason": ""}


def _load_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read acceptance input {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"acceptance input {path.name} must contain an object")
    return value


def _artifact_digests(run: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative in _DETERMINISTIC_ARTIFACTS:
        path = run / relative
        if path.is_file():
            rows.append({"artifact": relative, "status": "recorded",
                         "sha256": _file_digest(path), "reason": ""})
        else:
            rows.append({"artifact": relative, "status": "unavailable", "sha256": "",
                         "reason": "artifact was not produced for this run"})
    # Signal manifests carry tool/provider execution identities.  The
    # normalized form omits volatile run-output locations; only its digest is
    # frozen, so repository paths and command output never enter this file.
    for path in sorted((run / "signals").glob("*.manifest.normalized.json")) \
            if (run / "signals").is_dir() else []:
        rows.append({"artifact": f"signals/{path.name}", "status": "recorded",
                     "sha256": _file_digest(path), "reason": ""})
    return rows


def _repository_snapshots(spec: TargetSpec, identities: identity.IdentityMap,
                          provenance: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_states = {
        str(row.get("repo_id", "")): str(row.get("source_state_sha256", ""))
        for row in provenance.get("targets", []) if isinstance(row, dict)
    }
    rows = []
    for target in sorted(spec.repos, key=lambda item: identities.reference_for(item.repo_id)):
        rows.append({
            "repository_ref": identities.reference_for(target.repo_id),
            "snapshot": {
                "state": "git" if target.git.is_git else "NON-GIT",
                "revision": target.git.head,
                "branch": target.git.branch,
                "dirty_detail": target.git.dirty_detail,
                "source_state_sha256": source_states.get(target.repo_id, ""),
            },
        })
    return rows


def _provider_projection(run: Path) -> dict[str, Any]:
    document = _load_object(run / "provider-execution.json") or {}
    rows = []
    for row in document.get("executions", []):
        if not isinstance(row, dict):
            continue
        coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else None
        rows.append({
            "provider_id": str(row.get("provider_id", "")),
            "capability_id": str(row.get("capability_id", "")),
            "repository_ref": str(row.get("repository_ref", "")),
            "matched_profiles": sorted(str(value) for value in row.get("matched_profiles", [])
                                       if isinstance(value, str)),
            "outcome": str(row.get("outcome", "")),
            "coverage": ({key: coverage.get(key) for key in
                          ("applicability", "status", "reason_code")}
                         if coverage is not None else None),
            "tools": [{key: tool.get(key) for key in ("tool_id", "signal_id", "status")}
                      for tool in row.get("tools", []) if isinstance(tool, dict)],
        })
    return {"status": "recorded" if document else "unavailable",
            "executions": sorted(rows, key=lambda item: (
                item["provider_id"], item["repository_ref"], item["capability_id"])),
            "reason": "" if document else "provider execution record is absent"}


def _capability_projection(run: Path) -> dict[str, Any]:
    document = _load_object(run / "capabilities.json") or {}
    rows = []
    for row in document.get("capabilities", []):
        if not isinstance(row, dict):
            continue
        rows.append({key: row.get(key) for key in (
            "capability_id", "applicable", "status", "reason",
            "expected_artifacts", "observed_artifacts", "missing_artifacts")})
    return {"status": "recorded" if document else "unavailable",
            "aggregate_status": document.get("aggregate_status", ""),
            "capabilities": sorted(rows, key=lambda item: str(item.get("capability_id", ""))),
            "reason": "" if document else "capability coverage record is absent"}


def _semantic_partition_projection(run: Path) -> dict[str, Any]:
    document = _load_object(run / "tasks" / "lens-semantic-partitions.json")
    if document is None:
        return {"status": "unavailable", "plans": [],
                "reason": "semantic partition plan was not produced"}
    plans = []
    for plan in document.get("plans", []):
        if not isinstance(plan, dict):
            continue
        partitions = []
        for partition in plan.get("partitions", []):
            if not isinstance(partition, dict):
                continue
            partitions.append({key: partition.get(key) for key in (
                "partition_id", "kind", "repository_ref", "boundary_partitions",
                "input_assignments", "source_input_digests")})
        plans.append({key: plan.get(key) for key in (
            "task_id", "plan_id", "active", "estimated_tokens", "context_budget_tokens",
            "source_input_digests", "measurement") if key in plan} | {
            "partitions": sorted(partitions, key=lambda item: str(item.get("partition_id", "")))})
    return {"status": "recorded", "plans": sorted(plans, key=lambda item: str(item.get("task_id", ""))),
            "reason": ""}


def _task_packets(run: Path) -> list[dict[str, Any]]:
    engine = Engine(run)
    if not engine.ledger_exists():
        raise ValueError("acceptance manifest requires planned task packets (ledger is absent)")
    tasks = engine._rebuild(engine._read_records())
    rows = []
    for task_id in sorted(tasks):
        packet = tasks[task_id].packet
        rows.append({
            "task_id": packet.task_id,
            "task_type": packet.task_type,
            "template_id": packet.template_id,
            "template_version": packet.template_version,
            "input_digest": packet.input_digest,
            "input_digests": {name: value.digest for name, value in sorted(packet.inputs.items())},
            "context_budget_tokens": packet.context_budget_tokens,
            "depends_on": list(packet.depends_on),
        })
    return rows


def build_manifest(run_dir: str | Path) -> dict[str, Any]:
    """Build the model-independent acceptance contract for a planned run."""
    run = Path(run_dir).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    identities = identity.load(run)
    provenance = run_provenance.load(run)
    generation = provenance.get("generation", {})
    if not isinstance(generation, dict):
        generation = {}
    analyzer = provenance.get("analyzer", {})
    if not isinstance(analyzer, dict):
        analyzer = {}
    document = {
        "schema_version": SCHEMA_VERSION,
        "kind": "overview-acceptance-input-contract",
        "packet_order": "task-id-lexicographic",
        "repository_snapshots": _repository_snapshots(spec, identities, provenance),
        "analysis_language": str(generation.get("language", "")),
        "preparation": provenance.get("preparation"),
        "analyzer": {key: analyzer.get(key) for key in (
            "package", "version", "git_head", "git_branch", "dirty_detail",
            "source_state_sha256")},
        "tool_versions": provenance.get("tool_versions", []),
        "provider_execution": _provider_projection(run),
        "capability_coverage": _capability_projection(run),
        "artifact_digests": _artifact_digests(run),
        "semantic_partition_plan": _semantic_partition_projection(run),
        "task_packets": _task_packets(run),
        "source_selection_policy": {
            "version": "selection-fetch.v1",
            "artifact": "selection requirements are packet inputs and therefore covered by input_digests",
        },
    }
    document["manifest_digest"] = _digest(document)
    return document


def _validated_manifest(path: Path) -> dict[str, Any]:
    document = _load_object(path)
    if document is None:
        raise ValueError(f"acceptance manifest is absent: {path}")
    observed = document.get("manifest_digest")
    unsigned = dict(document)
    unsigned.pop("manifest_digest", None)
    if not isinstance(observed, str) or observed != _digest(unsigned):
        raise ValueError(f"acceptance manifest digest is invalid: {path}")
    return document


def freeze(run_dir: str | Path) -> Path:
    """Write the immutable pre-model manifest, or verify an identical freeze."""
    run = Path(run_dir).expanduser().resolve()
    path = run / FILENAME
    candidate = build_manifest(run)
    if path.exists():
        existing = _validated_manifest(path)
        if existing.get("manifest_digest") != candidate["manifest_digest"]:
            raise ValueError(
                "acceptance manifest inputs changed after freezing; mint a new run "
                "instead of treating the changed packet set as a model-only comparison")
        return path
    write_new_text(path, json.dumps(candidate, indent=2, sort_keys=True) + "\n")
    return path


def load_manifest(run_dir: str | Path) -> dict[str, Any]:
    return _validated_manifest(Path(run_dir).expanduser().resolve() / FILENAME)


def _is_recorded_value(value: Any) -> bool:
    return value not in (None, "", "unknown", "manual", "host", "human")


def _as_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _semantic_partition_for(packet: Any) -> dict[str, Any]:
    source = packet.inputs.get("semantic-partition.json")
    if source is None:
        return availability(None, "task is not a semantic partition")
    try:
        value = json.loads(source.content)
    except ValueError:
        return availability(None, "semantic partition descriptor is not valid JSON")
    if not isinstance(value, dict):
        return availability(None, "semantic partition descriptor is not an object")
    return availability({key: value.get(key) for key in (
        "plan_id", "partition_id", "kind", "repository_ref", "active")},
                        "semantic partition descriptor is absent")


def _current_generations(engine: Engine) -> tuple[dict[str, int], dict[tuple[str, int, int], Any],
                                                   dict[tuple[str, int, int], Any]]:
    """Return current generation counters plus claims/submissions by attempt."""
    generations: dict[str, int] = {}
    claims: dict[tuple[str, int, int], Any] = {}
    submissions: dict[tuple[str, int, int], Any] = {}
    for record in engine._read_records():
        if record.event == "created":
            generations[record.task_id] = generations.get(record.task_id, 0) + 1
        generation = generations.get(record.task_id, 0)
        if record.event == "claimed":
            claims[(record.task_id, generation, int(record.detail["attempt"]))] = record
        elif record.event == "submitted":
            result = record.detail["result"]
            submissions[(record.task_id, generation, int(result["attempt"]))] = record
    return generations, claims, submissions


def build_execution_provenance(run_dir: str | Path) -> dict[str, Any]:
    """Project current task telemetry into explicit recorded/unavailable rows."""
    run = Path(run_dir).expanduser().resolve()
    engine = Engine(run)
    if not engine.ledger_exists():
        return {"schema_version": SCHEMA_VERSION, "tasks": [],
                "summary": {"executed_task_count": 0, "model_ab_ready": False,
                            "reason": "orchestrator ledger is absent"}}
    records = engine._read_records()
    tasks = engine._rebuild(records)
    generations, claims, submissions = _current_generations(engine)
    states = engine.task_states()
    manifest = run / FILENAME
    frozen = load_manifest(run) if manifest.is_file() else None
    frozen_packets = {
        (str(row.get("task_id", "")), str(row.get("input_digest", "")))
        for row in (frozen or {}).get("task_packets", []) if isinstance(row, dict)
    }
    current_packets = {(task_id, task.packet.input_digest) for task_id, task in tasks.items()}
    packet_mismatches = sorted(
        {task_id for task_id, _digest_value in current_packets - frozen_packets}
        | {task_id for task_id, _digest_value in frozen_packets - current_packets})
    rows = []
    readiness_problems: list[str] = []
    for task_id in sorted(tasks):
        task = tasks[task_id]
        attempt = task.attempts[-1].number if task.attempts else 0
        generation = generations.get(task_id, 0)
        claim = claims.get((task_id, generation, attempt))
        submitted = submissions.get((task_id, generation, attempt))
        result = submitted.detail["result"] if submitted is not None else None
        executor = result.get("executor", {}) if isinstance(result, dict) else (
            claim.detail.get("executor", {}) if claim is not None else {})
        params = executor.get("params", {}) if isinstance(executor, dict) else {}
        params = params if isinstance(params, dict) else {}
        model = executor.get("model") if isinstance(executor, dict) else None
        timing = result.get("timing", {}) if isinstance(result, dict) else {}
        tokens = result.get("tokens") if isinstance(result, dict) else None
        mode = "api" if executor.get("kind") in {"anthropic", "openai-compatible"} else "host"
        queue_seconds = None
        if claim is not None and isinstance(timing, dict):
            started, claimed_at = _as_iso(str(timing.get("started_at", ""))), _as_iso(claim.at)
            if started is not None and claimed_at is not None:
                queue_seconds = max(0.0, (started - claimed_at).total_seconds())
        token_rows = {
            "input": availability(tokens.get("input") if isinstance(tokens, dict) else None,
                                  "provider response did not expose input token usage"),
            "cached_input": availability(tokens.get("cached_input") if isinstance(tokens, dict) else None,
                                         "provider response did not expose cached-input token usage"),
            "output": availability(tokens.get("output") if isinstance(tokens, dict) else None,
                                   "provider response did not expose output token usage"),
        }
        timing_rows = {
            "queue_wall_clock_s": availability(queue_seconds,
                                                "claim and task-start timestamps were not both recorded"),
            "request_wall_clock_s": availability(params.get("request_wall_clock_s"),
                                                  "executor did not record request wall-clock timing"),
            "model_wall_clock_s": availability(params.get("model_wall_clock_s"),
                                                "host/provider did not expose model-only wall-clock timing"),
            "task_wall_clock_s": availability(timing.get("wall_clock_s") if isinstance(timing, dict) else None,
                                               "task result did not record wall-clock timing"),
            "validation_wall_clock_s": availability(None,
                                                     "validation timing is not exposed by the task-result protocol"),
            "fetch_wall_clock_s": availability(None,
                                                "source-fetch timing is not exposed by the task-result protocol"),
            "repair_wall_clock_s": availability(None,
                                                 "repair timing is not exposed by the task-result protocol"),
        }
        row = {
            "task_id": task_id,
            "state": states.get(task_id, "pending"),
            "attempt": availability(attempt if attempt else None, "task has not been claimed"),
            "retry_count": availability(max(0, len(task.attempts) - 1) if task.attempts else None,
                                        "task has not been claimed"),
            "executor_mode": availability(mode if executor else None, "executor has not been recorded"),
            "executor_kind": availability(executor.get("kind") if isinstance(executor, dict) else None,
                                            "executor has not been recorded"),
            "model": availability(model if _is_recorded_value(model) else None,
                                  "host/provider did not expose an actual model identifier"),
            "effort": availability(params.get("effort"),
                                    "host/provider did not expose a reasoning-effort setting"),
            "temperature": availability(params.get("temperature"),
                                         "host/provider did not expose a temperature setting"),
            "tokens": token_rows,
            "timing": timing_rows,
            "context_budget_tokens": availability(task.packet.context_budget_tokens,
                                                   "task packet is missing its context budget"),
            "semantic_partition": _semantic_partition_for(task.packet),
        }
        if result is not None:
            required = (row["model"], row["temperature"], token_rows["input"],
                        token_rows["output"], timing_rows["request_wall_clock_s"],
                        timing_rows["task_wall_clock_s"])
            if any(value["status"] != "recorded" for value in required):
                readiness_problems.append(task_id)
        rows.append(row)
    manifest_state = availability(
        frozen.get("manifest_digest") if frozen is not None else None,
        "acceptance manifest has not been frozen before execution")
    executed = [row for row in rows if row["state"] in {"validated", "failed"}]
    return {
        "schema_version": SCHEMA_VERSION,
        "acceptance_manifest_digest": manifest_state,
        "tasks": rows,
        "summary": {
            "executed_task_count": len(executed),
            "model_ab_ready": bool(executed) and not readiness_problems
                              and not packet_mismatches
                              and manifest_state["status"] == "recorded",
            "provenance_limited_tasks": sorted(readiness_problems),
            "frozen_packet_mismatches": packet_mismatches,
            "reason": ("" if bool(executed) and not readiness_problems and not packet_mismatches
                       and manifest_state["status"] == "recorded"
                       else "model A/B requires the frozen packet set plus recorded model, temperature, token, request, and task timing provenance for every executed task"),
        },
    }


def write_execution_provenance(run_dir: str | Path) -> Path:
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / EXECUTION_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_artifact_text(path, json.dumps(build_execution_provenance(run), indent=2,
                                            sort_keys=True) + "\n")
    return path


def _audit_projection(run: Path) -> dict[str, Any]:
    document = _load_object(run / "consistency-audit.json")
    if document is None:
        return {"status": "unavailable", "value": None,
                "reason": "final consistency audit is absent"}
    return {"status": "recorded", "value": {
        "status": document.get("status", ""),
        "failed_count": document.get("failed_count", 0),
        "checks": [{key: row.get(key) for key in ("check", "status", "detail")}
                   for row in document.get("checks", []) if isinstance(row, dict)],
    }, "reason": ""}


def compare(base_run: str | Path, candidate_run: str | Path) -> dict[str, Any]:
    """Compare frozen inputs first, then attach the existing semantic baseline."""
    from . import parity

    base = Path(base_run).expanduser().resolve()
    candidate = Path(candidate_run).expanduser().resolve()
    left, right = load_manifest(base), load_manifest(candidate)
    comparable = (
        "repository_snapshots", "analysis_language", "preparation", "analyzer",
        "tool_versions", "provider_execution", "capability_coverage", "artifact_digests",
        "semantic_partition_plan", "task_packets", "source_selection_policy",
    )
    differences = [
        {"field": field, "base_digest": _digest(left.get(field)),
         "candidate_digest": _digest(right.get(field))}
        for field in comparable if left.get(field) != right.get(field)
    ]
    base_execution = _load_object(base / "tasks" / EXECUTION_FILENAME) or \
        build_execution_provenance(base)
    candidate_execution = _load_object(candidate / "tasks" / EXECUTION_FILENAME) or \
        build_execution_provenance(candidate)
    same_inputs = not differences and left["manifest_digest"] == right["manifest_digest"]
    ready = bool(base_execution.get("summary", {}).get("model_ab_ready")) and \
        bool(candidate_execution.get("summary", {}).get("model_ab_ready"))
    classification = ("model-only" if same_inputs and ready else
                      "functional-only" if same_inputs else "input-pipeline-difference")
    return {
        "schema_version": SCHEMA_VERSION,
        "base_manifest_digest": left["manifest_digest"],
        "candidate_manifest_digest": right["manifest_digest"],
        "classification": classification,
        "input_differences": differences,
        "model_provenance": {
            "base": base_execution.get("summary", {}),
            "candidate": candidate_execution.get("summary", {}),
        },
        "semantic_baseline": parity.compare_semantic(base, candidate),
        "final_audit": {
            "base": _audit_projection(base),
            "candidate": _audit_projection(candidate),
        },
    }
