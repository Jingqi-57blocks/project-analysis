"""Command-line entry point for per-tool execution and full TargetSpec sweeps."""

from __future__ import annotations

import argparse
import functools
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from .contract_version import CONTRACT_VERSION
from .executor import (SignalResult, WrapperSafetyError,
                       prepare_output_directory, run_tool,
                       use_existing_run_directory)
from .registry import (PROVIDER_OWNED_SIGNAL_TOOLS, git_history, jscpd_multi,
                       local_tools, network_tools, tool_for)
from .sanitize import sanitize_text
from .status import Status, aggregate, wrapper_exit_code
from .targetspec import TargetSpec


def _record_summary(out: Path, results: list[SignalResult]) -> None:
    manifests: dict[tuple[str, str, str], list[str]] = {}
    for path in sorted(out.glob("*.manifest.json")):
        if path.name.endswith(".manifest.normalized.json"):
            continue
        try:
            doc = json.loads(path.read_text("utf-8"))
            repository_ref = (doc.get("repos") or [{}])[0].get(
                "repository_ref", "")
            key = (doc.get("tool", ""), repository_ref, doc.get("status", ""))
            manifests.setdefault(key, []).append(path.name)
        except (OSError, ValueError, KeyError, IndexError):
            continue
    payload = {
        "schema_version": CONTRACT_VERSION,
        "aggregate_status": aggregate([x.status for x in results]).value,
        "signals": [
            {"tool": x.tool, "repository_ref": x.repository_ref,
             "status": x.status.value,
             "reason": x.reason,
             "view": x.view_path.name if x.view_path else "",
             "manifest": (x.manifest_path.name if x.manifest_path else
                          manifests.get(
                              (x.tool, x.repository_ref, x.status.value), [""])[0])}
            for x in sorted(results, key=lambda r: (r.repository_ref, r.tool))
        ],
    }
    (out / "run-summary.json").write_text(
        sanitize_text(json.dumps(payload, indent=2, sort_keys=True) + "\n"), "utf-8"
    )


def _run_one(args: argparse.Namespace, spec: TargetSpec, out: Path,
             identities) -> list[SignalResult]:
    target = spec.repo(identities.internal_id_for(args.repo))
    definition = git_history(target, args.since, args.coupling_sample_cap) \
        if args.tool == "git-history" else tool_for(args.tool, target)
    return [run_tool(
        definition, target, out, args.scan_date,
        identities.repository(target.repo_id),
        allow_network=args.include_network,
    )]


def _resolve_jobs(requested: int | None) -> int:
    """Bounded worker count for the sweep's thread pool (57B-115).

    ``None`` (flag omitted) means "pick the default": ``min(8, cpu_count)``.
    An explicit value must be >= 1 — ``1`` is not a special "disable
    threading" sentinel handled elsewhere, it simply makes the pool a no-op
    (see ``_run_tasks``), which is exactly today's serial call path.
    """
    if requested is None:
        return min(8, os.cpu_count() or 1)
    if requested < 1:
        raise ValueError(f"--jobs must be >= 1, got {requested}")
    return requested


def _run_tasks(tasks: list[Callable[[], SignalResult]], *,
              jobs: int) -> list[SignalResult]:
    """Execute independent signal-tool invocations, at most ``jobs`` at once.

    Every task is a fully-bound ``run_tool`` call (its target/tool/output
    already fixed): parallelism only changes WHEN a subprocess runs, never
    what gets written, since each task writes its own uniquely-named
    artifacts (``<tool>-<artifact-key>`` or a cross-repo ``signal_id``) —
    concurrent tasks never share an output path. Results are always
    returned in the SAME order the tasks were submitted (the same order the
    old serial ``for`` loop would have produced), regardless of completion
    order or thread count, so a caller that sorts/serializes afterward (as
    ``_record_summary`` does) gets byte-identical output whether this ran
    with ``jobs=1`` or ``jobs=8``.

    ``jobs <= 1`` (or fewer than two tasks, where a pool buys nothing) never
    constructs a ``ThreadPoolExecutor`` at all — it calls each task directly
    in a plain loop, i.e. today's exact serial path, unchanged.
    """
    if jobs <= 1 or len(tasks) <= 1:
        return [task() for task in tasks]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(lambda task: task(), tasks))


def _sweep(args: argparse.Namespace, spec: TargetSpec, out: Path,
           identities, *,
           exclude_tool_names: frozenset[str] = frozenset()) -> list[SignalResult]:
    """``exclude_tool_names`` (57B-82 A2, additive, default empty — every
    pre-existing call site is unaffected): ``cli._prepare_overview`` passes
    ``PROVIDER_OWNED_SIGNAL_TOOLS`` so git-history/osv-scanner/outdated run
    exactly once, through their own capability providers, instead of also
    running here. The standalone ``run``/``sweep`` CLI subcommands are
    user-facing debug paths and never pass this — they keep executing every
    tool directly, unchanged.

    Every (repo, tool) invocation below — plus the cross-repo per-family
    jscpd pass — is subprocess-bound and independent of every other one, so
    they are collected into a flat, deterministically-ordered task list
    (``functools.partial`` binds each task's arguments immediately, avoiding
    the classic late-binding-closure bug of capturing a loop variable by
    reference) and handed to ``_run_tasks`` (57B-115), which runs up to
    ``--jobs`` of them concurrently. Collection order — and therefore
    everything written from ``results`` afterward — is identical to the
    pre-57B-115 serial loop regardless of how many workers ran it; see
    ``_run_tasks``'s own docstring.
    """
    repos = sorted(spec.repos, key=lambda r: r.repo_id)
    tasks: list[Callable[[], SignalResult]] = []
    for target in repos:
        definitions = local_tools(target)
        # Respect the CLI's reproducible history window instead of the registry default.
        definitions = [git_history(target, args.since, args.coupling_sample_cap)
                       if d.name == "git-history" else d for d in definitions]
        # Always materialize applicable network lanes. The executor records
        # them as SKIPPED without authorization, so absence cannot masquerade
        # as a clean/covered result.
        definitions += network_tools(target)
        if exclude_tool_names:
            definitions = [d for d in definitions if d.name not in exclude_tool_names]
        repo_identity = identities.repository(target.repo_id)
        for definition in definitions:
            tasks.append(functools.partial(
                run_tool, definition, target, out, args.scan_date, repo_identity,
                allow_network=args.include_network,
            ))
    # Cross-repo duplication runs per LANGUAGE FAMILY: Phase 0 proved jscpd is
    # same-language only (zero JS<->Go clones), so cross-family runs only burn
    # the sweep's largest timeout for known-noise output.
    for family, members in sorted(_family_groups(repos).items()):
        if len(members) < 2:
            continue
        definition = jscpd_multi(members)
        tasks.append(functools.partial(
            run_tool, definition, members[0], out, args.scan_date,
            identities.repository(members[0].repo_id),
            additional_targets=members[1:], signal_id=f"jscpd-cross-{family}",
            additional_repository_identities=[
                identities.repository(item.repo_id) for item in members[1:]],
            allow_network=args.include_network,
        ))
    return _run_tasks(tasks, jobs=_resolve_jobs(getattr(args, "jobs", None)))


