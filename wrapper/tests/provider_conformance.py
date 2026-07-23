"""Shared conformance battery for capability-provider migrations (57B-86).

This is a plain importable module (mirroring ``system_model_fixtures.py`` —
not a pytest fixture file) that centralizes the provider-testing patterns
already duplicated across ``test_profile_contracts.py`` and
``test_provider_execution.py`` into one reusable battery every future
capability-provider migration (57B-81 and beyond) must pass.

The conformance command is:

    pytest tests/test_conformance.py

It runs independently of the pipeline (nothing here touches
``prepare-overview`` or any real run directory) and independently of the
parity comparator (:mod:`analysis_wrapper.parity`) — a provider that fails
this battery is broken regardless of what a two-run diff would show.

Detection (battery step 1) is honestly split in two, because
``profiles.detection.detect`` evaluates ONLY ``bundled_registry()``: a
synthetic profile (the common case here, and for any provider still under
development before its profile is bundled) can never be observed through
``detect()``, so this module instead verifies the FINGERPRINT mechanism
directly — the marker file the profile names is actually present, and a
facet built the way discovery would build one carries the right profile id.
Only a profile whose id is already registered in ``bundled_registry()``
goes through the real ``detect()`` call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from analysis_wrapper import identity
from analysis_wrapper.evidence import (
    APPLICABILITY_VALUES,
    STATUS_VALUES,
    Coverage,
    Fact,
    SourceRef,
    make_fact_id,
)
from analysis_wrapper.evidence import catalog
from analysis_wrapper.identity import IdentityMap
from analysis_wrapper.profiles import detection
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import (
    CapabilityResult,
    Fingerprint,
    Profile,
    RunContext,
    ToolAccess,
)
from analysis_wrapper.profiles.execution import (
    FILENAME,
    RecordingToolAccess,
    run_providers,
    write_execution_record,
)
from analysis_wrapper.profiles.registry import ProfileRegistry
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.status import Status
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, TechnologyFacet, stable_repo_id

_CONFORMANCE_TOOL_ID = "conformance-tool"

# Bare coverage-outcome behaviors: name -> (applicability, status, detail).
# Each exercises one axis of the Coverage vocabulary in isolation so the
# execution loop's outcome vocabulary (battery step 6) can be checked one
# outcome at a time.
_COVERAGE_OUTCOMES: dict[str, tuple[str, str, str]] = {
    "unavailable": ("applicable", "unavailable", ""),
    "skipped": ("applicable", "skipped", ""),
    "partial": ("applicable", "partial", ""),
    "failed-status": ("applicable", "failed", ""),
    "not-applicable": ("not-applicable", "complete",
                       "conformance battery: positive evidence of non-applicability"),
}


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------


def make_coverage(**overrides) -> Coverage:
    fields = {"applicability": "applicable", "status": "complete",
              "reason_code": "ok", "detail": ""}
    fields.update(overrides)
    return Coverage(**fields)


def make_profile(profile_id: str = "conformance-profile",
                 capability_id: str = "conformance-capability",
                 marker: str = "conformance.marker") -> Profile:
    return Profile(
        profile_id=profile_id, kind="language", display_name=profile_id,
        fingerprints=(Fingerprint("manifest-file", marker),),
        capability_ids=(capability_id,),
    )


def make_repo(tmp_path: Path, *, marker: str = "conformance.marker",
             name: str = "repo", profile_id: str = "conformance-profile") -> RepoTarget:
    path = Path(tmp_path) / name
    path.mkdir(parents=True, exist_ok=True)
    (path / marker).touch()
    return RepoTarget(
        repo_id=stable_repo_id(str(path)), path=str(path),
        facets=[TechnologyFacet(profile_id, "language", ["."], [marker])],
    )


def make_identities(workspace: Path, repos: list[RepoTarget]) -> IdentityMap:
    return identity.build(
        TargetSpec(list(repos)), workspace_root=workspace,
        project_id=stable_repo_id(str(workspace)))


def make_context(spec: TargetSpec, output_dir: Path, *, tool_access: ToolAccess,
                 network_authorized: bool = False, scan_date: str = "2026-07-23",
                 provenance: dict | None = None,
                 identities: IdentityMap | None = None) -> RunContext:
    return RunContext(
        targets=spec, output_dir=Path(output_dir), scan_date=scan_date,
        network_authorized=network_authorized,
        provenance=provenance if provenance is not None else {"schema_version": 1},
        tool_access=tool_access, identities=identities,
    )


@dataclass(frozen=True)
class _StatusStub:
    """A minimal object exposing just the ``.status`` a provider reads."""

    status: object

    def execute(self, tool_id, target, *, signal_id=""):
        return self


@dataclass(frozen=True)
class ConformanceProvider:
    """One behavior-parameterized reference provider exercising every path.

    ``behavior`` selects:
      "facts"    — one Fact (carrying a SourceRef) + complete coverage.
      "empty"    — applicable + complete + zero facts (a legitimate empty scan).
      "raise"    — raises RuntimeError (must be recorded, never sink the loop).
      "tool"     — delegates to ``context.tool_access`` and echoes the
                   resulting status back into its own coverage.
      any key of ``_COVERAGE_OUTCOMES`` — returns a CapabilityResult whose
                   Coverage carries exactly that applicability/status, so the
                   loop's outcome vocabulary is exercised one axis at a time.
    """

    provider_id: str
    capability_id: str
    profile_ids: tuple[str, ...]
    behavior: str = "facts"

    def run(self, context: RunContext, target: RepoTarget) -> CapabilityResult:
        if self.behavior == "raise":
            raise RuntimeError("conformance provider intentionally failed")
        if self.behavior == "tool":
            result = context.tool_access.execute(
                _CONFORMANCE_TOOL_ID, target, signal_id="conformance-probe")
            status = result.status
            status_text = status.value if hasattr(status, "value") else str(status)
            mapped_status = status_text if status_text in STATUS_VALUES else "failed"
            return CapabilityResult(
                capability_id=self.capability_id, provider_id=self.provider_id,
                repo_id=target.repo_id,
                coverage=make_coverage(status=mapped_status, reason_code="tool-echo",
                                       detail=f"echoed tool status {status_text!r}"),
            )
        if self.behavior in _COVERAGE_OUTCOMES:
            applicability, status, detail = _COVERAGE_OUTCOMES[self.behavior]
            return CapabilityResult(
                capability_id=self.capability_id, provider_id=self.provider_id,
                repo_id=target.repo_id,
                coverage=Coverage(applicability=applicability, status=status,
                                  reason_code=f"conformance-{self.behavior}", detail=detail),
            )
        if self.behavior == "empty":
            return CapabilityResult(
                capability_id=self.capability_id, provider_id=self.provider_id,
                repo_id=target.repo_id, coverage=make_coverage(),
            )
        if self.behavior == "facts":
            # 57B-81: RunContext now carries the run's IdentityMap, so a
            # provider resolves its own target's human-readable reference
            # through context.identities rather than guessing one from a
            # path. The basename fallback below is kept ONLY for identity-
            # less unit contexts (identities=None) that predate this wiring
            # and would otherwise leak the raw internal repo_id (battery
            # step 5) or collide across duplicate-basename repos.
            if context.identities is not None:
                reference = context.identities.reference_for(target.repo_id)
            else:
                reference = Path(target.path).name
            fact = Fact(
                fact_id=make_fact_id(self.capability_id, target.repo_id,
                                     "observation", (reference,)),
                kind="observation", data={"observed": True},
                source_refs=(SourceRef(repository_ref=reference, revision="NON-GIT",
                                       path="conformance.marker", line=1),),
            )
            return CapabilityResult(
                capability_id=self.capability_id, provider_id=self.provider_id,
                repo_id=target.repo_id, coverage=make_coverage(), facts=(fact,),
            )
        raise ValueError(f"unknown conformance behavior {self.behavior!r}")


# ---------------------------------------------------------------------------
# The battery itself
# ---------------------------------------------------------------------------


def run_provider_conformance(profile: Profile, provider, *, tmp_path: Path,
                             extra_profiles: tuple[Profile, ...] = ()) -> None:
    """Run every conformance step for one (profile, provider) pair.

    ``extra_profiles`` is for a MULTI-profile provider (one whose
    ``profile_ids`` names more than one profile, e.g. a provider linked to
    both a ``language.javascript`` and a ``language.typescript`` profile):
    the registry construction below must carry every profile the provider
    references (``ProfileRegistry`` rejects a provider that names an unknown
    profile), even though this battery only asserts DETECTION for the
    primary ``profile`` argument.
    """
    marker = profile.fingerprints[0].value
    repo = make_repo(tmp_path / "target", marker=marker, profile_id=profile.profile_id)
    workspace = tmp_path / "target"
    is_bundled = profile.profile_id in {
        item.profile_id for item in bundled_registry().profiles}

    # 1. Detection.
    if is_bundled:
        report = detection.detect(repo.path)
        assert any(facet.profile_id == profile.profile_id for facet in report.facets), (
            f"bundled detect() did not observe {profile.profile_id!r} from its "
            "own fingerprint marker")
    else:
        assert (Path(repo.path) / marker).is_file(), (
            "conformance repo is missing the fingerprint marker its own profile names")
        constructed = TechnologyFacet(
            profile_id=profile.profile_id, kind=profile.kind,
            scope_roots=["."], evidence=[marker])
        assert constructed.profile_id == profile.profile_id, (
            "a facet built the way discovery would must carry the profile's own id")

    identities = make_identities(workspace, [repo])
    registry = ProfileRegistry((profile, *extra_profiles), (provider,))
    spec = TargetSpec([repo])

    # 2. Applicability: the provider is selected exactly once, and every
    # matching facet's profile id is disclosed.
    context = make_context(spec, tmp_path, tool_access=_StatusStub(status=Status.COMPLETE),
                           identities=identities)
    results, rows = run_providers(registry, context)
    matches = [row for row in rows if row["provider_id"] == provider.provider_id]
    assert len(matches) == 1, "an applicable provider must be selected exactly once"
    row = matches[0]
    assert profile.profile_id in row["matched_profiles"]

    # 3. Deterministic registration.
    assert ProfileRegistry((profile, *extra_profiles), (provider,)).profiles == registry.profiles
    assert ProfileRegistry((profile, *extra_profiles), (provider,)).providers == registry.providers
    with pytest.raises(ValueError, match="duplicate profile_id"):
        ProfileRegistry((profile, profile), (provider,))
    with pytest.raises(ValueError, match="explicit Profile"):
        ProfileRegistry(({"profile_id": profile.profile_id},), (provider,))

    # 4. Deterministic execution: identical inputs -> identical record rows,
    # and byte-identical written artifacts.
    results_2, rows_2 = run_providers(registry, context)
    assert rows_2 == rows, "identical inputs must select and record identically"
    run_a, run_b = tmp_path / "run-a", tmp_path / "run-b"
    run_a.mkdir()
    run_b.mkdir()
    write_execution_record(run_a, rows=rows, network_authorized=False, scan_date="2026-07-23")
    write_execution_record(run_b, rows=rows_2, network_authorized=False, scan_date="2026-07-23")
    assert (run_a / FILENAME).read_bytes() == (run_b / FILENAME).read_bytes()
    catalog.write(run_a, results, identities)
    catalog.write(run_b, results_2, identities)
    assert (run_a / catalog.FILENAME).read_bytes() == (run_b / catalog.FILENAME).read_bytes()

    # 5. Canonical results + 6. Outcome vocabulary.
    behavior = getattr(provider, "behavior", "")
    if behavior == "raise":
        assert row["outcome"] == "failed"
        assert row["reason"], "a failed provider must disclose a nonempty reason"
        assert row["coverage"] is None
        assert not results
    else:
        assert row["outcome"] == "completed"
        result = next(item for item in results if item.provider_id == provider.provider_id)
        assert isinstance(result, CapabilityResult)
        assert result.coverage.applicability in APPLICABILITY_VALUES
        assert result.coverage.status in STATUS_VALUES
        assert all(isinstance(fact, Fact) for fact in result.facts)
        assert row["coverage"] == {
            "applicability": result.coverage.applicability,
            "status": result.coverage.status,
            "reason_code": result.coverage.reason_code,
        }
        if behavior == "facts":
            assert result.facts and result.facts[0].source_refs
            # A reference provider must resolve its SourceRef.repository_ref
            # through the run's IdentityMap (57B-81), not guess one from a
            # path — the value must match exactly, not merely be non-empty.
            assert result.facts[0].source_refs[0].repository_ref == (
                identities.reference_for(repo.repo_id))
        if behavior == "empty":
            document = catalog.build(results, identities, run_a)
            items = document["capabilities"][provider.capability_id]["items"]
            assert items[0]["facts"] == {
                "total_count": 0, "included_count": 0, "truncated": False, "items": []}
        if behavior in _COVERAGE_OUTCOMES:
            applicability, status, _ = _COVERAGE_OUTCOMES[behavior]
            assert result.coverage.applicability == applicability
            assert result.coverage.status == status

    assert row["repository_ref"] == identities.reference_for(repo.repo_id)
    record_bytes = (run_a / FILENAME).read_bytes()
    catalog_bytes = (run_a / catalog.FILENAME).read_bytes()
    assert repo.repo_id.encode("utf-8") not in record_bytes, (
        "the raw internal repo_id must never leak into the execution record")
    assert repo.repo_id.encode("utf-8") not in catalog_bytes, (
        "the raw internal repo_id must never leak into the evidence catalog")

    # 7. Network policy: the flag reaches the record honestly both ways, and
    # a recording stub sees the same flag the provider itself observed.
    for authorized in (False, True):
        recorder = RecordingToolAccess(inner=_StatusStub(status=Status.COMPLETE))
        net_context = make_context(
            spec, tmp_path, tool_access=recorder, network_authorized=authorized,
            identities=identities)
        _, net_rows = run_providers(registry, net_context)
        net_run = tmp_path / f"net-{authorized}"
        net_run.mkdir()
        write_execution_record(
            net_run, rows=net_rows, network_authorized=authorized, scan_date="2026-07-23")
        document = json.loads((net_run / FILENAME).read_text("utf-8"))
        assert document["network_authorized"] is authorized
        if behavior == "tool":
            assert net_rows[0]["tools"] == [
                {"tool_id": _CONFORMANCE_TOOL_ID, "signal_id": "conformance-probe",
                 "status": "complete"}]

    # 8. Tool-access boundary: providers only ever see the narrow ToolAccess
    # surface — never a ToolDef, argv, or executor internals.
    real_access = ExecutorToolAccess(spec, identities, tmp_path, "2026-07-23",
                                     network_authorized=False)
    wrapped = RecordingToolAccess(inner=real_access)
    for access in (real_access, wrapped):
        assert isinstance(access, ToolAccess)
        assert hasattr(access, "execute")
        assert not hasattr(access, "argv")
        assert not hasattr(access, "tooldef")
        assert not hasattr(access, "subprocess")
