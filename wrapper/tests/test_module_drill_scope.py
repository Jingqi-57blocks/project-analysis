"""57B-125: ModuleScope v1 and immutable Module Drill run layout."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from analysis_wrapper import capabilities, identity, module_map, run_provenance
from analysis_wrapper.evidence.coverage import Coverage
from analysis_wrapper.executor import WrapperSafetyError
from analysis_wrapper.lifecycle import Pointers, RunState
from analysis_wrapper.module_drill import (
    MODULE_SCOPE_VERSION,
    Boundary,
    ModuleCoverage,
    ModuleIdentity,
    ModuleRunLayout,
    ModuleScope,
    ModuleScopeRequest,
    OverviewLineage,
    OverviewScopeProvider,
    OwnedLocation,
    ProjectSnapshot,
    RepositorySnapshot,
    Selector,
    create_module_run,
    load_scope,
    mint_module_run_id,
    resolve_scope,
    write_scope,
)
from analysis_wrapper.system_model import assemble as system_model
from analysis_wrapper.targetspec import TargetSpec
from system_model_fixtures import write_run as write_system_run


SHA = "a" * 40


def _snapshot() -> ProjectSnapshot:
    return ProjectSnapshot(
        project_ref="sample-project",
        repositories=(RepositorySnapshot("api", SHA, "git"),),
    )


def _scope(mode: str = "standalone") -> ModuleScope:
    project = _snapshot()
    lineage = (OverviewLineage("overview-run", project.snapshot_id,
                               ("module-map.json:modules/billing",))
               if mode == "overview" else None)
    return ModuleScope(
        contract_version=MODULE_SCOPE_VERSION,
        source_mode=mode,
        project=project,
        selector=Selector("billing", "name"),
        module=ModuleIdentity("billing", "Billing", ("invoices",), "business", "high"),
        owned_scope=(OwnedLocation(
            "api", "internal/billing", ("internal/billing/service.go",),
            ("CreateInvoice",), (f"api@{SHA}:internal/billing/service.go:12",),
        ),),
        assigned_candidates=("candidate.billing",),
        boundaries=(Boundary(
            "outbound", "api", "payments", "api",
            (f"api@{SHA}:internal/billing/service.go:34",),
        ),),
        coverage=ModuleCoverage(
            (("callgraph", Coverage("applicable", "complete", "callgraph-complete")),),
            limitations=("UI entry is not proven.",),
            unknowns=("Runtime activation is unresolved.",),
        ),
        overview_lineage=lineage,
    )


def test_scope_json_round_trip_and_persistence_is_path_safe(tmp_path):
    scope = _scope("overview")
    path = tmp_path / "module-scope.json"

    assert load_scope(write_scope(path, scope)) == scope
    persisted = path.read_text(encoding="utf-8")
    assert str(tmp_path) not in persisted
    assert json.loads(persisted)["project"]["project_ref"] == "sample-project"

    with pytest.raises(FileExistsError):
        write_scope(path, scope)


@pytest.mark.parametrize("mutation, match", [
    (lambda doc: doc.__setitem__("contract_version", "module-scope/v0"), "unsupported"),
    (lambda doc: doc.pop("owned_scope"), "unsupported shape"),
    (lambda doc: doc["owned_scope"][0].__setitem__("root", "/Users/secret"), "relative"),
    (lambda doc: doc["boundaries"][0].__setitem__("neighbor_id", "billing"), "owned module"),
    (lambda doc: doc["project"].__setitem__("project_ref", "/Users/secret"), "relative"),
    (lambda doc: doc["owned_scope"][0]["evidence_refs"].__setitem__(
        0, f"api@{SHA}:/Users/secret.go:1"), "relative"),
])
def test_scope_rejects_invalid_contract_and_boundary_shapes(mutation, match):
    document = _scope().to_dict()
    mutation(document)
    with pytest.raises(ValueError, match=match):
        ModuleScope.from_dict(document)


def test_overview_and_standalone_providers_normalize_the_same_core_scope():
    project = _snapshot()
    request = ModuleScopeRequest("standalone", project, Selector("billing", "name"))

    class StandaloneProvider:
        source_mode = "standalone"

        def resolve(self, _request):
            return _scope("standalone")

    standalone = resolve_scope(StandaloneProvider(), request)

    overview_request = ModuleScopeRequest("overview", project, Selector("billing", "name"))

    class OverviewProvider:
        source_mode = "overview"

        def resolve(self, _request):
            return _scope("overview")

    overview = resolve_scope(OverviewProvider(), overview_request)
    assert standalone.module == overview.module
    assert standalone.owned_scope == overview.owned_scope
    assert standalone.boundaries == overview.boundaries
    assert standalone.project == overview.project


def test_provider_cannot_return_scope_for_other_selector_or_source_mode():
    class BadProvider:
        source_mode = "standalone"

        def resolve(self, _request):
            return _scope("overview")

    with pytest.raises(ValueError, match="wrong source_mode"):
        resolve_scope(BadProvider(), ModuleScopeRequest(
            "standalone", _snapshot(), Selector("billing", "name")))


def test_overview_requires_matching_lineage_and_standalone_rejects_it():
    project = _snapshot()
    document = _scope("overview").to_dict()
    document["overview_lineage"]["snapshot_id"] = "b" * 16
    with pytest.raises(ValueError, match="must match"):
        ModuleScope.from_dict(document)

    document = _scope("standalone").to_dict()
    document["overview_lineage"] = {
        "source_run_id": "overview-run",
        "snapshot_id": project.snapshot_id,
        "evidence_refs": ["module-map.json:modules/billing"],
    }
    with pytest.raises(ValueError, match="standalone"):
        ModuleScope.from_dict(document)


def test_layout_is_immutable_collision_safe_and_has_canonical_paths(tmp_path, target):
    layout = ModuleRunLayout(tmp_path / "skill", "sample-project", "billing", "first-run")
    scope = _scope()
    created = create_module_run(layout, TargetSpec([target]), scope, language="en")

    assert created.run_dir == (
        tmp_path / "skill" / "output" / "sample-project" / "modules" / "billing" / "first-run"
    )
    assert created.scope_path.is_file()
    assert created.evidence_path.name == "module-evidence.json"
    assert created.prd_path.name == "prd.md" and created.health_path.name == "health.md"
    assert created.html_export_dir == (
        tmp_path / "skill" / "exported" / "sample-project-analysis" / "modules"
        / "billing" / "first-run" / "html"
    )
    assert json.loads(created.run_state_path.read_text()) == {
        "contract_version": "module-run/v1",
        "language": "en",
        "module_id": "billing",
        "project_key": "sample-project",
        "run_id": "first-run",
        "source_mode": "standalone",
        "stages": {"scope": "done", "evidence": "pending", "prd": "pending", "health": "pending"},
    }

    with pytest.raises(ValueError, match="already exists"):
        create_module_run(layout, TargetSpec([target]), scope, language="en")


def test_module_run_id_is_readable_deterministic_and_unique_per_existing_run(tmp_path):
    root = tmp_path / "skill"
    moment = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
    first = mint_module_run_id(
        root, "sample-project", "billing", _snapshot(), language="en",
        label="comparison", when=moment)
    assert first.startswith("comparison-")
    taken = root / "output" / "sample-project" / "modules" / "billing" / first
    taken.mkdir(parents=True)
    assert mint_module_run_id(
        root, "sample-project", "billing", _snapshot(), language="en",
        label="comparison", when=moment) == f"{first}-2"


def test_module_run_refuses_a_layout_inside_analyzed_source(target):
    layout = ModuleRunLayout(target.path, "sample-project", "billing", "unsafe-run")
    with pytest.raises(WrapperSafetyError, match="inside target"):
        create_module_run(layout, TargetSpec([target]), _scope(), language="en")


def _overview_run(tmp_path, monkeypatch):
    """A minimal completed overview made from normal structured artifacts."""
    run = write_system_run(tmp_path / "overview")
    model = system_model.assemble(run)
    system_model.dump(model, run)
    module_map.write_candidates(run, model.to_dict())
    candidates = json.loads((run / "module-candidates.json").read_text("utf-8"))
    (run / "module-map.json").write_text(json.dumps({
        "schema_version": module_map.MAP_SCHEMA_VERSION,
        "modules": [{"module_id": "sample-capability", "name": "Sample capability",
                     "classification": "business", "confidence": "medium",
                     "aliases": ["sample"]}],
        "candidate_dispositions": [{
            "candidate_id": row["candidate_id"], "disposition": "merged",
            "module_ids": ["sample-capability"], "reason": "fixture ownership",
        } for row in candidates["candidates"]],
    }), "utf-8")
    capabilities.write(run)
    spec = TargetSpec.load(run / "targets.json")
    identities = identity.load(run)
    state = RunState.create("scope-fixture", identities.project.internal_id, spec)
    for stage in state.stages:
        state.mark(stage)
    state.save(run)
    run_provenance.write(run, run_provenance.create_document(
        spec, analyzer_root=tmp_path, language="en"))
    # The fixture represents non-existent historic repositories. Production
    # staleness is tested below; this keeps the fixture focused on artifact
    # projection instead of a temporary Git checkout.
    monkeypatch.setattr(RunState, "staleness", lambda _self: [])
    project = OverviewScopeProvider(run)._snapshot(spec, identities)
    return run, project


def test_overview_provider_projects_exact_scope_from_structured_artifacts(tmp_path, monkeypatch):
    run, project = _overview_run(tmp_path, monkeypatch)
    provider = OverviewScopeProvider(run)
    scope = resolve_scope(provider, ModuleScopeRequest(
        "overview", project, Selector("sample", "alias")))

    assert scope.module.module_id == "sample-capability"
    assert scope.source_mode == "overview"
    assert scope.overview_lineage and scope.overview_lineage.source_run_id == "scope-fixture"
    assert scope.assigned_candidates
    assert scope.owned_scope
    assert {name for name, _coverage in scope.coverage.capabilities} >= {
        "discovery", "system-model"}
    assert all(".md" not in ref for location in scope.owned_scope
               for ref in location.evidence_refs)
    wrong_project = ProjectSnapshot("sample-project", (
        RepositorySnapshot("api", "c" * 40, "git"),
        RepositorySnapshot("web", "b" * 40, "git"),
    ))
    with pytest.raises(ValueError) as mismatch:
        provider.resolve(ModuleScopeRequest(
            "overview", wrong_project, Selector("sample", "alias")))
    assert mismatch.value.code == "project-snapshot-mismatch"


def test_overview_provider_rejects_ambiguous_stale_and_missing_coverage(tmp_path, monkeypatch):
    run, project = _overview_run(tmp_path, monkeypatch)
    mapping = json.loads((run / "module-map.json").read_text("utf-8"))
    duplicate = dict(mapping["modules"][0])
    duplicate["module_id"] = "sample-other"
    duplicate["aliases"] = ["sample"]
    mapping["modules"].append(duplicate)
    # Reassign one candidate so the module map remains internally valid.
    mapping["candidate_dispositions"][-1]["module_ids"] = ["sample-other"]
    (run / "module-map.json").write_text(json.dumps(mapping), "utf-8")
    with pytest.raises(ValueError) as ambiguous:
        OverviewScopeProvider(run).resolve(ModuleScopeRequest(
            "overview", project, Selector("sample", "alias")))
    assert ambiguous.value.code == "ambiguous-module"

    # Restore a valid map then exercise freshness and required coverage.
    mapping["modules"].pop()
    mapping["candidate_dispositions"][-1]["module_ids"] = ["sample-capability"]
    (run / "module-map.json").write_text(json.dumps(mapping), "utf-8")
    monkeypatch.setattr(RunState, "staleness", lambda _self: ["api moved"])
    with pytest.raises(ValueError) as stale:
        OverviewScopeProvider(run).resolve(ModuleScopeRequest(
            "overview", project, Selector("sample-capability", "name")))
    assert stale.value.code == "stale-overview"
    monkeypatch.setattr(RunState, "staleness", lambda _self: [])
    (run / "capabilities.json").unlink()
    with pytest.raises(ValueError) as missing:
        OverviewScopeProvider(run).resolve(ModuleScopeRequest(
            "overview", project, Selector("sample-capability", "name")))
    assert missing.value.code == "missing-artifact"


def test_overview_provider_can_follow_only_an_accepted_current_pointer(tmp_path, monkeypatch):
    run, project = _overview_run(tmp_path, monkeypatch)
    root = tmp_path / "skill"
    project_key = "sample-project"
    destination = root / "output" / project_key / "overview" / "scope-fixture"
    destination.parent.mkdir(parents=True)
    run.rename(destination)
    Pointers(root / "state" / project_key)._write({
        "latest_completed": "scope-fixture", "current": "scope-fixture"})
    scope = OverviewScopeProvider.from_current(root, project_key).resolve(
        ModuleScopeRequest("overview", project, Selector("sample-capability", "name")))
    assert scope.module.name == "Sample capability"
    with pytest.raises(ValueError) as missing_current:
        OverviewScopeProvider.from_current(root, "missing")
    assert missing_current.value.code == "no-current-overview"
    with pytest.raises(ValueError) as unsafe_key:
        OverviewScopeProvider.from_current(root, "../outside")
    assert unsafe_key.value.code == "invalid-project-key"


@pytest.mark.parametrize("value", ["../escape", "two/parts", "two\\parts", ".", "name."])
def test_layout_rejects_non_portable_segments(tmp_path, value):
    with pytest.raises(ValueError, match="path segment"):
        ModuleRunLayout(tmp_path, "project", "billing", value)
