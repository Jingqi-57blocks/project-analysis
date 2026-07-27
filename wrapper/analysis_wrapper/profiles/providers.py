"""Bundled capability providers (57B-81 callgraph/depmap; 57B-80 datastore;
57B-82 A1 deploy-units; 57B-82 A2 git-history/dependency-risk; 57B-84
access-model/integration-evidence/route-inventory/ui-route-linkage).

Each provider below is a thin adapter, not a reimplementation: it resolves
identity through ``context.identities`` (a provider's ONLY path to a
human-readable reference or artifact key — never a path basename, per
``RunContext``'s own contract), calls the UNMODIFIED lane/producer analysis
function exactly as the legacy single-pass emitters did, and writes exactly
the artifact shape its capability owns:

* callgraph providers write ONE per-repo/lane FRAGMENT, never the final
  ``<artifact-key>.jsonl`` directly — a repo can carry both a Go and a JS/TS
  facet, so the merge/sort/dedup across a repo's lanes belongs to
  :func:`analysis_wrapper.callgraph.emit.assemble`, not to either provider;
* dependency-map providers write the FINAL per-repo/lane map file directly
  (a repo's Go map and JS/TS map never collide on name — different suffixes —
  so there is nothing to merge) plus a coverage-only fragment for
  :func:`analysis_wrapper.depmap.emit.assemble` to roll up.
* the datastore-evidence, deploy-units, access-evidence, and
  integration-evidence providers each write the FULL per-repo producer
  result directly (one repo, one producer, no per-lane fragmentation or
  cross-repo assembler needed) and are ``universal`` — see each one's own
  docstring below. deploy-units/access-evidence/integration-evidence carry
  NO linked profiles at all (``profile_ids = ()``); see
  ``CapabilityProvider``'s docstring for why ``ProfileRegistry`` accepts
  that.
* the route-inventory and ui-route-linkage providers (57B-84 B2) write ONE
  per-repo FRAGMENT each (like the callgraph providers above), not a final
  artifact directly — route liveness needs a cross-repo JOIN (every
  frontend's calls against every backend's routes) computed once per run,
  which belongs to :func:`analysis_wrapper.routes.emit.assemble`, not to
  either provider. Also zero-profile ``universal``, like deploy-units.
* the git-history and dependency-risk providers are the FIRST to actually
  execute an external SIGNAL TOOL (via ``context.tool_access.execute`` —
  every provider above either calls an in-process analyzer function directly
  or, for datastore/deploy-units/access-evidence/integration-evidence, a
  producer with no executor seam at all) rather than wrap one. Both are
  zero-profile ``universal``, like deploy-units, and both are RESUME-SAFE: a
  signal artifact is write-once (``run_tool``'s own collision-refusal,
  unlike the idempotent ``replace_artifact_text`` every other provider here
  uses), so re-running the provider stage over an already-populated
  ``signals/`` directory reuses the existing manifest instead of
  re-invoking the tool — see ``_run_or_reuse_signal`` below.

Every write goes through :func:`~analysis_wrapper.executor.replace_artifact_text`
(atomic, idempotent) rather than the create-once ``write_new_text`` the
legacy emitters used: the execution loop may legitimately invoke a provider
more than once against the same output directory (the shared conformance
battery's own determinism check does exactly this), and a provider's output
for one (repo, lane) pair is always the SAME deterministic content for the
same inputs — so re-writing it must never be treated as a clobber. The two
signal-tool providers below are the one exception: THEIR underlying
artifacts (``signals/*.manifest.json``/``*.view.txt``) are written by
``run_tool`` itself, which is write-once by design (immutable per-signal
evidence) — ``_run_or_reuse_signal`` is what keeps re-invocation safe for
them specifically.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..callgraph import go_lane as callgraph_go_lane
from ..callgraph import js_lane as callgraph_js_lane
from ..datastore_coverage import classify as classify_data_model
from ..depmap import go_lane as depmap_go_lane
from ..depmap import js_lane as depmap_js_lane
from ..evidence.coverage import Coverage, aggregate, from_datastore_coverage
from ..evidence.facts import Fact, SourceRef, make_fact_id
from ..executor import create_stage_dir, replace_artifact_text
from ..sanitize import sanitize_text
from ..targetspec import RepoTarget
from .contracts import ArtifactRef, CapabilityResult, RunContext
from .selection import is_go_target, is_node_target

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


def _coverage_from_availability(payload: dict, *, reason_prefix: str) -> Coverage:
    """Coverage for a simple ast-grep fail-closed producer (57B-84's
    access-model/integration-evidence): ``available`` IS the whole story —
    unlike ``discovery.tables`` there is no partial sub-lane (no SQL-style
    second producer that can independently fail) — so this is always
    ``applicable``, ``complete`` when ast-grep ran, ``unavailable`` with the
    producer's own notes as detail when it didn't."""
    if payload.get("available"):
        return Coverage(applicability="applicable", status="complete",
                        reason_code=f"{reason_prefix}-complete", detail="")
    detail = "; ".join(payload.get("notes", [])) or f"{reason_prefix} unavailable"
    return Coverage(applicability="applicable", status="unavailable",
                    reason_code=f"{reason_prefix}-unavailable", detail=detail)


def _write_json(path: Path, payload: dict) -> None:
    replace_artifact_text(
        path, sanitize_text(json.dumps(payload, indent=2, sort_keys=True) + "\n"))


def _coverage_from_route_fragment(notes: list[str], *, reason_prefix: str) -> Coverage:
    """Coverage for a route-domain fragment provider (57B-84 B2): every repo
    is scanned (universal), so this is always ``applicable``. A cap-hit note
    downgrades THIS repo's own coverage to ``partial`` — mirroring
    ``_coverage_from_lane``'s complete/partial vocabulary for a
    fragment-writing provider. Whether this repo counts as a route backend
    or UI frontend at all is a CONTENT fact (the fragment's own
    ``applicable`` flag, read by ``routes.emit.assemble``), not a
    coverage-outcome fact: a backend with a clean, complete scan and a
    non-backend with a clean, complete (empty) scan are both ``complete`` —
    exactly ``DatastoreEvidenceProvider``'s own not-applicable-is-a-content-
    fact philosophy."""
    status = "partial" if notes else "complete"
    return Coverage(applicability="applicable", status=status,
                    reason_code=f"{reason_prefix}-{status}", detail="; ".join(notes))


def _has_module_signal_routes(run_dir: Path, identities, repository_ref: str) -> bool:
    """Legacy ``discover()``'s own ``_produce_has_routes`` gate, read back
    from THIS run's already-written ``discovery-report.json``
    (``module_signals.routes``: a stage-1 signal computed once per repo by
    ``discovery.modules.extract`` — a lighter, DIFFERENT heuristic than
    ``liveness.route_registrations()``). Read back rather than recomputed,
    so the two scans can never silently disagree from reimplementation
    drift; ``discovery/modules.py`` itself: unchanged this slice.

    Re-parses the small discovery-report.json once per (provider, target)
    call (up to twice per target across both route providers) rather than
    caching across a frozen-dataclass provider instance — a deliberate,
    disclosed simplification: the file is small (no filesystem walk), and
    every other bundled universal provider already accepts a comparable
    per-target re-scan cost for far more expensive full-tree walks.

    In the real CLI pipeline, ``discovery-report.json`` is ALWAYS present by
    the time providers run (``discovery.emit.write_stage1`` writes it before
    ``run_provider_stage`` is ever called). A narrower harness that invokes
    ``run_providers``/a provider's own ``run()`` directly — the provider
    conformance battery, ``test_provider_execution.py``'s synthetic-provider
    loop tests — has no reason to also stand up a full discovery-report.json,
    so a missing/unreadable one degrades to "signal unknown" (``False``)
    rather than failing this provider's execution: every OTHER bundled
    provider is fully self-contained (no dependency on a sibling stage's own
    output), and this fallback keeps that same guarantee for the one gate
    input this domain genuinely cannot derive from ``target`` alone. The
    ``target.profiles_for_capability(...)`` half of each gate still applies
    even when this signal is unavailable."""
    from .. import identity as identity_mod

    try:
        report = identity_mod.load_discovery_report(run_dir, identities)
    except (OSError, ValueError):
        return False
    for block in report.get("repos", []):
        if block.get("repository_ref") == repository_ref:
            return bool(block.get("module_signals", {}).get("routes"))
    return False


def _artifact_ref(path: Path, run_dir: Path, kind: str) -> ArtifactRef:
    return ArtifactRef(path=str(path.relative_to(run_dir)), kind=kind)


def _revision_for(target: RepoTarget) -> str:
    """SourceRef-safe revision string for one target.

    Mirrors ``findings.py``'s own citation-grammar derivation (duplicated,
    not imported — the same duplication already exists across findings.py,
    tooldefs.py, and run_provenance.py) rather than the callgraph lanes'
    simpler ``target.git.head`` shortcut: ``Fact.SourceRef.revision`` accepts
    only a lowercase 40-char SHA, ``NON-GIT``, or ``WORKTREE``, and a raw
    empty/dirty head would violate that grammar.
    """
    if not target.git.is_git:
        return "NON-GIT"
    if target.git.dirty_detail != "no":
        return "WORKTREE"
    return target.git.head.lower()


def _parse_evidence_site(site: str) -> tuple[str, int]:
    """Split one ``discovery.tables`` evidence-site string into (path, line).

    ast-grep sites are ``"path:line"``; raw-SQL sites (``tables._sql_coverage``)
    are a bare ``"path"`` with no line ever tracked. A site without a trailing
    ``:<digits>`` segment therefore gets line 1 — ``SourceRef``'s own floor —
    rather than inventing a line sqlglot never located.
    """
    path, _, tail = site.rpartition(":")
    if path and tail.isdigit():
        return path, int(tail)
    return site, 1


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


@dataclass(frozen=True)
class DatastoreEvidenceProvider:
    """Wraps :func:`analysis_wrapper.discovery.tables.generate` (57B-80 PR2).

    Unlike the four lane providers above, this one is deliberately
    ``universal``: ``discovery.tables.generate``'s full-tree walk is the
    honest absence proof behind a ``not-applicable`` datastore verdict (a
    detector that never scanned a repo must never be credited with having
    confirmed it has no datastore), so this provider must run on every
    repository regardless of which facets were detected there, not only the
    ones already carrying a ``datastore.*`` facet. ``universal`` is read by
    the execution loop via ``getattr(provider, "universal", False)`` — every
    other bundled provider simply lacks the attribute and keeps its existing
    facet-gated behavior unchanged.

    Unlike the four lane providers above, ``run()`` never touches
    ``context.tool_access``: ``tables.generate()`` has no such seam — it
    calls ``astgrep``/``sqlglot`` directly, exactly as the legacy stage-1
    discovery producer already does. This is a deliberate, accepted
    constraint of wrapping that producer UNCHANGED, not a new executor
    bypass introduced by this provider (its invocation path is byte-identical
    to today's stage-1 call) — this provider's execution-record ``tools`` log
    is therefore always empty and it cannot honor ``network_authorized``.
    Routing ``discovery.tables`` through ToolDef/executor is potential later
    cleanup (57B-85 at the earliest), out of scope here.
    """

    provider_id: str = "datastore-evidence"
    capability_id: str = "data-model"
    profile_ids: tuple[str, ...] = (
        "datastore.sequelize", "datastore.gorm", "datastore.mongodb-native",
        "datastore.mongoose", "datastore.sql",
    )
    universal: bool = True

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        # Function-local import: discovery/ and profiles/ have a known
        # circular-import trap (profiles.bundled -> profiles.providers ->
        # discovery.* -> ... -> profiles.bundled through other discovery
        # submodules); a module-level import here would risk recreating it.
        from ..discovery import tables

        identities = _identities(context, self.provider_id)
        repository_ref = identities.reference_for(target.repo_id)
        artifact_key = identities.artifact_key_for(target.repo_id)

        evidence = tables.generate(
            target.path, target.repo_id, tier2_exclusions=target.tier2_exclusions)
        payload = evidence.to_dict()

        run_dir = Path(context.output_dir)
        datastore_dir = create_stage_dir(run_dir / "datastore")
        artifact_path = datastore_dir / f"{artifact_key}.json"
        _write_json(artifact_path, payload)

        data_model = classify_data_model(
            [{"repository_ref": repository_ref, "table_evidence": payload}])
        coverage = from_datastore_coverage(data_model)

        revision = _revision_for(target)
        store_metadata = payload.get("store_metadata", {})
        facts = tuple(
            self._table_fact(
                name, buckets, store_metadata.get(name, {}),
                capability_id=self.capability_id,
                repository_ref=repository_ref, revision=revision,
            )
            for name, buckets in sorted(payload.get("tables", {}).items())
        )
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id, coverage=coverage, facts=facts,
            artifact_refs=(_artifact_ref(artifact_path, run_dir, "datastore-evidence"),),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )

    @staticmethod
    def _table_fact(name: str, buckets: dict, metadata: dict, *, capability_id: str,
                    repository_ref: str, revision: str) -> Fact:
        # The SAME evidence site legitimately appears under two different
        # access buckets for one table (e.g. a Sequelize createTable call is
        # both a "declaration" and a "schema_write" at the identical site) —
        # access-type distinction is preserved in ``data["access"]`` below, so
        # source_refs are deduped rather than citing the same location twice.
        source_refs = tuple(sorted(
            {
                SourceRef(repository_ref=repository_ref, revision=revision,
                         path=path, line=line)
                for sites in buckets.values() for site in sites
                for path, line in (_parse_evidence_site(site),)
            },
            key=lambda ref: ref.to_string(),
        ))
        data = {
            "physical_name": metadata.get("physical_name", name),
            "kind": metadata.get("kind", "table"),
            "families": list(metadata.get("families", [])),
            "logical_names": list(metadata.get("logical_names", [])),
            "access": {access: list(sites) for access, sites in sorted(buckets.items())},
        }
        fact_id = make_fact_id(capability_id, repository_ref, "data-store", (name,))
        return Fact(fact_id=fact_id, kind="data-store", data=data, source_refs=source_refs)


def _coverage_from_deploy_units(status: str, notes: tuple[str, ...] | list[str]) -> Coverage:
    """Map ``deploy_units.generate()``'s own two-value status vocabulary
    straight across: ``inferred`` (artifacts found) and ``unknown`` (a
    completed scan found none — the honest answer today, never converted to
    ``not-applicable``, which would require a producer that positively proves
    absence rather than merely not finding one) are BOTH complete, applicable
    outcomes. A disclosed ``COVERAGE CAP`` note (the module's own file/byte
    scan-cap disclosure — see ``deploy_units._MAX_FILES``/``_MAX_BYTES``)
    degrades the outcome to ``partial`` instead, the same free-text
    reason+notes join ``_coverage_from_lane`` uses above."""
    capped = any("COVERAGE CAP" in note for note in notes)
    detail = "; ".join(notes)[:_DETAIL_LIMIT]
    return Coverage(applicability="applicable", status="partial" if capped else "complete",
                    reason_code=f"deploy-units-{status}", detail=detail)


@dataclass(frozen=True)
class DeployUnitsProvider:
    """Wraps :func:`analysis_wrapper.discovery.deploy_units.generate` (57B-82 A1).

    ``universal`` for the same reason as ``DatastoreEvidenceProvider`` above:
    ``deploy_units.generate``'s full-tree walk is itself the honest "unknown"
    disclosure for a repo with no deploy artifact, so it must run on every
    repository, not only ones carrying some pre-selected facet.

    Unlike every other bundled provider, ``profile_ids`` is INTENTIONALLY
    empty: a Dockerfile, compose file, Go ``package main`` entrypoint, or CI
    deploy step can appear in a repo carrying any combination of detected
    language/framework/datastore facets (or none at all) — there is no
    detected technology whose presence predicts a deploy artifact's presence,
    so linking this provider to one would assert a relationship discovery
    never observed. ``facet_provenance`` is therefore always empty for this
    provider's results, which is the honest disclosure, not an omission.

    Same ``context.tool_access``/``network_authorized`` constraint as
    ``DatastoreEvidenceProvider``: ``deploy_units.generate()`` has no
    executor seam and is called exactly as the legacy stage-1 discovery
    producer already did.
    """

    provider_id: str = "deploy-units"
    capability_id: str = "deployable-units"
    profile_ids: tuple[str, ...] = ()
    universal: bool = True

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        # Function-local import: same discovery/<->profiles circular-import
        # trap ``DatastoreEvidenceProvider`` documents above.
        from ..discovery import deploy_units

        identities = _identities(context, self.provider_id)
        artifact_key = identities.artifact_key_for(target.repo_id)

        result = deploy_units.generate(target.path, target.tier2_exclusions)
        payload = result.to_dict()

        run_dir = Path(context.output_dir)
        deploy_dir = create_stage_dir(run_dir / "deploy")
        artifact_path = deploy_dir / f"{artifact_key}.json"
        _write_json(artifact_path, payload)

        coverage = _coverage_from_deploy_units(result.status, result.notes)
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id, coverage=coverage,
            artifact_refs=(_artifact_ref(artifact_path, run_dir, "deploy-units-evidence"),),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )


@dataclass(frozen=True)
class AccessEvidenceProvider:
    """Wraps :func:`analysis_wrapper.discovery.access_model.generate` (57B-84).

    Unlike ``DatastoreEvidenceProvider``, this producer has no dedicated
    profile at all — access-control-shaped code is looked for in every
    repository regardless of language/framework, so ``profile_ids`` is
    deliberately empty; ``universal=True`` is what makes the execution loop
    select it anyway (``ProfileRegistry`` accepts an empty ``profile_ids``
    only for a universal provider — see its own docstring). This slice emits
    NO Facts (coverage + the full artifact only); a downstream consumer that
    wants per-role/per-check evidence reads the artifact directly, exactly as
    the retired stage-1 producer's callers already did.

    Same accepted tool_access constraint as ``DatastoreEvidenceProvider``:
    ``access_model.generate()`` has no such seam (calls ``astgrep`` directly,
    unchanged from the legacy stage-1 call), so this provider's ``tools`` log
    is always empty and it cannot honor ``network_authorized``.
    """

    provider_id: str = "access-evidence"
    capability_id: str = "access-model"
    profile_ids: tuple[str, ...] = ()
    universal: bool = True

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        # Function-local import: same profiles<->discovery circular-import
        # trap as DatastoreEvidenceProvider's own import of discovery.tables.
        from ..discovery import access_model

        identities = _identities(context, self.provider_id)
        artifact_key = identities.artifact_key_for(target.repo_id)

        evidence = access_model.generate(
            target.path, target.repo_id, tier2_exclusions=target.tier2_exclusions)
        payload = evidence.to_dict()

        run_dir = Path(context.output_dir)
        access_dir = create_stage_dir(run_dir / "access")
        artifact_path = access_dir / f"{artifact_key}.json"
        _write_json(artifact_path, payload)

        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage_from_availability(payload, reason_prefix="access-model"),
            artifact_refs=(_artifact_ref(artifact_path, run_dir, "access-evidence"),),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )


@dataclass(frozen=True)
class IntegrationEvidenceProvider:
    """Wraps :func:`analysis_wrapper.discovery.integrations.generate` (57B-84).

    Same shape as ``AccessEvidenceProvider``: no dedicated profile (assembled-
    URL / integration-package evidence is looked for everywhere), universal
    selection, no Facts this slice, same accepted tool_access constraint.
    """

    provider_id: str = "integration-evidence"
    capability_id: str = "integration-evidence"
    profile_ids: tuple[str, ...] = ()
    universal: bool = True

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        # Function-local import: same profiles<->discovery circular-import
        # trap as DatastoreEvidenceProvider's own import of discovery.tables.
        from ..discovery import integrations

        identities = _identities(context, self.provider_id)
        artifact_key = identities.artifact_key_for(target.repo_id)

        evidence = integrations.generate(
            target.path, target.repo_id, tier2_exclusions=target.tier2_exclusions)
        payload = evidence.to_dict()

        run_dir = Path(context.output_dir)
        integrations_dir = create_stage_dir(run_dir / "integrations")
        artifact_path = integrations_dir / f"{artifact_key}.json"
        _write_json(artifact_path, payload)

        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage_from_availability(
                payload, reason_prefix="integration-evidence"),
            artifact_refs=(_artifact_ref(artifact_path, run_dir, "integration-evidence"),),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )


# ---------------------------------------------------------------------------
# Signal-tool-executing providers (57B-82 A2).
# ---------------------------------------------------------------------------

_VALID_SIGNAL_STATUS = {"complete", "partial", "failed", "skipped"}


def _coverage_from_signal(reason_prefix: str, status: str, reason: str) -> Coverage:
    """Map one signal-tool outcome straight into Coverage: a signal that ran
    (or was validly SKIPPED by the executor itself — never invoked, but for
    a disclosed, deliberate reason such as missing network authorization) is,
    by definition, applicable. ``reason`` is passed through UNMODIFIED as
    ``detail`` — this is the exact text preservation the network-off SKIPPED
    case depends on (``run_tool``'s own literal "network-capable tool
    requires explicit authorization" must survive verbatim)."""
    safe_status = status if status in _VALID_SIGNAL_STATUS else "failed"
    return Coverage(applicability="applicable", status=safe_status,
                    reason_code=f"{reason_prefix}-{safe_status}",
                    detail=reason[:_DETAIL_LIMIT])


def _signal_artifact_refs(run_dir: Path, manifest_path: "Path | None",
                         view_path: "Path | None") -> tuple[ArtifactRef, ...]:
    refs = []
    if manifest_path is not None:
        refs.append(_artifact_ref(manifest_path, run_dir, "signal-manifest"))
    if view_path is not None:
        refs.append(_artifact_ref(view_path, run_dir, "signal-view"))
    return tuple(refs)


def _run_or_reuse_signal(context: RunContext, tool_id: str, target: RepoTarget,
                         artifact_key: str, *, tooldef=None,
                         ) -> tuple[str, str, tuple[ArtifactRef, ...]]:
    """Execute ``tool_id`` via ``context.tool_access``, or reuse an
    already-written manifest from a prior ``prepare-overview`` pass.

    Signal-tool artifacts are write-once (``run_tool``'s own
    ``_assert_signal_paths_available`` refuses to overwrite an existing
    manifest/view) — unlike every OTHER bundled provider's idempotent
    ``replace_artifact_text`` writes. Since the provider stage runs
    UNCONDITIONALLY on every ``prepare-overview`` pass (including one that
    resumes an already-completed run, where ``signals/`` already holds this
    exact tool's manifest from a PRIOR pass), re-invoking here would hit that
    collision refusal and crash a normal resume. Checking for the existing
    manifest FIRST — and reading its own recorded ``status``/``reason``
    instead of re-running — keeps this provider naturally idempotent, and
    (proven by the shared conformance battery's own determinism check, which
    calls ``run_providers`` twice against the SAME context) produces the
    IDENTICAL Coverage either way.

    Defensive against the conformance battery's ``_StatusStub``-based
    tool_access (only ever provides ``.status`` — no ``.reason``,
    ``.manifest_path``, or ``.view_path``: real only in production, where
    ``ExecutorToolAccess`` always returns a genuine ``SignalResult``) via
    ``getattr`` with honest fallbacks rather than a crash.

    KNOWN, INTENTIONAL disclosure difference from every other bundled
    provider: on the reuse branch, ``context.tool_access.execute(...)`` is
    never called, so ``execution.py``'s ``RecordingToolAccess`` never sees
    this (provider, target) pair's tool call — that row's own
    ``provider-execution.json["tools"]`` entry is an empty list on a
    resumed pass, where a fresh pass shows the real invocation. This is
    HONEST, not a bug: an empty ``tools`` list truthfully means no tool call
    happened THIS pass. Coverage/status/reason are UNCHANGED either way
    (proven by the conformance battery's own two-call determinism check,
    and by ``tests/test_cli.py``'s real, non-stubbed resume test), so
    ``provider-execution.json`` is not strictly byte-for-byte identical
    between a fresh and a resumed pass for these two providers specifically
    — every OTHER field of every row is.
    """
    run_dir = Path(context.output_dir)
    signals_dir = run_dir / "signals"
    name = f"{tool_id}-{artifact_key}"
    manifest_path = signals_dir / f"{name}.manifest.json"
    if manifest_path.is_file():
        try:
            doc = json.loads(manifest_path.read_text("utf-8"))
        except (OSError, ValueError):
            doc = {}
        status = str(doc.get("status", "failed"))
        reason = str(doc.get("reason", ""))
        view_path = signals_dir / f"{name}.view.txt"
        refs = _signal_artifact_refs(
            run_dir, manifest_path, view_path if view_path.is_file() else None)
        return status, reason, refs

    result = context.tool_access.execute(tool_id, target, tooldef=tooldef)
    raw_status = getattr(result, "status", None)
    status = raw_status.value if hasattr(raw_status, "value") else str(raw_status or "failed")
    reason = getattr(result, "reason", "")
    refs = _signal_artifact_refs(
        run_dir, getattr(result, "manifest_path", None), getattr(result, "view_path", None))
    return status, reason, refs


@dataclass(frozen=True)
class GitHistoryProvider:
    """Wraps the ``git-history`` signal tool (57B-82 A2) via
    ``context.tool_access`` — see ``_run_or_reuse_signal`` above and
    ``contracts.ToolAccess``'s own docstring for the ``tooldef`` passthrough
    this needs: ``since``/``coupling_sample_cap`` are RUN-BOUND values
    (recorded once per run in ``RunContext.provenance["preparation"]``), not
    derivable from ``target`` alone the way ``registry.tool_for``'s default
    resolution assumes.

    ``universal`` (every repo is visited, git or not — a non-git verdict is
    POSITIVE provenance, never a skipped scan) and zero-profile, mirroring
    ``DeployUnitsProvider`` above: no detected TECHNOLOGY facet predicts
    whether a target is a git checkout at all — that's ``target.git.is_git``,
    a TargetSpec field, not something ``profiles.detection`` observes.
    """

    provider_id: str = "git-history"
    capability_id: str = "git-history"
    profile_ids: tuple[str, ...] = ()
    universal: bool = True

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        if not target.git.is_git:
            return CapabilityResult(
                capability_id=self.capability_id, provider_id=self.provider_id,
                repo_id=target.repo_id,
                coverage=Coverage(
                    applicability="not-applicable", status="complete",
                    reason_code="git-history-non-git",
                    # Verbatim match to discovery/emit.py's own disclosure
                    # (its ``reduced.append(...)`` line) — same positive
                    # provenance proof, not a paraphrase.
                    detail="non-git folder: targeted with reduced coverage — "
                           "no history lane, non-reproducible citations, "
                           "no caching"),
                facet_provenance=_facet_provenance(target, self.profile_ids),
            )

        # Function-local import: same registry<->profiles circular-import
        # trap DatastoreEvidenceProvider/DeployUnitsProvider document above
        # (registry.py itself only ever imports profiles.selection lazily).
        from ..registry import git_history

        identities = _identities(context, self.provider_id)
        artifact_key = identities.artifact_key_for(target.repo_id)
        preparation = (context.provenance or {}).get("preparation") or {}
        since = preparation.get("history_since") or None
        coupling_sample_cap = int(preparation.get("coupling_sample_cap") or 0)
        tooldef = git_history(target, since, coupling_sample_cap)

        status, reason, refs = _run_or_reuse_signal(
            context, "git-history", target, artifact_key, tooldef=tooldef)
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage_from_signal("git-history", status, reason),
            artifact_refs=refs,
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )


@dataclass(frozen=True)
class DependencyRiskProvider:
    """Replicates ``registry.network_tools``'s exact ecosystem gates (57B-82
    A2) as ONE capability: osv-scanner when the target declares ANY lockfile
    OR is a Go target; outdated when it's a Node target (yarn-vs-npm stays
    entirely inside ``registry.outdated`` — package-manager IDENTITY, not a
    facet predicate, per that function's own comment; no ``tooldef``
    passthrough is needed for either sub-tool, since neither depends on a
    run-bound value ``registry.tool_for``'s default resolution can't supply).

    A repo can select BOTH (e.g. a Node repo with a committed lockfile);
    this provider's own Coverage is the worst case across whichever ran, via
    :func:`~analysis_wrapper.evidence.coverage.aggregate` — so a
    network-unauthorized run's exact executor SKIPPED reason ("network-
    capable tool requires explicit authorization") surfaces unmodified in
    ``detail`` regardless of which sub-tool(s) produced it.

    ``universal`` + zero-profile, same rationale as ``GitHistoryProvider``:
    lockfile presence and Go/Node-ness are TargetSpec/facet questions this
    provider answers itself every run, not a pre-selected profile match.
    """

    provider_id: str = "dependency-risk"
    capability_id: str = "dependency-risk"
    profile_ids: tuple[str, ...] = ()
    universal: bool = True

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        run_osv = bool(target.pm.lockfile) or is_go_target(target)
        run_outdated = is_node_target(target)
        if not run_osv and not run_outdated:
            return CapabilityResult(
                capability_id=self.capability_id, provider_id=self.provider_id,
                repo_id=target.repo_id,
                coverage=Coverage(
                    applicability="not-applicable", status="complete",
                    reason_code="dependency-risk-not-applicable",
                    detail="no declared lockfile and neither a Go nor a Node "
                           "target — no dependency-risk tool applies"),
                facet_provenance=_facet_provenance(target, self.profile_ids),
            )

        identities = _identities(context, self.provider_id)
        artifact_key = identities.artifact_key_for(target.repo_id)
        coverages: list[Coverage] = []
        refs: list[ArtifactRef] = []
        if run_osv:
            status, reason, tool_refs = _run_or_reuse_signal(
                context, "osv-scanner", target, artifact_key)
            coverages.append(_coverage_from_signal("dependency-risk-osv", status, reason))
            refs.extend(tool_refs)
        if run_outdated:
            status, reason, tool_refs = _run_or_reuse_signal(
                context, "outdated", target, artifact_key)
            coverages.append(_coverage_from_signal("dependency-risk-outdated", status, reason))
            refs.extend(tool_refs)
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id, coverage=aggregate(coverages),
            artifact_refs=tuple(refs),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )


