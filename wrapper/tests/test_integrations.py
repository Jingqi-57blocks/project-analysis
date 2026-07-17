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


def test_host_fragment_filter_drops_property_paths_and_events():
    # 57B-41: dotted string literals that share a host's shape but are member
    # access / library fragments. Domain-neutral stand-ins for the WCP finds.
    assert not integrations._is_host_fragment("pj.id")        # member access (.id ccTLD)
    assert not integrations._is_host_fragment("wl.id")        # member access
    assert not integrations._is_host_fragment("widget.name")  # property path (.name gTLD-ish)
    assert not integrations._is_host_fragment("gadget.url")   # property path (.url not a TLD)
    assert not integrations._is_host_fragment("item.to")      # property path (.to ccTLD)
    assert not integrations._is_host_fragment("mouseleave.bs.carousel")  # library event
    assert not integrations._is_host_fragment("x.bs.y")               # library event ns
    assert not integrations._is_host_fragment("container.noop")       # template lookup
    # The TLD gate must NOT overreach onto genuine scheme-less hosts, including
    # deeper hosts under a property-ish ccTLD (the 2-label guard fires ONLY at
    # two labels, so a subdomain keeps a real `.id` / `.it` host).
    assert integrations._is_host_fragment("openapi.vendor.cn")
    assert integrations._is_host_fragment("dev-auth.vendor.com")
    assert integrations._is_host_fragment("api.vendor.id")    # 3 labels -> real host
    assert integrations._is_host_fragment("svc.example.it")   # 3 labels -> real host


def test_unavailable_astgrep_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(astgrep, "binary", lambda: None)
    astgrep._reset_probe_cache()
    ev = integrations.generate(str(tmp_path), "widget")
    assert not ev.available and not ev.host_fragments and not ev.integration_packages
    assert any("SKIPPED" in n for n in ev.notes)
    d = ev.to_dict()                                    # version recorded as unavailable
    assert d["tool_version"] == "(not installed)" and d["version_drift"] == ""


def test_signal_records_astgrep_version_and_path(monkeypatch, tmp_path):
    # The scan()-derived signal carries the version/path/drift of the ast-grep
    # that produced it, using the executor path's field names (57B-37).
    fake = astgrep.Probe(version="ast-grep 0.44.1", path="/opt/x/ast-grep")
    monkeypatch.setattr(astgrep, "binary", lambda: fake.path)
    monkeypatch.setattr(astgrep, "probe", lambda **k: fake)
    monkeypatch.setattr(astgrep, "scan", lambda *a, **k: [])
    d = integrations.generate(str(tmp_path), "widget").to_dict()
    assert d["tool"] == "ast-grep"
    assert d["tool_version"] == "ast-grep 0.44.1" and d["tool_path"] == "/opt/x/ast-grep"
    assert d["version_drift"] == ""


def test_version_drift_flows_into_signal_entry(monkeypatch, tmp_path):
    drifted = astgrep.Probe(version="ast-grep 9.9.9", path="/opt/x/ast-grep")
    monkeypatch.setattr(astgrep, "binary", lambda: drifted.path)
    monkeypatch.setattr(astgrep, "probe", lambda **k: drifted)
    monkeypatch.setattr(astgrep, "scan", lambda *a, **k: [])
    d = integrations.generate(str(tmp_path), "widget").to_dict()
    assert d["version_drift"] == "validated 0.44.1, found ast-grep 9.9.9"


def test_evidence_cap_is_disclosed(monkeypatch, tmp_path):
    # Six distinct sites for one host exceed the 5-site evidence cap; the drop of
    # the 6th must be disclosed as a COVERAGE CAP note (57B-31).
    from types import SimpleNamespace
    hosts = [SimpleNamespace(file=f"src/f{i}.go", line=1, text='"api.acme.io"',
                             rule_id="integration-host", vars={}) for i in range(6)]
    fake = astgrep.Probe(version="ast-grep 0.44.1", path="/opt/x/ast-grep")
    monkeypatch.setattr(astgrep, "binary", lambda: fake.path)
    monkeypatch.setattr(astgrep, "available", lambda: True)
    monkeypatch.setattr(astgrep, "probe", lambda **k: fake)
    monkeypatch.setattr(astgrep, "scan",
                        lambda repo, rules: hosts
                        if str(rules[0]).endswith("integration-host.yml") else [])
    ev = integrations.generate(str(tmp_path), "widget")
    assert any("COVERAGE CAP" in n and "5 sites" in n for n in ev.notes)
    assert len(ev.host_fragments[0]["evidence"]) == 5      # capped, not dropped-silent


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


@pytest.mark.skipif(not astgrep.available(), reason="ast-grep not installed")
def test_property_paths_and_events_are_not_host_candidates(tmp_path):
    # 57B-41 end-to-end: the permissive rule matches these dotted string literals,
    # but the filter must keep them out of the reported host fragments while the
    # genuine scheme-less host survives.
    src = tmp_path / "src"
    src.mkdir()
    (src / "widget.js").write_text(
        'const host = "api.gadget.io";\n'          # genuine host -> candidate
        'const path = _.get(o, "avatar.url");\n'    # property path
        'const key = data["pj.id"];\n'              # member-access-shaped literal
        'el.on("mouseleave.bs.carousel", fn);\n')   # library event namespace
    values = {h["value"] for h in integrations.generate(str(tmp_path), "w").host_fragments}
    assert "api.gadget.io" in values
    assert values.isdisjoint({"avatar.url", "pj.id", "mouseleave.bs.carousel"})
