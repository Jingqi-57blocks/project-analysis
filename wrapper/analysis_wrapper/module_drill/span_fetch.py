"""Plan-bound semantic span fetching for Module Drill recovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..executor import create_stage_dir, write_new_text
from .context import SourceContext
from .span_plan import FILENAME as PLAN_FILENAME, SCHEMA_VERSION as PLAN_SCHEMA, build as build_plan
from .spans import fetch_rows
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "semantic-spans/v1"
FILENAME = "semantic-spans.json"


def _load_plan(context: SourceContext) -> dict[str, Any]:
    path = context.module_run / "evidence" / PLAN_FILENAME
    try:
        plan = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("semantic span plan is required before planned span fetch") from exc
    if not isinstance(plan, dict) or plan.get("schema_version") != PLAN_SCHEMA:
        raise ContractError("semantic span plan has an unsupported schema")
    expected = build_plan(context)
    if plan != expected:
        raise ContractError("semantic span plan does not match the current evidence universe")
    return plan


def build(context: SourceContext) -> dict[str, Any]:
    """Fetch only the immutable request universe produced by the span planner."""
    plan = _load_plan(context)
    rows = fetch_rows(context.module_run, plan["requests"])
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": plan["source_manifest_digest"],
        "semantic_span_plan_digest": sha256_json(plan),
        "feature_graph_digest": plan["feature_graph_digest"],
        "frontier_candidates_digest": plan["frontier_candidates_digest"],
        "feature_id": plan["feature_id"],
        "spans": rows,
    }


def write(context: SourceContext) -> Path:
    """Write a plan-bound semantic span artifact exactly once."""
    out = create_stage_dir(context.module_run / "evidence") / FILENAME
    write_new_text(out, json.dumps(build(context), indent=2, sort_keys=True) + "\n")
    return out
