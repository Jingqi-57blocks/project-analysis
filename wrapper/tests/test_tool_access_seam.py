"""57B-82 A2: the ``ToolAccess.execute(..., tooldef=...)`` seam extension.

``tooldef`` lets a provider supply an already-constructed, explicitly
reviewed ``ToolDef`` instead of the run's default ``registry.tool_for``
resolution — the ONLY reason it exists is ``git-history``'s run-bound
``since``/``coupling_sample_cap`` (see ``GitHistoryProvider``). This module
tests the seam mechanics in isolation: the mismatch guard, that a given
``tooldef`` is honestly what executes, and that ``RecordingToolAccess``
collects the real ``SignalResult``s a provider's tool_access calls produce
(the plumbing ``cli._prepare_overview`` needs to fold them into
run-summary.json) without disturbing its existing log/isinstance behavior.
"""

from __future__ import annotations

import dataclasses

import pytest

from analysis_wrapper import identity
from analysis_wrapper.executor import SignalResult
from analysis_wrapper.profiles.contracts import CapabilityResult, Coverage, RunContext
from analysis_wrapper.profiles.execution import RecordingToolAccess, run_providers
from analysis_wrapper.profiles.registry import ProfileRegistry
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.registry import git_history, osv
from analysis_wrapper.targetspec import GitProvenance, RepoTarget, TargetSpec, stable_repo_id


def _target(tmp_path) -> RepoTarget:
    # ``git=`` set (fake but syntactically valid head) so `tool_for`'s
    # default resolution includes git-history in its candidate dict at all
    # (``registry.local_tools`` only appends it when ``target.git.is_git``);
    # these tests are about the SEAM (which tooldef executes, what gets
    # recorded), not about a genuine git repository's own content, so a
    # STALENESS-mismatch FAILED outcome downstream is fine — argv is still
    # captured before that check runs.
    path = tmp_path / "svc"
    path.mkdir(parents=True, exist_ok=True)
    return RepoTarget(repo_id=stable_repo_id(str(path)), path=str(path),
                      git=GitProvenance(head="a" * 40))


def _identities(tmp_path, target):
    return identity.build(TargetSpec([target]), workspace_root=tmp_path,
                          project_id=stable_repo_id(str(tmp_path)))


def test_mismatched_tooldef_is_refused(tmp_path):
    target = _target(tmp_path)
    identities = _identities(tmp_path, target)
    spec = TargetSpec([target])
    access = ExecutorToolAccess(spec, identities, tmp_path / "out", "2026-07-24")

    with pytest.raises(ValueError, match="does not match"):
        access.execute("git-history", target, tooldef=osv(target))


def test_given_tooldef_is_what_actually_executes(tmp_path):
    """Two DIFFERENT ``since`` windows on the SAME target must produce two
    DIFFERENT recorded argvs — proving the passed ``tooldef``, not
    ``tool_for``'s default resolution, is what ran."""
    target = _target(tmp_path)
    identities = _identities(tmp_path, target)
    spec = TargetSpec([target])
    access = ExecutorToolAccess(spec, identities, tmp_path / "out", "2026-07-24")

    result_a = access.execute("git-history", target,
                              tooldef=git_history(target, "2001-01-01", 0))
    assert "2001-01-01" in result_a.manifest.argv

    (tmp_path / "out2").mkdir()
    access2 = ExecutorToolAccess(spec, identities, tmp_path / "out2", "2026-07-24")
    result_b = access2.execute("git-history", target,
                              tooldef=git_history(target, "2015-06-01", 0))
    assert "2015-06-01" in result_b.manifest.argv
    assert result_a.manifest.argv != result_b.manifest.argv


