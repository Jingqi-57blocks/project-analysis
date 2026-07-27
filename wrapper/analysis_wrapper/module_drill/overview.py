"""Overview-backed ModuleScope provider.

This adapter reads only versioned JSON artifacts from a completed overview.
It does not parse an overview report, infer a new module boundary, or read
unrelated source files.  A later Module Drill stage can therefore distinguish
reused static evidence from new, module-focused evidence.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from .. import capabilities, findings, identity, module_map, run_provenance
from ..evidence.coverage import from_capability_status
from ..lifecycle import Pointers, RunState
from ..system_model.schema import SCHEMA_VERSION as SYSTEM_MODEL_SCHEMA_VERSION
from ..targetspec import TargetSpec
from .contracts import (
    MODULE_SCOPE_VERSION,
    Boundary,
    FindingHint,
    ModuleCoverage,
    ModuleIdentity,
    ModuleScope,
    ModuleScopeRequest,
    OverviewLineage,
    OwnedLocation,
    ProjectSnapshot,
    RepositorySnapshot,
    ScopeAlternative,
    ScopeResolutionError,
    Selector,
)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScopeResolutionError(
            "missing-artifact", f"overview is missing a readable {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ScopeResolutionError("invalid-artifact", f"{label} must contain a JSON object")
    return value


def _artifact_ref(path: str, location: str) -> str:
    return f"{path}:{location}"


def _relative_parent(paths: list[str]) -> str:
    """A conservative repository-relative root for an owned evidence group."""
    normalized = [PurePosixPath(path) for path in paths if path]
    if not normalized:
        return "."
    parents = [path.parent for path in normalized]
    common = list(parents[0].parts)
    for parent in parents[1:]:
        parts = parent.parts
        common = common[:next((index for index, item in enumerate(common)
                              if index >= len(parts) or parts[index] != item),
                             min(len(common), len(parts)))]
    return "/".join(common) or "."


class OverviewScopeProvider:
    """Resolve one exact module from a completed, compatible overview run."""

    source_mode = "overview"

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir).expanduser().resolve()

    def source_inputs(self) -> tuple[TargetSpec, identity.IdentityMap, ProjectSnapshot]:
        """Expose the exact already-validated source mapping to the command layer."""
        _state, spec, identities, _candidates, _map, _model, _coverage, _findings = self._load_contracts()
        return spec, identities, self._snapshot(spec, identities)

    @classmethod
    def from_current(cls, skill_root: str | Path, project_key: str) -> "OverviewScopeProvider":
        """Load the explicitly accepted overview for one output namespace."""
        root = Path(skill_root).expanduser().resolve()
        key_path = PurePosixPath(project_key)
        if (not isinstance(project_key, str) or not project_key or key_path.is_absolute()
                or len(key_path.parts) != 1 or project_key in {".", ".."}):
            raise ScopeResolutionError(
                "invalid-project-key", "current overview project key must be one safe path segment")
        current = Pointers(root / "state" / project_key).read().get("current")
        if not isinstance(current, str) or not current:
            raise ScopeResolutionError(
                "no-current-overview",
                f"project {project_key!r} has no accepted current overview",
            )
        return cls(root / "output" / project_key / "overview" / current)

    def _load_contracts(self) -> tuple[RunState, TargetSpec, identity.IdentityMap,
                                       dict[str, Any], dict[str, Any], dict[str, Any],
                                       dict[str, Any], dict[str, Any] | None]:
        try:
            state = RunState.load(self.run_dir)
            spec = TargetSpec.load(self.run_dir / "targets.json")
            identities = identity.load(self.run_dir)
        except (OSError, ValueError, KeyError) as exc:
            raise ScopeResolutionError("invalid-overview", str(exc)) from exc
        if state.next_stage():
            raise ScopeResolutionError(
                "incomplete-overview",
                f"overview {state.run_id!r} is incomplete (next stage: {state.next_stage()})",
            )
        if state.project_id != identities.project.internal_id:
            raise ScopeResolutionError(
                "project-mismatch", "overview run state does not match its identity map")
        expected_state = {
            target.repo_id: (target.git.head, target.git.dirty_detail)
            for target in spec.repos
        }
        recorded_state = {
            str(row.get("repo_id")): (str(row.get("head", "")),
                                      str(row.get("dirty_detail", "")))
            for row in state.provenance if isinstance(row, dict)
        }
        if recorded_state != expected_state:
            raise ScopeResolutionError(
                "source-contract-mismatch",
                "overview run state does not match its recorded source snapshot",
            )
        stale = state.staleness()
        try:
            provenance = run_provenance.load(self.run_dir)
        except (OSError, ValueError) as exc:
            raise ScopeResolutionError("missing-provenance", str(exc)) from exc
        stale.extend(run_provenance.target_source_staleness(provenance, spec))
        if stale:
            raise ScopeResolutionError(
                "stale-overview", "overview source changed: " + "; ".join(stale))

        try:
            candidates_doc, module_doc = module_map.validate(self.run_dir)
        except (OSError, ValueError) as exc:
            raise ScopeResolutionError("invalid-module-map", str(exc)) from exc
        model = _load_object(self.run_dir / "system-model.json", "system-model.json")
        if model.get("schema_version") != SYSTEM_MODEL_SCHEMA_VERSION:
            raise ScopeResolutionError(
                "unsupported-system-model",
                "system-model.json has an unsupported schema version",
            )
        if not isinstance(model.get("nodes"), list) or not isinstance(model.get("edges"), list):
            raise ScopeResolutionError("invalid-system-model", "system-model.json needs nodes and edges")
        if not all(isinstance(row, dict) and isinstance(row.get("id"), str)
                   for row in model["nodes"]):
            raise ScopeResolutionError("invalid-system-model", "system-model.json has an invalid node")
        if not all(isinstance(row, dict) and isinstance(row.get("id"), str)
                   and isinstance(row.get("src"), str) and isinstance(row.get("dst"), str)
                   and isinstance(row.get("type"), str)
                   for row in model["edges"]):
            raise ScopeResolutionError("invalid-system-model", "system-model.json has an invalid edge")
        coverage = _load_object(self.run_dir / "capabilities.json", "capabilities.json")
        if coverage.get("schema_version") != capabilities.SCHEMA_VERSION:
            raise ScopeResolutionError(
                "unsupported-capabilities", "capabilities.json has an unsupported schema version")
        if not isinstance(coverage.get("capabilities"), list) or not coverage["capabilities"]:
            raise ScopeResolutionError("missing-coverage", "capabilities.json has no capability records")

        findings_doc: dict[str, Any] | None = None
        finding_path = self.run_dir / findings.FINDINGS_FILE
        if finding_path.exists():
            try:
                findings_doc = findings.validate(self.run_dir)
            except (OSError, ValueError) as exc:
                raise ScopeResolutionError("invalid-findings", str(exc)) from exc
        return state, spec, identities, candidates_doc, module_doc, model, coverage, findings_doc

    @staticmethod
    def _snapshot(spec: TargetSpec, identities: identity.IdentityMap) -> ProjectSnapshot:
        repositories = []
        for target in spec.repos:
            repositories.append(RepositorySnapshot(
                repository_ref=identities.reference_for(target.repo_id),
                revision=target.git.head if target.git.is_git else "NON-GIT",
                source_state="git" if target.git.is_git else "non-git",
                dirty_detail=target.git.dirty_detail,
            ))
        return ProjectSnapshot(identities.project.reference, tuple(repositories))

    @staticmethod
    def _alternatives(rows: list[dict[str, Any]]) -> tuple[ScopeAlternative, ...]:
        return tuple(ScopeAlternative(
            selector=Selector(str(row["module_id"]), "name"),
            confidence=str(row["confidence"]),
            reason="overview module-map selector is not unique",
            evidence_refs=(_artifact_ref("module-map.json", f"modules/{row['module_id']}"),),
        ) for row in sorted(rows, key=lambda item: str(item["module_id"])))

    def _select(self, selector: Selector, module_doc: dict[str, Any]) -> dict[str, Any]:
        rows = list(module_doc["modules"])
        if selector.kind == "name":
            matches = [row for row in rows if selector.value in {
                str(row.get("module_id", "")), str(row.get("name", ""))}]
        elif selector.kind == "alias":
            matches = [row for row in rows if selector.value in set(row.get("aliases", []))]
        else:
            raise ScopeResolutionError(
                "unsupported-selector",
                f"overview scope supports exact module IDs, names, and aliases; not {selector.kind!r}",
            )
        if not matches:
            raise ScopeResolutionError(
                "module-not-found", f"no overview module matches {selector.kind} {selector.value!r}")
        if len(matches) != 1:
            raise ScopeResolutionError(
                "ambiguous-module", f"overview selector {selector.value!r} matches multiple modules",
                self._alternatives(matches),
            )
        return matches[0]

    @staticmethod
    def _coverage(document: dict[str, Any]) -> ModuleCoverage:
        entries = []
        limitations: list[str] = []
        for row in document["capabilities"]:
            capability_id = row.get("capability_id")
            status = row.get("status")
            applicable = row.get("applicable")
            if not isinstance(capability_id, str) or not isinstance(status, str) \
                    or not isinstance(applicable, bool):
                raise ScopeResolutionError("invalid-coverage", "capability record is incomplete")
            try:
                entries.append((capability_id, from_capability_status(status, applicable)))
            except ValueError as exc:
                raise ScopeResolutionError("invalid-coverage", str(exc)) from exc
            reason = row.get("reason")
            if isinstance(reason, str) and reason.strip():
                limitations.append(f"{capability_id}: {reason.strip()}")
        return ModuleCoverage(tuple(entries), limitations=tuple(sorted(set(limitations))))

    @staticmethod
    def _owned_scope(candidate_ids: set[str], candidates: dict[str, dict[str, Any]],
                     nodes: dict[str, dict[str, Any]]) -> tuple[OwnedLocation, ...]:
        grouped: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: {"files": set(), "symbols": set(), "refs": set()})
        for candidate_id in sorted(candidate_ids):
            candidate = candidates[candidate_id]
            repository_ref = str(candidate["repository_ref"])
            group = grouped[repository_ref]
            group["refs"].add(_artifact_ref("module-candidates.json", f"candidates/{candidate_id}"))
            for node_id in candidate.get("node_ids", []):
                node = nodes.get(str(node_id))
                if node is None:
                    continue
                group["refs"].add(_artifact_ref("system-model.json", f"nodes/{node_id}"))
                kind, label = str(node.get("kind", "")), str(node.get("label", ""))
                if kind == "file" and label:
                    group["files"].add(label)
                elif kind == "symbol" and label:
                    group["symbols"].add(label)
        locations = []
        for repository_ref, group in sorted(grouped.items()):
            files = tuple(sorted(group["files"]))
            locations.append(OwnedLocation(
                repository_ref=repository_ref,
                root=_relative_parent(list(files)),
                files=files,
                symbols=tuple(sorted(group["symbols"])),
                evidence_refs=tuple(sorted(group["refs"])),
            ))
        if not locations:
            raise ScopeResolutionError("insufficient-scope", "module has no assigned candidate ownership")
        return tuple(locations)

    @staticmethod
    def _boundaries(candidate_ids: set[str], candidates: dict[str, dict[str, Any]],
                    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> tuple[Boundary, ...]:
        owned = {str(node_id) for candidate_id in candidate_ids
                 for node_id in candidates[candidate_id].get("node_ids", [])}
        boundaries: dict[tuple[str, str, str], Boundary] = {}
        for edge in sorted(edges, key=lambda row: str(row.get("id", ""))):
            # Module-map and repository containment explain ownership; they
            # are not cross-boundary interactions to hand to a drill-down.
            if edge.get("type") == "containment":
                continue
            src, dst = str(edge.get("src", "")), str(edge.get("dst", ""))
            if (src in owned) == (dst in owned):
                continue
            neighbor_id = dst if src in owned else src
            neighbor = nodes.get(neighbor_id, {})
            owner_id = src if src in owned else dst
            owner = nodes.get(owner_id, {})
            repository_ref = str(neighbor.get("repository_ref") or owner.get("repository_ref") or "")
            if not repository_ref:
                continue
            direction = "outbound" if src in owned else "inbound"
            kind = str(edge.get("type", ""))
            if not kind:
                continue
            boundary = Boundary(
                direction=direction, kind=kind, neighbor_id=neighbor_id,
                repository_ref=repository_ref,
                evidence_refs=(_artifact_ref("system-model.json", f"edges/{edge.get('id', '')}"),),
            )
            boundaries[(direction, kind, neighbor_id)] = boundary
        return tuple(boundaries[key] for key in sorted(boundaries))

    @staticmethod
    def _finding_hints(module_id: str, document: dict[str, Any] | None) -> tuple[FindingHint, ...]:
        if not document:
            return ()
        hints = []
        for row in document.get("findings", []):
            if module_id not in row.get("affected_modules", []):
                continue
            finding_id = row.get("finding_id")
            if isinstance(finding_id, str):
                hints.append(FindingHint(
                    finding_id, (_artifact_ref("findings.json", f"findings/{finding_id}"),)))
        return tuple(sorted(hints, key=lambda item: item.finding_id))

    def resolve(self, request: ModuleScopeRequest) -> ModuleScope:
        if request.source_mode != self.source_mode:
            raise ScopeResolutionError("source-mode-mismatch", "overview provider requires source_mode='overview'")
        (state, spec, identities, candidates_doc, module_doc, model,
         coverage_doc, findings_doc) = self._load_contracts()
        project = self._snapshot(spec, identities)
        if project != request.project:
            raise ScopeResolutionError(
                "project-snapshot-mismatch",
                "requested project snapshot does not exactly match the overview source snapshot",
            )
        module_row = self._select(request.selector, module_doc)
        module_id = str(module_row["module_id"])
        assigned = {str(row["candidate_id"]) for row in module_doc["candidate_dispositions"]
                    if module_id in row.get("module_ids", [])}
        candidates = {str(row["candidate_id"]): row for row in candidates_doc["candidates"]}
        if not assigned or not assigned.issubset(candidates):
            raise ScopeResolutionError("invalid-module-map", "module candidate lineage is incomplete")
        node_index = {str(row.get("id", "")): row for row in model["nodes"]
                      if isinstance(row, dict) and row.get("id")}
        return ModuleScope(
            contract_version=MODULE_SCOPE_VERSION,
            source_mode=self.source_mode,
            project=project,
            selector=request.selector,
            module=ModuleIdentity(module_id, str(module_row["name"]),
                                  tuple(module_row.get("aliases", [])),
                                  str(module_row["classification"]),
                                  str(module_row["confidence"])),
            owned_scope=self._owned_scope(assigned, candidates, node_index),
            assigned_candidates=tuple(sorted(assigned)),
            boundaries=self._boundaries(assigned, candidates, node_index, model["edges"]),
            coverage=self._coverage(coverage_doc),
            overview_lineage=OverviewLineage(
                state.run_id, project.snapshot_id,
                ("targets.json:root", "identity-map.json:root", "module-map.json:root",
                 "system-model.json:root", "capabilities.json:root"),
            ),
            finding_hints=self._finding_hints(module_id, findings_doc),
        )
