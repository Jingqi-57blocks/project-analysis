"""Required parser/tool-definition behavior classes from Linear 57B-10."""

import json

from analysis_wrapper import parsers
from analysis_wrapper.registry import dependency_cruiser, outdated, staticcheck
from analysis_wrapper.status import Status
from analysis_wrapper.targetspec import PackageManager


def test_yarn_empty_success_is_valid_but_empty_error_is_not():
    assert parsers.validate_yarn_outdated("", 0) == ""
    assert parsers.validate_yarn_outdated("", 1)


def test_yarn_error_object_is_invalid():
    text = json.dumps({"type": "error", "data": "registry failed"}) + "\n"
    assert "error object" in parsers.validate_yarn_outdated(text, 1)


def test_pm_fallback_is_partial_and_disclosed(target):
    target.pm = PackageManager("pnpm", "pnpm-lock.yaml", "fixture")
    td = outdated(target)
    assert td.binary.endswith("npm")
    assert "package-manager fallback" in td.check_degraded(target, "{}", 0)


def test_corepack_guards_propagate_to_yarn(target):
    target.pm = PackageManager("yarn", "yarn.lock", "fixture")
    td = outdated(target)
    assert td.env["COREPACK_ENABLE_AUTO_PIN"] == "0"
    assert td.env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] == "0"
    assert td.env["COREPACK_ENABLE_PROJECT_SPEC"] == "0"
    assert td.env["COREPACK_ENABLE_NETWORK"] == "0"
    assert td.env["COREPACK_DEFAULT_TO_LATEST"] == "0"


def test_node_and_package_manager_environment_is_scrubbed(monkeypatch, target):
    target.pm = PackageManager("yarn", "yarn.lock", "fixture")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/target/hook.js")
    monkeypatch.setenv("YARN_REGISTRY", "https://evil.invalid")
    monkeypatch.setenv("NPM_CONFIG_REGISTRY", "https://evil.invalid")
    env = outdated(target).merged_env()
    assert "NODE_OPTIONS" not in env
    assert env["YARN_REGISTRY"] == "https://registry.yarnpkg.com/"
    assert env["NPM_CONFIG_REGISTRY"] == "https://registry.yarnpkg.com/"


def test_staticcheck_compile_failure_is_partial(target):
    td = staticcheck(target)
    reason = td.check_degraded(target, "-: package failed (compile)", 1)
    assert "incomplete" in reason


def test_depcruise_unresolved_over_15_percent_is_partial(target):
    # internal (relative) edges — the coupling graph the gate measures
    data = {"modules": [{"source": "a", "dependencies": [
        {"module": "./b", "couldNotResolve": True},
        {"module": "./c", "couldNotResolve": False},
    ]}]}
    td = dependency_cruiser(target)
    assert ">15%" in td.check_degraded(target, json.dumps(data), 0)


def test_depcruise_partitions_prod_dev_and_unclassified_imports(target, synthetic_repo):
    (synthetic_repo / "package.json").write_text(json.dumps({
        "dependencies": {"prod-lib": "1"},
        "devDependencies": {"dev-lib": "1"},
    }))
    data = {"modules": [{"source": "a", "dependencies": [
        {"module": "prod-lib/sub", "dependencyTypes": ["npm"]},
        {"module": "dev-lib", "dependencyTypes": ["npm-dev"]},
        {"module": "unknown-lib", "dependencyTypes": ["npm-no-pkg"]},
    ]}]}
    view = parsers.depcruise_view(target, json.dumps(data), "")
    assert "external_imports_production:\nprod-lib/sub" in view
    assert "external_imports_dev_test:\ndev-lib" in view
    assert "external_imports_unclassified:\nunknown-lib" in view


def test_tool_exit_semantics_table(target):
    from analysis_wrapper.registry import scc, lizard, jscpd, dependency_cruiser, osv, outdated, go_list, git_history
    definitions = [scc(target), lizard(target), jscpd(target), dependency_cruiser(target),
                   osv(target), outdated(target), go_list(target), staticcheck(target),
                   git_history(target, "2024-01-01")]
    expected = {
        "scc": {0}, "lizard": {0, 1}, "jscpd": {0}, "dependency-cruiser": {0},
        "osv-scanner": {0, 1}, "outdated": {0, 1}, "go-list": {0},
        "staticcheck": {0, 1}, "git-history": {0},
    }
    assert {x.name: set(x.normal_exits) for x in definitions} == expected