def _family_groups(repos: list) -> dict[str, list]:
    """Group repos by language family for same-language cross-repo runs."""
    from .profiles import selection  # lazy: see registry.py's note on this cycle
    groups: dict[str, list] = {}
    for target in repos:
        groups.setdefault(selection.family(target), []).append(target)
    return groups


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="project-analysis-wrapper")
    result.add_argument("--targets", help="TargetSpec JSON from discovery "
                                          "(required for run/sweep)")
    result.add_argument("--out",
                        help="output directory (signals dir for run/sweep; "
                             "run dir for discover)")
    result.add_argument(
        "--scan-date", default=None,
        help="recorded scan date (default: today for a new stage; an interrupted "
             "overview reuses its already-bound value)",
    )
    result.add_argument(
        "--since", default=None,
        help="history window start (default: 24 months before today; the value "
             "actually used is recorded in every git-history manifest)",
    )
    result.add_argument(
        "--coupling-sample-cap", type=int, default=0,
        help="cap commits fed into the git-history co-change pass (0 = no cap, "
             "default/unchanged; when set and exceeded, an evenly-spaced sample "
             "is used and disclosed in the manifest)",
    )
    result.add_argument(
        "--allow-hosts", default="",
        help="comma-separated extra network hosts approved for the package "
             "lane (e.g. private git hosts found in package.json); unapproved "
             "hosts make the signal SKIPPED, never silently contacted",
    )
    result.add_argument(
        "--jobs", type=int, default=None,
        help="max concurrent signal-tool invocations for prepare-overview/"
             "sweep (subprocess-bound work only — every write still happens "
             "in the same deterministic order --jobs 1 would produce); "
             "default: min(8, cpu count); --jobs 1 = today's exact serial path",
    )
    sub = result.add_subparsers(dest="command", required=True)
    one = sub.add_parser("run", help="run one tool against one repo")
    one.add_argument("--tool", required=True)
    one.add_argument("--repo", required=True,
                     help="repository reference from identity-map.json")
    one.add_argument("--include-network", action="store_true",
                     help="explicitly authorize a network-capable tool")
    sweep = sub.add_parser("sweep", help="run all applicable validated tools")
    sweep.add_argument("--include-network", action="store_true")
    cg = sub.add_parser(
        "callgraph", help="extract function/method call edges (57B-30) into "
                          "<out>/callgraph/<artifact-key>.jsonl + callgraph-coverage.json")
    cg.add_argument("--include-network", action="store_true",
                    help="authorize the Go module-cache warm for a cold cache "
                         "(offline-first; without it a cold cache fails closed)")
    dm = sub.add_parser(
        "dependency-map",
        help="produce per-repo import maps into <out>/imports/ "
             "(<artifact-key>.depcruise.json for JS/TS, <artifact-key>.golist.json for Go); "
             "the system-model stage consumes them into dependency edges")
    dm.add_argument("--include-network", action="store_true",
                    help="authorize the Go module-cache warm for a cold cache "
                         "(offline-first; without it a cold cache fails closed)")
    disc = sub.add_parser(
        "discover", help="produce the stage-1 run checkpoint "
                         "(targets.json + discovery-report.json)")
    disc.add_argument("--workspace", required=True,
                      help="target workspace root to inventory")
    disc.add_argument("--exclude", default="",
                      help="comma-separated repo basenames to exclude "
                           "(disclosed in the report)")
    disc.add_argument("--analyzer-root", default="",
                      help="override the analyzer's own checkout root that is "
                           "self-excluded from discovery (default: resolved "
                           "from the installed package; needs no operator input)")
    new_run = sub.add_parser(
        "new-run", help="mint a run dir under <skill-root>/output and run "
                        "discovery into it (stage 1 done)")
    new_run.add_argument("--workspace", required=True)
    new_run.add_argument("--skill-root", required=True,
                         help="skill base directory (owns state/ and output/)")
    new_run.add_argument("--language", default="zh-CN", choices=["en", "zh-CN"])
    new_run.add_argument(
        "--model", default="",
        help="actual generation model when the host exposes it; otherwise the "
             "run records 'unknown'",
    )
    new_run.add_argument(
        "--effort", default="",
        help="actual reasoning effort when the host exposes it; otherwise the "
             "run records 'unknown'",
    )
    new_run.add_argument(
        "--run-id", default="", metavar="LABEL",
        help="optional readable run label; the wrapper appends the 6-character "
             "input digest and a collision suffix when needed",
    )
    new_run.add_argument("--exclude", default="")
    new_run.add_argument("--analyzer-root", default="",
                         help="override the self-excluded analyzer checkout "
                              "root (default: resolved from the package)")
    sysmodel = sub.add_parser(
        "system-model",
        help="assemble system-model.json from a completed run dir (targets.json "
             "+ discovery-report.json + callgraph/); import maps under imports/ "
             "are consumed when present")
    sysmodel.add_argument("--run", required=True, help="completed run directory")
    prepare = sub.add_parser(
        "prepare-overview",
        help="run/resume the authoritative deterministic overview path into its "
             "canonical artifact locations, then write capabilities, audit, and "
             "bounded synthesis input")
    prepare.add_argument("--run", required=True, help="overview run directory")
    prepare.add_argument("--include-network", action="store_true",
                         help="explicitly authorize network-capable signal lanes")
    module_init = sub.add_parser("module-init", help="mint an incomplete Module Drill run")
    source = module_init.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-overview", default="", help="fresh overview source run")
    source.add_argument("--workspace", default="", help="workspace for standalone evidence preparation")
    module_init.add_argument("--output-root", required=True, help="analyzer-owned module output root")
    module_init.add_argument("--project-key", required=True)
    module_init.add_argument("--selector", required=True)
    module_init.add_argument("--language", choices=["en", "zh-CN"], default="zh-CN")
    module_init.add_argument("--run-id", default="")
    module_init.add_argument("--model", default="unknown")
    module_init.add_argument("--effort", default="unknown")
    module_init.add_argument("--exclude", default="")
    module_init.add_argument("--analyzer-root", default="")
    module_init.add_argument("--include-network", action="store_true")
    module_init.add_argument("--jobs", type=int, default=None)
    module_status = sub.add_parser("module-status", help="show verified Module Drill state")
    module_status.add_argument("--run", required=True)
    module_register = sub.add_parser("module-register", help="register Module Drill task packets")
    module_register.add_argument("--run", required=True)
    module_register.add_argument("--packets", required=True, help="TaskPacket JSON array path, or - for stdin")
    module_next = sub.add_parser("module-next", help="claim ready Module Drill tasks")
    module_next.add_argument("--run", required=True)
    module_next.add_argument("--claim", type=int, default=1)
    module_next.add_argument("--executor-kind", default="manual")
    module_next.add_argument("--model", default="unknown")
    module_submit = sub.add_parser("module-submit", help="submit one Module Drill task result")
    module_submit.add_argument("--run", required=True)
    module_submit.add_argument("--task", required=True)
    module_submit.add_argument("--result", required=True, help="TaskResult JSON path, or - for stdin")
    module_spans = sub.add_parser("module-fetch-spans", help="fetch verified semantic source spans")
    module_spans.add_argument("--run", required=True)
    module_spans.add_argument("--requests", required=True, help="span-request JSON array path, or - for stdin")
    module_spans.add_argument("--out", default="")
    module_evidence = sub.add_parser(
        "module-build-evidence", help="index verified canonical feature evidence")
    module_evidence.add_argument("--run", required=True)
    module_candidates = sub.add_parser(
        "module-build-candidates", help="build deterministic Module Drill candidates")
    module_candidates.add_argument("--run", required=True)
    module_rank = sub.add_parser(
        "module-plan-ranking", help="register the bounded Module Drill candidate ranking task")
    module_rank.add_argument("--run", required=True)
    module_finalize_ranking = sub.add_parser(
        "module-finalize-ranking", help="persist a validated Module Drill ranking decision")
    module_finalize_ranking.add_argument("--run", required=True)
    module_graph = sub.add_parser(
        "module-build-graph", help="build the observed Module Drill feature graph")
    module_graph.add_argument("--run", required=True)
    module_frontiers = sub.add_parser(
        "module-build-frontier-receipts", help="record exact observed Module Drill frontier expansions")
    module_frontiers.add_argument("--run", required=True)
    module_frontier_candidates = sub.add_parser(
        "module-build-frontier-candidates", help="index bounded Module Drill frontier candidates")
    module_frontier_candidates.add_argument("--run", required=True)
    module_graph_closure = sub.add_parser(
        "module-build-graph-closure", help="materialize bounded Module Drill structural graph closure")
    module_graph_closure.add_argument("--run", required=True)
    module_boundary_closure = sub.add_parser(
        "module-build-boundary-closure", help="link source-span-local Module Drill provider boundaries")
    module_boundary_closure.add_argument("--run", required=True)
    module_spans_plan = sub.add_parser(
        "module-plan-spans", help="plan revision-checked Module Drill semantic spans")
    module_spans_plan.add_argument("--run", required=True)
    module_planned_spans = sub.add_parser(
        "module-fetch-planned-spans", help="fetch the current Module Drill semantic span plan")
    module_planned_spans.add_argument("--run", required=True)
    module_sync_recovery = sub.add_parser(
        "module-plan-sync-recovery", help="register bounded synchronous Module Drill recovery")
    module_sync_recovery.add_argument("--run", required=True)
    module_finalize_sync = sub.add_parser(
        "module-finalize-sync-recovery", help="materialize validated synchronous Module Drill recovery")
    module_finalize_sync.add_argument("--run", required=True)
    module_async_recovery = sub.add_parser(
        "module-plan-async-recovery", help="register bounded asynchronous Module Drill recovery")
    module_async_recovery.add_argument("--run", required=True)
    module_finalize_async = sub.add_parser(
        "module-finalize-async-recovery", help="materialize validated asynchronous Module Drill recovery")
    module_finalize_async.add_argument("--run", required=True)
    module_finalize_model = sub.add_parser(
        "module-finalize-model", help="audit and materialize the authoritative Module Drill model")
    module_finalize_model.add_argument("--run", required=True)
    finalize_map = sub.add_parser(
        "finalize-module-map",
        help="validate complete candidate dispositions in module-map.json, "
             "materialize inferred module nodes, and refresh synthesis input")
    finalize_map.add_argument("--run", required=True, help="overview run directory")
    finalize_findings = sub.add_parser(
        "finalize-findings",
        help="validate atomic findings against source/signal/metric refs and "
             "render the protected technical and PM findings blocks")
    finalize_findings.add_argument("--run", required=True, help="overview run directory")
    finalize_findings.add_argument(
        "--report-failures", default="", metavar="PATH",
        help="(57B-116) write {finding_id: [failures]} JSON to PATH instead "
             "of raising on the first invalid finding, and skip rendering "
             "the protected findings blocks; omit for the original "
             "all-or-nothing validate+render behavior, unchanged")
    rekey_findings = sub.add_parser(
        "rekey-findings",
        help="(orchestrator, 57B-116) re-key a pre-finalization findings "
             "document's affected_modules from candidate IDs to finalized "
             "module IDs via module-map.json's expanded candidate_dispositions "
             "(a pure lookup); findings landing only on excluded/unresolved "
             "candidates are set aside in a 'tail' list instead of guessed")
    rekey_findings.add_argument("--run", required=True, help="overview run directory")
    rekey_findings.add_argument("--in", required=True, dest="findings_in",
                                help="path to the findings JSON document to re-key")
    rekey_findings.add_argument("--out", required=True,
                                help="path to write the {rekeyed, tail} JSON result")
    audit_overview = sub.add_parser(
        "audit-overview",
        help="audit final structured artifacts and reports before marking the "
             "overview stage done")
    audit_overview.add_argument("--run", required=True, help="overview run directory")
    exp = sub.add_parser(
        "export",
        help="export a completed run in a chosen format (deterministic; no "
             "network, no LLM). Default format: html. Written to "
             "<skill-root>/exported/{project}-analysis/{run-id}/{format}/ "
             "(gitignored).")
    exp.add_argument("--run", required=True, help="completed run directory")
    exp.add_argument("--format", nargs="?", const="__list__", default="html",
                     help="output format (default: html); pass --format with no "
                          "value to list available formats")
    exp.add_argument("--skill-root", default="",
                     help="skill root (default: auto-detected from the run path)")
    exp.add_argument("--out", default="",
                     help="explicit output dir (overrides the exported/ location)")
    mark = sub.add_parser("mark-stage", help="record a stage checkpoint as done")
    mark.add_argument("--run", required=True, help="run directory")
    mark.add_argument("--stage", required=True)
    roll = sub.add_parser(
        "rollback", help="re-open a stage AND all later stages (cascade — "
                         "later artifacts may embed the rolled-back outputs)")
    roll.add_argument("--run", required=True)
    roll.add_argument("--stage", required=True)
    status = sub.add_parser(
        "status", help="print resume point + staleness (exit 5 when stale)")
    status.add_argument("--run", required=True)
    accept = sub.add_parser(
        "accept", help="set the project's `current` pointer (explicit user "
                       "acceptance only)")
    accept.add_argument("--run", required=True)
    compare_runs = sub.add_parser(
        "compare-runs",
        help="(dev-only) compare two completed run directories for semantic parity")
    compare_runs.add_argument("base", help="base (known-good) completed run directory")
    compare_runs.add_argument("candidate", help="candidate completed run directory")
    compare_runs.add_argument(
        "--report", default="",
        help="optional path to write the full JSON parity report "
             "(with --semantic: overrides the default parity-semantic.json path)")
    compare_runs.add_argument(
        "--semantic", action="store_true",
        help="(dev-only) semantic-equivalence mode: writes parity-semantic.json, "
             "in the current directory by default (override with --report), "
             "instead of the byte-level report -- tolerant of id/wording churn "
             "a migration is expected to cause, flags substance drift only")
    next_task = sub.add_parser(
        "next-task",
        help="(orchestrator, 57B-115) claim up to N ready orchestrator tasks; "
             "prints the claimed {task, attempt} pairs as JSON")
    next_task.add_argument("--run", required=True, help="run directory")
    next_task.add_argument("--claim", type=int, default=1, help="max tasks to claim")
    next_task.add_argument("--executor-kind", default="manual",
                           help="recorded executor kind (e.g. 'anthropic', 'manual')")
    next_task.add_argument("--model", default="unknown", help="recorded executor model")
    submit_task = sub.add_parser(
        "submit-task",
        help="(orchestrator, 57B-115) submit + validate one orchestrator task result")
    submit_task.add_argument("--run", required=True, help="run directory")
    submit_task.add_argument("--task", required=True, help="task_id being submitted")
    submit_task.add_argument("--result", required=True,
                             help="path to a TaskResult JSON file, or - for stdin")
    run_executor_cmd = sub.add_parser(
        "run-executor",
        help="(orchestrator, 57B-115) bundled headless executor loop -- performs "
             "model-API network calls; invoked explicitly, with your own API key")
    run_executor_cmd.add_argument("--run", required=True, help="run directory")
    run_executor_cmd.add_argument("--adapter", required=True,
                                  choices=["anthropic", "openai-compatible"])
    run_executor_cmd.add_argument("--model", required=True, help="model id")
    run_executor_cmd.add_argument("--concurrency", type=int, default=1)
    run_executor_cmd.add_argument("--base-url", default="",
                                  help="required for --adapter openai-compatible")
    run_executor_cmd.add_argument(
        "--api-key-env", default="",
        help="env var holding the API key (default: ANTHROPIC_API_KEY / OPENAI_API_KEY)")
    run_executor_cmd.add_argument("--temperature", type=float, default=0.0)
    run_executor_cmd.add_argument("--max-attempts", type=int, default=3)
    conformance = sub.add_parser(
        "executor-conformance",
        help="(orchestrator, 57B-115) validate the 8 fixture task types (golden "
             "outputs by default, or a live --adapter/--model) against the "
             "orchestrator schemas")
    conformance.add_argument(
        "--run", default="",
        help="run dir to materialize the fixture DAG into (default: a fresh "
             "temp dir, removed afterward)")
    conformance.add_argument("--adapter", default="",
                             choices=["", "anthropic", "openai-compatible"],
                             help="omit to self-check the built-in golden outputs (no network)")
    conformance.add_argument("--model", default="")
    conformance.add_argument("--concurrency", type=int, default=1)
    conformance.add_argument("--base-url", default="")
    conformance.add_argument("--api-key-env", default="")
    plan_judgment_cmd = sub.add_parser(
        "plan-judgment",
        help="(orchestrator, 57B-116) compose + register the judgment DAG for a "
             "prepared run: one lens-findings task per repo-sharded lens x repo "
             "(plus one per workspace-sharded lens), the independent "
             "formation-proposal task, and -- for a source_reads lens -- its "
             "paired selection-fetch task in place of the lens task directly "
             "(fetch-selections + plan-lens-finalize compose the real one)")
    plan_judgment_cmd.add_argument("--run", required=True, help="run directory")
    plan_judgment_cmd.add_argument(
        "--context-budget", type=int, default=96000, dest="context_budget",
        help="per-packet context budget in estimated tokens (default: 96000)")
    plan_dedup_cmd = sub.add_parser(
        "plan-dedup",
        help="(orchestrator, 57B-116) compose + register the single global "
             "dedup-rank task from every VALIDATED lens-findings output already "
             "in the run's ledger -- run this after plan-judgment's lens tasks "
             "have validated")
    plan_dedup_cmd.add_argument("--run", required=True, help="run directory")
    plan_dedup_cmd.add_argument(
        "--context-budget", type=int, default=96000, dest="context_budget",
        help="per-packet context budget in estimated tokens (default: 96000)")
    assemble_findings_cmd = sub.add_parser(
        "assemble-findings",
        help="(orchestrator, 57B-116) deterministically merge the run's "
             "validated lens-findings pool via its validated dedup-rank "
             "output's merge_map/rank into findings.json-shaped rows -- pure "
             "mechanical application, no judgment; still candidate-keyed "
             "(rekey-findings runs after this)")
    assemble_findings_cmd.add_argument("--run", required=True, help="run directory")
    assemble_findings_cmd.add_argument(
        "--out", required=True, help="path to write the assembled findings JSON document")
    write_module_map_cmd = sub.add_parser(
        "write-module-map",
        help="(orchestrator, 57B-116) materialize module-map.json from the "
             "run's single validated formation-proposal task -- mechanical "
             "only (the task already decided modules/dispositions/rules); "
             "finalize-module-map (unchanged) validates/expands it afterward")
    write_module_map_cmd.add_argument("--run", required=True, help="run directory")
    write_module_map_cmd.add_argument(
        "--out", default="",
        help="override output path (default: <run>/module-map.json, the "
             "canonical location finalize-module-map reads)")
    fetch_selections_cmd = sub.add_parser(
        "fetch-selections",
        help="(orchestrator, 57B-116) fetch bounded, revision-checked, sanitized "
             "+/-40-line source excerpts for a VALIDATED selection-fetch task's "
             "requested locations -- writes fetched-evidence.json; run this "
             "before plan-lens-finalize for the same lens")
    fetch_selections_cmd.add_argument("--run", required=True, help="run directory")
    fetch_selections_cmd.add_argument(
        "--task", required=True, dest="task",
        help="the validated selection-fetch task_id to fetch for (a "
             "<lens-task-id>-select id)")
    fetch_selections_cmd.add_argument(
        "--out", default="",
        help="override output path (default: <run>/tasks/<task>-fetched-"
             "evidence.json, the canonical location plan-lens-finalize reads)")
    plan_lens_finalize_cmd = sub.add_parser(
        "plan-lens-finalize",
        help="(orchestrator, 57B-116) phase 2 of a source_reads lens's select/"
             "finalize pair: compose + register the REAL lens-findings task "
             "from its original inputs plus fetch-selections's fetched-"
             "evidence.json -- run this after fetch-selections for the same lens")
    plan_lens_finalize_cmd.add_argument("--run", required=True, help="run directory")
    plan_lens_finalize_cmd.add_argument(
        "--lens", required=True, dest="lens",
        help="the ORIGINAL lens task_id (not the -select id) plan-judgment "
             "would have used directly for a non-source_reads lens")
    plan_lens_finalize_cmd.add_argument(
        "--context-budget", type=int, default=96000, dest="context_budget",
        help="per-packet context budget in estimated tokens (default: 96000)")

    plan_reports_cmd = sub.add_parser(
        "plan-reports",
        help="register the authored report sections that are ready to run "
             "(rendered sections are produced at assembly time, not as tasks); "
             "call once per wave -- a later wave's packets are composed from "
             "the sections earlier waves already produced")
    plan_reports_cmd.add_argument("--run", required=True, help="run directory")
    plan_reports_cmd.add_argument(
        "--document", default=None,
        help="restrict to one document (default: all three)")
    plan_reports_cmd.add_argument(
        "--wave", type=int, default=None,
        help="restrict to one wave (default: every section whose dependencies "
             "are already satisfied)")
    plan_reports_cmd.add_argument(
        "--context-budget", type=int, default=96000, dest="context_budget",
        help="per-packet context budget in estimated tokens (default: 96000)")

    assemble_reports_cmd = sub.add_parser(
        "assemble-reports",
        help="write the report document(s) from rendered + validated authored "
             "sections, in template order -- assembly is the ONLY writer, so "
             "headings, ordering and protected blocks cannot drift")
    assemble_reports_cmd.add_argument("--run", required=True, help="run directory")
    assemble_reports_cmd.add_argument(
        "--document", default=None, help="one document (default: all three)")

    report_floors_cmd = sub.add_parser(
        "report-floors",
        help="report a document's completeness FLOORS together with its prose "
             "ceiling -- never the ceiling alone, because overflow is fixed by "
             "relocating content, never by dropping it")
    report_floors_cmd.add_argument("--run", required=True, help="run directory")
    report_floors_cmd.add_argument(
        "--document", default=None, help="one document (default: all three)")
    report_floors_cmd.add_argument(
        "--out", default="", help="optional path to write the JSON report")

    run_pipeline_cmd = sub.add_parser(
        "run-pipeline",
        help="drive the whole analysis end to end against an executor -- "
             "prepare, plan, execute, fetch, finalize, assemble, audit -- with "
             "no hand-driven orchestration")
    run_pipeline_cmd.add_argument("--run", required=True, help="prepared run directory")
    run_pipeline_cmd.add_argument(
        "--executor", default="api", choices=("api", "external"),
        help="'api' drives the bundled headless executor; 'external' stops at "
             "each phase boundary so another harness can claim the tasks")
    run_pipeline_cmd.add_argument("--adapter", default="anthropic",
                                  choices=("anthropic", "openai-compatible"))
    run_pipeline_cmd.add_argument("--model", default="", help="executor model id")
    run_pipeline_cmd.add_argument("--base-url", default="", dest="base_url")
    run_pipeline_cmd.add_argument("--api-key-env", default="", dest="api_key_env")
    run_pipeline_cmd.add_argument("--concurrency", type=int, default=4)
    run_pipeline_cmd.add_argument(
        "--context-budget", type=int, default=180000, dest="context_budget",
        help="per-packet context budget; must stay consistent across phases")
    run_pipeline_cmd.add_argument(
        "--stop-after", default="", help="stop after this phase (for staged runs)")
    return result


