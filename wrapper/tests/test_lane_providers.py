"""57B-81 PR2: the four bundled callgraph/dependency-map providers.

Two concerns:

1. Each of the four providers passes the shared conformance battery
   (``provider_conformance.run_provider_conformance``) against its REAL
   bundled profile(s) — the lane calls are monkeypatched (deterministic, no
   go/node toolchain needed), matching how the battery already runs for every
   other provider migration.

2. A polyglot repo (one carrying BOTH a Go and a JavaScript facet) drives the
   real callgraph/dependency-map provider loop + assemblers end to end and
   proves the two lanes merge/coexist correctly: one merged, deduped,
   sorted ``<artifact-key>.jsonl`` for callgraph; two independent,
   non-colliding map files for dependency-map.

Conformance wiring note (see the module-level ``_JS_PROFILE``/``_TS_PROFILE``
below): the shared battery's ``make_repo`` helper detects a profile by
touching exactly ONE marker file named after that profile's
``fingerprints[0].value``. The REAL bundled ``language.javascript`` and
``language.typescript`` profiles both lead with a ``source-extension``
fingerprint (e.g. ``.js``), and a file literally named ``.js`` has no
extension by ``pathlib``'s own reckoning — so the single-marker mechanism can
never detect them through that fingerprint. This module therefore builds its
own local ``Profile`` objects carrying the SAME profile_id/capability_ids as
the real bundled ones (so ``ProfileRegistry`` validation and the battery's
``is_bundled`` routing to the REAL ``detection.detect()`` both still apply
honestly) but with fingerprints reordered/trimmed so the single marker file
IS something the real detector recognizes (``package.json`` /
``manifest-default`` for JS; ``tsconfig.json`` / ``config-file`` for TS).
"""

from __future__ import annotations

import json

from analysis_wrapper import identity
from analysis_wrapper.callgraph import emit as cg_emit
from analysis_wrapper.callgraph import go_lane as cg_go_lane
from analysis_wrapper.callgraph import js_lane as cg_js_lane
from analysis_wrapper.callgraph.contract import CallEdge, RepoCoverage
from analysis_wrapper.depmap import emit as dm_emit
from analysis_wrapper.depmap import go_lane as dm_go_lane
from analysis_wrapper.depmap import js_lane as dm_js_lane
from analysis_wrapper.depmap.contract import RepoDepCoverage
from analysis_wrapper.profiles.bundled import (BUNDLED_PROFILES, BUNDLED_PROVIDERS,
                                               bundled_registry)
from analysis_wrapper.profiles.contracts import Fingerprint, Profile, RunContext
from analysis_wrapper.profiles.execution import run_providers
from analysis_wrapper.profiles.providers import (
    CallgraphGoProvider,
    CallgraphJsProvider,
    DepmapGoProvider,
    DepmapJsProvider,
)
from analysis_wrapper.profiles.registry import ProfileRegistry
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.targetspec import (GitProvenance, RepoTarget, TargetSpec,
                                         TechnologyFacet, stable_repo_id)
from provider_conformance import run_provider_conformance

_HEAD = "c" * 40
_CALLGRAPH_PROVIDERS = tuple(p for p in BUNDLED_PROVIDERS if p.capability_id == "callgraph")
_DEPMAP_PROVIDERS = tuple(p for p in BUNDLED_PROVIDERS if p.capability_id == "dependency-map")


# ---------------------------------------------------------------------------
# Conformance — all four bundled providers, against their real profiles.
# ---------------------------------------------------------------------------

_GO_PROFILE = bundled_registry().profile("language.go")
_JS_PROFILE = Profile(
    profile_id="language.javascript", kind="language", display_name="js",
    fingerprints=(Fingerprint("manifest-default", "package.json"),),
    capability_ids=("callgraph", "dependency-map"),
)
_TS_PROFILE = Profile(
    profile_id="language.typescript", kind="language", display_name="ts",
    fingerprints=(Fingerprint("config-file", "tsconfig.json"),),
    capability_ids=("callgraph", "dependency-map"),
)


def _edge(n: int, lang: str, repository_ref: str) -> CallEdge:
    return CallEdge(
        lang=lang, resolution="observed", kind="static-call",
        caller_symbol=f"caller{n}", caller_citation=f"{repository_ref}@{_HEAD}:main.{lang}:{n}",
        callee_symbol=f"callee{n}", callee_citation=f"{repository_ref}@{_HEAD}:util.{lang}:{n}",
        callsite_citation=f"{repository_ref}@{_HEAD}:main.{lang}:{n + 1}:2",
    )


def _stub_go_callgraph_analyze(target, *, repository_ref, allow_network=False, run=None, **_ignored):
    return [_edge(1, "go", repository_ref)], RepoCoverage(
        repository_ref=repository_ref, lang="go", status="complete",
        candidates_by_ext={".go": 1}, analyzed_by_ext={".go": 1}, edges_emitted=1)


