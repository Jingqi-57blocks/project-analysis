"""Declarative ast-grep rules exercised against domain-neutral fixtures (D5).

Fixtures live under wrapper/rules/fixtures and use only widget/gadget/example
naming — zero WCP vocabulary. Each rule is checked for its positives AND that the
disclosed negatives (mounts, non-route calls, local compute) do NOT match."""

import pytest

from analysis_wrapper import astgrep

FIX = astgrep.RULES_DIR / "fixtures"
pytestmark = pytest.mark.skipif(not astgrep.available(), reason="ast-grep not installed")


def _by_file(rule: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for match in astgrep.scan(FIX, [astgrep.rule_path(rule)]):
        out.setdefault(match.file, []).append(match)
    return out


def test_route_registration_positives_and_negatives():
    by = _by_file("route-registration.yml")
    js_paths = {m.vars.get("P") for m in by.get("routes.js", [])}
    assert js_paths == {"/widgets", "/gadgets/:id"}          # app.use / map excluded
    go_text = " ".join(m.text for m in by.get("routes.go", []))
    assert "/widgets" in go_text and "/gadgets" in go_text
    assert "Group" not in go_text and "Print" not in go_text  # mounts / non-routes


def test_http_call_positives_and_negatives():
    by = _by_file("http-call-site.yml")
    assert len(by.get("http-call.go", [])) == 2
    assert len(by.get("http-call.js", [])) == 2
    assert all("computeLocally" not in m.text
               for ms in by.values() for m in ms)


def test_client_init_positives_and_negatives():
    by = _by_file("client-init.yml")
    assert len(by.get("client-init.go", [])) == 2
    assert len(by.get("client-init.js", [])) == 2
    assert all("computeLocally" not in m.text
               for ms in by.values() for m in ms)


def test_integration_host_rule_matches_domain_shapes():
    by = _by_file("integration-host.yml")
    go_values = {m.text.strip('"') for m in by.get("host.go", [])}
    js_values = {m.text.strip("'\"") for m in by.get("host.js", [])}
    assert "api.gadget.io" in go_values
    assert "cdn.widget.io" in js_values
    # The rule is DELIBERATELY permissive: property-path / member-access / library
    # -event string literals share a host's shape and DO match here (57B-41).
    # Precision is the filter's job (see integrations._is_host_fragment), so these
    # must surface at the rule layer but never as reported host candidates.
    assert {"avatar.url", "pj.id", "mouseleave.bs.carousel"} <= js_values
