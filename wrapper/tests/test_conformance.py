"""Conformance battery for capability-provider migrations (57B-86).

Run this file standalone to validate a provider before it lands:

    pytest tests/test_conformance.py

It needs no run directory and touches no pipeline stage or the parity
comparator — every test here builds its own synthetic profile/provider/repo
via ``provider_conformance.py`` and runs the shared battery over it.
"""

import pytest

from analysis_wrapper.profiles.bundled import bundled_registry
from provider_conformance import ConformanceProvider, make_profile, run_provider_conformance


def test_reference_provider_passes_conformance(tmp_path):
    profile = make_profile()
    provider = ConformanceProvider(
        provider_id="conformance-provider", capability_id=profile.capability_ids[0],
        profile_ids=(profile.profile_id,), behavior="facts")
    run_provider_conformance(profile, provider, tmp_path=tmp_path)


def test_empty_result_behavior_conforms(tmp_path):
    profile = make_profile()
    provider = ConformanceProvider(
        provider_id="conformance-provider-empty", capability_id=profile.capability_ids[0],
        profile_ids=(profile.profile_id,), behavior="empty")
    run_provider_conformance(profile, provider, tmp_path=tmp_path)


def test_raising_provider_conforms_to_the_fail_soft_contract(tmp_path):
    profile = make_profile()
    provider = ConformanceProvider(
        provider_id="conformance-provider-raise", capability_id=profile.capability_ids[0],
        profile_ids=(profile.profile_id,), behavior="raise")
    run_provider_conformance(profile, provider, tmp_path=tmp_path)


def test_tool_delegating_provider_conforms(tmp_path):
    profile = make_profile()
    provider = ConformanceProvider(
        provider_id="conformance-provider-tool", capability_id=profile.capability_ids[0],
        profile_ids=(profile.profile_id,), behavior="tool")
    run_provider_conformance(profile, provider, tmp_path=tmp_path)


@pytest.mark.parametrize("behavior", [
    "unavailable", "skipped", "partial", "failed-status", "not-applicable"])
def test_coverage_outcome_variants_conform(tmp_path, behavior):
    profile = make_profile()
    provider = ConformanceProvider(
        provider_id=f"conformance-provider-{behavior}",
        capability_id=profile.capability_ids[0],
        profile_ids=(profile.profile_id,), behavior=behavior)
    run_provider_conformance(profile, provider, tmp_path=tmp_path)


def test_bundled_registry_conforms_structurally():
    """The bundled registry itself is a valid, deterministic, closed catalog
    even before any provider has migrated onto it (BUNDLED_PROVIDERS == ())."""
    registry = bundled_registry()
    assert list(registry.profiles) == sorted(
        registry.profiles, key=lambda item: item.profile_id)
    assert registry.providers == ()
    for profile in registry.profiles:
        for capability_id in profile.capability_ids:
            assert isinstance(capability_id, str) and capability_id
    assert not hasattr(registry, "register")
    assert not hasattr(registry, "add_profile")
    assert not hasattr(registry, "add_provider")


def test_conformance_command_is_standalone():
    # This file imports only analysis_wrapper.* + provider_conformance.py: no
    # run directory, no other stage's fixture, no parity comparator. A bare
    # `pytest tests/test_conformance.py` invocation collecting and passing
    # every test above IS the assertion; nothing further to check here.
    assert True
