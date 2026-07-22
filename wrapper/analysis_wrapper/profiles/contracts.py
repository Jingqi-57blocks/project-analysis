"""Minimal contracts shared by bundled profiles and capability providers.

The contracts intentionally carry data and trusted, already-imported provider
objects only.  Detection semantics and canonical evidence shapes are introduced
by later architecture stages; this module does not implement a rule language.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

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


def _json_safe(value: Any, label: str) -> None:
    try:
        json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain JSON-safe data: {exc}") from exc


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
    capability_id: str
    provider_id: str
    repo_id: str
    facts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    coverage: dict[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[ArtifactRef, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _validated_id(self.capability_id, "capability_id")
        _validated_id(self.provider_id, "provider_id")
        _validated_id(self.repo_id, "repo_id")
        object.__setattr__(self, "facts", tuple(self.facts))
        object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))
        if not all(isinstance(item, dict) for item in self.facts):
            raise ValueError("capability facts must be JSON object values")
        if not isinstance(self.coverage, dict):
            raise ValueError("capability coverage must be a JSON object")
        if not all(isinstance(item, ArtifactRef) for item in self.artifact_refs):
            raise ValueError("artifact_refs must contain ArtifactRef values")
        _json_safe(self.facts, "capability facts")
        _json_safe(self.coverage, "capability coverage")


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
    targets: "TargetSpec"
    output_dir: Path
    scan_date: str
    network_authorized: bool
    provenance: dict[str, Any]
    tool_access: ToolAccess


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
