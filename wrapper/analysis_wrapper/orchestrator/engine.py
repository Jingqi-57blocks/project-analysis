"""DAG runner + ledger for the orchestrator (57B-113 / 57B-115, M1).

The ledger — an append-only JSONL file at ``<run-dir>/tasks/ledger.jsonl``,
one :class:`~.contracts.LedgerRecord` per line — is the ONLY persistence this
module owns. All scheduling state (which tasks are ready/claimed/done/failed)
is reconstructed by replaying it from scratch on every call; there is no
separate state file that could drift out of sync with the ledger, so a crash
between any two lines leaves nothing to repair — the next call just replays
whatever made it to disk.

Digest-keyed intra-run resume: a task_id's packet may be re-created with a
different ``input_digest``/``template_version`` (its upstream inputs
changed); the engine treats that as a NEW generation of the same task_id —
prior attempts under the old digest stay in the ledger untouched (append-
only: history is never rewritten) but no longer count toward the new
generation's scheduling or attempt cap. A "validated" outcome only ever
short-circuits re-dispatch for the EXACT (task_id, input_digest,
template_version) it was recorded against.

Cascade failures (a task whose dependency failed permanently) are recorded
as ordinary ``failed`` ledger records whose ``reason`` starts with
:data:`CASCADE_REASON_PREFIX` — a convention on the free-text ``reason``
field, not a new ledger event or contract change (``contracts.py`` is left
untouched; ``LEDGER_EVENTS``/``_EVENT_DETAIL_FIELDS`` already allow a
``failed`` record with no preceding ``claimed`` record for the same
task_id, which is exactly what a cascade record is: the task was never
dispatched to an executor, so it consumes a synthetic attempt 1).

Concurrency: every read-modify-append sequence (creating tasks, claiming,
submitting, cascading) happens while holding an exclusive ``fcntl.flock`` on
a lockfile beside the ledger, so two executor processes racing on the same
run dir can never double-claim a task or interleave a corrupt line.
"""

from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from . import schemas
from .contracts import ExecutorInfo, LedgerRecord, TaskPacket, TaskResult

DEFAULT_MAX_ATTEMPTS = 3
LEDGER_DIRNAME = "tasks"
LEDGER_FILENAME = "ledger.jsonl"
LOCK_FILENAME = "ledger.lock"

# The marker on a "failed" ledger record's free-text `reason` that means
# "this task was never dispatched -- it failed because a dependency did".
# See the module docstring for why this is a text convention rather than a
# new ledger event.
CASCADE_REASON_PREFIX = "cascade: "


