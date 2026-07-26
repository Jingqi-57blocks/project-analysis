"""Discovery orchestrator (57B-11 S7): produce the stage-1 run checkpoint.

Runs every producer over the workspace and writes two artifacts into the run
directory — the resumable stage-1 checkpoint the rest of the pipeline builds
on:

- ``targets.json``      — the TargetSpec the wrapper executes against
- ``discovery-report.json`` — evidence: run metadata, per-repo provenance
  blocks, stack/PM/tier2 evidence, module-candidate signals, candidate notes

The report passes through the secret redactor before it is written. Nested
repos (submodules/embedded checkouts) are inventoried and DISCLOSED but only
top-level repos become execution targets in v1 (their trees are scanned as
part of the enclosing repo).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from .. import identity
from ..executor import write_new_text
from ..profiles.bundled import bundled_registry
from ..profiles import detection as profile_detection
from ..sanitize import redact
from ..targetspec import (RepoTarget, TargetSpec, overlapping_repo_pairs,
                          path_contains, stable_repo_id)
from . import (candidates, generated, inventory, modules, pm,
               provenance, self_exclusion, stacks)


def _manifest_inputs(repo_path: Path) -> tuple[dict, list[str]]:
    """Declared dependencies for candidate generation (parse-only)."""
    deps: dict = {}
    manifest = repo_path / "package.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text("utf-8"))
            if not isinstance(data, dict):
                data = {}
            for section in ("dependencies", "devDependencies",
                            "optionalDependencies", "peerDependencies"):
                values = data.get(section) or {}
                if isinstance(values, dict):
                    deps.update({k: str(v) for k, v in values.items()})
        except (OSError, ValueError):
            pass
    requires: list[str] = []
    gomod = repo_path / "go.mod"
    if gomod.is_file():
        # Structured `go mod edit -json` (OSS parser), direct requires only.
        requires = stacks.gomod_requires(gomod, include_indirect=False)
    return deps, requires


_DIRECT_PROJECT_MANIFESTS = ("package.json", "go.mod")
def _has_direct_project_manifest(path: Path) -> bool:
    return any((path / name).is_file() for name in _DIRECT_PROJECT_MANIFESTS)


def _non_git_projects(root: Path, repo_paths: list[str]) -> tuple[list[Path], list[Path]]:
    """Return canonical non-git projects and contained child projects.

    A direct root project owns its source tree, so stack-bearing child projects
    are disclosed rather than executed again.  Without a root manifest the
    workspace is a container and direct child projects become targets.
    """
    git_roots = {Path(path).expanduser().resolve() for path in repo_paths}
    child_projects: list[Path] = []
    try:
        children = sorted(
            p for p in root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
            and p.name not in inventory.PRUNE_DIRS
        )
    except OSError:
        children = []
    for path in children:
        resolved = path.resolve()
        if resolved in git_roots:
            continue
        if stacks.detect(resolved).stacks:
            child_projects.append(resolved)

    resolved_root = root.resolve()
    if _has_direct_project_manifest(resolved_root):
        selected = [] if resolved_root in git_roots else [resolved_root]
        return selected, child_projects
    return child_projects, []


def _produce_target(path: Path, repo_id: str) -> tuple[RepoTarget, list, dict]:
    """Run every per-repo producer for one target."""
    detected = profile_detection.detect(path)
    registry = bundled_registry()
    stack_report = stacks.StackReport(
        stacks=sorted(
            registry.profile(facet.profile_id).display_name
            for facet in detected.facets if facet.kind == "language"
        ),
        analysis_roots=list(detected.analysis_roots),
        frameworks=sorted(
            registry.profile(facet.profile_id).display_name
            for facet in detected.facets if facet.kind == "framework"
        ),
        # This legacy display block's evidence surface is FROZEN to
        # stacks.STACK_REPORT_FACET_KINDS (language/ecosystem/framework/
        # repository-trait). New facet kinds (datastore in 57B-80; more in
        # later migrations) are additive in technology_facets ONLY — they
        # must never alter this legacy stacks block, which deterministic
        # parity compares byte-for-byte.
        evidence=sorted({
            f"{facet.profile_id}: {item}"
            for facet in detected.facets
            if facet.kind in stacks.STACK_REPORT_FACET_KINDS
            for item in facet.evidence
        }),
    )
    pm_report = pm.identify(path)
    tier2 = generated.derive(path)
    prov_block = provenance.repo_provenance(path, repo_id)
    deps, requires = _manifest_inputs(path)
    cand = candidates.generate(
        path, repo_id, dependencies=deps, go_requires=requires,
        tier2_exclusions=tier2.exclusions)
    signals = modules.extract(path, tier2_exclusions=tier2.exclusions)

    target = RepoTarget(
        repo_id=repo_id, path=str(path),
        facets=list(detected.facets),
        analysis_roots=stack_report.analysis_roots,
        tier2_exclusions=tier2.exclusions,
        pm=pm_report,
        git=provenance.git_provenance(path),
    )
    report = {
        "repo_id": repo_id,
        "provenance": prov_block.to_dict(),
        "stacks": {"stacks": stack_report.stacks,
                   "analysis_roots": stack_report.analysis_roots,
                   "frameworks": stack_report.frameworks,
                   "evidence": stack_report.evidence},
        "technology_facets": [asdict(facet) for facet in detected.facets],
        "unclassified_file_inventory": list(detected.unclassified_inventory),
        "technology_detection_notes": list(detected.notes),
        "package_manager": {"name": pm_report.name,
                            "lockfile": pm_report.lockfile,
                            "evidence": pm_report.evidence},
        "tier2_exclusions": {"dirs": tier2.exclusions,
                             "evidence": tier2.evidence},
        "module_signals": signals.to_dict(),
        "candidate_notes": cand.notes,
    }
    return target, cand.candidates, report


def _match_keys(path: Path, workspace_root: Path) -> set[str]:
    """A repo/project's basename plus (when it is under ``workspace_root``)
    its workspace-relative posix path — the two forms an operator can name a
    repo by on the command line (``--repo api`` or ``--repo services/api``,
    the latter disambiguating same-basename repos in different subtrees)."""
    resolved = path.resolve()
    keys = {resolved.name}
    try:
        keys.add(resolved.relative_to(workspace_root).as_posix())
    except ValueError:
        pass
    return keys


def discover(workspace_root: str | Path,
             exclude_names: list[str] | None = None,
             analyzer_root: str | Path | None = None,
             include_repos: list[str] | None = None,
             only_path: str | None = None) -> tuple[TargetSpec, dict]:
    """Run all producers; return (TargetSpec, discovery report dict).

    ``analyzer_root`` is the analyzer's own checkout, excluded by canonical path
    identity so the tool never analyzes itself (57B-34). It defaults to the
    package install location and needs no operator input; self-exclusion is
    independent of ``exclude_names``. If the analyzer is embedded inside a
    legitimate target repo we FAIL CLOSED (see ``self_exclusion``).

    Scope targeting (57B-110): ``only_path`` restricts discovery to one
    workspace-relative subdirectory; ``include_repos`` is an ALLOWLIST of
    repo names (matched against a repo's basename or its workspace-relative
    posix path — see :func:`_match_keys`). Order of application, applied to
    every candidate repo/non-git project independently: (1) ``only_path``
    scoping, (2) ``include_repos`` allowlist, (3) ``exclude_names`` denylist
    (unchanged, pre-existing) — so ``--repo`` and ``--exclude`` are fully
    combinable (``--repo`` narrows the candidate set down; ``--exclude`` can
    still remove members from what ``--repo`` selected). Every repo excluded
    by scope narrowing is disclosed in the report's ``not_targeted`` list AND
    in the dedicated ``scope_narrowing`` block, same as a pre-existing
    ``--exclude`` — never a silent subset. An ``include_repos`` entry that
    matches nothing anywhere in the workspace is a hard, fail-closed error
    (never a silent no-op over the full workspace or an empty result).
    """
    inv = inventory.find_repos(workspace_root)
    excluded = set(exclude_names or [])
    include_set = set(include_repos or [])
    matched_include: set[str] = set()
    workspace_resolved = Path(inv.workspace_root)
    analyzer = self_exclusion.resolve_analyzer_root(analyzer_root)

    only_root: Path | None = None
    if only_path:
        candidate = (workspace_resolved / only_path).resolve()
        if not path_contains(workspace_resolved, candidate):
            raise ValueError(
                f"--only path {only_path!r} escapes the workspace root "
                f"{workspace_resolved}")
        if not candidate.is_dir():
            raise ValueError(
                f"--only path {only_path!r} is not a directory under "
                f"{workspace_resolved}")
        only_root = candidate

    repo_targets: list[RepoTarget] = []
    all_candidates = []
    repo_reports: list[dict] = []
    disclosed: list[str] = list(inv.skipped)
    reduced: list[str] = []
    scope_excluded: list[str] = []

    def in_only_scope(path: Path) -> bool:
        return only_root is None or path_contains(only_root, path)

    def repo_selected(path: Path) -> bool:
        if not include_set:
            return True
        keys = _match_keys(path, workspace_resolved)
        hit = keys & include_set
        matched_include.update(hit)
        return bool(hit)

    def admit(path: Path, repo_id: str) -> None:
        for existing in repo_targets:
            if (path_contains(existing.path, path)
                    or path_contains(path, existing.path)):
                raise ValueError(
                    "canonical analysis targets overlap: "
                    f"{existing.path} and {path}")
        target, cands, report = _produce_target(path, repo_id)
        repo_targets.append(target)
        all_candidates.extend(cands)
        repo_reports.append(report)

    def self_excluded(path: str | Path) -> bool:
        """True (and disclose) when ``path`` is the analyzer's own checkout.

        Raises ``AnalyzerBoundaryConflict`` when the analyzer is embedded inside
        this discovered target — excluding at admission is the single point that
        feeds every downstream repo list, so failing closed here is total.
        """
        verdict = self_exclusion.classify(path, analyzer)
        if verdict == self_exclusion.CONFLICT:
            raise self_exclusion.AnalyzerBoundaryConflict(
                self_exclusion.conflict_message(analyzer, path))
        if verdict == self_exclusion.SELF:
            disclosed.append(f"{path} ({self_exclusion.SELF_EXCLUSION_REASON})")
            return True
        return False

    git_paths = [hit.path for hit in inv.repos]
    raw_non_git, contained_non_git = _non_git_projects(
        Path(inv.workspace_root), git_paths)
    non_git_paths: list[Path] = []
    for path in raw_non_git:
        if self_excluded(path):
            continue
        if not in_only_scope(path):
            reason = f"{path} (outside --only scope {only_path!r})"
            disclosed.append(reason)
            scope_excluded.append(reason)
            continue
        if not repo_selected(path):
            reason = f"{path} (not selected by --repo)"
            disclosed.append(reason)
            scope_excluded.append(reason)
            continue
        if path.name in excluded:
            disclosed.append(f"{path} (excluded by operator flag)")
            continue
        non_git_paths.append(path)

    # Tracks, per git-repo path, whether it survived self-exclusion/--only/
    # --repo/--exclude gating on its OWN merits (True) or was itself excluded
    # (False). ``inv.repos`` is a top-down walk (inventory.find_repos appends
    # a directory's own hit before descending into its children), so by the
    # time a nested hit is processed its enclosing repo's entry is already
    # present here — letting the nested-repo disclosure below tell the truth
    # about whether the enclosing repo was actually scanned.
    repo_scope_status: dict[str, bool] = {}

    for hit in inv.repos:
        if self_excluded(hit.path):
            repo_scope_status[hit.path] = False
            continue
        if not in_only_scope(Path(hit.path)):
            reason = f"{hit.path} (outside --only scope {only_path!r})"
            disclosed.append(reason)
            scope_excluded.append(reason)
            repo_scope_status[hit.path] = False
            continue
        if not repo_selected(Path(hit.path)):
            reason = f"{hit.path} (not selected by --repo)"
            disclosed.append(reason)
            scope_excluded.append(reason)
            repo_scope_status[hit.path] = False
            continue
        if Path(hit.path).name in excluded:
            disclosed.append(f"{hit.path} (excluded by operator flag)")
            repo_scope_status[hit.path] = False
            continue
        repo_scope_status[hit.path] = True
        if hit.nested_in:
            if Path(hit.nested_in).resolve() == analyzer:
                # The enclosing repo is the self-excluded analyzer, so this
                # subtree is NOT scanned at all — say so instead of the normal
                # nested wording, which would falsely promise coverage.
                disclosed.append(
                    f"{hit.path} (nested in the analyzer-owned checkout — "
                    f"not scanned)")
            elif repo_scope_status.get(hit.nested_in) is False:
                # The enclosing repo itself was excluded from this run's scope
                # (self-excluded, --only/--repo narrowed out, or --exclude'd),
                # so nothing scanned this subtree either — the "scanned as
                # part of the enclosing repo" wording below would be false.
                disclosed.append(
                    f"{hit.path} (nested in {hit.nested_in}, which is excluded "
                    f"from this run's scope — not scanned)")
            else:
                disclosed.append(
                    f"{hit.path} (nested in {hit.nested_in} — scanned as part "
                    f"of the enclosing repo, not a separate target in v1)")
            continue
        owner = next((path for path in non_git_paths
                      if Path(hit.path).resolve() != path
                      and path_contains(path, hit.path)), None)
        if owner is not None:
            disclosed.append(
                f"{hit.path} (contained in {owner} — scanned as part of the "
                "canonical non-git project, not a separate target)")
            continue
        admit(Path(hit.path), hit.repo_id)

    unmatched_include = include_set - matched_include
    if unmatched_include:
        all_candidate_paths = [Path(p) for p in git_paths] + raw_non_git
        if only_root is not None:
            # Scoped to --only: the unfiltered inventory would list repos
            # --only itself excludes, which reads as self-contradictory
            # ("matched no repository ... available: [the very name it
            # rejected]"). Report what's actually reachable under --only.
            location = "in scope"
            candidate_paths = [p for p in all_candidate_paths if in_only_scope(p)]
        else:
            location = "in the workspace"
            candidate_paths = all_candidate_paths
        available = sorted({
            key for path in candidate_paths
            for key in _match_keys(path, workspace_resolved)
        })
        raise ValueError(
            f"--repo value(s) matched no repository {location}: "
            f"{sorted(unmatched_include)}; available: {available}")

    for path in non_git_paths:
        owner = next((Path(target.path).resolve() for target in repo_targets
                      if Path(target.path).resolve() != path
                      and (path_contains(target.path, path)
                           or path_contains(path, target.path))), None)
        if owner is not None:
            disclosed.append(
                f"{path} (overlaps canonical target {owner} — not targeted "
                "separately)")
            continue
        reduced.append(
            f"{path} (non-git folder: targeted with reduced coverage — "
            f"no history lane, non-reproducible citations, no caching)")
        admit(path, stable_repo_id(str(path)))

    if (only_path or include_set) and not repo_targets:
        # A narrowed run (--only/--repo) that ends up with zero execution
        # targets is never allowed to mint silently: without this, a request
        # like `--repo <name-that-matches-a-nested-repo-whose-enclosing-repo-
        # was-just-excluded>` (or `--only <a-dir-with-no-repos>`) would run to
        # completion, write a run directory, and exit 0 while claiming
        # coverage it never had (the enclosing-repo case is exactly what the
        # nested-repo disclosure above now refuses to misstate). Scoped to
        # the NARROWED case only — an unnarrowed empty workspace, or a
        # legitimate non-git/no-repos workspace with no --only/--repo given,
        # is a pre-existing, intentionally-supported outcome and must keep
        # working.
        requested_bits = []
        if only_path:
            requested_bits.append(f"--only={only_path!r}")
        if include_set:
            requested_bits.append(f"--repo={sorted(include_set)!r}")
        available = sorted({
            key for path in [Path(p) for p in git_paths] + raw_non_git
            for key in _match_keys(path, workspace_resolved)
        })
        raise ValueError(
            "narrowed scope ({}) matched zero analysis targets in this "
            "workspace; matched candidate name(s): {}; excluded: {}; "
            "available in workspace: {}".format(
                ", ".join(requested_bits),
                sorted(matched_include) if matched_include else "(none)",
                sorted(disclosed), available))

    workspace = Path(inv.workspace_root).resolve()
    root_targeted = any(Path(target.path).resolve() == workspace
                        for target in repo_targets)
    if root_targeted:
        for path in contained_non_git:
            disclosed.append(
                f"{path} (contained in root project {workspace} — scanned as "
                "part of the root, not a separate target)")
    elif not _has_direct_project_manifest(workspace) and repo_targets:
        disclosed.append(
            f"{workspace} (workspace container: no root-level package.json or "
            "go.mod; contained projects targeted separately)")

    spec = TargetSpec(repos=repo_targets, integration_candidates=all_candidates)
    overlaps = overlapping_repo_pairs(spec.repos)
    if overlaps:
        raise ValueError(f"canonical analysis targets overlap: {overlaps}")

    report = {
        "project_id": inv.project_id,
        "workspace_root": inv.workspace_root,
        "repos": repo_reports,
        "not_targeted": sorted(disclosed),
        "reduced_coverage_targets": sorted(reduced),
        "integration_candidate_count": len(all_candidates),
        # 57B-110: honest-disclosure surface for operator-narrowed scope,
        # dedicated (machine-readable) alongside the same entries already
        # folded into `not_targeted` above (57B-91-style "one fact, two
        # views" — never a silent subset). `only_path`/`repo_filter` record
        # the narrowing REQUEST even when it excluded nothing (e.g. --repo
        # named every repo in the workspace); `excluded` lists exactly which
        # candidates it removed and why.
        "scope_narrowing": {
            "only_path": only_path or None,
            "repo_filter": sorted(include_set) if include_set else None,
            "excluded": sorted(scope_excluded),
        },
        # route_inventory/ui_route_linkage (route liveness: a frontend joined
        # against backend route tables) retired here (57B-84 B2):
        # RouteInventoryProvider/UiRouteLinkageProvider are now the
        # capability-provider fragment source, and routes.emit.assemble
        # writes the canonical routes/route-inventory.json + routes/ui-
        # route-linkage.json run-level docs directly — see that module's
        # own docstring for the fragment+assemble shape and why it replaces
        # this stage-1 block (it also eliminates this block's own
        # `liveness.liveness()`-per-frontend backend rescan).
        #
        # role_catalog_by_repo (cross-repo role-catalog summary) retired here
        # (57B-84): access_model, its own source, is now the access-evidence
        # provider's own per-repo artifact, not a stage-1 report block —
        # synthesis_input.py re-derives role_catalog_by_repository from the
        # loaded access artifacts instead (byte-identical output, new source).
    }
    return spec, report


def write_stage1(run_dir: str | Path, spec: TargetSpec, report: dict) -> tuple[Path, Path]:
    """Persist the stage-1 checkpoint artifacts into the run directory."""
    out = Path(run_dir).expanduser().resolve()
    if out.exists() or out.is_symlink():
        raise ValueError(f"stage-1 run directory already exists: {out}")
    identity_mapping = identity.build(
        spec,
        workspace_root=report.get("workspace_root", ""),
        project_id=report.get("project_id", ""),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.stage1-", dir=out.parent))
    targets_path = out / "targets.json"
    report_path = out / "discovery-report.json"
    try:
        write_new_text(staging / "targets.json", spec.to_json())
        write_new_text(
            staging / "discovery-report.json",
            redact(json.dumps(
                identity.externalize_discovery_report(report, identity_mapping),
                indent=2, sort_keys=True)) + "\n",
        )
        identity.write_mapping(staging, identity_mapping)
        os.rename(staging, out)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return targets_path, report_path