def _plan_reports_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import reports
    run = Path(args.run).expanduser().resolve()
    planned = reports.plan_reports(
        run, document=args.document, wave=args.wave,
        context_budget_tokens=args.context_budget)
    if not planned:
        print("no report section is ready to plan "
              "(every ready section is already registered, or an earlier wave "
              "has not produced its dependencies yet)")
        return 0
    for row in planned:
        state = "created" if row.created else "unchanged"
        print(f"{row.section_id} -> {row.task_id} (wave {row.wave}, "
              f"budget {row.budget_words}w, ~{row.estimated_tokens} tokens) [{state}]")
    print(f"planned {len(planned)} section task(s)")
    return 0


def _assemble_reports_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import reports, sections as catalog
    run = Path(args.run).expanduser().resolve()
    documents = [args.document] if args.document else list(catalog.DOCUMENTS)
    for document in documents:
        path = reports.assemble_document(run, document)
        print(f"wrote {path}")
    return 0


def _report_floors_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import reports, sections as catalog
    run = Path(args.run).expanduser().resolve()
    documents = [args.document] if args.document else list(catalog.DOCUMENTS)
    payload, failed = {}, False
    for document in documents:
        report = reports.document_floors(run, document)
        payload[document] = report
        failed = failed or bool(report["failures"])
        print(f"{document}: {report['sections_present']}/{report['sections_expected']} "
              f"section(s) present, {report['prose_words']} prose words "
              f"(ceiling {report['prose_ceiling']}), {len(report['failures'])} failure(s)")
        for failure in report["failures"][:20]:
            print(f"  {failure['check']} @ {failure['location']}: {failure['detail']}")
    if args.out:
        Path(args.out).expanduser().resolve().write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {args.out}")
    return 3 if failed else 0


