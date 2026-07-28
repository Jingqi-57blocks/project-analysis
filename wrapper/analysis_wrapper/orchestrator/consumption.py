"""Deterministic, ledger-derived consumption manifest for final Overview audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import Engine
from .planner import fetch_selections_output_path

FILENAME = "consumption-manifest.json"


def _consumer(run: Path, task: Any) -> tuple[str, str, str]:
    """Return (terminal disposition, consumer artifact, reason code)."""
    packet = task.packet
    task_type = packet.task_type
    if task.terminally_failed:
        return "failed", "", "task-terminal-failure"
    if not task.done:
        return "partial", "", "task-not-terminal"
    if task_type == "formation-proposal":
        return "consumed", "module-map.json", "formation-materialized"
    if task_type == "boundary-resolution":
        return "consumed", "module-map.json", "boundary-resolution-applied"
    if task_type == "dedup-rank":
        return "consumed", "tasks/assembled-findings.json", "dedup-assembled"
    if task_type == "rekey-resolution":
        return "consumed", "finding-terminal-dispositions.json", "tail-dispositioned"
    if task_type == "selection-fetch":
        return "consumed", str(fetch_selections_output_path(run, packet.task_id).relative_to(run)), "source-selection-fetched"
    if task_type == "section-generate":
        output = _output_for_task(run, packet.task_id)
        section_id = output.get("section_id", "") if isinstance(output, dict) else ""
        from . import sections
        document = sections.BY_ID.get(section_id).document if section_id in sections.BY_ID else ""
        return "consumed", document, "section-assembled"
    if task_type == "lens-findings":
        output = _output_for_task(run, packet.task_id)
        rows = output.get("findings", []) if isinstance(output, dict) else []
        return ("consumed" if rows else "evidence-backed-no-finding",
                "tasks/assembled-findings.json" if rows else "tasks/ledger.jsonl",
                "lens-findings-assembled" if rows else "lens-no-finding")
    return "consumed", "tasks/ledger.jsonl", "validated-task-contract"


def _output_for_task(run: Path, task_id: str) -> Any:
    records = Engine(run)._read_records()
    latest: Any = None
    for record in records:
        if record.event == "submitted" and record.task_id == task_id:
            latest = record.detail["result"].get("output")
    return latest


def build(run_dir: str | Path) -> dict:
    run = Path(run_dir).expanduser().resolve()
    engine = Engine(run)
    if not engine.ledger_exists():
        return {"schema_version": 1, "entries": [], "authoritative": True}
    tasks = engine._rebuild(engine._read_records())
    canonical_ids: set[str] = set()
    findings_path = run / "findings.json"
    if findings_path.is_file():
        try:
            for row in json.loads(findings_path.read_text("utf-8")).get("findings", []):
                if not isinstance(row, dict):
                    continue
                lineage = row.get("lineage", {})
                canonical_ids.update(lineage.get("source_finding_ids", []))
                canonical_ids.add(row.get("finding_id", ""))
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            pass
    terminal_by_id: dict[str, str] = {}
    terminal_path = run / "finding-terminal-dispositions.json"
    if terminal_path.is_file():
        try:
            terminal_by_id = {
                row.get("finding_id", ""): row.get("disposition", "")
                for row in json.loads(terminal_path.read_text("utf-8")).get("dispositions", [])
                if isinstance(row, dict)
            }
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            pass
    entries = []
    for task_id, task in sorted(tasks.items()):
        disposition, consumer, reason_code = _consumer(run, task)
        if task.packet.task_type == "lens-findings" and task.done:
            output = _output_for_task(run, task_id)
            finding_ids = {row.get("finding_id") for row in output.get("findings", [])
                           if isinstance(row, dict)} if isinstance(output, dict) else set()
            accounted = canonical_ids | set(terminal_by_id)
            missing = sorted(finding_ids - accounted)
            if missing:
                disposition, consumer, reason_code = "partial", "", "lens-finding-lineage-missing"
        entries.append({
            "task_id": task_id,
            "task_type": task.packet.task_type,
            "input_digest": task.packet.input_digest,
            "template_version": task.packet.template_version,
            "terminal_disposition": disposition,
            "consumer": consumer,
            "reason_code": reason_code,
            "coverage_impact": ("required work did not reach a validated terminal state"
                                if disposition in {"partial", "failed"} else ""),
        })
    authoritative = all(row["terminal_disposition"] not in {"partial", "failed"}
                        and bool(row["consumer"]) for row in entries)
    return {"schema_version": 1, "entries": entries, "authoritative": authoritative}


def write(run_dir: str | Path) -> Path:
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(run), indent=2, sort_keys=True) + "\n", "utf-8")
    return path
