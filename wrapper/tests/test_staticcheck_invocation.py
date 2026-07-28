"""57B-151 staticcheck module-aware invocation and reuse contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_wrapper import identity
from analysis_wrapper.cli import _validate_reused_staticcheck_invocations
from analysis_wrapper.executor import run_tool
from analysis_wrapper.go_staticcheck import invocation
from analysis_wrapper.parsers import staticcheck_degraded, staticcheck_view
from analysis_wrapper.registry import staticcheck
from analysis_wrapper.status import Status
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id
from analysis_wrapper.tooldefs import InvocationPlan, ToolDef


def _target(path: Path, *, roots: list[str] | None = None) -> RepoTarget:
    path.mkdir(parents=True, exist_ok=True)
    return RepoTarget(repo_id=stable_repo_id(str(path)), path=str(path.resolve()),
                      analysis_roots=roots or [])


def test_staticcheck_root_module_plan_is_independent_of_wrapper_cwd(tmp_path, monkeypatch):
    repo = tmp_path / "root-module"
    target = _target(repo)
    (repo / "go.mod").write_text("module example.invalid/root\n", "utf-8")
    monkeypatch.chdir(tmp_path)  # deliberately not the target repository

    plan = invocation(target, "staticcheck")

    assert plan.cwd == repo.resolve()
    assert plan.argv == ["staticcheck", "./..."]
    assert plan.manifest_cwd == "repo"
    assert plan.identity["module_root"] == "."
    assert plan.identity["package_patterns"] == ["./..."]
    # A parent workspace cannot silently widen or redirect the package set.
    assert staticcheck(target).env["GOWORK"] == "off"


def test_staticcheck_nested_analysis_root_uses_its_own_module(tmp_path):
    repo = tmp_path / "nested-module"
    module = repo / "components" / "unit"
    target = _target(repo, roots=["components/unit"])
    module.mkdir(parents=True)
    (module / "go.mod").write_text("module example.invalid/unit\n", "utf-8")

    plan = invocation(target, "staticcheck")

    assert plan.cwd == module.resolve()
    assert plan.argv == ["staticcheck", "./..."]
    assert plan.manifest_cwd == "module:components/unit"
    assert plan.identity["analysis_roots"] == ["components/unit"]
    assert plan.reads == ["components/unit/go.mod"]


def test_staticcheck_root_module_limits_pattern_to_analysis_root(tmp_path):
    repo = tmp_path / "root-with-scope"
    scoped = repo / "internal" / "service"
    target = _target(repo, roots=["internal/service"])
    scoped.mkdir(parents=True)
    (repo / "go.mod").write_text("module example.invalid/scoped\n", "utf-8")

    plan = invocation(target, "staticcheck")

    assert plan.cwd == repo.resolve()
    assert plan.argv == ["staticcheck", "./internal/service/..."]
    assert plan.identity["package_patterns"] == ["./internal/service/..."]


def test_staticcheck_no_module_and_multiple_modules_are_explicit_reduced_coverage(tmp_path):
    no_module = _target(tmp_path / "no-module")
    no_module_plan = invocation(no_module, "staticcheck")
    assert no_module_plan.reason.startswith("staticcheck-no-go-module-detected")
    assert no_module_plan.identity["layout"] == "no-module-detected"

    repo = tmp_path / "multiple-modules"
    target = _target(repo)
    for name in ("one", "two"):
        root = repo / name
        root.mkdir(parents=True)
        (root / "go.mod").write_text("module example.invalid/generic\n", "utf-8")
    multiple_plan = invocation(target, "staticcheck")
    assert multiple_plan.reason.startswith("staticcheck-unsupported-multiple-modules")
    assert multiple_plan.identity["layout"] == "multiple-modules"


def _identities(target: RepoTarget):
    repo = Path(target.path)
    return identity.build(TargetSpec([target]), workspace_root=repo.parent,
                          project_id=stable_repo_id(str(repo.parent)))


def test_executor_uses_planned_nested_cwd_and_records_logical_identity(tmp_path, monkeypatch):
    repo = tmp_path / "executor-module"
    nested = repo / "nested"
    target = _target(repo, roots=["nested"])
    nested.mkdir(parents=True)
    (nested / "go.mod").write_text("module example.invalid/exec\n", "utf-8")
    monkeypatch.chdir(tmp_path)  # wrapper launch cwd is deliberately unrelated
    plan = invocation(target, "bash")
    tool = ToolDef(
        name="staticcheck", binary="bash", version_argv=["bash", "--version"],
        argv_builder=lambda _target: ["bash", "-c", "false"],
        invocation_builder=lambda _target, _out: InvocationPlan(
            argv=["bash", "-c", "test -f go.mod"], cwd=plan.cwd,
            manifest_cwd=plan.manifest_cwd, identity=plan.identity, reads=plan.reads),
    )

    result = run_tool(tool, target, tmp_path / "signals", "2026-07-28",
                      _identities(target).repository(target.repo_id))

    assert result.status is Status.COMPLETE
    assert result.manifest.cwd == "module:nested"
    assert result.manifest.invocation == plan.identity
    assert "nested/go.mod" in result.manifest.declared_reads


def test_staticcheck_plans_are_repository_local_in_a_multi_repo_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    api = workspace / "api"
    worker = workspace / "worker"
    api_target = _target(api)
    worker_target = _target(worker, roots=["cmd/job"])
    (api / "go.mod").write_text("module example.invalid/api\n", "utf-8")
    (worker / "cmd" / "job").mkdir(parents=True)
    (worker / "cmd" / "job" / "go.mod").write_text(
        "module example.invalid/worker-job\n", "utf-8")

    api_plan = invocation(api_target, "staticcheck")
    worker_plan = invocation(worker_target, "staticcheck")

    assert api_plan.cwd == api.resolve()
    assert api_plan.argv == ["staticcheck", "./..."]
    assert worker_plan.cwd == (worker / "cmd" / "job").resolve()
    assert worker_plan.argv == ["staticcheck", "./..."]
    assert api_plan.identity != worker_plan.identity


def test_staticcheck_no_package_result_is_partial_with_precise_lens_limitation(tmp_path):
    repo = tmp_path / "empty-package"
    target = _target(repo)
    (repo / "go.mod").write_text("module example.invalid/empty\n", "utf-8")
    plan = invocation(target, "bash")
    tool = ToolDef(
        name="staticcheck", binary="bash", version_argv=["bash", "--version"],
        argv_builder=lambda _target: ["bash", "-c", "false"],
        normal_exits=frozenset({0, 1}), degraders=[staticcheck_degraded],
        view_builder=staticcheck_view,
        invocation_builder=lambda _target, _out: InvocationPlan(
            argv=["bash", "-c", "echo './...' matched no packages >&2; exit 1"],
            cwd=plan.cwd, manifest_cwd=plan.manifest_cwd, identity=plan.identity,
            reads=plan.reads),
    )

    result = run_tool(tool, target, tmp_path / "signals", "2026-07-28",
                      _identities(target).repository(target.repo_id))

    assert result.status is Status.PARTIAL
    assert result.reason.startswith("staticcheck-no-package-universe")
    assert "coverage_limitation: staticcheck-no-package-universe" in result.view_path.read_text("utf-8")


def test_reused_staticcheck_requires_exact_invocation_and_tool_version(tmp_path, monkeypatch):
    repo = tmp_path / "reuse-module"
    target = _target(repo)
    (repo / "go.mod").write_text("module example.invalid/reuse\n", "utf-8")
    run = tmp_path / "run"
    signals = run / "signals"
    signals.mkdir(parents=True)
    spec = TargetSpec([target])
    spec.save(run / "targets.json")
    identities = _identities(target)
    identity.write_mapping(run, identities)

    plan = invocation(target, "bash")
    definition = ToolDef(
        name="staticcheck", binary="bash", version_argv=["bash", "--version"],
        argv_builder=lambda _target: ["bash", "-c", "true"],
        invocation_builder=lambda _target, _out: InvocationPlan(
            argv=["bash", "-c", "true"], cwd=plan.cwd,
            manifest_cwd=plan.manifest_cwd, identity=plan.identity, reads=plan.reads),
    )
    version = definition.probe_version(definition.resolved_binary())
    artifact_key = identities.artifact_key_for(target.repo_id)
    manifest = {
        "status": "complete", "invocation": plan.identity,
        "argv": ["bash", "-c", "true"], "cwd": plan.manifest_cwd,
        "env": {}, "tool_version": version,
    }
    path = signals / f"staticcheck-{artifact_key}.manifest.json"
    path.write_text(json.dumps(manifest), "utf-8")
    monkeypatch.setattr("analysis_wrapper.registry.staticcheck", lambda _target: definition)

    _validate_reused_staticcheck_invocations(signals, spec, identities)
    manifest["invocation"] = {"schema": "wrong"}
    path.write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="invocation identity"):
        _validate_reused_staticcheck_invocations(signals, spec, identities)
