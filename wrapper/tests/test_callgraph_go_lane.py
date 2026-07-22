"""Go lane: 9-col TSV parsing, in-module slice, resolution mapping, fail-closed."""

import subprocess

from analysis_wrapper.callgraph import go_lane
from analysis_wrapper.targetspec import GitProvenance, RepoTarget

MODULE = "example.com/app"


def _tsv(root):
    """A tiny synthetic 9-col callgraph TSV rooted at ``root`` (no real Go run)."""
    rows = [
        # static internal -> internal (function call)  => observed static-call
        ["static", "static function call", f"{MODULE}/svc.Run", f"{MODULE}/svc",
         f"{root}/svc/run.go:10", f"{root}/svc/run.go:12:5",
         f"{MODULE}/db.Query", f"{MODULE}/db", f"{root}/db/db.go:5"],
        # dynamic internal -> internal (method call)    => inferred method-dispatch
        ["dynamic", "dynamic method call", f"{MODULE}/svc.Run", f"{MODULE}/svc",
         f"{root}/svc/run.go:10", f"{root}/svc/run.go:13:9",
         f"(*{MODULE}/db.Conn).Close", f"{MODULE}/db", f"{root}/db/db.go:20"],
        # static internal -> external (stdlib fmt)       => external site, no edge
        ["static", "static function call", f"{MODULE}/svc.Run", f"{MODULE}/svc",
         f"{root}/svc/run.go:10", f"{root}/svc/run.go:14:2",
         "fmt.Println", "fmt", "/usr/local/go/src/fmt/print.go:100"],
        # external caller -> internal                    => skipped (caller not in module)
        ["static", "static method call", "(*net/http.Server).Serve", "net/http",
         "/usr/local/go/src/net/http/server.go:1", "/usr/local/go/src/net/http/server.go:2:3",
         f"{MODULE}/svc.Run", f"{MODULE}/svc", f"{root}/svc/run.go:10"],
    ]
    return "\n".join("\t".join(r) for r in rows) + "\n"


def _prod(root, *rels):
    return {str(root / r) for r in rels}


def test_parse_tsv_slices_in_module_and_maps_resolution(tmp_path):
    root = tmp_path.resolve()
    edges, counts = go_lane.parse_tsv(
        _tsv(root), module=MODULE, repo_id="app", commit="c" * 40, repo_root=root,
        prod_files=_prod(root, "svc/run.go", "db/db.go"))

    assert len(edges) == 2
    static_edge = next(e for e in edges if e.resolution == "observed")
    dynamic_edge = next(e for e in edges if e.resolution == "inferred")

    assert static_edge.kind == "static-call"
    assert static_edge.caller_symbol == "svc.Run"       # module prefix stripped
    assert static_edge.callee_symbol == "db.Query"
    assert static_edge.caller_citation == "app@" + "c" * 40 + ":svc/run.go:10"
    assert static_edge.callsite_citation == "app@" + "c" * 40 + ":svc/run.go:12:5"

    assert dynamic_edge.kind == "method-dispatch"
    assert dynamic_edge.callee_symbol == "(*db.Conn).Close"

    assert counts.resolved == 2      # two in-module call sites
    assert counts.external == 1      # the fmt.Println site
    assert counts.ambiguous == 0 and counts.unresolved == 0


def test_parse_tsv_is_deterministic_regardless_of_input_order(tmp_path):
    root = tmp_path.resolve()
    prod = _prod(root, "svc/run.go", "db/db.go")
    lines = _tsv(root).strip().split("\n")
    forward, _ = go_lane.parse_tsv("\n".join(lines) + "\n", module=MODULE,
                                   repo_id="app", commit="c" * 40, repo_root=root,
                                   prod_files=prod)
    reversed_text = "\n".join(reversed(lines)) + "\n"
    backward, _ = go_lane.parse_tsv(reversed_text, module=MODULE,
                                    repo_id="app", commit="c" * 40, repo_root=root,
                                    prod_files=prod)
    assert [e.to_json_line() for e in forward] == [e.to_json_line() for e in backward]


