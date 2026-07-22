"""Command-line entry point for per-tool execution and full TargetSpec sweeps."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from .executor import (SignalResult, WrapperSafetyError,
                       prepare_output_directory, run_tool,
                       use_existing_run_directory)
from .registry import git_history, jscpd_multi, local_tools, network_tools, tool_for
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
            repo_id = (doc.get("repos") or [{}])[0].get("repo_id", "")
            key = (doc.get("tool", ""), repo_id, doc.get("status", ""))
            manifests.setdefault(key, []).append(path.name)
        except (OSError, ValueError, KeyError, IndexError):
            continue
    payload = {
        "aggregate_status": aggregate([x.status for x in results]).value,
        "signals": [
            {"tool": x.tool, "repo_id": x.repo_id, "status": x.status.value,
             "reason": x.reason,
             "view": x.view_path.name if x.view_path else "",
             "manifest": (x.manifest_path.name if x.manifest_path else
                          manifests.get(
                              (x.tool, x.repo_id, x.status.value), [""])[0])}
            for x in sorted(results, key=lambda r: (r.repo_id, r.tool))
        ],
    }
    (out / "run-summary.json").write_text(
        sanitize_text(json.dumps(payload, indent=2, sort_keys=True) + "\n"), "utf-8"
    )


def _run_one(args: argparse.Namespace, spec: TargetSpec, out: Path) -> list[SignalResult]:
    target = spec.repo(args.repo)
    definition = git_history(target, args.since, args.coupling_sample_cap) \
        if args.tool == "git-history" else tool_for(args.tool, target)
    return [run_tool(
        definition, target, out, args.scan_date,
        allow_network=args.include_network,
    )]


def _sweep(args: argparse.Namespace, spec: TargetSpec, out: Path) -> list[SignalResult]:
    results: list[SignalResult] = []
    repos = sorted(spec.repos, key=lambda r: r.repo_id)
    for target in repos:
        definitions = local_tools(target)
        # Respect the CLI's reproducible history window instead of the registry default.
        definitions = [git_history(target, args.since, args.coupling_sample_cap)
                       if d.name == "git-history" else d for d in definitions]
        # Always materialize applicable network lanes. The executor records
        # them as SKIPPED without authorization, so absence cannot masquerade
        # as a clean/covered result.
        definitions += network_tools(target)
        for definition in definitions:
            results.append(run_tool(
                definition, target, out, args.scan_date,
                allow_network=args.include_network,
            ))
    # Cross-repo duplication runs per LANGUAGE FAMILY: Phase 0 proved jscpd is
    # same-language only (zero JS<->Go clones), so cross-family runs only burn
    # the sweep's largest timeout for known-noise output.
    for family, members in sorted(_family_groups(repos).items()):
        if len(members) < 2:
            continue
        definition = jscpd_multi(members)
        results.append(run_tool(
            definition, members[0], out, args.scan_date,
            additional_targets=members[1:], signal_id=f"jscpd-cross-{family}",
            allow_network=args.include_network,
        ))
    return results


def _family_groups(repos: list) -> dict[str, list]:
    """Group repos by language family for same-language cross-repo runs."""
    groups: dict[str, list] = {}
    for target in repos:
        stacks = {x.lower() for x in target.stacks}
        if stacks & {"js", "ts", "tsx", "javascript", "typescript"} or \
                (Path(target.path) / "package.json").is_file():
            family = "node"
        elif "go" in stacks or (Path(target.path) / "go.mod").is_file():
            family = "go"
        else:
            family = "other"
        groups.setdefault(family, []).append(target)
    return groups


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="project-analysis-wrapper")
    result.add_argument("--targets", help="TargetSpec JSON from discovery "
                                          "(required for run/sweep)")
    result.add_argument("--out",
                        help="output directory (signals dir for run/sweep; "
                             "run dir for discover)")
    result.add_argument("--scan-date", default=date.today().isoformat())
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
    sub = result.add_subparsers(dest="command", required=True)
    one = sub.add_parser("run", help="run one tool against one repo")
    one.add_argument("--tool", required=True)
    one.add_argument("--repo", required=True, help="stable repo_id from TargetSpec")
    one.add_argument("--include-network", action="store_true",
                     help="explicitly authorize a network-capable tool")
    sweep = sub.add_parser("sweep", help="run all applicable validated tools")
    sweep.add_argument("--include-network", action="store_true")
    cg = sub.add_parser(
        "callgraph", help="extract function/method call edges (57B-30) into "
                          "<out>/callgraph/<repo_id>.jsonl + callgraph-coverage.json")
    cg.add_argument("--include-network", action="store_true",
                    help="authorize the Go module-cache warm for a cold cache "
                         "(offline-first; without it a cold cache fails closed)")
    dm = sub.add_parser(
        "dependency-map",
        help="produce per-repo import maps into <out>/imports/ "
             "(<repo_id>.depcruise.json for JS/TS, <repo_id>.golist.json for Go); "
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
        "--run-id", default="", metavar="LABEL",
        help="optional readable run label; the wrapper appends the 6-character "
             "input digest and a collision suffix when needed",
    )
    new_run.add_argument("--exclude", default="")
    new_run.add_argument("--analyzer-root", default="",
                         help="override the self-excluded analyzer checkout "
                              "root (default: resolved from the package)")
    drill = sub.add_parser(
        "new-drilldown", help="mint a drill-down run from a completed overview "
                              "run (--from-run → current pointer → refuse)")
    drill.add_argument("--skill-root", required=True)
    drill.add_argument("--module", required=True, help="module-id from the map")
    drill.add_argument("--from-run", default="",
                       help="explicit source overview run id")
    drill.add_argument("--language", default="",
                       help="report language (default: the source run's)")
    drill.add_argument("--project", default="",
                       help="project-id (needed only when output/ has several)")
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
    return result


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
    """<skill-root>/output/<project-id>/overview/<run-id> -> <skill-root>/state/<project-id>."""
    project_dir = run_dir.parent.parent
    return project_dir.parent.parent / "state" / project_dir.name


def _new_run(args: argparse.Namespace) -> int:
    from . import lifecycle
    from .discovery import emit
    exclude = [x.strip() for x in args.exclude.split(",") if x.strip()]
    spec, report = emit.discover(
        args.workspace, exclude_names=exclude,
        analyzer_root=args.analyzer_root or None)
    overview_root = (Path(args.skill_root).expanduser().resolve()
                     / "output" / report["project_id"] / "overview")
    run_id = lifecycle.mint_run_id(
        [f"{r.repo_id}:{r.git.head}:{r.git.dirty_detail}" for r in spec.repos],
        args.language, label=args.run_id,
        exists=lambda rid: (overview_root / rid).exists())
    run_dir = overview_root / run_id
    emit.write_stage1(run_dir, spec, report)
    state = lifecycle.RunState.create(
        run_id, report["project_id"], spec, language=args.language,
        analysis_identity={"wrapper": "project-analysis-wrapper"})
    state.mark("discovery")
    state.save(run_dir)
    print(f"run: {run_dir}")
    print(f"inspection_only: {state.inspection_only}")
    print(f"next stage: {state.next_stage()}")
    return 0


def _new_drilldown(args: argparse.Namespace) -> int:
    from . import lifecycle
    root = Path(args.skill_root).expanduser().resolve()
    projects = sorted(p for p in (root / "output").iterdir() if p.is_dir()) \
        if (root / "output").is_dir() else []
    if args.project:
        projects = [p for p in projects if p.name == args.project]
    if len(projects) != 1:
        print("wrapper input error: pass --project "
              f"(found {len(projects)} project dirs under output/)", file=sys.stderr)
        return 2
    project_dir = projects[0]
    overview_root = project_dir / "overview"

    def completed_runs() -> list[str]:
        found = []
        for run_dir in sorted(overview_root.iterdir()) if overview_root.is_dir() else []:
            try:
                if lifecycle.RunState.load(run_dir).next_stage() == "":
                    found.append(run_dir.name)
            except (OSError, ValueError, KeyError):
                continue
        return found

    # Resolution: --from-run → current pointer → refuse (never implicit).
    source_id = args.from_run
    if not source_id:
        source_id = lifecycle.Pointers(root / "state" / project_dir.name).read().get("current")
    if not source_id:
        print("drill-down refused: no --from-run given and no run has been "
              "ACCEPTED as `current`. Completed overview runs: "
              + (", ".join(completed_runs()) or "(none)")
              + ". Accept one (`accept --run <dir>`) or pass --from-run explicitly.",
              file=sys.stderr)
        return 2
    source_dir = overview_root / source_id
    if not (source_dir / lifecycle.RunState.FILENAME).is_file():
        print(f"wrapper input error: overview run {source_id!r} not found; "
              f"completed runs: {', '.join(completed_runs()) or '(none)'}",
              file=sys.stderr)
        return 2
    source = lifecycle.RunState.load(source_dir)
    if source.next_stage() != "":
        print(f"drill-down refused: source run {source_id} is incomplete "
              f"(next stage: {source.next_stage()})", file=sys.stderr)
        return 2
    stale = source.staleness()
    if stale:
        print("drill-down refused: source overview run is STALE — run a new "
              "overview. Moved repos:", file=sys.stderr)
        for line in stale:
            print(f"  {line}", file=sys.stderr)
        return 5

    drill_root = project_dir / "drilldown"
    heads = [f"{r['repo_id']}:{r['head']}:{r['dirty_detail']}" for r in source.provenance]
    run_id = lifecycle.mint_run_id(
        heads + [f"module:{args.module}"], args.language or source.language,
        exists=lambda rid: (drill_root / rid).exists())
    run_dir = drill_root / run_id
    run_dir.mkdir(parents=True)
    state = lifecycle.RunState.create_drilldown(
        run_id, source, args.module, language=args.language or None)
    state.mark("resolve")
    state.save(run_dir)
    (run_dir / "source_overview_run").write_text(
        f"{source.run_id}\n{source_dir}\n", "utf-8")
    print(f"run: {run_dir}")
    print(f"module: {args.module} | language: {state.language} | "
          f"source: {source.run_id} (inspection_only: {state.inspection_only})")
    print(f"next stage: {state.next_stage()}")
    return 0


def _callgraph(args: argparse.Namespace, spec: TargetSpec, out: Path) -> int:
    from .callgraph import emit as cg_emit
    report = cg_emit.run_callgraph(
        spec, out, args.scan_date, allow_network=args.include_network)
    for cov in sorted(report.repos, key=lambda c: (c.repo_id, c.lang)):
        suffix = f": {cov.reason}" if cov.reason else ""
        print(f"{cov.repo_id} [{cov.lang}] {cov.status} "
              f"(edges={cov.edges_emitted}, resolved={cov.call_sites.resolved})"
              f"{suffix}")
    if not report.repos:
        print("no Go/JS/TS repositories in the TargetSpec — nothing to analyze")
        return 0
    return 3 if cg_emit.aggregate_status(report) is Status.FAILED else 0


def _depmap(args: argparse.Namespace, spec: TargetSpec, out: Path) -> int:
    from .depmap import emit as dm_emit
    report = dm_emit.run_depmap(
        spec, out, args.scan_date, allow_network=args.include_network)
    for cov in sorted(report.repos, key=lambda c: (c.repo_id, c.lane)):
        suffix = f": {cov.reason}" if cov.reason else ""
        print(f"{cov.repo_id} [{cov.lane}] {cov.status} "
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


def _prepare_overview(args: argparse.Namespace) -> int:
    """Authoritative deterministic overview preparation (57B-47).

    Existing canonical stage outputs are validated and reused; missing stages
    run exactly once.  Producer paths never depend on model effort.
    """
    from . import (capabilities, coverage_render, lifecycle, module_map,
                   overview_audit, synthesis_input, workspace_metrics)
    from .callgraph import emit as cg_emit
    from .depmap import emit as dm_emit
    from .system_model.assemble import assemble, dump

    run = Path(args.run).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    use_existing_run_directory(run, spec.repos)
    state = lifecycle.RunState.load(run)
    stale = state.staleness()
    if stale:
        raise ValueError("run is stale; mint a new run: " + "; ".join(stale))
    if spec.repos and not os.environ.get("WORKSPACE_ROOT"):
        os.environ["WORKSPACE_ROOT"] = os.path.commonpath(
            [str(Path(repo.path).expanduser().resolve()) for repo in spec.repos])

    signals = run / "signals"
    signal_summary = signals / "run-summary.json"
    if signal_summary.is_file():
        _load_object(signal_summary)
        print("signals: reused canonical run-summary.json")
    else:
        if signals.exists():
            raise ValueError("signals/ exists without run-summary.json; refuse partial reuse")
        out = prepare_output_directory(signals, spec.repos)
        results = _sweep(args, spec, out)
        _record_summary(out, results)
        print(f"signals: wrote {len(results)} result(s)")

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

    # These producers read the same immutable targets but own disjoint output
    # trees, so running them together is safe and avoids a second cold traversal.
    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        if need_callgraph:
            jobs["callgraph"] = pool.submit(
                cg_emit.run_callgraph, spec, run, args.scan_date,
                allow_network=args.include_network)
        if need_depmap:
            jobs["dependency-map"] = pool.submit(
                dm_emit.run_depmap, spec, run, args.scan_date,
                allow_network=args.include_network)
        completed = {name: future.result() for name, future in jobs.items()}
    for name in ("callgraph", "dependency-map"):
        if name in completed:
            print(f"{name}: wrote {len(completed[name].repos)} lane result(s)")

    model = assemble(run)
    dump(model, run)
    model_doc = model.to_dict()
    module_map.write_candidates(run, model_doc)
    capabilities_path = capabilities.write(run)
    coverage_path = coverage_render.write(run)
    metrics_path = workspace_metrics.write(run)
    packet_path = synthesis_input.write(run)
    audit_path = overview_audit.write(run)
    audit = _load_object(audit_path)
    capability_doc = _load_object(capabilities_path)
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


def _finalize_module_map(args: argparse.Namespace) -> int:
    from . import module_map, module_render, overview_audit, synthesis_input
    from .system_model.assemble import assemble, dump
    run = Path(args.run).expanduser().resolve()
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
    technical, pm = findings.write(args.run)
    count = len(findings.validate(args.run).get("findings", []))
    print(f"findings: {count} validated atomic finding(s)")
    print(f"wrote {technical}")
    print(f"wrote {pm}")
    return 0


def _audit_overview(args: argparse.Namespace) -> int:
    from . import overview_audit
    run = Path(args.run).expanduser().resolve()
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
        print("module drill-down: "
              + ("available" if detail.drilldown_available else "stub (Phase 2)"))
    return 0


def _lifecycle_cmd(args: argparse.Namespace) -> int:
    from . import lifecycle
    run_dir = Path(args.run).expanduser().resolve()
    state = lifecycle.RunState.load(run_dir)
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


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.allow_hosts:
            # Registry guards read this when deciding whether a dependency
            # host outside the default registries is explicitly approved.
            os.environ["PROJECT_ANALYSIS_ALLOW_HOSTS"] = args.allow_hosts
        if args.command == "new-run":
            return _new_run(args)
        if args.command == "new-drilldown":
            return _new_drilldown(args)
        if args.command in ("mark-stage", "rollback", "status", "accept"):
            return _lifecycle_cmd(args)
        if args.command == "system-model":
            return _system_model(args)
        if args.command == "prepare-overview":
            return _prepare_overview(args)
        if args.command == "finalize-module-map":
            return _finalize_module_map(args)
        if args.command == "finalize-findings":
            return _finalize_findings(args)
        if args.command == "audit-overview":
            return _audit_overview(args)
        if args.command == "export":
            return _export(args)
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
            return (_callgraph if args.command == "callgraph" else _depmap)(
                args, spec, out)
        out = prepare_output_directory(args.out, spec.repos)
        results = _run_one(args, spec, out) if args.command == "run" else _sweep(args, spec, out)
        _record_summary(out, results)
        for item in results:
            suffix = f": {item.reason}" if item.reason else ""
            print(f"{item.repo_id} {item.tool}: {item.status.value}{suffix}")
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