class EngineError(ValueError):
    """A fail-closed engine refusal: unknown task, dependency cycle, unknown
    dependency, corrupt ledger line, or a protocol-level misuse (submitting
    against a task with no outstanding claim, a stale attempt number, ...).
    Distinct from an ordinary task VALIDATION failure, which is always
    recorded to the ledger rather than raised."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _summarize_failures(failures: Sequence[Mapping[str, str]]) -> str:
    text = "; ".join(f"{item['check']}: {item['detail']}" for item in failures)
    return text[:4000]


@dataclass
class ClaimedTask:
    """What ``Engine.claim`` hands an executor: the packet plus the attempt
    number the executor must echo back on its ``TaskResult``."""

    packet: TaskPacket
    attempt: int


@dataclass
class _Attempt:
    number: int
    outcome: str | None = None  # None (outstanding) | "validated" | "failed"


@dataclass
class _Task:
    task_id: str
    packet: TaskPacket
    depends_on: tuple[str, ...]
    attempts: list[_Attempt] = field(default_factory=list)
    done: bool = False
    exhausted: bool = False       # ordinary attempts hit the cap
    cascade_failed: bool = False  # a dependency failed permanently

    @property
    def terminally_failed(self) -> bool:
        return self.exhausted or self.cascade_failed

    @property
    def outstanding(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].outcome is None


def _topo_order(tasks: Mapping[str, _Task]) -> list[str]:
    """Dependencies-before-dependents order over the full task set; raises
    :class:`EngineError` on an unknown dependency or a cycle (fail-closed)."""
    indegree = {task_id: len(task.depends_on) for task_id, task in tasks.items()}
    dependents: dict[str, list[str]] = {task_id: [] for task_id in tasks}
    for task_id, task in tasks.items():
        for dep in task.depends_on:
            if dep not in tasks:
                raise EngineError(f"task {task_id!r} depends_on unknown task_id {dep!r}")
            dependents[dep].append(task_id)
    queue = sorted(task_id for task_id, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while queue:
        queue.sort()
        current = queue.pop(0)
        order.append(current)
        for dependent in dependents[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if len(order) != len(tasks):
        remaining = sorted(set(tasks) - set(order))
        raise EngineError(f"dependency cycle detected involving: {remaining}")
    return order


def _is_ready(task: _Task, tasks: Mapping[str, _Task]) -> bool:
    if task.done or task.terminally_failed or task.outstanding:
        return False
    return all(tasks[dep].done for dep in task.depends_on)


class Engine:
    """One run directory's task DAG + ledger. Stateless between calls other
    than the paths it was constructed with — every method re-reads the
    ledger fresh under its own lock."""

    def __init__(self, run_dir: str | Path, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        if max_attempts < 1:
            raise EngineError("max_attempts must be at least 1")
        self.run_dir = Path(run_dir)
        self.max_attempts = max_attempts
        self.ledger_dir = self.run_dir / LEDGER_DIRNAME
        self.ledger_path = self.ledger_dir / LEDGER_FILENAME
        self.lock_path = self.ledger_dir / LOCK_FILENAME

    def ledger_exists(self) -> bool:
        return self.ledger_path.is_file()

    # -- ledger I/O ---------------------------------------------------- #

    def _read_records(self) -> list[LedgerRecord]:
        if not self.ledger_path.is_file():
            return []
        records: list[LedgerRecord] = []
        text = self.ledger_path.read_text("utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except ValueError as exc:
                raise EngineError(f"{self.ledger_path}:{line_no}: invalid JSON: {exc}") from exc
            try:
                records.append(LedgerRecord.from_dict(raw))
            except ValueError as exc:
                raise EngineError(
                    f"{self.ledger_path}:{line_no}: invalid ledger record: {exc}") from exc
        return records

    def _append_unlocked(self, record: LedgerRecord) -> None:
        """Append one record. Caller MUST already hold the lock (``_locked``)."""
        with open(self.ledger_path, "a", encoding="utf-8") as handle:
            handle.write(record.to_json_line())

    def _locked(self) -> "_LockedSection":
        return _LockedSection(self)

    def _rebuild(self, records: Sequence[LedgerRecord]) -> dict[str, _Task]:
        tasks: dict[str, _Task] = {}
        for record in records:
            if record.event == "created":
                packet = TaskPacket.from_dict(record.detail["task"])
                existing = tasks.get(packet.task_id)
                same_generation = existing is not None and (
                    existing.packet.input_digest == packet.input_digest
                    and existing.packet.template_version == packet.template_version)
                if not same_generation:
                    tasks[packet.task_id] = _Task(
                        task_id=packet.task_id, packet=packet, depends_on=packet.depends_on)
                # else: idempotent re-create under an unchanged digest -- no-op.
            elif record.event == "claimed":
                task = tasks[record.task_id]
                task.attempts.append(_Attempt(number=record.detail["attempt"]))
            elif record.event == "submitted":
                # The result is already contract-valid (LedgerRecord verified
                # it on the way in); nothing further to reconstruct from it —
                # the outcome lives in the "validated"/"failed" record that
                # follows.
                pass
            elif record.event == "validated":
                task = tasks[record.task_id]
                task.attempts[-1].outcome = "validated"
                task.done = True
            elif record.event == "failed":
                task = tasks[record.task_id]
                reason = record.detail["reason"]
                attempt_no = record.detail["attempt"]
                if reason.startswith(CASCADE_REASON_PREFIX):
                    task.cascade_failed = True
                    task.attempts.append(_Attempt(number=attempt_no, outcome="failed"))
                else:
                    if task.attempts and task.attempts[-1].outcome is None:
                        task.attempts[-1].outcome = "failed"
                    else:
                        # Defensive: a "failed" without a matching outstanding
                        # claim shouldn't happen via this engine's own API,
                        # but a hand-edited/foreign ledger shouldn't crash replay.
                        task.attempts.append(_Attempt(number=attempt_no, outcome="failed"))
                    if len(task.attempts) >= self.max_attempts:
                        task.exhausted = True
        return tasks

    # -- task creation --------------------------------------------------- #

    def create_tasks(self, packets: Sequence[TaskPacket]) -> list[str]:
        """Register a batch of task packets, validating the FULL resulting
        DAG (existing tasks + this batch) for unknown dependencies and
        cycles BEFORE writing anything (atomic, all-or-nothing). A packet
        whose task_id already exists with an IDENTICAL (input_digest,
        template_version) is a no-op (idempotent re-create); a DIFFERENT
        digest/version starts a new generation for that task_id.

        Returns the task_ids that actually got a new ``created`` record.
        """
        with self._locked():
            tasks = self._rebuild(self._read_records())
            combined: dict[str, TaskPacket] = {tid: t.packet for tid, t in tasks.items()}
            seen_in_batch: set[str] = set()
            for packet in packets:
                if packet.task_id in seen_in_batch:
                    raise EngineError(f"duplicate task_id in this batch: {packet.task_id!r}")
                seen_in_batch.add(packet.task_id)
                combined[packet.task_id] = packet
            probe = {tid: _Task(task_id=tid, packet=p, depends_on=p.depends_on)
                    for tid, p in combined.items()}
            _topo_order(probe)  # raises EngineError on unknown dep / cycle; fail closed

            created: list[str] = []
            for packet in packets:
                existing = tasks.get(packet.task_id)
                if existing is not None and (
                        existing.packet.input_digest == packet.input_digest
                        and existing.packet.template_version == packet.template_version):
                    continue
                self._append_unlocked(LedgerRecord(
                    event="created", task_id=packet.task_id, at=now_iso(),
                    detail={"task": packet.to_dict()}))
                created.append(packet.task_id)
            return created

    # -- cascade ----------------------------------------------------------- #

    def reconcile(self) -> list[str]:
        """Idempotently propagate permanent failure to the dependents of any
        terminally-failed task (transitively, in one pass). Returns the
        task_ids newly cascade-failed by this call (empty = already
        reconciled — safe to call as often as needed)."""
        with self._locked():
            tasks = self._rebuild(self._read_records())
            order = _topo_order(tasks)
            newly_failed: list[LedgerRecord] = []
            for task_id in order:
                task = tasks[task_id]
                if task.done or task.terminally_failed:
                    continue
                failed_dep = next(
                    (dep for dep in task.depends_on if tasks[dep].terminally_failed), None)
                if failed_dep is None:
                    continue
                task.cascade_failed = True  # so downstream dependents see it this same pass
                newly_failed.append(LedgerRecord(
                    event="failed", task_id=task_id, at=now_iso(),
                    detail={"reason": f"{CASCADE_REASON_PREFIX}dependency {failed_dep!r} "
                                     "failed permanently", "attempt": 1}))
            for record in newly_failed:
                self._append_unlocked(record)
            return [record.task_id for record in newly_failed]

    # -- scheduling -------------------------------------------------------- #

    def ready_task_ids(self) -> list[str]:
        self.reconcile()
        with self._locked():
            tasks = self._rebuild(self._read_records())
            return sorted(tid for tid, task in tasks.items() if _is_ready(task, tasks))

    def task_states(self) -> dict[str, str]:
        """The current TERMINAL-or-not state of every known task_id:
        ``"validated"``, ``"failed"`` (exhausted or cascaded — permanent),
        or ``"pending"`` (still queued, claimed, or blocked on a dependency
        that has not yet resolved either way). Callers driving a loop to
        completion (claim until nothing is ready) can rely on every task_id
        ending up ``"validated"``/``"failed"`` — a DAG can only run out of
        ready work once every root has resolved one way or the other, and
        that resolution propagates down through ``done``/cascade."""
        with self._locked():
            tasks = self._rebuild(self._read_records())
        states: dict[str, str] = {}
        for task_id, task in tasks.items():
            if task.done:
                states[task_id] = "validated"
            elif task.terminally_failed:
                states[task_id] = "failed"
            else:
                states[task_id] = "pending"
        return states

    def claim(self, count: int = 1, *, executor_kind: str, model: str,
             params: Mapping[str, object] | None = None) -> list[ClaimedTask]:
        if count < 1:
            raise EngineError("claim count must be at least 1")
        self.reconcile()  # separate lock cycle -- never nested with the one below
        with self._locked():
            tasks = self._rebuild(self._read_records())
            ready_ids = sorted(tid for tid, task in tasks.items() if _is_ready(task, tasks))
            executor_info = ExecutorInfo(kind=executor_kind, model=model, params=dict(params or {}))
            claimed: list[ClaimedTask] = []
            for task_id in ready_ids[:count]:
                task = tasks[task_id]
                attempt_no = len(task.attempts) + 1
                self._append_unlocked(LedgerRecord(
                    event="claimed", task_id=task_id, at=now_iso(),
                    detail={"executor": executor_info.to_dict(), "attempt": attempt_no}))
                claimed.append(ClaimedTask(packet=task.packet, attempt=attempt_no))
            return claimed

    # -- submission ---------------------------------------------------------- #

    def submit(self, task_id: str, raw_result: Mapping) -> dict:
        """Validate + record one submission: contract shape first (a
        malformed result never reaches a "submitted" record, since
        ``LedgerRecord`` itself demands an embedded, contract-valid
        ``TaskResult`` there), then the output schema for the task's
        ``task_type``. Always appends a "failed" or "validated" record —
        never silent — then reconciles cascades (in a separate, non-nested
        lock cycle) before returning.
        """
        with self._locked():
            tasks = self._rebuild(self._read_records())
            if task_id not in tasks:
                raise EngineError(f"unknown task_id: {task_id!r}")
            task = tasks[task_id]
            if not task.outstanding:
                raise EngineError(f"task {task_id!r} has no outstanding claim to submit against")
            current_attempt = task.attempts[-1].number

            try:
                result = TaskResult.from_dict(dict(raw_result))
            except ValueError as exc:
                reason = f"malformed task result: {exc}"
                self._append_unlocked(LedgerRecord(
                    event="failed", task_id=task_id, at=now_iso(),
                    detail={"reason": reason, "attempt": current_attempt}))
                outcome = {
                    "task_id": task_id, "status": "failed", "attempt": current_attempt,
                    "failures": [{"check": "task-result", "detail": reason, "location": ""}],
                }
            else:
                if result.task_id != task_id:
                    raise EngineError(
                        f"result task_id {result.task_id!r} does not match --task {task_id!r}")
                if result.attempt != current_attempt:
                    raise EngineError(
                        f"result.attempt {result.attempt} does not match the outstanding "
                        f"attempt {current_attempt} for {task_id!r} "
                        "(stale or duplicate submission)")

                self._append_unlocked(LedgerRecord(
                    event="submitted", task_id=task_id, at=now_iso(),
                    detail={"result": result.to_dict()}))

                if result.status == "ok":
                    failures = schemas.validate_output(task.packet.task_type, result.output)
                else:
                    failures = [{"check": "executor-status",
                                "detail": f"executor reported status={result.status!r}",
                                "location": "status"}]

                if not failures:
                    self._append_unlocked(LedgerRecord(
                        event="validated", task_id=task_id, at=now_iso(),
                        detail={"validation": {"passed": True, "failures": []}}))
                    outcome = {"task_id": task_id, "status": "validated",
                              "attempt": current_attempt, "failures": []}
                else:
                    self._append_unlocked(LedgerRecord(
                        event="failed", task_id=task_id, at=now_iso(),
                        detail={"reason": _summarize_failures(failures),
                                "attempt": current_attempt}))
                    outcome = {"task_id": task_id, "status": "failed",
                              "attempt": current_attempt, "failures": failures}

        # `reconcile()` acquires its own lock -- it must run only AFTER the
        # `with self._locked()` block above has released this one (never
        # nested; see the module docstring / _LockedSection).
        self.reconcile()
        return outcome


class _LockedSection:
    """`with engine._locked():` — an exclusive ``fcntl.flock`` around one
    read-modify-append sequence. A fresh file handle is opened per call
    (never nested within the same call stack: ``reconcile``/``claim``/
    ``submit`` always finish and release before any OTHER lock acquisition
    they trigger, so two flocks from the same process are never held at
    once)."""

    def __init__(self, engine: Engine):
        self._engine = engine
        self._handle = None

    def __enter__(self) -> None:
        self._engine.ledger_dir.mkdir(parents=True, exist_ok=True)
        self._handle = open(self._engine.lock_path, "a+")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)

    def __exit__(self, *exc_info) -> None:
        assert self._handle is not None
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
