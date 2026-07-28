"""Immutable Module Drill run initialization from a verified overview source."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import lifecycle, run_provenance
from ..executor import write_new_text
from ..targetspec import TargetSpec, path_contains
from .run_state import AuditResult, RunStateProjection
from .source_manifest import build_from_overview, write as write_source_manifest
from .validation import ContractError, sha256_json


@dataclass(frozen=True)
class InitializedRun:
    run_dir: Path
    run_id: str
    manifest_path: Path
    state_path: Path


def _overview_state(source: Path) -> tuple[object, TargetSpec]:
    if not (source / "targets.json").is_file() or not (source / "run-state.json").is_file():
        raise ContractError("overview source must contain targets.json and run-state.json")
    state = lifecycle.RunState.load(source)
    spec = TargetSpec.load(source / "targets.json")
    problems = state.staleness()
    try:
        provenance = run_provenance.load(source)
        problems.extend(run_provenance.target_source_staleness(provenance, spec))
    except (OSError, ValueError) as exc:
        raise ContractError(f"overview source provenance is invalid: {exc}") from exc
    if problems:
        raise ContractError("overview source is stale: " + "; ".join(problems))
    return state, spec


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


def _source_snapshot_id(source: Path, state: Any, spec: TargetSpec) -> str:
    return sha256_json({
        "overview_run": state.run_id,
        "repositories": [
            {"repo_id": repo.repo_id, "head": repo.git.head,
             "dirty": repo.git.dirty_detail}
            for repo in sorted(spec.repos, key=lambda item: item.repo_id)
        ],
        "source_provenance": (source / "run-provenance.json").read_text("utf-8"),
    })


def initialize_from_overview(
    source_run: str | Path,
    *,
    output_root: str | Path,
    project_key: str,
    selector: str,
    language: str,
    run_label: str = "",
    model: str = "unknown",
    effort: str = "unknown",
) -> InitializedRun:
    """Create one immutable incomplete Module Drill run from a fresh overview.

    It deliberately does not create model tasks or claim completion.  The
    later module driver owns the task ledger; this initializer only establishes
    the exact source snapshot that every later task must consume.
    """
    source = Path(source_run).expanduser().resolve()
    state, spec = _overview_state(source)
    root = _output_root(output_root, spec)
    if not isinstance(selector, str) or not selector.strip():
        raise ContractError("module selector must be a non-empty string")
    if language not in {"en", "zh-CN"}:
        raise ContractError("module language must be en or zh-CN")

    module_root = root / _project_key(project_key) / "modules"
    identity_inputs = [
        f"overview:{state.run_id}", f"selector:{selector}", f"language:{language}",
        f"model:{model}", f"effort:{effort}",
        *[f"{repo.repo_id}:{repo.git.head}:{repo.git.dirty_detail}" for repo in spec.repos],
    ]
    run_id = lifecycle.mint_run_id(
        identity_inputs, language, label=run_label,
        exists=lambda candidate: (module_root / candidate).exists())
    run_dir = module_root / run_id
    run_dir.mkdir(parents=True)

    try:
        snapshot_id = _source_snapshot_id(source, state, spec)
        manifest = build_from_overview(source, snapshot_id=snapshot_id)
        manifest_path = write_source_manifest(run_dir / "source-manifest.json", manifest)
        audit = AuditResult(False, ("source-integrity",), ("pending-module-driver",))
        projection = RunStateProjection(
            run_id=run_id,
            source_manifest_digest=sha256_json(manifest.to_dict()),
            ledger_digest=sha256_json([]), complete=False, audit=audit)
        state_path = run_dir / "run-state.json"
        write_new_text(state_path, json.dumps(projection.to_dict(), indent=2, sort_keys=True) + "\n")
        write_new_text(run_dir / "provenance.json", json.dumps({
            "source_overview_run": state.run_id,
            "source_run": str(source), "selector": selector,
            "language": language, "model": model, "effort": effort,
        }, indent=2, sort_keys=True) + "\n")
    except Exception:
        # A failed initialization must not leave a directory that a later run
        # could mistake for an interrupted, resumable checkpoint.
        for child in sorted(run_dir.glob("*")):
            child.unlink()
        run_dir.rmdir()
        raise
    return InitializedRun(run_dir, run_id, manifest_path, state_path)