def test_safe_flags_are_load_bearing(target):
    dep = dependency_cruiser(target)
    assert "--no-config" in dep.build_argv(target)
    go = staticcheck(target)
    assert go.env["GOFLAGS"] == "-mod=readonly"
    assert go.env["GOTOOLCHAIN"] == "local"
    assert go.env["GOWORK"] == "off"
    # OFFLINE-FIRST: the Go lane must contact zero network destinations.
    assert go.env["GOPROXY"] == "off" and go.env["GOSUMDB"] == "off"
    assert go.network is False
    assert "OFFLINE-FIRST" in go.extra_notes


def test_go_lane_is_offline_even_with_host_proxy_config(monkeypatch, target):
    monkeypatch.setenv("GOPROXY", "https://corp-proxy.invalid")
    monkeypatch.setenv("GOPRIVATE", "internal.invalid")
    monkeypatch.setenv("GOFLAGS", "-mod=mod")          # unsafe: would rewrite go.mod
    monkeypatch.setenv("GOTOOLCHAIN", "auto")          # unsafe: toolchain downloads
    env = staticcheck(target).merged_env()
    # Safety pins always win over host values — including the proxy: a host
    # proxy would be an undisclosed network destination for a network=False
    # signal, so the lane runs fully offline against the warm module cache.
    assert env["GOFLAGS"] == "-mod=readonly"
    assert env["GOTOOLCHAIN"] == "local"
    assert env["GOWORK"] == "off"
    assert env["GOPROXY"] == "off"
    assert env["GOSUMDB"] == "off"
    # Private-module settings are inert offline and pass through undisturbed.
    assert env["GOPRIVATE"] == "internal.invalid"


def test_osv_v2_uses_explicit_source_scan_and_lockfile(target):
    from analysis_wrapper.registry import osv
    target.pm = PackageManager("npm", "package-lock.json", "fixture")
    argv = osv(target).build_argv(target)
    assert argv[1:3] == ["scan", "source"]
    assert "--lockfile" in argv and "--format" in argv
    assert argv[argv.index("--data-source") + 1] == "native"
    assert "--no-resolve" in argv
    assert argv[argv.index("--config") + 1] == "/dev/null"


def test_npm_outdated_rejects_empty_exit_one_and_error_objects():
    assert parsers.validate_npm_outdated("", 0) == ""
    assert "exit 0" in parsers.validate_npm_outdated("", 1)
    assert "error" in parsers.validate_npm_outdated(
        json.dumps({"error": {"code": "E404", "summary": "missing"}}), 1
    )


def test_outdated_endpoint_policy_is_configuration_aware(target, synthetic_repo, monkeypatch):
    """Endpoint policy: benign project config proceeds with a note; anything
    that can alter endpoints/auth — or a dependency host outside the approved
    set — is a guard refusal (SKIPPED), never silently contacted. Forced
    --registry cannot neutralize project-level .npmrc or scoped registries."""
    monkeypatch.delenv("PROJECT_ANALYSIS_ALLOW_HOSTS", raising=False)
    target.pm = PackageManager("npm", "package-lock.json", "fixture")

    # Benign keys: signal survives, presence is disclosed.
    (synthetic_repo / ".npmrc").write_text("save-exact=true\nloglevel=warn\n")
    td = outdated(target)
    assert td.check_guards(target) == ""
    assert ".npmrc" in td.extra_notes and "benign" in td.extra_notes
    # The forced registry stays on the command line and is declared up front.
    assert any("registry.npmjs.org" in arg for arg in td.build_argv(target))
    assert "declared endpoint: registry.npmjs.org" in td.extra_notes

    # Endpoint-affecting key: refusal naming the offending key.
    (synthetic_repo / ".npmrc").write_text("registry=https://private.invalid\n")
    reason = outdated(target).check_guards(target)
    assert "endpoints/auth" in reason and ".npmrc:registry" in reason
    (synthetic_repo / ".npmrc").unlink()

    # Dependency host outside the approved registries: refusal naming the host.
    (synthetic_repo / "package.json").write_text(json.dumps({
        "dependencies": {"custom": "git+ssh://example.invalid/custom.git"},
    }))
    reason = outdated(target).check_guards(target)
    assert "example.invalid" in reason and "--allow-hosts" in reason

    # Explicit operator approval unblocks exactly that host, read from the
    # PROJECT_ANALYSIS_ALLOW_HOSTS environment variable.
    monkeypatch.setenv("PROJECT_ANALYSIS_ALLOW_HOSTS", "example.invalid")
    assert outdated(target).check_guards(target) == ""


