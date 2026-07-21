"""Developer-provided pinned Go call-graph tool: resolve and version."""

import subprocess

from analysis_wrapper import go_tools


def _make_binary(bin_dir):
    bin_dir.mkdir(parents=True, exist_ok=True)
    binary = bin_dir / "callgraph"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


def test_resolve_prefers_analyzer_owned(tmp_path):
    binary = _make_binary(tmp_path / "bin")
    resolved, note = go_tools.resolve(tmp_path / "bin")
    assert resolved == binary and note == ""


def test_resolve_absent_reports_install_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(go_tools.shutil, "which", lambda _n: None)
    resolved, note = go_tools.resolve(tmp_path / "empty")
    assert resolved is None
    assert go_tools.CALLGRAPH_VERSION in note and "README.md" in note


def test_resolve_path_fallback_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(go_tools.shutil, "which", lambda _n: "/usr/local/bin/callgraph")
    resolved, note = go_tools.resolve(tmp_path / "empty")
    assert str(resolved) == "/usr/local/bin/callgraph"
    assert "PATH" in note


def test_installed_version_parses_go_version_output():
    out = ("\tpath\tgolang.org/x/tools/cmd/callgraph\n"
           "\tmod\tgolang.org/x/tools\tv0.48.0\th1:abc=\n")

    def run(argv, **_k):
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    assert go_tools.installed_version("/x/callgraph", go="/usr/bin/go", run=run) == "v0.48.0"
