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
from . import (access_model, candidates, deploy_units, generated, integrations,
               inventory, liveness, modules, pm, provenance, self_exclusion,
               stacks)


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
    integ = integrations.generate(path, repo_id, tier2_exclusions=tier2.exclusions)
    access = access_model.generate(path, repo_id, tier2_exclusions=tier2.exclusions)
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
        "integration_evidence": integ.to_dict(),
        "access_model": access.to_dict(),
        "deployable_units": deploy_units.generate(path, tier2.exclusions).to_dict(),
    }
    return target, cand.candidates, report


def discover(workspace_root: str | Path,
             exclude_names: list[str] | None = None,
             analyzer_root: str | Path | None = None) -> tuple[TargetSpec, dict]:
    """Run all producers; return (TargetSpec, discovery report dict).

    ``analyzer_root`` is the analyzer's own checkout, excluded by canonical path
    identity so the tool never analyzes itself (57B-34). It defaults to the
    package install location and needs no operator input; self-exclusion is
    independent of ``exclude_names``. If the analyzer is embedded inside a
    legitimate target repo we FAIL CLOSED (see ``self_exclusion``).
    """
    inv = inventory.find_repos(workspace_root)
    excluded = set(exclude_names or [])
    analyzer = self_exclusion.resolve_analyzer_root(analyzer_root)

    repo_targets: list[RepoTarget] = []
    all_candidates = []
    repo_reports: list[dict] = []
    disclosed: list[str] = list(inv.skipped)
    reduced: list[str] = []

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
        if path.name in excluded:
            disclosed.append(f"{path} (excluded by operator flag)")
            continue
        non_git_paths.append(path)

    for hit in inv.repos:
        if self_excluded(hit.path):
            continue
        if Path(hit.path).name in excluded:
            disclosed.append(f"{hit.path} (excluded by operator flag)")
            continue
        if hit.nested_in:
            if Path(hit.nested_in).resolve() == analyzer:
                # The enclosing repo is the self-excluded analyzer, so this
                # subtree is NOT scanned at all — say so instead of the normal
                # nested wording, which would falsely promise coverage.
                disclosed.append(
                    f"{hit.path} (nested in the analyzer-owned checkout — "
                    f"not scanned)")
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

    # Route liveness: a frontend (Node/TS repo with a src/ SPA layout, no route
    # registrations of its own) joined against the backend route tables.
    def _tier2(repo_id: str) -> list:
        r = next((x for x in repo_reports if x["repo_id"] == repo_id), None)
        return r["tier2_exclusions"]["dirs"] if r else []
    backends, frontends = [], []
    for t in repo_targets:
        has_routes = any(_produce_has_routes(r) for r in repo_reports
                         if r["repo_id"] == t.repo_id)
        repo_report = next((r for r in repo_reports if r["repo_id"] == t.repo_id), {})
        stacks_l = {s.lower() for s in t.stacks}
        if has_routes or t.profiles_for_capability("route-inventory"):
            backends.append((t.repo_id, t.path, _tier2(t.repo_id)))
        if (t.profiles_for_capability("ui-route-linkage") or
                (not t.profiles_for_capability("route-inventory") and
                 stacks_l & {"ts", "tsx", "js"} and
                 (Path(t.path) / "src").is_dir() and not has_routes)):
            frontends.append(t)
    route_inventory = None
    ui_route_linkage = None
    if backends:
        inventory_rows = []
        inventory_notes = []
        for repo_id, path, tier2_dirs in sorted(backends):
            stats = {"file_cap_hit": False, "oversized": 0}
            for hit in liveness.route_registrations(
                    path, tier2_dirs, stats, include_mounts=True):
                inventory_rows.append({
                    "repo_id": repo_id, "method": hit.method, "path": hit.path,
                    "route_evidence": hit.evidence,
                    "registration_kind": (
                        "mount" if hit.method.upper() in liveness._MOUNTS else "endpoint"),
                })
            if stats["file_cap_hit"] or stats["oversized"]:
                inventory_notes.append(
                    f"{repo_id}: COVERAGE CAP in fallback route scan "
                    f"(file_cap={stats['file_cap_hit']}, oversized={stats['oversized']})")
        if not liveness.astgrep.available():
            inventory_notes.append("ROUTE EXTRACTION FALLBACK: ast-grep unavailable")
        route_inventory = {
            "notes": inventory_notes,
            "rows": sorted(inventory_rows, key=lambda row: (
                row["repo_id"], row["method"], row["path"], row["route_evidence"])),
            **liveness.astgrep.probe().provenance(),
        }
        linkage_rows = []
        calls_by_frontend = {}
        linkage_notes = []
        for frontend in sorted(frontends, key=lambda row: row.repo_id):
            linked = liveness.liveness(frontend.path, backends)
            calls_by_frontend[frontend.repo_id] = linked.calls_by_base()
            linkage_notes.extend(f"{frontend.repo_id}: {note}" for note in linked.notes)
            for row in linked.rows:
                if row.status not in {"ui-called", "method-unresolved"} \
                        or not row.caller_evidence:
                    continue
                linkage_rows.append({
                    "frontend_repo_id": frontend.repo_id,
                    "repo_id": row.repo_id,
                    "method": row.method,
                    "path": row.path,
                    "route_evidence": row.route_evidence,
                    "status": row.status,
                    "caller_evidence": row.caller_evidence,
                })
        ui_route_linkage = {
            "frontends": sorted(t.repo_id for t in frontends),
            "calls_by_frontend": calls_by_frontend,
            "rows": sorted(linkage_rows, key=lambda row: (
                row["frontend_repo_id"], row["repo_id"], row["method"],
                row["path"], row["route_evidence"])),
            "notes": sorted(set(linkage_notes)),
        }

    report = {
        "project_id": inv.project_id,
        "workspace_root": inv.workspace_root,
        "repos": repo_reports,
        "not_targeted": sorted(disclosed),
        "reduced_coverage_targets": sorted(reduced),
        "integration_candidate_count": len(all_candidates),
        "route_inventory": route_inventory,
        "ui_route_linkage": ui_route_linkage,
        # Cross-repo role-catalog summary so catalog DIFFERENCES are computable
        # (locate-only; names, not meanings).
        "role_catalog_by_repo": {
            r["repo_id"]: r.get("access_model", {}).get("role_catalog_names", [])
            for r in repo_reports
        },
    }
    return spec, report


def _produce_has_routes(repo_report: dict) -> bool:
    return bool(repo_report.get("module_signals", {}).get("routes"))


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
