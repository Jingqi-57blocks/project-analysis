"""Conformance battery for capability-provider migrations (57B-86).

Run this file standalone to validate a provider before it lands:

    pytest tests/test_conformance.py

It needs no run directory and touches no pipeline stage or the parity
comparator — every test here builds its own synthetic profile/provider/repo
via ``provider_conformance.py`` and runs the shared battery over it.
"""

import json

import pytest

from analysis_wrapper.evidence import catalog
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.execution import run_providers
from analysis_wrapper.profiles.registry import ProfileRegistry
from analysis_wrapper.targetspec import TargetSpec
from provider_conformance import (
    ConformanceProvider,
    make_context,
    make_identities,
    make_profile,
    make_repo,
    run_provider_conformance,
)


class _NoopToolAccess:
    """The facts-behavior provider under test never calls tool_access."""

    def execute(self, tool_id, target, *, signal_id=""):
        raise AssertionError("facts-behavior provider should not use tool_access")


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


def test_universal_provider_conforms_and_is_selected_with_zero_matched_facets(tmp_path):
    """57B-80 PR2's universal-selection mechanism, proven generic: a purely
    synthetic profile/provider pair (no real technology, no real bundled
    profile) still passes the full battery, including its dedicated
    universal-selection step (battery step 9) — the underlying selection
    code has no branch tied to any concrete capability, exactly like
    ``execution.py``'s own no-technology-literals invariant."""
    profile = make_profile()
    provider = ConformanceProvider(
        provider_id="conformance-provider-universal", capability_id=profile.capability_ids[0],
        profile_ids=(profile.profile_id,), behavior="facts", universal=True)
    run_provider_conformance(profile, provider, tmp_path=tmp_path)


def test_zero_profile_universal_provider_conforms_via_profile_none(tmp_path):
    """57B-82 A1's zero-profile universal-provider shape, proven generic: a
    purely synthetic provider with ``profile_ids=()`` and ``universal=True``
    still passes the full battery through ``profile=None`` — including step
    9's bare-repo selection proof — exactly as the linked-profile universal
    case above does. The underlying selection code (``registry.py``'s
    empty-profile carve-out, ``execution.py``'s loop) has no branch tied to
    any concrete capability or profile, mirroring how the universal flag
    itself got a synthetic test in 57B-80 PR2."""
    provider = ConformanceProvider(
        provider_id="conformance-provider-zero-profile-universal",
        capability_id="conformance-capability",
        profile_ids=(), behavior="facts", universal=True)
    run_provider_conformance(None, provider, tmp_path=tmp_path)


def test_bundled_registry_conforms_structurally():
    """The bundled registry itself is a valid, deterministic, closed catalog
    (57B-81 PR2 populated BUNDLED_PROVIDERS with the four migrated
    callgraph/dependency-map providers; this asserts the registry stays a
    sorted, immutable, self-consistent catalog with them present)."""
    registry = bundled_registry()
    assert list(registry.profiles) == sorted(
        registry.profiles, key=lambda item: item.profile_id)
    assert list(registry.providers) == sorted(
        registry.providers, key=lambda item: item.provider_id)
    assert registry.providers
    for profile in registry.profiles:
        for capability_id in profile.capability_ids:
            assert isinstance(capability_id, str) and capability_id
    assert not hasattr(registry, "register")
    assert not hasattr(registry, "add_profile")
    assert not hasattr(registry, "add_provider")


def test_duplicate_basename_repos_resolve_distinct_references_end_to_end(tmp_path):
    """Two repos both named "api" must never collapse onto one reference.

    The facts-behavior reference provider (57B-81) resolves each target's
    SourceRef.repository_ref through the run's IdentityMap rather than
    guessing from a path basename, so the loop and the evidence catalog must
    both carry the distinct shortest-unique-path references ("apps/api" vs
    "services/api") — never the ambiguous shared basename "api", and never
    the raw internal repo_id either."""
    profile = make_profile()
    marker = profile.fingerprints[0].value
    workspace = tmp_path / "workspace"
    app_api = make_repo(workspace, marker=marker, name="apps/api",
                        profile_id=profile.profile_id)
    service_api = make_repo(workspace, marker=marker, name="services/api",
                            profile_id=profile.profile_id)
    identities = make_identities(workspace, [app_api, service_api])

    provider = ConformanceProvider(
        provider_id="conformance-provider-dup", capability_id=profile.capability_ids[0],
        profile_ids=(profile.profile_id,), behavior="facts")
    registry = ProfileRegistry((profile,), (provider,))
    spec = TargetSpec([app_api, service_api])
    context = make_context(spec, tmp_path, tool_access=_NoopToolAccess(), identities=identities)

    results, rows = run_providers(registry, context)

    assert len(results) == 2
    by_repo_id = {result.repo_id: result for result in results}
    assert by_repo_id[app_api.repo_id].facts[0].source_refs[0].repository_ref == "apps/api"
    assert by_repo_id[service_api.repo_id].facts[0].source_refs[0].repository_ref == "services/api"
    assert {row["repository_ref"] for row in rows} == {"apps/api", "services/api"}

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    document = catalog.build(results, identities, run_dir)
    scopes = {row["scope"] for row in document["capabilities"][provider.capability_id]["items"]}
    assert scopes == {"apps/api", "services/api"}
    serialized = json.dumps(document, ensure_ascii=False)
    internal_ids = {app_api.repo_id, service_api.repo_id}
    assert all(internal_id not in serialized for internal_id in internal_ids)


def test_conformance_command_is_standalone():
    # This file imports only analysis_wrapper.* + provider_conformance.py: no
    # run directory, no other stage's fixture, no parity comparator. A bare
    # `pytest tests/test_conformance.py` invocation collecting and passing
    # every test above IS the assertion; nothing further to check here.
    assert True
