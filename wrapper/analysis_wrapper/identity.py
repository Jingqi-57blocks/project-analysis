"""Per-run project and repository identity mapping.

Internal IDs remain stable join keys.  Human-readable names and references are
derived once from canonical paths and consumed through this module; callers
must not guess names by stripping hash-looking suffixes.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from .executor import write_new_text
from .targetspec import TargetSpec, stable_repo_id


FILENAME = "identity-map.json"
SCHEMA_VERSION = 1
_SOURCE_NATIVE = "native"
_SOURCE_LEGACY = "legacy-derived"
_PROJECT_FIELDS = {
    "internal_id", "display_name", "reference", "artifact_key", "canonical_path",
}
_REPOSITORY_FIELDS = {
    "internal_id", "display_name", "reference", "artifact_key",
    "workspace_relative_path", "canonical_path",
}
_PORTABLE_ILLEGAL = frozenset('<>:"/\\|?*%')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} contains control characters")
    return value


def _canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def artifact_key(reference: str, *, encode_all: bool = False) -> str:
    """Encode a readable reference as one reversible portable path segment."""
    value = _require_text(reference, "identity reference")
    encoded: list[str] = []
    last = len(value) - 1
    for index, char in enumerate(value):
        unsafe = encode_all or (
            char in _PORTABLE_ILLEGAL
            or ord(char) < 32
            or ord(char) == 127
            or (index == last and char in {" ", "."})
        )
        if unsafe:
            encoded.extend(f"%{byte:02X}" for byte in char.encode("utf-8"))
        else:
            encoded.append(char)
    result = "".join(encoded)
    stem = result.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        first = result[0]
        replacement = "".join(f"%{byte:02X}" for byte in first.encode("utf-8"))
        result = replacement + result[1:]
    if result in {".", ".."}:
        result = "".join(f"%{ord(char):02X}" for char in result)
    return result


def decode_artifact_key(value: str) -> str:
    """Reverse :func:`artifact_key`, rejecting malformed percent escapes."""
    key = _require_text(value, "artifact key")
    index = 0
    while index < len(key):
        if key[index] != "%":
            index += 1
            continue
        if index + 2 >= len(key) or any(
            char not in "0123456789abcdefABCDEF" for char in key[index + 1:index + 3]
        ):
            raise ValueError(f"artifact key has a malformed percent escape: {value!r}")
        index += 3
    try:
        return unquote(key, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"artifact key contains invalid UTF-8 escapes: {value!r}") from exc


@dataclass(frozen=True)
class ProjectIdentity:
    internal_id: str
    display_name: str
    reference: str
    artifact_key: str
    canonical_path: str


@dataclass(frozen=True)
class RepositoryIdentity:
    internal_id: str
    display_name: str
    reference: str
    artifact_key: str
    workspace_relative_path: str
    canonical_path: str


@dataclass(frozen=True)
class IdentityMap:
    project: ProjectIdentity
    repositories: tuple[RepositoryIdentity, ...]
    source: str = _SOURCE_NATIVE
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("identity map has an unsupported schema version")
        if self.source not in {_SOURCE_NATIVE, _SOURCE_LEGACY}:
            raise ValueError("identity map source must be native or legacy-derived")
        object.__setattr__(self, "repositories", tuple(self.repositories))
        internal_ids = [item.internal_id for item in self.repositories]
        references = [item.reference for item in self.repositories]
        artifact_keys = [item.artifact_key for item in self.repositories]
        if len(internal_ids) != len(set(internal_ids)):
            raise ValueError("identity map has duplicate repository internal IDs")
        if len(references) != len(set(references)):
            raise ValueError("identity map has duplicate repository references")
        if len(artifact_keys) != len(set(artifact_keys)):
            raise ValueError("identity map has duplicate repository artifact keys")
        if len(artifact_keys) != len({key.casefold() for key in artifact_keys}):
            raise ValueError(
                "identity map has repository artifact keys that collide "
                "on a case-insensitive filesystem"
            )
        if self.project.artifact_key != artifact_key(self.project.reference):
            raise ValueError("project artifact key does not match its reference")
        if decode_artifact_key(self.project.artifact_key) != self.project.reference:
            raise ValueError("project artifact key is not reversible")
        for item in self.repositories:
            if item.artifact_key not in {
                artifact_key(item.reference), artifact_key(item.reference, encode_all=True)
            }:
                raise ValueError(
                    f"repository {item.internal_id!r} artifact key does not match its reference"
                )
            if decode_artifact_key(item.artifact_key) != item.reference:
                raise ValueError(
                    f"repository {item.internal_id!r} artifact key is not reversible"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "project": asdict(self.project),
            "repositories": [asdict(item) for item in self.repositories],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def repository(self, internal_id: str) -> RepositoryIdentity:
        for item in self.repositories:
            if item.internal_id == internal_id:
                return item
        raise KeyError(f"unknown repository internal ID {internal_id!r}")


def _shortest_unique_references(
    relative_paths: dict[str, PurePosixPath],
) -> dict[str, str]:
    by_basename: dict[str, list[str]] = {}
    for internal_id, relative in relative_paths.items():
        basename = relative.name
        by_basename.setdefault(basename, []).append(internal_id)

    references: dict[str, str] = {}
    for basename, internal_ids in sorted(by_basename.items()):
        if len(internal_ids) == 1:
            references[internal_ids[0]] = basename
            continue
        parts_by_id = {
            internal_id: relative_paths[internal_id].parts
            for internal_id in internal_ids
        }
        max_depth = max(len(parts) for parts in parts_by_id.values())
        for depth in range(2, max_depth + 1):
            candidates = {
                internal_id: "/".join(parts[-depth:])
                for internal_id, parts in parts_by_id.items()
            }
            if len(set(candidates.values())) == len(candidates):
                references.update(candidates)
                break
        else:
            raise ValueError(
                f"cannot derive unique repository references for basename {basename!r}"
            )
    return references


def _portable_artifact_keys(references: dict[str, str]) -> dict[str, str]:
    base = {internal_id: artifact_key(reference)
            for internal_id, reference in references.items()}
    by_casefold: dict[str, list[str]] = {}
    for internal_id, key in base.items():
        by_casefold.setdefault(key.casefold(), []).append(internal_id)
    result = dict(base)
    for internal_ids in by_casefold.values():
        if len(internal_ids) < 2:
            continue
        for internal_id in internal_ids:
            result[internal_id] = artifact_key(
                references[internal_id], encode_all=True)
    if len(result) != len({key.casefold() for key in result.values()}):
        raise ValueError("cannot derive portable unique repository artifact keys")
    return result


def build(
    spec: TargetSpec,
    *,
    workspace_root: str | Path,
    project_id: str,
    source: str = _SOURCE_NATIVE,
) -> IdentityMap:
    workspace = _canonical(workspace_root)
    project_name = _require_text(workspace.name, "project display name")
    reference_paths: dict[str, PurePosixPath] = {}
    workspace_relatives: dict[str, str] = {}
    display_names: dict[str, str] = {}
    canonical_paths: dict[str, Path] = {}
    for target in spec.repos:
        path = _canonical(target.path)
        try:
            relative = path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(
                f"repository {target.repo_id!r} is outside workspace scope {workspace}"
            ) from exc
        workspace_relative = relative.as_posix()
        reference_path = PurePosixPath(path.name if relative == Path(".") else workspace_relative)
        if not reference_path.name:
            raise ValueError(f"repository {target.repo_id!r} has no readable basename")
        reference_paths[target.repo_id] = reference_path
        workspace_relatives[target.repo_id] = workspace_relative
        display_names[target.repo_id] = path.name
        canonical_paths[target.repo_id] = path

    references = _shortest_unique_references(reference_paths)
    artifact_keys = _portable_artifact_keys(references)
    repositories = tuple(
        RepositoryIdentity(
            internal_id=target.repo_id,
            display_name=display_names[target.repo_id],
            reference=references[target.repo_id],
            artifact_key=artifact_keys[target.repo_id],
            workspace_relative_path=workspace_relatives[target.repo_id],
            canonical_path=str(canonical_paths[target.repo_id]),
        )
        for target in sorted(spec.repos, key=lambda item: references[item.repo_id])
    )
    result = IdentityMap(
        schema_version=SCHEMA_VERSION,
        source=source,
        project=ProjectIdentity(
            internal_id=_require_text(project_id, "project internal ID"),
            display_name=project_name,
            reference=project_name,
            artifact_key=artifact_key(project_name),
            canonical_path=str(workspace),
        ),
        repositories=repositories,
    )
    validate_against(result, spec)
    return result


def _strict_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} must contain exactly {sorted(fields)}")
    return value


def from_dict(value: Any) -> IdentityMap:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "source", "project", "repositories"
    }:
        raise ValueError("identity map has an unsupported shape")
    project_raw = _strict_object(value["project"], _PROJECT_FIELDS, "project identity")
    repositories_raw = value["repositories"]
    if not isinstance(repositories_raw, list):
        raise ValueError("identity map repositories must be a list")
    project = ProjectIdentity(**{
        key: _require_text(project_raw[key], f"project.{key}")
        for key in _PROJECT_FIELDS
    })
    repositories = tuple(
        RepositoryIdentity(**{
            key: _require_text(
                _strict_object(row, _REPOSITORY_FIELDS, f"repositories[{index}]")[key],
                f"repositories[{index}].{key}",
            )
            for key in _REPOSITORY_FIELDS
        })
        for index, row in enumerate(repositories_raw)
    )
    return IdentityMap(
        schema_version=value["schema_version"],
        source=_require_text(value["source"], "identity map source"),
        project=project,
        repositories=repositories,
    )


def validate_against(mapping: IdentityMap, spec: TargetSpec) -> None:
    expected = {target.repo_id: str(_canonical(target.path)) for target in spec.repos}
    observed = {item.internal_id: item.canonical_path for item in mapping.repositories}
    if set(observed) != set(expected):
        missing = sorted(set(expected) - set(observed))
        extra = sorted(set(observed) - set(expected))
        raise ValueError(
            f"identity map repository set differs from TargetSpec; missing={missing}, extra={extra}"
        )
    changed = sorted(
        internal_id for internal_id in expected
        if str(_canonical(observed[internal_id])) != expected[internal_id]
    )
    if changed:
        raise ValueError(
            "identity map repository paths differ from TargetSpec: " + ", ".join(changed)
        )


def write(run_dir: str | Path, spec: TargetSpec, report: dict[str, Any]) -> Path:
    mapping = build(
        spec,
        workspace_root=report.get("workspace_root", ""),
        project_id=report.get("project_id", ""),
    )
    return write_mapping(run_dir, mapping)


def write_mapping(run_dir: str | Path, mapping: IdentityMap) -> Path:
    path = Path(run_dir).expanduser().resolve() / FILENAME
    write_new_text(path, mapping.to_json())
    return path


def _legacy_workspace(run: Path, spec: TargetSpec, discovery: dict[str, Any]) -> Path:
    recorded = discovery.get("workspace_root")
    if isinstance(recorded, str) and recorded:
        return _canonical(recorded)
    paths = [str(_canonical(target.path)) for target in spec.repos]
    if not paths:
        raise ValueError("cannot derive legacy identity mapping without repositories")
    return Path(os.path.commonpath(paths))


def derive_legacy(run_dir: str | Path) -> IdentityMap:
    run = Path(run_dir).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    try:
        discovery = json.loads((run / "discovery-report.json").read_text("utf-8"))
    except (OSError, ValueError):
        discovery = {}
    try:
        state = json.loads((run / "run-state.json").read_text("utf-8"))
    except (OSError, ValueError):
        state = {}
    workspace = _legacy_workspace(run, spec, discovery)
    project_id = discovery.get("project_id") or state.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        project_id = stable_repo_id(str(workspace))
    return build(
        spec,
        workspace_root=workspace,
        project_id=project_id,
        source=_SOURCE_LEGACY,
    )


def load(run_dir: str | Path) -> IdentityMap:
    run = Path(run_dir).expanduser().resolve()
    path = run / FILENAME
    if not path.is_file():
        return derive_legacy(run)
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {FILENAME}: {exc}") from exc
    mapping = from_dict(value)
    spec = TargetSpec.load(run / "targets.json")
    try:
        discovery = json.loads((run / "discovery-report.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"native {FILENAME} requires a readable discovery-report.json: {exc}"
        ) from exc
    expected = build(
        spec,
        workspace_root=discovery.get("workspace_root", ""),
        project_id=discovery.get("project_id", ""),
    )
    if mapping != expected:
        raise ValueError(f"{FILENAME} differs from deterministic discovery identity")
    return mapping
