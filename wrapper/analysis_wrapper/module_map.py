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
_RULE_SELECTOR_FIELDS = {
    "candidate_ids", "repo_ids", "signal_kinds", "values",
    "value_prefixes", "evidence_path_prefixes", "node_ids",
}
_RULE_FIELDS = {
    "rule_id", "selectors", "remaining", "disposition", "module_ids", "reason",
}


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


def _candidate_universe(run: Path, candidates_doc: dict,
                        module_doc: dict) -> tuple[dict[str, dict], int, int]:
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
    return candidates, len(mechanical), len(additional)


def _evidence_paths(candidate: dict) -> list[str]:
    return [path for evidence in candidate.get("evidence", [])
            if (path := ids.citation_file(str(evidence)))]


def _selector_matches(selector: dict, candidate: dict) -> bool:
    def string_list(field: str) -> list[str]:
        value = selector.get(field, [])
        if not isinstance(value, list) or not value or not all(
                isinstance(item, str) and item for item in value):
            raise ValueError(f"candidate rule selector {field!r} must be a non-empty string list")
        return value

    unknown = set(selector) - _RULE_SELECTOR_FIELDS
    if unknown:
        raise ValueError(f"candidate rule selector has unsupported fields: {sorted(unknown)}")
    if not selector:
        raise ValueError("candidate rule selector cannot be empty")
    if "candidate_ids" in selector and candidate["candidate_id"] not in string_list(
            "candidate_ids"):
        return False
    if "repo_ids" in selector and candidate.get("repo_id") not in string_list("repo_ids"):
        return False
    if "signal_kinds" in selector and candidate.get("signal_kind") not in string_list(
            "signal_kinds"):
        return False
    value = str(candidate.get("value", ""))
    if "values" in selector and value not in string_list("values"):
        return False
    if "value_prefixes" in selector and not any(
            value.startswith(prefix) for prefix in string_list("value_prefixes")):
        return False
    if "evidence_path_prefixes" in selector and not any(
            path.startswith(prefix)
            for path in _evidence_paths(candidate)
            for prefix in string_list("evidence_path_prefixes")):
        return False
    if "node_ids" in selector and not set(candidate.get("node_ids", [])).intersection(
            string_list("node_ids")):
        return False
    return True


