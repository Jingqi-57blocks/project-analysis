"""Canonical, source-mode-neutral ModuleEvidence v1 bundle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..evidence.facts import Fact, SourceRef, make_fact_id
from ..executor import write_new_text
from .contracts import Boundary, ModuleScope

MODULE_EVIDENCE_VERSION = "module-evidence/v1"
MAX_SOURCE_FILES = 128
MAX_SOURCE_BYTES = 512 * 1024


def _artifact_ref(ref: str) -> bool:
    """Run-relative structured evidence, never a local absolute path."""
    head, separator, tail = ref.partition(":")
    if not separator or not head or Path(head).is_absolute() or ".." in Path(head).parts:
        return False
    return (head.endswith(".json") or head.startswith("signals/")) and not tail.startswith("/")


def _source_ref(ref: str) -> bool:
    try:
        SourceRef.from_string(ref)
        return True
    except ValueError:
        return False


def _refs(refs: tuple[str, ...]) -> tuple[tuple[SourceRef, ...], tuple[str, ...]]:
    source, artifacts = [], []
    for ref in refs:
        if _source_ref(ref):
            source.append(SourceRef.from_string(ref))
        elif _artifact_ref(ref):
            artifacts.append(ref)
        else:
            raise ValueError(f"unsupported ModuleEvidence reference: {ref!r}")
    return tuple(source), tuple(sorted(set(artifacts)))


@dataclass(frozen=True)
class SourceRead:
    repository_ref: str
    path: str
    source_ref: str
    status: str
    bytes_read: int
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"read", "capped", "missing", "unreadable"}:
            raise ValueError("source read status is unsupported")
        SourceRef.from_string(self.source_ref)
        if self.bytes_read < 0:
            raise ValueError("source read bytes must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {"repository_ref": self.repository_ref, "path": self.path,
                "source_ref": self.source_ref, "status": self.status,
                "bytes_read": self.bytes_read, "detail": self.detail}


@dataclass(frozen=True)
class ModuleFact:
    fact: Fact
    status: str
    activation: str
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"observed", "inferred", "unresolved"}:
            raise ValueError("module fact status is unsupported")
        if self.activation not in {"not-applicable", "not-established", "static-entry"}:
            raise ValueError("module fact activation is unsupported")
        if not all(_artifact_ref(ref) for ref in self.artifact_refs):
            raise ValueError("module fact artifact refs must be structured run-relative refs")

    def to_dict(self) -> dict[str, Any]:
        return {"fact": self.fact.to_dict(), "status": self.status,
                "activation": self.activation,
                "artifact_refs": list(sorted(set(self.artifact_refs)))}


@dataclass(frozen=True)
class VerifiedBoundary:
    direction: str
    kind: str
    neighbor_id: str
    repository_ref: str
    status: str
    source_refs: tuple[SourceRef, ...]
    artifact_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"observed", "inferred", "unresolved"}:
            raise ValueError("boundary status is unsupported")
        if not self.source_refs and not self.artifact_refs:
            raise ValueError("boundary needs source or structured artifact evidence")

    def to_dict(self) -> dict[str, Any]:
        return {"direction": self.direction, "kind": self.kind,
                "neighbor_id": self.neighbor_id, "repository_ref": self.repository_ref,
                "status": self.status,
                "source_refs": sorted(ref.to_string() for ref in self.source_refs),
                "artifact_refs": list(sorted(set(self.artifact_refs)))}


@dataclass(frozen=True)
class ModuleEvidence:
    contract_version: str
    scope_ref: dict[str, str]
    facts: tuple[ModuleFact, ...]
    boundaries: tuple[VerifiedBoundary, ...]
    coverage: dict[str, Any]
    source_reads: tuple[SourceRead, ...]
    finding_hints: tuple[dict[str, Any], ...]
    unknowns: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.contract_version != MODULE_EVIDENCE_VERSION:
            raise ValueError("unsupported ModuleEvidence version")
        if set(self.scope_ref) != {"module_id", "snapshot_id", "source_mode"}:
            raise ValueError("ModuleEvidence scope_ref has an unsupported shape")
        if not all(isinstance(item, ModuleFact) for item in self.facts):
            raise ValueError("ModuleEvidence facts must be ModuleFact values")
        if not all(isinstance(item, VerifiedBoundary) for item in self.boundaries):
            raise ValueError("ModuleEvidence boundaries must be VerifiedBoundary values")
        if not all(isinstance(item, SourceRead) for item in self.source_reads):
            raise ValueError("ModuleEvidence source_reads must be SourceRead values")

    def to_dict(self) -> dict[str, Any]:
        return {"contract_version": self.contract_version, "scope_ref": self.scope_ref,
                "facts": [item.to_dict() for item in self.facts],
                "boundaries": [item.to_dict() for item in self.boundaries],
                "coverage": self.coverage,
                "source_reads": [item.to_dict() for item in self.source_reads],
                "finding_hints": list(self.finding_hints), "unknowns": list(self.unknowns)}


def _source_revision(scope: ModuleScope, repository_ref: str) -> str:
    for repo in scope.project.repositories:
        if repo.repository_ref == repository_ref:
            return (repo.revision if repo.source_state == "non-git" or repo.dirty_detail == "no"
                    else "WORKTREE")
    raise ValueError(f"scope does not include repository {repository_ref!r}")


def _read_owned(scope: ModuleScope, paths: Mapping[str, str | Path]) -> tuple[list[ModuleFact], list[SourceRead], list[str]]:
    facts: list[ModuleFact] = []
    reads: list[SourceRead] = []
    unknowns: list[str] = []
    total = 0
    for location in scope.owned_scope:
        base = Path(paths.get(location.repository_ref, ""))
        if not base.is_dir():
            raise ValueError(f"missing local source path for repository {location.repository_ref!r}")
        revision = _source_revision(scope, location.repository_ref)
        for relative in location.files:
            source = SourceRef(location.repository_ref, revision, relative, 1)
            path = (base / relative).resolve()
            if not path.is_relative_to(base.resolve()) or not path.is_file():
                reads.append(SourceRead(location.repository_ref, relative, source.to_string(), "missing", 0,
                                        "owned source file is unavailable"))
                unknowns.append(f"Owned file could not be read: {location.repository_ref}:{relative}")
                continue
            size = path.stat().st_size
            if len(reads) >= MAX_SOURCE_FILES or total + size > MAX_SOURCE_BYTES:
                reads.append(SourceRead(location.repository_ref, relative, source.to_string(), "capped", 0,
                                        "bounded ModuleEvidence read limit reached"))
                unknowns.append("Some owned files were not read because the bounded read limit was reached.")
                continue
            try:
                content = path.read_bytes()
            except OSError as exc:
                reads.append(SourceRead(location.repository_ref, relative, source.to_string(), "unreadable", 0,
                                        str(exc)))
                unknowns.append(f"Owned file could not be read: {location.repository_ref}:{relative}")
                continue
            total += len(content)
            reads.append(SourceRead(location.repository_ref, relative, source.to_string(), "read", len(content)))
            fact = Fact(make_fact_id("module-evidence", location.repository_ref, "owned-file", (relative,)),
                        "owned-file", {"repository_ref": location.repository_ref, "path": relative,
                                       "bytes": len(content)}, (source,))
            facts.append(ModuleFact(fact, "observed", "not-applicable"))
    return facts, reads, unknowns


def build_module_evidence(scope: ModuleScope, repository_paths: Mapping[str, str | Path]) -> ModuleEvidence:
    """Create evidence from exactly the owned scope; never walk a neighbor."""
    if not isinstance(scope, ModuleScope):
        raise ValueError("build_module_evidence requires ModuleScope")
    facts, reads, unknowns = _read_owned(scope, repository_paths)
    boundaries = []
    for boundary in scope.boundaries:
        source_refs, artifact_refs = _refs(boundary.evidence_refs)
        status = "observed" if source_refs else "inferred" if artifact_refs else "unresolved"
        boundaries.append(VerifiedBoundary(boundary.direction, boundary.kind, boundary.neighbor_id,
                                           boundary.repository_ref, status, source_refs, artifact_refs))
    coverage = scope.coverage.to_dict()
    coverage["module_evidence"] = {"status": "partial" if unknowns else "complete",
                                    "read_limit": {"files": MAX_SOURCE_FILES, "bytes": MAX_SOURCE_BYTES}}
    hints = tuple({"finding_id": hint.finding_id, "status": "hint-only",
                   "evidence_refs": list(hint.evidence_refs)} for hint in scope.finding_hints)
    return ModuleEvidence(MODULE_EVIDENCE_VERSION,
                          {"module_id": scope.module.module_id, "snapshot_id": scope.snapshot_id,
                           "source_mode": scope.source_mode},
                          tuple(sorted(facts, key=lambda item: item.fact.fact_id)),
                          tuple(sorted(boundaries, key=lambda item: (item.kind, item.neighbor_id))),
                          coverage, tuple(reads), hints, tuple(sorted(set(unknowns))))


def write_module_evidence(path: str | Path, evidence: ModuleEvidence) -> Path:
    if not isinstance(evidence, ModuleEvidence):
        raise ValueError("write_module_evidence requires ModuleEvidence")
    destination = Path(path)
    write_new_text(destination, json.dumps(evidence.to_dict(), indent=2, sort_keys=True) + "\n")
    return destination


def load_module_evidence(path: str | Path) -> dict[str, Any]:
    """Load the persisted factual input with the minimum contract checks."""
    try:
        document = json.loads(Path(path).read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load ModuleEvidence: {exc}") from exc
    required = {"contract_version", "scope_ref", "facts", "boundaries", "coverage",
                "source_reads", "finding_hints", "unknowns"}
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("ModuleEvidence has an unsupported shape")
    if document["contract_version"] != MODULE_EVIDENCE_VERSION:
        raise ValueError("ModuleEvidence has an unsupported version")
    if not all(isinstance(document[key], list) for key in
               ("facts", "boundaries", "source_reads", "finding_hints", "unknowns")):
        raise ValueError("ModuleEvidence list fields are invalid")
    return document
