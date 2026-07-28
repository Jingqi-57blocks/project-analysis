"""Deterministic, evidence-local partitions for oversized lens packets.

``composer.compose`` is intentionally a last-resort mechanical sharder: it
can split one large input, but necessarily carries every other input on every
child.  That is useful for a single huge signal, but it is the wrong shape for
a workspace lens containing several repositories and evidence families.

This module forms semantic packets *before* that fallback is reached.  A row
of structured evidence, or a signal view, is assigned exactly once to its
repository partition or to the explicit cross-boundary partition.  Aggregate
metadata is kept once in that cross-boundary packet; local packets receive a
small digest/count descriptor instead of a copied global blob.  The planner
persists the resulting graph so a completed run can be audited without
inferring the partitioning from task-id spellings.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .composer import estimate_tokens


SCHEMA_VERSION = 1
PLAN_FILENAME = "lens-semantic-partitions.json"

# Reserve room in a source-reading lens for fetched excerpts and in every
# packet for the partition descriptor/requirements rebuilt by the planner.
# Below this point a single packet is both smaller and more coherent; at or
# above it repository/cross-boundary work is materially clearer than generic
# largest-input sharding.
ACTIVATION_FRACTION = 0.40

# A deliberately tiny test/recovery budget is useful for exercising
# composer.py's mechanical sharding, but does not leave enough fixed context
# for a meaningful semantic work item (or its source-selection companion).
# Normal overview runs use 96k by default and may explicitly use 180k.
MIN_CONTEXT_BUDGET_TOKENS = 24_000
MAX_SELECTION_INDEX_ROWS = 200

_CONTROL_INPUTS = frozenset({
    "requirements.json", "selection-requirements.json", "sharding",
})
_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Partition:
    """One actual work item's local evidence and audit descriptor."""

    partition_id: str
    kind: str
    repository_ref: str
    inputs: Mapping[str, str]
    descriptor: Mapping[str, Any]


@dataclass(frozen=True)
class Plan:
    """The stable semantic graph for one logical lens task."""

    task_id: str
    plan_id: str
    active: bool
    partitions: tuple[Partition, ...]
    source_input_digests: Mapping[str, str]
    source_input_bytes: Mapping[str, int]
    estimated_tokens: int
    context_budget_tokens: int

    def to_dict(self) -> dict[str, Any]:
        partitioned_bytes = sum(
            len(content.encode("utf-8")) for partition in self.partitions
            for input_id, content in partition.inputs.items()
            if input_id not in _CONTROL_INPUTS and input_id != "semantic-partition.json")
        source_bytes = sum(self.source_input_bytes.values())
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "active": self.active,
            "estimated_tokens": self.estimated_tokens,
            "context_budget_tokens": self.context_budget_tokens,
            "source_input_digests": dict(sorted(self.source_input_digests.items())),
            "measurement": {
                "estimated_tokens_before_partitioning": self.estimated_tokens,
                "source_evidence_bytes": source_bytes,
                "partitioned_evidence_bytes": partitioned_bytes,
                "repeated_evidence_bytes": max(0, partitioned_bytes - source_bytes),
            },
            "partitions": [dict(partition.descriptor) for partition in self.partitions],
        }


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fragment(value: str) -> str:
    slug = _SLUG_UNSAFE.sub("-", value.lower()).strip("-")[:16].strip("-") or "x"
    return f"{slug}-{_digest(value)[:8]}"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _row_repositories(row: Any, known: set[str]) -> set[str]:
    """Return explicit repository ownership for a bounded evidence row.

    We deliberately use only typed ownership fields rather than guessing from
    prose, filenames, or an LLM classification.  A row mentioning more than
    one known repository is cross-boundary; a row with no typed owner remains
    visible in the cross-boundary packet instead of being silently discarded.
    """
    if not isinstance(row, dict):
        return set()
    values: list[Any] = [row.get("repository_ref"), row.get("repository")]
    values.extend(row.get("repository_refs", []) if isinstance(row.get("repository_refs"), list)
                  else [])
    values.extend(row.get("repositories", []) if isinstance(row.get("repositories"), list)
                  else [])
    return {value for value in values if isinstance(value, str) and value in known}


