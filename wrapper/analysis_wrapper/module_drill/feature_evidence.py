"""Canonical, feature-addressable evidence for Module Drill.

This is deliberately an index over complete deterministic provider artifacts,
not an overview report or its bounded synthesis projection.  It does not pick
a feature, traverse dependencies, or infer business behaviour.  Those are
separate stages which consume the stable evidence IDs emitted here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..evidence.facts import SourceRef
from ..executor import create_stage_dir, write_new_text
from .context import SourceContext
from .coverage import Coverage
from .scope import FeatureSeed
from .validation import ContractError, sha256_json

SCHEMA_VERSION = "feature-evidence/v1"
FILENAME = "feature-evidence.json"


@dataclass(frozen=True)
class EvidenceItem:
    """One evidence-backed feature anchor from a canonical provider artifact."""

    evidence_id: str
    kind: str
    repository_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    artifact_id: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "repository_refs": list(self.repository_refs),
            "source_refs": list(self.source_refs),
            "artifact_id": self.artifact_id,
            "data": self.data,
        }


def _id(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"evidence-{digest}"


def _site(path_and_line: object, repository_ref: str, revisions: dict[str, str]) -> str:
    if not isinstance(path_and_line, str) or not path_and_line:
        raise ContractError("canonical evidence site must be a non-empty path:line string")
    path, marker, line_text = path_and_line.rpartition(":")
    if not marker or not path or not line_text.isdigit() or int(line_text) < 1:
        raise ContractError(f"canonical evidence site is not path:line: {path_and_line!r}")
    revision = revisions.get(repository_ref)
    if revision is None:
        raise ContractError(f"canonical evidence names repository outside snapshot: {repository_ref!r}")
    return SourceRef(repository_ref, revision, path, int(line_text)).to_string()


def _source_refs(sites: Iterable[object], repository_ref: str,
                 revisions: dict[str, str]) -> tuple[str, ...]:
    return tuple(sorted({_site(site, repository_ref, revisions) for site in sites}))


def _documents(context: SourceContext) -> dict[str, tuple[str, dict[str, Any]]]:
    """Load all verified canonical JSON artifacts, rejecting source tampering.

    The source manifest's digest is an integrity commitment.  A later write
    to a source artifact must never be silently consumed by Module Drill.
    """
    rows: dict[str, tuple[str, dict[str, Any]]] = {}
    for artifact in context.manifest.artifacts:
        if artifact.kind != "canonical" or artifact.integrity != "verified":
            continue
        path = context.source_run / artifact.relative_path
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ContractError(f"canonical evidence artifact is missing: {artifact.relative_path}") from exc
        if path.is_symlink() or not resolved.is_relative_to(context.source_run) or not resolved.is_file():
            raise ContractError(f"canonical evidence artifact is unsafe: {artifact.relative_path}")
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != artifact.digest:
            raise ContractError(f"canonical evidence artifact digest changed: {artifact.relative_path}")
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ContractError(f"canonical evidence artifact is not JSON: {artifact.relative_path}") from exc
        if not isinstance(document, dict):
            raise ContractError(f"canonical evidence artifact is not an object: {artifact.relative_path}")
        rows[artifact.relative_path] = (artifact.artifact_id, document)
    return rows


def _route_items(artifact_id: str, document: dict[str, Any],
                 revisions: dict[str, str]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for row in document.get("rows", []):
        if not isinstance(row, dict):
            raise ContractError("route inventory rows must be objects")
        repository_ref = row.get("repository_ref")
        method, path, evidence = row.get("method"), row.get("path"), row.get("route_evidence")
        if not all(isinstance(value, str) and value for value in (repository_ref, method, path, evidence)):
            raise ContractError("route inventory row lacks repository_ref, method, path, or route_evidence")
        refs = _source_refs((evidence,), repository_ref, revisions)
        items.append(EvidenceItem(
            _id(artifact_id, "route", repository_ref, method, path, evidence), "route",
            (repository_ref,), refs, artifact_id,
            {"method": method, "path": path, "status": row.get("status", "")},
        ))
    return items


def _ui_link_items(artifact_id: str, document: dict[str, Any],
                   revisions: dict[str, str]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for row in document.get("rows", []):
        if not isinstance(row, dict):
            raise ContractError("UI-route linkage rows must be objects")
        frontend = row.get("frontend_repository_ref")
        backend = row.get("repository_ref")
        method, path, route_evidence = row.get("method"), row.get("path"), row.get("route_evidence")
        callers = row.get("caller_evidence")
        if not all(isinstance(value, str) and value for value in (frontend, backend, method, path, route_evidence)) \
                or not isinstance(callers, list):
            raise ContractError("UI-route linkage row has an invalid required field")
        refs = _source_refs((route_evidence,), backend, revisions) + _source_refs(callers, frontend, revisions)
        repositories = (frontend,) if frontend == backend else (frontend, backend)
        items.append(EvidenceItem(
            _id(artifact_id, "ui-route-link", frontend, backend, method, path, *sorted(callers)),
            "ui-action", repositories, tuple(sorted(set(refs))), artifact_id,
            {"method": method, "path": path, "route_status": row.get("status", ""),
             "target_repository_ref": backend},
        ))
    return items


def _datastore_items(artifact_id: str, document: dict[str, Any], repository_ref: str,
                     revisions: dict[str, str]) -> list[EvidenceItem]:
    tables = document.get("tables", {})
    metadata = document.get("store_metadata", {})
    if not isinstance(tables, dict) or not isinstance(metadata, dict):
        raise ContractError("datastore evidence tables and store_metadata must be objects")
    items: list[EvidenceItem] = []
    for name, accesses in sorted(tables.items()):
        if not isinstance(name, str) or not isinstance(accesses, dict):
            raise ContractError("datastore evidence table row is invalid")
        sites = [site for bucket in accesses.values() if isinstance(bucket, list) for site in bucket]
        refs = _source_refs(sites, repository_ref, revisions)
        if not refs:
            continue
        meta = metadata.get(name, {})
        if not isinstance(meta, dict):
            meta = {}
        items.append(EvidenceItem(
            _id(artifact_id, "datastore", repository_ref, name), "datastore", (repository_ref,),
            refs, artifact_id,
            {"name": name, "access_kinds": sorted(str(key) for key in accesses),
             "physical_name": meta.get("physical_name", name), "store_kind": meta.get("kind", "")},
        ))
    return items


def _access_items(artifact_id: str, document: dict[str, Any], repository_ref: str,
                  revisions: dict[str, str]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for row in document.get("role_catalog", []):
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ContractError("access role_catalog row is invalid")
        refs = _source_refs((row.get("evidence"),), repository_ref, revisions)
        items.append(EvidenceItem(_id(artifact_id, "role", repository_ref, row["name"], *refs),
                                  "access-role", (repository_ref,), refs, artifact_id,
                                  {"name": row["name"], "kind": row.get("kind", "")}))
    for kind in ("authz_checks", "middleware", "route_guards", "contextual_identity"):
        bucket = document.get(kind, {})
        if not isinstance(bucket, dict):
            raise ContractError(f"access {kind} must be an object")
        samples = bucket.get("sample", [])
        if not isinstance(samples, list):
            raise ContractError(f"access {kind}.sample must be a list")
        for index, site in enumerate(samples):
            refs = _source_refs((site,), repository_ref, revisions)
            items.append(EvidenceItem(_id(artifact_id, kind, repository_ref, str(index), *refs),
                                      "access-check", (repository_ref,), refs, artifact_id,
                                      {"category": kind, "observed_count": bucket.get("count", 0)}))
    return items


def _integration_items(artifact_id: str, document: dict[str, Any], repository_ref: str,
                       revisions: dict[str, str]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for kind, key, label in (("integration-host", "host_fragments", "value"),
                             ("integration-package", "integration_packages", "package")):
        rows = document.get(key, [])
        if not isinstance(rows, list):
            raise ContractError(f"integration {key} must be a list")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get(label), str):
                raise ContractError(f"integration {key} row is invalid")
            evidence = row.get("evidence", [])
            if not isinstance(evidence, list):
                raise ContractError(f"integration {key}.evidence must be a list")
            refs = _source_refs(evidence, repository_ref, revisions)
            if not refs:
                continue
            items.append(EvidenceItem(_id(artifact_id, kind, repository_ref, row[label]), kind,
                                      (repository_ref,), refs, artifact_id,
                                      {label: row[label], "candidate": True}))
    return items


def _boundary_items(artifact_id: str, document: dict[str, Any], repository_ref: str,
                    revisions: dict[str, str]) -> list[EvidenceItem]:
    groups = (
        ("async-boundary", "async_boundaries"),
        ("configuration", "configuration_references"),
        ("test-file", "test_files"),
    )
    items: list[EvidenceItem] = []
    for kind, key in groups:
        rows = document.get(key, [])
        if not isinstance(rows, list):
            raise ContractError(f"feature boundaries {key} must be a list")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ContractError(f"feature boundaries {key} row is invalid")
            refs = _source_refs((row.get("evidence"),), repository_ref, revisions)
            items.append(EvidenceItem(
                _id(artifact_id, kind, repository_ref, str(index), *refs), kind,
                (repository_ref,), refs, artifact_id, dict(row)))
    return items


def _repo_for_artifact(path: str, context: SourceContext) -> str | None:
    name = Path(path).stem
    for repository_ref in context.manifest.repository_refs:
        if context.identities.artifact_key_for(
                context.identities.internal_id_for(repository_ref)) == name:
            return repository_ref
    return None


def _seed_kind(item: EvidenceItem) -> str | None:
    return {
        "ui-action": "ui-action", "route": "route", "datastore": "datastore",
        "access-role": "symbol", "access-check": "symbol",
        "integration-host": "package", "integration-package": "package",
        "async-boundary": "job-event", "configuration": "symbol",
    }.get(item.kind)


def build(context: SourceContext) -> dict[str, Any]:
    """Build the complete, deterministic feature evidence index for one run."""
    documents = _documents(context)
    revisions = {row.repository_ref: row.revision for row in context.manifest.repositories}
    items: list[EvidenceItem] = []
    consumed: list[str] = []
    for relative, (artifact_id, document) in sorted(documents.items()):
        if relative == "routes/route-inventory.json":
            items.extend(_route_items(artifact_id, document, revisions))
        elif relative == "routes/ui-route-linkage.json":
            items.extend(_ui_link_items(artifact_id, document, revisions))
        elif relative.startswith("datastore/"):
            repository_ref = _repo_for_artifact(relative, context)
            if repository_ref is not None:
                items.extend(_datastore_items(artifact_id, document, repository_ref, revisions))
        elif relative.startswith("access/"):
            repository_ref = _repo_for_artifact(relative, context)
            if repository_ref is not None:
                items.extend(_access_items(artifact_id, document, repository_ref, revisions))
        elif relative.startswith("integrations/"):
            repository_ref = _repo_for_artifact(relative, context)
            if repository_ref is not None:
                items.extend(_integration_items(artifact_id, document, repository_ref, revisions))
        elif relative.startswith("feature-boundaries/"):
            repository_ref = _repo_for_artifact(relative, context)
            if repository_ref is not None:
                items.extend(_boundary_items(artifact_id, document, repository_ref, revisions))
        else:
            continue
        consumed.append(artifact_id)

    unique = {item.evidence_id: item for item in items}
    if len(unique) != len(items):
        raise ContractError("canonical feature evidence produced duplicate evidence IDs")
    ordered = tuple(sorted(unique.values(), key=lambda item: item.evidence_id))
    seeds: list[FeatureSeed] = []
    for item in ordered:
        kind = _seed_kind(item)
        if kind is None:
            continue
        seeds.append(FeatureSeed(
            seed_id=f"seed-{item.evidence_id.removeprefix('evidence-')[:20]}", kind=kind,
            repository_ref=item.repository_refs[0], evidence_refs=item.source_refs,
            coverage=Coverage("unknown", "unavailable", (), (
                "feature dimension coverage is evaluated only after bounded closure",)),
        ))
    manifest_digest = sha256_json(context.manifest.to_dict())
    return {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digest": manifest_digest,
        "source_snapshot_id": context.manifest.snapshot_id,
        "consumed_artifact_ids": sorted(set(consumed)),
        "items": [item.to_dict() for item in ordered],
        "seeds": [seed.to_dict() for seed in sorted(seeds, key=lambda seed: seed.seed_id)],
    }


def write(context: SourceContext) -> Path:
    """Persist the index once in the Module Drill's immutable evidence area."""
    evidence_dir = create_stage_dir(context.module_run / "evidence")
    out = evidence_dir / FILENAME
    document = build(context)
    write_new_text(out, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return out
