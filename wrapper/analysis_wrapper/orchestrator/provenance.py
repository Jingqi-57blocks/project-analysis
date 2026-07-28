"""Observed executor provenance derived from the append-only task ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .engine import Engine


def _load_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _ledger_state(run: Path) -> tuple[Engine, list, dict]:
    engine = Engine(run)
    records = engine._read_records() if engine.ledger_exists() else []
    tasks = engine._rebuild(records) if records else {}
    return engine, records, tasks


def _generation(run: Path) -> dict:
    raw = _load_object(run / "run-provenance.json").get("generation", {})
    raw = raw if isinstance(raw, dict) else {}
    return {
        "language": raw.get("language", "unknown") or "unknown",
        "model": raw.get("model", "unknown") or "unknown",
        "effort": raw.get("effort", "unknown") or "unknown",
    }


def build(run_dir: str | Path) -> dict:
    run = Path(run_dir).expanduser().resolve()
    _engine, records, tasks = _ledger_state(run)
    claims: dict[tuple[str, int], dict] = {}
    rows: list[dict] = []
    for record in records:
        if record.event == "claimed":
            claims[(record.task_id, record.detail["attempt"])] = record.detail["executor"]
        if record.event != "submitted":
            continue
        result = record.detail["result"]
        attempt = result["attempt"]
        executor = result.get("executor") or claims.get((record.task_id, attempt), {})
        timing = result.get("timing", {})
        tokens = result.get("tokens")
        task = tasks.get(record.task_id)
        rows.append({
            "task_id": record.task_id,
            "attempt": attempt,
            "executor": executor,
            "timing": timing,
            "tokens": tokens,
            "input_digest": task.packet.input_digest if task is not None else "",
            "model_available": bool(executor.get("model")) and executor.get("model") != "unknown",
            "tokens_available": tokens is not None,
        })
    return {
        "schema_version": 1,
        "generation": _generation(run),
        "tasks": rows,
    }


def acceptance_manifest(run_dir: str | Path) -> dict:
    """Stable evidence needed to compare two controlled executor runs.

    The artifact deliberately records model/effort as observations rather
    than pretending they are always known.  A reviewer can verify that two
    runs share source inputs and task packets before interpreting semantic
    output differences as a model/effort effect.
    """
    run = Path(run_dir).expanduser().resolve()
    _engine, _records, tasks = _ledger_state(run)
    generation = _generation(run)
    target_bytes = (run / "targets.json").read_bytes() if (run / "targets.json").is_file() else b""
    packets = [{
        "task_id": task_id,
        "task_type": task.packet.task_type,
        "template_id": task.packet.template_id,
        "template_version": task.packet.template_version,
        "input_digest": task.packet.input_digest,
        "output_schema_id": task.packet.output_schema_id,
        "depends_on": list(task.packet.depends_on),
    } for task_id, task in sorted(tasks.items())]
    return {
        "schema_version": 1,
        "baseline": {
            "targets_sha256": hashlib.sha256(target_bytes).hexdigest(),
            "language": generation["language"],
            "task_packets": packets,
        },
        "observed_generation": generation,
        "semantic_compare_command": "compare-runs BASE_RUN CANDIDATE_RUN --semantic",
    }


def write(run_dir: str | Path) -> Path:
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / "execution-provenance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(run), ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    acceptance_path = run / "tasks" / "acceptance-manifest.json"
    acceptance_path.write_text(json.dumps(acceptance_manifest(run), ensure_ascii=False,
                                          indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def problems(run_dir: str | Path) -> list[str]:
    run = Path(run_dir).expanduser().resolve()
    path = run / "tasks" / "execution-provenance.json"
    if not path.is_file():
        return ["execution provenance is missing"]
    try:
        rows = json.loads(path.read_text("utf-8")).get("tasks")
    except (OSError, ValueError) as exc:
        return [f"execution provenance unreadable: {exc}"]
    if not isinstance(rows, list):
        return ["execution provenance has no tasks list"]
    problems: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("task_id"):
            problems.append("malformed execution provenance row")
            continue
        timing = row.get("timing")
        executor = row.get("executor")
        if not isinstance(timing, dict) or not isinstance(executor, dict):
            problems.append(f"missing executor/timing provenance for {row.get('task_id')}")
        if not isinstance(row.get("input_digest"), str) or not row.get("input_digest"):
            problems.append(f"missing task input digest for {row.get('task_id')}")
    acceptance_path = run / "tasks" / "acceptance-manifest.json"
    acceptance = _load_object(acceptance_path)
    baseline = acceptance.get("baseline")
    if not acceptance_path.is_file() or not isinstance(baseline, dict):
        problems.append("acceptance manifest is missing or malformed")
    elif not isinstance(baseline.get("targets_sha256"), str) \
            or not isinstance(baseline.get("task_packets"), list):
        problems.append("acceptance manifest lacks task packet baseline")
    return problems
