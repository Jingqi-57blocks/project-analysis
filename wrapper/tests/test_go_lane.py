"""Go lane hardening: build-setting env recording, load-failure degrade, warm."""

import subprocess

from analysis_wrapper import go_cache, parsers
from analysis_wrapper.registry import go_list


def test_go_env_records_build_settings_and_offline_pins(target):
    td = go_list(target)
    assert td.env["GOFLAGS"] == "-mod=readonly"
    assert td.env["GOPROXY"] == "off" and td.env["GOSUMDB"] == "off"
    # Host go is present in this environment, so the build universe is recorded.
    assert "GOOS" in td.env and "GOARCH" in td.env and "CGO_ENABLED" in td.env
    assert "build settings" in td.extra_notes


def test_go_list_degraded_flags_package_load_error():
    stream = '{"ImportPath":"x","Error":{"Err":"cannot find package"}}\n### STDERR ###\n'
    assert "partial" in parsers.go_list_degraded(None, stream, 0)


def test_go_list_degraded_clean_on_healthy_run():
    stream = ('{"ImportPath":"x","Imports":["y"]}\n### STDERR ###\n'
              'warning: stat cache mismatch')
    assert parsers.go_list_degraded(None, stream, 0) == ""


def test_go_cache_warm_requires_go_module(tmp_path):
    ok, detail = go_cache.warm(tmp_path)
    assert not ok and "go.mod" in detail


def test_go_cache_warm_allows_network_stays_readonly(tmp_path):
    (tmp_path / "go.mod").write_text("module widget\n")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    ok, _detail = go_cache.warm(tmp_path, go_binary="/usr/bin/go", run=run)
    assert ok
    argv, kwargs = calls[0]
    assert argv == ["/usr/bin/go", "list", "-deps", "-json", "./..."]
    # The warm step is the ONLY Go step permitted network: GOPROXY is NOT off,
    # but it stays read-only so the target is never mutated.
    assert kwargs["env"].get("GOPROXY") != "off"
    assert kwargs["env"]["GOFLAGS"] == "-mod=readonly"