@dataclass(frozen=True)
class RouteInventoryProvider:
    """Wraps :func:`analysis_wrapper.discovery.liveness.route_registrations`
    as a per-repo route FRAGMENT (57B-84 B2).

    Unlike the four single-artifact universal providers above, route
    liveness needs a cross-repo JOIN (every frontend's UI calls against
    every backend's routes) computed ONCE per run, not once per repo — so
    this provider (and ``UiRouteLinkageProvider`` below) write a per-repo
    FRAGMENT under ``routes/.fragments/``, and
    :func:`analysis_wrapper.routes.emit.assemble` (called once,
    post-provider-loop) performs the join and writes the two canonical run
    artifacts. This mirrors the callgraph/depmap fragment+assemble shape,
    not the datastore/access/integration/deploy single-artifact one.

    ``universal`` + zero profiles for the same reason as
    ``AccessEvidenceProvider``: whether a repo is a route "backend" is
    decided per-repo (an already-observed route signal, or an explicit
    ``route-inventory`` profile match), not by one detected technology
    facet — see ``_has_module_signal_routes`` for why that signal is READ
    BACK rather than rescanned. Legacy ``discover()``'s own backend gate,
    replicated exactly: ``has_routes or target.profiles_for_capability(
    "route-inventory")``.

    A non-backend repo still gets a fragment (``applicable: false``, empty
    rows) — disclosure of "this repo was scanned and is not a backend",
    not an omission; ``routes.emit.assemble`` filters on ``applicable``.
    """

    provider_id: str = "route-inventory"
    capability_id: str = "route-inventory"
    profile_ids: tuple[str, ...] = ()
    universal: bool = True

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        # Function-local import: same profiles<->discovery circular-import
        # trap as DatastoreEvidenceProvider's own import of discovery.tables.
        from ..discovery import liveness

        identities = _identities(context, self.provider_id)
        repository_ref = identities.reference_for(target.repo_id)
        artifact_key = identities.artifact_key_for(target.repo_id)
        run_dir = Path(context.output_dir)

        has_routes = _has_module_signal_routes(run_dir, identities, repository_ref)
        applicable = bool(
            has_routes or target.profiles_for_capability("route-inventory"))

        rows: list[dict] = []
        notes: list[str] = []
        if applicable:
            stats: dict = {"file_cap_hit": False, "oversized": 0}
            hits = liveness.route_registrations(
                target.path, target.tier2_exclusions, stats, include_mounts=True)
            rows = sorted(({
                "method": hit.method, "path": hit.path,
                "route_evidence": hit.evidence,
                "registration_kind": (
                    "mount" if hit.method.upper() in liveness._MOUNTS else "endpoint"),
            } for hit in hits), key=lambda row: (
                row["method"], row["path"], row["route_evidence"]))
            if stats["file_cap_hit"] or stats["oversized"]:
                notes.append(
                    f"{repository_ref}: COVERAGE CAP in fallback route scan "
                    f"(file_cap={stats['file_cap_hit']}, oversized={stats['oversized']})")

        routes_dir = create_stage_dir(run_dir / "routes")
        fragments_dir = create_stage_dir(routes_dir / ".fragments")
        path = fragments_dir / f"{artifact_key}.routes.json"
        _write_json(path, {
            "artifact_key": artifact_key, "repository_ref": repository_ref,
            "applicable": applicable, "rows": rows, "notes": notes,
        })
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage_from_route_fragment(notes, reason_prefix="route-inventory"),
            artifact_refs=(_artifact_ref(path, run_dir, "route-inventory-fragment"),),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )


