"""Bundled capability providers wrapping the callgraph/depmap lanes (57B-81 PR2).

Each provider below is a thin adapter, not a reimplementation: it resolves
identity through ``context.identities`` (a provider's ONLY path to a
human-readable reference or artifact key — never a path basename, per
``RunContext``'s own contract), calls the UNMODIFIED lane ``analyze()``
exactly as the legacy single-pass emitters did, and writes exactly the
artifact shape its capability owns:

* callgraph providers write ONE per-repo/lane FRAGMENT, never the final
  ``<artifact-key>.jsonl`` directly — a repo can carry both a Go and a JS/TS
  facet, so the merge/sort/dedup across a repo's lanes belongs to
  :func:`analysis_wrapper.callgraph.emit.assemble`, not to either provider;
* dependency-map providers write the FINAL per-repo/lane map file directly
  (a repo's Go map and JS/TS map never collide on name — different suffixes —
  so there is nothing to merge) plus a coverage-only fragment for
  :func:`analysis_wrapper.depmap.emit.assemble` to roll up.

Every write goes through :func:`~analysis_wrapper.executor.replace_artifact_text`
(atomic, idempotent) rather than the create-once ``write_new_text`` the
legacy emitters used: the execution loop may legitimately invoke a provider
more than once against the same output directory (the shared conformance
battery's own determinism check does exactly this), and a provider's output
for one (repo, lane) pair is always the SAME deterministic content for the
same inputs — so re-writing it must never be treated as a clobber.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..callgraph import go_lane as callgraph_go_lane
from ..callgraph import js_lane as callgraph_js_lane
from ..depmap import go_lane as depmap_go_lane
from ..depmap import js_lane as depmap_js_lane
from ..evidence.coverage import Coverage
from ..executor import create_stage_dir, replace_artifact_text
from ..sanitize import sanitize_text
from ..targetspec import RepoTarget
from .contracts import ArtifactRef, CapabilityResult, RunContext

# Bounded free-text detail (mirrors the ~300-char bounds lanes already apply to
# their own failure reasons); generous enough to keep a lane's reason+notes
# readable without letting one runaway tool message balloon the record.
_DETAIL_LIMIT = 500


def _identities(context: RunContext, provider_id: str):
    if context.identities is None:
        raise ValueError(
            f"provider {provider_id!r} requires RunContext.identities to be "
            "resolved — the execution loop always supplies one in production"
        )
    return context.identities


def _facet_provenance(target: RepoTarget, profile_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Which of THIS target's detected facets this provider actually matched."""
    linked = set(profile_ids)
    return tuple(sorted({facet.profile_id for facet in target.facets
                         if facet.profile_id in linked}))


def _coverage_from_lane(status: str, *, reason: str, notes: str) -> Coverage:
    """Map a lane's own status vocabulary (complete/partial/failed/unavailable
    — a subset of Coverage.STATUS_VALUES) straight across: a provider that ran
    is, by definition, applicable."""
    detail = "; ".join(part for part in (reason, notes) if part)
    return Coverage(applicability="applicable", status=status,
                    reason_code=f"lane-{status}", detail=detail[:_DETAIL_LIMIT])


def _write_json(path: Path, payload: dict) -> None:
    replace_artifact_text(
        path, sanitize_text(json.dumps(payload, indent=2, sort_keys=True) + "\n"))


def _artifact_ref(path: Path, run_dir: Path, kind: str) -> ArtifactRef:
    return ArtifactRef(path=str(path.relative_to(run_dir)), kind=kind)


@dataclass(frozen=True)
class CallgraphGoProvider:
    """Wraps :func:`analysis_wrapper.callgraph.go_lane.analyze`."""

    provider_id: str = "callgraph-go"
    capability_id: str = "callgraph"
    profile_ids: tuple[str, ...] = ("language.go",)

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        identities = _identities(context, self.provider_id)
        repository_ref = identities.reference_for(target.repo_id)
        artifact_key = identities.artifact_key_for(target.repo_id)
        edges, cov = callgraph_go_lane.analyze(
            target, repository_ref=repository_ref,
            allow_network=context.network_authorized)

        run_dir = Path(context.output_dir)
        cg_dir = create_stage_dir(run_dir / "callgraph")
        fragments_dir = create_stage_dir(cg_dir / ".fragments")
        ordered = sorted(set(edges), key=lambda edge: edge.sort_key())
        path = fragments_dir / f"{artifact_key}.go.json"
        _write_json(path, {
            "artifact_key": artifact_key, "lane": "go",
            "coverage_row": cov.to_dict(),
            "edges": [asdict(edge) for edge in ordered],
        })
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage_from_lane(cov.status, reason=cov.reason, notes=cov.notes),
            artifact_refs=(_artifact_ref(path, run_dir, "callgraph-fragment"),),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )


