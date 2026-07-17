"""Deployable-unit signal view (item 13) — locate + parse-as-data, domain-neutral."""

from analysis_wrapper.discovery import deploy_units


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_locates_units_and_parses_compose_services(tmp_path):
    repo = tmp_path / "widget-svc"
    _write(repo / "build" / "Dockerfile", "FROM scratch\n")
    _write(repo / "deploy" / "docker-compose.prod.yml",
           "services:\n  api:\n    build: .\n  cache:\n    image: redis:7\n")
    _write(repo / "main.go", "package main\nfunc main() {}\n")
    _write(repo / "internal" / "common" / "util.go", "package common\n")  # library, no marker

    result = deploy_units.generate(str(repo))
    assert result.status == "inferred"
    kinds = {(u["kind"], u["name"]) for u in result.units}
    assert ("container-image", ".") in kinds
    assert ("go-main-binary", ".") in kinds
    services = {u["name"]: u for u in result.units if u["kind"] == "compose-service"}
    assert services["api"]["built_here"] is True
    assert services["cache"]["image"] == "redis:7" and services["cache"]["built_here"] is False
    # a plain library dir is never a deployable unit
    assert not any("common" in u["name"] for u in result.units)


def test_repo_without_artifacts_is_unknown_not_empty(tmp_path):
    repo = tmp_path / "lib-only"
    _write(repo / "src" / "index.js", "export const x = 1;\n")
    result = deploy_units.generate(str(repo))
    assert result.status == "unknown"
    assert result.units == [] and result.artifacts == []
    assert any("unknown" in n for n in result.notes)
