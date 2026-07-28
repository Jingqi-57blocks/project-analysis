"""Task contracts for the orchestrator workstream (57B-113 / 57B-114, M0).

Plain dataclasses + JSON, matching the wrapper's existing contract style
(``targetspec.py``, ``identity.py``): ``to_dict``/``from_dict`` do the whole
validation job and raise ``ValueError`` with a precise message on malformed
input; a non-raising ``validate_*`` counterpart is also provided for callers
(an orchestrator loop, a ledger reader) that want to collect problems as
structured failures instead of stopping at the first exception.

This module defines the SHAPES exchanged between an orchestrator and its task
executors (a "task packet" going out, a "task result" coming back, and the
lifecycle events recorded to a JSONL ledger). It does not execute anything and
does not read or write a run directory — that belongs to a later milestone.
It carries its OWN contract version (``ORCHESTRATOR_CONTRACT_VERSION``);
existing wrapper artifact schemas (``findings.SCHEMA_VERSION``,
``module_map.MAP_SCHEMA_VERSION``, etc.) are untouched by this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

ORCHESTRATOR_CONTRACT_VERSION = "1.0.0"

# One task packet per unit of LLM/tool work the orchestrator hands out. Every
# task type maps to exactly one output schema in ``schemas.py``.
TASK_TYPES = frozenset({
    "lens-findings", "formation-proposal", "boundary-resolution", "rekey-resolution", "dedup-rank",
    "section-generate", "repair-edit-ops", "coherence-check", "selection-fetch",
})

LEDGER_EVENTS = frozenset({"created", "claimed", "released", "submitted", "validated", "failed"})
RESULT_STATUSES = frozenset({"ok", "failed"})

# A stable kebab-case slug — shared shape for task_id/finding-id-like handles
# across this module (mirrors module_map._MODULE_ID / findings._ID).
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_ISO8601 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


def _canonical_json(value: Any) -> str:
    """Deterministic JSON serialization used everywhere a digest is computed."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_slug(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError(f"{label} must be a stable kebab-case slug")
    return value


def _require_text(value: Any, label: str, *, allow_multiline: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if not allow_multiline and ("\n" in value or "\r" in value):
        raise ValueError(f"{label} must be one non-empty line")
    return value


def _require_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ISO8601.fullmatch(value):
        raise ValueError(f"{label} must be an ISO-8601 timestamp")
    return value


def _require_nonneg_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _require_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _strict_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} must contain exactly {sorted(fields)}")
    return value


# --------------------------------------------------------------------------- #
# TaskInput
# --------------------------------------------------------------------------- #

_TASK_INPUT_FIELDS = {"content", "digest"}


@dataclass(frozen=True)
class TaskInput:
    """One named input handed to a task: its content plus a content digest so
    a resumed/replayed task can detect a stale or tampered input."""

    content: str
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise ValueError("task input content must be a string")
        expected = _sha256(self.content)
        if self.digest != expected:
            raise ValueError("task input digest does not match its content")

    @classmethod
    def for_content(cls, content: str) -> "TaskInput":
        return cls(content=content, digest=_sha256(content))

    def to_dict(self) -> dict[str, str]:
        return {"content": self.content, "digest": self.digest}

    @classmethod
    def from_dict(cls, value: Any, label: str = "task input") -> "TaskInput":
        row = _strict_object(value, _TASK_INPUT_FIELDS, label)
        if not isinstance(row["content"], str):
            raise ValueError(f"{label}.content must be a string")
        if not isinstance(row["digest"], str) or not row["digest"]:
            raise ValueError(f"{label}.digest must be a non-empty string")
        return cls(content=row["content"], digest=row["digest"])


def compute_input_digest(instructions: str, inputs: Mapping[str, TaskInput],
                         output_schema_id: str) -> str:
    """The sha256 a ``TaskPacket`` is keyed on: instructions + every named
    input's content/digest + the declared output schema, canonically
    serialized. Two packets with the same digest are provably the same unit
    of work; a mismatched digest on load means the packet was tampered with
    or corrupted in transit."""
    payload = {
        "instructions": instructions,
        "inputs": {name: item.to_dict() for name, item in inputs.items()},
        "output_schema_id": output_schema_id,
    }
    return _sha256(_canonical_json(payload))


# --------------------------------------------------------------------------- #
# TaskPacket
# --------------------------------------------------------------------------- #

_TASK_PACKET_FIELDS = {
    "contract_version", "task_id", "task_type", "template_id", "template_version",
    "instructions", "inputs", "output_schema_id", "context_budget_tokens",
    "depends_on", "input_digest",
}


