"""Command-line entry point for per-tool execution and full TargetSpec sweeps."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from .executor import SignalResult, WrapperSafetyError, prepare_output_directory, run_tool
from .registry import git_history, jscpd_multi, local_tools, network_tools, tool_for
from .sanitize import sanitize_text
from .status import Status, aggregate, wrapper_exit_code
from .targetspec import TargetSpec


def _record_summary(out: Path, results: list[SignalResult]) -> None:
    payload = {
        "aggregate_status": aggregate([x.status for x in results]).value,
        "signals": [
            {"tool": x.tool, "repo_id": x.repo_id, "status": x.status.value,
             "reason": x.reason,
             "view": x.view_path.name if x.view_path else ""}
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
    result = argparse.ArgumentParser(prog="project-doctor-wrapper")
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
    disc = sub.add_parser(
        "discover", help="produce the stage-1 run checkpoint "
                         "(targets.json + discovery-report.json)")
    disc.add_argument("--workspace", required=True,
                      help="target workspace root to inventory")
    disc.add_argument("--exclude", default="",
                      help="comma-separated repo basenames to exclude "
                           "(disclosed in the report)")
    new_run = sub.add_parser(
        "new-run", help="mint a run dir under <skill-root>/output and run "
                        "discovery into it (stage 1 done)")
    new_run.add_argument("--workspace", required=True)
    new_run.add_argument("--skill-root", required=True,
                         help="skill base directory (owns state/ and output/)")
    new_run.add_argument("--language", default="zh-CN", choices=["en", "zh-CN"])
    new_run.add_argument("--exclude", default="")
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
    spec, report = emit.discover(args.workspace, exclude_names=exclude)
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
    spec, report = emit.discover(args.workspace, exclude_names=exclude)
    overview_root = (Path(args.skill_root).expanduser().resolve()
                     / "output" / report["project_id"] / "overview")
    run_id = lifecycle.mint_run_id(
        [f"{r.repo_id}:{r.git.head}:{r.git.dirty_detail}" for r in spec.repos],
        args.language, exists=lambda rid: (overview_root / rid).exists())
    run_dir = overview_root / run_id
    emit.write_stage1(run_dir, spec, report)
    state = lifecycle.RunState.create(
        run_id, report["project_id"], spec, language=args.language,
        analysis_identity={"wrapper": "project-doctor-wrapper"})
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
            os.environ["PROJECT_DOCTOR_ALLOW_HOSTS"] = args.allow_hosts
        if args.command == "new-run":
            return _new_run(args)
        if args.command == "new-drilldown":
            return _new_drilldown(args)
        if args.command in ("mark-stage", "rollback", "status", "accept"):
            return _lifecycle_cmd(args)
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