def _run_pipeline_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import driver
    run = Path(args.run).expanduser().resolve()
    outcome = driver.run_pipeline(
        run,
        executor=args.executor, adapter=args.adapter, model=args.model,
        base_url=args.base_url, api_key_env=args.api_key_env,
        concurrency=args.concurrency, context_budget_tokens=args.context_budget,
        stop_after=args.stop_after or None, log=print)
    print(json.dumps(outcome["summary"], indent=2, sort_keys=True))
    return 0 if outcome["complete"] else 3


def _discover(args: argparse.Namespace) -> int:
    from .discovery import emit
    exclude = [x.strip() for x in args.exclude.split(",") if x.strip()]
    spec, report = emit.discover(
        args.workspace, exclude_names=exclude,
        analyzer_root=args.analyzer_root or None)
    targets_path, report_path = emit.write_stage1(args.out, spec, report)
    print(f"{len(spec.repos)} target repo(s), "
          f"{len(spec.integration_candidates)} integration candidate(s)")
    for line in report["not_targeted"]:
        print(f"not targeted: {line}")
    print(f"wrote {targets_path}")
    print(f"wrote {report_path}")
    return 0


def _state_dir_for(run_dir: Path) -> Path:
    """Map a readable project output key to its matching state directory."""
    project_dir = run_dir.parent.parent
    return project_dir.parent.parent / "state" / project_dir.name


def _new_run(args: argparse.Namespace) -> int:
    from . import identity, lifecycle, run_provenance
    from .discovery import emit, self_exclusion
    model = run_provenance.metadata_value(args.model, "model")
    effort = run_provenance.metadata_value(args.effort, "effort")
    analyzer_root = self_exclusion.resolve_analyzer_root(args.analyzer_root or None)
    exclude = [x.strip() for x in args.exclude.split(",") if x.strip()]
    spec, report = emit.discover(
        args.workspace, exclude_names=exclude,
        analyzer_root=args.analyzer_root or None)
    identities = identity.build(
        spec,
        workspace_root=report["workspace_root"],
        project_id=report["project_id"],
    )
    output_root = Path(args.skill_root).expanduser().resolve() / "output"
    project_key = identity.claim_project_namespace(output_root, identities)
    overview_root = output_root / project_key / "overview"
    run_id = lifecycle.mint_run_id(
        [f"{r.repo_id}:{r.git.head}:{r.git.dirty_detail}" for r in spec.repos],
        args.language, label=args.run_id,
        exists=lambda rid: (overview_root / rid).exists())
    run_dir = overview_root / run_id
    emit.write_stage1(run_dir, spec, report)
    analyzer = run_provenance.analyzer_observation(analyzer_root)
    state = lifecycle.RunState.create(
        run_id, report["project_id"], spec, language=args.language,
        analysis_identity={
            "wrapper": "project-analysis-wrapper",
            "analyzer": analyzer,
            "model": model,
            "effort": effort,
        })
    state.mark("discovery")
    run_provenance.write(
        run_dir,
        run_provenance.create_document(
            spec,
            analyzer_root=analyzer_root,
            language=args.language,
            model=args.model,
            effort=args.effort,
            analyzed_at=state.analyzed_at,
        ),
    )
    state.save(run_dir)
    print(f"run: {run_dir}")
    print(f"inspection_only: {state.inspection_only}")
    print(f"next stage: {state.next_stage()}")
    return 0


