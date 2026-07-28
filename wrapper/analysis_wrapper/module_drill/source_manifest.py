"""Build the canonical, bounded source index for a Module Drill run.

This module deliberately indexes *artifacts*, not their contents.  Its callers
may use the resulting manifest to locate complete provider output and then do
targeted, revision-checked source reads; they must never treat an overview
packet, report, or evidence-catalog projection as the full fact universe.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .. import identity
from .coverage import Coverage
from .source import ArtifactRecord, ProviderOutcome, RepositorySnapshot, SourceManifest, ToolIdentity
from .validation import ContractError, sha256_json

_CANONICAL_FILES = frozenset({
    "targets.json", "identity-map.json", "run-provenance.json",
    "provider-execution.json", "capabilities.json", "system-model.json",
    "module-candidates.json", "callgraph-coverage.json",
})
_CANONICAL_DIRECTORIES = frozenset({
    "access", "callgraph", "datastore", "deploy", "imports", "integrations", "routes",
})
_INDEX_FILES = frozenset({"synthesis-input.json", "evidence-catalog.json"})
_EXCLUDED_PARTS = frozenset({"raw", "tasks", ".git", "__pycache__"})


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_id(relative: str) -> str:
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
    stem = Path(relative).stem.lower().replace("_", "-")
    safe = "".join(char if char.isalnum() or char == "-" else "-" for char in stem)
    # ``artifact-`` + ``-`` + twelve digest chars leaves 42 characters under
    # the shared 64-character stable-ID ceiling.
    safe = (safe.strip("-") or "artifact")[:42]
    return f"artifact-{safe}-{digest}"


def _artifact_records(source_run: Path) -> tuple[ArtifactRecord, ...]:
    records: list[ArtifactRecord] = []
    for path in sorted(source_run.rglob("*.json")):
        relative = path.relative_to(source_run).as_posix()
        parts = Path(relative).parts
        if any(part in _EXCLUDED_PARTS for part in parts):
            continue
        top = parts[0]
        if relative in _INDEX_FILES:
            kind = "index"
        elif relative in _CANONICAL_FILES or top in _CANONICAL_DIRECTORIES:
            kind = "canonical"
        else:
            # Keep unknown JSON visible for diagnosis, but prohibit it from
            # silently becoming source authority.
            kind = "view"
        try:
            document = _load_object(path, relative)
            schema_version = str(document.get("schema_version") or "unversioned")
            integrity = "verified"
            digest = _digest(path)
        except ContractError:
            schema_version = "unreadable"
            integrity = "corrupt"
            digest = sha256_json({"corrupt": relative})
        records.append(ArtifactRecord(
            artifact_id=_artifact_id(relative), relative_path=relative,
            schema_version=schema_version, digest=digest, kind=kind,
            integrity=integrity,
        ))
    return tuple(records)


def _tool_id(value: object) -> str:
    raw = str(value or "unknown").lower()
    normalized = "".join(char if char.isalnum() else "-" for char in raw).strip("-")
    return normalized[:63] or "unknown"


def _tool_identities(provenance: dict[str, Any]) -> tuple[ToolIdentity, ...]:
    observed: dict[str, str] = {}
    for row in provenance.get("tool_versions", []):
        if not isinstance(row, dict):
            continue
        tool_id = _tool_id(row.get("tool"))
        observed.setdefault(tool_id, str(row.get("version") or "unknown"))
    return tuple(ToolIdentity(tool_id, version) for tool_id, version in sorted(observed.items()))


def _provider_coverage(row: dict[str, Any]) -> Coverage:
    raw = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    applicability = raw.get("applicability")
    status = raw.get("status")
    if applicability not in {"applicable", "not-applicable", "unknown"}:
        applicability = "unknown"
    if status not in {"complete", "partial", "unavailable", "skipped", "failed"}:
        status = "failed" if row.get("outcome") == "failed" else "unavailable"
    limitations: list[str] = []
    reason = row.get("reason")
    if isinstance(reason, str) and reason:
        limitations.append(reason)
    # Provider execution records capture an outcome, not the positive
    # code-level proof required for a feature dimension to be not-applicable.
    # Preserve that distinction instead of laundering an empty result into a
    # clean bill of health.
    if applicability == "not-applicable":
        applicability = "unknown"
        limitations.append("provider execution has no positive feature-level evidence")
    return Coverage(applicability, status, (), tuple(sorted(set(limitations))))


def _provider_outcomes(
    execution: dict[str, Any], artifacts: Iterable[ArtifactRecord],
) -> tuple[ProviderOutcome, ...]:
    execution_artifact = next(
        (record.artifact_id for record in artifacts
         if record.relative_path == "provider-execution.json" and record.authoritative),
        None,
    )
    rows = execution.get("executions", [])
    if not isinstance(rows, list):
        raise ContractError("provider-execution.json executions must be a list")
    outcomes: list[ProviderOutcome] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ContractError(f"provider-execution.json executions[{index}] must be an object")
        provider_id = row.get("provider_id")
        capability_id = row.get("capability_id")
        if not isinstance(provider_id, str) or not isinstance(capability_id, str):
            raise ContractError(f"provider-execution.json executions[{index}] lacks provider/capability id")
        coverage = _provider_coverage(row)
        artifact_ids = (execution_artifact,) if execution_artifact else ()
        if coverage.applicability == "applicable" and coverage.status == "complete" \
                and not artifact_ids:
            coverage = Coverage("applicable", "unavailable", (),
                                ("provider execution artifact is missing",))
        tools = row.get("tools") if isinstance(row.get("tools"), list) else []
        unsupported = tuple(sorted({
            _tool_id(item.get("tool_id")) for item in tools
            if isinstance(item, dict) and item.get("status") == "skipped"
        }))
        outcomes.append(ProviderOutcome(
            provider_id=provider_id, capability_id=capability_id,
            coverage=coverage, artifact_ids=artifact_ids,
            truncation=(), unsupported=unsupported,
        ))
    return tuple(sorted(outcomes, key=lambda item: (item.provider_id, item.capability_id)))


def build_from_overview(source_run: str | Path, *, snapshot_id: str) -> SourceManifest:
    """Normalize one completed overview's deterministic evidence surface."""
    run = Path(source_run).expanduser().resolve()
    targets = _load_object(run / "targets.json", "targets.json")
    provenance = _load_object(run / "run-provenance.json", "run-provenance.json")
    execution = _load_object(run / "provider-execution.json", "provider-execution.json")
    repos = targets.get("repos")
    if not isinstance(repos, list) or not repos:
        raise ContractError("targets.json must contain at least one repository")
    identities = identity.load(run)
    snapshots = tuple(RepositorySnapshot(
        repository_ref=identities.reference_for(str(item.get("repo_id") or "")),
        revision=str((item.get("git") or {}).get("head") or "NON-GIT"),
        dirty_state=("clean" if (item.get("git") or {}).get("dirty_detail") == "no"
                     else "non-git" if not (item.get("git") or {}).get("head") else "dirty"),
    ) for item in repos if isinstance(item, dict))
    if len(snapshots) != len(repos):
        raise ContractError("targets.json repositories must be objects")
    artifacts = _artifact_records(run)
    return SourceManifest(
        source_mode="overview-backed", source_overview_run=run.name,
        snapshot_id=snapshot_id, repositories=snapshots,
        preparation_options=dict(provenance.get("preparation") or {}),
        tools=_tool_identities(provenance), artifacts=artifacts,
        providers=_provider_outcomes(execution, artifacts),
    )