def _stub_js_callgraph_analyze(target, *, repository_ref, run=None, **_ignored):
    return [_edge(1, "js", repository_ref)], RepoCoverage(
        repository_ref=repository_ref, lang="js", status="complete",
        candidates_by_ext={".js": 1}, analyzed_by_ext={".js": 1}, edges_emitted=1)


def _stub_go_depmap_analyze(target, *, repository_ref, artifact_key, allow_network=False,
                            run=None, go_binary=None, timeout_s=300):
    payload = {"module": "example.com/conformance", "packages": [], "stdlib": []}
    cov = RepoDepCoverage(repository_ref=repository_ref, lane="go", status="complete",
                          map_file=f"{artifact_key}.golist.json", units=0)
    return payload, cov


def _stub_js_depmap_analyze(target, out_dir, *, repository_ref, artifact_key,
                            run=None, timeout_s=None):
    payload = {"modules": [], "internal_sources": []}
    cov = RepoDepCoverage(repository_ref=repository_ref, lane="js", status="complete",
                          map_file=f"{artifact_key}.depcruise.json", units=0)
    return payload, cov


def test_callgraph_go_provider_conforms(tmp_path, monkeypatch):
    monkeypatch.setattr(cg_go_lane, "analyze", _stub_go_callgraph_analyze)
    run_provider_conformance(_GO_PROFILE, CallgraphGoProvider(), tmp_path=tmp_path)


def test_depmap_go_provider_conforms(tmp_path, monkeypatch):
    monkeypatch.setattr(dm_go_lane, "analyze", _stub_go_depmap_analyze)
    run_provider_conformance(_GO_PROFILE, DepmapGoProvider(), tmp_path=tmp_path)


def test_callgraph_js_provider_conforms_via_javascript_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(cg_js_lane, "analyze", _stub_js_callgraph_analyze)
    run_provider_conformance(_JS_PROFILE, CallgraphJsProvider(), tmp_path=tmp_path,
                             extra_profiles=(_TS_PROFILE,))


def test_callgraph_js_provider_conforms_via_typescript_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(cg_js_lane, "analyze", _stub_js_callgraph_analyze)
    run_provider_conformance(_TS_PROFILE, CallgraphJsProvider(), tmp_path=tmp_path,
                             extra_profiles=(_JS_PROFILE,))


def test_depmap_js_provider_conforms_via_javascript_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(dm_js_lane, "analyze", _stub_js_depmap_analyze)
    run_provider_conformance(_JS_PROFILE, DepmapJsProvider(), tmp_path=tmp_path,
                             extra_profiles=(_TS_PROFILE,))


def test_depmap_js_provider_conforms_via_typescript_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(dm_js_lane, "analyze", _stub_js_depmap_analyze)
    run_provider_conformance(_TS_PROFILE, DepmapJsProvider(), tmp_path=tmp_path,
                             extra_profiles=(_JS_PROFILE,))


def test_bundled_providers_profile_ids_declare_their_capability():
    """Pin the provider<->profile capability linkage for EVERY bundled
    provider against the REAL bundled profiles — not just the ones a
    conformance test happens to exercise with one (the conformance tests
    above use locally-built stand-in profiles for language.javascript/
    language.typescript so a single marker file can drive real detection;
    they never check the REAL bundled language.typescript profile's own
    capability_ids against CallgraphJsProvider/DepmapJsProvider).

    ``ProfileRegistry`` already refuses to construct if this is violated, so
    ``bundled_registry()`` succeeding is an IMPLICIT proof — but a violation
    would then surface as an unrelated-looking ValueError in whichever test
    happens to call ``bundled_registry()`` first, not as a clear, targeted
    signal. This test exists so a future edit that drops a capability
    declaration from a profile without updating the provider(s) linked to it
    fails loudly and specifically here: without this check, execution
    (facet-driven selection) and accounting (capabilities.py/system_model
    coverage, which read a profile's OWN capability_ids) could silently
    split — the provider keeps running while the capability's own coverage
    partition flips to not-applicable.
    """
    registry = bundled_registry()
    assert BUNDLED_PROVIDERS
    for provider in BUNDLED_PROVIDERS:
        for profile_id in provider.profile_ids:
            profile = registry.profile(profile_id)
            assert provider.capability_id in profile.capability_ids, (
                f"{provider.provider_id!r} links to profile {profile_id!r}, "
                f"which does not declare capability {provider.capability_id!r} "
                f"(declares {profile.capability_ids!r})"
            )


# ---------------------------------------------------------------------------
# Polyglot fixture — one repo, two facets, two providers per capability.
# ---------------------------------------------------------------------------

