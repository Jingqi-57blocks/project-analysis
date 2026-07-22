from dataclasses import dataclass
from pathlib import Path

import pytest

from analysis_wrapper import identity
from analysis_wrapper.profiles import (
    ArtifactRef,
    CapabilityResult,
    ExecutorToolAccess,
    Fingerprint,
    Profile,
    ProfileRegistry,
    RunContext,
    run_provider,
)
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.targetspec import TargetSpec, stable_repo_id


@dataclass(frozen=True)
class SyntheticProvider:
    provider_id: str = "synthetic-provider"
    capability_id: str = "synthetic-capability"
    profile_ids: tuple[str, ...] = ("synthetic-profile",)

    def run(self, context, target):
        return CapabilityResult(
            capability_id=self.capability_id,
            provider_id=self.provider_id,
            repo_id=target.repo_id,
            facts=({"observed": True},),
            coverage={"status": "complete"},
            artifact_refs=(ArtifactRef("evidence/synthetic.json", "evidence"),),
        )


def _profile(profile_id="synthetic-profile", capability="synthetic-capability"):
    return Profile(
        profile_id=profile_id,
        kind="language",
        display_name=profile_id,
        fingerprints=(Fingerprint("manifest-file", "synthetic.marker"),),
        capability_ids=(capability,),
    )


def test_registry_is_explicit_deterministic_and_has_no_mutation_api():
    second = _profile("z-profile", "z-capability")

    @dataclass(frozen=True)
    class SecondProvider:
        provider_id: str = "z-provider"
        capability_id: str = "z-capability"
        profile_ids: tuple[str, ...] = ("z-profile",)

        def run(self, context, target):  # pragma: no cover - registry-only fixture
            raise AssertionError

    registry = ProfileRegistry((second, _profile()), (SecondProvider(), SyntheticProvider()))
    assert [item.profile_id for item in registry.profiles] == [
        "synthetic-profile", "z-profile"]
    assert [item.provider_id for item in registry.providers] == [
        "synthetic-provider", "z-provider"]
    assert not hasattr(registry, "register")
    assert bundled_registry().profiles and bundled_registry().providers == ()


def test_registry_rejects_duplicates_unknowns_and_untrusted_shapes():
    with pytest.raises(ValueError, match="duplicate profile_id"):
        ProfileRegistry((_profile(), _profile()), (SyntheticProvider(),))
    with pytest.raises(ValueError, match="duplicate provider_id"):
        ProfileRegistry((_profile(),), (SyntheticProvider(), SyntheticProvider()))

    bad = SyntheticProvider(profile_ids=("missing",))
    with pytest.raises(ValueError, match="unknown profiles"):
        ProfileRegistry((_profile(),), (bad,))
    with pytest.raises(ValueError, match="explicit Profile"):
        ProfileRegistry(({"profile_id": "from-target"},), ())
    with pytest.raises(ValueError, match="Fingerprint"):
        Profile("p", "language", "p", (lambda: None,), ("c",))
    with pytest.raises(ValueError, match="unsupported fingerprint kind"):
        Fingerprint("typo-rule", "synthetic.marker")
    with pytest.raises(ValueError, match="duplicate profile IDs"):
        ProfileRegistry(
            (_profile(),),
            (SyntheticProvider(profile_ids=("synthetic-profile", "synthetic-profile")),),
        )


def test_synthetic_provider_runs_through_the_contract(target, tmp_path):
    registry = ProfileRegistry((_profile(),), (SyntheticProvider(),))
    context = RunContext(
        targets=TargetSpec([target]), output_dir=tmp_path,
        scan_date="2026-07-22", network_authorized=False,
        provenance={"schema_version": 1}, tool_access=object(),
    )
    result = run_provider(registry.provider("synthetic-provider"), context, target)
    assert result.facts == ({"observed": True},)
    assert result.artifact_refs[0].path == "evidence/synthetic.json"


def test_result_data_and_artifact_paths_fail_closed(target):
    with pytest.raises(ValueError, match="JSON-safe"):
        CapabilityResult("c", "p", target.repo_id, facts=({"bad": object()},))
    with pytest.raises(ValueError, match="relative"):
        ArtifactRef("../outside.json")
    with pytest.raises(ValueError, match="relative"):
        ArtifactRef(str(Path(target.path) / "absolute.json"))


def test_executor_tool_access_resolves_reviewed_id_then_delegates(monkeypatch, target, tmp_path):
    seen = {}
    reviewed_tool = object()

    def fake_run(tooldef, actual_target, out, scan_date, repo_identity, **kwargs):
        seen.update(tooldef=tooldef, target=actual_target, out=out,
                    scan_date=scan_date, identity=repo_identity, kwargs=kwargs)
        return "result"

    def fake_resolve(tool_id, actual_target):
        seen.update(tool_id=tool_id, resolved_for=actual_target)
        return reviewed_tool

    monkeypatch.setattr("analysis_wrapper.profiles.tool_access.tool_for", fake_resolve)
    monkeypatch.setattr("analysis_wrapper.profiles.tool_access.run_tool", fake_run)
    spec = TargetSpec([target])
    identities = identity.build(
        spec, workspace_root=tmp_path,
        project_id=stable_repo_id(str(tmp_path)))
    access = ExecutorToolAccess(
        spec, identities, tmp_path, "2026-07-22", network_authorized=True)
    assert access.execute("scc", target, signal_id="fixture") == "result"
    assert seen["tool_id"] == "scc"
    assert seen["tooldef"] is reviewed_tool
    assert seen["target"] is target
    assert seen["identity"].reference == Path(target.path).name
    assert seen["kwargs"] == {"signal_id": "fixture", "allow_network": True}
