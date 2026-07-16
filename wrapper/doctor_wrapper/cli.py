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
    definition = git_history(target, args.since) if args.tool == "git-history" \
        else tool_for(args.tool, target)
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
        definitions = [git_history(target, args.since) if d.name == "git-history" else d
                       for d in definitions]
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
    result.add_argument("--targets", required=True, help="TargetSpec JSON from discovery")
    result.add_argument("--out", required=True, help="run signals directory")
    result.add_argument("--scan-date", default=date.today().isoformat())
    result.add_argument(
        "--since", default=None,
        help="history window start (default: 24 months before today; the value "
             "actually used is recorded in every git-history manifest)",
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
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.allow_hosts:
            # Registry guards read this when deciding whether a dependency
            # host outside the default registries is explicitly approved.
            os.environ["PROJECT_DOCTOR_ALLOW_HOSTS"] = args.allow_hosts
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