def test_go_view_normalizes_random_stat_cache_suffix(tmp_path, target):
    (tmp_path / "go.mod").write_text("module example.invalid/x\n")
    target.path = str(tmp_path)
    stream = json.dumps({"ImportPath": "example.invalid/x", "Imports": []})
    first = parsers.go_list_view(target, stream, "open pkg/@v/v1.info12345.tmp: denied")
    second = parsers.go_list_view(target, stream, "open pkg/@v/v1.info98765.tmp: denied")
    assert first == second and ".info<TMP>.tmp" in first


def test_go_view_matches_internal_packages_on_module_boundaries(tmp_path, target):
    (tmp_path / "go.mod").write_text("module example.com/foo\n")
    target.path = str(tmp_path)
    stream = "\n".join(json.dumps(row) for row in [
        {"ImportPath": "example.com/foo", "Imports": ["example.com/foo/sub", "example.com/foo-tools"]},
        {"ImportPath": "example.com/foo/sub", "Imports": []},
        {"ImportPath": "example.com/foo-tools", "Imports": []},
    ])
    view = parsers.go_list_view(target, stream, "")
    assert "internal_packages: 2" in view
    assert "example.com/foo-tools" in view.split("external_imports:\n", 1)[1]


def test_jscpd_view_extracts_ranked_cross_file_clone_pairs(target):
    stdout = (
        "Clone found (javascript)\n"
        " - a/x.js [10:1 - 25:4] (15 lines, 90 tokens)\n"
        "   b/y.js [40:1 - 55:4]\n"
        "Clone found (javascript)\n"
        " - c/z.js [1:1 - 6:2] (5 lines, 30 tokens)\n"
        "   c/z.js [80:1 - 85:2]\n"
        "Found 2 clones.\n"
    )
    view = parsers.jscpd_view(target, stdout, "")
    # cross-file pair present with span + both endpoints; same-file pair excluded
    assert "15\ta/x.js:10-25\tb/y.js:40-55" in view
    assert "c/z.js:1-6\tc/z.js" not in view.split("cross-file", 1)[1].split("summary", 1)[0]
    assert "1 same-file" in view


def test_depcruise_partial_keys_on_internal_edges_not_external_subpaths(target):
    # 20 external npm subpaths unresolved (antd/es/*) + a healthy internal graph
    # (10 resolved, 1 unresolved = 9%): overall 21/31 = 68% but internal 9% —
    # the gate must key on internal only, and the view must expose that number.
    deps = [{"module": f"antd/es/c{i}", "couldNotResolve": True,
             "dependencyTypes": ["npm"]} for i in range(20)]
    deps += [{"module": f"./m{i}", "couldNotResolve": False} for i in range(10)]
    deps += [{"module": "./missing", "couldNotResolve": True}]
    data = {"modules": [{"source": "a", "dependencies": deps}]}
    text = json.dumps(data)
    td = dependency_cruiser(target)
    verdict = td.check_degraded(target, text, 0)
    assert verdict == "", f"external subpaths inflated the gate: {verdict!r}"
    view = parsers.depcruise_view(target, text, "")
    assert "internal_edges: 11" in view
    assert "internal_unresolved_edges: 1" in view


def test_depcruise_partial_still_fires_on_broken_internal_graph(target):
    deps = [{"module": f"./m{i}", "couldNotResolve": True} for i in range(9)]
    deps += [{"module": "./ok", "couldNotResolve": False}]
    data = {"modules": [{"source": "a", "dependencies": deps}]}
    verdict = dependency_cruiser(target).check_degraded(target, json.dumps(data), 0)
    assert "INTERNAL edges unresolved" in verdict and "9/10" in verdict
