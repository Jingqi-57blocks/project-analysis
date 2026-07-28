"""Canonical evidence-source contracts for Module Drill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .coverage import Coverage
from .validation import ContractError, enum, exact_object, mapping, sha256, slug, string_list, text, unique_ids

SOURCE_MANIFEST_VERSION = "source-manifest/v1"
SOURCE_MODES = frozenset({"overview-backed", "standalone"})
ARTIFACT_KINDS = frozenset({"canonical", "fragment", "index", "view"})
INTEGRITY_STATES = frozenset({"verified", "missing", "corrupt", "stale"})


@dataclass(frozen=True)
class RepositorySnapshot:
    repository_ref: str
    revision: str
    dirty_state: str

    def __post_init__(self) -> None:
        text(self.repository_ref, "repository_ref")
        text(self.revision, "revision")
        enum(self.dirty_state, {"clean", "dirty", "non-git"}, "dirty_state")

    def to_dict(self) -> dict[str, str]:
        return {"repository_ref": self.repository_ref, "revision": self.revision,
                "dirty_state": self.dirty_state}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "RepositorySnapshot":
        row = exact_object(value, {"repository_ref", "revision", "dirty_state"}, label)
        return cls(text(row["repository_ref"], f"{label}.repository_ref"),
                   text(row["revision"], f"{label}.revision"),
                   enum(row["dirty_state"], {"clean", "dirty", "non-git"},
                        f"{label}.dirty_state"))


@dataclass(frozen=True)
class ToolIdentity:
    tool_id: str
    version: str

    def __post_init__(self) -> None:
        slug(self.tool_id, "tool_id")
        text(self.version, "tool version")

    def to_dict(self) -> dict[str, str]:
        return {"tool_id": self.tool_id, "version": self.version}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "ToolIdentity":
        row = exact_object(value, {"tool_id", "version"}, label)
        return cls(slug(row["tool_id"], f"{label}.tool_id"),
                   text(row["version"], f"{label}.version"))


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    relative_path: str
    schema_version: str
    digest: str
    kind: str
    integrity: str

    def __post_init__(self) -> None:
        slug(self.artifact_id, "artifact_id")
        if not isinstance(self.relative_path, str) or not self.relative_path or self.relative_path.startswith("/") \
                or ".." in self.relative_path.split("/"):
            raise ContractError("artifact relative_path must be a safe non-empty relative path")
        text(self.schema_version, "artifact schema_version")
        sha256(self.digest, "artifact digest")
        enum(self.kind, ARTIFACT_KINDS, "artifact kind")
        enum(self.integrity, INTEGRITY_STATES, "artifact integrity")

    @property
    def authoritative(self) -> bool:
        return self.kind in {"canonical", "fragment"} and self.integrity == "verified"

    def to_dict(self) -> dict[str, str]:
        return {"artifact_id": self.artifact_id, "relative_path": self.relative_path,
                "schema_version": self.schema_version, "digest": self.digest,
                "kind": self.kind, "integrity": self.integrity}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "ArtifactRecord":
        row = exact_object(value, {"artifact_id", "relative_path", "schema_version", "digest", "kind", "integrity"}, label)
        return cls(slug(row["artifact_id"], f"{label}.artifact_id"),
                   text(row["relative_path"], f"{label}.relative_path"),
                   text(row["schema_version"], f"{label}.schema_version"),
                   sha256(row["digest"], f"{label}.digest"),
                   enum(row["kind"], ARTIFACT_KINDS, f"{label}.kind"),
                   enum(row["integrity"], INTEGRITY_STATES, f"{label}.integrity"))


@dataclass(frozen=True)
class ProviderOutcome:
    provider_id: str
    capability_id: str
    coverage: Coverage
    artifact_ids: tuple[str, ...]
    truncation: tuple[str, ...]
    unsupported: tuple[str, ...]

    def __post_init__(self) -> None:
        slug(self.provider_id, "provider_id")
        slug(self.capability_id, "capability_id")

    def to_dict(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "capability_id": self.capability_id,
                "coverage": self.coverage.to_dict(), "artifact_ids": list(self.artifact_ids),
                "truncation": list(self.truncation), "unsupported": list(self.unsupported)}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "ProviderOutcome":
        row = exact_object(value, {"provider_id", "capability_id", "coverage", "artifact_ids", "truncation", "unsupported"}, label)
        return cls(
            provider_id=slug(row["provider_id"], f"{label}.provider_id"),
            capability_id=slug(row["capability_id"], f"{label}.capability_id"),
            coverage=Coverage.from_dict(row["coverage"], f"{label}.coverage"),
            artifact_ids=string_list(row["artifact_ids"], f"{label}.artifact_ids", allow_empty=True),
            truncation=string_list(row["truncation"], f"{label}.truncation", allow_empty=True),
            unsupported=string_list(row["unsupported"], f"{label}.unsupported", allow_empty=True),
        )


@dataclass(frozen=True)
class SourceManifest:
    source_mode: str
    snapshot_id: str
    repositories: tuple[RepositorySnapshot, ...]
    preparation_options: dict[str, Any]
    tools: tuple[ToolIdentity, ...]
    artifacts: tuple[ArtifactRecord, ...]
    providers: tuple[ProviderOutcome, ...]

    @property
    def repository_refs(self) -> frozenset[str]:
        """Stable repository universe available to this recovery run."""
        return frozenset(item.repository_ref for item in self.repositories)

    def __post_init__(self) -> None:
        enum(self.source_mode, SOURCE_MODES, "source_mode")
        sha256(self.snapshot_id, "snapshot_id")
        if not self.repositories:
            raise ContractError("source manifest must identify at least one repository")
        unique_ids((item.repository_ref for item in self.repositories), "source manifest repositories")
        unique_ids((item.tool_id for item in self.tools), "source manifest tools")
        unique_ids((item.artifact_id for item in self.artifacts), "source manifest artifacts")
        unique_ids((item.provider_id for item in self.providers), "source manifest providers")
        by_id = {item.artifact_id: item for item in self.artifacts}
        for provider in self.providers:
            if provider.coverage.applicability == "applicable" \
                    and provider.coverage.status == "complete" and not provider.artifact_ids:
                raise ContractError(
                    f"complete applicable provider {provider.provider_id} requires canonical evidence")
            for artifact_id in provider.artifact_ids:
                artifact = by_id.get(artifact_id)
                if artifact is None:
                    raise ContractError(
                        f"provider {provider.provider_id} references unknown artifact {artifact_id!r}")
                if not artifact.authoritative:
                    raise ContractError(
                        f"provider {provider.provider_id} cannot treat {artifact.kind} artifact "
                        f"{artifact_id!r} as canonical evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SOURCE_MANIFEST_VERSION,
            "source_mode": self.source_mode,
            "snapshot_id": self.snapshot_id,
            "repositories": [item.to_dict() for item in self.repositories],
            "preparation_options": self.preparation_options,
            "tools": [item.to_dict() for item in self.tools],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "providers": [item.to_dict() for item in self.providers],
        }

    @classmethod
    def from_dict(cls, value: Any, label: str = "source manifest") -> "SourceManifest":
        row = exact_object(value, {
            "schema_version", "source_mode", "snapshot_id", "repositories",
            "preparation_options", "tools", "artifacts", "providers",
        }, label)
        if row["schema_version"] != SOURCE_MANIFEST_VERSION:
            raise ContractError(f"{label}.schema_version must be {SOURCE_MANIFEST_VERSION!r}")
        if not isinstance(row["repositories"], list) or not isinstance(row["tools"], list) \
                or not isinstance(row["artifacts"], list) or not isinstance(row["providers"], list):
            raise ContractError(f"{label} repositories/tools/artifacts/providers must be lists")
        return cls(
            source_mode=enum(row["source_mode"], SOURCE_MODES, f"{label}.source_mode"),
            snapshot_id=sha256(row["snapshot_id"], f"{label}.snapshot_id"),
            repositories=tuple(RepositorySnapshot.from_dict(item, f"{label}.repositories[{index}]")
                               for index, item in enumerate(row["repositories"])),
            preparation_options=dict(mapping(row["preparation_options"], f"{label}.preparation_options")),
            tools=tuple(ToolIdentity.from_dict(item, f"{label}.tools[{index}]")
                        for index, item in enumerate(row["tools"])),
            artifacts=tuple(ArtifactRecord.from_dict(item, f"{label}.artifacts[{index}]")
                            for index, item in enumerate(row["artifacts"])),
            providers=tuple(ProviderOutcome.from_dict(item, f"{label}.providers[{index}]")
                            for index, item in enumerate(row["providers"])),
        )