@dataclass(frozen=True)
class TaskPacket:
    task_id: str
    task_type: str
    template_id: str
    template_version: str
    instructions: str
    inputs: Mapping[str, TaskInput]
    output_schema_id: str
    context_budget_tokens: int
    depends_on: tuple[str, ...]
    input_digest: str

    def __post_init__(self) -> None:
        _require_slug(self.task_id, "task_id")
        if self.task_type not in TASK_TYPES:
            raise ValueError(f"task_type must be one of {sorted(TASK_TYPES)}")
        _require_text(self.template_id, "template_id")
        _require_text(self.template_version, "template_version")
        _require_text(self.instructions, "instructions", allow_multiline=True)
        if not isinstance(self.inputs, Mapping):
            raise ValueError("inputs must be a mapping of name to TaskInput")
        for name, item in self.inputs.items():
            _require_text(name, "input name")
            if not isinstance(item, TaskInput):
                raise ValueError(f"input {name!r} must be a TaskInput")
        _require_text(self.output_schema_id, "output_schema_id")
        _require_positive_int(self.context_budget_tokens, "context_budget_tokens")
        for dep in self.depends_on:
            _require_slug(dep, "depends_on entry")
        if self.task_id in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("depends_on must not contain duplicates")
        expected_digest = compute_input_digest(
            self.instructions, self.inputs, self.output_schema_id)
        if self.input_digest != expected_digest:
            raise ValueError(
                "input_digest does not match instructions/inputs/output_schema_id")

    @classmethod
    def create(cls, *, task_id: str, task_type: str, template_id: str,
               template_version: str, instructions: str,
               inputs: Mapping[str, str], output_schema_id: str,
               context_budget_tokens: int,
               depends_on: tuple[str, ...] = ()) -> "TaskPacket":
        """Build a packet from raw input CONTENT (name -> text); computes
        every TaskInput digest and the packet's own input_digest."""
        built_inputs = {name: TaskInput.for_content(text) for name, text in inputs.items()}
        digest = compute_input_digest(instructions, built_inputs, output_schema_id)
        return cls(
            task_id=task_id, task_type=task_type, template_id=template_id,
            template_version=template_version, instructions=instructions,
            inputs=built_inputs, output_schema_id=output_schema_id,
            context_budget_tokens=context_budget_tokens,
            depends_on=tuple(depends_on), input_digest=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": ORCHESTRATOR_CONTRACT_VERSION,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "template_id": self.template_id,
            "template_version": self.template_version,
            "instructions": self.instructions,
            "inputs": {name: item.to_dict() for name, item in sorted(self.inputs.items())},
            "output_schema_id": self.output_schema_id,
            "context_budget_tokens": self.context_budget_tokens,
            "depends_on": list(self.depends_on),
            "input_digest": self.input_digest,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TaskPacket":
        row = _strict_object(value, _TASK_PACKET_FIELDS, "task packet")
        if row["contract_version"] != ORCHESTRATOR_CONTRACT_VERSION:
            raise ValueError(
                f"task packet contract_version must be {ORCHESTRATOR_CONTRACT_VERSION!r}")
        raw_inputs = row["inputs"]
        if not isinstance(raw_inputs, dict):
            raise ValueError("task packet inputs must be an object")
        inputs = {name: TaskInput.from_dict(raw, f"inputs[{name!r}]")
                  for name, raw in raw_inputs.items()}
        raw_depends_on = row["depends_on"]
        if not isinstance(raw_depends_on, list) or not all(
                isinstance(item, str) for item in raw_depends_on):
            raise ValueError("task packet depends_on must be a string list")
        return cls(
            task_id=row["task_id"], task_type=row["task_type"],
            template_id=row["template_id"], template_version=row["template_version"],
            instructions=row["instructions"], inputs=inputs,
            output_schema_id=row["output_schema_id"],
            context_budget_tokens=row["context_budget_tokens"],
            depends_on=tuple(raw_depends_on), input_digest=row["input_digest"],
        )


def validate_task_packet(value: Any) -> list[dict[str, str]]:
    """Non-raising counterpart to :meth:`TaskPacket.from_dict`: returns a list
    of structured failures (empty = valid) instead of raising, so a caller
    iterating over many packets (e.g. a ledger reader) can report every
    problem rather than stopping at the first one."""
    try:
        TaskPacket.from_dict(value)
    except ValueError as exc:
        return [{"check": "task-packet", "detail": str(exc), "location": ""}]
    return []


# --------------------------------------------------------------------------- #
# TaskResult
# --------------------------------------------------------------------------- #

_EXECUTOR_FIELDS = {"kind", "model", "params"}
_TOKENS_FIELDS = {"input", "output"}
_VALIDATION_FIELDS = {"passed", "failures"}
_TIMING_FIELDS = {"started_at", "finished_at", "wall_clock_s"}
_TASK_RESULT_FIELDS = {
    "contract_version", "task_id", "status", "output", "executor", "timing",
    "tokens", "validation", "attempt",
}


def _require_failure_row(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"check", "detail", "location"}:
        raise ValueError(f"{label} must contain exactly ['check', 'detail', 'location']")
    for key in ("check", "detail", "location"):
        if not isinstance(value[key], str):
            raise ValueError(f"{label}.{key} must be a string")
    return {"check": value["check"], "detail": value["detail"], "location": value["location"]}


@dataclass(frozen=True)
class ExecutorInfo:
    kind: str
    model: str
    params: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_text(self.kind, "executor.kind")
        _require_text(self.model, "executor.model")
        if not isinstance(self.params, Mapping):
            raise ValueError("executor.params must be a mapping")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "model": self.model, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutorInfo":
        row = _strict_object(value, _EXECUTOR_FIELDS, "executor")
        if not isinstance(row["params"], dict):
            raise ValueError("executor.params must be an object")
        return cls(kind=row["kind"], model=row["model"], params=row["params"])


@dataclass(frozen=True)
class TaskTiming:
    started_at: str
    finished_at: str
    wall_clock_s: float

    def __post_init__(self) -> None:
        _require_timestamp(self.started_at, "timing.started_at")
        _require_timestamp(self.finished_at, "timing.finished_at")
        if isinstance(self.wall_clock_s, bool) or not isinstance(
                self.wall_clock_s, (int, float)) or self.wall_clock_s < 0:
            raise ValueError("timing.wall_clock_s must be a non-negative number")

    def to_dict(self) -> dict[str, Any]:
        return {"started_at": self.started_at, "finished_at": self.finished_at,
                "wall_clock_s": self.wall_clock_s}

    @classmethod
    def from_dict(cls, value: Any) -> "TaskTiming":
        row = _strict_object(value, _TIMING_FIELDS, "timing")
        return cls(started_at=row["started_at"], finished_at=row["finished_at"],
                    wall_clock_s=row["wall_clock_s"])


@dataclass(frozen=True)
class TokenUsage:
    input: int
    output: int

    def __post_init__(self) -> None:
        _require_nonneg_int(self.input, "tokens.input")
        _require_nonneg_int(self.output, "tokens.output")

    def to_dict(self) -> dict[str, int]:
        return {"input": self.input, "output": self.output}

    @classmethod
    def from_dict(cls, value: Any) -> "TokenUsage":
        row = _strict_object(value, _TOKENS_FIELDS, "tokens")
        return cls(input=row["input"], output=row["output"])


@dataclass(frozen=True)
class ValidationOutcome:
    passed: bool
    failures: tuple[Mapping[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise ValueError("validation.passed must be a boolean")
        if self.passed and self.failures:
            raise ValueError("validation.passed cannot be true with non-empty failures")
        if not self.passed and not self.failures:
            raise ValueError("validation.passed false requires at least one failure")
        for index, failure in enumerate(self.failures):
            _require_failure_row(dict(failure), f"validation.failures[{index}]")

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "failures": [dict(item) for item in self.failures]}

    @classmethod
    def from_dict(cls, value: Any) -> "ValidationOutcome":
        row = _strict_object(value, _VALIDATION_FIELDS, "validation")
        failures = row["failures"]
        if not isinstance(failures, list):
            raise ValueError("validation.failures must be a list")
        return cls(passed=row["passed"],
                    failures=tuple(_require_failure_row(item, f"validation.failures[{i}]")
                                   for i, item in enumerate(failures)))


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: str
    output: Any
    executor: ExecutorInfo
    timing: TaskTiming
    tokens: TokenUsage | None
    validation: ValidationOutcome
    attempt: int

    def __post_init__(self) -> None:
        _require_slug(self.task_id, "task_id")
        if self.status not in RESULT_STATUSES:
            raise ValueError(f"status must be one of {sorted(RESULT_STATUSES)}")
        if not isinstance(self.executor, ExecutorInfo):
            raise ValueError("executor must be an ExecutorInfo")
        if not isinstance(self.timing, TaskTiming):
            raise ValueError("timing must be a TaskTiming")
        if self.tokens is not None and not isinstance(self.tokens, TokenUsage):
            raise ValueError("tokens must be a TokenUsage or None")
        if not isinstance(self.validation, ValidationOutcome):
            raise ValueError("validation must be a ValidationOutcome")
        _require_positive_int(self.attempt, "attempt")
        try:
            json.dumps(self.output)
        except TypeError as exc:
            raise ValueError(f"output must be JSON-serializable: {exc}") from exc
        if self.status == "failed" and self.validation.passed:
            raise ValueError("a failed result cannot carry a passed validation outcome")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": ORCHESTRATOR_CONTRACT_VERSION,
            "task_id": self.task_id,
            "status": self.status,
            "output": self.output,
            "executor": self.executor.to_dict(),
            "timing": self.timing.to_dict(),
            "tokens": self.tokens.to_dict() if self.tokens is not None else None,
            "validation": self.validation.to_dict(),
            "attempt": self.attempt,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TaskResult":
        row = _strict_object(value, _TASK_RESULT_FIELDS, "task result")
        if row["contract_version"] != ORCHESTRATOR_CONTRACT_VERSION:
            raise ValueError(
                f"task result contract_version must be {ORCHESTRATOR_CONTRACT_VERSION!r}")
        tokens = None if row["tokens"] is None else TokenUsage.from_dict(row["tokens"])
        return cls(
            task_id=row["task_id"], status=row["status"], output=row["output"],
            executor=ExecutorInfo.from_dict(row["executor"]),
            timing=TaskTiming.from_dict(row["timing"]), tokens=tokens,
            validation=ValidationOutcome.from_dict(row["validation"]),
            attempt=row["attempt"],
        )


def validate_task_result(value: Any) -> list[dict[str, str]]:
    """Non-raising counterpart to :meth:`TaskResult.from_dict`."""
    try:
        TaskResult.from_dict(value)
    except ValueError as exc:
        return [{"check": "task-result", "detail": str(exc), "location": ""}]
    return []


# --------------------------------------------------------------------------- #
# Ledger records (JSONL lifecycle log)
# --------------------------------------------------------------------------- #

_LEDGER_RECORD_FIELDS = {"contract_version", "event", "task_id", "at", "detail"}
# Every event's detail carries exactly these keys (open dicts within — the
# nested shapes are validated by the relevant contract's own from_dict).
_EVENT_DETAIL_FIELDS = {
    "created": {"task"},
    "claimed": {"executor", "attempt"},
    "released": {"reason", "attempt"},
    "submitted": {"result"},
    "validated": {"validation"},
    "failed": {"reason", "attempt"},
}


@dataclass(frozen=True)
class LedgerRecord:
    event: str
    task_id: str
    at: str
    detail: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.event not in LEDGER_EVENTS:
            raise ValueError(f"ledger event must be one of {sorted(LEDGER_EVENTS)}")
        _require_slug(self.task_id, "task_id")
        _require_timestamp(self.at, "at")
        if not isinstance(self.detail, Mapping):
            raise ValueError("ledger record detail must be a mapping")
        expected_keys = _EVENT_DETAIL_FIELDS[self.event]
        if set(self.detail) != expected_keys:
            raise ValueError(
                f"ledger event {self.event!r} detail must contain exactly "
                f"{sorted(expected_keys)}")
        if self.event == "created":
            TaskPacket.from_dict(self.detail["task"])
        elif self.event == "claimed":
            ExecutorInfo.from_dict(self.detail["executor"])
            _require_positive_int(self.detail["attempt"], "detail.attempt")
        elif self.event == "released":
            _require_text(self.detail["reason"], "detail.reason", allow_multiline=True)
            _require_positive_int(self.detail["attempt"], "detail.attempt")
        elif self.event == "submitted":
            TaskResult.from_dict(self.detail["result"])
        elif self.event == "validated":
            ValidationOutcome.from_dict(self.detail["validation"])
        elif self.event == "failed":
            _require_text(self.detail["reason"], "detail.reason", allow_multiline=True)
            _require_positive_int(self.detail["attempt"], "detail.attempt")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": ORCHESTRATOR_CONTRACT_VERSION,
            "event": self.event,
            "task_id": self.task_id,
            "at": self.at,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "LedgerRecord":
        row = _strict_object(value, _LEDGER_RECORD_FIELDS, "ledger record")
        if row["contract_version"] != ORCHESTRATOR_CONTRACT_VERSION:
            raise ValueError(
                f"ledger record contract_version must be {ORCHESTRATOR_CONTRACT_VERSION!r}")
        detail = row["detail"]
        if not isinstance(detail, dict):
            raise ValueError("ledger record detail must be an object")
        return cls(event=row["event"], task_id=row["task_id"], at=row["at"], detail=detail)

    def to_json_line(self) -> str:
        return _canonical_json(self.to_dict()) + "\n"


def validate_ledger_record(value: Any) -> list[dict[str, str]]:
    """Non-raising counterpart to :meth:`LedgerRecord.from_dict`."""
    try:
        LedgerRecord.from_dict(value)
    except ValueError as exc:
        return [{"check": "ledger-record", "detail": str(exc), "location": ""}]
    return []
