"""Call-graph emit: facet-driven provider selection, fragment assembly,
determinism (57B-81 PR2 — lane selection moved off the old stack/manifest
selector onto the provider/facet architecture; see
``tests/test_lane_providers.py`` for the polyglot merge case and the four
providers' conformance coverage)."""

import json
import shutil

import pytest

from analysis_wrapper import identity, node_env
from analysis_wrapper.callgraph import emit, js_lane
from analysis_wrapper.profiles.bundled import BUNDLED_PROFILES, BUNDLED_PROVIDERS
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.execution import run_providers
from analysis_wrapper.profiles.registry import ProfileRegistry
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.targetspec import (GitProvenance, RepoTarget, TargetSpec,
                                         TechnologyFacet, stable_repo_id)

_NODE = shutil.which("node")
_TS = node_env.typescript_lib().exists()
requires_node = pytest.mark.skipif(
    not (_NODE and _TS and js_lane._HELPER.is_file()),
    reason="node / analyzer typescript / extractor helper not available")

_CALLGRAPH_PROVIDERS = tuple(p for p in BUNDLED_PROVIDERS if p.capability_id == "callgraph")


def test_callgraph_providers_are_selected_by_facet_not_by_stack_or_manifest(tmp_path):
    """The lane a repo gets is now entirely a function of its DETECTED
    facets (the old lane selector used to also sniff go.mod/package.json
    presence directly; that fallback is gone — a repo's facets are the
    single source of truth)."""
    (tmp_path / "go.mod").write_text("module x\n")
    go_target = RepoTarget(repo_id="g", path=str(tmp_path), facets=[
        TechnologyFacet("language.go", "language", ["."], ["go.mod"])
    ])
    assert emit._lanes(go_target) == ["go"]

    js_dir = tmp_path / "js"
    js_dir.mkdir()
    (js_dir / "package.json").write_text("{}\n")
    js_target = RepoTarget(repo_id="j", path=str(js_dir), facets=[
        TechnologyFacet("language.javascript", "language", ["."], ["package.json"])
    ])
    assert emit._lanes(js_target) == ["js"]

    # A manifest present with NO matching facet selects nothing — facets, not
    # file sniffing, decide applicability.
    unfaceted = tmp_path / "unfaceted"
    unfaceted.mkdir()
    (unfaceted / "package.json").write_text("{}\n")
    assert emit._lanes(RepoTarget(repo_id="u", path=str(unfaceted))) == []

    other = tmp_path / "other"
    other.mkdir()
    assert emit._lanes(RepoTarget(repo_id="o", path=str(other))) == []

    # The SAME facets select the matching bundled providers through the real
    # registry (what production actually runs), not just the legacy helper.
    assert {p.provider_id for p in BUNDLED_PROVIDERS
           if p.capability_id == "callgraph"
           and set(go_target.profiles_for_capability("callgraph")) & set(p.profile_ids)
           } == {"callgraph-go"}
    assert {p.provider_id for p in BUNDLED_PROVIDERS
           if p.capability_id == "callgraph"
           and set(js_target.profiles_for_capability("callgraph")) & set(p.profile_ids)
           } == {"callgraph-js"}


def _js_spec(tmp_path):
    repo = tmp_path / "widget"
    (repo / "src").mkdir(parents=True)
    (repo / "tsconfig.json").write_text(
        '{"compilerOptions":{"module":"commonjs","target":"es2020"},"include":["src"]}\n')
    (repo / "src" / "b.ts").write_text("export function helper(): number { return 1; }\n")
    (repo / "src" / "a.ts").write_text(
        "import { helper } from './b';\nexport function run(): void { helper(); }\n")
    target = RepoTarget(repo_id="widget", path=str(repo), facets=[
                            TechnologyFacet("language.typescript", "language",
                                            ["src"], ["tsconfig.json"])
                        ],
                        git=GitProvenance(head="a" * 40))
    return TargetSpec(repos=[target]), repo


