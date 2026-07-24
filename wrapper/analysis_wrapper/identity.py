"""Per-run project and repository identity mapping.

Internal IDs remain stable join keys.  Human-readable names and references are
derived once from canonical paths and consumed through this module; callers
must not guess names by stripping hash-looking suffixes.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from .executor import write_new_text
from .targetspec import TargetSpec, stable_repo_id


FILENAME = "identity-map.json"
SCHEMA_VERSION = 1
_SOURCE_NATIVE = "native"
# In-memory-only source for IdentityMap instances derived by derive_legacy()
# (57B-83 C2). Never written to identity-map.json: write_mapping()/build()'s
# default keep producing "native", and load()'s own equality-with-a-freshly
# -built-expected-map check would reject an on-disk file claiming this source
# anyway (the freshly built comparison map always defaults to "native").
_SOURCE_LEGACY_DERIVED = "legacy-derived"
_VALID_SOURCES = {_SOURCE_NATIVE, _SOURCE_LEGACY_DERIVED}
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


def claim_project_namespace(output_root: str | Path, mapping: IdentityMap) -> str:
    """Atomically claim a readable local namespace for one workspace.

    The basename stays unchanged in the ordinary case. If another workspace
    with the same basename already owns that namespace, prepend the shortest
    readable parent suffix needed to distinguish it. No path hash is exposed.
    """
    root = _canonical(output_root)
    root.mkdir(parents=True, exist_ok=True)
    registry = root / ".project-identities"
    registry.mkdir(exist_ok=True)
    project_path = _canonical(mapping.project.canonical_path)
    parts = [part for part in project_path.parts if part not in {project_path.anchor, ""}]

    def claim_owner(claim_dir: Path) -> IdentityMap | None:
        marker = claim_dir / FILENAME
        try:
            return from_dict(json.loads(marker.read_text("utf-8")))
        except (OSError, ValueError):
            return None

    for depth in range(1, len(parts) + 1):
        reference = "/".join(parts[-depth:])
        candidate = artifact_key(reference)
        if len(candidate.encode("utf-8")) > 240:
            continue
        namespace = root / candidate
        claim_dir = registry / candidate
        if claim_dir.exists():
            owner = claim_owner(claim_dir)
            if owner is not None \
                    and _canonical(owner.project.canonical_path) == project_path:
                namespace.mkdir(exist_ok=True)
                return candidate
            continue

        # A namespace created before the atomic registry existed is accepted
        # only when every readable run identity belongs to this workspace.
        existing_maps = sorted(namespace.glob(f"overview/*/{FILENAME}"))
        owners: list[Path | None] = []
        for path in existing_maps:
            try:
                existing = from_dict(json.loads(path.read_text("utf-8")))
            except (OSError, ValueError):
                owners.append(None)
                continue
            owners.append(_canonical(existing.project.canonical_path))
        if namespace.exists() and (not owners or any(owner != project_path
                                                      for owner in owners)):
            continue

        staging = Path(tempfile.mkdtemp(prefix=f".{candidate}.claim-", dir=registry))
        try:
            write_mapping(staging, mapping)
            os.rename(staging, claim_dir)
            namespace.mkdir(exist_ok=True)
            return candidate
        except OSError:
            if staging.exists():
                shutil.rmtree(staging)
            owner = claim_owner(claim_dir)
            if owner is not None \
                    and _canonical(owner.project.canonical_path) == project_path:
                namespace.mkdir(exist_ok=True)
                return candidate
    raise ValueError(
        "cannot claim a collision-free readable project namespace; "
        "move the workspace or choose a different parent path"
    )


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
        if self.source not in _VALID_SOURCES:
            raise ValueError("identity map has an unsupported source")
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

    def repository_by_reference(self, reference: str) -> RepositoryIdentity:
        for item in self.repositories:
            if item.reference == reference:
                return item
        raise KeyError(f"unknown repository reference {reference!r}")

    def repository_by_artifact_key(self, key: str) -> RepositoryIdentity:
        for item in self.repositories:
            if item.artifact_key == key:
                return item
        raise KeyError(f"unknown repository artifact key {key!r}")

    def reference_for(self, internal_id: str) -> str:
        return self.repository(internal_id).reference

    def artifact_key_for(self, internal_id: str) -> str:
        return self.repository(internal_id).artifact_key

    def internal_id_for(self, reference: str) -> str:
        return self.repository_by_reference(reference).internal_id


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
    expected_project_id = stable_repo_id(str(workspace))
    if project_id != expected_project_id:
        raise ValueError(
            "project internal ID must be derived from the canonical workspace path"
        )
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


def externalize_discovery_report(report: dict[str, Any],
                                 mapping: IdentityMap) -> dict[str, Any]:
    """Project a discovery document into the evidence-plane identity contract."""
    replacements = {item.internal_id: item.reference for item in mapping.repositories}
    projected = json.loads(json.dumps(report))
    projected["project_ref"] = mapping.project.reference
    projected.pop("project_id", None)

    def replace_field(row: dict[str, Any], old: str, new: str) -> None:
        if old in row:
            original = str(row.pop(old))
            row[new] = replacements.get(original, original)

    for row in projected.get("repos", []):
        if isinstance(row, dict):
            replace_field(row, "repo_id", "repository_ref")
            row["notes"] = _externalize_notes(row.get("notes", []), replacements)

    role_catalog = projected.pop("role_catalog_by_repo", {})
    projected["role_catalog_by_repository"] = {
        replacements.get(str(key), str(key)): value
        for key, value in role_catalog.items()
    }

    inventory = projected.get("route_inventory")
    if isinstance(inventory, dict):
        for row in inventory.get("rows", []):
            if isinstance(row, dict):
                replace_field(row, "repo_id", "repository_ref")
        inventory["notes"] = _externalize_notes(inventory.get("notes", []), replacements)

    linkage = projected.get("ui_route_linkage")
    if isinstance(linkage, dict):
        linkage["frontends"] = [replacements.get(str(item), str(item))
                                for item in linkage.get("frontends", [])]
        calls = linkage.pop("calls_by_frontend", {})
        linkage["calls_by_frontend_repository"] = {
            replacements.get(str(key), str(key)): value for key, value in calls.items()
        }
        for row in linkage.get("rows", []):
            if isinstance(row, dict):
                replace_field(row, "frontend_repo_id", "frontend_repository_ref")
                replace_field(row, "repo_id", "repository_ref")
        linkage["notes"] = _externalize_notes(linkage.get("notes", []), replacements)

    projected["schema_version"] = "2.0.0"
    return projected


def _externalize_notes(notes: Any, replacements: dict[str, str]) -> Any:
    """Rewrite only wrapper-owned ``<repository-id>:`` note prefixes."""
    if not isinstance(notes, list):
        return notes
    result = []
    for item in notes:
        if isinstance(item, str):
            for internal_id, reference in replacements.items():
                if item.startswith(internal_id + ":"):
                    item = reference + item[len(internal_id):]
                    break
        result.append(item)
    return result


def load_discovery_report(run_dir: str | Path,
                          mapping: IdentityMap | None = None) -> dict[str, Any]:
    """Load and validate the current external discovery contract."""
    run = Path(run_dir).expanduser().resolve()
    value = json.loads((run / "discovery-report.json").read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("discovery-report.json must contain an object")
    identities = mapping or load(run)
    if value.get("schema_version") != "2.0.0":
        raise ValueError("discovery-report.json uses an unsupported contract version")
    legacy_locations: list[str] = []
    for field in ("project_id", "role_catalog_by_repo", "route_liveness"):
        if field in value:
            legacy_locations.append(field)
    for index, row in enumerate(value.get("repos", [])):
        if isinstance(row, dict) and "repo_id" in row:
            legacy_locations.append(f"repos[{index}].repo_id")
    linkage = value.get("ui_route_linkage")
    if isinstance(linkage, dict):
        if "calls_by_frontend" in linkage:
            legacy_locations.append("ui_route_linkage.calls_by_frontend")
        for index, row in enumerate(linkage.get("rows", [])):
            if not isinstance(row, dict):
                continue
            for field in ("repo_id", "frontend_repo_id"):
                if field in row:
                    legacy_locations.append(f"ui_route_linkage.rows[{index}].{field}")
    inventory = value.get("route_inventory")
    if isinstance(inventory, dict):
        for index, row in enumerate(inventory.get("rows", [])):
            if isinstance(row, dict) and "repo_id" in row:
                legacy_locations.append(f"route_inventory.rows[{index}].repo_id")
    if legacy_locations:
        raise ValueError(
            "discovery-report.json uses unsupported legacy field(s): "
            f"{', '.join(legacy_locations)}; regenerate the run"
        )
    if value.get("project_ref") != identities.project.reference:
        raise ValueError("discovery project_ref differs from identity map")
    return value


def load_table_evidence_by_repo(run_dir: str | Path,
                                mapping: "IdentityMap | None" = None) -> dict[str, dict]:
    """Load the datastore-evidence provider's per-repo artifacts (57B-80 PR3),
    keyed by human-readable ``repository_ref`` — the same shape the retired
    stage-1 discovery producer's ``table_evidence`` block used to carry
    inline, so every downstream projection keeps consuming it identically.

    Mirrors how ``system_model.from_callgraph``/``from_imports`` consume
    their own ``<run>/<stage>/<artifact_key>.*`` artifacts: reads directly
    from ``run_dir`` rather than requiring a prior in-process pass, so a
    resumed or standalone call (the provider stage hasn't necessarily run in
    THIS pass) still finds whatever a previous pass already wrote. A missing
    ``datastore/`` directory, or a repo with no artifact in it, is silently
    omitted — never a crash on absence — matching the empty-dict default
    every consumer already applies at its own ``table_evidence`` lookup.
    """
    run = Path(run_dir).expanduser().resolve()
    identities = mapping or load(run)
    datastore_dir = run / "datastore"
    result: dict[str, dict] = {}
    if not datastore_dir.is_dir():
        return result
    for path in sorted(datastore_dir.glob("*.json")):
        try:
            reference = identities.repository_by_artifact_key(path.stem).reference
        except KeyError:
            continue
        try:
            value = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            result[reference] = value
    return result


def load(run_dir: str | Path) -> IdentityMap:
    run = Path(run_dir).expanduser().resolve()
    path = run / FILENAME
    if not path.is_file():
        raise ValueError(f"current run is missing required {FILENAME}")
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
    canonical_project_path = str(_canonical(mapping.project.canonical_path))
    if mapping.project.internal_id != stable_repo_id(canonical_project_path):
        raise ValueError(f"{FILENAME} project identity is not anchored to its canonical path")
    state_path = run / "run-state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read run-state.json: {exc}") from exc
        if state.get("project_id") != mapping.project.internal_id:
            raise ValueError(f"{FILENAME} project identity differs from run-state.json")
    expected = build(
        spec,
        workspace_root=canonical_project_path,
        project_id=mapping.project.internal_id,
    )
    if discovery.get("project_ref") != mapping.project.reference:
        raise ValueError("discovery project_ref differs from identity map")
    if mapping != expected:
        raise ValueError(f"{FILENAME} differs from deterministic discovery identity")
    return mapping


# --------------------------------------------------------------------------- #
# Read-only legacy fallback (57B-83 C2) — export path only.
#
# Pre-57B-88 completed runs predate identity-map.json (and their
# discovery-report.json predates the externalized 2.0.0 contract that
# load_discovery_report() enforces), so load() above always fails on them at
# its very first check. derive_legacy() is a SEPARATE, best-effort function
# that reconstructs a read-only equivalent IdentityMap purely from what an old
# run already carries on disk: run-state.json's provenance rows (repo_id +
# path) for repository identity. It is called from exactly one place —
# report_html.run_inputs.load()'s fallback branch, reached only after load()
# itself has already failed — and from nowhere else: no analysis-plane
# producer (discovery, callgraph, findings, system-model, ...) may reach it,
# and load()'s own strict behavior above is untouched by its existence.
#
# It never opens targets.json (whose pre-88 shape has no schema_version and a
# completely different per-repo layout — see TargetSpec.from_dict, which
# would reject it outright) and never writes anything: the old run directory
# is read-only input, never a write target.
# --------------------------------------------------------------------------- #

def _legacy_workspace_root(run: Path, repo_paths: list[Path]) -> Path:
    """Best-effort workspace root for a pre-88 run's PROJECT display name only.

    Never used as a join key (run-state.json's own project_id is trusted for
    that) — only its basename feeds the project's human-readable reference.
    Prefers discovery-report.json's ``workspace_root`` field (present even in
    the pre-88 shape); reads it as plain JSON, tolerating any other shape or
    schema mismatch in that file, since only this one field is needed. Falls
    back to the common parent of the run's own repository paths when that
    field, or the file itself, is unavailable.
    """
    discovery_path = run / "discovery-report.json"
    if discovery_path.is_file():
        try:
            discovery = json.loads(discovery_path.read_text("utf-8"))
        except (OSError, ValueError):
            discovery = None
        if isinstance(discovery, dict):
            root = discovery.get("workspace_root")
            if isinstance(root, str) and root:
                return _canonical(root)
    if not repo_paths:
        raise ValueError("cannot derive a workspace root with no repository paths")
    return Path(os.path.commonpath([str(path) for path in repo_paths]))


def derive_legacy(run_dir: str | Path) -> IdentityMap:
    """Derive a read-only IdentityMap for a pre-57B-88 run with no
    identity-map.json on disk. See the module section comment above for the
    reachability guarantee and why targets.json is never consulted here.

    Uses the same reference-derivation rules as build() (shortest unique
    basename per repository, tie-broken by the shortest unique workspace-
    relative path suffix; a portable, collision-free artifact key per
    reference) so a legacy-derived map agrees with what build() would have
    produced natively, had this run gone through the modern producer.
    ``project_id`` is trusted as recorded in run-state.json rather than
    recomputed from the derived workspace root: recomputing would make a
    purely best-effort display-name derivation load-bearing for a join key
    that is already trustworthy as recorded, and best-effort root inference
    (see ``_legacy_workspace_root``) is not guaranteed to reproduce the exact
    canonical path the id was originally minted from.
    """
    run = Path(run_dir).expanduser().resolve()
    try:
        state = json.loads((run / "run-state.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read run-state.json: {exc}") from exc
    if not isinstance(state, dict):
        raise ValueError("run-state.json must contain an object")
    project_id = state.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("run-state.json is missing project_id")

    repo_paths: dict[str, Path] = {}
    for index, row in enumerate(state.get("provenance", []) or []):
        if not isinstance(row, dict):
            raise ValueError(f"run-state.json provenance[{index}] must be an object")
        repo_id, path = row.get("repo_id"), row.get("path")
        if not isinstance(repo_id, str) or not repo_id:
            raise ValueError(f"run-state.json provenance[{index}] is missing repo_id")
        if not isinstance(path, str) or not path:
            raise ValueError(f"run-state.json provenance[{index}] is missing path")
        repo_paths[repo_id] = _canonical(path)
    if not repo_paths:
        raise ValueError("run-state.json carries no provenance rows to derive identity from")

    workspace_root = _legacy_workspace_root(run, list(repo_paths.values()))
    reference_paths: dict[str, PurePosixPath] = {}
    workspace_relatives: dict[str, str] = {}
    display_names: dict[str, str] = {}
    for repo_id, path in repo_paths.items():
        try:
            relative = path.relative_to(workspace_root)
            workspace_relative = relative.as_posix()
            reference_path = PurePosixPath(
                path.name if relative == Path(".") else workspace_relative)
        except ValueError:
            # This repo's recorded path isn't under the (best-effort) derived
            # workspace root; fall back to its own basename as an
            # independent reference rather than failing the whole run.
            workspace_relative = path.name
            reference_path = PurePosixPath(path.name)
        if not reference_path.name:
            raise ValueError(f"repository {repo_id!r} has no readable basename")
        reference_paths[repo_id] = reference_path
        workspace_relatives[repo_id] = workspace_relative
        display_names[repo_id] = path.name

    references = _shortest_unique_references(reference_paths)
    artifact_keys = _portable_artifact_keys(references)
    repositories = tuple(
        RepositoryIdentity(
            internal_id=repo_id,
            display_name=display_names[repo_id],
            reference=references[repo_id],
            artifact_key=artifact_keys[repo_id],
            workspace_relative_path=workspace_relatives[repo_id],
            canonical_path=str(repo_paths[repo_id]),
        )
        for repo_id in sorted(repo_paths, key=lambda rid: references[rid])
    )
    project_name = _require_text(workspace_root.name, "project display name")
    return IdentityMap(
        schema_version=SCHEMA_VERSION,
        source=_SOURCE_LEGACY_DERIVED,
        project=ProjectIdentity(
            internal_id=project_id,
            display_name=project_name,
            reference=project_name,
            artifact_key=artifact_key(project_name),
            canonical_path=str(workspace_root),
        ),
        repositories=repositories,
    )