def _run_capability_providers(spec: TargetSpec, out: Path, scan_date: str, *,
                              capability_id: str, allow_network: bool) -> None:
    """Run every bundled provider for ONE capability against ``spec``,
    writing only that capability's own canonical artifacts (fragments / map
    files — the caller's assembler turns those into the final coverage doc).

    Deliberately does NOT write ``provider-execution.json`` or
    ``evidence-catalog.json``: both are REPLACE-style writers over the
    run's FULL provider set, so a capability-scoped call here would either
    silently narrow an existing full record down to just this capability, or
    (chaining ``callgraph`` then ``dependency-map`` into one run dir — a
    supported sequence, per the stage markers) leave them reflecting only
    whichever capability ran LAST — contradicting the other capability's
    artifacts still sitting on disk, with no later step to catch it
    (``prepare-overview`` skips its own provider pass once both coverage
    docs already exist). Those two run-level disclosure artifacts are owned
    solely by ``prepare-overview``'s single full, unfiltered provider pass.
    """
    from . import identity
    from .profiles.bundled import BUNDLED_PROFILES, BUNDLED_PROVIDERS
    from .profiles.contracts import RunContext
    from .profiles.execution import run_providers
    from .profiles.registry import ProfileRegistry
    from .profiles.tool_access import ExecutorToolAccess

    identities = identity.load(out)
    registry = ProfileRegistry(
        BUNDLED_PROFILES,
        tuple(p for p in BUNDLED_PROVIDERS if p.capability_id == capability_id))
    access = ExecutorToolAccess(spec, identities, out, scan_date,
                                network_authorized=allow_network)
    context = RunContext(
        targets=spec, output_dir=out, scan_date=scan_date,
        network_authorized=allow_network, provenance={},
        tool_access=access, identities=identities,
    )
    run_providers(registry, context)


def _callgraph(args: argparse.Namespace, spec: TargetSpec, out: Path) -> int:
    from .callgraph import emit as cg_emit
    _run_capability_providers(
        spec, out, args.scan_date, capability_id="callgraph",
        allow_network=args.include_network)
    report = cg_emit.assemble(out, args.scan_date)
    for cov in sorted(report.repos, key=lambda c: (c.repository_ref, c.lang)):
        suffix = f": {cov.reason}" if cov.reason else ""
        print(f"{cov.repository_ref} [{cov.lang}] {cov.status} "
              f"(edges={cov.edges_emitted}, resolved={cov.call_sites.resolved})"
              f"{suffix}")
    if not report.repos:
        print("no Go/JS/TS repositories in the TargetSpec — nothing to analyze")
        return 0
    return 3 if cg_emit.aggregate_status(report) is Status.FAILED else 0


def _depmap(args: argparse.Namespace, spec: TargetSpec, out: Path) -> int:
    from .depmap import emit as dm_emit
    _run_capability_providers(
        spec, out, args.scan_date, capability_id="dependency-map",
        allow_network=args.include_network)
    report = dm_emit.assemble(out, args.scan_date)
    for cov in sorted(report.repos, key=lambda c: (c.repository_ref, c.lane)):
        suffix = f": {cov.reason}" if cov.reason else ""
        print(f"{cov.repository_ref} [{cov.lane}] {cov.status} "
              f"(units={cov.units}, map={cov.map_file or '-'}){suffix}")
    if not report.repos:
        print("no Go/JS/TS repositories in the TargetSpec — nothing to analyze")
        return 0
    return 3 if dm_emit.aggregate_status(report) is Status.FAILED else 0


def _system_model(args: argparse.Namespace) -> int:
    from .system_model.assemble import assemble, dump
    run = Path(args.run).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    # $WORKSPACE relativization backstop for the sanitizer (citations are already
    # repo-relative, but keep parity with the other emitters).
    if spec.repos and not os.environ.get("WORKSPACE_ROOT"):
        os.environ["WORKSPACE_ROOT"] = os.path.commonpath(
            [str(Path(r.path).expanduser().resolve()) for r in spec.repos])
    model = assemble(run)
    out = dump(model, run)
    stats = model.to_dict()["stats"]
    print(f"wrote {out}")
    print(f"nodes: {stats['node_count']} {stats['nodes_by_kind']}")
    print(f"edges: {stats['edge_count']} {stats['edges_by_type']}")
    partials = [name for name, p in model.coverage.items()
                if p["status"] != "complete"]
    if partials:
        print("non-complete partitions: " + ", ".join(
            f"{n}={model.coverage[n]['status']}" for n in sorted(partials)))
    return 0


def _load_object(path: Path) -> dict:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _assert_fresh_run(run: Path, *, require_provenance: bool = True) -> "object":
    """Refuse to advance a real run after target/analyzer state changed."""
    from . import lifecycle, run_provenance
    state = lifecycle.RunState.load(run)
    provenance = None
    if require_provenance:
        provenance = run_provenance.load(run)
    stale = state.staleness()
    if provenance is not None:
        spec = TargetSpec.load(run / "targets.json")
        stale.extend(run_provenance.target_source_staleness(provenance, spec))
    if stale:
        raise ValueError("run is stale; mint a new run: " + "; ".join(stale))
    return state


