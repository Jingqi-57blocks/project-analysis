"""57B-82 A2: the git-history capability provider.

Two concerns:

1. ``GitHistoryProvider`` is the FIRST bundled provider to actually execute
   a signal tool via ``context.tool_access.execute(...)`` (every provider
   before it either calls an in-process analyzer directly, or — datastore/
   deploy-units — a producer with no executor seam at all). Its own manifest
   /view output must be byte-identical to the legacy sweep's direct
   ``registry.git_history(...)`` + ``run_tool(...)`` invocation, and a
   non-git target's verdict must mirror discovery's own "reduced coverage"
   disclosure exactly.

2. It is resume-safe: signal-tool artifacts are write-once (unlike every
   OTHER provider's idempotent ``replace_artifact_text``), so a SECOND call
   against an already-populated ``signals/`` directory must reuse the
   existing manifest rather than crash on ``run_tool``'s own collision
   refusal — exercised here against a REAL ``ExecutorToolAccess``, not the
   shared battery's minimal ``_StatusStub`` (which never writes a real file,
   so it can't prove this on its own).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from analysis_wrapper import identity
from analysis_wrapper.discovery import provenance
from analysis_wrapper.executor import run_tool
from analysis_wrapper.profiles.contracts import RunContext
from analysis_wrapper.profiles.providers import GitHistoryProvider
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.registry import git_history
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id
from provider_conformance import run_provider_conformance


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=A", "-c", "user.email=a@example.invalid",
         *args],
        check=True, capture_output=True, text=True,
    )


def _make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("hello\n", "utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return path


def _git_target(path: Path) -> RepoTarget:
    return RepoTarget(repo_id=stable_repo_id(str(path)), path=str(path),
                      git=provenance.git_provenance(path))


def _non_git_target(path: Path) -> RepoTarget:
    path.mkdir(parents=True, exist_ok=True)
    (path / "readme.txt").write_text("no git here\n", "utf-8")
    return RepoTarget(repo_id=stable_repo_id(str(path)), path=str(path))


def _context(spec: TargetSpec, identities, run_dir: Path, *,
            since: str = "2020-01-01", coupling_sample_cap: int = 0,
            network_authorized: bool = False) -> RunContext:
    access = ExecutorToolAccess(spec, identities, run_dir / "signals", "2026-07-24",
                                network_authorized=network_authorized)
    return RunContext(
        targets=spec, output_dir=run_dir, scan_date="2026-07-24",
        network_authorized=network_authorized,
        provenance={"preparation": {"history_since": since,
                                    "coupling_sample_cap": coupling_sample_cap}},
        tool_access=access, identities=identities,
    )


def test_non_git_target_is_not_applicable_with_discoverys_own_disclosure(tmp_path):
    target = _non_git_target(tmp_path / "lib-only")
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    context = _context(spec, identities, tmp_path / "run")

    result = GitHistoryProvider().run(context, target)

    assert result.coverage.applicability == "not-applicable"
    assert result.coverage.status == "complete"
    # Mirrors discovery/emit.py's own "non-git folder: ... reduced coverage"
    # disclosure verbatim (see that module's admit()/reduced.append() call).
    assert "non-git folder: reduced coverage" in result.coverage.detail
    assert "no history lane" in result.coverage.detail
    assert result.facts == ()
    assert result.artifact_refs == ()


def test_provider_matches_direct_sweep_invocation_byte_for_byte(tmp_path):
    """The core byte-identity requirement: the provider's manifest/view for a
    git target must be indistinguishable from what the legacy sweep's direct
    ``registry.git_history(...)`` + ``run_tool(...)`` call would have
    written, given the SAME run-bound since/coupling-sample-cap."""
    repo_path = _make_git_repo(tmp_path / "repo")
    target = _git_target(repo_path)
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    artifact_key = identities.artifact_key_for(target.repo_id)
    since = "2020-01-01"

    # A: legacy path.
    run_a = tmp_path / "run-a"
    signals_a = run_a / "signals"
    signals_a.mkdir(parents=True)
    tooldef = git_history(target, since, 0)
    direct = run_tool(tooldef, target, signals_a, "2026-07-24",
                      identities.repository(target.repo_id), allow_network=False)

    # B: provider path.
    run_b = tmp_path / "run-b"
    (run_b / "signals").mkdir(parents=True)
    context = _context(spec, identities, run_b, since=since)
    result = GitHistoryProvider().run(context, target)

    name = f"git-history-{artifact_key}"
    normalized_a = (signals_a / f"{name}.manifest.normalized.json").read_text("utf-8")
    normalized_b = (run_b / "signals" / f"{name}.manifest.normalized.json").read_text("utf-8")
    assert normalized_a == normalized_b
    view_a = (signals_a / f"{name}.view.txt").read_text("utf-8")
    view_b = (run_b / "signals" / f"{name}.view.txt").read_text("utf-8")
    assert view_a == view_b

    # Coverage reflects the SAME status/reason the direct call produced —
    # never re-derived independently.
    assert result.coverage.applicability == "applicable"
    assert result.coverage.status == direct.status.value
    assert result.coverage.detail == direct.reason
    assert result.artifact_refs[0].path == f"signals/{name}.manifest.json"
    assert any(ref.kind == "signal-view" for ref in result.artifact_refs) == \
        bool(direct.view_path)


def test_run_bound_since_and_coupling_cap_are_read_from_provenance(tmp_path):
    """The provider must use the RUN-BOUND values (as ``cli._prepare_overview``
    binds them into ``RunContext.provenance["preparation"]``), not
    ``registry.git_history``'s own defaults — proven by pinning a wildly
    different ``since`` and observing the tool's own recorded window."""
    repo_path = _make_git_repo(tmp_path / "repo")
    target = _git_target(repo_path)
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    artifact_key = identities.artifact_key_for(target.repo_id)
    run_dir = tmp_path / "run"
    (run_dir / "signals").mkdir(parents=True)
    context = _context(spec, identities, run_dir, since="1999-01-01",
                       coupling_sample_cap=3)

    GitHistoryProvider().run(context, target)

    manifest = json.loads(
        (run_dir / "signals" / f"git-history-{artifact_key}.manifest.json")
        .read_text("utf-8"))
    assert "1999-01-01" in manifest["argv"]
    assert "3" in manifest["argv"]