def _compact_scalar(value: Any, *, limit: int = 240) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_compact_scalar(item, limit=limit) for item in value[:12]]
    if isinstance(value, dict):
        return {str(key): _compact_scalar(item, limit=limit)
                for key, item in list(sorted(value.items()))[:12]}
    return str(value)[:limit]


def _selection_locator(row: Any, index: int) -> dict[str, Any]:
    """A source-selection index row, retaining locators rather than blobs."""
    if not isinstance(row, dict):
        return {"index": index, "value": _compact_scalar(row)}
    useful = (
        "candidate_id", "id", "node_id", "repository_ref", "repository_refs",
        "kind", "label", "value", "path", "method", "signal_kind", "status",
        "evidence", "evidence_refs", "refs", "source_ref", "test_files",
        "ci_configs", "package_json", "go_mod_module",
    )
    result = {"index": index}
    for key in useful:
        if key in row:
            result[key] = _compact_scalar(row[key])
    if len(result) == 1:
        result["keys"] = sorted(str(key) for key in row)[:24]
    return result


def _selection_index_input(input_id: str, content: str) -> str:
    """Keep source selection local and compact under the original input id.

    Selection requirements refer to typed input *names*.  Retaining those
    names lets the schema keep enforcing role/input correspondence while the
    executor receives an index of citable locations rather than a duplicate
    of the final lens's full evidence payload.
    """
    def bounded(rows: list[dict[str, Any]], total: int) -> list[dict[str, Any]]:
        if total <= MAX_SELECTION_INDEX_ROWS:
            return rows
        head = MAX_SELECTION_INDEX_ROWS * 3 // 5
        tail = MAX_SELECTION_INDEX_ROWS - head
        return ([{"index_summary": {"total_rows": total,
                                     "included_rows": MAX_SELECTION_INDEX_ROWS,
                                     "truncated": True,
                                     "source_digest": _digest(content)}}]
                + rows[:head] + rows[-tail:])

    try:
        value = json.loads(content)
    except ValueError:
        lines = content.splitlines()
        rows = [{"input_id": input_id, "line": number, "text": line[:240]}
                for number, line in enumerate(lines, start=1)]
        return _json(bounded(rows, len(rows)))
    if isinstance(value, list):
        rows = [_selection_locator(row, index) for index, row in enumerate(value)]
        return _json(bounded(rows, len(rows)))
    if isinstance(value, dict):
        return _json([{
            "input_id": input_id,
            "kind": "metadata",
            "keys": sorted(str(key) for key in value),
            "digest": _digest(content),
        }])
    return _json([{"input_id": input_id, "value": _compact_scalar(value)}])


def compact_selection_inputs(inputs: Mapping[str, str]) -> dict[str, str]:
    """Build compact selection indexes while retaining every typed input id."""
    return {input_id: (content if input_id == "semantic-partition.json"
                       else _selection_index_input(input_id, content))
            for input_id, content in sorted(inputs.items())
            if input_id not in _CONTROL_INPUTS}


def _partition_descriptor(*, task_id: str, plan_id: str, partition_id: str,
                          kind: str, repository_ref: str, assignments: list[dict[str, Any]],
                          source_digests: Mapping[str, str], boundary_partitions: Sequence[str],
                          active: bool) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "parent_task_id": task_id,
        "partition_id": partition_id,
        "kind": kind,
        "repository_ref": repository_ref,
        "active": active,
        "input_assignments": assignments,
        "source_input_digests": dict(sorted(source_digests.items())),
        "boundary_partitions": list(boundary_partitions),
    }


