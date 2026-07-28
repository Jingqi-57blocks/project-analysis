"""Thin CLI adapters for the Module Drill lifecycle capabilities."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..orchestrator.contracts import TaskPacket
from ..orchestrator.engine import EngineError
from .driver import ModuleDriver
from .feature_evidence import write as write_feature_evidence
from .candidate_universe import write as write_candidate_universe
from .context import load as load_source_context
from .ranking import register as register_ranking
from .selection import finalize as finalize_selection
from .feature_graph import write as write_feature_graph
from .frontier_receipts import write as write_frontier_receipts
from .frontier_candidates import write as write_frontier_candidates
from .span_plan import write as write_span_plan
from .span_fetch import write as write_planned_spans
from .runtime import initialize_from_overview
from .spans import fetch
from .standalone import initialize as initialize_standalone
from .validation import ContractError


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _load_json(path: str, label: str) -> Any:
    try:
        text = sys.stdin.read() if path == "-" else Path(path).expanduser().read_text("utf-8")
        return json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not valid JSON: {exc}") from exc


def _init(args) -> int:
    if args.from_overview:
        result = initialize_from_overview(
            args.from_overview, output_root=args.output_root, project_key=args.project_key,
            selector=args.selector, language=args.language, run_label=args.run_id,
            model=args.model, effort=args.effort)
        _print({"run": str(result.run_dir), "run_id": result.run_id,
                "source_mode": "overview-backed", "next": "register module tasks"})
        return 0
    result = initialize_standalone(
        args.workspace, output_root=args.output_root, project_key=args.project_key,
        selector=args.selector, language=args.language, run_label=args.run_id,
        model=args.model, effort=args.effort,
        exclude_names=tuple(item.strip() for item in args.exclude.split(",") if item.strip()),
        analyzer_root=args.analyzer_root or None, include_network=args.include_network,
        scan_date=args.scan_date, since=args.since,
        coupling_sample_cap=args.coupling_sample_cap, allow_hosts=args.allow_hosts,
        jobs=args.jobs)
    _print({"run": str(result.run_dir), "run_id": result.run_id,
            "source_mode": "standalone", "next": "register module tasks"})
    return 0


def _status(args) -> int:
    status = ModuleDriver(args.run).status()
    _print({"run_id": status.run_id, "task_states": status.task_states,
            "complete": status.complete, "audit": status.audit.to_dict()})
    return 0


def _register(args) -> int:
    raw = _load_json(args.packets, "--packets")
    if not isinstance(raw, list):
        raise ContractError("--packets must contain a JSON array")
    packets = [TaskPacket.from_dict(row) for row in raw]
    created = ModuleDriver(args.run).register(packets)
    _print({"created": created})
    return 0


def _next(args) -> int:
    driver = ModuleDriver(args.run)
    if not driver.engine.ledger_exists():
        print("wrapper input error: no Module Drill ledger; register module tasks first", file=sys.stderr)
        return 6
    claimed = driver.claim(args.claim, executor_kind=args.executor_kind, model=args.model)
    _print([{"task": item.packet.to_dict(), "attempt": item.attempt} for item in claimed])
    return 0


def _submit(args) -> int:
    raw = _load_json(args.result, "--result")
    if not isinstance(raw, dict):
        raise ContractError("--result must contain a JSON object")
    outcome = ModuleDriver(args.run).submit(args.task, raw)
    _print(outcome)
    return 0 if outcome["status"] == "validated" else 3


def _spans(args) -> int:
    raw = _load_json(args.requests, "--requests")
    if not isinstance(raw, list):
        raise ContractError("--requests must contain a JSON array")
    out = fetch(args.run, raw, out=args.out or None)
    _print({"spans": str(out)})
    return 0


def _evidence(args) -> int:
    out = write_feature_evidence(load_source_context(args.run))
    _print({"evidence": str(out)})
    return 0


def _candidates(args) -> int:
    out = write_candidate_universe(load_source_context(args.run))
    _print({"candidates": str(out)})
    return 0


def _rank_candidates(args) -> int:
    created = register_ranking(args.run)
    _print({"created": created, "next": "claim module-candidate-ranking"})
    return 0


def _finalize_ranking(args) -> int:
    result = finalize_selection(args.run)
    _print({"decision": result.decision, "resolution": str(result.resolution_path),
            "scope": str(result.scope_path) if result.scope_path else ""})
    return 0 if result.scope_path is not None else 3


def _graph(args) -> int:
    out = write_feature_graph(load_source_context(args.run))
    _print({"graph": str(out)})
    return 0


def _frontier_receipts(args) -> int:
    out = write_frontier_receipts(load_source_context(args.run))
    _print({"frontier_state": str(out)})
    return 0


def _frontier_candidates(args) -> int:
    out = write_frontier_candidates(load_source_context(args.run))
    _print({"frontier_candidates": str(out)})
    return 0


def _span_plan(args) -> int:
    out = write_span_plan(load_source_context(args.run))
    _print({"span_plan": str(out)})
    return 0


def _planned_spans(args) -> int:
    out = write_planned_spans(load_source_context(args.run))
    _print({"semantic_spans": str(out)})
    return 0


def run(args) -> int:
    """Dispatch a Module Drill subcommand and preserve normal CLI exit codes."""
    try:
        handlers = {
            "module-init": _init, "module-status": _status, "module-register": _register,
            "module-next": _next, "module-submit": _submit, "module-fetch-spans": _spans,
            "module-build-evidence": _evidence,
            "module-build-candidates": _candidates,
            "module-plan-ranking": _rank_candidates,
            "module-finalize-ranking": _finalize_ranking,
            "module-build-graph": _graph,
            "module-build-frontier-receipts": _frontier_receipts,
            "module-build-frontier-candidates": _frontier_candidates,
            "module-plan-spans": _span_plan,
            "module-fetch-planned-spans": _planned_spans,
        }
        handler = handlers.get(args.command)
        if handler is None:
            raise ContractError(f"unknown Module Drill command {args.command!r}")
        return handler(args)
    except ContractError as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 5 if "source snapshot is stale" in str(exc) else 2
    except EngineError as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