def _polyglot_repo(tmp_path) -> RepoTarget:
    repo = tmp_path / "fullstack"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/fullstack\n")
    (repo / "package.json").write_text("{}\n")
    return RepoTarget(
        repo_id=stable_repo_id(str(repo)), path=str(repo),
        facets=[
            TechnologyFacet("language.go", "language", ["."], ["go.mod"]),
            TechnologyFacet("language.javascript", "language", ["."], ["package.json"]),
        ],
        git=GitProvenance(head=_HEAD),
    )


def _run_capability_loop(spec, out, identities, providers, *, scan_date):
    out.mkdir()
    access = ExecutorToolAccess(spec, identities, out, scan_date, network_authorized=False)
    context = RunContext(
        targets=spec, output_dir=out, scan_date=scan_date,
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )
    registry = ProfileRegistry(BUNDLED_PROFILES, providers)
    return run_providers(registry, context)


def test_polyglot_repo_merges_both_callgraph_lanes_into_one_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(cg_go_lane, "analyze", _stub_go_callgraph_analyze)
    monkeypatch.setattr(cg_js_lane, "analyze", _stub_js_callgraph_analyze)

    repo = _polyglot_repo(tmp_path)
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))
    out = tmp_path / "run"

    results, rows = _run_capability_loop(
        spec, out, identities, _CALLGRAPH_PROVIDERS, scan_date="2026-07-23")
    assert {row["provider_id"] for row in rows} == {"callgraph-go", "callgraph-js"}
    assert all(row["outcome"] == "completed" for row in rows)
    assert len(results) == 2

    report = cg_emit.assemble(out, "2026-07-23")
    key = identities.artifact_key_for(repo.repo_id)
    jsonl = out / "callgraph" / f"{key}.jsonl"
    assert jsonl.is_file()
    lines = [json.loads(line) for line in jsonl.read_text("utf-8").splitlines()]
    assert len(lines) == 2                     # one go edge + one js edge, none duplicated
    assert {line["lang"] for line in lines} == {"go", "js"}
    assert len(lines) == len({json.dumps(line, sort_keys=True) for line in lines})
    # No stray per-lane files alongside the merged one.
    assert sorted(p.name for p in (out / "callgraph").glob("*.jsonl")) == [f"{key}.jsonl"]

    assert len(report.repos) == 2
    assert {cov.lang for cov in report.repos} == {"go", "js"}
    assert all(cov.repository_ref == identities.reference_for(repo.repo_id)
              for cov in report.repos)


def test_polyglot_repo_callgraph_is_byte_deterministic_across_two_full_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(cg_go_lane, "analyze", _stub_go_callgraph_analyze)
    monkeypatch.setattr(cg_js_lane, "analyze", _stub_js_callgraph_analyze)

    repo = _polyglot_repo(tmp_path)
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))
    key = identities.artifact_key_for(repo.repo_id)

    out_a, out_b = tmp_path / "run-a", tmp_path / "run-b"
    _run_capability_loop(spec, out_a, identities, _CALLGRAPH_PROVIDERS, scan_date="2026-07-23")
    cg_emit.assemble(out_a, "2026-07-23")
    _run_capability_loop(spec, out_b, identities, _CALLGRAPH_PROVIDERS, scan_date="2026-07-23")
    cg_emit.assemble(out_b, "2026-07-23")

    assert (out_a / "callgraph" / f"{key}.jsonl").read_bytes() == \
           (out_b / "callgraph" / f"{key}.jsonl").read_bytes()
    assert (out_a / "callgraph-coverage.json").read_bytes() == \
           (out_b / "callgraph-coverage.json").read_bytes()


def test_polyglot_repo_dependency_map_writes_two_non_colliding_maps(tmp_path, monkeypatch):
    monkeypatch.setattr(dm_go_lane, "analyze", _stub_go_depmap_analyze)
    monkeypatch.setattr(dm_js_lane, "analyze", _stub_js_depmap_analyze)

    repo = _polyglot_repo(tmp_path)
    spec = TargetSpec([repo])
    identities = identity.build(
        spec, workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))
    out = tmp_path / "run"

    results, rows = _run_capability_loop(
        spec, out, identities, _DEPMAP_PROVIDERS, scan_date="2026-07-23")
    assert {row["provider_id"] for row in rows} == {"depmap-go", "depmap-js"}
    assert len(results) == 2

    report = dm_emit.assemble(out, "2026-07-23")
    key = identities.artifact_key_for(repo.repo_id)
    assert (out / "imports" / f"{key}.golist.json").is_file()
    assert (out / "imports" / f"{key}.depcruise.json").is_file()
    assert len(report.repos) == 2
    assert {cov.lane for cov in report.repos} == {"go", "js"}
