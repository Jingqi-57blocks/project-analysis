"""Ledger-reading helper for downstream orchestrator code (57B-113 / 57B-116, M2).

Everything past M0/M1 that needs a validated task's OUTPUT (not just its
state) goes through the one function here rather than re-parsing the
ledger -- ``planner.py``'s two-phase dedup planning is the first caller;
any future section-generate/coherence-check driver is the next one.

``Engine`` itself never returns an output payload from ``task_states()``
(only the state string) -- the ledger line that actually carries it is the
"submitted" record, one per attempt, keyed by ``(task_id, attempt number)``.
This module reuses ``Engine``'s own replay (``_read_records``/``_rebuild``)
to find each task's CURRENT generation and its LATEST attempt, then looks up
that attempt's own "submitted" record for the output -- rather than writing
a second ledger parser, per this module's own no-duplicate-parser mandate.
Reaching into ``Engine``'s underscore-prefixed replay methods is deliberate
same-package reuse, not a layering violation: both modules live in this one
``orchestrator`` package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import Engine


def validated_outputs(run_dir: str | Path, task_type: str | None = None) -> dict[str, Any]:
    """``{task_id: output}`` for every task whose CURRENT generation is
    validated, optionally filtered to one ``task_type``.

    A task_id re-created under a new digest (a lens/synthesis template
    edited -- see ``engine.py``'s module docstring on digest-keyed
    generations) is reported under its latest generation's own output only:
    an older generation's "submitted" record for the SAME (task_id,
    attempt-number) pair was always written EARLIER in the ledger, so the
    later write wins in the single forward pass below by construction (the
    ledger is append-only and chronologically ordered).
    """
    engine = Engine(run_dir)
    if not engine.ledger_exists():
        return {}
    records = engine._read_records()
    tasks = engine._rebuild(records)

    output_by_attempt: dict[tuple[str, int], Any] = {}
    for record in records:
        if record.event == "submitted":
            result = record.detail["result"]
            output_by_attempt[(record.task_id, result["attempt"])] = result["output"]

    found: dict[str, Any] = {}
    for task_id, task in tasks.items():
        if not task.done or not task.attempts:
            continue
        if task_type is not None and task.packet.task_type != task_type:
            continue
        attempt_number = task.attempts[-1].number
        output = output_by_attempt.get((task_id, attempt_number))
        if output is not None:
            found[task_id] = output
    return found
