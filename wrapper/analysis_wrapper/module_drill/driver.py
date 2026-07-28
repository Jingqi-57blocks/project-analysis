"""Module Drill task-driver over the shared append-only orchestrator ledger.

This driver owns Module Drill lifecycle mechanics only.  It does not invent
feature evidence or render documents: later phases register the concrete task
waves and the final audit.  The ledger is the sole task-state authority;
``run-state.json`` is a replaceable, verified projection for callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..executor import replace_artifact_text
from ..orchestrator.contracts import TaskPacket
from ..orchestrator.engine import ClaimedTask, Engine
from ..orchestrator.results import validated_outputs
from .context import SourceContext, load as load_source_context
from .protocol import MODULE_TASK_TYPES, schema_for_task_type
from .run_state import AuditResult, RunStateProjection
from .validation import ContractError, sha256_json


_PENDING_AUDIT_CHECK = "pending-final-module-audit"
_BASE_AUDIT_CHECKS = ("source-integrity", "ledger-integrity")


@dataclass(frozen=True)
class DriverStatus:
    """The public, derived status of an incomplete or later finalized run."""

    run_id: str
    task_states: dict[str, str]
    complete: bool
    audit: AuditResult


class ModuleDriver:
    """One Module Drill run's safe task registration, claiming, and submission.

    A module run cannot report completion through this class.  Completion is
    exclusively the later final-audit phase's responsibility, so an otherwise
    valid ledger can never be mistaken for an authoritative feature recovery.
    """

    def __init__(self, module_run: str | Path, *, max_attempts: int = 3) -> None:
        self.run = Path(module_run).expanduser().resolve()
        self.context: SourceContext = load_source_context(self.run)
        self.engine = Engine(self.run, max_attempts=max_attempts)

    def _state(self) -> RunStateProjection:
        try:
            return RunStateProjection.from_dict(
                json.loads((self.run / "run-state.json").read_text("utf-8")))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ContractError(f"module run-state is invalid: {exc}") from exc

    def _ledger_digest(self) -> str:
        records = self.engine._read_records()
        return sha256_json([record.to_dict() for record in records])

    def _pending_audit(self, task_states: Mapping[str, str]) -> AuditResult:
        failed = [task_id for task_id, state in sorted(task_states.items()) if state == "failed"]
        failed.append(_PENDING_AUDIT_CHECK)
        return AuditResult(False, _BASE_AUDIT_CHECKS, tuple(failed))

    def refresh(self) -> DriverStatus:
        """Revalidate source freshness and atomically derive the run projection."""
        self.context = load_source_context(self.run)
        prior = self._state()
        task_states = self.engine.task_states()
        projection = RunStateProjection(
            run_id=prior.run_id,
            source_manifest_digest=prior.source_manifest_digest,
            ledger_digest=self._ledger_digest(),
            complete=False,
            audit=self._pending_audit(task_states),
        )
        replace_artifact_text(
            self.run / "run-state.json",
            json.dumps(projection.to_dict(), indent=2, sort_keys=True) + "\n",
        )
        return DriverStatus(projection.run_id, task_states, projection.complete, projection.audit)

    def register(self, packets: Sequence[TaskPacket]) -> list[str]:
        """Append Module Drill packets after rejecting foreign task contracts."""
        checked = list(packets)
        for packet in checked:
            if packet.task_type not in MODULE_TASK_TYPES:
                raise ContractError(f"module driver refuses non-module task type {packet.task_type!r}")
            expected_schema = schema_for_task_type(packet.task_type)
            if packet.output_schema_id != expected_schema:
                raise ContractError(
                    f"module task {packet.task_id!r} must use {expected_schema!r}, "
                    f"not {packet.output_schema_id!r}")
        self.context = load_source_context(self.run)
        created = self.engine.create_tasks(checked)
        self.refresh()
        return created

    def claim(self, count: int, *, executor_kind: str, model: str,
              params: Mapping[str, object] | None = None) -> list[ClaimedTask]:
        self.context = load_source_context(self.run)
        claimed = self.engine.claim(count, executor_kind=executor_kind, model=model, params=params)
        self.refresh()
        return claimed

    def submit(self, task_id: str, raw_result: Mapping[str, Any]) -> dict[str, Any]:
        self.context = load_source_context(self.run)
        outcome = self.engine.submit(task_id, raw_result)
        self.refresh()
        return outcome

    def status(self) -> DriverStatus:
        """Return a verified projection without erasing an authoritative completion.

        ``refresh`` is deliberately used after mutable ledger operations so an
        incomplete run always advertises its pending final audit.  A completed
        run is different: its finalizer has already written the authoritative
        audit projection.  Recomputing the generic pre-final projection while
        merely asking for status would otherwise turn a passing run back into
        ``complete=false``.  Preserve it only while both the source context and
        append-only ledger still match; any drift falls back to the fail-closed
        pending projection.
        """
        self.context = load_source_context(self.run)
        prior = self._state()
        task_states = self.engine.task_states()
        if prior.complete and prior.audit.passed and prior.ledger_digest == self._ledger_digest():
            return DriverStatus(prior.run_id, task_states, True, prior.audit)
        return self.refresh()

    def validated_task(self, task_id: str) -> tuple[TaskPacket, Any]:
        """Return one current validated task packet and its submitted output.

        Downstream Module Drill phases must not independently parse the
        append-only ledger.  This method keeps the current-generation check
        and output lookup adjacent to the driver that owns the ledger.
        """
        self.context = load_source_context(self.run)
        records = self.engine._read_records()
        task = self.engine._rebuild(records).get(task_id)
        if task is None or not task.done:
            raise ContractError(f"module task {task_id!r} is not validated")
        output = validated_outputs(self.run, task_type=task.packet.task_type).get(task_id)
        if output is None:
            raise ContractError(f"module task {task_id!r} has no validated output")
        return task.packet, output
