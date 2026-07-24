"""57B-78: the single-run capability-provider execution loop.

Providers here are synthetic (``profile_ids`` like ``synthetic-a``); no real
bundled provider is exercised in most of these tests — the loop mechanics
(selection, determinism, failure isolation, network-flag plumbing) are
exactly the same regardless of which providers are registered. The four REAL
bundled providers (57B-81 PR2) get their own dedicated coverage in
``tests/test_lane_providers.py`` and the shared conformance battery.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis_wrapper import identity
from analysis_wrapper.evidence import Coverage, Fact, catalog
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import CapabilityResult, Fingerprint, Profile, RunContext
from analysis_wrapper.profiles.execution import (
    FILENAME,
    RecordingToolAccess,
    run_provider_stage,
    run_providers,
    write_execution_record,
)
from analysis_wrapper.profiles.registry import ProfileRegistry
from analysis_wrapper.profiles.tool_access import ExecutorToolAccess
from analysis_wrapper.status import Status
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, TechnologyFacet, stable_repo_id

_BANNED_LITERALS = (
    "\"go\"", "'go'", "javascript", "typescript", "\"js\"", "'js'",
    "\"ts\"", "'ts'", "python", "express", "django", "gorm", "react",
)


def _target(path: Path, *facets: TechnologyFacet) -> RepoTarget:
    path.mkdir(parents=True, exist_ok=True)
    return RepoTarget(repo_id=stable_repo_id(str(path)), path=str(path.resolve()),
                      facets=list(facets))


def _identities(workspace: Path, repos: list[RepoTarget]):
    return identity.build(
        TargetSpec(repos), workspace_root=workspace,
        project_id=stable_repo_id(str(workspace)))


def _coverage(**overrides):
    fields = {"applicability": "applicable", "status": "complete",
              "reason_code": "ok", "detail": ""}
    fields.update(overrides)
    return Coverage(**fields)


def _profile(profile_id: str, capability_id: str) -> Profile:
    return Profile(
        profile_id=profile_id, kind="language", display_name=profile_id,
        fingerprints=(Fingerprint("manifest-file", "synthetic.marker"),),
        capability_ids=(capability_id,),
    )


class _NoopToolAccess:
    """A provider that never calls tool_access should never reach this."""

    def execute(self, tool_id, target, *, signal_id="", tooldef=None):
        raise AssertionError("tool_access should not be used by this provider")


@dataclass(frozen=True)
class _Provider:
    provider_id: str
    capability_id: str
    profile_ids: tuple
    facts: tuple = ()

    def run(self, context, target):
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id, coverage=_coverage(), facts=self.facts,
        )


def _context(repo: RepoTarget, tool_access, identities=None, **overrides) -> RunContext:
    fields = {
        "targets": TargetSpec([repo]), "output_dir": Path("."), "scan_date": "2026-07-23",
        "network_authorized": False, "provenance": {}, "tool_access": tool_access,
        "identities": identities,
    }
    fields.update(overrides)
    return RunContext(**fields)


def test_selection_order_is_deterministic_and_stable_across_runs(tmp_path):
    workspace = tmp_path / "workspace"
    alpha = _target(workspace / "alpha", TechnologyFacet("synthetic-a", "language", ["."], ["m"]))
    beta = _target(workspace / "beta", TechnologyFacet("synthetic-b", "language", ["."], ["m"]))
    identities = _identities(workspace, [alpha, beta])
    registry = ProfileRegistry(
        (_profile("synthetic-a", "cap-a"), _profile("synthetic-b", "cap-b")),
        (_Provider("prov-two", "cap-a", ("synthetic-a",)),
         _Provider("prov-one", "cap-b", ("synthetic-b",))),
    )
    context = RunContext(
        targets=TargetSpec([alpha, beta]), output_dir=tmp_path, scan_date="2026-07-23",
        network_authorized=False, provenance={}, tool_access=_NoopToolAccess(),
        identities=identities,
    )

    results_one, rows_one = run_providers(registry, context)
    results_two, rows_two = run_providers(registry, context)

    assert [row["provider_id"] for row in rows_one] == ["prov-one", "prov-two"]
    assert rows_one == [
        {"provider_id": "prov-one", "capability_id": "cap-b",
         "repository_ref": "beta", "matched_profiles": ["synthetic-b"],
         "outcome": "completed", "reason": "",
         "coverage": {"applicability": "applicable", "status": "complete",
                     "reason_code": "ok"},
         "tools": []},
        {"provider_id": "prov-two", "capability_id": "cap-a",
         "repository_ref": "alpha", "matched_profiles": ["synthetic-a"],
         "outcome": "completed", "reason": "",
         "coverage": {"applicability": "applicable", "status": "complete",
                     "reason_code": "ok"},
         "tools": []},
    ]
    assert rows_one == rows_two
    assert len(results_one) == 2


def test_duplicate_facets_execute_provider_once_with_disclosed_matches(tmp_path):
    workspace = tmp_path / "workspace"
    repo = _target(
        workspace / "combo",
        TechnologyFacet("synthetic-a", "language", ["."], ["m"]),
        TechnologyFacet("synthetic-b", "language", ["."], ["m"]),
    )
    identities = _identities(workspace, [repo])
    registry = ProfileRegistry(
        (_profile("synthetic-a", "cap"), _profile("synthetic-b", "cap")),
        (_Provider("prov", "cap", ("synthetic-a", "synthetic-b")),),
    )
    context = _context(repo, _NoopToolAccess(), identities=identities, targets=TargetSpec([repo]))

    results, rows = run_providers(registry, context)

    assert len(rows) == 1
    assert rows[0]["matched_profiles"] == ["synthetic-a", "synthetic-b"]
    assert len(results) == 1


def test_multiple_providers_with_conflicting_facts_reach_catalog_unmerged(tmp_path):
    """Two providers disagree about the SAME dependency's resolved version —
    same kind, same natural key ("name"), contradictory data — and the
    catalog must keep both rather than merging or reconciling them."""
    workspace = tmp_path / "workspace"
    repo = _target(workspace / "svc", TechnologyFacet("synthetic-a", "language", ["."], ["m"]))
    identities = _identities(workspace, [repo])
    fact_one = Fact(fact_id="fact:one", kind="dependency",
                    data={"name": "left-pad", "resolved_version": "1.2.0"})
    fact_two = Fact(fact_id="fact:two", kind="dependency",
                    data={"name": "left-pad", "resolved_version": "1.3.0"})
    registry = ProfileRegistry(
        (_profile("synthetic-a", "shared-cap"),),
        (_Provider("prov-a", "shared-cap", ("synthetic-a",), facts=(fact_one,)),
         _Provider("prov-b", "shared-cap", ("synthetic-a",), facts=(fact_two,))),
    )
    context = _context(repo, _NoopToolAccess(), identities=identities, targets=TargetSpec([repo]))

    results, rows = run_providers(registry, context)

    assert len(results) == 2
    assert {row["provider_id"] for row in rows} == {"prov-a", "prov-b"}
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    document = catalog.build(results, identities, run_dir)
    items = document["capabilities"]["shared-cap"]["items"]
    assert len(items) == 2
    all_data = {
        tuple(sorted(row["data"].items()))
        for entry in items for row in entry["facts"]["items"]
    }
    assert all_data == {
        (("name", "left-pad"), ("resolved_version", "1.2.0")),
        (("name", "left-pad"), ("resolved_version", "1.3.0")),
    }


@dataclass(frozen=True)
class _RaisingProvider:
    provider_id: str = "prov-fail"
    capability_id: str = "cap"
    profile_ids: tuple = ("synthetic-a",)

    def run(self, context, target):
        raise RuntimeError("boom")


def test_provider_failure_is_recorded_not_raised_and_others_still_run(tmp_path):
    workspace = tmp_path / "workspace"
    repo = _target(workspace / "svc", TechnologyFacet("synthetic-a", "language", ["."], ["m"]))
    identities = _identities(workspace, [repo])
    registry = ProfileRegistry(
        (_profile("synthetic-a", "cap"),),
        (_RaisingProvider(), _Provider("prov-ok", "cap", ("synthetic-a",))),
    )
    context = _context(repo, _NoopToolAccess(), identities=identities, targets=TargetSpec([repo]))

    results, rows = run_providers(registry, context)

    failed_row = next(row for row in rows if row["provider_id"] == "prov-fail")
    ok_row = next(row for row in rows if row["provider_id"] == "prov-ok")
    assert failed_row["outcome"] == "failed"
    assert failed_row["reason"].startswith("RuntimeError")
    assert failed_row["coverage"] is None
    assert ok_row["outcome"] == "completed"
    assert len(results) == 1
    assert results[0].provider_id == "prov-ok"


class _StubSkipToolAccess:
    def execute(self, tool_id, target, *, signal_id="", tooldef=None):
        return SimpleNamespace(status=Status.SKIPPED)


@dataclass(frozen=True)
class _ToolAwareProvider:
    provider_id: str = "prov-tool"
    capability_id: str = "cap"
    profile_ids: tuple = ("synthetic-a",)

    def run(self, context, target):
        result = context.tool_access.execute("stub-tool", target, signal_id="probe")
        outcome_status = "skipped" if result.status is Status.SKIPPED else "complete"
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id,
            coverage=Coverage(applicability="applicable", status=outcome_status,
                              reason_code="tool-unavailable", detail="stub tool skipped"),
        )


def test_missing_tool_is_disclosed_honestly_in_the_tool_log_and_coverage(tmp_path):
    workspace = tmp_path / "workspace"
    repo = _target(workspace / "svc", TechnologyFacet("synthetic-a", "language", ["."], ["m"]))
    identities = _identities(workspace, [repo])
    registry = ProfileRegistry((_profile("synthetic-a", "cap"),), (_ToolAwareProvider(),))
    context = _context(repo, _StubSkipToolAccess(), identities=identities, targets=TargetSpec([repo]))

    results, rows = run_providers(registry, context)

    assert rows[0]["outcome"] == "completed"
    assert rows[0]["tools"] == [
        {"tool_id": "stub-tool", "signal_id": "probe", "status": "skipped"}]
    assert rows[0]["coverage"]["status"] == "skipped"
    assert len(results) == 1


@pytest.mark.parametrize("network_authorized", [False, True])
def test_network_flag_reaches_run_tool_and_is_recorded(
        monkeypatch, tmp_path, network_authorized):
    workspace = tmp_path / "workspace"
    repo = _target(workspace / "svc", TechnologyFacet("synthetic-a", "language", ["."], ["m"]))
    identities = _identities(workspace, [repo])
    seen = {}

    def fake_run(tooldef, actual_target, out, scan_date, repo_identity, **kwargs):
        seen["kwargs"] = kwargs
        return SimpleNamespace(status=Status.COMPLETE)

    def fake_resolve(tool_id, actual_target):
        return object()

    monkeypatch.setattr("analysis_wrapper.profiles.tool_access.tool_for", fake_resolve)
    monkeypatch.setattr("analysis_wrapper.profiles.tool_access.run_tool", fake_run)

    @dataclass(frozen=True)
    class _NetworkProvider:
        provider_id: str = "prov-net"
        capability_id: str = "cap"
        profile_ids: tuple = ("synthetic-a",)

        def run(self, context, target):
            context.tool_access.execute("network-tool", target)
            return CapabilityResult(
                capability_id=self.capability_id, provider_id=self.provider_id,
                repo_id=target.repo_id, coverage=_coverage(),
            )

    registry = ProfileRegistry((_profile("synthetic-a", "cap"),), (_NetworkProvider(),))
    spec = TargetSpec([repo])
    access = ExecutorToolAccess(spec, identities, tmp_path, "2026-07-23",
                                network_authorized=network_authorized)
    context = RunContext(
        targets=spec, output_dir=tmp_path, scan_date="2026-07-23",
        network_authorized=network_authorized, provenance={}, tool_access=access,
        identities=identities,
    )

    results, rows = run_providers(registry, context)

    assert seen["kwargs"]["allow_network"] is network_authorized
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_execution_record(run_dir, rows=rows, network_authorized=network_authorized,
                           scan_date="2026-07-23")
    document = json.loads((run_dir / FILENAME).read_text("utf-8"))
    assert document["network_authorized"] is network_authorized


def test_run_provider_stage_records_network_authorized_true(tmp_path):
    """Cheap end-to-end check that the True direction also survives the
    one-call driver into the written record (the loop itself is exercised
    above; the empty bundled registry means no real provider runs here)."""
    workspace = tmp_path / "workspace"
    repo = _target(workspace / "svc")
    identities = _identities(workspace, [repo])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    spec = TargetSpec([repo])

    run_provider_stage(
        run_dir, spec, identities, scan_date="2026-07-23",
        network_authorized=True, provenance={"schema_version": 1},
    )

    document = json.loads((run_dir / FILENAME).read_text("utf-8"))
    assert document["network_authorized"] is True


@dataclass(frozen=True)
class _EmptyProvider:
    provider_id: str = "prov-empty"
    capability_id: str = "cap"
    profile_ids: tuple = ("synthetic-a",)

    def run(self, context, target):
        return CapabilityResult(
            capability_id=self.capability_id, provider_id=self.provider_id,
            repo_id=target.repo_id, coverage=_coverage(),
        )


def test_legitimate_empty_result_is_completed_not_failed(tmp_path):
    workspace = tmp_path / "workspace"
    repo = _target(workspace / "svc", TechnologyFacet("synthetic-a", "language", ["."], ["m"]))
    identities = _identities(workspace, [repo])
    registry = ProfileRegistry((_profile("synthetic-a", "cap"),), (_EmptyProvider(),))
    context = _context(repo, _NoopToolAccess(), identities=identities, targets=TargetSpec([repo]))

    results, rows = run_providers(registry, context)

    assert rows[0]["outcome"] == "completed"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    document = catalog.build(results, identities, run_dir)
    facts_view = document["capabilities"]["cap"]["items"][0]["facts"]
    assert facts_view == {"total_count": 0, "included_count": 0, "truncated": False, "items": []}


def test_run_provider_stage_only_runs_universal_providers_with_no_matching_facets(tmp_path):
    """A repo with NO detected facets matches none of the four FACET-GATED
    bundled providers (57B-81 PR2's callgraph/dependency-map ones, each
    linked to a specific language facet) — but the EIGHT ``universal``
    providers (57B-80's datastore-evidence, 57B-82's deploy-units/
    dependency-risk/git-history, 57B-84's access-evidence,
    integration-evidence, route-inventory, and ui-route-linkage) all run
    regardless, so this is no longer a zero-execution no-op. Seven of the
    eight (all but datastore-evidence) are ALSO zero-profile — see
    ``profiles/registry.py``'s carve-out — so their empty
    ``matched_profiles`` reflects having no profile to match at all, not
    merely a facet that didn't match. This bare repo is also non-git with no
    declared lockfile/ecosystem, so dependency-risk/git-history both land on
    their own not-applicable branch rather than executing a real signal
    tool. This run dir also has no discovery-report.json (this test
    exercises the provider-stage loop in isolation, the same synthetic-
    ``identities`` shape every other test in this module uses) —
    route-inventory/ui-route-linkage's own ``module_signals.routes`` gate
    input degrades to "unknown" in that case (``_has_module_signal_routes``'s
    own documented fallback), same as a real repo discovery found no route
    signal. The stage stays deterministic and byte-identical across repeated
    calls either way."""
    workspace = tmp_path / "workspace"
    repo = _target(workspace / "svc")
    identities = _identities(workspace, [repo])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    spec = TargetSpec([repo])

    summary_one = run_provider_stage(
        run_dir, spec, identities, scan_date="2026-07-23",
        network_authorized=False, provenance={"schema_version": 1},
    )
    execution_bytes_one = (run_dir / FILENAME).read_bytes()
    catalog_bytes_one = (run_dir / catalog.FILENAME).read_bytes()

    summary_two = run_provider_stage(
        run_dir, spec, identities, scan_date="2026-07-23",
        network_authorized=False, provenance={"schema_version": 1},
    )
    execution_bytes_two = (run_dir / FILENAME).read_bytes()
    catalog_bytes_two = (run_dir / catalog.FILENAME).read_bytes()

    assert summary_one == {"executions": 8, "failed": 0} == summary_two
    executions = json.loads(execution_bytes_one)["executions"]
    assert [row["provider_id"] for row in executions] == [
        "access-evidence", "datastore-evidence", "dependency-risk",
        "deploy-units", "git-history", "integration-evidence",
        "route-inventory", "ui-route-linkage"]
    for row in executions:
        assert row["matched_profiles"] == []
        assert row["universal"] is True
    assert execution_bytes_one == execution_bytes_two
    assert catalog_bytes_one == catalog_bytes_two
    assert bundled_registry().providers


def test_execution_module_has_no_technology_literals():
    from analysis_wrapper.profiles import execution
    source = Path(execution.__file__).read_text("utf-8")
    lowered = source.lower()
    hits = [literal for literal in _BANNED_LITERALS if literal in lowered]
    assert hits == []


def test_provider_execution_record_never_leaks_raw_repo_id(tmp_path):
    workspace = tmp_path / "workspace"
    repo = _target(workspace / "svc", TechnologyFacet("synthetic-a", "language", ["."], ["m"]))
    identities = _identities(workspace, [repo])
    registry = ProfileRegistry(
        (_profile("synthetic-a", "cap"),), (_Provider("prov", "cap", ("synthetic-a",)),),
    )
    context = _context(repo, _NoopToolAccess(), identities=identities, targets=TargetSpec([repo]))

    _, rows = run_providers(registry, context)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_execution_record(run_dir, rows=rows, network_authorized=False, scan_date="2026-07-23")

    serialized = (run_dir / FILENAME).read_text("utf-8")
    assert repo.repo_id not in serialized
