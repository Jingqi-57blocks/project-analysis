"""Deterministic completeness requirements for lens and source-selection tasks.

The packet is the contract: an executor may reach a negative conclusion, but
it cannot silently narrow the evidence, checklist, source-selection roles, or
coverage it was asked to account for.  Everything in this module is derived
from typed packet inputs and the lens template -- never keyword matching over
repository text -- so the same prepared snapshot produces the same contract.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 2


def canonical_digest(value: Any) -> str:
    """Stable digest used to link a composer-created child contract to its
    unsharded parent contract."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _kind(input_id: str) -> str:
    if input_id.startswith("signals/"):
        return "signal-view"
    if input_id == "fetched-evidence.json":
        return "fetched-source"
    if input_id == "selection-role-results.json":
        return "source-selection-result"
    if input_id == "test-ci-evidence.json":
        return "typed-test-ci-inventory"
    if input_id.endswith("-meta.json"):
        return "bounded-evidence-metadata"
    if input_id.endswith(".json"):
        return "structured-evidence"
    return "evidence"


def _input_requirements(inputs: Mapping[str, str]) -> list[dict[str, str]]:
    ignored = {"requirements.json", "selection-requirements.json", "sharding"}
    return [{"input_id": input_id, "kind": _kind(input_id)}
            for input_id in sorted(inputs) if input_id not in ignored]


def selection_role_requirements(lens_id: str, inputs: Mapping[str, str], *,
                                source_reads: bool) -> list[dict[str, Any]]:
    """The applicable roles for one source-reading lens.

    ``evidence_input_ids`` and ``inventory_paths`` deliberately identify
    typed evidence families/fields, instead of asking a model to decide that a
    file merely *looks relevant*.  Roles are omitted for non-source-reading
    lenses; every listed role is therefore applicable to its packet.
    """
    if not source_reads:
        return []

    available = set(inputs)
    roles: list[dict[str, Any]] = [{
        "role_id": "lens-critical-source",
        "description": "Verify the material lens claim against a concrete source location.",
        "evidence_input_ids": sorted(
            input_id for input_id in available
            if input_id.startswith("signals/") or input_id == "module-candidates.json"),
        "inventory_paths": [],
    }]
    if lens_id == "safety-net" and "test-ci-evidence.json" in available:
        roles.extend([
            {
                "role_id": "test-source",
                "description": "Use the test-file inventory to select a representative test source.",
                "evidence_input_ids": ["test-ci-evidence.json"],
                "inventory_paths": ["test_files.paths"],
            },
            {
                "role_id": "ci-config",
                "description": "Use the CI-config inventory to select execution wiring.",
                "evidence_input_ids": ["test-ci-evidence.json"],
                "inventory_paths": ["ci_configs.path"],
            },
            {
                "role_id": "declared-validation-tooling",
                "description": "Use declared package/module metadata for type, migration, or test tooling.",
                "evidence_input_ids": ["test-ci-evidence.json"],
                "inventory_paths": ["package_json.scripts", "package_json.devDependencies",
                                    "go_mod_module"],
            },
        ])
    if lens_id == "open-lens":
        if "role-catalog-by-repository.json" in available:
            roles.append({
                "role_id": "access-or-contract-source",
                "description": "Use the role/access inventory to verify an enforcement or contract source.",
                "evidence_input_ids": ["role-catalog-by-repository.json"],
                "inventory_paths": ["items[].repository_ref", "items[].roles"],
            })
        if "route-inventory.json" in available or "ui-route-linkage.json" in available:
            roles.append({
                "role_id": "operational-or-route-source",
                "description": "Use the typed route/linkage inventory to verify an operational or route-level source.",
                "evidence_input_ids": sorted(input_id for input_id in (
                    "route-inventory.json", "ui-route-linkage.json") if input_id in available),
                "inventory_paths": ["rows[].repository_ref", "rows[].path"],
            })
    if lens_id == "dependencies-cycles" and "graph-nodes.json" in available:
        roles.append({
            "role_id": "dependency-boundary-source",
            "description": "Use the graph inventory to verify a dependency boundary source.",
            "evidence_input_ids": ["graph-nodes.json"],
            "inventory_paths": ["items[].repository_ref", "items[].kind"],
        })
    if lens_id == "structure-inventory" and "module-candidates.json" in available:
        roles.append({
            "role_id": "structural-boundary-source",
            "description": "Use the module-candidate inventory to verify a structural boundary source.",
            "evidence_input_ids": ["module-candidates.json"],
            "inventory_paths": ["[].repository_ref", "[].evidence"],
        })
    return roles


def _checklist(lens_id: str, inputs: Mapping[str, str], *,
               source_roles: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows = [
        {"dimension_id": "evidence-accounting",
         "description": "Account for every required packet input before concluding."},
        {"dimension_id": "finding-or-negative-result",
         "description": "Tie each finding or scoped no-concern result to cited evidence."},
    ]
    if source_roles:
        rows.append({"dimension_id": "source-selection",
                     "description": "Account for every applicable source-selection role and fetch limit."})
    if lens_id == "safety-net":
        rows.extend([
            {"dimension_id": "tests", "description": "Inspect observed test evidence."},
            {"dimension_id": "ci-execution", "description": "Inspect CI or execution wiring."},
            {"dimension_id": "declared-validation-tooling",
             "description": "Inspect declared validation/tooling evidence."},
        ])
    if lens_id == "open-lens":
        rows.extend([
            {"dimension_id": "residual-evidence",
             "description": "Inspect residual evidence supplied in this packet."},
            {"dimension_id": "cross-lens-boundary",
             "description": "Do not claim cross-lens non-duplication without a supplied comparison set."},
        ])
    return rows


def lens_requirements(lens_id: str, inputs: Mapping[str, str], *,
                      source_reads: bool, task_id: str, shard: str,
                      repository_ref: str | None, context_budget_tokens: int,
                      max_selections: int) -> dict[str, Any]:
    """Return the complete, stable contract for a planned lens packet."""
    roles = selection_role_requirements(lens_id, inputs, source_reads=source_reads)
    input_requirements = _input_requirements(inputs)
    coverage = [
        {"coverage_id": row["input_id"], "kind": "signal-view"}
        for row in input_requirements if row["kind"] == "signal-view"
    ]
    coverage.extend({"coverage_id": f"source-selection/{row['role_id']}",
                     "kind": "source-selection-role"} for row in roles)
    return {
        "schema_version": SCHEMA_VERSION,
        "lens_id": lens_id,
        "parent_task_id": task_id,
        "expected_shard_scope": {
            "shard": shard,
            "repository_ref": repository_ref or "",
        },
        "inherited_limits": {
            "context_budget_tokens": context_budget_tokens,
            "max_selections": max_selections if source_reads else 0,
        },
        "input_requirements": input_requirements,
        "checklist_requirements": _checklist(lens_id, inputs, source_roles=roles),
        "selection_role_requirements": roles,
        "coverage_requirements": coverage,
    }


def selection_requirements(lens_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Project the source-role portion of ``requirements.json`` for a paired
    selection task, retaining its parent identity and typed role inventories."""
    return {
        "schema_version": SCHEMA_VERSION,
        "lens_id": lens_contract.get("lens_id", ""),
        "parent_task_id": lens_contract.get("parent_task_id", ""),
        "parent_requirements_digest": canonical_digest(lens_contract),
        "expected_shard_scope": lens_contract.get("expected_shard_scope", {}),
        "inherited_limits": lens_contract.get("inherited_limits", {}),
        "roles": lens_contract.get("selection_role_requirements", []),
    }
