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
from pathlib import Path

from ..sanitize import redact
from ..targetspec import RepoTarget, TargetSpec, stable_repo_id
from . import (candidates, generated, inventory, liveness, modules, pm,
               provenance, stacks)


def _manifest_inputs(repo_path: Path) -> tuple[dict, list[str]]:
    """Declared dependencies for candidate generation (parse-only)."""
    deps: dict = {}
    manifest = repo_path / "package.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text("utf-8"))
            for section in ("dependencies", "devDependencies",
                            "optionalDependencies", "peerDependencies"):
                deps.update({k: str(v) for k, v in (data.get(section) or {}).items()})
        except (OSError, ValueError):
            pass
    requires: list[str] = []
    gomod = repo_path / "go.mod"
    if gomod.is_file():
        try:
            for line in gomod.read_text("utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.endswith("// indirect"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and "." in parts[0] and parts[1].startswith("v"):
                    requires.append(parts[0])
                elif len(parts) >= 3 and parts[0] == "require" and parts[2].startswith("v"):
                    requires.append(parts[1])
        except OSError:
            pass
    return deps, requires


def _non_git_projects(root: Path, repo_paths: list[str]) -> list[Path]:
    """Top-level non-git folders with a detectable stack: reduced-coverage
    targets (no history lane, non-reproducible citations — per SKILL.md)."""
    found: list[Path] = []
    candidates = [root] if not repo_paths else []
    try:
        candidates += sorted(
            p for p in root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
            and p.name not in inventory.PRUNE_DIRS
        )
    except OSError:
        return found
    for path in candidates:
        resolved = str(path.resolve())
        if any(resolved == r or resolved.startswith(r + "/") for r in repo_paths):
            continue
        if stacks.detect(path).stacks:
            found.append(path.resolve())
    return found


def _produce_target(path: Path, repo_id: str) -> tuple[RepoTarget, list, dict]:
    """Run every per-repo producer for one target."""
    stack_report = stacks.detect(path)
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
        stacks=stack_report.stacks,
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
        "package_manager": {"name": pm_report.name,
                            "lockfile": pm_report.lockfile,
                            "evidence": pm_report.evidence},
        "tier2_exclusions": {"dirs": tier2.exclusions,
                             "evidence": tier2.evidence},
        "module_signals": signals.to_dict(),
        "candidate_notes": cand.notes,
    }
    return target, cand.candidates, report


def discover(workspace_root: str | Path,
             exclude_names: list[str] | None = None) -> tuple[TargetSpec, dict]:
    """Run all producers; return (TargetSpec, discovery report dict)."""
    inv = inventory.find_repos(workspace_root)
    excluded = set(exclude_names or [])

    repo_targets: list[RepoTarget] = []
    all_candidates = []
    repo_reports: list[dict] = []
    disclosed: list[str] = list(inv.skipped)
    reduced: list[str] = []

    def admit(path: Path, repo_id: str) -> None:
        target, cands, report = _produce_target(path, repo_id)
        repo_targets.append(target)
        all_candidates.extend(cands)
        repo_reports.append(report)

    for hit in inv.repos:
        if Path(hit.path).name in excluded:
            disclosed.append(f"{hit.path} (excluded by operator flag)")
            continue
        if hit.nested_in:
            disclosed.append(
                f"{hit.path} (nested in {hit.nested_in} — scanned as part of "
                f"the enclosing repo, not a separate target in v1)")
            continue
        admit(Path(hit.path), hit.repo_id)

    git_paths = [hit.path for hit in inv.repos]
    for path in _non_git_projects(Path(inv.workspace_root), git_paths):
        if path.name in excluded:
            disclosed.append(f"{path} (excluded by operator flag)")
            continue
        reduced.append(
            f"{path} (non-git folder: targeted with reduced coverage — "
            f"no history lane, non-reproducible citations, no caching)")
        admit(path, stable_repo_id(str(path)))

    spec = TargetSpec(repos=repo_targets, integration_candidates=all_candidates)

    # Route liveness: a frontend (Node/TS repo with a src/ SPA layout, no route
    # registrations of its own) joined against the backend route tables.
    def _tier2(repo_id: str) -> list:
        r = next((x for x in repo_reports if x["repo_id"] == repo_id), None)
        return r["tier2_exclusions"]["dirs"] if r else []
    backends, frontends = [], []
    for t in repo_targets:
        has_routes = any(_produce_has_routes(r) for r in repo_reports
                         if r["repo_id"] == t.repo_id)
        stacks_l = {s.lower() for s in t.stacks}
        if has_routes:
            backends.append((t.repo_id, t.path, _tier2(t.repo_id)))
        elif stacks_l & {"ts", "tsx", "js"} and (Path(t.path) / "src").is_dir():
            frontends.append(t)
    liveness_report = None
    if backends and len(frontends) == 1:
        rep = liveness.liveness(frontends[0].path, backends)
        liveness_report = {
            "frontend": frontends[0].repo_id,
            "calls_by_base": rep.calls_by_base(),
            "notes": rep.notes,
            "rows": [{"repo_id": r.repo_id, "method": r.method, "path": r.path,
                      "route_evidence": r.route_evidence, "status": r.status,
                      "caller_evidence": r.caller_evidence} for r in rep.rows],
        }

    report = {
        "project_id": inv.project_id,
        "workspace_root": inv.workspace_root,
        "repos": repo_reports,
        "not_targeted": sorted(disclosed),
        "reduced_coverage_targets": sorted(reduced),
        "integration_candidate_count": len(all_candidates),
        "route_liveness": liveness_report,
    }
    return spec, report


def _produce_has_routes(repo_report: dict) -> bool:
    return bool(repo_report.get("module_signals", {}).get("routes"))


def write_stage1(run_dir: str | Path, spec: TargetSpec, report: dict) -> tuple[Path, Path]:
    """Persist the stage-1 checkpoint artifacts into the run directory."""
    out = Path(run_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    targets_path = out / "targets.json"
    report_path = out / "discovery-report.json"
    spec.save(targets_path)
    report_path.write_text(
        redact(json.dumps(report, indent=2, sort_keys=True)) + "\n", "utf-8")
    return targets_path, report_path
