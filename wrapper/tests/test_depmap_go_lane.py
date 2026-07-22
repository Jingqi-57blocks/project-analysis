"""depmap.go_lane — go list stream projection: leak-free, deterministic, correct."""

import json

from analysis_wrapper.depmap import go_lane
from analysis_wrapper.targetspec import GitProvenance, RepoTarget

_MODULE = "example.com/app"

# A concatenated-JSON `go list -deps -json` stream (objects, NOT an array), with
# the absolute-path fields go list really emits so the leak check is meaningful.
_STREAM = "\n".join(json.dumps(o) for o in [
    {"ImportPath": "fmt", "Standard": True, "Dir": "/usr/local/go/src/fmt",
     "Imports": ["errors", "io"]},
    {"ImportPath": "errors", "Standard": True, "Dir": "/usr/local/go/src/errors"},
    {"ImportPath": "io", "Standard": True, "Dir": "/usr/local/go/src/io"},
    {"ImportPath": "github.com/lib/pq", "Dir": "/home/u/go/pkg/mod/pq",
     "Imports": ["fmt"]},
    {"ImportPath": _MODULE, "Dir": "/home/u/app", "Root": "/home/u/app",
     "Imports": ["fmt", "example.com/app/internal/store", "github.com/lib/pq"]},
    {"ImportPath": "example.com/app/internal/store", "Dir": "/home/u/app/internal/store",
     "Imports": ["fmt", "errors", "example.com/app/internal/util"]},
    {"ImportPath": "example.com/app/internal/util", "Dir": "/home/u/app/internal/util",
     "Imports": ["fmt"]},
])


def test_projection_keeps_internal_graph_and_stdlib_set():
    payload = go_lane.project(_STREAM, _MODULE)
    assert payload["module"] == _MODULE
    pkgs = {p["import_path"]: p["imports"] for p in payload["packages"]}
    # Only the module's own packages are kept (pq / stdlib are not internal nodes).
    assert set(pkgs) == {_MODULE, "example.com/app/internal/store",
                         "example.com/app/internal/util"}
    assert "example.com/app/internal/store" in pkgs[_MODULE]
    # stdlib set carries only stdlib packages internal packages actually import.
    assert set(payload["stdlib"]) == {"errors", "fmt"}


def test_projection_is_leak_free_and_deterministic():
    a = json.dumps(go_lane.project(_STREAM, _MODULE), sort_keys=True)
    b = json.dumps(go_lane.project(_STREAM, _MODULE), sort_keys=True)
    assert a == b
    # No absolute machine path (Dir/Root) survives into the projection.
    assert "/home/u" not in a and "/usr/local/go" not in a


def test_projection_sorts_packages_and_imports():
    payload = go_lane.project(_STREAM, _MODULE)
    paths = [p["import_path"] for p in payload["packages"]]
    assert paths == sorted(paths)
    for pkg in payload["packages"]:
        assert pkg["imports"] == sorted(pkg["imports"])


def test_parse_stream_handles_concatenated_objects():
    objs = go_lane.parse_stream(_STREAM)
    assert len(objs) == 7
    assert objs[0]["ImportPath"] == "fmt"


def _target(tmp_path, *, go_mod: bool = True) -> RepoTarget:
    if go_mod:
        (tmp_path / "go.mod").write_text(f"module {_MODULE}\n")
    return RepoTarget(repo_id="app-1", path=str(tmp_path), stacks=["go"],
                      git=GitProvenance(head="c" * 40))


def test_analyze_fails_closed_without_module_directive(tmp_path):
    (tmp_path / "go.mod").write_text("// no module directive\n")
    target = RepoTarget(repo_id="app-1", path=str(tmp_path), stacks=["go"],
                        git=GitProvenance(head="c" * 40))
    payload, cov = go_lane.analyze(
        target, repository_ref="app", artifact_key="app",
        go_binary="/usr/bin/go",
                                   run=lambda *a, **k: None)  # never reached
    assert payload is None
    assert cov.status == "failed"
    assert "module" in cov.reason


def test_analyze_projects_from_a_fake_go_list(tmp_path):
    class _Proc:
        returncode = 0
        stdout = _STREAM
        stderr = ""

    def fake_run(argv, **kwargs):
        assert argv[1:4] == ["list", "-deps", "-json"]
        return _Proc()

    payload, cov = go_lane.analyze(
        _target(tmp_path), repository_ref="app", artifact_key="app",
        go_binary="/usr/bin/go",
                                   run=fake_run)
    assert cov.status == "complete"
    assert cov.lane == "go"
    assert cov.map_file == "app.golist.json"
    assert cov.units == 3
    assert cov.reference_counts == {
        "internal": 2, "third_party": 1, "stdlib": 4, "total": 7}
    assert payload["module"] == _MODULE


def test_analyze_fails_closed_on_nonzero_exit(tmp_path):
    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "go: cannot find module providing package foo"

    payload, cov = go_lane.analyze(
        _target(tmp_path), repository_ref="app", artifact_key="app",
        go_binary="/usr/bin/go",
                                   run=lambda *a, **k: _Proc())
    assert payload is None
    assert cov.status == "failed"
    assert "cold module cache" in cov.reason