@dataclass(frozen=True)
class UiRouteLinkageProvider:
    """Wraps :func:`analysis_wrapper.discovery.liveness.ui_call_sites` as a
    per-repo frontend FRAGMENT (57B-84 B2). See ``RouteInventoryProvider``'s
    docstring for the shared fragment+assemble rationale.

    Frontend gate, replicated exactly from legacy ``discover()``: an
    explicit ``ui-route-linkage`` profile match, OR (no ``route-inventory``
    profile match AND a ts/js/tsx stack AND a ``src/`` dir AND this repo has
    no own registered routes). ``has_routes`` here reads the SAME
    ``module_signals.routes`` signal ``RouteInventoryProvider`` reads,
    independently — the two providers may execute in either order within a
    run, so neither can depend on the other's fragment already existing on
    disk.

    A non-frontend repo still gets a fragment (``applicable: false``, no
    calls) for the same disclosure reason as ``RouteInventoryProvider``'s
    own non-backend fragment.
    """

    provider_id: str = "ui-route-linkage"
    capability_id: str = "ui-route-linkage"
    profile_ids: tuple[str, ...] = ()
    universal: bool = True

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        # Function-local import: same profiles<->discovery circular-import
        # trap as DatastoreEvidenceProvider's own import of discovery.tables.
        from ..discovery import liveness

        identities = _identities(context, self.provider_id)
        repository_ref = identities.reference_for(target.repo_id)
        artifact_key = identities.artifact_key_for(target.repo_id)
        run_dir = Path(context.output_dir)

        has_routes = _has_module_signal_routes(run_dir, identities, repository_ref)
        stacks_l = {s.lower() for s in target.stacks}
        applicable = bool(
            target.profiles_for_capability("ui-route-linkage") or
            (not target.profiles_for_capability("route-inventory") and
             stacks_l & {"ts", "tsx", "js"} and
             (Path(target.path) / "src").is_dir() and not has_routes))

        calls: list[dict] = []
        notes: list[str] = []
        if applicable:
            stats: dict = {"file_cap_hit": False, "oversized": 0}
            hits = liveness.ui_call_sites(target.path, stats)
            calls = [{"base": hit.base, "path": hit.path,
                      "evidence": hit.evidence, "method": hit.method}
                     for hit in hits]
            if stats["file_cap_hit"] or stats["oversized"]:
                notes.append(
                    "COVERAGE CAP in ui-call-site scan "
                    f"(file_cap={stats['file_cap_hit']}, oversized={stats['oversized']})")

        routes_dir = create_stage_dir(run_dir / "routes")
        fragments_dir = create_stage_dir(routes_dir / ".fragments")
        path = fragments_dir / f"{artifact_key}.uicalls.json"
        _write_json(path, {
            "artifact_key": artifact_key, "repository_ref": repository_ref,
            "applicable": applicable, "calls": calls, "notes": notes,
        })
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage_from_route_fragment(notes, reason_prefix="ui-route-linkage"),
            artifact_refs=(_artifact_ref(path, run_dir, "ui-route-linkage-fragment"),),
            facet_provenance=_facet_provenance(target, self.profile_ids),
        )
