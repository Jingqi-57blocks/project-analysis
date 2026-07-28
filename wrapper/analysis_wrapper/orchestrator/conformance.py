"""Executor conformance fixtures for the orchestrator protocol (57B-113 / 57B-115, M1).

One realistic, minimal :class:`~.contracts.TaskPacket` PLUS one golden,
schema-valid output per task type in :data:`~.contracts.TASK_TYPES` (eight
fixtures total). ``run_conformance`` materializes a temp run dir, loads the
fixture DAG (the eight fixtures are mutually independent — no
``depends_on`` between them), and submits either:

  - the golden outputs (the default, no network, no model call — a
    self-check that the fixtures/goldens themselves stay schema-valid as
    ``schemas.py`` evolves; this is what the pytest suite exercises), or
  - a LIVE executor's outputs (``config`` given — drives the real
    ``executor_api.run_executor`` loop against the fixture DAG, so a
    candidate model/adapter can be conformance-tested end to end)

through the exact same ``Engine.submit`` validation path production traffic
uses. A model/executor "passes" when every fixture task validates.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .composer import compose
from .contracts import (
    TASK_TYPES, ExecutorInfo, TaskPacket, TaskResult, TaskTiming, TokenUsage, ValidationOutcome,
)
from .engine import Engine, now_iso
from .executor_api import AdapterConfig, Transport, run_executor, urllib_transport

FIXTURE_TEMPLATE_VERSION = "1.0.0"
FIXTURE_CONTEXT_BUDGET = 8000


@dataclass(frozen=True)
class Fixture:
    task_type: str
    packet: TaskPacket
    golden_output: object


def _fixture(task_type: str, instructions: str, inputs: Mapping[str, str],
            output_schema_id: str, golden_output: object) -> Fixture:
    packets = compose(
        task_id=f"conformance-{task_type}",
        template_id=f"conformance-{task_type}-template",
        template_version=FIXTURE_TEMPLATE_VERSION,
        task_type=task_type, instructions=instructions, inputs=inputs,
        output_schema_id=output_schema_id, context_budget_tokens=FIXTURE_CONTEXT_BUDGET)
    assert len(packets) == 1, "a conformance fixture must never need sharding"
    return Fixture(task_type=task_type, packet=packets[0], golden_output=golden_output)


FIXTURES: tuple[Fixture, ...] = (
    _fixture(
        "lens-findings",
        "Return findings for the structure lens as a JSON object matching the "
        "lens-findings output schema. Cite evidence using signals/<view>:<line>, "
        "a source ref, or a metric ref only.",
        {"signals": "views: 3 observed import edges between module-a and module-b."},
        "lens-findings.v1",
        {
            "findings": [{
                "finding_id": "finding-conformance-example",
                "claim": "The conformance fixture module imports another module directly.",
                "lens": "structure",
                "affected_modules": ["module-a", "module-b"],
                "evidence": [{
                    "fact": "module-a/index.ts imports module-b/client.ts at the top of the file.",
                    "refs": ["signals/imports.view.txt:1"],
                    "basis": "static-reference",
                }],
                "evidence_basis": ["static-reference"],
                "impact": "Changes to module-b ripple into module-a with no interface boundary.",
                "priority": "medium",
                "confidence": "medium",
                "limitations": "Static import evidence only; no runtime call data.",
                "suggested_direction": "Introduce an explicit interface between the two modules.",
                "changeability_question": "boundary-clarity",
            }],
            "coverage": [{"signal": "imports.view.txt", "status": "complete", "note": ""}],
        },
    ),
    _fixture(
        "formation-proposal",
        "Propose a module formation for the given candidates as a JSON object "
        "matching the formation-proposal output schema.",
        {"candidates": "candidate mc-conformance-1: repository conformance-repo, "
                       "value 'module-a', evidence: static import scan."},
        "formation-proposal.v1",
        {
            "modules": [{
                "module_id": "conformance-module",
                "name": "Conformance Module",
                "classification": "business",
                "confidence": "medium",
                "aliases": [],
            }],
        },
    ),
    _fixture(
        "boundary-resolution",
        "Resolve the disposition for the given candidates as a JSON object "
        "matching the boundary-resolution output schema.",
        {"candidates": "candidate mc-conformance-1 is unresolved between 'standalone' "
                       "and 'merged'; evidence: single consumer, no shared code."},
        "boundary-resolution.v1",
        {
            "dispositions": [{
                "candidate_id": "mc-conformance-1",
                "disposition": "standalone",
                "module_ids": ["conformance-module"],
                "reason": "Clear, isolated boundary with no shared dependents.",
            }],
        },
    ),
    _fixture(
        "rekey-resolution",
        "Disposition the supplied rekey tail with one finite terminal outcome.",
        {"tail": "finding-conformance-tail has one static evidence item."},
        "rekey-resolution.v1",
        {
            "dispositions": [{
                "finding_id": "finding-conformance-tail",
                "disposition": "evidence-backed-no-finding",
                "module_ids": [],
                "reason_code": "unsupported",
                "evidence_refs": ["signals/imports.view.txt:1"],
            }],
        },
    ),
    _fixture(
        "dedup-rank",
        "Deduplicate and rank the given findings as a JSON object matching the "
        "dedup-rank output schema. input_finding_ids = "
        "['finding-conformance-a', 'finding-conformance-b'].",
        {"findings": "finding-conformance-a: module-a imports module-b directly. "
                     "finding-conformance-b: duplicate of finding-conformance-a "
                     "(same evidence, same claim)."},
        "dedup-rank.v1",
        {
            "input_finding_ids": ["finding-conformance-a", "finding-conformance-b"],
            "merge_map": {
                "finding-conformance-a": {
                    "status": "surviving", "absorbed_into": None,
                    "reason": "Primary, most complete finding.",
                },
                "finding-conformance-b": {
                    "status": "absorbed", "absorbed_into": "finding-conformance-a",
                    "reason": "Same root cause as finding-conformance-a.",
                },
            },
            "rank": [{"finding_id": "finding-conformance-a",
                     "reason": "Highest blast radius among surviving findings."}],
        },
    ),
    _fixture(
        "section-generate",
        "Generate section content as a JSON object matching the section-generate "
        "output schema.",
        {"outline": "Section: conformance-section. Summarize the fixture scenario "
                    "in one short paragraph."},
        "section-generate.v1",
        {
            "section_id": "conformance-section",
            "content_md": "Conformance fixture section content for validation.",
            "word_count": 6,
        },
    ),
    _fixture(
        "repair-edit-ops",
        "Propose edit ops fixing the given failed checks as a JSON object "
        "matching the repair-edit-ops output schema.",
        {"failed_checks": "check conformance-check-a failed: placeholder text is inaccurate.",
         "document": "The document contains old placeholder text that needs fixing."},
        "repair-edit-ops.v1",
        {
            "edits": [{
                "locate": "old placeholder text",
                "replace": "corrected placeholder text",
                "fixes": "conformance-check-a",
            }],
        },
    ),
    _fixture(
        "coherence-check",
        "Check whether the two documents are mutually consistent; return a JSON "
        "object matching the coherence-check output schema.",
        {"document_a": "Module A depends on Module B.",
         "document_b": "Module B has no dependents."},
        "coherence-check.v1",
        {"consistent": True, "edit_ops": []},
    ),
    _fixture(
        "selection-fetch",
        "Fetch the requested selection as a JSON object matching the "
        "selection-fetch output schema, citing ref + quoted_text.",
        {"request": "Fetch a short illustrative quote from src/example.ts."},
        "selection-fetch.v1",
        {
            "selections": [{
                "selection_id": "conformance-selection-1",
                "purpose": "Illustrative quoted excerpt for conformance testing.",
                "ref": "conformance-repo@" + "a" * 40 + ":src/example.ts:1",
                "quoted_text": "export const example = 1;",
            }],
        },
    ),
)

assert {fixture.task_type for fixture in FIXTURES} == TASK_TYPES  # one fixture per task type


def _golden_result(task_id: str, output: object, attempt: int) -> TaskResult:
    at = now_iso()
    return TaskResult(
        task_id=task_id, status="ok", output=output,
        executor=ExecutorInfo(kind="conformance-golden", model="golden", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()),
        attempt=attempt,
    )


def run_conformance(*, run_dir: str | None = None, config: AdapterConfig | None = None,
                    concurrency: int = 1, transport: Transport = urllib_transport) -> dict:
    """Returns ``{"passed": bool, "results": {task_type: outcome}}`` where
    ``outcome`` is ``"validated"``, ``"failed"``, or ``"not-run"``.

    With ``config`` omitted, submits the built-in golden outputs (no
    network). With ``config`` given, drives ``executor_api.run_executor``
    against the fixture DAG for a real conformance test of that
    adapter/model.
    """
    owns_tempdir = run_dir is None
    run = Path(run_dir).expanduser().resolve() if run_dir else Path(
        tempfile.mkdtemp(prefix="pa-conformance-"))
    try:
        engine = Engine(run)
        engine.create_tasks([fixture.packet for fixture in FIXTURES])
        outcomes: dict[str, str] = {}
        if config is not None:
            summary = run_executor(run, config, concurrency=concurrency, transport=transport)
            outcomes.update({task_id: "validated" for task_id in summary["validated"]})
            outcomes.update({task_id: "failed" for task_id in summary["failed"]})
        else:
            golden_by_id = {fixture.packet.task_id: fixture.golden_output for fixture in FIXTURES}
            claimed = engine.claim(len(FIXTURES), executor_kind="conformance-golden",
                                   model="golden")
            for item in claimed:
                result = _golden_result(item.packet.task_id,
                                        golden_by_id[item.packet.task_id], item.attempt)
                outcome = engine.submit(item.packet.task_id, result.to_dict())
                outcomes[item.packet.task_id] = outcome["status"]
        results = {fixture.task_type: outcomes.get(fixture.packet.task_id, "not-run")
                  for fixture in FIXTURES}
        return {"passed": all(status == "validated" for status in results.values()),
               "results": results}
    finally:
        if owns_tempdir:
            shutil.rmtree(run, ignore_errors=True)
