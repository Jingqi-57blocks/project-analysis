"""Bundled headless executor for the orchestrator protocol (57B-113 / 57B-115, M1).

**This module performs model-API network calls.** It is invoked EXPLICITLY
by a user (the ``run-executor`` CLI subcommand) supplying their own API key
through an environment variable; nothing in the analysis pipeline
(``prepare-overview``, ``finalize-*``, ``audit-overview``, ...) imports it,
and it never runs implicitly as a side effect of any other command.

It is one possible implementation of the executor protocol defined by
``engine.py``'s ``next-task``/``submit-task`` verbs (claim -> build a
request -> call the model API -> parse the JSON output -> submit a
``TaskResult``) — any program that loops over those two verbs is an
executor; this one just happens to be bundled.

Two adapters, both built on ``urllib.request`` only (no third-party HTTP
dependency, matching this package's stdlib-only policy):

  - ``anthropic``: POSTs to ``https://api.anthropic.com/v1/messages`` with
    an ``x-api-key`` header (default env var ``ANTHROPIC_API_KEY``) and the
    required ``anthropic-version`` header.
  - ``openai-compatible``: POSTs to ``<base_url>/chat/completions`` with a
    ``Bearer`` authorization header (default env var ``OPENAI_API_KEY``).

The actual HTTP call is made through an injectable ``Transport`` callable
so tests can drive the full claim/execute/submit loop with a fake transport
and make zero real network calls.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Mapping

from .contracts import ExecutorInfo, TaskPacket, TaskResult, TaskTiming, TokenUsage, ValidationOutcome
from .engine import ClaimedTask, Engine, now_iso

# (url, headers, body, timeout_seconds) -> (status_code, response_body)
Transport = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]

DEFAULT_TIMEOUT_S = 120.0
MAX_RETRIES = 5
RETRY_STATUS = {429, 500, 502, 503, 504}


class ExecutorError(RuntimeError):
    """A configuration/transport problem that affects every task the same
    way (missing API key, unknown adapter, missing --base-url) -- these
    abort the whole run rather than being recorded per-task, since retrying
    a different task would never help. An ordinary model/HTTP/parse problem
    for ONE task is instead captured as a ``status="failed"`` TaskResult and
    goes through the normal per-task ledger accounting."""


def urllib_transport(url: str, headers: Mapping[str, str], body: bytes,
                     timeout: float) -> tuple[int, bytes]:
    """The real network transport."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


_CODE_FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    match = _CODE_FENCE.match(text.strip())
    return match.group(1) if match else text


def parse_json_output(text: str) -> object:
    return json.loads(_strip_code_fence(text))


@dataclass(frozen=True)
class AdapterConfig:
    name: str  # "anthropic" | "openai-compatible"
    model: str
    base_url: str = ""
    api_key_env: str = ""
    temperature: float = 0.0


def _api_key(env_var: str) -> str:
    key = os.environ.get(env_var, "")
    if not key:
        raise ExecutorError(
            f"missing API key: set the {env_var} environment variable before "
            "running run-executor")
    return key


def _packet_user_content(packet: TaskPacket) -> str:
    parts = [f"### {name}\n{item.content}" for name, item in sorted(packet.inputs.items())]
    parts.append(f"### output_schema_id\n{packet.output_schema_id}")
    return "\n\n".join(parts)


def _anthropic_request(packet: TaskPacket, config: AdapterConfig) -> tuple[str, Mapping[str, str], bytes]:
    key = _api_key(config.api_key_env or "ANTHROPIC_API_KEY")
    body = json.dumps({
        "model": config.model,
        "max_tokens": 8192,
        "temperature": config.temperature,
        "system": packet.instructions,
        "messages": [{"role": "user", "content": _packet_user_content(packet)}],
    }).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    return "https://api.anthropic.com/v1/messages", headers, body


def _anthropic_parse(status: int, raw: bytes) -> tuple[object, TokenUsage | None]:
    doc = json.loads(raw.decode("utf-8"))
    if status >= 400:
        raise ValueError(f"anthropic API error {status}: {doc}")
    text = "".join(block.get("text", "") for block in doc.get("content", [])
                   if isinstance(block, dict))
    usage = doc.get("usage") or {}
    tokens = (TokenUsage(input=int(usage.get("input_tokens", 0)),
                         output=int(usage.get("output_tokens", 0)))
             if usage else None)
    return parse_json_output(text), tokens


def _openai_request(packet: TaskPacket, config: AdapterConfig) -> tuple[str, Mapping[str, str], bytes]:
    if not config.base_url:
        raise ExecutorError("the openai-compatible adapter requires --base-url")
    key = _api_key(config.api_key_env or "OPENAI_API_KEY")
    body = json.dumps({
        "model": config.model,
        "temperature": config.temperature,
        "messages": [
            {"role": "system", "content": packet.instructions},
            {"role": "user", "content": _packet_user_content(packet)},
        ],
    }).encode("utf-8")
    headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}
    return config.base_url.rstrip("/") + "/chat/completions", headers, body


