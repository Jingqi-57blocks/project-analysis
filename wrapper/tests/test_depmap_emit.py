"""depmap.emit: facet-driven provider selection, coverage assembly,
determinism (57B-81 PR2 — lane selection moved off the old stack/manifest
selector onto the provider/facet architecture; see
``tests/test_lane_providers.py`` for the four providers' conformance
coverage)."""

import json

from analysis_wrapper import identity
from analysis_wrapper.depmap import emit
from analysis_wrapper.depmap import go_lane as dm_go_lane
from analysis_wrapper.profiles.bundled import BUNDLED_PROFILES, BUNDLED_PROVIDERS
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.execution import run_providers
from analysis_wrapper.profiles.registry import ProfileRegistry
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.targetspec import (GitProvenance, RepoTarget, TargetSpec,
                                         TechnologyFacet, stable_repo_id)

_MODULE = "example.com/app"
_STREAM = "\n".join(json.dumps(o) for o in [
    {"ImportPath": "fmt", "Standard": True, "Dir": "/usr/local/go/src/fmt"},
    {"ImportPath": _MODULE, "Dir": "/home/u/app",
     "Imports": ["fmt", "example.com/app/internal/store"]},
    {"ImportPath": "example.com/app/internal/store", "Dir": "/home/u/app/internal/store",
     "Imports": ["fmt"]},
])

_DEPMAP_PROVIDERS = tuple(p for p in BUNDLED_PROVIDERS if p.capability_id == "dependency-map")


def _go_spec(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "go.mod").write_text(f"module {_MODULE}\n")
    target = RepoTarget(repo_id="app-1", path=str(repo), facets=[
                            TechnologyFacet("language.go", "language", ["."], ["go.mod"])
                        ],
                        git=GitProvenance(head="e" * 40))
    return TargetSpec(repos=[target]), repo


def _fake_go_run(argv, **kwargs):
    class _Proc:
        returncode = 0
        stdout = _STREAM
        stderr = ""
    return _Proc()


def _identities(spec, workspace):
    return identity.build(
        spec, workspace_root=workspace,
        project_id=stable_repo_id(str(workspace)))


def test_dependency_map_providers_are_selected_by_facet_not_by_stack_or_manifest(tmp_path):
    """The lane a repo gets is now entirely a function of its DETECTED
    facets (the old lane selector used to also sniff go.mod/package.json
    presence directly; that fallback is gone — a repo's facets are the
    single source of truth)."""
    (tmp_path / "go.mod").write_text("module x\n")
    go_target = RepoTarget(repo_id="g", path=str(tmp_path), facets=[
        TechnologyFacet("language.go", "language", ["."], ["go.mod"])
    ])
    assert emit._lanes(go_target) == ["go"]

    js = tmp_path / "js"
    js.mkdir()
    (js / "package.json").write_text("{}\n")
    js_target = RepoTarget(repo_id="j", path=str(js), facets=[
        TechnologyFacet("language.javascript", "language", ["."], ["package.json"])
    ])
    assert emit._lanes(js_target) == ["js"]

    unfaceted = tmp_path / "unfaceted"
    unfaceted.mkdir()
    (unfaceted / "package.json").write_text("{}\n")
    assert emit._lanes(RepoTarget(repo_id="u", path=str(unfaceted))) == []

    other = tmp_path / "other"
    other.mkdir()
    assert emit._lanes(RepoTarget(repo_id="o", path=str(other))) == []

    assert {p.provider_id for p in _DEPMAP_PROVIDERS
           if set(go_target.profiles_for_capability("dependency-map")) & set(p.profile_ids)
           } == {"depmap-go"}
    assert {p.provider_id for p in _DEPMAP_PROVIDERS
           if set(js_target.profiles_for_capability("dependency-map")) & set(p.profile_ids)
           } == {"depmap-js"}


