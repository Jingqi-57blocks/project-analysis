"""End-to-end pipeline driver (57B-113 / 57B-117, M3).

Every timing measurement this workstream produced before this module was
hand-assembled: an orchestrating session claimed packets, ran executors,
submitted results and moved between phases by hand, so "wall clock" included
however long a human-driven loop happened to take. That number could never be
honest, and neither could the model-agnostic claim, because the
``next-task``/``submit-task`` protocol had never actually driven a run.

This module is the fix. It walks the phase graph itself and, at every
judgment phase, hands the ready tasks to an executor through the SAME two
verbs any third-party harness would use. Two executor modes:

``api``
    Drives the bundled headless executor (``executor_api``) — a real
    end-to-end run with no agent harness involved at all.

``external``
    Runs every deterministic step and stops at each judgment phase with the
    tasks planned and claimable, so another harness (a Claude Code session, a
    third-party runner) can execute them and re-invoke to continue. The phase
    graph, not the harness, owns the ordering.

Phases are resumable by construction: each is a function of ledger state, so
re-invoking after any interruption picks up where the ledger says the run
actually is — no separate progress file to drift.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import formation, reports, sections as catalog
from .engine import Engine
from .results import validated_outputs

Log = Callable[[str], None]


class DriverError(RuntimeError):
    """A phase could not proceed and the run must not silently continue in a
    half-finished state."""


@dataclass
class PhaseOutcome:
    name: str
    started_at: float
    finished_at: float = 0.0
    executed: int = 0
    detail: str = ""

    @property
    def seconds(self) -> float:
        return max(0.0, (self.finished_at or time.monotonic()) - self.started_at)


@dataclass
class RunState:
    run: Path
    executor: str
    context_budget_tokens: int
    log: Log
    phases: list[PhaseOutcome] = field(default_factory=list)
    executor_config: Any = None
    concurrency: int = 4
    blocked_on: str = ""

    def phase(self, name: str) -> PhaseOutcome:
        outcome = PhaseOutcome(name=name, started_at=time.monotonic())
        self.phases.append(outcome)
        self.log(f"[phase] {name}")
        return outcome


# --------------------------------------------------------------------------- #
# executing whatever the ledger says is ready
# --------------------------------------------------------------------------- #

def _drain(state: RunState, outcome: PhaseOutcome) -> bool:
    """Execute every currently-ready task, or report that an external
    executor must. Returns True when the phase is fully drained."""
    engine = Engine(state.run)
    ready = engine.ready_task_ids()
    if not ready:
        return True
    if state.executor != "api":
        state.blocked_on = outcome.name
        outcome.detail = (f"{len(ready)} task(s) awaiting an external executor: "
                          f"{', '.join(ready[:6])}"
                          + (" …" if len(ready) > 6 else ""))
        state.log(f"  {outcome.detail}")
        return False
    from . import executor_api
    result = executor_api.run_executor(
        state.run, state.executor_config, concurrency=state.concurrency)
    outcome.executed += len(result.get("validated", [])) + len(result.get("failed", []))
    failed = result.get("failed", [])
    if failed:
        # Honest-failure path: a permanently failed task does not stop the
        # run, it becomes a disclosed coverage gap downstream. What must NOT
        # happen is proceeding as though it had succeeded.
        state.log(f"  {len(failed)} task(s) failed permanently: {', '.join(failed[:5])}")
        outcome.detail = f"{len(failed)} permanently failed"
    return True


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #

def _phase_judgment(state: RunState) -> bool:
    """Lens findings + formation, and the select/fetch/finalize pairs the
    source_reads lenses need."""
    from . import planner, selection

    outcome = state.phase("judgment: plan")
    planner.plan_judgment(state.run, context_budget_tokens=state.context_budget_tokens)
    outcome.finished_at = time.monotonic()

    outcome = state.phase("judgment: execute selects and lenses")
    if not _drain(state, outcome):
        outcome.finished_at = time.monotonic()
        return False
    outcome.finished_at = time.monotonic()

    # Any select task that validated now has evidence to fetch and a real
    # lens task to compose. This is the two-phase pair completing itself.
    outcome = state.phase("judgment: fetch selections and finalize lenses")
    selects = validated_outputs(state.run, task_type="selection-fetch")
    finalized = 0
    for select_task_id in sorted(selects):
        if not select_task_id.endswith("-select") and "-select-shard-" not in select_task_id:
            continue
        try:
            selection.fetch(state.run, select_task_id)
        except Exception as exc:  # noqa: BLE001 - disclosed, never silent
            state.log(f"  fetch-selections {select_task_id}: {exc}")
            continue
        lens_task_id = select_task_id.split("-select")[0]
        try:
            planner.plan_lens_finalize(
                state.run, lens_task_id,
                context_budget_tokens=state.context_budget_tokens)
            finalized += 1
        except Exception as exc:  # noqa: BLE001
            state.log(f"  plan-lens-finalize {lens_task_id}: {exc}")
    outcome.detail = f"{finalized} source-verified lens task(s) composed"
    outcome.finished_at = time.monotonic()

    outcome = state.phase("judgment: execute source-verified lenses")
    drained = _drain(state, outcome)
    outcome.finished_at = time.monotonic()
    return drained


def _phase_module_map(state: RunState) -> bool:
    from .. import module_map
    outcome = state.phase("module map: write + finalize")
    formation.write(state.run)
    module_map.expand_candidate_rules(state.run)
    module_map.validate(state.run)
    outcome.detail = "zero-omission/zero-overlap gate passed"
    outcome.finished_at = time.monotonic()
    return True


def _phase_findings(state: RunState) -> bool:
    from .. import findings as findings_module
    from . import assemble as assemble_module
    from . import planner, rekey

    outcome = state.phase("findings: plan dedup")
    planner.plan_dedup(state.run, context_budget_tokens=state.context_budget_tokens)
    outcome.finished_at = time.monotonic()

    outcome = state.phase("findings: execute dedup")
    if not _drain(state, outcome):
        outcome.finished_at = time.monotonic()
        return False
    outcome.finished_at = time.monotonic()

    outcome = state.phase("findings: assemble, re-key, finalize")
    assembled = state.run / "tasks" / "assembled-findings.json"
    assembled.parent.mkdir(parents=True, exist_ok=True)
    document = assemble_module.assemble(state.run)
    assembled.write_text(json.dumps(document, ensure_ascii=False, indent=1), "utf-8")
    rekeyed = rekey.rekey(state.run, document)
    tail = rekeyed.get("tail", [])
    if tail:
        # The tail is a genuine judgment remainder (a finding whose candidate
        # was dispositioned excluded/unresolved). It is DISCLOSED, never
        # dropped and never mechanically guessed onto a neighbouring module.
        outcome.detail = (f"{len(tail)} finding(s) need the nearest-enclosing-module "
                          "judgment pass before finalize")
        state.log(f"  {outcome.detail}")
        (state.run / "tasks" / "rekey-tail.json").write_text(
            json.dumps(tail, ensure_ascii=False, indent=1), "utf-8")
    (state.run / "findings.json").write_text(json.dumps(
        {"schema_version": document["schema_version"],
         "findings": rekeyed.get("rekeyed", [])}, ensure_ascii=False, indent=1), "utf-8")
    findings_module.write(state.run)
    outcome.finished_at = time.monotonic()
    return True


def _phase_reports(state: RunState) -> bool:
    outcome = state.phase("reports: plan and execute by wave")
    waves = sorted({section.wave for section in catalog.authored()})
    for wave in waves:
        planned = reports.plan_reports(
            state.run, wave=wave, context_budget_tokens=state.context_budget_tokens)
        if not planned:
            continue
        state.log(f"  wave {wave}: {len(planned)} section task(s)")
        if not _drain(state, outcome):
            outcome.finished_at = time.monotonic()
            return False
    outcome.finished_at = time.monotonic()

    outcome = state.phase("reports: assemble and check floors")
    problems = 0
    for document in catalog.DOCUMENTS:
        reports.assemble_document(state.run, document)
        report = reports.document_floors(state.run, document)
        problems += len(report["failures"])
        state.log(f"  {document}: {report['sections_present']}/"
                  f"{report['sections_expected']} sections, "
                  f"{report['prose_words']} prose words, "
                  f"{len(report['failures'])} floor/ceiling failure(s)")
    reports.write_views_manifest(state.run)
    outcome.detail = f"{problems} floor/ceiling failure(s) across all documents"
    outcome.finished_at = time.monotonic()
    return True


def _phase_audit(state: RunState) -> bool:
    from .. import overview_audit
    outcome = state.phase("audit")
    path = overview_audit.write(state.run, require_module_map=True, require_reports=True)
    audit = json.loads(Path(path).read_text("utf-8"))
    failed = [check for check in audit.get("checks", []) if check.get("status") == "fail"]
    outcome.detail = (f"{len(failed)} failing check(s)" if failed else "all checks passed")
    state.log(f"  {outcome.detail}")
    outcome.finished_at = time.monotonic()
    return True


PHASES: tuple[tuple[str, Callable[[RunState], bool]], ...] = (
    ("judgment", _phase_judgment),
    ("module-map", _phase_module_map),
    ("findings", _phase_findings),
    ("reports", _phase_reports),
    ("audit", _phase_audit),
)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def run_pipeline(run_dir: str | Path, *, executor: str = "api",
                 adapter: str = "anthropic", model: str = "",
                 base_url: str = "", api_key_env: str = "",
                 concurrency: int = 4, context_budget_tokens: int = 180_000,
                 stop_after: str | None = None,
                 log: Log = print) -> dict[str, Any]:
    """Drive a PREPARED run to completion.

    Returns a summary with per-phase wall-clock. Those numbers are the honest
    ones: they measure the pipeline, not an orchestrating session's own
    latency, which is exactly why this entry point exists.
    """
    run = Path(run_dir).expanduser().resolve()
    if not (run / "targets.json").is_file():
        raise DriverError(
            f"{run} is not a prepared run directory (no targets.json) -- "
            "mint it with new-run and run prepare-overview first")

    state = RunState(run=run, executor=executor,
                     context_budget_tokens=context_budget_tokens,
                     log=log, concurrency=concurrency)
    if executor == "api":
        from .executor_api import AdapterConfig
        if not model:
            raise DriverError("--model is required when driving the bundled executor")
        state.executor_config = AdapterConfig(
            name=adapter, model=model, base_url=base_url, api_key_env=api_key_env)

    started = time.monotonic()
    complete = True
    for name, phase in PHASES:
        if not phase(state):
            complete = False
            break
        if stop_after and name == stop_after:
            log(f"[stop] requested after phase {name!r}")
            complete = False
            break
    total = time.monotonic() - started

    summary = {
        "complete": complete,
        "blocked_on": state.blocked_on,
        "total_seconds": round(total, 1),
        "total_minutes": round(total / 60.0, 1),
        "phases": [{"phase": outcome.name,
                    "seconds": round(outcome.seconds, 1),
                    "executed_tasks": outcome.executed,
                    "detail": outcome.detail}
                   for outcome in state.phases],
    }
    (run / "tasks" / "pipeline-timing.json").parent.mkdir(parents=True, exist_ok=True)
    (run / "tasks" / "pipeline-timing.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", "utf-8")
    return {"complete": complete, "summary": summary, "state": state}