def build_plan(*, task_id: str, inputs: Mapping[str, str],
               repository_refs: Sequence[str], signal_owners: Mapping[str, str],
               instructions: str, context_budget_tokens: int) -> Plan:
    """Return a deterministic semantic graph for a logical lens task.

    The inactive one-node graph is persisted too, but does not alter the
    packet.  This preserves existing small-task behavior and makes activation
    an observable consequence of measured input size rather than a heuristic
    hidden in executor behavior.
    """
    evidence = {name: content for name, content in inputs.items()
                if name not in _CONTROL_INPUTS}
    source_digests = {name: _digest(content) for name, content in evidence.items()}
    source_bytes = {name: len(content.encode("utf-8")) for name, content in evidence.items()}
    estimated = estimate_tokens(instructions) + sum(estimate_tokens(value)
                                                    for value in evidence.values())
    canonical = _json({"task_id": task_id, "source_input_digests": source_digests,
                       "repositories": sorted(repository_refs),
                       "context_budget_tokens": context_budget_tokens})
    plan_id = _digest(canonical)[:20]
    active = context_budget_tokens >= MIN_CONTEXT_BUDGET_TOKENS \
        and estimated >= max(1, int(context_budget_tokens * ACTIVATION_FRACTION)) \
        and bool(repository_refs)
    if not active:
        descriptor = _partition_descriptor(
            task_id=task_id, plan_id=plan_id, partition_id="whole", kind="whole",
            repository_ref="", assignments=[{
                "input_id": name, "mode": "whole", "source_digest": source_digests[name],
            } for name in sorted(evidence)], source_digests=source_digests,
            boundary_partitions=(), active=False)
        return Plan(task_id=task_id, plan_id=plan_id, active=False,
                    partitions=(Partition("whole", "whole", "", dict(inputs), descriptor),),
                    source_input_digests=source_digests, source_input_bytes=source_bytes,
                    estimated_tokens=estimated,
                    context_budget_tokens=context_budget_tokens)

    repositories = sorted(set(repository_refs))
    known = set(repositories)
    partition_ids = {repo: f"repo-{_fragment(repo)}" for repo in repositories}
    raw_inputs: dict[str, dict[str, str]] = {repo: {} for repo in repositories}
    assignments: dict[str, list[dict[str, Any]]] = {repo: [] for repo in repositories}
    cross_inputs: dict[str, str] = {}
    cross_assignments: list[dict[str, Any]] = []

    def add(repo: str | None, input_id: str, value: Any, *, mode: str,
            total_rows: int | None = None, assigned_rows: int | None = None,
            raw_content: bool = False) -> None:
        target_inputs = cross_inputs if repo is None else raw_inputs[repo]
        target_assignments = cross_assignments if repo is None else assignments[repo]
        target_inputs[input_id] = value if raw_content else _json(value)
        row: dict[str, Any] = {
            "input_id": input_id, "mode": mode, "source_digest": source_digests[input_id],
        }
        if total_rows is not None:
            row["total_rows"] = total_rows
        if assigned_rows is not None:
            row["assigned_rows"] = assigned_rows
        target_assignments.append(row)

    for input_id, content in sorted(evidence.items()):
        if input_id.startswith("signals/"):
            owner = signal_owners.get(input_id)
            add(owner if owner in known else None, input_id, content, mode="signal-view",
                raw_content=True)
            continue
        try:
            value = json.loads(content)
        except ValueError:
            add(None, input_id, content, mode="untyped-text", raw_content=True)
            continue
        if isinstance(value, list):
            buckets: dict[str | None, list[Any]] = {repo: [] for repo in repositories}
            buckets[None] = []
            for row in value:
                owners = _row_repositories(row, known)
                destination = next(iter(owners)) if len(owners) == 1 else None
                buckets[destination].append(row)
            for repo in repositories:
                if buckets[repo]:
                    add(repo, input_id, buckets[repo], mode="repository-local",
                        total_rows=len(value), assigned_rows=len(buckets[repo]))
            if buckets[None]:
                add(None, input_id, buckets[None], mode="cross-boundary",
                    total_rows=len(value), assigned_rows=len(buckets[None]))
            if not any(buckets.values()):
                # Empty evidence is still an explicit scoped negative input;
                # record/hand it to the cross-boundary partition so the plan
                # accounts for every source family rather than treating an
                # empty list as an invisible omission.
                add(None, input_id, [], mode="empty", total_rows=0, assigned_rows=0)
            continue
        if isinstance(value, dict):
            # A meta object can be important context, but it must appear only
            # once.  Local partitions receive its exact digest/count through
            # their semantic descriptor, not a byte-for-byte duplicate.
            add(None, input_id, value, mode="global-metadata")
            continue
        add(None, input_id, value, mode="scalar")

    partitions: list[Partition] = []
    all_partition_ids = [partition_ids[repo] for repo in repositories]
    if cross_inputs:
        all_partition_ids.append("cross-boundary")
    for repo in repositories:
        if not raw_inputs[repo]:
            continue
        partition_id = partition_ids[repo]
        descriptor = _partition_descriptor(
            task_id=task_id, plan_id=plan_id, partition_id=partition_id,
            kind="repository", repository_ref=repo, assignments=assignments[repo],
            source_digests=source_digests,
            boundary_partitions=[pid for pid in all_partition_ids if pid != partition_id],
            active=True)
        local_inputs = dict(raw_inputs[repo])
        local_inputs["semantic-partition.json"] = _json(descriptor)
        partitions.append(Partition(partition_id, "repository", repo, local_inputs, descriptor))
    if cross_inputs:
        descriptor = _partition_descriptor(
            task_id=task_id, plan_id=plan_id, partition_id="cross-boundary",
            kind="cross-boundary", repository_ref="", assignments=cross_assignments,
            source_digests=source_digests,
            boundary_partitions=[pid for pid in all_partition_ids if pid != "cross-boundary"],
            active=True)
        cross_inputs["semantic-partition.json"] = _json(descriptor)
        partitions.append(Partition("cross-boundary", "cross-boundary", "",
                                    cross_inputs, descriptor))
    if not partitions:
        # Defensive fail-closed fallback: all evidence is still represented
        # exactly once in an explicit cross-boundary partition.
        descriptor = _partition_descriptor(
            task_id=task_id, plan_id=plan_id, partition_id="cross-boundary",
            kind="cross-boundary", repository_ref="", assignments=[],
            source_digests=source_digests, boundary_partitions=(), active=True)
        fallback = dict(evidence)
        fallback["semantic-partition.json"] = _json(descriptor)
        partitions.append(Partition("cross-boundary", "cross-boundary", "", fallback, descriptor))

    return Plan(task_id=task_id, plan_id=plan_id, active=True,
                partitions=tuple(partitions), source_input_digests=source_digests,
                source_input_bytes=source_bytes, estimated_tokens=estimated,
                context_budget_tokens=context_budget_tokens)