def _prepare_overview(args: argparse.Namespace) -> int:
    """Authoritative deterministic overview preparation (57B-47).

    Existing canonical stage outputs are validated and reused; missing stages
    run exactly once.  Producer paths never depend on model effort.
    """
    from . import (capabilities, cohesion, coverage_render, lifecycle, module_map,
                   overview_audit, run_provenance, synthesis_input,
                   workspace_metrics)
    from .callgraph import emit as cg_emit
    from .depmap import emit as dm_emit
    from .routes import emit as routes_emit
    from .system_model.assemble import assemble, dump

    run = Path(args.run).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    use_existing_run_directory(run, spec.repos)
    state = _assert_fresh_run(run)
    # Bind every option that can change deterministic evidence before deciding
    # whether an existing canonical checkpoint may be reused.
    provenance = run_provenance.load(run)
    bound = provenance.get("preparation")
    bound = bound if isinstance(bound, dict) else {}
    args.scan_date = args.scan_date or bound.get("scan_date") or date.today().isoformat()
    args.since = (args.since or bound.get("history_since")
                  or (date.today() - timedelta(days=730)).isoformat())
    allowed_hosts = [host.strip().lower() for host in args.allow_hosts.split(",")
                     if host.strip()]
    provenance = run_provenance.bind_preparation(run, {
        "scan_date": args.scan_date,
        "history_since": args.since,
        "coupling_sample_cap": args.coupling_sample_cap,
        "network_authorized": args.include_network,
        "allowed_hosts": allowed_hosts,
    })
    if spec.repos and not os.environ.get("WORKSPACE_ROOT"):
        os.environ["WORKSPACE_ROOT"] = os.path.commonpath(
            [str(Path(repo.path).expanduser().resolve()) for repo in spec.repos])

    signals = run / "signals"
    signal_summary = signals / "run-summary.json"
    # ``fresh_sweep_results`` stays None on a REUSED signals stage (below):
    # no new signal ran, so nothing needs recording into run-summary.json —
    # matching the untouched "reused canonical run-summary.json" contract.
    # On a FRESH stage it holds the sweep's own results (now excluding
    # PROVIDER_OWNED_SIGNAL_TOOLS — see git-history/dependency-risk below),
    # merged with the providers' own signal results once they've run, so the
    # single run-summary.json write still reflects every signal tool that
    # actually executed this pass (57B-82 A2).
    fresh_sweep_results: list[SignalResult] | None = None
    if signal_summary.is_file():
        _load_object(signal_summary)
        print("signals: reused canonical run-summary.json")
    else:
        if signals.exists():
            raise ValueError("signals/ exists without run-summary.json; refuse partial reuse")
        out = prepare_output_directory(signals, spec.repos)
        from . import identity
        fresh_sweep_results = _sweep(args, spec, out, identity.load(run),
                                     exclude_tool_names=PROVIDER_OWNED_SIGNAL_TOOLS)

    need_callgraph = not (run / "callgraph-coverage.json").is_file()
    if not need_callgraph:
        _load_object(run / "callgraph-coverage.json")
        print("callgraph: reused canonical coverage")
    elif (run / "callgraph").exists():
        raise ValueError("callgraph/ exists without canonical coverage; refuse partial reuse")

    dep_marker = run / "imports" / "depmap-coverage.json"
    need_depmap = not dep_marker.is_file()
    if not need_depmap:
        _load_object(dep_marker)
        print("dependency-map: reused canonical coverage")
    elif (run / "imports").exists():
        raise ValueError("imports/ exists without canonical coverage; refuse partial reuse")

    routes_marker = run / "routes" / "route-coverage.json"
    need_routes = not routes_marker.is_file()
    if not need_routes:
        _load_object(routes_marker)
        print("routes: reused canonical coverage")
    elif (run / "routes").exists():
        raise ValueError("routes/ exists without canonical coverage; refuse partial reuse")

    # The provider stage now ALWAYS runs on every full prepare-overview pass
    # (57B-80 PR2), regardless of need_callgraph/need_depmap: BUNDLED_PROVIDERS
    # also carries universal providers (datastore-evidence, deploy-units,
    # git-history, dependency-risk) whose own full-tree scan (or, for the
    # signal-tool pair, own executor-backed run) is the honest absence proof
    # behind their capability's not-applicable/coverage verdict, so they need
    # to run every pass — not only when callgraph/depmap happen to be stale.
    # This is safe unconditionally: an empty/no-op selection already yields a
    # stable, byte-identical provider-execution.json/evidence-catalog.json
    # (run_providers's own documented guarantee), and when BOTH
    # callgraph/depmap coverage docs already exist, the four lane providers
    # still re-run and rewrite byte-identical fragments (harmless, but not
    # free) — exactly the same "re-run, nothing new to assemble" tradeoff
    # already accepted below when only ONE of the two was previously stale.
    # Only the missing capability's assembler call actually produces a new
    # final artifact; an already-assembled capability's output is left
    # untouched. git-history/dependency-risk are ALSO safe on a re-run of an
    # already-signals-complete pass: they reuse the existing manifest rather
    # than re-invoking the tool (see providers.py's ``_run_or_reuse_signal``)
    # — the collector below only ever gets fresh entries when the tool
    # ACTUALLY executed this pass.
    from . import identity
    from .profiles.execution import run_provider_stage
    provider_signal_results: list[SignalResult] = []
    provider_summary = run_provider_stage(
        run, spec, identity.load(run), scan_date=args.scan_date,
        network_authorized=args.include_network, provenance=provenance,
        signal_results=provider_signal_results)
    print(f"providers: {provider_summary['executions']} execution(s), "
          f"{provider_summary['failed']} failed")
    if fresh_sweep_results is not None:
        # Deferred from the signals block above (57B-82 A2): only NOW do the
        # git-history/dependency-risk providers' own signal results exist to
        # fold in, so run-summary.json still carries every signal tool that
        # ran this pass — byte-identical in shape to the pre-A2 sweep-only
        # summary, just sourced from two places instead of one.
        combined_results = fresh_sweep_results + provider_signal_results
        _record_summary(signals, combined_results)
        print(f"signals: wrote {len(combined_results)} result(s)")
    if need_callgraph:
        report = cg_emit.assemble(run, args.scan_date)
        print(f"callgraph: wrote {len(report.repos)} lane result(s)")
    if need_depmap:
        report = dm_emit.assemble(run, args.scan_date)
        print(f"dependency-map: wrote {len(report.repos)} lane result(s)")
    if need_routes:
        route_result = routes_emit.assemble(run)
        print(f"routes: {route_result.backends} backend(s), "
              f"{route_result.frontends} frontend(s)")

    run_provenance.refresh_tool_versions(run)

    model = assemble(run)
    dump(model, run)
    model_doc = model.to_dict()
    module_map.write_candidates(run, model_doc)
    # Cohesion measurements (57B-116, M2) read module-candidates.json (just
    # written above) plus the model/signals already on disk by this point in
    # the stage plan — every one of its inputs is already available here, and
    # nothing later in this function depends on its output, so it slots in
    # right after the candidate universe it measures over.
    cohesion_path = cohesion.write(run, model_doc)
    capabilities_path = capabilities.write(run)
    coverage_path = coverage_render.write(run)
    metrics_path = workspace_metrics.write(run)
    packet_path = synthesis_input.write(run)
    audit_path = overview_audit.write(run)
    audit = _load_object(audit_path)
    capability_doc = _load_object(capabilities_path)
    print(f"wrote {cohesion_path}")
    print(f"wrote {capabilities_path}")
    print(f"wrote {coverage_path}")
    print(f"wrote {metrics_path}")
    print(f"wrote {packet_path}")
    print(f"audit: {audit['status']} ({audit['failed_count']} failed checks)")
    if audit["status"] == "passed" and capability_doc["aggregate_status"] != "failed":
        if state.stages.get("signals") != "done":
            state.mark("signals")
            state.save(run)
        print(f"signals checkpoint: done; next: {state.next_stage() or '(complete)'}")
        return 0
    return 3


def prepare_deterministic_evidence(args: argparse.Namespace) -> int:
    """Run the canonical deterministic evidence pass without generating prose.

    The historical command name remains ``prepare-overview`` because it is
    the public overview entry point.  Its implementation has always stopped
    before LLM judgment and report assembly, however, so Module Drill can
    safely reuse the exact same discovery/provider/system-model preparation
    surface for a standalone source snapshot.
    """
    return _prepare_overview(args)


def _finalize_module_map(args: argparse.Namespace) -> int:
    from . import module_map, module_render, overview_audit, synthesis_input
    from .system_model.assemble import assemble, dump
    run = Path(args.run).expanduser().resolve()
    if (run / "run-state.json").is_file():
        _assert_fresh_run(run)
    module_map.expand_candidate_rules(run)
    module_map.validate(run)
    model = assemble(run)
    dump(model, run)
    module_render.write(run)
    synthesis_input.write(run)
    audit_path = overview_audit.write(run, require_module_map=True)
    audit = _load_object(audit_path)
    modules = model.coverage["modules"]["counts"].get("modules", 0)
    print(f"module map: {modules} inferred module node(s)")
    print(f"audit: {audit['status']} ({audit['failed_count']} failed checks)")
    return 0 if audit["status"] == "passed" else 3