def test_provider_reuses_existing_manifest_on_a_resumed_pass_without_crashing(tmp_path):
    """Signal artifacts are write-once (run_tool's own collision refusal) —
    a SECOND call against the SAME real signals/ directory must not crash,
    and must produce IDENTICAL Coverage/artifact_refs (proven against a REAL
    ExecutorToolAccess, not the conformance battery's stub)."""
    repo_path = _make_git_repo(tmp_path / "repo")
    target = _git_target(repo_path)
    spec = TargetSpec([target])
    identities = identity.build(spec, workspace_root=tmp_path,
                                project_id=stable_repo_id(str(tmp_path)))
    run_dir = tmp_path / "run"
    (run_dir / "signals").mkdir(parents=True)
    context = _context(spec, identities, run_dir)

    first = GitHistoryProvider().run(context, target)
    second = GitHistoryProvider().run(context, target)  # must not raise

    assert first.coverage == second.coverage
    assert first.artifact_refs == second.artifact_refs


def test_provider_conforms_via_zero_profile_battery_shape(tmp_path):
    """The battery's zero-profile ``RepoTarget`` never carries ``git=``
    (only ``repo_id``/``path`` — see ``run_provider_conformance``'s own
    docstring), so ``repo_setup`` writing real git content to disk would not
    change anything: this provider gates on ``target.git.is_git``, a
    RepoTarget FIELD, not on filesystem content the way deploy-units'
    ``discovery.deploy_units.generate`` does. This run therefore always
    exercises the not-applicable branch — still a real, valuable proof of
    the GENERIC battery guarantees (determinism, no repo_id leak, tool-access
    boundary, universal bare-repo selection); the real-execution branch is
    proven by the dedicated tests above instead."""
    run_provider_conformance(None, GitHistoryProvider(), tmp_path=tmp_path)


def test_bundled_git_history_provider_is_registered_zero_profile_universal():
    from analysis_wrapper.profiles.bundled import bundled_registry
    registry = bundled_registry()
    provider = registry.provider("git-history")
    assert isinstance(provider, GitHistoryProvider)
    assert provider.profile_ids == ()
    assert getattr(provider, "universal", False) is True
    assert provider.capability_id == "git-history"
