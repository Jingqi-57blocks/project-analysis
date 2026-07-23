"""profiles.selection — the single facet-driven predicate module (57B-81 PR3)
that replaced the duplicated is-go/is-node copies in registry/cli/depcruise_lane.
"""

import inspect

from analysis_wrapper import cli, depcruise_lane
from analysis_wrapper.profiles import selection
from analysis_wrapper.registry import _language_args, local_tools, network_tools
from analysis_wrapper.targetspec import RepoTarget, TechnologyFacet, stable_repo_id


def _repo(tmp_path, name, facets):
    d = tmp_path / name
    d.mkdir()
    return RepoTarget(repo_id=stable_repo_id(str(d)), path=str(d), facets=facets)


def _facet(profile_id, kind="language"):
    return TechnologyFacet(profile_id, kind, ["."], ["fixture-evidence"])


# --- predicates against facet-built targets -------------------------------------

def test_go_only_target(tmp_path):
    target = _repo(tmp_path, "go-only", [_facet("language.go")])
    assert selection.is_go_target(target)
    assert not selection.is_node_target(target)
    assert not selection.is_ts_target(target)
    assert selection.family(target) == "go"
    assert selection.lizard_languages(target) == ["go"]


def test_js_only_target(tmp_path):
    target = _repo(tmp_path, "js-only", [_facet("language.javascript")])
    assert selection.is_node_target(target)
    assert not selection.is_go_target(target)
    assert not selection.is_ts_target(target)
    assert selection.family(target) == "node"
    assert selection.lizard_languages(target) == ["javascript"]


def test_ts_only_target(tmp_path):
    target = _repo(tmp_path, "ts-only", [_facet("language.typescript")])
    assert selection.is_ts_target(target)
    assert selection.is_node_target(target)
    assert selection.family(target) == "node"
    langs = selection.lizard_languages(target)
    assert "typescript" in langs and "tsx" in langs


def test_ecosystem_node_only_target_with_no_language_facet(tmp_path):
    # package.json manifest facet, no JS/TS language facet: still counts as
    # node — reproduces the old raw package.json-probe reach.
    target = _repo(tmp_path, "eco-node-only", [_facet("ecosystem.node", kind="ecosystem")])
    assert selection.is_node_target(target)
    assert selection.family(target) == "node"
    assert selection.lizard_languages(target) == []


def test_polyglot_go_and_js_groups_as_node(tmp_path):
    # node wins ties (matches the old elif order in cli._family_groups).
    target = _repo(tmp_path, "polyglot", [_facet("language.go"), _facet("language.javascript")])
    assert selection.is_go_target(target)
    assert selection.is_node_target(target)
    assert selection.family(target) == "node"


def test_zero_facets_selects_nothing(tmp_path):
    target = _repo(tmp_path, "bare", [])
    assert not selection.is_go_target(target)
    assert not selection.is_node_target(target)
    assert not selection.is_ts_target(target)
    assert selection.family(target) == "other"
    assert selection.lizard_languages(target) == []


# --- integration pins: the five re-pointed call sites ---------------------------

def test_go_faceted_target_local_tools_includes_staticcheck_and_go_list(tmp_path):
    target = _repo(tmp_path, "go-tools", [_facet("language.go")])
    names = {td.name for td in local_tools(target)}
    assert {"staticcheck", "go-list"} <= names


def test_node_faceted_target_local_tools_includes_dependency_cruiser(tmp_path):
    target = _repo(tmp_path, "node-tools", [_facet("language.javascript")])
    names = {td.name for td in local_tools(target)}
    assert "dependency-cruiser" in names


def test_go_faceted_lockfile_less_target_network_tools_includes_osv(tmp_path):
    target = _repo(tmp_path, "go-network", [_facet("language.go")])
    assert target.pm.lockfile == ""
    names = {td.name for td in network_tools(target)}
    assert "osv-scanner" in names


def test_ecosystem_node_only_target_network_tools_includes_outdated(tmp_path):
    target = _repo(tmp_path, "eco-node-network", [_facet("ecosystem.node", kind="ecosystem")])
    names = {td.name for td in network_tools(target)}
    assert "outdated" in names


# --- source-scan: no manifest probing left in selection logic -------------------

def test_no_manifest_probing_left_in_selection_call_sites():
    for fn in (local_tools, network_tools, _language_args, cli._family_groups,
               depcruise_lane._is_ts_target):
        src = inspect.getsource(fn)
        assert "is_file(" not in src, f"{fn.__qualname__}: manifest probe left in selection logic"
        assert ".stacks" not in src, f"{fn.__qualname__}: legacy .stacks read left in selection logic"
