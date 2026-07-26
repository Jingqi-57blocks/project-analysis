"""DAG runner + ledger tests (57B-113 / 57B-115, M1) — ordering, cascade,
digest-keyed resume/re-dispatch, attempts cap, and lock-protected concurrent
claiming. No run directory dependencies (schemas' own structural checks are
covered by test_orchestrator_schemas.py; this file is about the ENGINE)."""

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskPacket, TaskResult, TaskTiming, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine, EngineError


def _packet(task_id: str, *, depends_on=(), content: str = "y") -> TaskPacket:
    return TaskPacket.create(
        task_id=task_id, task_type="lens-findings", template_id="tpl",
        template_version="1.0.0", instructions="do it", inputs={"x": content},
        output_schema_id="lens-findings.v1", context_budget_tokens=1000,
        depends_on=depends_on)


def _valid_result(task_id: str, attempt: int, *, output=None) -> dict:
    output = output if output is not None else {"findings": [], "coverage": []}
    return TaskResult(
        task_id=task_id, status="ok", output=output,
        executor=ExecutorInfo(kind="manual", model="m", params={}),
        timing=TaskTiming(started_at="2026-07-26T00:00:00Z",
                         finished_at="2026-07-26T00:00:01Z", wall_clock_s=1.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()),
        attempt=attempt,
    ).to_dict()


def _invalid_result(task_id: str, attempt: int) -> dict:
    return _valid_result(task_id, attempt, output={"not": "a-valid-shape"})


# --------------------------------------------------------------------------- #
# creation / DAG validity
# --------------------------------------------------------------------------- #

