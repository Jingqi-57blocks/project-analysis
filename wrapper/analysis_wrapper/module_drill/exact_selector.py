"""Conservative deterministic resolution for syntactically exact selectors.

Natural-language interpretation stays in the bounded ranking task.  This
module deliberately handles only selector forms whose value can be compared
directly with a canonical evidence item, so an exact route/path/candidate ID
does not spend a model task or become a keyword-based guess.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..evidence.facts import SourceRef
from ..executor import create_stage_dir, write_new_text
from .candidate_universe import load as load_universe
from .context import SourceContext
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "exact-selector-resolution/v1"
FILENAME = "exact-selector-resolution.json"

_ROUTE = re.compile(
    r"(?:(?P<method>GET|POST|PUT|PATCH|DELETE)\s+)?(?P<path>/[^\s?#]+)$", re.IGNORECASE)
_LABEL_PREFIXES = frozenset({"datastore", "package", "host", "integration", "job", "event", "config"})


@dataclass(frozen=True)
class ExactSelectorResolution:
    """A direct, inspectable selector result before any ranking task runs."""

    selector: str
    decision: str
    candidate_ids: tuple[str, ...]
    match_kind: str
    matched_values: tuple[str, ...]
    candidate_universe_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "selector": self.selector,
            "decision": self.decision,
            "candidate_ids": list(self.candidate_ids),
            "match_kind": self.match_kind,
            "matched_values": list(self.matched_values),
            "candidate_universe_digest": self.candidate_universe_digest,
        }


def _selector(context: SourceContext) -> str:
    try:
        provenance = json.loads((context.module_run / "provenance.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("module provenance is invalid for exact selector resolution") from exc
    value = provenance.get("selector") if isinstance(provenance, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ContractError("module provenance has no non-empty selector")
    return value.strip()


def _items(context: SourceContext, universe: dict[str, Any]) -> dict[str, dict[str, Any]]:
    path = context.module_run / "evidence" / "feature-evidence.json"
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("feature evidence is invalid for exact selector resolution") from exc
    if not isinstance(document, dict) or sha256_json(document) != universe["feature_evidence_digest"]:
        raise ContractError("candidate universe and feature evidence disagree")
    rows = document.get("items")
    if not isinstance(rows, list):
        raise ContractError("feature evidence items are invalid for exact selector resolution")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("evidence_id"), str):
            raise ContractError("feature evidence item has no stable evidence ID")
        result[row["evidence_id"]] = row
    return result


def _candidates_for_item_ids(universe: dict[str, Any], item_ids: set[str]) -> set[str]:
    return {
        row["candidate_id"] for row in universe["candidates"]
        if item_ids & set(row["evidence_ids"])
    }


def _resolution(selector: str, candidates: set[str], match_kind: str,
                matched_values: set[str], universe: dict[str, Any]) -> ExactSelectorResolution:
    ordered = tuple(sorted(candidates))
    if len(ordered) == 1:
        decision = "selected"
    elif ordered:
        decision = "ambiguous"
    else:
        decision = "no-match"
    return ExactSelectorResolution(
        selector=selector, decision=decision, candidate_ids=ordered,
        match_kind=match_kind, matched_values=tuple(sorted(matched_values)),
        candidate_universe_digest=sha256_json(universe),
    )


def _route_resolution(selector: str, universe: dict[str, Any],
                      items: dict[str, dict[str, Any]]) -> ExactSelectorResolution | None:
    match = _ROUTE.fullmatch(selector)
    if match is None:
        return None
    method = match.group("method")
    path = match.group("path")
    item_ids: set[str] = set()
    matched: set[str] = set()
    for item_id, item in items.items():
        if item.get("kind") not in {"route", "ui-action"}:
            continue
        data = item.get("data")
        if not isinstance(data, dict) or data.get("path") != path:
            continue
        item_method = data.get("method")
        if method is not None and item_method != method.upper():
            continue
        item_ids.add(item_id)
        matched.add(f"{item_method or ''} {path}".strip())
    return _resolution(selector, _candidates_for_item_ids(universe, item_ids), "route", matched, universe)


def _source_path_resolution(selector: str, universe: dict[str, Any],
                            items: dict[str, dict[str, Any]]) -> ExactSelectorResolution | None:
    full_ref: SourceRef | None
    try:
        full_ref = SourceRef.from_string(selector)
    except ValueError:
        full_ref = None
    looks_like_path = "/" in selector and not selector.startswith("/") and " " not in selector
    if full_ref is None and not looks_like_path:
        return None
    item_ids: set[str] = set()
    matched: set[str] = set()
    for item_id, item in items.items():
        refs = item.get("source_refs")
        if not isinstance(refs, list):
            raise ContractError("feature evidence item has invalid source refs")
        for value in refs:
            if not isinstance(value, str):
                raise ContractError("feature evidence source ref is invalid")
            ref = SourceRef.from_string(value)
            same = value == selector if full_ref is not None else (
                ref.path == selector or f"{ref.repository_ref}/{ref.path}" == selector)
            if same:
                item_ids.add(item_id)
                matched.add(value)
    return _resolution(selector, _candidates_for_item_ids(universe, item_ids), "source-path", matched, universe)


def _label_resolution(selector: str, universe: dict[str, Any],
                      items: dict[str, dict[str, Any]]) -> ExactSelectorResolution | None:
    prefix, separator, value = selector.partition(":")
    prefix, value = prefix.lower(), value.strip()
    if not separator or prefix not in _LABEL_PREFIXES or not value:
        return None
    expected_kinds = {
        "datastore": {"datastore"},
        "package": {"integration-package"},
        "host": {"integration-host"},
        "integration": {"integration-package", "integration-host"},
        "job": {"async-boundary"},
        "event": {"async-boundary"},
        "config": {"configuration"},
    }[prefix]
    expected_fields = {
        "datastore": ("name", "physical_name"),
        "package": ("package",),
        "host": ("value",),
        "integration": ("package", "value"),
        "job": ("job", "queue", "operation", "name"),
        "event": ("event", "name", "operation"),
        "config": ("name", "key"),
    }[prefix]
    item_ids: set[str] = set()
    matched: set[str] = set()
    for item_id, item in items.items():
        if item.get("kind") not in expected_kinds:
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            raise ContractError("feature evidence item has invalid label data")
        if any(data.get(field) == value for field in expected_fields):
            item_ids.add(item_id)
            matched.add(value)
    return _resolution(selector, _candidates_for_item_ids(universe, item_ids), f"{prefix}-label", matched, universe)


def resolve(context: SourceContext) -> ExactSelectorResolution | None:
    """Return a result only for a deliberately narrow exact selector syntax."""
    selector = _selector(context)
    universe = load_universe(context)
    items = _items(context, universe)
    candidate_ids = {row["candidate_id"] for row in universe["candidates"]}
    if selector.startswith("candidate-"):
        return _resolution(selector, {selector} & candidate_ids, "candidate-id", {selector}, universe)
    for resolver in (_route_resolution, _source_path_resolution, _label_resolution):
        result = resolver(selector, universe, items)
        if result is not None:
            return result
    return None


def write(context: SourceContext) -> Path | None:
    """Persist an exact result, or return ``None`` when ranking is required."""
    resolution = resolve(context)
    if resolution is None:
        return None
    directory = create_stage_dir(context.module_run / "evidence")
    path = directory / FILENAME
    if path.exists():
        if load(context) != resolution:
            raise ContractError("persisted exact selector resolution is stale or tampered")
        return path
    write_new_text(path, json.dumps(resolution.to_dict(), indent=2, sort_keys=True) + "\n")
    return path


def load(context: SourceContext) -> ExactSelectorResolution | None:
    """Read a persisted exact result only when it still equals fresh evidence."""
    path = context.module_run / "evidence" / FILENAME
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("exact selector resolution is invalid") from exc
    required = {"schema_version", "selector", "decision", "candidate_ids", "match_kind",
                "matched_values", "candidate_universe_digest"}
    if not isinstance(row, dict) or set(row) != required or row.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("exact selector resolution has an invalid schema")
    if row.get("decision") not in {"selected", "ambiguous", "no-match"} \
            or not isinstance(row.get("selector"), str) \
            or not isinstance(row.get("match_kind"), str) \
            or not isinstance(row.get("candidate_ids"), list) \
            or not isinstance(row.get("matched_values"), list) \
            or not all(isinstance(value, str) and value for value in row["candidate_ids"] + row["matched_values"]):
        raise ContractError("exact selector resolution has invalid values")
    result = ExactSelectorResolution(
        selector=row["selector"], decision=row["decision"],
        candidate_ids=tuple(row["candidate_ids"]), match_kind=row["match_kind"],
        matched_values=tuple(row["matched_values"]), candidate_universe_digest=row["candidate_universe_digest"],
    )
    if result != resolve(context):
        raise ContractError("exact selector resolution does not match current canonical evidence")
    return result