def test_omitted_tooldef_is_byte_identical_to_default_resolution(tmp_path):
    """A caller that never passes ``tooldef`` sees the SAME behavior as
    before this parameter existed — proven by an explicit run-bound
    ``git_history(target, since=None, 0)`` call (registry's own default
    ``since``) matching what plain ``tool_for("git-history", target)``
    resolves to."""
    target = _target(tmp_path)
    identities = _identities(tmp_path, target)
    spec = TargetSpec([target])

    access_default = ExecutorToolAccess(spec, identities, tmp_path / "a", "2026-07-24")
    default_result = access_default.execute("git-history", target)

    access_explicit = ExecutorToolAccess(spec, identities, tmp_path / "b", "2026-07-24")
    explicit_result = access_explicit.execute(
        "git-history", target, tooldef=git_history(target, None, 0))

    assert default_result.manifest.argv == explicit_result.manifest.argv


def _coverage(**overrides):
    fields = {"applicability": "applicable", "status": "complete",
              "reason_code": "ok", "detail": ""}
    fields.update(overrides)
    return Coverage(**fields)


@dataclasses.dataclass(frozen=True)
class _ToolCallingProvider:
    """A synthetic provider that calls tool_access.execute for real, used to
    prove run_providers' signal_results collector without needing a real
    bundled provider."""

    provider_id: str = "tool-calling-provider"
    capability_id: str = "cap"
    profile_ids: tuple = ()
    universal: bool = True
    behavior: str = "ok"

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        result = context.tool_access.execute("git-history", target)
        if self.behavior == "raise":
            raise RuntimeError("intentional failure after a real tool call")
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=_coverage(status=result.status.value, reason_code="ok"),
        )


def test_recording_tool_access_collects_real_signal_results(tmp_path):
    target = _target(tmp_path)
    identities = _identities(tmp_path, target)
    access = ExecutorToolAccess(TargetSpec([target]), identities, tmp_path / "out",
                                "2026-07-24")
    recorder = RecordingToolAccess(inner=access)

    result = recorder.execute("git-history", target)

    assert recorder.signals == [result]
    assert isinstance(recorder.signals[0], SignalResult)
    assert recorder.log == [{"tool_id": "git-history", "signal_id": "",
                             "status": result.status.value}]


def test_run_providers_signal_results_collector_is_additive_and_optional(tmp_path):
    """``signal_results`` is a pure side channel: omitting it changes
    nothing (return signature unchanged), and passing it collects a REAL
    provider's tool_access executions in order — including from a provider
    that later RAISES (the tool already ran; its manifest is real evidence
    regardless of the provider's own outcome)."""
    target = _target(tmp_path)
    identities = _identities(tmp_path, target)
    spec = TargetSpec([target])
    access = ExecutorToolAccess(spec, identities, tmp_path / "out", "2026-07-24")
    context = RunContext(
        targets=spec, output_dir=tmp_path, scan_date="2026-07-24",
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )
    registry = ProfileRegistry((), (_ToolCallingProvider(),))

    # Omitted: unchanged 2-tuple return, no crash.
    results, rows = run_providers(registry, context)
    assert len(results) == 1
    assert rows[0]["outcome"] == "completed"

    # Given: collects the real SignalResult produced.
    (tmp_path / "out2").mkdir()
    access2 = ExecutorToolAccess(spec, identities, tmp_path / "out2", "2026-07-24")
    context2 = dataclasses.replace(context, tool_access=access2)
    collected: list[SignalResult] = []
    run_providers(registry, context2, signal_results=collected)
    assert len(collected) == 1
    assert isinstance(collected[0], SignalResult)
    assert collected[0].tool == "git-history"


def test_run_providers_signal_results_collector_captures_failed_provider_too(tmp_path):
    target = _target(tmp_path)
    identities = _identities(tmp_path, target)
    spec = TargetSpec([target])
    access = ExecutorToolAccess(spec, identities, tmp_path / "out", "2026-07-24")
    context = RunContext(
        targets=spec, output_dir=tmp_path, scan_date="2026-07-24",
        network_authorized=False, provenance={}, tool_access=access,
        identities=identities,
    )
    registry = ProfileRegistry((), (_ToolCallingProvider(behavior="raise"),))

    collected: list[SignalResult] = []
    results, rows = run_providers(registry, context, signal_results=collected)

    assert not results
    assert rows[0]["outcome"] == "failed"
    assert len(collected) == 1  # the tool call happened before the raise