def _openai_parse(status: int, raw: bytes) -> tuple[object, TokenUsage | None]:
    doc = json.loads(raw.decode("utf-8"))
    if status >= 400:
        raise ValueError(f"openai-compatible API error {status}: {doc}")
    text = doc["choices"][0]["message"]["content"]
    usage = doc.get("usage") or {}
    tokens = (TokenUsage(input=int(usage.get("prompt_tokens", 0)),
                         output=int(usage.get("completion_tokens", 0)))
             if usage else None)
    return parse_json_output(text), tokens


_ADAPTERS: dict[str, tuple[Callable, Callable]] = {
    "anthropic": (_anthropic_request, _anthropic_parse),
    "openai-compatible": (_openai_request, _openai_parse),
}


def _call_with_retry(transport: Transport, url: str, headers: Mapping[str, str], body: bytes,
                     *, max_retries: int, timeout: float,
                     sleep: Callable[[float], None]) -> tuple[int, bytes]:
    attempt = 0
    while True:
        status, raw = transport(url, headers, body, timeout)
        if status not in RETRY_STATUS or attempt >= max_retries:
            return status, raw
        sleep(min(2 ** attempt, 30))
        attempt += 1


def run_one(packet: TaskPacket, config: AdapterConfig, attempt: int, *,
           transport: Transport = urllib_transport, timeout: float = DEFAULT_TIMEOUT_S,
           max_retries: int = MAX_RETRIES,
           sleep: Callable[[float], None] = time.sleep) -> TaskResult:
    """Execute one TaskPacket end to end. Never raises for an ordinary
    model/HTTP/parse problem — returns a ``status="failed"`` TaskResult
    instead; only a configuration problem (missing key, unknown adapter,
    missing --base-url) raises :class:`ExecutorError` (build_request runs
    before any network call, so this always happens before the timing clock
    below would even start)."""
    if config.name not in _ADAPTERS:
        raise ExecutorError(f"unknown executor adapter: {config.name!r}")
    build_request, parse_response = _ADAPTERS[config.name]
    executor_info = ExecutorInfo(kind=config.name, model=config.model,
                                 params={"temperature": config.temperature})
    url, headers, body = build_request(packet, config)  # may raise ExecutorError -- let it propagate

    started_at = now_iso()
    start = time.monotonic()
    try:
        status, raw = _call_with_retry(transport, url, headers, body, max_retries=max_retries,
                                       timeout=timeout, sleep=sleep)
        output, tokens = parse_response(status, raw)
    except Exception as exc:  # HTTP error, malformed JSON, anything else -> a FAILED result
        finished_at = now_iso()
        return TaskResult(
            task_id=packet.task_id, status="failed", output=None, executor=executor_info,
            timing=TaskTiming(started_at=started_at, finished_at=finished_at,
                              wall_clock_s=time.monotonic() - start),
            tokens=None,
            validation=ValidationOutcome(passed=False, failures=(
                {"check": "executor-error", "detail": str(exc), "location": ""},)),
            attempt=attempt,
        )
    finished_at = now_iso()
    return TaskResult(
        task_id=packet.task_id, status="ok", output=output, executor=executor_info,
        timing=TaskTiming(started_at=started_at, finished_at=finished_at,
                          wall_clock_s=time.monotonic() - start),
        tokens=tokens,
        validation=ValidationOutcome(passed=True, failures=()),
        attempt=attempt,
    )


def run_executor(run_dir, config: AdapterConfig, *, concurrency: int = 1,
                 transport: Transport = urllib_transport, timeout: float = DEFAULT_TIMEOUT_S,
                 max_retries: int = MAX_RETRIES, sleep: Callable[[float], None] = time.sleep,
                 max_attempts: int = 3) -> dict:
    """claim -> execute (threaded, HTTP-bound) -> submit, repeating until no
    ready task remains. Returns ``{"validated": [task_id, ...], "failed":
    [task_id, ...]}`` — each task_id's FINAL state after every retry the
    max-attempts policy allowed (a task retried twice before exhausting
    appears once, in "failed", not three times). An :class:`ExecutorError`
    (config-level) propagates out immediately rather than being swallowed
    into the summary."""
    engine = Engine(run_dir, max_attempts=max_attempts)
    while True:
        batch: list[ClaimedTask] = engine.claim(
            concurrency, executor_kind=config.name, model=config.model,
            params={"temperature": config.temperature})
        if not batch:
            break
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {
                pool.submit(run_one, claimed.packet, config, claimed.attempt,
                           transport=transport, timeout=timeout, max_retries=max_retries,
                           sleep=sleep): claimed
                for claimed in batch
            }
            results: list[tuple[str, TaskResult]] = [
                (claimed.packet.task_id, future.result()) for future, claimed in futures.items()]
        for task_id, result in results:
            engine.submit(task_id, result.to_dict())
    states = engine.task_states()
    return {
        "validated": sorted(task_id for task_id, state in states.items() if state == "validated"),
        "failed": sorted(task_id for task_id, state in states.items() if state == "failed"),
    }
