"""Assembled-URL / integration-package discovery (item 5), domain-neutral."""

import pytest

from analysis_wrapper import astgrep
from analysis_wrapper.discovery import integrations


def test_host_fragment_filter_drops_files_and_identifiers():
    assert integrations._is_host_fragment("api.gadget.io")
    assert integrations._is_host_fragment("cdn.widget.co")
    assert not integrations._is_host_fragment("quarterly.xlsx")   # file extension
    assert not integrations._is_host_fragment("Widget.Gadget")    # identifier path
    assert not integrations._is_host_fragment("styles.css")
    assert not integrations._is_host_fragment("localhost")
    assert not integrations._is_host_fragment("svc.internal.local")   # reserved
    assert not integrations._is_host_fragment("api.gadget.example")   # reserved TLD


def test_unavailable_astgrep_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(astgrep, "binary", lambda: None)
    ev = integrations.generate(str(tmp_path), "widget")
    assert not ev.available and not ev.host_fragments and not ev.integration_packages
    assert any("SKIPPED" in n for n in ev.notes)


@pytest.mark.skipif(not astgrep.available(), reason="ast-grep not installed")
def test_integration_evidence_on_synthetic_repo(tmp_path):
    acme = tmp_path / "internal" / "handlers" / "acme"
    acme.mkdir(parents=True)
    (acme / "service.go").write_text(
        'package acme\nconst (\n\tscheme = "https"\n\thost = "api.acme.io"\n)\n')
    (acme / "http.go").write_text(
        'package acme\nimport "net/http"\nfunc call(u string) { http.Get(u) }\n')
    common = tmp_path / "internal" / "handlers" / "common"
    common.mkdir(parents=True)
    (common / "util.go").write_text("package common\nfunc noop() {}\n")

    ev = integrations.generate(str(tmp_path), "widget-svc")
    assert ev.available
    assert "api.acme.io" in {h["value"] for h in ev.host_fragments}
    packages = {p["package"] for p in ev.integration_packages}
    assert "acme" in packages          # distinctively-named dir with HTTP calls
    assert "common" not in packages    # generic name / no HTTP calls
