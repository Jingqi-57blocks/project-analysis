"""Structured module-candidate accounting and inferred module materialization.

Discovery emits evidence signals, not business boundaries.  This module turns
those signals into a stable candidate universe, validates synthesis's explicit
one-time disposition of every candidate, and then adds only the accepted module
boundaries to the canonical system model as inferred nodes.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .sanitize import sanitize_text
from .executor import replace_artifact_text
from .system_model import ids
from .system_model.builder import ModelBuilder
from .targetspec import TargetSpec

CANDIDATE_SCHEMA_VERSION = "1.0.0"
MAP_SCHEMA_VERSION = "1.0.0"
DISPOSITIONS = (
    "standalone", "merged", "platform", "shared-infrastructure",
    "excluded", "unresolved",
)
CLASSIFICATIONS = ("business", "platform", "shared-infra", "unresolved")
_MODULE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_ADDED_CANDIDATE_ID = re.compile(
    r"^mc-added-[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def _load(path: Path) -> dict:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _candidate_id(repo_id: str, kind: str, value: str) -> str:
    digest = hashlib.sha256(f"{repo_id}\0{kind}\0{value}".encode()).hexdigest()[:16]
    return f"mc-{digest}"


def _citation(repo_id: str, head: str, position: str) -> str:
    return ids.make_citation(repo_id, head, position) if position else ""


def _node_index(model: dict) -> dict[tuple[str, str], list[str]]:
    index: dict[tuple[str, str], list[str]] = {}
    for node in model.get("nodes", []):
        repo_id = str(node.get("repo_id", ""))
        kind = str(node.get("kind", ""))
        label = str(node.get("label", ""))
        attrs = node.get("attrs", {})
        keys = {(kind, label)}
        if kind == "route":
            keys.add(("route", str(attrs.get("path", ""))))
        if kind == "data-store":
            keys.add(("table", str(attrs.get("table", label))))
        if kind == "file":
            keys.add(("file", label))
        for key_kind, value in keys:
            index.setdefault((repo_id, f"{key_kind}:{value}"), []).append(node["id"])
    return index


def build_candidates(run_dir: str | Path, model: dict | None = None) -> dict:
    run = Path(run_dir).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    report = _load(run / "discovery-report.json")
    model = model or _load(run / "system-model.json")
    heads = {repo.repo_id: repo.git.head for repo in spec.repos}
    node_index = _node_index(model)
    file_nodes_by_repo: dict[str, list[tuple[str, str]]] = {}
    for node in model.get("nodes", []):
        if node.get("kind") == "file":
            file_nodes_by_repo.setdefault(str(node.get("repo_id", "")), []).append(
                (str(node.get("label", "")), str(node.get("id", ""))))
    candidates: dict[str, dict] = {}

    def add(repo_id: str, kind: str, value: str, evidence: list[str],
            node_keys: list[tuple[str, str]], direct_nodes: list[str] | None = None) -> None:
        if not value:
            return
        candidate_id = _candidate_id(repo_id, kind, value)
        row = candidates.setdefault(candidate_id, {
            "candidate_id": candidate_id,
            "repo_id": repo_id,
            "signal_kind": kind,
            "value": value,
            "evidence": [],
            "node_ids": [],
        })
        row["evidence"] = sorted(set(row["evidence"]) | {x for x in evidence if x})
        linked = set(row["node_ids"]) | set(direct_nodes or [])
        for key_kind, key_value in node_keys:
            linked.update(node_index.get((repo_id, f"{key_kind}:{key_value}"), []))
        row["node_ids"] = sorted(linked)

    for block in sorted(report.get("repos", []), key=lambda row: row.get("repo_id", "")):
        repo_id = str(block.get("repo_id", ""))
        head = heads.get(repo_id, "")
        signals = block.get("module_signals", {})
        for folder in signals.get("folders", []):
            folder = str(folder)
            prefix_nodes = [node_id for label, node_id in file_nodes_by_repo.get(repo_id, [])
                            if label == folder or label.startswith(folder + "/")]
            add(repo_id, "folder", str(folder),
                [f"discovery-report.json:repos[{repo_id}].module_signals.folders"],
                [], prefix_nodes)
        for route in signals.get("routes", []):
            value = str(route.get("path", ""))
            add(repo_id, "route", value,
                [_citation(repo_id, head, str(route.get("evidence", "")))],
                [("route", value)])
        for table in signals.get("tables", []):
            value = str(table.get("name", ""))
            add(repo_id, "table", value,
                [_citation(repo_id, head, str(table.get("evidence", "")))],
                [("table", value)])
        for path in signals.get("api_configs", []):
            value = str(path)
            add(repo_id, "api-config", value,
                [_citation(repo_id, head, f"{value}:1")], [("file", value)])

    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "project_id": report.get("project_id", ""),
        "candidate_count": len(candidates),
        "candidates": [candidates[key] for key in sorted(candidates)],
        "limitations": [
            "Candidate accounting covers mechanically surfaced route, folder, table, "
            "and committed API-config signals; it does not claim all business modules "
            "were discovered.",
        ],
    }


def write_candidates(run_dir: str | Path, model: dict | None = None) -> Path:
    run = Path(run_dir).expanduser().resolve()
    out = run / "module-candidates.json"
    replace_artifact_text(out, sanitize_text(json.dumps(
        build_candidates(run, model), indent=2, sort_keys=True) + "\n"))
    return out


def validate(run_dir: str | Path) -> tuple[dict, dict]:
    run = Path(run_dir).expanduser().resolve()
    candidates_doc = _load(run / "module-candidates.json")
    module_doc = _load(run / "module-map.json")
    if module_doc.get("schema_version") != MAP_SCHEMA_VERSION:
        raise ValueError(
            f"module-map.json schema_version must be {MAP_SCHEMA_VERSION!r}")
    mechanical = {row["candidate_id"]: row
                  for row in candidates_doc.get("candidates", [])}
    repo_ids = {repo.repo_id for repo in TargetSpec.load(run / "targets.json").repos}
    candidates = dict(mechanical)
    additional = module_doc.get("additional_candidates", [])
    if not isinstance(additional, list):
        raise ValueError("module-map.json additional_candidates must be a list")
    for index, row in enumerate(additional):
        if not isinstance(row, dict):
            raise ValueError(f"additional_candidates[{index}] must be an object")
        candidate_id = row.get("candidate_id", "")
        if not isinstance(candidate_id, str) or not _ADDED_CANDIDATE_ID.fullmatch(
                candidate_id):
            raise ValueError(
                f"additional_candidates[{index}].candidate_id must start mc-added-")
        if candidate_id in candidates:
            raise ValueError(f"duplicate added candidate_id {candidate_id!r}")
        evidence = row.get("evidence", [])
        if not isinstance(evidence, list) or not evidence or not all(
                isinstance(item, str) and item for item in evidence):
            raise ValueError(f"additional candidate {candidate_id!r} needs evidence")
        if row.get("repo_id") not in repo_ids or not row.get("value"):
            raise ValueError(f"additional candidate {candidate_id!r} needs repo_id and value")
        node_ids = row.get("node_ids", [])
        if not isinstance(node_ids, list) or not all(isinstance(item, str)
                                                     for item in node_ids):
            raise ValueError(f"additional candidate {candidate_id!r}.node_ids must be strings")
        candidates[candidate_id] = {
            "candidate_id": candidate_id,
            "repo_id": row["repo_id"],
            "signal_kind": "synthesis-added",
            "value": row["value"],
            "evidence": evidence,
            "node_ids": node_ids,
        }
    dispositions = module_doc.get("candidate_dispositions")
    modules = module_doc.get("modules")
    if not isinstance(dispositions, list) or not isinstance(modules, list):
        raise ValueError("module-map.json requires candidate_dispositions and modules lists")

    seen: dict[str, dict] = {}
    for index, row in enumerate(dispositions):
        if not isinstance(row, dict):
            raise ValueError(f"candidate_dispositions[{index}] must be an object")
        candidate_id = row.get("candidate_id", "")
        disposition = row.get("disposition", "")
        if candidate_id not in candidates:
            raise ValueError(f"candidate_dispositions[{index}] has unknown candidate_id")
        if candidate_id in seen:
            raise ValueError(f"candidate {candidate_id!r} was dispositioned more than once")
        if disposition not in DISPOSITIONS:
            raise ValueError(f"candidate {candidate_id!r} has unsupported disposition")
        if not isinstance(row.get("reason", ""), str) or not row.get("reason", "").strip():
            raise ValueError(f"candidate {candidate_id!r} needs an evidence-bounded reason")
        module_ids = row.get("module_ids", [])
        if not isinstance(module_ids, list) or not all(isinstance(x, str) for x in module_ids):
            raise ValueError(f"candidate {candidate_id!r}.module_ids must be a string list")
        if disposition in {"standalone", "merged", "platform", "shared-infrastructure"}:
            if len(module_ids) != 1:
                raise ValueError(f"candidate {candidate_id!r} must map to exactly one module")
        elif module_ids:
            raise ValueError(f"{disposition} candidate {candidate_id!r} cannot map to a module")
        seen[candidate_id] = row
    missing = sorted(set(candidates) - set(seen))
    if missing:
        raise ValueError(f"module-map.json omits {len(missing)} candidate(s): {missing[:5]}")

    module_rows: dict[str, dict] = {}
    for index, row in enumerate(modules):
        if not isinstance(row, dict):
            raise ValueError(f"modules[{index}] must be an object")
        module_id = row.get("module_id", "")
        if not isinstance(module_id, str) or not _MODULE_ID.fullmatch(module_id):
            raise ValueError(f"modules[{index}].module_id must be a stable kebab-case slug")
        if module_id in module_rows:
            raise ValueError(f"duplicate module_id {module_id!r}")
        if not isinstance(row.get("name", ""), str) or not row.get("name", "").strip():
            raise ValueError(f"module {module_id!r}.name must be non-empty")
        if row.get("classification") not in CLASSIFICATIONS:
            raise ValueError(f"module {module_id!r} has unsupported classification")
        confidence = row.get("confidence")
        if confidence not in {"high", "medium", "low"}:
            raise ValueError(f"module {module_id!r}.confidence must be high|medium|low")
        aliases = row.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(x, str) for x in aliases):
            raise ValueError(f"module {module_id!r}.aliases must be a string list")
        module_rows[module_id] = row

    referenced: dict[str, set[str]] = {module_id: set() for module_id in module_rows}
    for candidate_id, row in seen.items():
        for module_id in row.get("module_ids", []):
            if module_id not in module_rows:
                raise ValueError(f"candidate {candidate_id!r} references unknown module {module_id!r}")
            referenced[module_id].add(candidate_id)
            expected_class = {"platform": "platform",
                              "shared-infrastructure": "shared-infra"}.get(
                                  row["disposition"])
            if expected_class and module_rows[module_id]["classification"] != expected_class:
                raise ValueError(
                    f"candidate {candidate_id!r} disposition {row['disposition']} "
                    f"contradicts module {module_id!r} classification")
    empty = sorted(module_id for module_id, refs in referenced.items() if not refs)
    if empty:
        raise ValueError(f"module(s) have no candidate lineage: {empty}")
    universe = dict(candidates_doc)
    universe["mechanical_candidate_count"] = len(mechanical)
    universe["additional_candidate_count"] = len(additional)
    universe["candidate_count"] = len(candidates)
    universe["candidates"] = [candidates[key] for key in sorted(candidates)]
    return universe, module_doc


def load_into(builder: ModelBuilder, run_dir: str | Path, project_id: str) -> dict:
    run = Path(run_dir).expanduser().resolve()
    if not (run / "module-map.json").is_file():
        return {"present": False, "modules": 0, "candidates": 0,
                "dispositions": {}}
    candidates_doc, module_doc = validate(run)
    candidates = {row["candidate_id"]: row
                  for row in candidates_doc.get("candidates", [])}
    disposition_by_module: dict[str, list[str]] = {}
    disposition_counts: dict[str, int] = {}
    for row in module_doc["candidate_dispositions"]:
        disposition_counts[row["disposition"]] = \
            disposition_counts.get(row["disposition"], 0) + 1
        for module_id in row.get("module_ids", []):
            disposition_by_module.setdefault(module_id, []).append(row["candidate_id"])

    for module in sorted(module_doc["modules"], key=lambda row: row["module_id"]):
        module_id = module["module_id"]
        candidate_ids = sorted(disposition_by_module.get(module_id, []))
        evidence = sorted({cite for cid in candidate_ids
                           for cite in candidates[cid].get("evidence", [])})
        confidence = {"high": 0.9, "medium": 0.6, "low": 0.3}[module["confidence"]]
        node_id = builder.add_node(
            "module", [project_id, module_id], label=module.get("name", module_id),
            status="inferred", producer="synthesis/module-map",
            evidence=evidence, confidence=confidence,
            evidence_basis="inferred-linkage",
            attrs={"module_id": module_id,
                   "classification": module["classification"],
                   "aliases": sorted(set(module.get("aliases", []))),
                   "candidate_ids": candidate_ids})
        linked_nodes = sorted({node for cid in candidate_ids
                               for node in candidates[cid].get("node_ids", [])})
        for target_node in linked_nodes:
            if builder.has_node(target_node):
                builder.add_edge(
                    "containment", node_id, target_node, status="inferred",
                    producer="synthesis/module-map", evidence=evidence,
                    confidence=confidence, evidence_basis="inferred-linkage",
                    discriminator=module_id)
    return {
        "present": True,
        "modules": len(module_doc["modules"]),
        "candidates": len(candidates),
        "dispositions": dict(sorted(disposition_counts.items())),
    }
