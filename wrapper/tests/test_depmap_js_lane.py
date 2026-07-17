"""depmap.js_lane — reuses the depcruise ToolDef, captures + sorts the full map.

The ToolDef is stubbed so the lane's glue is tested without the node_tools env:
the real reuse path (binary/guards/prepare/argv/env come from
depcruise_lane.dependency_cruiser) is exercised in the WCP smoke, not unit tests.
"""

import json

from analysis_wrapper.depmap import js_lane
from analysis_wrapper.targetspec import GitProvenance, RepoTarget
from analysis_wrapper.tooldefs import ToolDef

_UNSORTED = {
    "modules": [
        {"source": "src/b.ts", "dependencies": [
            {"module": "./a", "resolved": "src/a.ts", "couldNotResolve": False},
            {"module": "lodash", "resolved": "node_modules/lodash/index.js"}]},
        {"source": "src/a.ts", "dependencies": [
            {"module": "zod", "resolved": "node_modules/zod/index.js"},
            {"module": "./b", "resolved": "src/b.ts", "couldNotResolve": False}]},
    ],
    "summary": {"violations": []},
}


def test_sorted_map_orders_modules_and_dependencies():
    out = js_lane._sorted_map(_UNSORTED)
    sources = [m["source"] for m in out["modules"]]
    assert sources == ["src/a.ts", "src/b.ts"]
    for module in out["modules"]:
        resolved = [d.get("resolved", "") for d in module["dependencies"]]
        assert resolved == sorted(resolved)
    # every field the normalizer reads survives the projection+sort
    local = next(d for m in out["modules"] for d in m["dependencies"]
                 if d["module"] == "./a")
    assert local["couldNotResolve"] is False and local["resolved"] == "src/a.ts"
    # the summary block (a leak/determinism hazard) is dropped
    assert "summary" not in out


def test_sorted_map_is_deterministic():
    a = json.dumps(js_lane._sorted_map(_UNSORTED), sort_keys=True)
    b = json.dumps(js_lane._sorted_map(_UNSORTED), sort_keys=True)
    assert a == b


def _stub_tooldef(monkeypatch):
    fake = ToolDef(name="dependency-cruiser", binary="echo",
                   version_argv=["echo", "18.1.0"],
                   argv_builder=lambda _t: ["echo", "unused"])
    monkeypatch.setattr(js_lane.depcruise_lane, "dependency_cruiser", lambda _t: fake)


def test_analyze_captures_and_sorts_the_map(monkeypatch, tmp_path):
    _stub_tooldef(monkeypatch)
    repo = tmp_path / "web"
    repo.mkdir()
    (repo / "package.json").write_text("{}\n")
    target = RepoTarget(repo_id="web-1", path=str(repo), stacks=["js"],
                        git=GitProvenance(head="f" * 40))

    class _Proc:
        returncode = 0
        stdout = json.dumps(_UNSORTED)
        stderr = ""

    payload, cov = js_lane.analyze(target, tmp_path / "cfg",
                                   run=lambda *a, **k: _Proc())
    assert cov.status == "complete"
    assert cov.lane == "js"
    assert cov.map_file == "web-1.depcruise.json"
    assert [m["source"] for m in payload["modules"]] == ["src/a.ts", "src/b.ts"]


def test_analyze_fails_closed_on_invalid_json(monkeypatch, tmp_path):
    _stub_tooldef(monkeypatch)
    repo = tmp_path / "web"
    repo.mkdir()
    target = RepoTarget(repo_id="web-1", path=str(repo), stacks=["js"],
                        git=GitProvenance(head="f" * 40))

    class _Proc:
        returncode = 0
        stdout = "not json"
        stderr = ""

    payload, cov = js_lane.analyze(target, tmp_path / "cfg",
                                   run=lambda *a, **k: _Proc())
    assert payload is None
    assert cov.status == "failed"
