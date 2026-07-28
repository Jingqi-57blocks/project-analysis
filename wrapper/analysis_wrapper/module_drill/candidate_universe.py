"""Deterministic candidate universe for natural-language Module Drill selection.

This stage intentionally does not interpret the selector.  It turns canonical
feature evidence into stable candidates first; the later ranking task may only
choose among these IDs or return ambiguity/no-match.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from .context import SourceContext
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "candidate-universe/v1"
FILENAME = "candidate-universe.json"


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    seed_ids: tuple[str, ...]
    repository_refs: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "seed_ids": list(self.seed_ids),
            "repository_refs": list(self.repository_refs),
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
        }


def _candidate_id(seed_ids: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\x1f".join(seed_ids).encode("utf-8")).hexdigest()[:20]
    return f"candidate-{digest}"


def _load_index(context: SourceContext) -> dict[str, Any]:
    path = context.module_run / "evidence" / "feature-evidence.json"
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("feature evidence index is required before candidate construction") from exc
    if not isinstance(document, dict) or document.get("schema_version") != "feature-evidence/v1":
        raise ContractError("feature evidence index has an unsupported schema")
    if document.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError("feature evidence index does not bind the current source manifest")
    if document.get("source_snapshot_id") != context.manifest.snapshot_id:
        raise ContractError("feature evidence index does not bind the current source snapshot")
    if not isinstance(document.get("items"), list) or not isinstance(document.get("seeds"), list):
        raise ContractError("feature evidence index items and seeds must be lists")
    return document


def _seed_by_evidence(items: list[dict[str, Any]], seeds: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    item_ids = {item.get("evidence_id") for item in items if isinstance(item, dict)}
    result: dict[str, dict[str, Any]] = {}
    for seed in seeds:
        if not isinstance(seed, dict):
            raise ContractError("feature evidence seed must be an object")
        seed_id = seed.get("seed_id")
        if not isinstance(seed_id, str) or not seed_id.startswith("seed-"):
            raise ContractError("feature evidence seed has an invalid seed_id")
        evidence_id = "evidence-" + seed_id.removeprefix("seed-")
        if evidence_id not in item_ids:
            raise ContractError("feature evidence seed does not name an indexed evidence item")
        if evidence_id in result:
            raise ContractError("feature evidence has duplicate seed ownership")
        result[evidence_id] = seed
    return result


def _row(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _exact_ui_route_candidates(items: list[dict[str, Any]], seed_by_evidence: dict[str, dict[str, Any]]) -> list[Candidate]:
    routes: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in items:
        if item.get("kind") != "route":
            continue
        data = _row(item.get("data"), "route data")
        repositories = item.get("repository_refs")
        if not isinstance(repositories, list) or len(repositories) != 1:
            raise ContractError("route evidence must have exactly one repository")
        method, path = data.get("method"), data.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            raise ContractError("route evidence lacks method or path")
        routes.setdefault((repositories[0], method, path), []).append(item)

    candidates: list[Candidate] = []
    for action in items:
        if action.get("kind") != "ui-action":
            continue
        action_id = action.get("evidence_id")
        action_seed = seed_by_evidence.get(action_id)
        if action_seed is None:
            continue
        data = _row(action.get("data"), "UI action data")
        backend, method, path = data.get("target_repository_ref"), data.get("method"), data.get("path")
        if not all(isinstance(value, str) for value in (backend, method, path)):
            raise ContractError("UI action evidence lacks route-link fields")
        for route in routes.get((backend, method, path), []):
            route_seed = seed_by_evidence.get(route.get("evidence_id"))
            if route_seed is None:
                continue
            seed_ids = tuple(sorted((str(action_seed["seed_id"]), str(route_seed["seed_id"]))))
            repositories = tuple(dict.fromkeys(
                [*action.get("repository_refs", []), *route.get("repository_refs", [])]))
            candidates.append(Candidate(
                _candidate_id(seed_ids), seed_ids, repositories,
                tuple(sorted((str(action_id), str(route["evidence_id"])))),
                "exact UI-to-route method and path linkage",
            ))
    return candidates


def _singleton_candidates(items: list[dict[str, Any]], seed_by_evidence: dict[str, dict[str, Any]],
                          used_seed_ids: set[str]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for item in items:
        evidence_id = item.get("evidence_id")
        seed = seed_by_evidence.get(evidence_id)
        if seed is None or seed["seed_id"] in used_seed_ids:
            continue
        repositories = item.get("repository_refs")
        if not isinstance(repositories, list) or not all(isinstance(value, str) for value in repositories):
            raise ContractError("feature evidence item has invalid repository_refs")
        seed_id = str(seed["seed_id"])
        candidates.append(Candidate(
            _candidate_id((seed_id,)), (seed_id,), tuple(repositories), (str(evidence_id),),
            "one deterministic evidence anchor",
        ))
    return candidates


def build(context: SourceContext) -> dict[str, Any]:
    """Construct candidates solely from canonical evidence relationships."""
    index = _load_index(context)
    items = [_row(item, "feature evidence item") for item in index["items"]]
    seed_by_evidence = _seed_by_evidence(items, index["seeds"])
    linked = _exact_ui_route_candidates(items, seed_by_evidence)
    used = {seed_id for candidate in linked for seed_id in candidate.seed_ids}
    candidates = linked + _singleton_candidates(items, seed_by_evidence, used)
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    if len(by_id) != len(candidates):
        raise ContractError("candidate construction produced duplicate candidate IDs")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": sha256_json(context.manifest.to_dict()),
        "source_snapshot_id": context.manifest.snapshot_id,
        "feature_evidence_digest": sha256_json(index),
        "candidates": [candidate.to_dict() for candidate in sorted(by_id.values(), key=lambda row: row.candidate_id)],
    }


def load(context: SourceContext) -> dict[str, Any]:
    """Load the persisted candidate universe bound to this exact source snapshot.

    The ranking stage must never reconstruct candidates from selector text or
    from a bounded overview projection.  It consumes this immutable document
    after rechecking both its source-manifest and canonical evidence bindings.
    """
    path = context.module_run / "evidence" / FILENAME
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("candidate universe is required before ranking") from exc
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("candidate universe has an unsupported schema")
    if document.get("source_manifest_digest") != sha256_json(context.manifest.to_dict()):
        raise ContractError("candidate universe does not bind the current source manifest")
    if document.get("source_snapshot_id") != context.manifest.snapshot_id:
        raise ContractError("candidate universe does not bind the current source snapshot")
    index = _load_index(context)
    if document.get("feature_evidence_digest") != sha256_json(index):
        raise ContractError("candidate universe does not bind the current feature evidence")
    if sha256_json(document) != sha256_json(build(context)):
        raise ContractError("candidate universe differs from deterministic source evidence")
    rows = document.get("candidates")
    if not isinstance(rows, list):
        raise ContractError("candidate universe candidates must be a list")
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "candidate_id", "seed_ids", "repository_refs", "evidence_ids", "reason"}:
            raise ContractError("candidate universe candidate has an invalid shape")
        candidate_id = row["candidate_id"]
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in ids:
            raise ContractError("candidate universe candidate IDs must be unique non-empty strings")
        ids.add(candidate_id)
        for field in ("seed_ids", "repository_refs", "evidence_ids"):
            value = row[field]
            if not isinstance(value, list) or not value or not all(
                    isinstance(item, str) and item for item in value) or len(value) != len(set(value)):
                raise ContractError(f"candidate universe {field} must be a unique non-empty string list")
        if not isinstance(row["reason"], str) or not row["reason"]:
            raise ContractError("candidate universe candidate reason must be a non-empty string")
    return document


def write(context: SourceContext) -> Path:
    """Persist the universe once; it is input to the later ranking task."""
    directory = create_stage_dir(context.module_run / "evidence")
    out = directory / FILENAME
    write_new_text(out, json.dumps(build(context), indent=2, sort_keys=True) + "\n")
    return out