def _identity_map(out, spec, workspace):
    out.mkdir()
    spec.save(out / "targets.json")
    mapping = identity.build(
        spec, workspace_root=workspace,
        project_id=stable_repo_id(str(workspace)))
    identity.write_mapping(out, mapping)
    return mapping


def _run_callgraph_providers(spec, out, identities, *, scan_date):
    """Drive the real production path: the loop over the callgraph-only
    provider set, then the fragment assembler — end to end, no stubbing."""
    access = ExecutorToolAccess(spec, identities, out, scan_date, network_authorized=False)
    context = RunContext(
        targets=spec, output_dir=out, scan_date=scan_date,
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )
    registry = ProfileRegistry(BUNDLED_PROFILES, _CALLGRAPH_PROVIDERS)
    run_providers(registry, context)
    return emit.assemble(out, scan_date)


@requires_node
def test_callgraph_providers_write_edges_and_coverage(tmp_path):
    spec, _repo = _js_spec(tmp_path)
    out = tmp_path / "out"
    identities = _identity_map(out, spec, tmp_path)
    report = _run_callgraph_providers(spec, out, identities, scan_date="2026-07-17")

    jsonl = out / "callgraph" / "widget.jsonl"
    coverage = out / "callgraph-coverage.json"
    assert jsonl.is_file() and coverage.is_file()

    lines = [json.loads(x) for x in jsonl.read_text().splitlines()]
    assert any(e["callee_symbol"] == "helper" and e["kind"] == "static-call" for e in lines)

    data = json.loads(coverage.read_text())
    assert data["scan_date"] == "2026-07-17"
    entry = next(r for r in data["repos"] if r["repository_ref"] == "widget")
    assert entry["status"] == "complete"
    assert entry["call_sites"]["resolved"] >= 1
    assert len(report.repos) == 1


@requires_node
def test_callgraph_providers_are_byte_for_byte_deterministic(tmp_path):
    spec, _repo = _js_spec(tmp_path)
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    identities_a = _identity_map(out_a, spec, tmp_path)
    identities_b = _identity_map(out_b, spec, tmp_path)
    _run_callgraph_providers(spec, out_a, identities_a, scan_date="2026-07-17")
    _run_callgraph_providers(spec, out_b, identities_b, scan_date="2026-07-17")
    assert (out_a / "callgraph" / "widget.jsonl").read_bytes() == \
           (out_b / "callgraph" / "widget.jsonl").read_bytes()
    assert (out_a / "callgraph-coverage.json").read_bytes() == \
           (out_b / "callgraph-coverage.json").read_bytes()


def test_assemble_skips_unsupported_repos_but_still_writes_coverage(tmp_path):
    """A repo with no callgraph facet runs no provider — zero fragments — and
    the assembler still writes the (empty) canonical coverage doc rather than
    omitting it."""
    other = tmp_path / "docs"
    other.mkdir()
    (other / "readme.md").write_text("# hi\n")
    spec = TargetSpec(repos=[RepoTarget(repo_id="docs", path=str(other))])
    out = tmp_path / "out"
    identities = _identity_map(out, spec, tmp_path)
    report = _run_callgraph_providers(spec, out, identities, scan_date="2026-07-17")
    assert report.repos == []
    assert not (out / "callgraph" / "docs.jsonl").exists()
    assert (out / "callgraph-coverage.json").is_file()


def test_assembler_has_no_technology_branching_literals():
    """The merge/assemble path is technology-neutral: "go"/"js" only ever
    travel as DATA inside a fragment, never as a branching literal in the
    assembler itself (mirrors the loop's own banned-literal test)."""
    import inspect
    source = inspect.getsource(emit.assemble) + inspect.getsource(
        emit._repo_coverage_from_row)
    lowered = source.lower()
    for literal in ('"go"', "'go'", '"js"', "'js'", "javascript", "typescript"):
        assert literal not in lowered, literal
