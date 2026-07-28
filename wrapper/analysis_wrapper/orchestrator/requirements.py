"""Machine-readable completeness requirements for lens packets.

An executor may conclude that an input has no supported finding, but it may
not silently omit that input.  Requirements use packet input ids rather than
display labels so the contract is reusable across workspaces.
"""

from __future__ import annotations

from typing import Mapping


SCHEMA_VERSION = 1


def _kind(name: str) -> str:
    if name.startswith("signals/"):
        return "signal"
    if name == "fetched-evidence.json":
        return "fetched-source"
    if name == "test-ci-evidence.json":
        return "test-ci-evidence"
    if name.endswith(".json"):
        return "structured-evidence"
    return "evidence"


def _checklist(lens_id: str, inputs: Mapping[str, str]) -> list[dict[str, str]]:
    rows = [
        {"dimension_id": "evidence-accounting", "description":
         "Account for every required packet input before concluding."},
        {"dimension_id": "finding-or-negative-result", "description":
         "Tie every supported finding or scoped negative result to evidence."},
    ]
    if "fetched-evidence.json" in inputs:
        rows.append({"dimension_id": "source-selection", "description":
                     "Account for requested source evidence and disclosed fetch limits."})
    if lens_id == "safety-net":
        rows.extend([
            {"dimension_id": "tests", "description": "Inspect observed test evidence."},
            {"dimension_id": "ci-execution", "description": "Inspect CI or execution wiring."},
            {"dimension_id": "type-checks", "description": "Inspect applicable type-check evidence."},
            {"dimension_id": "migrations", "description": "Inspect applicable migration evidence."},
            {"dimension_id": "installed-test-tooling", "description":
             "Inspect declared versus observed test-tooling use."},
        ])
    if lens_id == "open-lens":
        rows.extend([
            {"dimension_id": "residual-evidence", "description":
             "Inspect evidence not owned by a named lens in this packet."},
            {"dimension_id": "cross-lens-context", "description":
             "Only compare for duplication when the comparison set is supplied."},
        ])
    return rows


def lens_requirements(lens_id: str, inputs: Mapping[str, str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "lens_id": lens_id,
        "input_requirements": [
            {"input_id": name, "kind": _kind(name)}
            for name in sorted(inputs) if name != "requirements.json"
        ],
        "checklist_requirements": _checklist(lens_id, inputs),
    }


def selection_requirements(lens_id: str) -> dict:
    """Return the explicit source-selection roles for a source-reading lens.

    A selection task may report that a role has no applicable or readable
    source, but it must say so explicitly.  This keeps a thin source request
    from being mistaken for a complete investigation merely because the
    resulting JSON happens to be schema-valid.
    """
    roles = [
        {
            "role_id": "lens-critical-source",
            "description": "Select a source location needed to verify the lens's most material claim.",
        },
    ]
    if lens_id == "safety-net":
        roles.extend([
            {"role_id": "test-source", "description": "Select representative test source or explicitly report none."},
            {"role_id": "ci-config", "description": "Select CI execution/configuration evidence or explicitly report none."},
            {"role_id": "type-or-migration-config", "description": "Select applicable type-check or migration configuration evidence."},
            {"role_id": "test-tooling-declaration", "description": "Select declared test-tooling evidence or explicitly report none."},
        ])
    if lens_id == "open-lens":
        roles.extend([
            {"role_id": "operational-or-configuration-source", "description": "Select an operational or configuration source relevant to residual risk."},
            {"role_id": "access-or-contract-source", "description": "Select an access, API, or contract source relevant to residual risk."},
        ])
    return {"schema_version": SCHEMA_VERSION, "lens_id": lens_id, "roles": roles}
