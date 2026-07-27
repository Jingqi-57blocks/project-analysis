"""Bounded standalone ModuleScope discovery for JS/TS and Go workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .. import identity
from ..discovery import emit
from ..evidence.coverage import Coverage
from ..exclusions import SOURCE_EXT, is_excluded_relative
from ..system_model import ids
from ..targetspec import RepoTarget, TargetSpec, stable_repo_id
from .contracts import (MODULE_SCOPE_VERSION, Boundary, ModuleCoverage,
                        ModuleIdentity, ModuleScope, ModuleScopeRequest,
                        OwnedLocation, ProjectSnapshot, RepositorySnapshot,
                        ScopeAlternative, ScopeResolutionError, Selector)

_SYMBOL = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+([A-Za-z_$][\w$]*)|^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")
_ROUTE = re.compile(r"\.(get|post|put|patch|delete|options)\s*\(\s*['\"]([^'\"]+)|\b(GET|POST|PUT|PATCH|DELETE)\s*\(\s*['\"]([^'\"]+)")
_IMPORT = re.compile(r"(?:from\s+|require\s*\(|import\s*\()['\"]([^'\"]+)['\"]|^\s*import\s*(?:[._][\w./-]*\s*)?['\"]([^'\"]+)['\"]")


@dataclass(frozen=True)
class _Candidate:
    target: RepoTarget
    root: str
    files: tuple[str, ...]
    symbols: tuple[str, ...]
    anchor: str
    name: str


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result[:63].strip("-") or "module"


def _canonical(value: str) -> str:
    return re.sub(r"[-_\s]+", "", value).casefold()


class StandaloneScopeProvider:
    """Resolve exactly one bounded code scope without creating an overview run."""

    source_mode = "standalone"

    def __init__(self, workspace: str | Path, *, analyzer_root: str | Path | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.analyzer_root = analyzer_root
        self._prepared: tuple[TargetSpec, identity.IdentityMap, ProjectSnapshot] | None = None

    def _prepare(self) -> tuple[TargetSpec, identity.IdentityMap, ProjectSnapshot]:
        if self._prepared is not None:
            return self._prepared
        try:
            spec, _report = emit.discover(self.workspace, analyzer_root=self.analyzer_root)
        except (OSError, ValueError) as exc:
            raise ScopeResolutionError("discovery-failed", str(exc)) from exc
        if not spec.repos:
            raise ScopeResolutionError("no-targets", "workspace has no supported source targets")
        project_id = stable_repo_id(str(self.workspace))
        identities = identity.build(spec, workspace_root=self.workspace, project_id=project_id)
        snapshot = ProjectSnapshot(identities.project.reference, tuple(
            RepositorySnapshot(identities.reference_for(target.repo_id),
                               target.git.head if target.git.is_git else "NON-GIT",
                               "git" if target.git.is_git else "non-git",
                               target.git.dirty_detail)
            for target in spec.repos))
        self._prepared = spec, identities, snapshot
        return self._prepared

    @property
    def project_snapshot(self) -> ProjectSnapshot:
        return self._prepare()[2]

    @staticmethod
    def _citation(target: RepoTarget, repository_ref: str, relative: str, line: int) -> str:
        revision = (target.git.head if target.git.is_git and target.git.dirty_detail == "no"
                    else "WORKTREE" if target.git.is_git else "NON-GIT")
        return f"{repository_ref}@{revision}:{relative}:{line}"

    @staticmethod
    def _files(target: RepoTarget, root: Path) -> tuple[str, ...]:
        base = Path(target.path).resolve()
        result: list[str] = []
        for directory, dirnames, filenames in os.walk(root):
            path = Path(directory)
            relative_dir = path.relative_to(base)
            dirnames[:] = sorted(name for name in dirnames if not is_excluded_relative(
                target, (relative_dir / name).as_posix()))
            for filename in sorted(filenames):
                candidate = path / filename
                relative = candidate.relative_to(base).as_posix()
                if candidate.suffix in SOURCE_EXT and not is_excluded_relative(target, relative):
                    result.append(relative)
        return tuple(result)

    def _path_candidates(self, spec: TargetSpec, selector: Selector) -> list[_Candidate]:
        if selector.kind != "path":
            return []
        relative = PurePosixPath(selector.value)
        candidates = []
        for target in spec.repos:
            path = Path(target.path) / relative
            if not path.exists() or not path.resolve().is_relative_to(Path(target.path).resolve()):
                continue
            root = path if path.is_dir() else path.parent
            files = self._files(target, root) if path.is_dir() else (relative.as_posix(),)
            candidates.append(_Candidate(target, root.relative_to(target.path).as_posix() or ".",
                                         files, (), f"{relative.as_posix()}:1", path.name))
        return candidates

    def _package_candidates(self, spec: TargetSpec, selector: Selector) -> list[_Candidate]:
        if selector.kind != "package":
            return []
        result = []
        for target in spec.repos:
            root = Path(target.path)
            package_json, gomod = root / "package.json", root / "go.mod"
            name = ""
            if package_json.is_file():
                try:
                    name = str(json.loads(package_json.read_text("utf-8")).get("name", ""))
                except (OSError, ValueError):
                    pass
            if not name and gomod.is_file():
                match = re.search(r"^\s*module\s+(\S+)", gomod.read_text("utf-8", errors="replace"), re.M)
                name = match.group(1) if match else ""
            if name == selector.value:
                manifest = "package.json" if package_json.is_file() else "go.mod"
                result.append(_Candidate(target, ".", self._files(target, root), (), f"{manifest}:1", name))
        return result

    def _directory_candidates(self, spec: TargetSpec, selector: Selector) -> list[_Candidate]:
        if selector.kind not in {"name", "alias"}:
            return []
        wanted = _canonical(selector.value)
        result = []
        for target in spec.repos:
            for source_root in target.root_paths():
                try:
                    children = sorted(path for path in source_root.iterdir() if path.is_dir())
                except OSError:
                    continue
                for child in children:
                    relative = child.relative_to(target.path).as_posix()
                    if _canonical(child.name) == wanted and not is_excluded_relative(target, relative):
                        result.append(_Candidate(target, relative, self._files(target, child), (),
                                                 f"{relative}:1", child.name))
        return result

    def _source_candidates(self, spec: TargetSpec, selector: Selector) -> list[_Candidate]:
        if selector.kind not in {"symbol", "route", "api"}:
            return []
        result = []
        for target in spec.repos:
            for root in target.root_paths():
                for relative in self._files(target, root):
                    path = Path(target.path) / relative
                    try:
                        lines = path.read_text("utf-8", errors="replace").splitlines()
                    except OSError:
                        continue
                    for index, line in enumerate(lines, 1):
                        matched = False
                        if selector.kind == "symbol":
                            symbols = [value for match in _SYMBOL.findall(line)
                                       for value in match if value]
                            matched = selector.value in symbols
                        else:
                            for parts in _ROUTE.findall(line):
                                method, route = (parts[0] or parts[2]).upper(), parts[1] or parts[3]
                                matched = selector.value in {route, f"{method} {route}"}
                                if matched:
                                    break
                        if matched:
                            owned_root = self._module_root(target, relative)
                            root_path = Path(target.path) / owned_root
                            result.append(_Candidate(target, owned_root,
                                                     self._files(target, root_path),
                                                     (selector.value,) if selector.kind == "symbol" else (),
                                                     f"{relative}:{index}", selector.value))
        return result

    @staticmethod
    def _module_root(target: RepoTarget, relative: str) -> str:
        """Use the nearest immediate source-root child, never a whole repo by accident."""
        source = PurePosixPath(relative)
        for configured in target.analysis_roots or ["."]:
            root = PurePosixPath(configured)
            try:
                remainder = source.relative_to(root)
            except ValueError:
                continue
            if remainder.parts:
                candidate = root / remainder.parts[0]
                path = Path(target.path) / candidate
                if path.is_dir():
                    return candidate.as_posix()
        return str(source.parent) or "."

    def _candidates(self, spec: TargetSpec, selector: Selector) -> list[_Candidate]:
        raw = (self._path_candidates(spec, selector) or self._package_candidates(spec, selector)
               or self._directory_candidates(spec, selector) or self._source_candidates(spec, selector))
        # A route can be registered twice in one module and a symbol can have
        # several declarations in one owned root.  That is still one scope,
        # not a reason to fabricate ambiguity.
        unique: dict[tuple[str, str], _Candidate] = {}
        for candidate in raw:
            unique.setdefault((candidate.target.repo_id, candidate.root), candidate)
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _alternatives(candidates: list[_Candidate], identities: identity.IdentityMap) -> tuple[ScopeAlternative, ...]:
        return tuple(ScopeAlternative(
            Selector(candidate.root, "path"), "medium",
            "standalone selector matches more than one direct code scope",
            (f"{identities.reference_for(candidate.target.repo_id)}@"
             f"{candidate.target.git.head or 'NON-GIT'}:{candidate.anchor}",),
        ) for candidate in candidates)

    def _boundaries(self, candidate: _Candidate, repository_ref: str) -> tuple[Boundary, ...]:
        result: dict[str, Boundary] = {}
        for relative in candidate.files:
            path = Path(candidate.target.path) / relative
            try:
                lines = path.read_text("utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, 1):
                for match in _IMPORT.findall(line):
                    for value in filter(None, match):
                        result[value] = Boundary(
                            "outbound", "dependency", value, repository_ref,
                            (self._citation(candidate.target, repository_ref, relative, line_number),))
        return tuple(result[key] for key in sorted(result))

    def resolve(self, request: ModuleScopeRequest) -> ModuleScope:
        if request.source_mode != self.source_mode:
            raise ScopeResolutionError("source-mode-mismatch", "standalone provider requires source_mode='standalone'")
        spec, identities, snapshot = self._prepare()
        if request.project != snapshot:
            raise ScopeResolutionError("project-snapshot-mismatch", "requested project differs from workspace snapshot")
        candidates = self._candidates(spec, request.selector)
        if not candidates:
            raise ScopeResolutionError("module-not-found", f"no direct code scope matches {request.selector.value!r}")
        if len(candidates) != 1:
            raise ScopeResolutionError("ambiguous-module", "standalone selector is ambiguous",
                                       self._alternatives(candidates, identities))
        candidate = candidates[0]
        reference = identities.reference_for(candidate.target.repo_id)
        anchor_path, anchor_line = candidate.anchor.rsplit(":", 1)
        anchor = self._citation(candidate.target, reference, anchor_path, int(anchor_line))
        module_id = _slug(candidate.root if candidate.root != "." else candidate.name)
        candidate_id = "standalone." + hashlib.sha256(
            f"{reference}\0{candidate.root}".encode()).hexdigest()[:16]
        limitations = ["Standalone scope uses bounded direct source evidence; overview-wide findings are unavailable."]
        if snapshot.inspection_only:
            limitations.append("Source is dirty or non-Git; this standalone scope is inspection-only.")
        return ModuleScope(
            MODULE_SCOPE_VERSION, self.source_mode, snapshot, request.selector,
            ModuleIdentity(module_id, candidate.name, (candidate.root,), "unresolved", "high"),
            (OwnedLocation(reference, candidate.root, candidate.files, candidate.symbols, (anchor,)),),
            (candidate_id,), self._boundaries(candidate, reference),
            ModuleCoverage((("standalone-discovery", Coverage("applicable", "complete", "direct-discovery")),
                            ("overview-evidence", Coverage("unknown", "unavailable", "no-overview"))),
                           limitations=tuple(limitations)),
        )
