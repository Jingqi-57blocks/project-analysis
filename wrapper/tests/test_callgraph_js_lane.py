"""JS/TS lane: real node extractor on a synthetic TS fixture + fail-closed."""

import shutil

import pytest

from analysis_wrapper import node_env
from analysis_wrapper.callgraph import js_lane
from analysis_wrapper.targetspec import GitProvenance, RepoTarget, TechnologyFacet

_NODE = shutil.which("node")
_TS = node_env.typescript_lib().exists()
requires_node = pytest.mark.skipif(
    not (_NODE and _TS and js_lane._HELPER.is_file()),
    reason="node / analyzer typescript / extractor helper not available")


def _fixture(tmp_path):
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"strict":true,"module":"commonjs","target":"es2020"},'
        '"include":["src"]}\n')
    src = tmp_path / "src"
    src.mkdir()
    (src / "b.ts").write_text(
        "export function helper(): number { return 1; }\n"
        "export class Widget { build(): void {} }\n")
    (src / "a.ts").write_text(
        "import { helper, Widget } from './b';\n"
        "export function run(): void {\n"
        "  helper();\n"               # resolved-internal cross-file call
        "  const w = new Widget();\n"  # constructor (internal)
        "  w.build();\n"               # method-dispatch (internal)
        "  console.log('x');\n"        # external (lib)
        "  const dyn: any = {};\n"
        "  dyn.foo();\n"               # unresolved (dynamic)
        "}\n")
    # Excluded families must not be analyzed or emitted.
    (src / "a.test.ts").write_text("import {run} from './a'; run();\n")
    (src / "mock").mkdir()
    (src / "mock" / "m.ts").write_text("export const m = 1;\n")
    return RepoTarget(repo_id="widget", path=str(tmp_path), facets=[
        TechnologyFacet("language.typescript", "language", ["src"], ["tsconfig.json"])
    ],
                      git=GitProvenance(head="a" * 40))


@requires_node
def test_extractor_classifies_and_emits_internal_edges(tmp_path):
    target = _fixture(tmp_path)
    edges, cov = js_lane.analyze(target, repository_ref="widget")

    assert cov.status == "complete"
    assert cov.algorithm == "tsconfig"
    assert cov.tool == "typescript" and cov.tool_version
    # Only the two production files are candidates/analyzed; test + mock excluded.
    assert cov.candidates_by_ext == {".ts": 2}
    assert cov.analyzed_by_ext == {".ts": 2}
    assert cov.excluded_by_reason.get("test") == 1
    assert cov.excluded_by_reason.get("mock") == 1

    kinds = {(e.callee_symbol, e.kind) for e in edges}
    assert ("helper", "static-call") in kinds
    assert ("Widget", "constructor") in kinds
    assert ("build", "method-dispatch") in kinds
    assert all(e.resolution == "observed" for e in edges)
    # No edge cites the excluded test/mock files.
    assert not any("a.test.ts" in e.callsite_citation or "mock/" in e.callsite_citation
                   for e in edges)

    assert cov.call_sites.resolved == 3
    assert cov.call_sites.external >= 1       # console.log
    assert cov.call_sites.unresolved >= 1     # dyn.foo()


@requires_node
def test_extractor_output_is_deterministic(tmp_path):
    target = _fixture(tmp_path)
    first, _ = js_lane.analyze(target, repository_ref="widget")
    second, _ = js_lane.analyze(target, repository_ref="widget")
    assert [e.to_json_line() for e in first] == [e.to_json_line() for e in second]


def test_analyze_unavailable_when_node_missing(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"name":"x"}\n')
    (tmp_path / "index.js").write_text("module.exports = 1;\n")
    monkeypatch.setattr(js_lane.shutil, "which", lambda _name: None)
    target = RepoTarget(repo_id="x", path=str(tmp_path), facets=[
        TechnologyFacet("language.javascript", "language", ["."], ["index.js"])
    ],
                        git=GitProvenance(head="a" * 40))

    def fail_run(*_a, **_k):
        raise AssertionError("extractor must not run when node is unavailable")

    edges, cov = js_lane.analyze(target, repository_ref="x", run=fail_run)
    assert edges == []
    assert cov.status == "unavailable"
    assert "unavailable" in cov.reason