@dataclass(frozen=True)
class CallgraphJsProvider:
    """Wraps :func:`analysis_wrapper.callgraph.js_lane.analyze`."""

    provider_id: str = "callgraph-js"
    capability_id: str = "callgraph"
    profile_ids: tuple[str, ...] = ("language.javascript", "language.typescript")

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        identities = _identities(context, self.provider_id)
        repository_ref = identities.reference_for(target.repo_id)
        artifact_key = identities.artifact_key_for(target.repo_id)
        edges, cov = callgraph_js_lane.analyze(target, repository_ref=repository_ref)

        run_dir = Path(context.output_dir)
        cg_dir = create_stage_dir(run_dir / "callgraph")
        fragments_dir = create_stage_dir(cg_dir / ".fragments")
        ordered = sorted(set(edges), key=lambda edge: edge.sort_key())
        path = fragments_dir / f"{artifact_key}.js.json"
        _write_json(path, {
            "artifact_key": artifact_key, "lane": "js",
            "coverage_row": cov.to_dict(),
            "edges": [asdict(edge) for edge in ordered],
        })
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage_from_lane(cov.status, reason=cov.reason, notes=cov.notes),
            artifact_refs=(_artifact_ref(path, run_dir, "callgraph-fragment"),),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )


@dataclass(frozen=True)
class DepmapGoProvider:
    """Wraps :func:`analysis_wrapper.depmap.go_lane.analyze`."""

    provider_id: str = "depmap-go"
    capability_id: str = "dependency-map"
    profile_ids: tuple[str, ...] = ("language.go",)

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        identities = _identities(context, self.provider_id)
        repository_ref = identities.reference_for(target.repo_id)
        artifact_key = identities.artifact_key_for(target.repo_id)
        payload, cov = depmap_go_lane.analyze(
            target, repository_ref=repository_ref, artifact_key=artifact_key,
            allow_network=context.network_authorized)

        run_dir = Path(context.output_dir)
        imports_dir = create_stage_dir(run_dir / "imports")
        artifact_refs = []
        if payload is not None:
            map_path = imports_dir / f"{artifact_key}.golist.json"
            _write_json(map_path, payload)
            artifact_refs.append(_artifact_ref(map_path, run_dir, "dependency-map"))
        fragments_dir = create_stage_dir(imports_dir / ".fragments")
        fragment_path = fragments_dir / f"{artifact_key}.go.json"
        _write_json(fragment_path, {
            "artifact_key": artifact_key, "lane": "go",
            "coverage_row": cov.to_dict(),
        })
        artifact_refs.append(_artifact_ref(fragment_path, run_dir, "dependency-map-fragment"))
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage_from_lane(cov.status, reason=cov.reason, notes=cov.notes),
            artifact_refs=tuple(artifact_refs),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )


@dataclass(frozen=True)
class DepmapJsProvider:
    """Wraps :func:`analysis_wrapper.depmap.js_lane.analyze`."""

    provider_id: str = "depmap-js"
    capability_id: str = "dependency-map"
    profile_ids: tuple[str, ...] = ("language.javascript", "language.typescript")

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        identities = _identities(context, self.provider_id)
        repository_ref = identities.reference_for(target.repo_id)
        artifact_key = identities.artifact_key_for(target.repo_id)

        run_dir = Path(context.output_dir)
        # depcruise's ts-alias prep needs an analyzer-owned config dir under
        # the run tree — same nested create_stage_dir pattern the legacy
        # emitter used (guards both the shared config root and this repo's
        # own subdirectory against symlink redirection).
        config_root = create_stage_dir(run_dir / ".depmap-config")
        config_dir = create_stage_dir(config_root / artifact_key)
        payload, cov = depmap_js_lane.analyze(
            target, config_dir, repository_ref=repository_ref, artifact_key=artifact_key)

        imports_dir = create_stage_dir(run_dir / "imports")
        artifact_refs = []
        if payload is not None:
            map_path = imports_dir / f"{artifact_key}.depcruise.json"
            _write_json(map_path, payload)
            artifact_refs.append(_artifact_ref(map_path, run_dir, "dependency-map"))
        fragments_dir = create_stage_dir(imports_dir / ".fragments")
        fragment_path = fragments_dir / f"{artifact_key}.js.json"
        _write_json(fragment_path, {
            "artifact_key": artifact_key, "lane": "js",
            "coverage_row": cov.to_dict(),
        })
        artifact_refs.append(_artifact_ref(fragment_path, run_dir, "dependency-map-fragment"))
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage_from_lane(cov.status, reason=cov.reason, notes=cov.notes),
            artifact_refs=tuple(artifact_refs),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )
