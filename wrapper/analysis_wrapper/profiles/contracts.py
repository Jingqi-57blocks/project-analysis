"""Minimal contracts shared by bundled profiles and capability providers.

The contracts intentionally carry data and trusted, already-imported provider
objects only; this module does not implement a rule language. Detection
semantics live in :mod:`analysis_wrapper.profiles.detection`; the canonical
evidence shapes a :class:`CapabilityResult` carries (``Coverage``/``Fact``)
live in :mod:`analysis_wrapper.evidence` (57B-79), imported here rather than
duplicated so profiles and providers share one vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..evidence.coverage import Coverage
from ..evidence.facts import Fact
from ..identity import IdentityMap

if TYPE_CHECKING:
    from ..executor import SignalResult
    from ..targetspec import RepoTarget, TargetSpec


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FINGERPRINT_KINDS = {
    "config-file",
    "fallback",
    "go-require",
    "manifest-default",
    "manifest-file",
    "package-dependency",
    "source-extension",
}


def _validated_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(
            f"{label} must use 1-128 letters, digits, dot, underscore, or hyphen"
        )
    return value


@dataclass(frozen=True)
class Fingerprint:
    """One data-only technology observation descriptor.

    ``kind`` selects a bundled detector; ``value`` is a literal path, manifest
    key, configuration marker, or analyzer-owned rule ID.  Neither field is
    executable and no callback is accepted.
    """

    kind: str
    value: str

    def __post_init__(self) -> None:
        _validated_id(self.kind, "fingerprint kind")
        if self.kind not in FINGERPRINT_KINDS:
            raise ValueError(f"unsupported fingerprint kind {self.kind!r}")
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("fingerprint value must be a non-empty string")


@dataclass(frozen=True)
class Profile:
    profile_id: str
    kind: str
    display_name: str
    fingerprints: tuple[Fingerprint, ...]
    capability_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validated_id(self.profile_id, "profile_id")
        _validated_id(self.kind, "profile kind")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError(f"profile {self.profile_id!r} needs a display name")
        object.__setattr__(self, "fingerprints", tuple(self.fingerprints))
        object.__setattr__(self, "capability_ids", tuple(self.capability_ids))
        if not self.fingerprints:
            raise ValueError(f"profile {self.profile_id!r} needs at least one fingerprint")
        if not all(isinstance(item, Fingerprint) for item in self.fingerprints):
            raise ValueError("profile fingerprints must be Fingerprint values")
        for capability_id in self.capability_ids:
            _validated_id(capability_id, "capability_id")
        if len(set(self.capability_ids)) != len(self.capability_ids):
            raise ValueError(f"profile {self.profile_id!r} has duplicate capability IDs")


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    kind: str = "artifact"

    def __post_init__(self) -> None:
        _validated_id(self.kind, "artifact kind")
        path = Path(self.path)
        if not self.path or path.is_absolute() or ".." in path.parts:
            raise ValueError("artifact path must be non-empty and relative to the run")


@dataclass(frozen=True)
class CapabilityResult:
    """One provider's evidence for one repository target.

    ``coverage`` and ``facts`` carry the canonical evidence types from
    :mod:`analysis_wrapper.evidence` (see 57B-79) rather than raw dicts, so a
    result's applicability/status and its individual facts are validated and
    JSON-safe by construction. ``facet_provenance`` records which detected
    technology facet(s) (:class:`~analysis_wrapper.targetspec.TechnologyFacet`
    profile IDs) produced or affect this result — informational lineage, not
    part of the result's own identity.
    """

    capability_id: str
    provider_id: str
    repo_id: str
    coverage: Coverage
    facts: tuple[Fact, ...] = field(default_factory=tuple)
    artifact_refs: tuple[ArtifactRef, ...] = field(default_factory=tuple)
    facet_provenance: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _validated_id(self.capability_id, "capability_id")
        _validated_id(self.provider_id, "provider_id")
        _validated_id(self.repo_id, "repo_id")
        object.__setattr__(self, "facts", tuple(self.facts))
        object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))
        object.__setattr__(self, "facet_provenance", tuple(self.facet_provenance))
        if not isinstance(self.coverage, Coverage):
            raise ValueError("capability coverage must be a Coverage value")
        if not all(isinstance(item, Fact) for item in self.facts):
            raise ValueError("capability facts must be Fact values")
        if not all(isinstance(item, ArtifactRef) for item in self.artifact_refs):
            raise ValueError("artifact_refs must contain ArtifactRef values")
        for profile_id in self.facet_provenance:
            _validated_id(profile_id, "facet_provenance entry")


@runtime_checkable
class ToolAccess(Protocol):
    """The only external-tool execution surface available to providers."""

    def execute(
        self,
        tool_id: str,
        target: "RepoTarget",
        *,
        signal_id: str = "",
    ) -> "SignalResult": ...


@dataclass(frozen=True)
class RunContext:
    """Everything one provider run needs for one (provider, target) pair.

    ``identities`` gives a provider its only path to the run's IdentityMap:
    a provider receives just this context plus its ``RepoTarget`` and cannot
    reach the map any other way. Use ``context.identities.reference_for(
    target.repo_id)`` for a target's human-readable ``repository_ref`` (e.g.
    in a ``SourceRef``) and ``context.identities.artifact_key_for(...)`` for
    an artifact filename — never derive either from ``target.path``'s
    basename, which collides across duplicate-basename workspaces (57B-86).
    Optional and defaulted to ``None`` so existing identity-less unit
    contexts keep constructing unchanged.
    """

    targets: "TargetSpec"
    output_dir: Path
    scan_date: str
    network_authorized: bool
    provenance: dict[str, Any]
    tool_access: ToolAccess
    identities: "IdentityMap | None" = None

    def __post_init__(self) -> None:
        if self.identities is not None and not isinstance(self.identities, IdentityMap):
            raise ValueError("RunContext identities must be an IdentityMap or None")


@runtime_checkable
class CapabilityProvider(Protocol):
    provider_id: str
    capability_id: str
    profile_ids: tuple[str, ...]

    def run(self, context: RunContext, target: "RepoTarget") -> CapabilityResult: ...


def run_provider(
    provider: CapabilityProvider,
    context: RunContext,
    target: "RepoTarget",
) -> CapabilityResult:
    """Invoke an already-bundled provider and validate its result identity."""
    result = provider.run(context, target)
    if not isinstance(result, CapabilityResult):
        raise ValueError(f"provider {provider.provider_id!r} returned an invalid result")
    if result.provider_id != provider.provider_id:
        raise ValueError("capability result provider_id does not match its provider")
    if result.capability_id != provider.capability_id:
        raise ValueError("capability result capability_id does not match its provider")
    if result.repo_id != target.repo_id:
        raise ValueError("capability result repo_id does not match its target")
    return result
