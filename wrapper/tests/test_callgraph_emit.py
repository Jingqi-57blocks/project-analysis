"""Call-graph emit: lane selection, artifact layout, determinism."""

import json
import shutil
import subprocess

import pytest

from analysis_wrapper import identity, node_env
from analysis_wrapper.callgraph import emit, js_lane
from analysis_wrapper.targetspec import (GitProvenance, RepoTarget, TargetSpec,
                                         stable_repo_id)

_NODE = shutil.which("node")
_TS = node_env.typescript_lib().exists()
requires_node = pytest.mark.skipif(
    not (_NODE and _TS and js_lane._HELPER.is_file()),
    reason="node / analyzer typescript / extractor helper not available")


def test_select_lanes_by_stack_and_manifest(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    go_target = RepoTarget(repo_id="g", path=str(tmp_path), stacks=["go"])
    assert emit.select_lanes(go_target) == ["go"]

    js_dir = tmp_path / "js"
    js_dir.mkdir()
    (js_dir / "package.json").write_text("{}\n")
    js_target = RepoTarget(repo_id="j", path=str(js_dir), stacks=[])
    assert emit.select_lanes(js_target) == ["js"]

    other = tmp_path / "other"
    other.mkdir()
    assert emit.select_lanes(RepoTarget(repo_id="o", path=str(other), stacks=["rust"])) == []


def _js_spec(tmp_path):
    repo = tmp_path / "widget"
    (repo / "src").mkdir(parents=True)
    (repo / "tsconfig.json").write_text(
        '{"compilerOptions":{"module":"commonjs","target":"es2020"},"include":["src"]}\n')
    (repo / "src" / "b.ts").write_text("export function helper(): number { return 1; }\n")
    (repo / "src" / "a.ts").write_text(
        "import { helper } from './b';\nexport function run(): void { helper(); }\n")
    target = RepoTarget(repo_id="widget", path=str(repo), stacks=["ts"],
                        git=GitProvenance(head="a" * 40))
    return TargetSpec(repos=[target]), repo


def _identity(out, spec, workspace):
    out.mkdir()
    spec.save(out / "targets.json")
    mapping = identity.build(
        spec, workspace_root=workspace,
        project_id=stable_repo_id(str(workspace)))
    identity.write_mapping(out, mapping)
    return mapping


@requires_node
def test_run_callgraph_writes_edges_and_coverage(tmp_path):
    spec, _repo = _js_spec(tmp_path)
    out = tmp_path / "out"
    identities = _identity(out, spec, tmp_path)
    report = emit.run_callgraph(
        spec, out, "2026-07-17", identities=identities)

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
def test_run_callgraph_is_byte_for_byte_deterministic(tmp_path):
    spec, _repo = _js_spec(tmp_path)
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    identities_a = _identity(out_a, spec, tmp_path)
    identities_b = _identity(out_b, spec, tmp_path)
    emit.run_callgraph(
        spec, out_a, "2026-07-17", identities=identities_a)
    emit.run_callgraph(
        spec, out_b, "2026-07-17", identities=identities_b)
    assert (out_a / "callgraph" / "widget.jsonl").read_bytes() == \
           (out_b / "callgraph" / "widget.jsonl").read_bytes()
    assert (out_a / "callgraph-coverage.json").read_bytes() == \
           (out_b / "callgraph-coverage.json").read_bytes()


def test_run_callgraph_skips_unsupported_repos(tmp_path):
    other = tmp_path / "docs"
    other.mkdir()
    (other / "readme.md").write_text("# hi\n")
    spec = TargetSpec(repos=[RepoTarget(repo_id="docs", path=str(other), stacks=["md"])])
    out = tmp_path / "out"
    identities = _identity(out, spec, tmp_path)
    report = emit.run_callgraph(
        spec, out, "2026-07-17", identities=identities)
    assert report.repos == []
    assert not (out / "callgraph" / "docs.jsonl").exists()
    assert (out / "callgraph-coverage.json").is_file()