def _run_depmap_providers(spec, out, identities, *, scan_date):
    """Drive the real production path: the loop over the depmap-only
    provider set, then the coverage assembler. ``DepmapGoProvider`` does not
    accept a fake ``run=`` callable itself (it calls the lane's ``analyze()``
    with only the kwargs listed in the design), so a test needing a fake
    ``go`` binary monkeypatches ``go_lane.analyze`` directly instead."""
    out.mkdir(parents=True, exist_ok=True)
    access = ExecutorToolAccess(spec, identities, out, scan_date, network_authorized=False)
    context = RunContext(
        targets=spec, output_dir=out, scan_date=scan_date,
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )
    registry = ProfileRegistry(BUNDLED_PROFILES, _DEPMAP_PROVIDERS)
    run_providers(registry, context)
    return emit.assemble(out, scan_date)


def _patch_go_lane_with_fake_run(monkeypatch):
    """Force the depmap Go provider's ``go_lane.analyze()`` call onto the
    fake ``go list`` subprocess stub: the provider itself never accepts a
    ``run=`` override (it calls the lane exactly as the design specifies), so
    the fake is injected by wrapping the ORIGINAL lane function and patching
    the module attribute the provider resolves at call time."""
    original = dm_go_lane.analyze

    def fake_analyze(target, *, repository_ref, artifact_key, allow_network=False,
                     run=None, go_binary=None, timeout_s=300):
        return original(
            target, repository_ref=repository_ref, artifact_key=artifact_key,
            allow_network=allow_network, run=_fake_go_run, timeout_s=timeout_s)

    monkeypatch.setattr(dm_go_lane, "analyze", fake_analyze)


def test_dependency_map_providers_write_go_map_and_coverage(tmp_path, monkeypatch):
    spec, _repo = _go_spec(tmp_path)
    out = tmp_path / "run"
    identities = _identities(spec, tmp_path)

    _patch_go_lane_with_fake_run(monkeypatch)
    report = _run_depmap_providers(spec, out, identities, scan_date="2026-07-18")

    golist = out / "imports" / "app.golist.json"
    coverage = out / "imports" / "depmap-coverage.json"
    assert golist.is_file() and coverage.is_file()

    payload = json.loads(golist.read_text())
    assert payload["module"] == _MODULE
    assert "/home/u" not in golist.read_text()        # leak-free projection
    cov = json.loads(coverage.read_text())
    entry = next(r for r in cov["repos"] if r["repository_ref"] == "app")
    assert entry["status"] == "complete" and entry["lane"] == "go"
    assert len(report.repos) == 1


def test_dependency_map_providers_are_byte_for_byte_deterministic(tmp_path, monkeypatch):
    spec, _repo = _go_spec(tmp_path)
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    identities = _identities(spec, tmp_path)

    _patch_go_lane_with_fake_run(monkeypatch)
    _run_depmap_providers(spec, out_a, identities, scan_date="2026-07-18")
    _run_depmap_providers(spec, out_b, identities, scan_date="2026-07-18")
    assert (out_a / "imports" / "app.golist.json").read_bytes() == \
           (out_b / "imports" / "app.golist.json").read_bytes()
    assert (out_a / "imports" / "depmap-coverage.json").read_bytes() == \
           (out_b / "imports" / "depmap-coverage.json").read_bytes()


def test_assemble_skips_unsupported_repos_but_still_writes_coverage(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    spec = TargetSpec(repos=[RepoTarget(repo_id="docs", path=str(docs))])
    out = tmp_path / "run"
    identities = _identities(spec, tmp_path)
    report = _run_depmap_providers(spec, out, identities, scan_date="2026-07-18")
    assert report.repos == []
    assert not (out / "imports" / "docs.golist.json").exists()
    assert (out / "imports" / "depmap-coverage.json").is_file()


def test_assembler_has_no_technology_branching_literals():
    """The coverage-merge path is technology-neutral: "go"/"js" only ever
    travel as DATA inside a fragment, never as a branching literal in the
    assembler itself (mirrors the loop's own banned-literal test)."""
    import inspect
    source = inspect.getsource(emit.assemble) + inspect.getsource(
        emit._repo_dep_coverage_from_row)
    lowered = source.lower()
    for literal in ('"go"', "'go'", '"js"', "'js'", "javascript", "typescript"):
        assert literal not in lowered, literal
