"""57B-88: stable internal IDs and human-readable names are separate."""

import json
from pathlib import Path

import pytest

from analysis_wrapper import identity
from analysis_wrapper.discovery import emit
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id


def _target(path: Path) -> RepoTarget:
    path.mkdir(parents=True, exist_ok=True)
    return RepoTarget(repo_id=stable_repo_id(str(path)), path=str(path.resolve()))


def test_unique_repositories_keep_real_names(tmp_path):
    workspace = tmp_path / "WCP"
    service = _target(workspace / "wcp-service")
    ui = _target(workspace / "wcp-ui")

    mapping = identity.build(
        TargetSpec([service, ui]), workspace_root=workspace,
        project_id=stable_repo_id(str(workspace)),
    )

    assert mapping.project.display_name == "WCP"
    assert mapping.project.reference == "WCP"
    assert mapping.repository(service.repo_id).display_name == "wcp-service"
    assert mapping.repository(service.repo_id).reference == "wcp-service"
    assert mapping.repository(ui.repo_id).reference == "wcp-ui"


def test_duplicate_basenames_use_shortest_unique_workspace_suffix(tmp_path):
    workspace = tmp_path / "workspace"
    app_api = _target(workspace / "apps" / "api")
    service_api = _target(workspace / "services" / "api")
    web = _target(workspace / "web")

    mapping = identity.build(
        TargetSpec([service_api, web, app_api]), workspace_root=workspace,
        project_id="workspace-internal",
    )

    assert mapping.repository(app_api.repo_id).display_name == "api"
    assert mapping.repository(app_api.repo_id).reference == "apps/api"
    assert mapping.repository(service_api.repo_id).reference == "services/api"
    assert mapping.repository(web.repo_id).reference == "web"
    assert mapping.repository(app_api.repo_id).artifact_key == "apps%2Fapi"


def test_names_preserve_unicode_spaces_case_and_hex_looking_suffix(tmp_path):
    workspace = tmp_path / "项目 Alpha"
    names = ["API", "api", "服务", "worker-deadbeef"]
    targets = [_target(workspace / name) for name in names]

    mapping = identity.build(
        TargetSpec(targets), workspace_root=workspace, project_id="internal-project",
    )

    assert mapping.project.display_name == "项目 Alpha"
    assert {item.reference for item in mapping.repositories} == set(names)
    assert mapping.repository(targets[-1].repo_id).reference == "worker-deadbeef"
    api_keys = {
        mapping.repository(target.repo_id).artifact_key
        for target in targets[:2]
    }
    assert len({key.casefold() for key in api_keys}) == 2
    assert {identity.decode_artifact_key(key) for key in api_keys} == {"API", "api"}


def test_artifact_key_is_reversible_style_and_portable():
    assert identity.artifact_key("apps/api") == "apps%2Fapi"
    assert identity.artifact_key("50% off") == "50%25 off"
    assert identity.artifact_key("服务 API") == "服务 API"
    assert identity.artifact_key("CON") == "%43ON"
    assert identity.artifact_key("name.") == "name%2E"
    for value in ("apps/api", "50% off", "服务 API", "CON", "name."):
        assert identity.decode_artifact_key(identity.artifact_key(value)) == value
    with pytest.raises(ValueError, match="malformed"):
        identity.decode_artifact_key("bad%2")


def test_root_project_target_keeps_exact_workspace_scope(tmp_path):
    workspace = tmp_path / "single-app"
    target = _target(workspace)
    mapping = identity.build(
        TargetSpec([target]), workspace_root=workspace, project_id="internal-project",
    )
    repo = mapping.repository(target.repo_id)
    assert repo.display_name == "single-app"
    assert repo.reference == "single-app"
    assert repo.workspace_relative_path == "."


def test_out_of_scope_repository_is_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = _target(tmp_path / "outside")
    with pytest.raises(ValueError, match="outside workspace scope"):
        identity.build(
            TargetSpec([outside]), workspace_root=workspace, project_id="project",
        )


def test_mapping_rejects_tampering_and_target_mismatch(tmp_path):
    workspace = tmp_path / "workspace"
    target = _target(workspace / "api")
    spec = TargetSpec([target])
    mapping = identity.build(spec, workspace_root=workspace, project_id="project")
    document = mapping.to_dict()
    document["repositories"][0]["artifact_key"] = "forged"
    with pytest.raises(ValueError, match="artifact key"):
        identity.from_dict(document)

    other = _target(workspace / "other")
    with pytest.raises(ValueError, match="repository set differs"):
        identity.validate_against(mapping, TargetSpec([other]))


def test_native_load_recomputes_and_rejects_readable_name_tampering(tmp_path):
    workspace = tmp_path / "workspace"
    target = _target(workspace / "api")
    run = tmp_path / "run"
    report = {
        "project_id": "project-internal",
        "workspace_root": str(workspace.resolve()),
        "repos": [],
    }
    emit.write_stage1(run, TargetSpec([target]), report)
    document = json.loads((run / identity.FILENAME).read_text())
    document["project"]["display_name"] = "forged"
    (run / identity.FILENAME).write_text(json.dumps(document))

    with pytest.raises(ValueError, match="differs from deterministic"):
        identity.load(run)


def test_stage1_writes_native_mapping(tmp_path):
    workspace = tmp_path / "workspace"
    target = _target(workspace / "api")
    run = tmp_path / "run"
    report = {
        "project_id": "project-internal",
        "workspace_root": str(workspace.resolve()),
        "repos": [],
    }

    emit.write_stage1(run, TargetSpec([target]), report)

    mapping = identity.load(run)
    assert mapping.source == "native"
    assert mapping.project.display_name == "workspace"
    assert (run / identity.FILENAME).is_file()


def test_stage1_retry_refuses_without_mixing_checkpoint_files(tmp_path):
    workspace = tmp_path / "workspace"
    target = _target(workspace / "api")
    run = tmp_path / "run"
    report = {
        "project_id": "project-internal",
        "workspace_root": str(workspace.resolve()),
        "repos": [],
    }
    emit.write_stage1(run, TargetSpec([target]), report)
    before = {
        name: (run / name).read_bytes()
        for name in ("targets.json", "discovery-report.json", identity.FILENAME)
    }
    changed = {**report, "project_id": "different"}

    with pytest.raises(ValueError, match="already exists"):
        emit.write_stage1(run, TargetSpec([target]), changed)

    assert before == {name: (run / name).read_bytes() for name in before}


def test_legacy_mapping_is_derived_read_only(tmp_path):
    workspace = tmp_path / "legacy-project"
    target = _target(workspace / "api")
    run = tmp_path / "legacy-run"
    run.mkdir()
    TargetSpec([target]).save(run / "targets.json")
    (run / "discovery-report.json").write_text(json.dumps({
        "project_id": "legacy-project-deadbeef",
        "workspace_root": str(workspace.resolve()),
    }))
    (run / "run-state.json").write_text(json.dumps({
        "project_id": "legacy-project-deadbeef",
    }))

    mapping = identity.load(run)

    assert mapping.source == "legacy-derived"
    assert mapping.project.display_name == "legacy-project"
    assert mapping.repository(target.repo_id).reference == "api"
    assert not (run / identity.FILENAME).exists()


def test_invalid_shape_and_schema_version_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="unsupported shape"):
        identity.from_dict({"schema_version": 1})

    workspace = tmp_path / "workspace"
    target = _target(workspace / "api")
    document = identity.build(
        TargetSpec([target]), workspace_root=workspace, project_id="project",
    ).to_dict()
    document["schema_version"] = 2
    with pytest.raises(ValueError, match="schema version"):
        identity.from_dict(document)
