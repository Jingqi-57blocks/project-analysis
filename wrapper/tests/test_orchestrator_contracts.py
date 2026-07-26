"""Contract round-trip and fail-closed validation tests for the orchestrator
task envelopes (57B-114 M0) — domain-neutral, no real run directory needed."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator import contracts as C


def _packet(**overrides) -> C.TaskPacket:
    kwargs = dict(
        task_id="task-lens-structure",
        task_type="lens-findings",
        template_id="lens-findings-template",
        template_version="1.0.0",
        instructions="Return findings for the structure lens.",
        inputs={"signals": "views: 3 lines"},
        output_schema_id="lens-findings.v1",
        context_budget_tokens=4000,
    )
    kwargs.update(overrides)
    return C.TaskPacket.create(**kwargs)


def test_task_packet_round_trips_and_computes_its_own_digest():
    packet = _packet()
    assert packet.input_digest == C.compute_input_digest(
        packet.instructions, packet.inputs, packet.output_schema_id)
    restored = C.TaskPacket.from_dict(packet.to_dict())
    assert restored == packet
    assert restored.to_dict() == packet.to_dict()


def test_task_packet_rejects_unknown_task_type():
    with pytest.raises(ValueError, match="task_type must be one of"):
        _packet(task_type="not-a-real-task-type")


def test_task_packet_rejects_tampered_input_digest():
    doc = _packet().to_dict()
    doc["input_digest"] = "0" * 64
    with pytest.raises(ValueError, match="input_digest does not match"):
        C.TaskPacket.from_dict(doc)


def test_task_packet_rejects_tampered_input_content_digest():
    doc = _packet().to_dict()
    doc["inputs"]["signals"]["content"] = "tampered content"
    with pytest.raises(ValueError, match="digest does not match its content"):
        C.TaskPacket.from_dict(doc)


def test_task_packet_rejects_self_dependency_and_duplicate_depends_on():
    with pytest.raises(ValueError, match="cannot depend on itself"):
        _packet(task_id="task-a", depends_on=("task-a",))
    with pytest.raises(ValueError, match="duplicates"):
        _packet(depends_on=("task-b", "task-b"))


def test_task_packet_rejects_wrong_contract_version():
    doc = _packet().to_dict()
    doc["contract_version"] = "0.0.1"
    with pytest.raises(ValueError, match="contract_version must be"):
        C.TaskPacket.from_dict(doc)


def test_task_packet_rejects_unknown_and_missing_fields():
    doc = _packet().to_dict()
    doc["extra_field"] = "nope"
    with pytest.raises(ValueError, match="must contain exactly"):
        C.TaskPacket.from_dict(doc)
    doc = _packet().to_dict()
    del doc["task_id"]
    with pytest.raises(ValueError, match="must contain exactly"):
        C.TaskPacket.from_dict(doc)


def test_task_packet_rejects_non_positive_context_budget():
    with pytest.raises(ValueError, match="positive integer"):
        _packet(context_budget_tokens=0)


def test_validate_task_packet_is_non_raising():
    assert C.validate_task_packet(_packet().to_dict()) == []
    doc = _packet().to_dict()
    doc["input_digest"] = "0" * 64
    failures = C.validate_task_packet(doc)
    assert len(failures) == 1
    assert failures[0]["check"] == "task-packet"
    assert "input_digest" in failures[0]["detail"]


def _result(**overrides) -> C.TaskResult:
    kwargs = dict(
        task_id="task-lens-structure",
        status="ok",
        output={"findings": []},
        executor=C.ExecutorInfo(kind="llm", model="claude-x", params={}),
        timing=C.TaskTiming(started_at="2026-07-26T00:00:00Z",
                            finished_at="2026-07-26T00:00:05Z", wall_clock_s=5.0),
        tokens=C.TokenUsage(input=100, output=50),
        validation=C.ValidationOutcome(passed=True, failures=()),
        attempt=1,
    )
    kwargs.update(overrides)
    return C.TaskResult(**kwargs)


def test_task_result_round_trips_including_null_tokens():
    result = _result(tokens=None)
    restored = C.TaskResult.from_dict(result.to_dict())
    assert restored == result
    assert restored.to_dict()["tokens"] is None


def test_task_result_rejects_output_that_is_not_json_serializable():
    with pytest.raises(ValueError, match="JSON-serializable"):
        _result(output={"bad": object()})


def test_task_result_rejects_inconsistent_validation_outcome():
    with pytest.raises(ValueError, match="cannot be true with non-empty failures"):
        C.ValidationOutcome(passed=True, failures=({"check": "x", "detail": "y", "location": ""},))
    with pytest.raises(ValueError, match="requires at least one failure"):
        C.ValidationOutcome(passed=False, failures=())


def test_task_result_rejects_failed_status_with_passed_validation():
    with pytest.raises(ValueError, match="failed result cannot carry a passed validation"):
        _result(status="failed", validation=C.ValidationOutcome(passed=True, failures=()))


def test_task_result_rejects_bad_timestamp_and_negative_wall_clock():
    with pytest.raises(ValueError, match="ISO-8601"):
        _result(timing=C.TaskTiming(started_at="not-a-time",
                                    finished_at="2026-07-26T00:00:05Z", wall_clock_s=1.0))
    with pytest.raises(ValueError, match="non-negative number"):
        _result(timing=C.TaskTiming(started_at="2026-07-26T00:00:00Z",
                                    finished_at="2026-07-26T00:00:05Z", wall_clock_s=-1.0))


def test_task_result_rejects_negative_token_counts():
    with pytest.raises(ValueError, match="non-negative integer"):
        C.TokenUsage(input=-1, output=0)


def test_validate_task_result_is_non_raising():
    assert C.validate_task_result(_result().to_dict()) == []
    doc = _result().to_dict()
    doc["status"] = "not-a-status"
    failures = C.validate_task_result(doc)
    assert len(failures) == 1 and failures[0]["check"] == "task-result"


def _ledger_record(event: str, detail: dict, task_id: str = "task-lens-structure") -> C.LedgerRecord:
    return C.LedgerRecord(event=event, task_id=task_id, at="2026-07-26T00:00:00Z", detail=detail)


def test_ledger_record_created_embeds_and_validates_a_task_packet():
    record = _ledger_record("created", {"task": _packet().to_dict()})
    line = record.to_json_line()
    assert line.endswith("\n")
    assert line.count("\n") == 1
    restored = C.LedgerRecord.from_dict(record.to_dict())
    assert restored == record


def test_ledger_record_created_rejects_a_malformed_embedded_packet():
    bad_packet = _packet().to_dict()
    bad_packet["task_type"] = "not-a-real-task-type"
    with pytest.raises(ValueError, match="task_type must be one of"):
        _ledger_record("created", {"task": bad_packet})


def test_ledger_record_claimed_and_failed_require_positive_attempt():
    _ledger_record("claimed", {"executor": {"kind": "llm", "model": "x", "params": {}},
                               "attempt": 1})
    with pytest.raises(ValueError, match="positive integer"):
        _ledger_record("claimed", {"executor": {"kind": "llm", "model": "x", "params": {}},
                                   "attempt": 0})
    _ledger_record("failed", {"reason": "executor timed out", "attempt": 2})
    with pytest.raises(ValueError, match="positive integer"):
        _ledger_record("failed", {"reason": "x", "attempt": -1})


def test_ledger_record_rejects_wrong_detail_keys_for_its_event():
    with pytest.raises(ValueError, match="must contain exactly"):
        _ledger_record("created", {"task": _packet().to_dict(), "extra": 1})
    with pytest.raises(ValueError, match="must contain exactly"):
        _ledger_record("validated", {"task": _packet().to_dict()})


def test_ledger_record_rejects_unknown_event():
    with pytest.raises(ValueError, match="event must be one of"):
        _ledger_record("archived", {})


def test_validate_ledger_record_is_non_raising():
    valid = _ledger_record("failed", {"reason": "x", "attempt": 1}).to_dict()
    assert C.validate_ledger_record(valid) == []
    invalid = dict(valid, event="archived")
    failures = C.validate_ledger_record(invalid)
    assert len(failures) == 1 and failures[0]["check"] == "ledger-record"


def test_task_input_for_content_and_digest_mismatch():
    item = C.TaskInput.for_content("hello world")
    assert item.digest == C.TaskInput.for_content("hello world").digest
    with pytest.raises(ValueError, match="digest does not match"):
        C.TaskInput(content="hello world", digest="0" * 64)
