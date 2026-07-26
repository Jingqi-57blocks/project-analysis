"""Command-line entry point for per-tool execution and full TargetSpec sweeps."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from . import compat, paths
from .executor import (SignalResult, WrapperSafetyError,
                       prepare_output_directory, run_tool,
                       use_existing_run_directory)
from .registry import (PROVIDER_OWNED_SIGNAL_TOOLS, git_history, jscpd_multi,
                       local_tools, network_tools, tool_for)
from .sanitize import sanitize_text
from .status import Status, aggregate, wrapper_exit_code
from .targetspec import TargetSpec

# 57B-95 review FIX 1: every subcommand that takes an EXISTING run via
# `--run` and advances/mutates it further (as opposed to `status`/`accept`,
# which take `--run` too but never write into the run directory itself --
# see the call site in `main()`). Kept as one explicit list rather than N
# scattered call sites so the choke point in `main()` covers all of them and
# a future command is one line to add here, not one easy-to-forget call
# inline in its own handler.
_RUN_ARG_ADVANCE_COMMANDS = frozenset({
    "mark-stage", "rollback", "system-model", "prepare-overview",
    "finalize-module-map", "finalize-findings", "audit-overview",
})


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
        "schema_version": "2.0.0",
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


def _sweep(args: argparse.Namespace, spec: TargetSpec, out: Path,
           identities, *,
           exclude_tool_names: frozenset[str] = frozenset()) -> list[SignalResult]:
    """``exclude_tool_names`` (57B-82 A2, additive, default empty — every
    pre-existing call site is unaffected): ``cli._prepare_overview`` passes
    ``PROVIDER_OWNED_SIGNAL_TOOLS`` so git-history/osv-scanner/outdated run
    exactly once, through their own capability providers, instead of also
    running here. The standalone ``run``/``sweep`` CLI subcommands are
    user-facing debug paths and never pass this — they keep executing every
    tool directly, unchanged."""
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
        if exclude_tool_names:
            definitions = [d for d in definitions if d.name not in exclude_tool_names]
        for definition in definitions:
            results.append(run_tool(
                definition, target, out, args.scan_date,
                identities.repository(target.repo_id),
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
            identities.repository(members[0].repo_id),
            additional_targets=members[1:], signal_id=f"jscpd-cross-{family}",
            additional_repository_identities=[
                identities.repository(item.repo_id) for item in members[1:]],
            allow_network=args.include_network,
        ))
    return results


def _family_groups(repos: list) -> dict[str, list]:
    """Group repos by language family for same-language cross-repo runs."""
    from .profiles import selection  # lazy: see registry.py's note on this cycle
    groups: dict[str, list] = {}
    for target in repos:
        groups.setdefault(selection.family(target), []).append(target)
    return groups


def _skill_version() -> str:
    """Single source of the skill version: the VERSION file at the skill root.

    Resolved from this module's own location (…/wrapper/analysis_wrapper/cli.py
    -> skill root is parents[2]) so it works from any working directory and under
    any host that invokes the wrapper by absolute path.
    """
    try:
        text = (Path(__file__).resolve().parents[2] / "VERSION").read_text("utf-8")
        return text.strip() or "unknown"
    except OSError:
        return "unknown"


def _version_string() -> str:
    from . import __version__ as pkg
    return (f"Project Analysis skill {_skill_version()} "
            f"(analysis-wrapper package {pkg})")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="project-analysis-wrapper")
    result.add_argument("--version", action="version", version=_version_string(),
                        help="print the skill version and exit")
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
        "new-run", help="mint a run dir under the data root's output/ tree "
                        "(see `project-analysis-wrapper migrate` for legacy "
                        "--skill-root layouts) and run discovery into it "
                        "(stage 1 done)")
    new_run.add_argument("--workspace", required=True)
    new_run.add_argument(
        "--skill-root", default="",
        help="deprecated, ignored for data placement: run output/state/exported "
             "always live under the data root (see PROJECT_ANALYSIS_HOME / "
             "paths.data_root(), resolved automatically). Kept only for CLI "
             "back-compat with older invocations; no longer required.")
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
    drill = sub.add_parser(
        "new-drilldown", help="mint a drill-down run from a completed overview "
                              "run (--from-run → current pointer → refuse)")
    drill.add_argument(
        "--skill-root", default="",
        help="deprecated for data placement (see `new-run --skill-root`); when "
             "given, used ONLY as an override for the analyzer's own code-root "
             "identity, never for where output/state/exported live")
    drill.add_argument("--module", required=True, help="module-id from the map")
    drill.add_argument("--from-run", default="",
                       help="explicit source overview run id")
    drill.add_argument("--language", default="", choices=["", "en", "zh-CN"],
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
             "<data-root>/exported/{project}-analysis/{run-id}/{format}/.")
    exp.add_argument("--run", required=True, help="completed run directory")
    exp.add_argument("--format", nargs="?", const="__list__", default="html",
                     help="output format (default: html); pass --format with no "
                          "value to list available formats")
    exp.add_argument(
        "--skill-root", default="",
        help="deprecated, ignored: the export destination is always under the "
             "data root (see PROJECT_ANALYSIS_HOME / paths.data_root()); kept "
             "only for CLI back-compat. Use --out for an explicit override.")
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
        help="optional path to write the full JSON parity report")
    doctor = sub.add_parser(
        "doctor",
        help="offline preflight/readiness check: read tools/manifest.json, probe "
             "installed tools, and (with --workspace) sniff which analysis lanes "
             "apply, so absent Go tooling on a pure-JS target reports "
             "not-applicable rather than missing. Exit codes: 0 ok (including "
             "disclosed reduced coverage from absent optional tools; a data "
             "root that cannot be resolved is also reported in-band at exit "
             "0, not treated as fatal), 2 invalid invocation, 3 environment "
             "incomplete (Python < 3.11), 4 installation corrupt (manifest "
             "missing/malformed or unreadable code assets), 1 internal "
             "failure. Never installs, never writes "
             "to the target, never touches the network beyond probing local "
             "binaries for --version.")
    doctor.add_argument(
        "--workspace", default="",
        help="target workspace to sniff lane applicability for (optional; "
             "without it every lane is reported applicable-unknown)")
    doctor.add_argument("--json", action="store_true",
                        help="machine-readable structured output")
    from . import setup as setup_mod
    setup_mod.add_subparser(sub)
    migrate = sub.add_parser(
        "migrate",
        help="one-time move of a legacy --skill-root's output/state/exported "
             "into the current data root (idempotent; never merges namespaces; "
             "never touches generated runtimes — those are always rebuilt fresh)")
    migrate.add_argument(
        "--legacy-skill-root", required=True,
        help="the OLD skill-root directory whose output/state/exported "
             "subdirectories should be migrated")
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
    """Map a readable project output key to its matching state directory.

    Precedence (57B-89 Phase 2 review fix -- unifies the two state-root
    resolutions that used to disagree post-relocation): prefer the CURRENT
    data root (``paths.state_root()``) -- this is where ``new-drilldown``
    reads the ``current`` pointer from directly, and where every NEW write
    must land. The legacy location (derived from ``run_dir``'s own ancestor
    tree, matching a pre-relocation ``--skill-root`` layout) is honored only
    while the current project state dir has not been created yet AND the
    legacy one already exists -- i.e. only for the first read after a
    relocation, so an already-recorded acceptance does not silently look like
    "no run ACCEPTED". As soon as anything writes through this helper the
    current dir exists, and every later call (read or write) resolves to it.
    """
    project_dir = run_dir.parent.parent
    legacy = project_dir.parent.parent / "state" / project_dir.name
    current = paths.state_root() / project_dir.name
    if legacy != current and legacy.is_dir() and not current.is_dir():
        return legacy
    return current


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
    # Fail closed BEFORE anything is written if the data root would land
    # inside the analyzed workspace (57B-89 Phase 2 review fix): the target
    # tree must stay read-only, never host wrapper output/state/exported.
    workspace_root = Path(report["workspace_root"]).expanduser().resolve()
    paths.data_root(target=workspace_root)
    output_root = paths.output_root()
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
            degraded_runtime_notice=getattr(args, "degraded_runtime_notice", ""),
        ),
    )
    state.save(run_dir)
    print(f"run: {run_dir}")
    print(f"inspection_only: {state.inspection_only}")
    print(f"next stage: {state.next_stage()}")
    return 0


def _new_drilldown(args: argparse.Namespace) -> int:
    from . import lifecycle, run_provenance
    # `--skill-root`, when given, is a CODE-root override only (analyzer
    # identity fallback below) — it never determines where output/state live;
    # those always resolve through the data root (57B-89 Phase 2).
    code_root = (Path(args.skill_root).expanduser().resolve() if args.skill_root
                else paths.skill_root())
    output_root = paths.output_root()
    projects = sorted(p for p in output_root.iterdir()
                      if p.is_dir() and not p.name.startswith(".")) \
        if output_root.is_dir() else []
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
        source_id = lifecycle.Pointers(
            paths.state_root() / project_dir.name).read().get("current")
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
    source_provenance = run_provenance.load(source_dir)
    source_spec = TargetSpec.load(source_dir / "targets.json")
    stale = source.staleness()
    stale.extend(run_provenance.target_source_staleness(
        source_provenance, source_spec))
    if stale:
        print("drill-down refused: source overview run is STALE — run a new "
              "overview. Moved repos:", file=sys.stderr)
        for line in stale:
            print(f"  {line}", file=sys.stderr)
        return 5

    # Fail closed BEFORE anything is written if the data root would land
    # inside the source run's own workspace (57B-89 Phase 2 review fix):
    # new-drilldown does not take --workspace, but the source run's own
    # targets.json names it.
    if source_spec.repos:
        workspace_root = Path(os.path.commonpath(
            [str(Path(r.path).expanduser().resolve()) for r in source_spec.repos]))
        paths.data_root(target=workspace_root)

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
    source_spec.save(run_dir / "targets.json")
    generation = source_provenance.get("generation", {})
    analyzer = source_provenance.get("analyzer", {})
    run_provenance.write(
        run_dir,
        run_provenance.create_document(
            source_spec,
            analyzer_root=analyzer.get("root") or code_root,
            language=state.language,
            model=generation.get("model", ""),
            effort=generation.get("effort", ""),
            analyzed_at=state.analyzed_at,
            degraded_runtime_notice=getattr(args, "degraded_runtime_notice", ""),
        ),
    )
    (run_dir / "source_overview_run").write_text(
        f"{source.run_id}\n{source_dir}\n", "utf-8")
    print(f"run: {run_dir}")
    print(f"module: {args.module} | language: {state.language} | "
          f"source: {source.run_id} (inspection_only: {state.inspection_only})")
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
    from . import (capabilities, coverage_render, lifecycle, module_map,
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
    technical, pm = findings.write(args.run)
    count = len(findings.validate(args.run).get("findings", []))
    print(f"findings: {count} validated atomic finding(s)")
    print(f"wrote {technical}")
    print(f"wrote {pm}")
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
            # `--skill-root` (if the caller still passes it) is intentionally
            # ignored here — exported/ always lives under the data root
            # (57B-89 Phase 2), never wherever the code checkout happens to be.
            result = export_pkg.export(
                run, args.format, data_root=paths.data_root())
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


def _migrate(args: argparse.Namespace) -> int:
    report = paths.migrate_legacy(args.legacy_skill_root)
    # Non-zero on failure (57B-89 Phase 2 review fix) so scripted migration
    # can detect it instead of reading 0 unconditionally.
    exit_code = 0
    try:
        print(f"data root: {paths.data_root()}")
    except (OSError, ValueError) as exc:
        print(f"data root: unavailable ({exc})", file=sys.stderr)
        exit_code = 1
    for name in report["moved"]:
        print(f"moved: {name}")
    for name in report["skipped_absent"]:
        print(f"nothing to migrate: {name}")
    for warning in report["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
        if "could not prepare the data root" in warning:
            exit_code = 1
    return exit_code


def _compare_runs(args: argparse.Namespace) -> int:
    from . import parity
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


def _doctor(args: argparse.Namespace) -> int:
    from . import doctor as doctor_mod
    # doctor.run() maps every failure mode to its own documented exit code
    # internally and never raises; it must NOT be routed through this
    # module's blanket (OSError, ValueError, ...) -> 2 handler below, which
    # would collapse the distinct environment-incomplete/installation-corrupt
    # codes doctor is specifically required to keep separate.
    return doctor_mod.run(args.workspace or None, as_json=args.json)


def _setup(args: argparse.Namespace) -> int:
    from . import setup as setup_mod
    # setup.run() maps every failure mode to its own documented exit code
    # internally and never raises -- same rationale as _doctor above: this
    # must not be routed through the blanket exception handler in main().
    return setup_mod.main_from_args(args)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.allow_hosts:
            # Registry guards read this when deciding whether a dependency
            # host outside the default registries is explicitly approved.
            os.environ["PROJECT_ANALYSIS_ALLOW_HOSTS"] = args.allow_hosts
        # 57B-95: refuse a real analysis/resume command outright when the
        # installed runtime has drifted from this code's manifest pins;
        # read-only/informational commands (doctor, migrate, ...) stay exempt
        # so a user can always diagnose and fix the problem. The returned
        # notice is non-empty only when ACCEPT_DEGRADED_RUNTIME_ENV actually
        # suppressed a detected drift this invocation; threading it onto
        # `args` lets a run-minting command (new-run/new-drilldown) stamp it
        # into the fresh run's own provenance (FIX 5).
        args.degraded_runtime_notice = compat.guard_entry(args.command)
        # 57B-95 review FIX 1: `guard_entry` above only ever checks the
        # installed RUNTIME against this code's manifest pins -- it has no
        # notion of which run directory a gated command is about to advance,
        # so it can never catch "resuming an old-schema run under new code
        # would mix two artifact contracts in one run directory". Every
        # command here that reads an EXISTING run's artifacts and writes
        # MORE into that same run directory is covered by `compat.guard_run`
        # at this single choke point (derived from the code: every one of
        # these dispatches to a handler that loads `args.run` and then
        # mutates something under it -- see each handler's own docstring).
        # `status`/`accept` also take `--run` but must NEVER be gated here:
        # `status` only reads; `accept` only ever applies to an ALREADY
        # complete run (lifecycle.Pointers.accept enforces this) and writes
        # only an external pointer file, never into the run directory
        # itself -- completed runs stay unconditionally readable (FIX 4).
        if args.command in _RUN_ARG_ADVANCE_COMMANDS:
            compat.guard_run(args.run)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "setup":
            return _setup(args)
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
        if args.command == "compare-runs":
            return _compare_runs(args)
        if args.command == "migrate":
            return _migrate(args)
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
            # 57B-95 review FIX 1: unlike plain `run`/`sweep` (which always
            # demand a FRESH --out via prepare_output_directory above, so an
            # existing/incompatible run can never be reached that way), the
            # post-discovery `callgraph`/`dependency-map` subcommands layer
            # into an EXISTING run dir here -- the same schema-mixing hazard
            # `guard_run` exists to catch for --run-based commands below.
            compat.guard_run(out)
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
    except compat.RuntimeCompatRefusal as exc:
        print(f"wrapper runtime incompatible: {exc}", file=sys.stderr)
        return 4
    except compat.CompatRefusal as exc:
        # 57B-95 review FIX 1: `compat.guard_run` (wired above for every
        # command that advances an EXISTING run) raises this distinct
        # exception type -- it must never fall through to the generic
        # input-error handler below, which would give it the wrong message
        # and blur it together with an ordinary usage mistake.
        print(f"wrapper compat refusal: {exc}", file=sys.stderr)
        return 4
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"wrapper input error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("wrapper interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
