"""ModuleScope v1: a small, technology-neutral contract for Module Drill.

This module is intentionally a data contract.  It has no knowledge of the
overview pipeline, frameworks, target projects, Markdown, or report rendering.
Scope providers normalize their own inputs into :class:`ModuleScope`; all later
stages consume this one shape.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, runtime_checkable

from ..evidence.coverage import Coverage
from ..evidence.facts import SourceRef
from ..executor import write_new_text

MODULE_SCOPE_VERSION = "module-scope/v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SOURCE_MODES = frozenset({"overview", "standalone"})
_SELECTOR_KINDS = frozenset({"name", "alias", "path", "package", "symbol", "route", "api"})
_CONFIDENCE = frozenset({"high", "medium", "low"})
_DIRECTIONS = frozenset({"inbound", "outbound", "bidirectional"})
_REVISIONS = re.compile(r"^[0-9a-f]{40}$")
_STATES = frozenset({"git", "non-git"})


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must use 1-128 letters, digits, dot, underscore, or hyphen")
    return value


def _text(value: Any, label: str, *, allow_empty: bool = False, limit: int = 512) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} contains unsupported characters or is too long")
    return value


def _relative(value: Any, label: str, *, allow_dot: bool = False) -> str:
    value = _text(value, label)
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or (normalized == "." and not allow_dot):
        raise ValueError(f"{label} must be a relative path without '..'")
    return normalized


def _reference(value: Any, label: str) -> str:
    """Validate a logical project/repository reference, never a local path."""
    return _relative(value, label, allow_dot=False)


def _refs(value: Any, label: str, *, require: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list of evidence references")
    result = tuple(_text(item, f"{label} entry", limit=1024) for item in value)
    if require and not result:
        raise ValueError(f"{label} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{label} has duplicate evidence references")
    for item in result:
        if "@" in item:
            # Source citations use the existing, strict repository citation
            # grammar. In particular, it rejects an absolute file path after
            # a valid-looking repo/revision prefix.
            SourceRef.from_string(item)
        else:
            # Signal/artifact refs are persisted relative to this run.
            _relative(item.split(":", 1)[0], f"{label} artifact path")
    return result


def _list(value: Any, label: str, *, limit: int = 128) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list")
    result = tuple(_text(item, f"{label} entry") for item in value)
    if len(result) > limit or len(result) != len(set(result)):
        raise ValueError(f"{label} has too many or duplicate entries")
    return result


@dataclass(frozen=True)
class RepositorySnapshot:
    """One exact repository state, expressed without a machine-local path."""

    repository_ref: str
    revision: str
    source_state: Literal["git", "non-git"]
    dirty_detail: str = "no"

    def __post_init__(self) -> None:
        _reference(self.repository_ref, "repository_ref")
        if self.source_state not in _STATES:
            raise ValueError("source_state must be 'git' or 'non-git'")
        if self.source_state == "git" and not _REVISIONS.fullmatch(self.revision):
            raise ValueError("git repository revision must be a 40-character SHA")
        if self.source_state == "non-git" and self.revision != "NON-GIT":
            raise ValueError("non-git repository revision must be 'NON-GIT'")
        _text(self.dirty_detail, "dirty_detail")

    def to_dict(self) -> dict[str, str]:
        return {
            "repository_ref": self.repository_ref,
            "revision": self.revision,
            "source_state": self.source_state,
            "dirty_detail": self.dirty_detail,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RepositorySnapshot":
        if not isinstance(value, dict) or set(value) != {
            "repository_ref", "revision", "source_state", "dirty_detail"
        }:
            raise ValueError("repository snapshot has an unsupported shape")
        return cls(**value)


@dataclass(frozen=True)
class ProjectSnapshot:
    """The complete source snapshot Module Drill is allowed to describe."""

    project_ref: str
    repositories: tuple[RepositorySnapshot, ...]

    def __post_init__(self) -> None:
        _reference(self.project_ref, "project_ref")
        object.__setattr__(self, "repositories", tuple(self.repositories))
        if not self.repositories or not all(isinstance(item, RepositorySnapshot)
                                            for item in self.repositories):
            raise ValueError("project snapshot needs at least one repository snapshot")
        refs = [item.repository_ref for item in self.repositories]
        if len(refs) != len(set(refs)):
            raise ValueError("project snapshot has duplicate repository references")

    @property
    def snapshot_id(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def inspection_only(self) -> bool:
        return any(item.source_state == "non-git" or item.dirty_detail != "no"
                   for item in self.repositories)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_ref": self.project_ref,
            "repositories": [item.to_dict() for item in sorted(
                self.repositories, key=lambda item: item.repository_ref)],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ProjectSnapshot":
        if not isinstance(value, dict) or set(value) != {"project_ref", "repositories"}:
            raise ValueError("project snapshot has an unsupported shape")
        repos = value["repositories"]
        if not isinstance(repos, list):
            raise ValueError("project snapshot repositories must be a list")
        return cls(project_ref=value["project_ref"],
                   repositories=tuple(RepositorySnapshot.from_dict(item) for item in repos))


@dataclass(frozen=True)
class Selector:
    value: str
    kind: str

    def __post_init__(self) -> None:
        _text(self.value, "selector value")
        if self.kind not in _SELECTOR_KINDS:
            raise ValueError(f"selector kind must be one of {sorted(_SELECTOR_KINDS)}")
        if self.kind == "path":
            _relative(self.value, "path selector", allow_dot=True)

    def to_dict(self) -> dict[str, str]:
        return {"value": self.value, "kind": self.kind}

    @classmethod
    def from_dict(cls, value: Any) -> "Selector":
        if not isinstance(value, dict) or set(value) != {"value", "kind"}:
            raise ValueError("selector has an unsupported shape")
        return cls(**value)


@dataclass(frozen=True)
class ModuleIdentity:
    module_id: str
    name: str
    aliases: tuple[str, ...]
    classification: str
    confidence: str

    def __post_init__(self) -> None:
        _id(self.module_id, "module_id")
        _text(self.name, "module name")
        object.__setattr__(self, "aliases", _list(self.aliases, "module aliases"))
        _id(self.classification, "module classification")
        if self.confidence not in _CONFIDENCE:
            raise ValueError(f"module confidence must be one of {sorted(_CONFIDENCE)}")

    def to_dict(self) -> dict[str, Any]:
        return {"module_id": self.module_id, "name": self.name,
                "aliases": list(self.aliases), "classification": self.classification,
                "confidence": self.confidence}

    @classmethod
    def from_dict(cls, value: Any) -> "ModuleIdentity":
        if not isinstance(value, dict) or set(value) != {
            "module_id", "name", "aliases", "classification", "confidence"
        }:
            raise ValueError("module identity has an unsupported shape")
        return cls(**value)


@dataclass(frozen=True)
class OwnedLocation:
    """Owned implementation location; paths remain repository-relative."""

    repository_ref: str
    root: str
    files: tuple[str, ...] = field(default_factory=tuple)
    symbols: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _reference(self.repository_ref, "owned location repository_ref")
        _relative(self.root, "owned location root", allow_dot=True)
        object.__setattr__(self, "files", tuple(
            _relative(item, "owned location file") for item in self.files))
        object.__setattr__(self, "symbols", _list(self.symbols, "owned location symbols"))
        object.__setattr__(self, "evidence_refs", _refs(
            self.evidence_refs, "owned location evidence_refs"))
        if not self.files and not self.symbols:
            raise ValueError("owned location needs at least one file or symbol")

    def to_dict(self) -> dict[str, Any]:
        return {"repository_ref": self.repository_ref, "root": self.root,
                "files": list(self.files), "symbols": list(self.symbols),
                "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def from_dict(cls, value: Any) -> "OwnedLocation":
        if not isinstance(value, dict) or set(value) != {
            "repository_ref", "root", "files", "symbols", "evidence_refs"
        }:
            raise ValueError("owned location has an unsupported shape")
        return cls(**value)


@dataclass(frozen=True)
class Boundary:
    """Direct, first-order context; it cannot contain neighbor implementation."""

    direction: str
    kind: str
    neighbor_id: str
    repository_ref: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.direction not in _DIRECTIONS:
            raise ValueError(f"boundary direction must be one of {sorted(_DIRECTIONS)}")
        _id(self.kind, "boundary kind")
        _id(self.neighbor_id, "boundary neighbor_id")
        _reference(self.repository_ref, "boundary repository_ref")
        object.__setattr__(self, "evidence_refs", _refs(
            self.evidence_refs, "boundary evidence_refs"))

    def to_dict(self) -> dict[str, Any]:
        return {"direction": self.direction, "kind": self.kind,
                "neighbor_id": self.neighbor_id, "repository_ref": self.repository_ref,
                "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def from_dict(cls, value: Any) -> "Boundary":
        if not isinstance(value, dict) or set(value) != {
            "direction", "kind", "neighbor_id", "repository_ref", "evidence_refs"
        }:
            raise ValueError("boundary has an unsupported shape")
        return cls(**value)


@dataclass(frozen=True)
class ScopeAlternative:
    selector: Selector
    confidence: str
    reason: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.selector, Selector):
            raise ValueError("scope alternative selector must be a Selector")
        if self.confidence not in _CONFIDENCE:
            raise ValueError(f"scope alternative confidence must be one of {sorted(_CONFIDENCE)}")
        _text(self.reason, "scope alternative reason")
        object.__setattr__(self, "evidence_refs", _refs(
            self.evidence_refs, "scope alternative evidence_refs"))

    def to_dict(self) -> dict[str, Any]:
        return {"selector": self.selector.to_dict(), "confidence": self.confidence,
                "reason": self.reason, "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def from_dict(cls, value: Any) -> "ScopeAlternative":
        if not isinstance(value, dict) or set(value) != {
            "selector", "confidence", "reason", "evidence_refs"
        }:
            raise ValueError("scope alternative has an unsupported shape")
        return cls(selector=Selector.from_dict(value["selector"]),
                   confidence=value["confidence"], reason=value["reason"],
                   evidence_refs=tuple(value["evidence_refs"]))


@dataclass(frozen=True)
class ModuleCoverage:
    capabilities: tuple[tuple[str, Coverage], ...]
    limitations: tuple[str, ...] = field(default_factory=tuple)
    unresolved_alternatives: tuple[ScopeAlternative, ...] = field(default_factory=tuple)
    unknowns: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        entries = tuple(self.capabilities)
        if not entries:
            raise ValueError("module coverage needs at least one capability result")
        ids: list[str] = []
        for capability_id, coverage in entries:
            ids.append(_id(capability_id, "coverage capability_id"))
            if not isinstance(coverage, Coverage):
                raise ValueError("coverage entries must contain Coverage values")
        if len(ids) != len(set(ids)):
            raise ValueError("module coverage has duplicate capability IDs")
        object.__setattr__(self, "capabilities", tuple(sorted(entries, key=lambda item: item[0])))
        object.__setattr__(self, "limitations", _list(self.limitations, "coverage limitations"))
        object.__setattr__(self, "unknowns", _list(self.unknowns, "coverage unknowns"))
        object.__setattr__(self, "unresolved_alternatives", tuple(self.unresolved_alternatives))
        if not all(isinstance(item, ScopeAlternative) for item in self.unresolved_alternatives):
            raise ValueError("coverage alternatives must be ScopeAlternative values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capabilities": [
                {"capability_id": capability_id, "coverage": coverage.to_dict()}
                for capability_id, coverage in self.capabilities
            ],
            "limitations": list(self.limitations),
            "unresolved_alternatives": [item.to_dict() for item in self.unresolved_alternatives],
            "unknowns": list(self.unknowns),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ModuleCoverage":
        if not isinstance(value, dict) or set(value) != {
            "capabilities", "limitations", "unresolved_alternatives", "unknowns"
        }:
            raise ValueError("module coverage has an unsupported shape")
        rows = value["capabilities"]
        if not isinstance(rows, list):
            raise ValueError("module coverage capabilities must be a list")
        entries = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"capability_id", "coverage"}:
                raise ValueError("module coverage capability has an unsupported shape")
            payload = row["coverage"]
            if not isinstance(payload, dict):
                raise ValueError("module coverage payload must be an object")
            entries.append((row["capability_id"], Coverage(**payload)))
        alternatives = value["unresolved_alternatives"]
        if not isinstance(alternatives, list):
            raise ValueError("module coverage alternatives must be a list")
        return cls(capabilities=tuple(entries), limitations=tuple(value["limitations"]),
                   unresolved_alternatives=tuple(ScopeAlternative.from_dict(item)
                                                 for item in alternatives),
                   unknowns=tuple(value["unknowns"]))


@dataclass(frozen=True)
class OverviewLineage:
    source_run_id: str
    snapshot_id: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _id(self.source_run_id, "overview source_run_id")
        if not isinstance(self.snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{16}", self.snapshot_id):
            raise ValueError("overview snapshot_id must be a 16-character hex digest")
        object.__setattr__(self, "evidence_refs", _refs(
            self.evidence_refs, "overview lineage evidence_refs"))

    def to_dict(self) -> dict[str, Any]:
        return {"source_run_id": self.source_run_id, "snapshot_id": self.snapshot_id,
                "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def from_dict(cls, value: Any) -> "OverviewLineage":
        if not isinstance(value, dict) or set(value) != {
            "source_run_id", "snapshot_id", "evidence_refs"
        }:
            raise ValueError("overview lineage has an unsupported shape")
        return cls(**value)


@dataclass(frozen=True)
class FindingHint:
    finding_id: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _id(self.finding_id, "finding hint ID")
        object.__setattr__(self, "evidence_refs", _refs(
            self.evidence_refs, "finding hint evidence_refs"))

    def to_dict(self) -> dict[str, Any]:
        return {"finding_id": self.finding_id, "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def from_dict(cls, value: Any) -> "FindingHint":
        if not isinstance(value, dict) or set(value) != {"finding_id", "evidence_refs"}:
            raise ValueError("finding hint has an unsupported shape")
        return cls(**value)


@dataclass(frozen=True)
class ModuleScope:
    """The canonical, source-mode-neutral input to all Module Drill stages."""

    contract_version: str
    source_mode: str
    project: ProjectSnapshot
    selector: Selector
    module: ModuleIdentity
    owned_scope: tuple[OwnedLocation, ...]
    assigned_candidates: tuple[str, ...]
    boundaries: tuple[Boundary, ...]
    coverage: ModuleCoverage
    overview_lineage: OverviewLineage | None = None
    finding_hints: tuple[FindingHint, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.contract_version != MODULE_SCOPE_VERSION:
            raise ValueError(f"unsupported ModuleScope contract version: {self.contract_version!r}")
        if self.source_mode not in _SOURCE_MODES:
            raise ValueError(f"source_mode must be one of {sorted(_SOURCE_MODES)}")
        if not isinstance(self.project, ProjectSnapshot) or not isinstance(self.selector, Selector):
            raise ValueError("module scope needs ProjectSnapshot and Selector values")
        if not isinstance(self.module, ModuleIdentity) or not isinstance(self.coverage, ModuleCoverage):
            raise ValueError("module scope needs ModuleIdentity and ModuleCoverage values")
        object.__setattr__(self, "owned_scope", tuple(self.owned_scope))
        object.__setattr__(self, "boundaries", tuple(self.boundaries))
        object.__setattr__(self, "finding_hints", tuple(self.finding_hints))
        object.__setattr__(self, "assigned_candidates", tuple(
            _id(item, "assigned candidate ID") for item in self.assigned_candidates))
        if len(self.assigned_candidates) != len(set(self.assigned_candidates)):
            raise ValueError("module scope has duplicate assigned candidate IDs")
        if not self.owned_scope or not all(isinstance(item, OwnedLocation) for item in self.owned_scope):
            raise ValueError("module scope needs at least one owned location")
        if not all(isinstance(item, Boundary) for item in self.boundaries):
            raise ValueError("module scope boundaries must contain Boundary values")
        if any(item.neighbor_id == self.module.module_id for item in self.boundaries):
            raise ValueError("a boundary cannot name the owned module as its neighbor")
        if not all(isinstance(item, FindingHint) for item in self.finding_hints):
            raise ValueError("module scope finding_hints must contain FindingHint values")
        if self.source_mode == "overview":
            if self.overview_lineage is None:
                raise ValueError("overview source_mode requires overview_lineage")
            if self.overview_lineage.snapshot_id != self.project.snapshot_id:
                raise ValueError("overview lineage snapshot must match project snapshot")
        elif self.overview_lineage is not None:
            raise ValueError("standalone source_mode must not include overview_lineage")
        known_repos = {item.repository_ref for item in self.project.repositories}
        for location in self.owned_scope:
            if location.repository_ref not in known_repos:
                raise ValueError("owned scope names a repository outside the project snapshot")
        for boundary in self.boundaries:
            if boundary.repository_ref not in known_repos:
                raise ValueError("boundary names a repository outside the project snapshot")

    @property
    def snapshot_id(self) -> str:
        return self.project.snapshot_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "source_mode": self.source_mode,
            "project": self.project.to_dict(),
            "selector": self.selector.to_dict(),
            "module": self.module.to_dict(),
            "owned_scope": [item.to_dict() for item in self.owned_scope],
            "assigned_candidates": list(self.assigned_candidates),
            "boundaries": [item.to_dict() for item in self.boundaries],
            "coverage": self.coverage.to_dict(),
            "overview_lineage": (self.overview_lineage.to_dict()
                                 if self.overview_lineage else None),
            "finding_hints": [item.to_dict() for item in self.finding_hints],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ModuleScope":
        fields = {
            "contract_version", "source_mode", "project", "selector", "module",
            "owned_scope", "assigned_candidates", "boundaries", "coverage",
            "overview_lineage", "finding_hints",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("module scope has an unsupported shape")
        for list_field in ("owned_scope", "assigned_candidates", "boundaries", "finding_hints"):
            if not isinstance(value[list_field], list):
                raise ValueError(f"module scope {list_field} must be a list")
        lineage = value["overview_lineage"]
        if lineage is not None and not isinstance(lineage, dict):
            raise ValueError("module scope overview_lineage must be an object or null")
        return cls(
            contract_version=value["contract_version"], source_mode=value["source_mode"],
            project=ProjectSnapshot.from_dict(value["project"]),
            selector=Selector.from_dict(value["selector"]),
            module=ModuleIdentity.from_dict(value["module"]),
            owned_scope=tuple(OwnedLocation.from_dict(item) for item in value["owned_scope"]),
            assigned_candidates=tuple(value["assigned_candidates"]),
            boundaries=tuple(Boundary.from_dict(item) for item in value["boundaries"]),
            coverage=ModuleCoverage.from_dict(value["coverage"]),
            overview_lineage=OverviewLineage.from_dict(lineage) if lineage else None,
            finding_hints=tuple(FindingHint.from_dict(item) for item in value["finding_hints"]),
        )


@dataclass(frozen=True)
class ModuleScopeRequest:
    """Shared input passed to either normalized scope provider."""

    source_mode: str
    project: ProjectSnapshot
    selector: Selector

    def __post_init__(self) -> None:
        if self.source_mode not in _SOURCE_MODES:
            raise ValueError(f"source_mode must be one of {sorted(_SOURCE_MODES)}")
        if not isinstance(self.project, ProjectSnapshot) or not isinstance(self.selector, Selector):
            raise ValueError("scope request needs ProjectSnapshot and Selector values")


class ScopeResolutionError(ValueError):
    """A deliberate refusal: no unique evidence-backed module scope exists."""

    def __init__(self, code: str, message: str,
                 alternatives: tuple[ScopeAlternative, ...] = ()) -> None:
        self.code = _id(code, "scope resolution error code")
        self.alternatives = tuple(alternatives)
        if not all(isinstance(item, ScopeAlternative) for item in self.alternatives):
            raise ValueError("scope resolution alternatives must be ScopeAlternative values")
        super().__init__(message)


@runtime_checkable
class ScopeProvider(Protocol):
    """Source-specific resolver; providers return the same ModuleScope shape."""

    source_mode: str

    def resolve(self, request: ModuleScopeRequest) -> ModuleScope: ...


def resolve_scope(provider: ScopeProvider, request: ModuleScopeRequest) -> ModuleScope:
    """Run and cross-check a source provider at the narrow provider boundary."""
    # Runtime-checkable protocols carrying data attributes cannot safely use
    # isinstance() on every supported Python version. Validate the two actual
    # interface members explicitly instead.
    if not hasattr(provider, "source_mode") or not callable(getattr(provider, "resolve", None)):
        raise ValueError("scope provider must declare source_mode and resolve(request)")
    if provider.source_mode != request.source_mode:
        raise ValueError("scope provider source_mode does not match request")
    scope = provider.resolve(request)
    if not isinstance(scope, ModuleScope):
        raise ValueError("scope provider returned an invalid ModuleScope")
    if scope.source_mode != request.source_mode:
        raise ValueError("scope provider returned a scope with the wrong source_mode")
    if scope.project != request.project or scope.selector != request.selector:
        raise ValueError("scope provider returned a scope for a different project or selector")
    return scope


def write_scope(path: str | Path, scope: ModuleScope) -> Path:
    """Persist one immutable ModuleScope artifact; callers cannot overwrite it."""
    if not isinstance(scope, ModuleScope):
        raise ValueError("write_scope requires a ModuleScope")
    destination = Path(path)
    write_new_text(destination, json.dumps(scope.to_dict(), indent=2, sort_keys=True) + "\n")
    return destination


def load_scope(path: str | Path) -> ModuleScope:
    """Load and validate the canonical JSON scope without reading Markdown."""
    source = Path(path)
    try:
        value = json.loads(source.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load ModuleScope from {source.name}: {exc}") from exc
    return ModuleScope.from_dict(value)
