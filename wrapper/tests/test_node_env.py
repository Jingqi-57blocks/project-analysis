"""Analyzer-owned Node package env: probe parsing and binary policy."""

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
