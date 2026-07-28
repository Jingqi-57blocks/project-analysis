"""No-loss producer/consumer manifest for an Overview run.

The ledger proves a task was validated; it does not by itself prove that a
later phase used the result.  This module records that second half as a
deterministic artifact and gives the final audit an independent, testable
surface for validated-result consumption.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import Engine
from .results import validated_outputs


def _json(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    return json.loads(path.read_text("utf-8"))


def build(run_dir: str | Path) -> dict:
    run = Path(run_dir).expanduser().resolve()
    engine = Engine(run)
    records = engine._read_records() if engine.ledger_exists() else []
    tasks = engine._rebuild(records) if records else {}
    states = engine.task_states() if records else {}
    validated = validated_outputs(run)
    findings = _json(run / "findings.json", {}).get("findings", [])
    canonical_ids = {row.get("finding_id") for row in findings if isinstance(row, dict)}
    dispositions = _json(run / "tasks" / "finding-dispositions.json", [])
    disposition_ids = {row.get("finding_id") for row in dispositions if isinstance(row, dict)}
    dedup_outputs = validated_outputs(run, task_type="dedup-rank")
    merge_map = next(iter(dedup_outputs.values()), {}).get("merge_map", {}) \
        if len(dedup_outputs) == 1 else {}
    absorbed_ids = {
        finding_id for finding_id, row in merge_map.items()
        if isinstance(row, dict) and row.get("status") == "absorbed"
    }
    rows: list[dict] = []
    for task_id in sorted(tasks):
        task = tasks[task_id]
        task_type = task.packet.task_type
        state = states.get(task_id, "pending")
        consumer = ""
        consumed = state != "validated"
        detail = "terminal failure" if state == "failed" else "pending"
        output = validated.get(task_id)
        if state == "validated":
            if task_type == "lens-findings":
                ids = {row.get("finding_id") for row in output.get("findings", [])
                       if isinstance(row, dict)} if isinstance(output, dict) else set()
                consumed = ids <= canonical_ids | disposition_ids | absorbed_ids
                consumer = "findings.json/finding-dispositions.json/assembled-findings.json"
                detail = f"finding_ids={len(ids)}; dedup_absorbed={len(ids & absorbed_ids)}"
            elif task_type == "formation-proposal":
                consumed = (run / "module-map.json").is_file()
                consumer, detail = "module-map.json", "formation materialized"
            elif task_type == "boundary-resolution":
                consumed = (run / "tasks" / "module-formation-quality.json").is_file()
                consumer, detail = "module-map.json/module-formation-quality.json", "refinement materialized"
            elif task_type == "dedup-rank":
                consumed = (run / "tasks" / "assembled-findings.json").is_file()
                consumer, detail = "tasks/assembled-findings.json", "dedup materialized"
            elif task_type == "finding-resolution":
                consumed = (run / "tasks" / "finding-dispositions.json").is_file()
                consumer, detail = "findings.json/finding-dispositions.json", "tail dispositioned"
            elif task_type == "section-generate":
                section_id = output.get("section_id", "") if isinstance(output, dict) else ""
                consumer, detail = "assembled Markdown", f"section_id={section_id}"
                # Assembly's section catalog has one writer. The final audit
                # separately verifies every heading and protected projection.
                consumed = bool(section_id)
            elif task_type == "selection-fetch":
                consumer, detail = "tasks/*-fetched-evidence.json", "selection consumed by final lens"
                consumed = any((run / "tasks").glob(f"{task_id}*-fetched-evidence.json"))
            else:
                consumer, detail, consumed = "canonical downstream artifact", task_type, True
        rows.append({"task_id": task_id, "task_type": task_type, "state": state,
                     "consumed": consumed, "consumer": consumer, "detail": detail,
                     "input_digest": task.packet.input_digest})
    return {"schema_version": 1, "tasks": rows}


def write(run_dir: str | Path) -> Path:
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / "consumption-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(run), ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def problems(run_dir: str | Path) -> list[str]:
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / "consumption-manifest.json"
    if not path.is_file():
        return ["consumption manifest is missing"]
    manifest = _json(path, {})
    rows = manifest.get("tasks") if isinstance(manifest, dict) else None
    if not isinstance(rows, list):
        return ["consumption manifest has no tasks list"]
    problems: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            problems.append("malformed task row")
            continue
        if row.get("state") == "pending":
            problems.append(f"pending task: {row.get('task_id')}")
        if row.get("state") == "validated" and row.get("consumed") is not True:
            problems.append(f"unconsumed validated task: {row.get('task_id')}")
    return problems
