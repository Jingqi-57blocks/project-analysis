"""ast-grep runtime version probe (57B-37): probe once, record version + path,
disclose drift vs the validated version, fail closed to unavailable."""

import subprocess

import pytest

from analysis_wrapper import astgrep


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts from an empty per-run version cache."""
    astgrep._reset_probe_cache()
    yield
    astgrep._reset_probe_cache()


def _fake_run(version_line, *, returncode=0, counter=None):
    def run(argv, **kwargs):
        assert argv[1] == "--version"
        if counter is not None:
            counter.append(argv)
        return subprocess.CompletedProcess(
            args=argv, returncode=returncode, stdout=version_line, stderr="")
    return run


@pytest.mark.skipif(not astgrep.available(), reason="ast-grep not installed")
def test_probe_returns_real_version_and_path():
    p = astgrep.probe()
    assert p.available
    assert any(ch.isdigit() for ch in p.version)      # a real version string
    assert p.path and p.path.endswith(("ast-grep", "sg"))
    prov = p.provenance()
    assert prov["tool"] == "ast-grep"
    assert prov["tool_version"] == p.version and prov["tool_path"] == p.path


def test_probe_is_cached_once_per_run(monkeypatch):
    monkeypatch.setattr(astgrep, "binary", lambda: "/fake/bin/ast-grep")
    calls = []
    run = _fake_run("ast-grep 0.44.1\n", counter=calls)
    first = astgrep.probe(run=run)
    second = astgrep.probe(run=run)          # cache hit — run must NOT fire again
    assert first is second
    assert len(calls) == 1
    assert first.version == "ast-grep 0.44.1" and first.path == "/fake/bin/ast-grep"


def test_drift_disclosed_when_version_differs(monkeypatch):
    monkeypatch.setattr(astgrep, "binary", lambda: "/fake/bin/ast-grep")
    p = astgrep.probe(run=_fake_run("ast-grep 9.9.9\n"))
    assert p.drift == "validated 0.44.1, found ast-grep 9.9.9"
    assert p.provenance()["version_drift"] == p.drift


def test_no_drift_on_validated_version(monkeypatch):
    monkeypatch.setattr(astgrep, "binary", lambda: "/fake/bin/ast-grep")
    p = astgrep.probe(run=_fake_run("ast-grep 0.44.1\n"))
    assert p.drift == "" and p.provenance()["version_drift"] == ""


def test_unavailable_when_binary_absent(monkeypatch):
    monkeypatch.setattr(astgrep, "binary", lambda: None)
    p = astgrep.probe()
    assert not p.available and p.version is None
    prov = p.provenance()
    assert prov["tool_version"] == "(not installed)"
    assert prov["tool_path"] == "" and prov["version_drift"] == ""
    assert astgrep.unavailable_provenance() == prov


def test_unavailable_when_version_probe_fails(monkeypatch):
    monkeypatch.setattr(astgrep, "binary", lambda: "/fake/bin/ast-grep")

    def boom(argv, **kwargs):
        raise OSError("cannot spawn")

    p = astgrep.probe(run=boom)
    assert not p.available and p.provenance()["tool_version"] == "(not installed)"