def expand_candidate_rules(run_dir: str | Path) -> Path:
    """Expand compact synthesis selectors into canonical per-candidate rows.

    Selectors only filter exact structured candidate fields. They do not infer
    business meaning. Every candidate must match exactly one explicit row or
    exactly one rule; overlaps and omissions fail closed.
    """
    run = Path(run_dir).expanduser().resolve()
    path = run / "module-map.json"
    module_doc = _load(path)
    rules = module_doc.get("candidate_rules")
    if rules is None:
        return path
    if not isinstance(rules, list) or not rules:
        raise ValueError("module-map.json candidate_rules must be a non-empty list")
    candidates_doc = _load(run / "module-candidates.json")
    candidates, _, _ = _candidate_universe(run, candidates_doc, module_doc)
    explicit = module_doc.get("candidate_dispositions", [])
    if not isinstance(explicit, list):
        raise ValueError("module-map.json candidate_dispositions must be a list")
    rows: dict[str, dict] = {}
    for index, row in enumerate(explicit):
        if not isinstance(row, dict) or row.get("candidate_id") not in candidates:
            raise ValueError(f"candidate_dispositions[{index}] is invalid")
        candidate_id = row["candidate_id"]
        if candidate_id in rows:
            raise ValueError(f"candidate {candidate_id!r} was dispositioned more than once")
        rows[candidate_id] = row

    rule_ids: set[str] = set()
    remainder: dict | None = None
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"candidate_rules[{index}] must be an object")
        unknown_rule_fields = set(rule) - _RULE_FIELDS
        if unknown_rule_fields:
            raise ValueError(
                f"candidate_rules[{index}] has unsupported fields: "
                f"{sorted(unknown_rule_fields)}")
        rule_id = rule.get("rule_id", "")
        if not isinstance(rule_id, str) or not _MODULE_ID.fullmatch(rule_id):
            raise ValueError(f"candidate_rules[{index}].rule_id must be kebab-case")
        if rule_id in rule_ids:
            raise ValueError(f"duplicate candidate rule_id {rule_id!r}")
        rule_ids.add(rule_id)
        disposition = rule.get("disposition", "")
        module_ids = rule.get("module_ids", [])
        reason = rule.get("reason", "")
        if disposition not in DISPOSITIONS:
            raise ValueError(f"candidate rule {rule_id!r} has unsupported disposition")
        if not isinstance(module_ids, list) or not all(isinstance(x, str) for x in module_ids):
            raise ValueError(f"candidate rule {rule_id!r}.module_ids must be a string list")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"candidate rule {rule_id!r} needs an evidence-bounded reason")
        if rule.get("remaining") is True:
            if remainder is not None:
                raise ValueError("candidate_rules may contain only one remaining rule")
            if "selectors" in rule:
                raise ValueError(f"remaining candidate rule {rule_id!r} cannot have selectors")
            if disposition != "unresolved" or module_ids:
                raise ValueError(
                    f"remaining candidate rule {rule_id!r} must be unresolved with no module")
            remainder = {"rule_id": rule_id, "disposition": disposition,
                         "module_ids": [], "reason": reason}
            continue
        if "remaining" in rule:
            raise ValueError(f"candidate rule {rule_id!r}.remaining must be true or omitted")
        selectors = rule.get("selectors")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(f"candidate rule {rule_id!r} needs selectors")
        if not all(isinstance(selector, dict) for selector in selectors):
            raise ValueError(f"candidate rule {rule_id!r} selectors must be objects")
        matched = []
        for candidate_id, candidate in candidates.items():
            if any(_selector_matches(selector, candidate) for selector in selectors):
                matched.append(candidate_id)
        if not matched:
            raise ValueError(f"candidate rule {rule_id!r} matched no candidates")
        for candidate_id in matched:
            if candidate_id in rows:
                raise ValueError(
                    f"candidate {candidate_id!r} matched multiple explicit rows/rules")
            rows[candidate_id] = {
                "candidate_id": candidate_id,
                "disposition": disposition,
                "module_ids": list(module_ids),
                "reason": reason,
            }
    if remainder is not None:
        unmatched = sorted(set(candidates) - set(rows))
        if not unmatched:
            raise ValueError(
                f"remaining candidate rule {remainder['rule_id']!r} matched no candidates")
        for candidate_id in unmatched:
            rows[candidate_id] = {
                "candidate_id": candidate_id,
                "disposition": "unresolved",
                "module_ids": [],
                "reason": remainder["reason"],
            }
    missing = sorted(set(candidates) - set(rows))
    if missing:
        raise ValueError(
            f"candidate rules omit {len(missing)} candidate(s): {missing[:5]}")
    module_doc["candidate_dispositions"] = [rows[key] for key in sorted(rows)]
    module_doc.pop("candidate_rules", None)
    replace_artifact_text(path, sanitize_text(json.dumps(
        module_doc, indent=2, sort_keys=True) + "\n"))
    return path


def validate(run_dir: str | Path) -> tuple[dict, dict]:
    run = Path(run_dir).expanduser().resolve()
    candidates_doc = _load(run / "module-candidates.json")
    module_doc = _load(run / "module-map.json")
    if module_doc.get("schema_version") != MAP_SCHEMA_VERSION:
        raise ValueError(
            f"module-map.json schema_version must be {MAP_SCHEMA_VERSION!r}")
    candidates, mechanical_count, additional_count = _candidate_universe(
        run, candidates_doc, module_doc)
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
    universe["mechanical_candidate_count"] = mechanical_count
    universe["additional_candidate_count"] = additional_count
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
