"""Deterministic module-map.json writer (57B-113 / 57B-116, M2).

``planner.py`` composes the single global module-formation task as
task_type ``formation-proposal`` -- schemas.py's own module docstring notes
that this task's output already MIRRORS module-map.json's shape (``modules``
+ either ``candidate_rules`` or ``candidate_dispositions`` + optional
``additional_candidates``; see ``synthesis.md`` step 4). This module's ONE
job is mechanical: take the run's single validated ``formation-proposal``
output and materialize it at module-map.json's canonical path, stamped with
module_map.py's own ``MAP_SCHEMA_VERSION`` (never invented here).

No judgment happens in this module -- the formation-proposal task already
decided modules/dispositions/rules; ``write()`` only copies that decision to
disk. The existing ``finalize-module-map`` command (unchanged) then runs
``module_map.expand_candidate_rules``/``module_map.validate`` against the
file this module writes -- the zero-omission/zero-overlap gate stays
exactly where it was.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .. import module_map
from ..executor import replace_artifact_text
from ..sanitize import sanitize_text
from .results import validated_outputs

# The only top-level fields module-map.json's own contract recognizes
# (module_map.py's validate()/expand_candidate_rules()); anything else an
# executor's formation-proposal output happened to include is dropped here
# rather than silently carried into the canonical artifact.
_MODULE_MAP_FIELDS = ("modules", "candidate_rules", "candidate_dispositions",
                     "additional_candidates")
PARTITION_PLAN_SCHEMA_VERSION = 1
PARTITION_PLAN_FILENAME = "formation-partitions.json"
QUALITY_FILENAME = "module-formation-quality.json"
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


class FormationWriterError(ValueError):
    """The run's ledger does not hold exactly one validated
    ``formation-proposal`` task. Fail closed -- there is no reasonable
    partial module-map.json to write instead."""


def _formation_output(run: Path) -> Mapping[str, Any]:
    outputs = validated_outputs(run, task_type="formation-proposal")
    if not outputs:
        raise FormationWriterError(
            "no validated formation-proposal task found -- run plan-judgment "
            "and its executor to completion before write-module-map")
    plan_path = run / "tasks" / PARTITION_PLAN_FILENAME
    if plan_path.is_file():
        return _merge_partitioned_outputs(run, outputs, _load_json(plan_path))
    if len(outputs) > 1:
        raise FormationWriterError(
            "expected exactly one validated formation-proposal task, found "
            f"{len(outputs)}: {', '.join(sorted(outputs))}")
    return next(iter(outputs.values()))


def module_map_document(proposal: Mapping[str, Any]) -> dict:
    """The validated formation-proposal output, restricted to module-map.json's
    own recognized fields, with module_map.py's OWN ``MAP_SCHEMA_VERSION``
    stamped last -- so it always wins even if a stray same-named field
    somehow made it into the executor's output (schemas.py's
    formation-proposal validator does not reject unknown top-level keys)."""
    document = {key: proposal[key] for key in _MODULE_MAP_FIELDS if key in proposal}
    document["schema_version"] = module_map.MAP_SCHEMA_VERSION
    return document


def formation_task_id(partition_id: str) -> str:
    """Stable parent task id for one deterministic formation partition."""
    return f"formation-{partition_id}"


def partition_context(plan: Mapping[str, Any], partition_id: str) -> dict:
    """The bounded global context one formation work item must carry.

    The whole plan remains persisted as an audit artifact.  A model task gets
    its own candidate rows plus this concise context: global identity/merge
    order and summaries of partitions joined by an observed boundary link.
    """
    partitions = plan.get("partitions", [])
    by_id = {row.get("partition_id"): row for row in partitions if isinstance(row, dict)}
    partition = by_id.get(partition_id)
    if not isinstance(partition, dict):
        raise FormationWriterError(f"partition plan has no {partition_id!r}")
    links = [row for row in plan.get("cross_links", [])
             if isinstance(row, dict) and partition_id in row.get("partition_ids", [])]
    adjacent_ids = sorted({other for link in links for other in link.get("partition_ids", [])
                           if other != partition_id and other in by_id})
    def summary(row: Mapping[str, Any]) -> dict:
        return {
            "partition_id": row.get("partition_id", ""),
            "repository_ref": row.get("repository_ref", ""),
            "roots": row.get("roots", []),
            "candidate_count": len(row.get("candidate_ids", [])),
            "candidate_kinds": row.get("candidate_kinds", {}),
        }
    partition_id_value = partition.get("partition_id", "")
    boundary_candidate_ids = sorted({candidate_id for link in links
                                     for candidate_id in link.get("candidate_ids", [])})
    return {
        "schema_version": PARTITION_PLAN_SCHEMA_VERSION,
        "global_identity": plan.get("global_identity", {}),
        "merge_order": plan.get("merge_order", []),
        "partition": summary(partition) | {
            "candidate_ids": partition.get("candidate_ids", []),
            "cohesion_cluster_ids": partition.get("cohesion_cluster_ids", []),
            "boundary_link_ids": partition.get("boundary_link_ids", []),
        },
        "adjacent_partitions": [summary(by_id[item]) for item in adjacent_ids],
        "cross_links": links,
        "boundary_candidates": [plan.get("candidate_summaries", {}).get(candidate_id, {})
                                for candidate_id in boundary_candidate_ids],
        "cohesion_clusters": [row for row in plan.get("cohesion_clusters", [])
                              if isinstance(row, dict)
                              and partition_id_value in {
                                  plan.get("candidate_ownership", {}).get(candidate_id)
                                  for candidate_id in row.get("candidate_ids", [])
                              }],
    }


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _merge_cross_partition_module(
    existing: Mapping[str, Any], incoming: Mapping[str, Any],
) -> dict[str, Any]:
    """Mechanically reconcile one shared module proposed by multiple partitions.

    Each partition owns a disjoint candidate universe, so it may independently
    propose the same cross-boundary module.  A lower confidence or an extra
    alias is local judgment about the same definition, not a competing module
    boundary.  Preserve both signals deterministically: confidence is the most
    conservative value and aliases are a sorted set.  All other fields must
    still agree exactly, so this never chooses between competing names,
    classifications, or unrecognized semantic fields.
    """
    module_id = existing["module_id"]
    for key in sorted(set(existing) | set(incoming)):
        if key in {"confidence", "aliases"}:
            continue
        if (key not in existing or key not in incoming
                or existing[key] != incoming[key]):
            raise FormationWriterError(
                f"conflicting definition for cross-partition module {module_id!r}")
    merged = dict(existing)
    merged["confidence"] = min(
        (existing["confidence"], incoming["confidence"]),
        key=lambda value: _CONFIDENCE_RANK[value],
    )
    merged["aliases"] = sorted(set(existing["aliases"]) | set(incoming["aliases"]))
    return merged


def _merge_partitioned_outputs(run: Path, outputs: Mapping[str, Any], plan: Mapping[str, Any]) -> dict:
    """Losslessly merge independently validated formation partitions.

    The merge order comes from the persisted plan, never from model timing.
    It fails closed on an omitted/extra partition, duplicate candidate, or
    conflicting module/additional-candidate definition instead of choosing a
    convenient winner and silently erasing cross-boundary structure.
    """
    partitions = plan.get("partitions")
    merge_order = plan.get("merge_order")
    if not isinstance(partitions, list) or not isinstance(merge_order, list):
        raise FormationWriterError("formation partition plan is missing partitions or merge_order")
    by_partition = {row.get("partition_id"): row for row in partitions if isinstance(row, dict)}
    if set(by_partition) != set(merge_order) or len(by_partition) != len(partitions):
        raise FormationWriterError("formation partition plan has invalid deterministic merge_order")

    grouped: dict[str, list[tuple[str, Mapping[str, Any]]]] = defaultdict(list)
    expected_task_ids = set()
    for partition_id in merge_order:
        parent = formation_task_id(str(partition_id))
        expected_task_ids.add(parent)
        for task_id, output in outputs.items():
            if task_id == parent or task_id.startswith(parent + "-shard-"):
                if not isinstance(output, Mapping):
                    raise FormationWriterError(f"{task_id} formation output must be an object")
                grouped[str(partition_id)].append((task_id, output))
    unmatched = sorted(task_id for task_id in outputs
                       if not any(task_id == parent or task_id.startswith(parent + "-shard-")
                                  for parent in expected_task_ids))
    if unmatched:
        raise FormationWriterError(
            "formation output does not belong to the persisted partition plan: "
            + ", ".join(unmatched))
    missing = [partition_id for partition_id in merge_order if not grouped.get(str(partition_id))]
    if missing:
        raise FormationWriterError(
            "formation has no validated output for partition(s): " + ", ".join(map(str, missing)))

    modules: dict[str, Mapping[str, Any]] = {}
    dispositions: dict[str, Mapping[str, Any]] = {}
    additional_candidates: dict[str, Mapping[str, Any]] = {}
    for partition_id in merge_order:
        partition = by_partition[str(partition_id)]
        expected_candidates = set(partition.get("candidate_ids", []))
        partition_rows: list[Mapping[str, Any]] = []
        for task_id, output in sorted(grouped[str(partition_id)]):
            if "candidate_rules" in output:
                raise FormationWriterError(f"{task_id} used candidate_rules; partition outputs require explicit rows")
            task_rows = output.get("candidate_dispositions")
            if not isinstance(task_rows, list) or not all(isinstance(row, Mapping) for row in task_rows):
                raise FormationWriterError(f"{task_id} has no explicit candidate_dispositions")
            partition_rows.extend(task_rows)
            for row in output.get("modules", []):
                if not isinstance(row, Mapping) or not isinstance(row.get("module_id"), str):
                    raise FormationWriterError(f"{task_id} contains an invalid module row")
                existing = modules.get(row["module_id"])
                modules[row["module_id"]] = (
                    _merge_cross_partition_module(existing, row)
                    if existing is not None else row)
            additions = output.get("additional_candidates", [])
            if not isinstance(additions, list):
                raise FormationWriterError(f"{task_id} additional_candidates must be a list when present")
            for row in additions:
                if not isinstance(row, Mapping) or not isinstance(row.get("candidate_id"), str):
                    raise FormationWriterError(f"{task_id} contains an invalid additional candidate")
                candidate_id = row["candidate_id"]
                existing = additional_candidates.get(candidate_id)
                if existing is not None and _canonical(existing) != _canonical(row):
                    raise FormationWriterError(
                        f"conflicting definition for additional candidate {candidate_id!r}")
                additional_candidates[candidate_id] = row
        by_candidate = {row.get("candidate_id"): row for row in partition_rows}
        if len(by_candidate) != len(partition_rows):
            raise FormationWriterError(f"partition {partition_id} dispositioned a candidate more than once")
        base_rows = {candidate_id: row for candidate_id, row in by_candidate.items()
                     if candidate_id in expected_candidates}
        if set(base_rows) != expected_candidates:
            raise FormationWriterError(f"partition {partition_id} does not cover its candidate universe exactly")
        extra_rows = set(by_candidate) - expected_candidates
        known_added = set(additional_candidates)
        if not extra_rows <= known_added:
            raise FormationWriterError(
                f"partition {partition_id} dispositioned unknown candidate(s): "
                + ", ".join(sorted(map(str, extra_rows - known_added))))
        for candidate_id, row in by_candidate.items():
            if candidate_id in dispositions:
                raise FormationWriterError(f"candidate {candidate_id!r} was dispositioned in multiple partitions")
            dispositions[str(candidate_id)] = row

    expected_all = {candidate_id for partition in partitions
                    if isinstance(partition, Mapping) for candidate_id in partition.get("candidate_ids", [])}
    expected_all.update(additional_candidates)
    if set(dispositions) != expected_all:
        raise FormationWriterError("merged formation dispositions do not cover the final candidate universe")
    proposal: dict[str, Any] = {
        "modules": [dict(modules[module_id]) for module_id in sorted(modules)],
        "candidate_dispositions": [dict(dispositions[candidate_id])
                                     for candidate_id in sorted(dispositions)],
    }
    if additional_candidates:
        proposal["additional_candidates"] = [dict(additional_candidates[candidate_id])
                                                for candidate_id in sorted(additional_candidates)]
    return proposal


def write(run_dir: str | Path, *, out: str | Path | None = None) -> Path:
    """Write module-map.json (or ``out``, when given, for inspection/testing)
    from the run's single validated formation-proposal task. Returns the
    path written."""
    run = Path(run_dir).expanduser().resolve()
    proposal = _formation_output(run)
    document = module_map_document(proposal)
    out_path = Path(out).expanduser().resolve() if out else run / "module-map.json"
    replace_artifact_text(out_path, sanitize_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n"))
    return out_path


# --------------------------------------------------------------------------- #
# deterministic partition planning -- measured topology only, no boundary
# judgment. The formation model receives this plan alongside the whole
# candidate universe and remains the only component that proposes modules.
# --------------------------------------------------------------------------- #

def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise FormationWriterError(f"{path.name} must contain a JSON object")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _candidate_root(row: Mapping[str, Any]) -> str:
    """A deterministic, structural root label -- never a guessed domain name."""
    value = str(row.get("value", ""))
    kind = str(row.get("signal_kind", ""))
    if kind in {"route", "route-mount"}:
        _method, _space, value = value.partition(" ")
    if kind == "folder":
        parts = [part for part in value.split("/") if part]
    else:
        parts = [part for part in value.split("/") if part and not part.startswith("GET ")]
    if parts:
        return "/".join(parts[:2])
    return "(root)"


def _cohesion_rows(run: Path) -> tuple[list[dict], dict]:
    path = run / "cohesion-bundle.json"
    if not path.is_file():
        return [], {"available": False, "reason": "cohesion-bundle.json is absent"}
    doc = _load_json(path)
    rows = doc.get("clusters", [])
    if not isinstance(rows, list):
        raise FormationWriterError("cohesion-bundle.json clusters must be a list")
    return [row for row in rows if isinstance(row, dict)], {
        "available": True,
        "kinds": doc.get("kinds", {}),
        "limits": doc.get("limits", {}),
    }


def build_partition_plan(run_dir: str | Path) -> dict:
    """Build stable, auditable formation partitions from existing evidence.

    Primary ownership is always one candidate -> one partition.  Cohesion
    clusters can connect candidates inside a repository; cross-repository
    clusters stay explicit boundary links rather than causing an implicit
    merge.  This preserves both local working context and the global order a
    later formation judgment needs to reason about cross-boundary modules.
    """
    run = Path(run_dir).expanduser().resolve()
    candidates_doc = _load_json(run / "module-candidates.json")
    candidates = [row for row in candidates_doc.get("candidates", []) if isinstance(row, dict)]
    candidates = sorted(candidates, key=lambda row: str(row.get("candidate_id", "")))
    by_id = {str(row.get("candidate_id", "")): row for row in candidates}
    if not by_id or "" in by_id:
        raise FormationWriterError("module-candidates.json must contain non-empty candidate_id rows")

    roots = {candidate_id: _candidate_root(row) for candidate_id, row in by_id.items()}
    union = _UnionFind(sorted(by_id))
    # Root groups are a deterministic baseline even when no cohesion bundle
    # exists. They are structural path/route roots, not semantic modules.
    by_root: dict[tuple[str, str], list[str]] = defaultdict(list)
    for candidate_id, row in by_id.items():
        by_root[(str(row.get("repository_ref", "")), roots[candidate_id])].append(candidate_id)
    for members in by_root.values():
        for member in sorted(members)[1:]:
            union.union(sorted(members)[0], member)

    cohesion_rows, cohesion_meta = _cohesion_rows(run)
    cluster_rows: list[dict] = []
    for index, row in enumerate(sorted(cohesion_rows, key=lambda item: (
            str(item.get("kind", "")), [str(x) for x in item.get("members", [])]))):
        members = sorted({str(item) for item in row.get("members", []) if str(item) in by_id})
        if len(members) < 2:
            continue
        cluster_id = f"cohesion-{index:04d}"
        repositories = {str(by_id[item].get("repository_ref", "")) for item in members}
        if len(repositories) == 1:
            for member in members[1:]:
                union.union(members[0], member)
        cluster_rows.append({
            "cluster_id": cluster_id,
            "kind": str(row.get("kind", "")),
            "candidate_ids": members,
            "repository_refs": sorted(repositories),
            "evidence_refs": sorted({str(ref) for ref in row.get("evidence_refs", []) if ref}),
            "cross_repository": len(repositories) > 1,
        })

    components: dict[tuple[str, str], list[str]] = defaultdict(list)
    for candidate_id, row in by_id.items():
        components[(str(row.get("repository_ref", "")), union.find(candidate_id))].append(candidate_id)

    partitions: list[dict] = []
    ownership: dict[str, str] = {}
    for index, ((repository_ref, component_root), members) in enumerate(sorted(components.items())):
        ordered_members = sorted(members)
        partition_id = f"formation-{index:04d}"
        for candidate_id in ordered_members:
            ownership[candidate_id] = partition_id
        kinds: dict[str, int] = defaultdict(int)
        for candidate_id in ordered_members:
            kinds[str(by_id[candidate_id].get("signal_kind", ""))] += 1
        partitions.append({
            "partition_id": partition_id,
            "repository_ref": repository_ref,
            "component_root_candidate_id": component_root,
            "candidate_ids": ordered_members,
            "roots": sorted({roots[candidate_id] for candidate_id in ordered_members}),
            "candidate_kinds": dict(sorted(kinds.items())),
            "cohesion_cluster_ids": [],
        })

    by_partition = {row["partition_id"]: row for row in partitions}
    cross_links: list[dict] = []
    for cluster in cluster_rows:
        partition_ids = sorted({ownership[candidate_id] for candidate_id in cluster["candidate_ids"]})
        for partition_id in partition_ids:
            by_partition[partition_id]["cohesion_cluster_ids"].append(cluster["cluster_id"])
        if len(partition_ids) > 1:
            cross_links.append({
                "link_id": f"boundary-{len(cross_links):04d}",
                "kind": cluster["kind"],
                "partition_ids": partition_ids,
                "candidate_ids": cluster["candidate_ids"],
                "evidence_refs": cluster["evidence_refs"],
                "cross_repository": cluster["cross_repository"],
            })
    for partition in partitions:
        partition["cohesion_cluster_ids"] = sorted(partition["cohesion_cluster_ids"])
        partition["boundary_link_ids"] = [row["link_id"] for row in cross_links
                                          if partition["partition_id"] in row["partition_ids"]]

    return {
        "schema_version": PARTITION_PLAN_SCHEMA_VERSION,
        "candidate_universe_digest": _digest(candidates),
        "candidate_count": len(candidates),
        "candidate_summaries": {
            candidate_id: {
                "candidate_id": candidate_id,
                "repository_ref": row.get("repository_ref", ""),
                "signal_kind": row.get("signal_kind", ""),
                "value": row.get("value", ""),
                "evidence_refs": sorted({str(ref) for ref in row.get("evidence", []) if ref}),
                "node_ids": sorted({str(node_id) for node_id in row.get("node_ids", []) if node_id}),
            }
            for candidate_id, row in sorted(by_id.items())
        },
        "cohesion": cohesion_meta,
        "cohesion_clusters": cluster_rows,
        "partitions": partitions,
        "cross_links": cross_links,
        "candidate_ownership": dict(sorted(ownership.items())),
        "merge_order": [row["partition_id"] for row in partitions],
        "global_identity": {
            "repository_refs": sorted({str(row.get("repository_ref", "")) for row in candidates}),
            "candidate_universe_digest": _digest(candidates),
            "merge_order": [row["partition_id"] for row in partitions],
        },
    }


def write_partition_plan(run_dir: str | Path) -> Path:
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / PARTITION_PLAN_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_artifact_text(path, sanitize_text(json.dumps(
        build_partition_plan(run), indent=2, sort_keys=True) + "\n"))
    return path


# --------------------------------------------------------------------------- #
# unresolved follow-up context and structural quality gate.  This deliberately
# has no percentage/count threshold: a single evidence-bearing unresolved
# candidate can be material if graph references make it structural.
# --------------------------------------------------------------------------- #

def _model(run: Path) -> dict:
    path = run / "system-model.json"
    return _load_json(path) if path.is_file() else {"nodes": [], "edges": []}


def _candidate_contexts(run: Path, candidates: Mapping[str, dict], document: Mapping[str, Any]) -> dict[str, dict]:
    model = _model(run)
    plan = build_partition_plan(run)
    ownership = plan["candidate_ownership"]
    incident: dict[str, list[dict]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for edge in model.get("edges", []):
        if not isinstance(edge, dict):
            continue
        compact = {key: edge.get(key, "") for key in ("id", "type", "status", "src", "dst")}
        for node_id in (str(edge.get("src", "")), str(edge.get("dst", ""))):
            if node_id:
                incident[node_id].append(compact)
        if edge.get("dst"):
            incoming[str(edge["dst"])].append(str(edge.get("id", "")))
    modules_by_candidate: dict[str, set[str]] = defaultdict(set)
    candidates_by_node: dict[str, set[str]] = defaultdict(set)
    for candidate_id, candidate in candidates.items():
        for node_id in candidate.get("node_ids", []):
            candidates_by_node[str(node_id)].add(candidate_id)
    for row in document.get("candidate_dispositions", []):
        if not isinstance(row, dict):
            continue
        for module_id in row.get("module_ids", []):
            modules_by_candidate[str(row.get("candidate_id", ""))].add(str(module_id))

    contexts: dict[str, dict] = {}
    for candidate_id, candidate in candidates.items():
        node_ids = sorted({str(node_id) for node_id in candidate.get("node_ids", [])})
        related_candidates = {other for node_id in node_ids for other in candidates_by_node[node_id]}
        prior_modules = sorted({module_id for other in related_candidates
                                for module_id in modules_by_candidate.get(other, set())})
        edges = sorted({json.dumps(row, sort_keys=True): row
                        for node_id in node_ids for row in incident[node_id]}.values(),
                       key=lambda row: (str(row["type"]), str(row["id"])))
        incoming_ids = sorted({edge_id for node_id in node_ids for edge_id in incoming[node_id] if edge_id})
        links = [row["link_id"] for row in plan["cross_links"]
                 if candidate_id in row["candidate_ids"]]
        contexts[candidate_id] = {
            "candidate_id": candidate_id,
            "repository_ref": candidate.get("repository_ref", ""),
            "signal_kind": candidate.get("signal_kind", ""),
            "value": candidate.get("value", ""),
            "evidence_refs": sorted({str(ref) for ref in candidate.get("evidence", []) if ref}),
            "node_ids": node_ids,
            "immediate_graph": edges,
            "downstream_reference_edge_ids": incoming_ids,
            "prior_module_ids": prior_modules,
            "partition_id": ownership.get(candidate_id, ""),
            "boundary_link_ids": links,
        }
    return contexts


def unresolved_rows(run_dir: str | Path) -> list[dict]:
    """Every unresolved candidate with its immediate evidence/graph context."""
    run = Path(run_dir).expanduser().resolve()
    candidates_doc, document = module_map.validate(run)
    candidates = {row["candidate_id"]: row for row in candidates_doc.get("candidates", [])}
    contexts = _candidate_contexts(run, candidates, document)
    rows = []
    for disposition in document.get("candidate_dispositions", []):
        candidate_id = disposition.get("candidate_id") if isinstance(disposition, dict) else None
        if disposition.get("disposition") != "unresolved" or candidate_id not in contexts:
            continue
        rows.append({
            **contexts[candidate_id],
            "prior_reason": disposition.get("reason", ""),
            "disposition_evidence_refs": disposition.get("evidence_refs", []),
            "coverage_impact": disposition.get("coverage_impact", ""),
        })
    return sorted(rows, key=lambda row: row["candidate_id"])


def formation_quality(run_dir: str | Path, *, refined: bool) -> dict:
    run = Path(run_dir).expanduser().resolve()
    candidates_doc, document = module_map.validate(run)
    candidates = {row["candidate_id"]: row for row in candidates_doc.get("candidates", [])}
    unresolved = unresolved_rows(run)
    plan = build_partition_plan(run)
    blockers = []
    evidence_mass = 0
    downstream_references = 0
    for row in unresolved:
        evidence_bearing = bool(row["evidence_refs"] or row["node_ids"])
        high_evidence = bool(row["evidence_refs"]) and bool(row["node_ids"])
        downstream = bool(row["downstream_reference_edge_ids"])
        evidence_mass += len(row["evidence_refs"]) + len(row["node_ids"])
        downstream_references += len(row["downstream_reference_edge_ids"])
        row["evidence_bearing"] = evidence_bearing
        row["high_evidence"] = high_evidence
        row["downstream_referenced"] = downstream
        if not evidence_bearing:
            blockers.append({"candidate_id": row["candidate_id"], "reason": "unresolved candidate has no evidence context"})
        elif not refined and (high_evidence or downstream or evidence_bearing):
            blockers.append({"candidate_id": row["candidate_id"], "reason":
                             "evidence-bearing unresolved candidate requires targeted boundary resolution"})
        if refined:
            terminal_refs = row["disposition_evidence_refs"]
            coverage_impact = row["coverage_impact"]
            if not isinstance(terminal_refs, list) or not set(terminal_refs) & set(row["evidence_refs"]):
                blockers.append({"candidate_id": row["candidate_id"], "reason":
                                 "retained unresolved candidate lacks exact terminal evidence"})
            if not isinstance(coverage_impact, str) or not coverage_impact.strip():
                blockers.append({"candidate_id": row["candidate_id"], "reason":
                                 "retained unresolved candidate lacks explicit Coverage impact"})
    disposition_counts: dict[str, int] = defaultdict(int)
    for row in document.get("candidate_dispositions", []):
        if isinstance(row, dict):
            disposition_counts[str(row.get("disposition", ""))] += 1
    return {
        "schema_version": PARTITION_PLAN_SCHEMA_VERSION,
        "refined": refined,
        "authoritative": not blockers,
        "status": "passed" if not blockers else "blocked",
        "requires_boundary_resolution": bool(unresolved) and not refined,
        "diagnostics": {
            "candidate_count": len(candidates),
            "disposition_counts": dict(sorted(disposition_counts.items())),
            "unresolved_count": len(unresolved),
            "evidence_mass": evidence_mass,
            "partition_count": len(plan["partitions"]),
            "cross_link_count": len(plan["cross_links"]),
            "downstream_reference_count": downstream_references,
            "module_count": len(document.get("modules", [])),
        },
        "unresolved": unresolved,
        "blockers": blockers,
    }


def write_quality(run_dir: str | Path, *, refined: bool) -> Path:
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / QUALITY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_artifact_text(path, sanitize_text(json.dumps(
        formation_quality(run, refined=refined), indent=2, sort_keys=True) + "\n"))
    return path


def apply_boundary_resolution(run_dir: str | Path) -> bool:
    """Apply the validated targeted disposition pass without re-judging it."""
    run = Path(run_dir).expanduser().resolve()
    outputs = validated_outputs(run, task_type="boundary-resolution")
    if not outputs:
        return False
    before = unresolved_rows(run)
    expected_ids = {row["candidate_id"] for row in before}
    rows: list[dict[str, Any]] = []
    additions: list[dict[str, Any]] = []
    for task_id, output in sorted(outputs.items()):
        if not isinstance(output, dict):
            raise FormationWriterError(
                f"boundary-resolution {task_id} output must be an object")
        task_rows = output.get("dispositions")
        if not isinstance(task_rows, list) or not all(isinstance(row, dict) for row in task_rows):
            raise FormationWriterError(
                f"boundary-resolution {task_id} has no dispositions list")
        rows.extend(task_rows)
        task_additions = output.get("modules", [])
        if task_additions is not None and not isinstance(task_additions, list):
            raise FormationWriterError(
                f"boundary-resolution {task_id} modules must be a list when present")
        if not all(isinstance(row, dict) for row in task_additions or []):
            raise FormationWriterError(
                f"boundary-resolution {task_id} modules must contain objects")
        additions.extend(task_additions or [])
    by_id = {row.get("candidate_id"): row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != expected_ids:
        raise FormationWriterError(
            "boundary-resolution must disposition every unresolved candidate exactly once")
    document = _load_json(run / "module-map.json")
    modules = list(document.get("modules", []))
    existing = {row.get("module_id"): row for row in modules if isinstance(row, dict)}
    for row in additions:
        module_id = row.get("module_id") if isinstance(row, dict) else None
        if not module_id:
            raise FormationWriterError(
                f"boundary-resolution redefines existing/invalid module {module_id!r}")
        if module_id in existing:
            if _canonical(existing[module_id]) != _canonical(row):
                raise FormationWriterError(
                    f"boundary-resolution redefines existing/invalid module {module_id!r}")
            continue
        modules.append(row)
        existing[module_id] = row
    dispositions = document.get("candidate_dispositions")
    if not isinstance(dispositions, list):
        raise FormationWriterError("module-map has no expanded candidate_dispositions")
    document["modules"] = modules
    document["candidate_dispositions"] = [
        dict(by_id[row["candidate_id"]]) if isinstance(row, dict)
        and row.get("candidate_id") in by_id else row
        for row in dispositions
    ]
    path = run / "module-map.json"
    replace_artifact_text(path, sanitize_text(json.dumps(
        document, indent=2, sort_keys=True) + "\n"))
    module_map.validate(run)
    return True
