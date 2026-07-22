"""Dependency-cruiser lane: env binary, TS-support guard, argv, cycle view."""

import json
from pathlib import Path

from analysis_wrapper import node_env, parsers
from analysis_wrapper.depcruise_lane import dependency_cruiser
from analysis_wrapper.targetspec import RepoTarget, TechnologyFacet


def _language(target, profile_id, evidence):
    target.facets = [TechnologyFacet(profile_id, "language", ["."], [evidence])]


def test_binary_is_the_analyzer_env_never_global(target):
    td = dependency_cruiser(target)
    assert td.binary.replace("\\", "/").endswith("node_tools/node_modules/.bin/depcruise")


def test_ts_guard_passes_for_plain_js(target):
    _language(target, "language.javascript", "index.js")
    assert dependency_cruiser(target).check_guards(target) == ""


def test_ts_guard_unavailable_when_env_lacks_ts(monkeypatch, target):
    _language(target, "language.typescript", "tsconfig.json")
    monkeypatch.setattr(node_env, "probe", lambda *a, **k: node_env.NodeToolInfo(
        available=True, reason="", supports_ts=False, supports_tsx=False))
    reason = dependency_cruiser(target).check_guards(target)
    assert "unavailable" in reason


def test_ts_guard_unavailable_when_env_absent(monkeypatch, target):
    _language(target, "language.typescript", "tsconfig.json")
    monkeypatch.setattr(node_env, "probe", lambda *a, **k: node_env.NodeToolInfo(
        available=False, reason="analyzer node_tools env not installed"))
    assert "unavailable" in dependency_cruiser(target).check_guards(target)


def test_ts_guard_passes_when_env_supports_ts(monkeypatch, target):
    _language(target, "language.typescript", "tsconfig.json")
    monkeypatch.setattr(node_env, "probe", lambda *a, **k: node_env.NodeToolInfo(
        available=True, reason="", supports_ts=True, supports_tsx=True))
    assert dependency_cruiser(target).check_guards(target) == ""


def test_argv_fallback_uses_no_config_without_prepared_config(target, synthetic_repo):
    (synthetic_repo / "tsconfig.json").write_text("{}")
    _language(target, "language.typescript", "tsconfig.json")
    argv = dependency_cruiser(target).build_argv(target)
    assert "--no-config" in argv and "--ts-config" in argv
    assert argv[0].replace("\\", "/").endswith("node_modules/.bin/depcruise")


def test_resolution_note_reports_both_denominators():
    data = {"modules": [{"source": "src/a.ts", "dependencies": [
        {"module": "./b", "couldNotResolve": False},
        {"module": "antd/es/x", "couldNotResolve": True, "dependencyTypes": ["npm"]},
    ]}]}
    note = parsers.depcruise_resolution_note(json.dumps(data))
    assert "internal 1/1 (100.0%)" in note
    assert "total 1/2 (50.0%)" in note


def test_cycle_membership_lists_distinct_member_files():
    # Two entry points into the same 2-node loop → one distinct cycle.
    data = {"modules": [
        {"source": "src/a.ts", "dependencies": [
            {"module": "./b", "circular": True,
             "cycle": [{"name": "src/b.ts"}, {"name": "src/a.ts"}]}]},
        {"source": "src/b.ts", "dependencies": [
            {"module": "./a", "circular": True,
             "cycle": [{"name": "src/a.ts"}, {"name": "src/b.ts"}]}]},
    ]}
    cycles, distinct = parsers._depcruise_cycles(data)
    assert distinct == 1
    assert cycles[0] == ("src/a.ts", "src/b.ts")