def _finalize_findings(args: argparse.Namespace) -> int:
    from . import findings
    run = Path(args.run).expanduser().resolve()
    if (run / "run-state.json").is_file():
        _assert_fresh_run(run)
    if getattr(args, "report_failures", ""):
        # Incremental failure-surface mode (57B-116): additive-only branch —
        # the flag's absence falls straight through to the original
        # validate+render path below, untouched.
        failures = findings.validate_report_failures(run)
        out_path = Path(args.report_failures).expanduser().resolve()
        out_path.write_text(json.dumps(failures, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"findings: {len(failures)} finding(s) with failures")
        print(f"wrote {out_path}")
        return 0 if not failures else 3
    technical, pm = findings.write(args.run)
    count = len(findings.validate(args.run).get("findings", []))
    print(f"findings: {count} validated atomic finding(s)")
    print(f"wrote {technical}")
    print(f"wrote {pm}")
    return 0


def _rekey_findings(args: argparse.Namespace) -> int:
    from .orchestrator import rekey
    run = Path(args.run).expanduser().resolve()
    findings_path = Path(args.findings_in).expanduser().resolve()
    findings_doc = json.loads(findings_path.read_text("utf-8"))
    result = rekey.rekey(run, findings_doc)
    out_path = Path(args.out).expanduser().resolve()
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", "utf-8")
    print(f"rekeyed: {len(result['rekeyed'])}, tail: {len(result['tail'])}")
    print(f"wrote {out_path}")
    return 0


def _audit_overview(args: argparse.Namespace) -> int:
    from . import overview_audit
    run = Path(args.run).expanduser().resolve()
    if (run / "run-state.json").is_file():
        _assert_fresh_run(run)
    out = overview_audit.write(run, require_module_map=True, require_reports=True)
    result = _load_object(out)
    print(f"audit: {result['status']} ({result['failed_count']} failed checks)")
    for row in result["checks"]:
        if row["status"] == "fail":
            print(f"failed {row['check']}: {row['detail']}")
    return 0 if result["status"] == "passed" else 3


def _find_skill_root(run: Path) -> Path:
    """Locate the skill root above a run dir (the dir holding SKILL.md).

    Falls back to the ``<skill-root>/output/<project>/<stage>/<run>`` layout.
    """
    for parent in [run, *run.parents]:
        if (parent / "SKILL.md").is_file():
            return parent
    return run.parents[3] if len(run.parents) >= 4 else run.parent


def _export(args: argparse.Namespace) -> int:
    from . import export as export_pkg
    if args.format == "__list__":
        print("available export formats: "
              + ", ".join(export_pkg.available_formats()))
        return 0
    run = Path(args.run).expanduser().resolve()
    try:
        if args.out:
            result = export_pkg.export(
                run, args.format, out_dir=Path(args.out).expanduser().resolve())
        else:
            skill_root = (Path(args.skill_root).expanduser().resolve()
                          if args.skill_root else _find_skill_root(run))
            result = export_pkg.export(run, args.format, skill_root=skill_root)
    except export_pkg.ExporterUnavailable as exc:
        print(f"export unavailable: {exc}", file=sys.stderr)
        return 3
    print(f"exported {args.format} -> {result.out_dir}")
    detail = result.detail
    if detail is not None:
        print(f"pages: {len(detail.pages)} · documents: {len(detail.documents)} · "
              f"sections: {detail.section_count} · diagrams: {detail.diagram_count}")
        if detail.missing_artifacts:
            print("missing optional artifacts (rendered as unavailable): "
                  + ", ".join(detail.missing_artifacts))
    return 0


def _lifecycle_cmd(args: argparse.Namespace) -> int:
    from . import lifecycle, run_provenance
    run_dir = Path(args.run).expanduser().resolve()
    state = lifecycle.RunState.load(run_dir)
    if args.command in ("mark-stage", "rollback", "accept"):
        # Runs missing the current provenance contract cannot be reopened,
        # advanced, or accepted; regenerate them under the current contract.
        provenance = run_provenance.load(run_dir)
        stale = state.staleness()
        spec = TargetSpec.load(run_dir / "targets.json")
        stale.extend(run_provenance.target_source_staleness(provenance, spec))
        if stale:
            raise ValueError("run is stale; mint a new run: " + "; ".join(stale))
    if args.command == "mark-stage":
        state.mark(args.stage)
        state.save(run_dir)
        if args.stage == "overview":
            lifecycle.Pointers(_state_dir_for(run_dir)).set_latest_completed(state.run_id)
            print(f"latest_completed -> {state.run_id}")
        print(f"stage {args.stage}: done; next: {state.next_stage() or '(complete)'}")
        return 0
    if args.command == "rollback":
        reopened = state.rollback(args.stage)
        state.save(run_dir)
        print(f"re-opened: {', '.join(reopened)}; next: {state.next_stage()}")
        return 0
    if args.command == "status":
        stale = state.staleness()
        provenance_path = run_provenance.path_for(run_dir)
        if provenance_path.is_file():
            provenance = run_provenance.load(run_dir)
            spec = TargetSpec.load(run_dir / "targets.json")
            stale.extend(run_provenance.target_source_staleness(provenance, spec))
        print(f"run: {state.run_id} (inspection_only: {state.inspection_only})")
        print(f"next stage: {state.next_stage() or '(complete)'}")
        for line in stale:
            print(f"stale: {line}")
        return 5 if stale else 0
    if args.command == "accept":
        lifecycle.Pointers(_state_dir_for(run_dir)).accept(state)
        print(f"current -> {state.run_id}")
        return 0
    raise AssertionError(args.command)


def _print_semantic_summary(report: dict) -> None:
    findings = report["findings"]
    if findings == "not present in both runs":
        print("findings: not present in both runs")
    else:
        with_deltas = sum(1 for pair in findings["matched"] if pair["deltas"])
        print(f"findings: {len(findings['matched'])} matched "
              f"({with_deltas} with substance deltas), "
              f"{len(findings['unmatched_left'])} unmatched (base), "
              f"{len(findings['unmatched_right'])} unmatched (candidate)")
    module_map = report["module_map"]
    if module_map == "not present in both runs":
        print("module_map: not present in both runs")
    else:
        print(f"module_map: +{len(module_map['added'])} -{len(module_map['removed'])} "
              f"~{len(module_map['changed'])}")
    for name, doc in sorted(report["citations"].items()):
        if doc == "not present in both runs":
            print(f"citations/{name}: not present in both runs")
        else:
            print(f"citations/{name}: base_valid={doc['base_valid_count']} "
                  f"candidate_valid={doc['candidate_valid_count']} "
                  f"+{len(doc['added'])} -{len(doc['removed'])}")
    disposition = report["disposition_totals"]
    if disposition == "not present in both runs":
        print("disposition_totals: not present in both runs")
    else:
        print(f"disposition_totals: equal={disposition['equal']} "
              f"base={disposition['base']} candidate={disposition['candidate']}")
    for name, row in sorted(report["coverage"].items()):
        print(f"coverage/{name}: equal={row['equal']} differences={row['difference_count']}")
    for name, doc in sorted(report["section_completeness"].items()):
        if doc == "not present in both runs":
            print(f"section_completeness/{name}: not present in both runs")
            continue
        regressions = sorted(section_name for section_name, section in doc["sections"].items()
                             if not section["equal_or_greater"])
        suffix = f" [{', '.join(regressions[:5])}]" if regressions else ""
        print(f"section_completeness/{name}: {len(doc['sections'])} section(s), "
              f"{len(regressions)} regression(s){suffix}")


def _compare_runs_semantic(args: argparse.Namespace) -> int:
    from . import parity
    report = parity.compare_semantic(args.base, args.candidate)
    report_path = (Path(args.report) if args.report
                  else Path("parity-semantic.json")).expanduser().resolve()
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", "utf-8")
    print(f"base:      {args.base}")
    print(f"candidate: {args.candidate}")
    print(f"semantic report: {report_path}")
    _print_semantic_summary(report)
    return 3 if parity.has_semantic_mode_differences(report) else 0


def _compare_runs(args: argparse.Namespace) -> int:
    from . import parity
    if args.semantic:
        return _compare_runs_semantic(args)
    report = parity.compare(args.base, args.candidate)
    if args.report:
        Path(args.report).expanduser().resolve().write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", "utf-8")
    print(f"base:      {args.base}")
    print(f"candidate: {args.candidate}")
    for side in ("base", "candidate"):
        block = report["baseline"][side]["identity"]
        if block is not None:
            analyzer = block["analyzer"]
            print(f"  {side} analyzer: version={analyzer.get('version')} "
                  f"head={analyzer.get('git_head', '')[:12]} "
                  f"dirty={analyzer.get('dirty_detail')}")
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")
    if report["tool_drift"]:
        print("tool drift (informational; not counted):")
        for row in report["tool_drift"]:
            print(f"  {row['tool']}: base={row['base_version']} "
                  f"candidate={row['candidate_version']}")
    reasons = report["provider_execution_reasons"]
    if reasons["added"] or reasons["removed"]:
        print("provider execution reasons (informational; not counted — "
              "free text, environment-volatile):")
        for item in reasons["added"]:
            print(f"  + {item}")
        for item in reasons["removed"]:
            print(f"  - {item}")
    for name, section in sorted(report["sections"].items()):
        count = len(section["added"]) + len(section["removed"]) + len(section["changed"])
        presence = ("" if section["base_present"] == section["candidate_present"] else
                    f" [base_present={section['base_present']} "
                    f"candidate_present={section['candidate_present']}]")
        print(f"{name}: {count} difference(s){presence}")
        for row in section["added"]:
            print(f"  + {row['key']}: {row['value']}")
        for row in section["removed"]:
            print(f"  - {row['key']}: {row['value']}")
        for row in section["changed"]:
            tag = "reclassified" if row["reclassified"] else "conflicting"
            print(f"  ~ {row['key']} ({tag}): {row['base']} -> {row['candidate']}")
    for name, rows in sorted(report["prose"].items()):
        if not rows["added"] and not rows["removed"]:
            continue
        print(f"prose/{name}:")
        for item in rows["added"]:
            print(f"  + {item}")
        for item in rows["removed"]:
            print(f"  - {item}")
    print(f"total differences: {report['summary']['total_differences']}")
    return 3 if parity.has_semantic_differences(report) else 0


def _next_task(args: argparse.Namespace) -> int:
    from .orchestrator.engine import Engine
    run = Path(args.run).expanduser().resolve()
    engine = Engine(run)
    if not engine.ledger_exists():
        print(f"wrapper input error: no orchestrator ledger at "
              f"{run / 'tasks' / 'ledger.jsonl'} -- create tasks before claiming",
              file=sys.stderr)
        return 6
    claimed = engine.claim(args.claim, executor_kind=args.executor_kind, model=args.model)
    print(json.dumps([{"task": item.packet.to_dict(), "attempt": item.attempt}
                      for item in claimed], indent=2, sort_keys=True))
    return 0


def _submit_task(args: argparse.Namespace) -> int:
    from .orchestrator.engine import Engine, EngineError
    run = Path(args.run).expanduser().resolve()
    raw_text = sys.stdin.read() if args.result == "-" else \
        Path(args.result).expanduser().resolve().read_text("utf-8")
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(f"wrapper input error: --result is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(raw, dict):
        print("wrapper input error: --result must contain a JSON object", file=sys.stderr)
        return 2
    engine = Engine(run)
    try:
        outcome = engine.submit(args.task, raw)
    except EngineError as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0 if outcome["status"] == "validated" else 3


def _run_executor_cmd(args: argparse.Namespace) -> int:
    from .orchestrator.executor_api import AdapterConfig, ExecutorError, run_executor
    run = Path(args.run).expanduser().resolve()
    config = AdapterConfig(name=args.adapter, model=args.model, base_url=args.base_url,
                           api_key_env=args.api_key_env, temperature=args.temperature)
    try:
        summary = run_executor(run, config, concurrency=args.concurrency,
                               max_attempts=args.max_attempts)
    except ExecutorError as exc:
        print(f"wrapper executor error: {exc}", file=sys.stderr)
        return 4
    print(f"validated: {len(summary['validated'])}, failed: {len(summary['failed'])}")
    for task_id in summary["failed"]:
        print(f"  failed: {task_id}")
    return 0 if not summary["failed"] else 3


def _executor_conformance_cmd(args: argparse.Namespace) -> int:
    from .orchestrator.conformance import run_conformance
    from .orchestrator.executor_api import AdapterConfig, ExecutorError
    config = None
    if args.adapter:
        if not args.model:
            print("wrapper input error: --model is required with --adapter", file=sys.stderr)
            return 2
        config = AdapterConfig(name=args.adapter, model=args.model, base_url=args.base_url,
                               api_key_env=args.api_key_env)
    try:
        report = run_conformance(run_dir=args.run or None, config=config,
                                 concurrency=args.concurrency)
    except ExecutorError as exc:
        print(f"wrapper executor error: {exc}", file=sys.stderr)
        return 4
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 3


def _plan_judgment_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import planner
    run = Path(args.run).expanduser().resolve()
    try:
        planned = planner.plan_judgment(run, context_budget_tokens=args.context_budget)
    except planner.PlannerError as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
    for task in planned:
        detail = f", lens={task.lens_id}, shard={task.shard}" if task.lens_id else ""
        shard_note = f", {len(task.packet_ids)} packet(s)" if len(task.packet_ids) > 1 else ""
        status = "" if task.created else " (already planned, no-op)"
        print(f"{task.task_id} ({task.task_type}{detail}): "
              f"~{task.estimated_tokens} tokens{shard_note}{status}")
    print(f"planned {len(planned)} task(s), "
          f"{sum(1 for task in planned if task.created)} newly created")
    return 0


def _plan_dedup_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import planner
    run = Path(args.run).expanduser().resolve()
    try:
        task = planner.plan_dedup(run, context_budget_tokens=args.context_budget)
    except planner.PlannerError as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
    status = "" if task.created else " (already planned, no-op)"
    print(f"{task.task_id}: ~{task.estimated_tokens} tokens, "
          f"{len(task.packet_ids)} packet(s){status}")
    return 0


def _assemble_findings_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import assemble
    run = Path(args.run).expanduser().resolve()
    try:
        result = assemble.assemble(run)
    except assemble.AssembleError as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
    out_path = Path(args.out).expanduser().resolve()
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", "utf-8")
    print(f"assembled {len(result['findings'])} finding(s)")
    print(f"wrote {out_path}")
    return 0


def _write_module_map_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import formation
    run = Path(args.run).expanduser().resolve()
    try:
        out_path = formation.write(run, out=args.out or None)
    except formation.FormationWriterError as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out_path}")
    return 0


def _fetch_selections_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import selection
    run = Path(args.run).expanduser().resolve()
    try:
        out_path = selection.fetch(run, args.task, out=args.out or None)
    except selection.SelectionFetchError as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out_path}")
    return 0


