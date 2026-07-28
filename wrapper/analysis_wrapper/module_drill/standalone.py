"""Standalone Module Drill source preparation without overview judgment/reporting."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .. import lifecycle, run_provenance
from ..cli import prepare_deterministic_evidence
from ..discovery import emit, self_exclusion
from ..executor import write_new_text
from ..targetspec import TargetSpec, path_contains
from .run_state import AuditResult, RunStateProjection
from .source_manifest import build_from_standalone, write as write_source_manifest
from .validation import ContractError, sha256_json


@dataclass(frozen=True)
class InitializedStandaloneRun:
    run_dir: Path
    evidence_dir: Path
    run_id: str
    manifest_path: Path
    state_path: Path


def _output_root(output_root: str | Path, spec: TargetSpec) -> Path:
    root = Path(output_root).expanduser().resolve()
    for target in spec.repos:
        if path_contains(target.path, root):
            raise ContractError(
                "module run output must stay outside analyzed repositories: " + target.repo_id)
    return root


def _project_key(value: str) -> str:
    candidate = Path(value)
    if not isinstance(value, str) or not value or candidate.is_absolute() \
            or len(candidate.parts) != 1 or value in {".", ".."}:
        raise ContractError("project_key must be one safe output-path segment")
    return value


def _snapshot_id(evidence_run: Path, state: lifecycle.RunState, spec: TargetSpec) -> str:
    return sha256_json({
        "evidence_run": state.run_id,
        "repositories": [
            {"repo_id": repo.repo_id, "head": repo.git.head, "dirty": repo.git.dirty_detail}
            for repo in sorted(spec.repos, key=lambda item: item.repo_id)
        ],
        "source_provenance": (evidence_run / "run-provenance.json").read_text("utf-8"),
    })


def _prepare_args(evidence_dir: Path, *, include_network: bool, scan_date: str,
                  since: str, coupling_sample_cap: int, allow_hosts: str,
                  jobs: int | None) -> argparse.Namespace:
    return argparse.Namespace(
        run=str(evidence_dir), include_network=include_network, scan_date=scan_date,
        since=since, coupling_sample_cap=coupling_sample_cap, allow_hosts=allow_hosts,
        jobs=jobs,
    )


def initialize(
    workspace: str | Path,
    *,
    output_root: str | Path,
    project_key: str,
    selector: str,
    language: str,
    run_label: str = "",
    model: str = "unknown",
    effort: str = "unknown",
    exclude_names: tuple[str, ...] = (),
    analyzer_root: str | Path | None = None,
    include_network: bool = False,
    scan_date: str | None = None,
    since: str | None = None,
    coupling_sample_cap: int = 0,
    allow_hosts: str = "",
    jobs: int | None = None,
) -> InitializedStandaloneRun:
    """Create a self-contained evidence snapshot for direct Module Drill.

    It performs discovery and the existing deterministic evidence pass only.
    It never invokes a lens, task executor, or report renderer, so the
    resulting source is comparable to an overview's canonical evidence while
    remaining independent from any overview report or pointer.
    """
    if not isinstance(selector, str) or not selector.strip():
        raise ContractError("module selector must be a non-empty string")
    if language not in {"en", "zh-CN"}:
        raise ContractError("module language must be en or zh-CN")
    if coupling_sample_cap < 0:
        raise ContractError("coupling_sample_cap must be non-negative")
    analyzer = self_exclusion.resolve_analyzer_root(analyzer_root)
    spec, report = emit.discover(workspace, exclude_names=list(exclude_names), analyzer_root=analyzer)
    root = _output_root(output_root, spec)
    project = _project_key(project_key)
    module_root = root / project / "modules"
    prepared_scan_date = scan_date or date.today().isoformat()
    prepared_since = since or (date.today() - timedelta(days=730)).isoformat()
    identity_inputs = [
        "source:standalone", f"selector:{selector}", f"language:{language}",
        f"model:{model}", f"effort:{effort}", f"network:{include_network}",
        f"scan-date:{prepared_scan_date}", f"since:{prepared_since}",
        f"coupling-cap:{coupling_sample_cap}", f"allow-hosts:{allow_hosts}",
        *[f"{repo.repo_id}:{repo.git.head}:{repo.git.dirty_detail}" for repo in spec.repos],
    ]
    run_id = lifecycle.mint_run_id(
        identity_inputs, language, label=run_label,
        exists=lambda candidate: (module_root / candidate).exists())
    run_dir = module_root / run_id
    evidence_dir = run_dir / "evidence"
    run_dir.mkdir(parents=True)
    try:
        emit.write_stage1(evidence_dir, spec, report)
        evidence_state = lifecycle.RunState.create(
            f"standalone-{run_id}", report["project_id"], spec, language=language,
            analysis_identity={
                "wrapper": "project-analysis-wrapper",
                "analyzer": run_provenance.analyzer_observation(analyzer),
                "model": run_provenance.metadata_value(model, "model"),
                "effort": run_provenance.metadata_value(effort, "effort"),
            },
        )
        evidence_state.mark("discovery")
        run_provenance.write(evidence_dir, run_provenance.create_document(
            spec, analyzer_root=analyzer, language=language, model=model, effort=effort,
            analyzed_at=evidence_state.analyzed_at))
        evidence_state.save(evidence_dir)
        result = prepare_deterministic_evidence(_prepare_args(
            evidence_dir, include_network=include_network, scan_date=prepared_scan_date,
            since=prepared_since, coupling_sample_cap=coupling_sample_cap,
            allow_hosts=allow_hosts, jobs=jobs))
        if result != 0:
            raise ContractError(f"standalone deterministic evidence preparation failed (exit {result})")
        evidence_state = lifecycle.RunState.load(evidence_dir)
        snapshot_id = _snapshot_id(evidence_dir, evidence_state, spec)
        manifest = build_from_standalone(evidence_dir, snapshot_id=snapshot_id)
        manifest_path = write_source_manifest(run_dir / "source-manifest.json", manifest)
        audit = AuditResult(False, ("source-integrity",), ("pending-module-driver",))
        projection = RunStateProjection(
            run_id=run_id, source_manifest_digest=sha256_json(manifest.to_dict()),
            ledger_digest=sha256_json([]), complete=False, audit=audit)
        state_path = run_dir / "run-state.json"
        write_new_text(state_path, json.dumps(projection.to_dict(), indent=2, sort_keys=True) + "\n")
        write_new_text(run_dir / "provenance.json", json.dumps({
            "source_mode": "standalone", "source_evidence_run": str(evidence_dir),
            "selector": selector, "language": language, "model": model, "effort": effort,
            "preparation": {
                "include_network": include_network, "scan_date": prepared_scan_date,
                "since": prepared_since, "coupling_sample_cap": coupling_sample_cap,
                "allow_hosts": allow_hosts, "jobs": jobs,
            },
        }, indent=2, sort_keys=True) + "\n")
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    return InitializedStandaloneRun(run_dir, evidence_dir, run_id, manifest_path, state_path)
