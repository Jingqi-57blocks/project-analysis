"""Analyzer-owned Node toolchain env: probe parsing, binary policy, setup argv."""

import subprocess
from pathlib import Path

import pytest

from analysis_wrapper import node_env

_INFO_TS_ON = """
    dependency-cruiser@18.1.0
    ✔ typescript             >=2.0.0 <7.0.0      typescript@5.9.3
    x babel                  >=7.0.0 <8.0.0      -
    ✔ extension
    ✔ .ts
    ✔ .tsx
    x .vue
"""

_INFO_TS_OFF = """
    dependency-cruiser@18.1.0
    x typescript             >=2.0.0 <7.0.0      -
    ✔ .js
    x .tsx
"""


def test_parse_info_typescript_enabled():
    info = node_env._parse_info(_INFO_TS_ON)
    assert info.available and info.supports_ts and info.supports_tsx
    assert info.depcruise_version == "18.1.0"
    assert info.typescript_version == "5.9.3"


def test_parse_info_typescript_disabled():
    info = node_env._parse_info(_INFO_TS_OFF)
    assert info.available and not info.supports_ts and not info.supports_tsx


def test_expected_binary_is_env_local_never_global(tmp_path):
    binary = node_env.expected_depcruise_binary(tmp_path)
    assert binary == tmp_path / "node_modules" / ".bin" / "depcruise"
    # Not installed here → depcruise_binary() is None (fail-closed, no global).
    assert node_env.depcruise_binary(tmp_path) is None


def test_probe_reports_unavailable_when_env_absent(tmp_path):
    info = node_env.probe(tmp_path, use_cache=False)
    assert not info.available and "not installed" in info.reason


def test_setup_requires_committed_lockfile(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    with pytest.raises(RuntimeError, match="lockfile"):
        node_env.setup(tmp_path, run=lambda *a, **k: subprocess.CompletedProcess(a, 0))


def test_setup_uses_frozen_lockfile_and_ignore_scripts(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    (tmp_path / "node_modules" / ".bin" / "depcruise").write_text("#!/bin/sh\n")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    node_env.setup(tmp_path, run=run, pnpm="/usr/bin/pnpm")
    assert calls and calls[0][0] == "/usr/bin/pnpm"
    assert "--frozen-lockfile" in calls[0] and "--ignore-scripts" in calls[0]
    assert "--dir" in calls[0] and str(tmp_path) in calls[0]