def test_create_tasks_writes_ledger_and_is_idempotent_on_unchanged_digest(tmp_path):
    engine = Engine(tmp_path)
    assert not engine.ledger_exists()
    created = engine.create_tasks([_packet("a"), _packet("b", depends_on=("a",))])
    assert created == ["a", "b"]
    assert engine.ledger_exists()

    # Re-creating the SAME packets (unchanged digest) is a no-op.
    again = engine.create_tasks([_packet("a")])
    assert again == []
    lines = (tmp_path / "tasks" / "ledger.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_create_tasks_rejects_unknown_dependency_atomically(tmp_path):
    engine = Engine(tmp_path)
    with pytest.raises(EngineError, match="unknown task_id"):
        engine.create_tasks([_packet("a", depends_on=("missing",))])
    assert not engine.ledger_exists()  # nothing written -- fail closed, all-or-nothing


def test_create_tasks_rejects_cycles_atomically(tmp_path):
    engine = Engine(tmp_path)
    with pytest.raises(EngineError, match="cycle"):
        engine.create_tasks([_packet("x", depends_on=("y",)), _packet("y", depends_on=("x",))])
    assert not engine.ledger_exists()


def test_create_tasks_rejects_duplicate_task_id_within_one_batch(tmp_path):
    engine = Engine(tmp_path)
    with pytest.raises(EngineError, match="duplicate task_id"):
        engine.create_tasks([_packet("a"), _packet("a")])


# --------------------------------------------------------------------------- #
# ordering / readiness
# --------------------------------------------------------------------------- #

def test_dependents_are_not_ready_until_their_dependency_validates(tmp_path):
    engine = Engine(tmp_path)
    engine.create_tasks([_packet("a"), _packet("b", depends_on=("a",))])
    assert engine.ready_task_ids() == ["a"]

    claimed = engine.claim(5, executor_kind="manual", model="m")
    assert [c.packet.task_id for c in claimed] == ["a"]
    assert engine.ready_task_ids() == []  # a outstanding, b still blocked

    outcome = engine.submit("a", _valid_result("a", claimed[0].attempt))
    assert outcome["status"] == "validated"
    assert engine.ready_task_ids() == ["b"]


def test_claim_never_reclaims_an_outstanding_task(tmp_path):
    engine = Engine(tmp_path)
    engine.create_tasks([_packet("a")])
    first = engine.claim(5, executor_kind="manual", model="m")
    assert len(first) == 1
    second = engine.claim(5, executor_kind="manual", model="m")
    assert second == []


def test_claim_respects_the_requested_count(tmp_path):
    engine = Engine(tmp_path)
    engine.create_tasks([_packet(f"t{i}") for i in range(5)])
    claimed = engine.claim(3, executor_kind="manual", model="m")
    assert len(claimed) == 3
    assert len(engine.ready_task_ids()) == 2


# --------------------------------------------------------------------------- #
# attempts cap + cascade
# --------------------------------------------------------------------------- #

def test_failed_attempts_are_retried_up_to_the_cap_then_exhausted(tmp_path):
    engine = Engine(tmp_path, max_attempts=2)
    engine.create_tasks([_packet("a")])

    claimed = engine.claim(1, executor_kind="manual", model="m")
    outcome = engine.submit("a", _invalid_result("a", claimed[0].attempt))
    assert outcome["status"] == "failed" and outcome["attempt"] == 1
    assert engine.ready_task_ids() == ["a"]  # one attempt left

    claimed = engine.claim(1, executor_kind="manual", model="m")
    assert claimed[0].attempt == 2
    outcome = engine.submit("a", _invalid_result("a", claimed[0].attempt))
    assert outcome["status"] == "failed" and outcome["attempt"] == 2
    assert engine.ready_task_ids() == []  # exhausted -- never retried again
    assert engine.task_states()["a"] == "failed"


def test_permanent_failure_cascades_to_transitive_dependents(tmp_path):
    engine = Engine(tmp_path, max_attempts=1)
    engine.create_tasks([_packet("a"), _packet("b", depends_on=("a",)),
                        _packet("c", depends_on=("b",))])
    claimed = engine.claim(1, executor_kind="manual", model="m")
    engine.submit("a", _invalid_result("a", claimed[0].attempt))  # exhausts at cap=1

    states = engine.task_states()
    assert states["a"] == "failed"
    assert states["b"] == "failed"
    assert states["c"] == "failed"
    assert engine.ready_task_ids() == []

    ledger_events = [json.loads(line) for line in
                     (tmp_path / "tasks" / "ledger.jsonl").read_text().splitlines()]
    cascade_reasons = {row["task_id"]: row["detail"]["reason"] for row in ledger_events
                       if row["event"] == "failed" and row["task_id"] in {"b", "c"}}
    assert "cascade:" in cascade_reasons["b"]
    assert "cascade:" in cascade_reasons["c"]


def test_reconcile_is_idempotent(tmp_path):
    engine = Engine(tmp_path, max_attempts=1)
    engine.create_tasks([_packet("a"), _packet("b", depends_on=("a",))])
    claimed = engine.claim(1, executor_kind="manual", model="m")
    engine.submit("a", _invalid_result("a", claimed[0].attempt))
    first_pass = engine.reconcile()
    assert first_pass == []  # already reconciled by submit()'s own call
    second_pass = engine.reconcile()
    assert second_pass == []


# --------------------------------------------------------------------------- #
# digest-keyed resume / re-dispatch
# --------------------------------------------------------------------------- #

def test_validated_task_is_never_redispatched_on_replay(tmp_path):
    engine = Engine(tmp_path)
    engine.create_tasks([_packet("a")])
    claimed = engine.claim(1, executor_kind="manual", model="m")
    engine.submit("a", _valid_result("a", claimed[0].attempt))

    # A fresh Engine instance replaying the SAME ledger sees it as done.
    resumed = Engine(tmp_path)
    assert resumed.task_states()["a"] == "validated"
    assert resumed.ready_task_ids() == []
    assert resumed.claim(5, executor_kind="manual", model="m") == []


def test_changed_input_digest_starts_a_new_generation_and_resets_attempts(tmp_path):
    engine = Engine(tmp_path)
    engine.create_tasks([_packet("a", content="v1")])
    claimed = engine.claim(1, executor_kind="manual", model="m")
    engine.submit("a", _valid_result("a", claimed[0].attempt))
    assert engine.task_states()["a"] == "validated"

    # Re-create task "a" with DIFFERENT input content -> different digest.
    created = engine.create_tasks([_packet("a", content="v2-changed")])
    assert created == ["a"]
    assert engine.task_states()["a"] == "pending"
    assert engine.ready_task_ids() == ["a"]

    claimed2 = engine.claim(1, executor_kind="manual", model="m")
    assert claimed2[0].attempt == 1  # fresh generation, fresh attempt counter

    # History from the OLD generation is still in the ledger (append-only).
    lines = (tmp_path / "tasks" / "ledger.jsonl").read_text().splitlines()
    created_events = [json.loads(line) for line in lines if json.loads(line)["event"] == "created"]
    assert len(created_events) == 2


# --------------------------------------------------------------------------- #
# submit protocol invariants
# --------------------------------------------------------------------------- #

def test_submit_rejects_unknown_task_and_non_outstanding_task(tmp_path):
    engine = Engine(tmp_path)
    engine.create_tasks([_packet("a")])
    with pytest.raises(EngineError, match="unknown task_id"):
        engine.submit("nope", _valid_result("nope", 1))
    with pytest.raises(EngineError, match="no outstanding claim"):
        engine.submit("a", _valid_result("a", 1))  # never claimed


def test_submit_rejects_stale_attempt_number(tmp_path):
    engine = Engine(tmp_path)
    engine.create_tasks([_packet("a")])
    claimed = engine.claim(1, executor_kind="manual", model="m")
    with pytest.raises(EngineError, match="does not match the outstanding attempt"):
        engine.submit("a", _valid_result("a", claimed[0].attempt + 1))


def test_submit_records_failed_for_a_malformed_result_without_a_submitted_record(tmp_path):
    engine = Engine(tmp_path, max_attempts=3)
    engine.create_tasks([_packet("a")])
    engine.claim(1, executor_kind="manual", model="m")
    malformed = {"not": "a task result at all"}
    outcome = engine.submit("a", malformed)
    assert outcome["status"] == "failed"
    assert outcome["failures"][0]["check"] == "task-result"

    events = [json.loads(line)["event"] for line in
             (tmp_path / "tasks" / "ledger.jsonl").read_text().splitlines()]
    assert "submitted" not in events  # can't embed an invalid TaskResult there
    assert events.count("failed") == 1
    assert engine.ready_task_ids() == ["a"]  # one attempt consumed, two left


def test_max_attempts_must_be_at_least_one(tmp_path):
    with pytest.raises(EngineError, match="max_attempts"):
        Engine(tmp_path, max_attempts=0)


# --------------------------------------------------------------------------- #
# concurrency-safe claiming
# --------------------------------------------------------------------------- #

def test_two_threads_claiming_concurrently_never_double_claim(tmp_path):
    engine = Engine(tmp_path)
    engine.create_tasks([_packet(f"t{i}") for i in range(20)])

    claimed_ids: list[str] = []
    lock = threading.Lock()

    def worker():
        while True:
            got = engine.claim(1, executor_kind="worker", model="m")
            if not got:
                return
            with lock:
                claimed_ids.append(got[0].packet.task_id)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed_ids) == 20
    assert len(set(claimed_ids)) == 20  # no task claimed twice
