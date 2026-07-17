"""depmap.emit — lane selection, deterministic imports/ output, CLI chaining."""

import json

from analysis_wrapper.depmap import emit
from analysis_wrapper.targetspec import GitProvenance, RepoTarget, TargetSpec

_MODULE = "example.com/app"
_STREAM = "\n".join(json.dumps(o) for o in [
    {"ImportPath": "fmt", "Standard": True, "Dir": "/usr/local/go/src/fmt"},
    {"ImportPath": _MODULE, "Dir": "/home/u/app",
     "Imports": ["fmt", "example.com/app/internal/store"]},
    {"ImportPath": "example.com/app/internal/store", "Dir": "/home/u/app/internal/store",
     "Imports": ["fmt"]},
])


def _go_spec(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "go.mod").write_text(f"module {_MODULE}\n")
    target = RepoTarget(repo_id="app-1", path=str(repo), stacks=["go"],
                        git=GitProvenance(head="e" * 40))
    return TargetSpec(repos=[target]), repo


def _fake_go_run(argv, **kwargs):
    class _Proc:
        returncode = 0
        stdout = _STREAM
        stderr = ""
    return _Proc()


def test_select_lanes_by_stack_and_manifest(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    assert emit.select_lanes(
        RepoTarget(repo_id="g", path=str(tmp_path), stacks=["go"])) == ["go"]
    js = tmp_path / "js"
    js.mkdir()
    (js / "package.json").write_text("{}\n")
    assert emit.select_lanes(RepoTarget(repo_id="j", path=str(js), stacks=[])) == ["js"]
    other = tmp_path / "other"
    other.mkdir()
    assert emit.select_lanes(
        RepoTarget(repo_id="o", path=str(other), stacks=["rust"])) == []


def test_run_depmap_writes_go_map_and_coverage(tmp_path):
    spec, _repo = _go_spec(tmp_path)
    out = tmp_path / "run"
    report = emit.run_depmap(spec, out, "2026-07-18", run=_fake_go_run)

    golist = out / "imports" / "app-1.golist.json"
    coverage = out / "imports" / "depmap-coverage.json"
    assert golist.is_file() and coverage.is_file()

    payload = json.loads(golist.read_text())
    assert payload["module"] == _MODULE
    assert "/home/u" not in golist.read_text()        # leak-free projection
    cov = json.loads(coverage.read_text())
    entry = next(r for r in cov["repos"] if r["repo_id"] == "app-1")
    assert entry["status"] == "complete" and entry["lane"] == "go"
    assert len(report.repos) == 1


def test_run_depmap_is_byte_for_byte_deterministic(tmp_path):
    spec, _repo = _go_spec(tmp_path)
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    emit.run_depmap(spec, out_a, "2026-07-18", run=_fake_go_run)
    emit.run_depmap(spec, out_b, "2026-07-18", run=_fake_go_run)
    assert (out_a / "imports" / "app-1.golist.json").read_bytes() == \
           (out_b / "imports" / "app-1.golist.json").read_bytes()
    assert (out_a / "imports" / "depmap-coverage.json").read_bytes() == \
           (out_b / "imports" / "depmap-coverage.json").read_bytes()


def test_run_depmap_skips_unsupported_repos(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    spec = TargetSpec(repos=[RepoTarget(repo_id="docs", path=str(docs), stacks=["md"])])
    out = tmp_path / "run"
    report = emit.run_depmap(spec, out, "2026-07-18", run=_fake_go_run)
    assert report.repos == []
    assert not (out / "imports" / "docs.golist.json").exists()
    assert (out / "imports" / "depmap-coverage.json").is_file()