def test_parse_tsv_drops_edges_citing_excluded_files(tmp_path):
    """Generated/mock in-module files are compiled by `callgraph ./...` but the
    production boundary must gate EMISSION: an edge whose callee lives in an
    excluded file is not emitted (counted external); a call site inside an
    excluded file is not counted at all."""
    root = tmp_path.resolve()
    rows = [
        # production -> production : emitted, resolved
        ["static", "static function call", f"{MODULE}/svc.Run", f"{MODULE}/svc",
         f"{root}/svc/run.go:10", f"{root}/svc/run.go:12:5",
         f"{MODULE}/svc.Help", f"{MODULE}/svc", f"{root}/svc/helper.go:3"],
        # production caller -> GENERATED callee (db.pb.go) : external site, no edge
        ["static", "static function call", f"{MODULE}/svc.Run", f"{MODULE}/svc",
         f"{root}/svc/run.go:10", f"{root}/svc/run.go:13:2",
         f"{MODULE}/db.Marshal", f"{MODULE}/db", f"{root}/db/db.pb.go:99"],
        # GENERATED caller (db.pb.go) -> production callee : call site not counted
        ["static", "static function call", f"{MODULE}/db.Init", f"{MODULE}/db",
         f"{root}/db/db.pb.go:5", f"{root}/db/db.pb.go:6:1",
         f"{MODULE}/svc.Help", f"{MODULE}/svc", f"{root}/svc/helper.go:3"],
    ]
    text = "\n".join("\t".join(r) for r in rows) + "\n"
    edges, counts = go_lane.parse_tsv(
        text, module=MODULE, repo_id="app", commit="c" * 40, repo_root=root,
        prod_files=_prod(root, "svc/run.go", "svc/helper.go"))

    assert len(edges) == 1
    assert edges[0].callee_symbol == "svc.Help"
    assert not any("db.pb.go" in e.callee_citation or "db.pb.go" in e.callsite_citation
                   for e in edges)
    assert counts.resolved == 1          # only the production->production site
    assert counts.external == 1          # the production->generated site



def test_module_path_parses_directive(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/app // comment\n\ngo 1.21\n")
    assert go_lane.module_path(tmp_path) == "example.com/app"


def test_module_path_absent(tmp_path):
    assert go_lane.module_path(tmp_path) is None


def test_analyze_unavailable_when_tool_absent(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
    target = RepoTarget(repo_id="app", path=str(tmp_path), stacks=["go"],
                        git=GitProvenance(head="d" * 40))

    def fail_run(*_a, **_k):  # the tool must never be invoked when it is absent
        raise AssertionError("callgraph must not run when the binary is unavailable")

    edges, cov = go_lane.analyze(
        target, repository_ref="app", bin_dir=tmp_path / "empty-gobin", run=fail_run)
    assert edges == []
    assert cov.status == "unavailable"
    assert "not installed" in cov.reason


def test_analyze_failed_on_nonzero_exit(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    (tmp_path / "main.go").write_text("package main\n")
    gobin = tmp_path / "gobin"
    gobin.mkdir()
    (gobin / "callgraph").write_text("#!/bin/sh\nexit 1\n")
    (gobin / "callgraph").chmod(0o755)
    target = RepoTarget(repo_id="app", path=str(tmp_path), stacks=["go"],
                        git=GitProvenance(head="d" * 40))

    def run(argv, **kwargs):
        # Serves both the `go version -m` probe and the callgraph invocation;
        # a nonzero exit with a cold-cache-shaped message must fail closed.
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="cannot find module providing package")

    edges, cov = go_lane.analyze(
        target, repository_ref="app", bin_dir=gobin, run=run)
    assert edges == []
    assert cov.status == "failed"
    assert "cold module cache" in cov.reason