def _plan_lens_finalize_cmd(args: argparse.Namespace) -> int:
    from .orchestrator import planner
    run = Path(args.run).expanduser().resolve()
    try:
        task = planner.plan_lens_finalize(
            run, args.lens, context_budget_tokens=args.context_budget)
    except planner.PlannerError as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
    status = "" if task.created else " (already planned, no-op)"
    print(f"{task.task_id}: ~{task.estimated_tokens} tokens, "
          f"{len(task.packet_ids)} packet(s){status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.allow_hosts:
            # Registry guards read this when deciding whether a dependency
            # host outside the default registries is explicitly approved.
            os.environ["PROJECT_ANALYSIS_ALLOW_HOSTS"] = args.allow_hosts
        if args.command == "new-run":
            return _new_run(args)
        if args.command in ("mark-stage", "rollback", "status", "accept"):
            return _lifecycle_cmd(args)
        if args.command == "system-model":
            return _system_model(args)
        if args.command == "prepare-overview":
            return prepare_deterministic_evidence(args)
        if args.command.startswith("module-"):
            from .module_drill import commands
            return commands.run(args)
        if args.command == "finalize-module-map":
            return _finalize_module_map(args)
        if args.command == "finalize-findings":
            return _finalize_findings(args)
        if args.command == "rekey-findings":
            return _rekey_findings(args)
        if args.command == "audit-overview":
            return _audit_overview(args)
        if args.command == "export":
            return _export(args)
        if args.command == "compare-runs":
            return _compare_runs(args)
        if args.command == "next-task":
            return _next_task(args)
        if args.command == "submit-task":
            return _submit_task(args)
        if args.command == "run-executor":
            return _run_executor_cmd(args)
        if args.command == "executor-conformance":
            return _executor_conformance_cmd(args)
        if args.command == "plan-judgment":
            return _plan_judgment_cmd(args)
        if args.command == "plan-dedup":
            return _plan_dedup_cmd(args)
        if args.command == "assemble-findings":
            return _assemble_findings_cmd(args)
        if args.command == "write-module-map":
            return _write_module_map_cmd(args)
        if args.command == "fetch-selections":
            return _fetch_selections_cmd(args)
        if args.command == "plan-lens-finalize":
            return _plan_lens_finalize_cmd(args)
        if args.command == "plan-reports":
            return _plan_reports_cmd(args)
        if args.command == "assemble-reports":
            return _assemble_reports_cmd(args)
        if args.command == "report-floors":
            return _report_floors_cmd(args)
        if args.command == "run-pipeline":
            return _run_pipeline_cmd(args)
        if not args.out:
            print("wrapper input error: --out is required for this command",
                  file=sys.stderr)
            return 2
        if args.command == "discover":
            return _discover(args)
        if not args.targets:
            print("wrapper input error: --targets is required for run/sweep",
                  file=sys.stderr)
            return 2
        spec = TargetSpec.load(args.targets)
        # $WORKSPACE relativization needs a workspace root; derive it from the
        # targets so sanitized artifacts never embed absolute machine paths
        # even when the workspace lives outside $HOME (review P2-11).
        if spec.repos and not os.environ.get("WORKSPACE_ROOT"):
            os.environ["WORKSPACE_ROOT"] = os.path.commonpath(
                [str(Path(r.path).expanduser().resolve()) for r in spec.repos]
            )
        # Post-discovery stages layer into the existing run dir (discovery made
        # it); run/sweep still demand a fresh signals dir. A per-stage marker
        # refuses re-running a stage over its own prior evidence.
        if args.command in ("callgraph", "dependency-map"):
            marker = ("callgraph-coverage.json" if args.command == "callgraph"
                      else "imports/depmap-coverage.json")
            out = use_existing_run_directory(args.out, spec.repos, stage_marker=marker)
            args.scan_date = args.scan_date or date.today().isoformat()
            return (_callgraph if args.command == "callgraph" else _depmap)(
                args, spec, out)
        args.scan_date = args.scan_date or date.today().isoformat()
        out = prepare_output_directory(args.out, spec.repos)
        from . import identity
        identities = identity.load(Path(args.targets).expanduser().resolve().parent)
        results = (_run_one(args, spec, out, identities)
                   if args.command == "run" else _sweep(args, spec, out, identities))
        _record_summary(out, results)
        for item in results:
            suffix = f": {item.reason}" if item.reason else ""
            print(f"{item.repository_ref} {item.tool}: {item.status.value}{suffix}")
        return wrapper_exit_code([x.status for x in results])
    except WrapperSafetyError as exc:
        # Safety refusals are not input mistakes: distinct message + exit code.
        print(f"wrapper safety refusal: {exc}", file=sys.stderr)
        return 4
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("wrapper interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
