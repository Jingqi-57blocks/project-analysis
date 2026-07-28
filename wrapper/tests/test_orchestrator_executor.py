"""Bundled headless executor tests (57B-113 / 57B-115, M1) — the full
claim/execute/submit loop driven by a FAKE transport (no real network calls
anywhere in this file): success, retry-on-429, and malformed-JSON output
resolving to a failed validation after the attempts cap is exhausted."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator.contracts import TaskPacket
from analysis_wrapper.orchestrator.engine import Engine
from analysis_wrapper.orchestrator.executor_api import (
    AdapterConfig, ExecutorError, parse_json_output, run_executor, run_one,
)

GOOD_OUTPUT = {"findings": [], "coverage": []}


def _packet(task_id: str) -> TaskPacket:
    return TaskPacket.create(
        task_id=task_id, task_type="lens-findings", template_id="tpl",
        template_version="1.0.0", instructions="do it", inputs={"x": "y"},
        output_schema_id="lens-findings.v1", context_budget_tokens=1000)


def _anthropic_body(text: str, *, input_tokens: int = 10, output_tokens: int = 5) -> bytes:
    return json.dumps({
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }).encode("utf-8")


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_run_executor_success_path(tmp_path):
    Engine(tmp_path).create_tasks([_packet("ok-task")])

    def transport(url, headers, body, timeout):
        assert url == "https://api.anthropic.com/v1/messages"
        assert headers["x-api-key"] == "test-key"
        return 200, _anthropic_body(json.dumps(GOOD_OUTPUT))

    summary = run_executor(tmp_path, AdapterConfig(name="anthropic", model="claude-x"),
                           transport=transport, sleep=lambda s: None)
    assert summary == {"validated": ["ok-task"], "failed": []}


def test_run_executor_retries_on_429_then_succeeds(tmp_path):
    Engine(tmp_path).create_tasks([_packet("retry-task")])
    attempts = {"n": 0}

    def transport(url, headers, body, timeout):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return 429, b'{"error": "rate limited"}'
        return 200, _anthropic_body(json.dumps(GOOD_OUTPUT))

    summary = run_executor(tmp_path, AdapterConfig(name="anthropic", model="claude-x"),
                           transport=transport, sleep=lambda s: None)
    assert summary == {"validated": ["retry-task"], "failed": []}
    assert attempts["n"] == 3


def test_run_executor_gives_up_after_max_retries_on_persistent_5xx(tmp_path):
    Engine(tmp_path).create_tasks([_packet("down-task")])
    calls = {"n": 0}

    def transport(url, headers, body, timeout):
        calls["n"] += 1
        return 503, b'{"error": "down"}'

    summary = run_executor(tmp_path, AdapterConfig(name="anthropic", model="claude-x"),
                           transport=transport, sleep=lambda s: None, max_retries=2,
                           max_attempts=1)
    assert summary == {"validated": [], "failed": ["down-task"]}
    assert calls["n"] == 3  # 1 initial + 2 retries, for the single allowed attempt


def test_malformed_json_output_becomes_a_failed_result_and_eventually_exhausts(tmp_path):
    Engine(tmp_path, max_attempts=2).create_tasks([_packet("bad-json-task")])

    def transport(url, headers, body, timeout):
        return 200, _anthropic_body("this is not json at all")

    summary = run_executor(tmp_path, AdapterConfig(name="anthropic", model="claude-x"),
                           transport=transport, sleep=lambda s: None, max_attempts=2)
    # Reported exactly ONCE despite being retried up to the attempts cap.
    assert summary == {"validated": [], "failed": ["bad-json-task"]}

    events = [json.loads(line) for line in
             (tmp_path / "tasks" / "ledger.jsonl").read_text().splitlines()]
    failed_attempts = [row["detail"]["attempt"] for row in events if row["event"] == "failed"]
    assert failed_attempts == [1, 2]


def test_missing_api_key_raises_immediately_and_naming_the_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    Engine(tmp_path).create_tasks([_packet("key-task")])

    def transport(url, headers, body, timeout):
        raise AssertionError("must never reach the network without a key")

    with pytest.raises(ExecutorError, match="ANTHROPIC_API_KEY"):
        run_executor(tmp_path, AdapterConfig(name="anthropic", model="claude-x"),
                    transport=transport, sleep=lambda s: None)

    # Configuration is checked before Engine.claim, so recovery is not needed.
    events = [json.loads(line) for line in
              (tmp_path / "tasks" / "ledger.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["created"]


def test_unknown_adapter_raises_executor_error(tmp_path):
    packet = _packet("solo")
    with pytest.raises(ExecutorError, match="unknown executor adapter"):
        run_one(packet, AdapterConfig(name="not-a-real-adapter", model="x"), 1)


def test_openai_compatible_adapter_requires_base_url(tmp_path):
    packet = _packet("solo")
    with pytest.raises(ExecutorError, match="--base-url"):
        run_one(packet, AdapterConfig(name="openai-compatible", model="x"), 1,
               transport=lambda *a: (200, b"{}"))


def test_openai_compatible_adapter_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    Engine(tmp_path).create_tasks([_packet("openai-task")])

    def transport(url, headers, body, timeout):
        assert url == "https://example.test/v1/chat/completions"
        assert headers["authorization"] == "Bearer openai-test-key"
        payload = {"choices": [{"message": {"content": json.dumps(GOOD_OUTPUT)}}],
                  "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
        return 200, json.dumps(payload).encode("utf-8")

    summary = run_executor(
        tmp_path, AdapterConfig(name="openai-compatible", model="x",
                                base_url="https://example.test/v1"),
        transport=transport, sleep=lambda s: None)
    assert summary == {"validated": ["openai-task"], "failed": []}


def test_parse_json_output_strips_a_code_fence():
    assert parse_json_output("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert parse_json_output('{"a": 1}') == {"a": 1}
