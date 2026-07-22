"""Deterministic evaluation of bundled, data-only technology fingerprints."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..targetspec import TechnologyFacet
from .bundled import bundled_registry


_SKIP = {"node_modules", "vendor", ".git", "dist", "build", "coverage"}
_SOURCE_LIMIT = 400
_EVIDENCE_LIMIT = 8
_GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?([\w./-]+)\s+v[\w.+-]+")
_GOMOD_ENV = {
    "GOTOOLCHAIN": "local", "GOWORK": "off", "GOFLAGS": "-mod=readonly",
    "GOPROXY": "off", "GOSUMDB": "off",
}


@dataclass(frozen=True)
class DetectionReport:
    facets: tuple[TechnologyFacet, ...]
    analysis_roots: tuple[str, ...]
    unclassified_inventory: tuple[dict, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


def gomod_requires(gomod: Path, *, include_indirect: bool = True) -> list[str]:
    """Parse go.mod as data, using the Go parser offline when available."""
    go = shutil.which("go")
    if go:
        try:
            proc = subprocess.run(
                [go, "mod", "edit", "-json"], cwd=str(gomod.parent),
                capture_output=True, text=True, timeout=30,
                env={**os.environ, **_GOMOD_ENV},
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                return [
                    req["Path"] for req in (data.get("Require") or [])
                    if isinstance(req, dict) and req.get("Path")
                    and (include_indirect or not req.get("Indirect"))
                ]
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    return _gomod_requires_textual(gomod, include_indirect=include_indirect)


def _gomod_requires_textual(gomod: Path, *, include_indirect: bool) -> list[str]:
    try:
        text = gomod.read_text("utf-8", errors="replace")
    except OSError:
        return []
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not include_indirect and stripped.endswith("// indirect"):
            continue
        match = _GO_REQUIRE.match(stripped)
        if match:
            out.append(match.group(1))
    return out


def _iter_dirs(root: Path, max_depth: int = 2) -> list[Path]:
    found = [root]
    stack = [(root, 0)]
    while stack:
        base, depth = stack.pop()
        if depth >= max_depth:
            continue
        try:
            children = sorted(path for path in base.iterdir() if path.is_dir())
        except OSError:
            continue
        for child in children:
            if child.name in _SKIP or child.name.startswith("."):
                continue
            found.append(child)
            stack.append((child, depth + 1))
    return sorted(set(found))


def _relative(root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return relative or "."


def _source_hits(base: Path, extensions: set[str]) -> list[Path]:
    hits: list[Path] = []
    seen = 0
    stack = [base]
    while stack and seen < _SOURCE_LIMIT:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP and not entry.name.startswith("."):
                    stack.append(entry)
            else:
                seen += 1
                if entry.suffix.lower() in extensions:
                    hits.append(entry)
                    if len(hits) >= _EVIDENCE_LIMIT:
                        return hits
            if seen >= _SOURCE_LIMIT:
                break
    return hits


def _package_data(manifest: Path) -> tuple[set[str], str]:
    try:
        data = json.loads(manifest.read_text("utf-8"))
    except (OSError, ValueError):
        return set(), ""
    if not isinstance(data, dict):
        return set(), ""
    dependencies: set[str] = set()
    for section in (
        "dependencies", "devDependencies", "optionalDependencies", "peerDependencies",
    ):
        value = data.get(section) or {}
        if isinstance(value, dict):
            dependencies.update(str(name) for name in value)
    manager = data.get("packageManager", "")
    return dependencies, manager if isinstance(manager, str) else ""


def _scope(root: Path, project: Path, profile_extensions: set[str]) -> str:
    source = project / "src"
    chosen = source if source.is_dir() and _source_hits(source, profile_extensions) else project
    return _relative(root, chosen)


def _unclassified(root: Path, claimed_extensions: set[str]) -> tuple[dict, ...]:
    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    seen = 0
    stack = [root]
    while stack and seen < _SOURCE_LIMIT:
        base = stack.pop()
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP and not entry.name.startswith("."):
                    stack.append(entry)
                continue
            seen += 1
            extension = entry.suffix.lower() or "<none>"
            if extension in claimed_extensions:
                continue
            counts[extension] = counts.get(extension, 0) + 1
            samples.setdefault(extension, [])
            if len(samples[extension]) < 3:
                samples[extension].append(_relative(root, entry))
            if seen >= _SOURCE_LIMIT:
                break
    return tuple(
        {"extension": extension, "count": counts[extension],
         "samples": samples[extension]}
        for extension in sorted(counts)
    )


def detect(repo_path: str | Path) -> DetectionReport:
    root = Path(repo_path).expanduser().resolve()
    registry = bundled_registry()
    directories = _iter_dirs(root)
    project_dirs = [
        directory for directory in directories
        if any((directory / name).is_file() for name in
               ("package.json", "go.mod", "tsconfig.json", "tsconfig.app.json",
                "tsconfig.base.json"))
    ] or [root]

    packages = {
        directory: _package_data(directory / "package.json")
        for directory in project_dirs if (directory / "package.json").is_file()
    }
    go_requires = {
        directory: set(gomod_requires(directory / "go.mod"))
        for directory in project_dirs if (directory / "go.mod").is_file()
    }
    claimed_extensions = {
        fingerprint.value.lower()
        for profile in registry.profiles
        for fingerprint in profile.fingerprints
        if fingerprint.kind == "source-extension"
    }
    facets: list[TechnologyFacet] = []
    notes: list[str] = []

    # Evaluate non-default fingerprints first so a package-only JavaScript
    # default never masks a real TypeScript observation in the same scope.
    language_hits_by_scope: dict[Path, set[str]] = {}
    pending_defaults: list[tuple[object, Path, str]] = []
    for profile in registry.profiles:
        if profile.profile_id == "repository.unclassified":
            continue
        evidence: set[str] = set()
        scopes: set[str] = set()
        source_only = True
        for fingerprint in profile.fingerprints:
            kind, value = fingerprint.kind, fingerprint.value
            if kind == "manifest-default":
                for directory in project_dirs:
                    path = directory / value
                    if path.is_file():
                        pending_defaults.append((profile, directory, _relative(root, path)))
                continue
            for directory in project_dirs:
                path = directory / value
                if kind in {"manifest-file", "config-file"} and path.is_file():
                    evidence.add(_relative(root, path))
                    extensions = {
                        item.value.lower() for item in profile.fingerprints
                        if item.kind == "source-extension"
                    }
                    scopes.add(_scope(root, directory, extensions))
                    if profile.kind == "language":
                        language_hits_by_scope.setdefault(directory, set()).add(
                            profile.profile_id
                        )
                    source_only = False
                elif kind == "package-dependency" and directory in packages \
                        and value in packages[directory][0]:
                    manifest = _relative(root, directory / "package.json")
                    evidence.add(f"{manifest}#dependency:{value}")
                    scopes.add(_relative(root, directory))
                    source_only = False
                elif kind == "go-require" and value in go_requires.get(directory, set()):
                    manifest = _relative(root, directory / "go.mod")
                    evidence.add(f"{manifest}#require:{value}")
                    scopes.add(_relative(root, directory))
                    source_only = False
        source_extensions = {
            item.value.lower() for item in profile.fingerprints
            if item.kind == "source-extension"
        }
        if source_extensions:
            for directory in project_dirs:
                hits = _source_hits(directory, source_extensions)
                if not hits:
                    continue
                evidence.update(_relative(root, path) for path in hits)
                scopes.add(_scope(root, directory, source_extensions))
                language_hits_by_scope.setdefault(directory, set()).add(profile.profile_id)
        if evidence:
            facets.append(TechnologyFacet(
                profile_id=profile.profile_id, kind=profile.kind,
                scope_roots=sorted(scopes), evidence=sorted(evidence),
                confidence="medium" if source_only else "high",
            ))

    for profile, directory, evidence in pending_defaults:
        if profile.profile_id in language_hits_by_scope.get(directory, set()):
            continue
        if any(
            other != profile.profile_id
            for other in language_hits_by_scope.get(directory, set())
        ):
            continue
        facets.append(TechnologyFacet(
            profile_id=profile.profile_id, kind=profile.kind,
            scope_roots=[_relative(root, directory)], evidence=[evidence],
            confidence="medium",
        ))

    # Merge repeated observations for one profile into one deterministic facet.
    merged: dict[str, TechnologyFacet] = {}
    for facet in facets:
        current = merged.get(facet.profile_id)
        if current is None:
            merged[facet.profile_id] = facet
            continue
        current.scope_roots = sorted(set(current.scope_roots + facet.scope_roots))
        current.evidence = sorted(set(current.evidence + facet.evidence))
        if facet.confidence == "high":
            current.confidence = "high"

    # Conflicting Node package-manager declarations are an observation, not an
    # execution decision. Preserve the conflict on the ecosystem facet.
    node = merged.get("ecosystem.node")
    if node is not None:
        for directory, (_, declared) in packages.items():
            lock_managers = {
                manager for filename, manager in (
                    ("package-lock.json", "npm"), ("yarn.lock", "yarn"),
                    ("pnpm-lock.yaml", "pnpm"),
                ) if (directory / filename).is_file()
            }
            declared_manager = declared.split("@", 1)[0].lower() if declared else ""
            if len(lock_managers) > 1 or (
                declared_manager and lock_managers and declared_manager not in lock_managers
            ):
                node.state = "conflicting"
                node.confidence = "medium"
                node.evidence.append(
                    f"{_relative(root, directory)}: conflicting package-manager evidence"
                )
                node.evidence = sorted(set(node.evidence))

    inventory = _unclassified(root, claimed_extensions)
    if not any(facet.kind in {"language", "ecosystem", "framework"}
               for facet in merged.values()):
        samples = [sample for row in inventory for sample in row["samples"]][:_EVIDENCE_LIMIT]
        merged["repository.unclassified"] = TechnologyFacet(
            profile_id="repository.unclassified", kind="repository-trait",
            scope_roots=["."], evidence=samples or ["generic file inventory is empty"],
            confidence="low", state="unknown",
        )
        notes.append("no bundled technology profile matched; generic inventory retained")

    language_roots = {
        scope for facet in merged.values() if facet.kind == "language"
        for scope in facet.scope_roots
    }
    if "." in language_roots and len(language_roots) > 1:
        notes.append(
            "analysis roots collapsed to repo root; covered sub-roots: "
            + ", ".join(sorted(language_roots - {"."}))
        )
    analysis_roots = () if "." in language_roots else tuple(sorted(language_roots))
    return DetectionReport(
        facets=tuple(sorted(merged.values(), key=lambda facet: (
            facet.kind, facet.profile_id, facet.scope_roots))),
        analysis_roots=analysis_roots,
        unclassified_inventory=inventory,
        notes=tuple(notes),
    )