def task_id_for_partition(task_id: str, partition: Partition, *, active: bool) -> str:
    """Keep old task ids for small one-packet work; name active work items."""
    if not active:
        return task_id
    # TaskPacket ids are capped at 63 characters.  Reserve enough space for
    # both the paired ``-select`` and a generic-composer ``-shard-32`` suffix
    # so the safety-net sharder remains available inside every local packet.
    suffix = f"-sp-{_digest(partition.partition_id)[:10]}"
    limit = 63 - len("-select-shard-32")
    prefix = task_id[:limit - len(suffix)].rstrip("-")
    return f"{prefix}{suffix}"


def write_manifest(run_dir: str | Path, plans: Sequence[Plan]) -> Path:
    """Persist an auditable graph for the logical lens tasks planned so far."""
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / PLAN_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            value = json.loads(path.read_text("utf-8"))
            if isinstance(value, dict):
                existing = value
        except ValueError:
            # Do not silently overwrite an invalid artifact: a run with a
            # corrupt plan cannot honestly claim its partition graph.
            raise ValueError(f"invalid semantic partition manifest: {path}")
    rows = {row.get("task_id"): row for row in existing.get("plans", [])
            if isinstance(row, dict) and isinstance(row.get("task_id"), str)}
    rows.update({plan.task_id: plan.to_dict() for plan in plans})
    document = {
        "schema_version": SCHEMA_VERSION,
        "plans": [rows[key] for key in sorted(rows)],
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return path


def validate_manifest(run_dir: str | Path) -> list[str]:
    """Validate the persisted semantic graph against immutable ledger packets.

    This deliberately checks structural ownership rather than narrative output:
    every active descriptor must be present in a real packet, each descriptor
    must be byte-for-byte the planned partition, and every list input's row
    counts must be assigned exactly once.  Generic composer children may carry
    the same *local* descriptor, so descriptors are de-duplicated by
    ``(parent_task_id, partition_id)`` before coverage is checked.
    """
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / PLAN_FILENAME
    errors: list[str] = []
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"semantic partition manifest unreadable: {exc}"]
    rows = document.get("plans") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        return ["semantic partition manifest has no plans list"]
    active_plans = {row.get("task_id"): row for row in rows
                    if isinstance(row, dict) and row.get("active") is True
                    and isinstance(row.get("task_id"), str)}
    if not active_plans:
        return []

    from .engine import Engine
    actual: dict[tuple[str, str], dict[str, Any]] = {}
    for record in Engine(run)._read_records():
        if record.event != "created":
            continue
        raw = record.detail.get("task", {}).get("inputs", {}).get("semantic-partition.json")
        content = raw.get("content") if isinstance(raw, dict) else None
        if not isinstance(content, str):
            continue
        try:
            descriptor = json.loads(content)
        except ValueError:
            errors.append(f"{record.task_id}: invalid semantic partition descriptor")
            continue
        if not isinstance(descriptor, dict) or descriptor.get("active") is not True:
            continue
        parent = descriptor.get("parent_task_id")
        partition_id = descriptor.get("partition_id")
        if not isinstance(parent, str) or not isinstance(partition_id, str):
            errors.append(f"{record.task_id}: semantic descriptor lacks stable identity")
            continue
        key = (parent, partition_id)
        existing = actual.get(key)
        if existing is not None and _json(existing) != _json(descriptor):
            errors.append(f"{parent}/{partition_id}: generic children disagree on partition descriptor")
        actual[key] = descriptor

    for task_id, plan in active_plans.items():
        expected = {
            (task_id, descriptor.get("partition_id")): descriptor
            for descriptor in plan.get("partitions", []) if isinstance(descriptor, dict)
            and isinstance(descriptor.get("partition_id"), str)
        }
        observed = {key: value for key, value in actual.items() if key[0] == task_id}
        if set(expected) != set(observed):
            errors.append(f"{task_id}: planned/packet partition ids differ")
            continue
        for key in expected:
            if _json(expected[key]) != _json(observed[key]):
                errors.append(f"{task_id}/{key[1]}: packet descriptor differs from manifest")

        grouped: dict[str, list[dict[str, Any]]] = {}
        for descriptor in expected.values():
            for assignment in descriptor.get("input_assignments", []):
                if isinstance(assignment, dict) and isinstance(assignment.get("input_id"), str):
                    grouped.setdefault(assignment["input_id"], []).append(assignment)
        source_ids = set(plan.get("source_input_digests", {}))
        if set(grouped) != source_ids:
            errors.append(f"{task_id}: partition assignments do not cover the exact source input set")
        for input_id, assignments in grouped.items():
            totals = {row.get("total_rows") for row in assignments if "total_rows" in row}
            if totals:
                if len(totals) != 1 or not all(isinstance(value, int) and value >= 0
                                                for value in totals):
                    errors.append(f"{task_id}/{input_id}: inconsistent list totals")
                    continue
                if any(not isinstance(row.get("assigned_rows"), int) for row in assignments) \
                        or sum(row["assigned_rows"] for row in assignments) != next(iter(totals)):
                    errors.append(f"{task_id}/{input_id}: list rows are not assigned exactly once")
            elif len(assignments) != 1:
                errors.append(f"{task_id}/{input_id}: non-list input has multiple owners")
        measurement = plan.get("measurement")
        if not isinstance(measurement, dict) or any(
                not isinstance(measurement.get(name), int) or measurement[name] < 0
                for name in ("source_evidence_bytes", "partitioned_evidence_bytes",
                             "repeated_evidence_bytes")):
            errors.append(f"{task_id}: partition byte measurement is invalid")
        elif measurement["repeated_evidence_bytes"] != max(
                0, measurement["partitioned_evidence_bytes"] - measurement["source_evidence_bytes"]):
            errors.append(f"{task_id}: partition repeated-byte measurement is inconsistent")
    return errors
