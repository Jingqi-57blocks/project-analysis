"""Access-model locate+count signal view (item 12), domain-neutral fixtures."""

import pytest

from analysis_wrapper import astgrep
from analysis_wrapper.discovery import access_model

FIX = astgrep.RULES_DIR / "fixtures" / "access"


def test_fail_closed_without_astgrep(monkeypatch):
    monkeypatch.setattr(astgrep, "binary", lambda: None)
    astgrep._reset_probe_cache()
    am = access_model.generate(str(FIX), "acc")
    assert not am.available and any("SKIPPED" in n for n in am.notes)
    d = am.to_dict()                                    # version recorded as unavailable
    assert d["tool_version"] == "(not installed)" and d["version_drift"] == ""


def test_signal_records_astgrep_version_and_drift(monkeypatch):
    # version/path on a clean run, and drift disclosed on a mismatch — both flow
    # onto the scan()-derived signal entry with the executor path's field names.
    ok = astgrep.Probe(version="ast-grep 0.44.1", path="/opt/x/ast-grep")
    monkeypatch.setattr(astgrep, "binary", lambda: ok.path)
    monkeypatch.setattr(astgrep, "scan", lambda *a, **k: [])
    monkeypatch.setattr(astgrep, "probe", lambda **k: ok)
    d = access_model.generate(str(FIX), "acc").to_dict()
    assert d["tool"] == "ast-grep" and d["tool_version"] == "ast-grep 0.44.1"
    assert d["tool_path"] == "/opt/x/ast-grep" and d["version_drift"] == ""

    drifted = astgrep.Probe(version="ast-grep 9.9.9", path="/opt/x/ast-grep")
    monkeypatch.setattr(astgrep, "probe", lambda **k: drifted)
    d2 = access_model.generate(str(FIX), "acc").to_dict()
    assert d2["version_drift"] == "validated 0.44.1, found ast-grep 9.9.9"


def test_capped_samples_are_independent_of_astgrep_result_order(monkeypatch, tmp_path):
    probe = astgrep.Probe(version="ast-grep 0.44.1", path="/opt/x/ast-grep")
    matches = [
        astgrep.Match(rule_id="authz-check-ts", file=f"src/p{i:02d}.ts",
                      line=i + 1, text=f"canUse({i})")
        for i in range(12)
    ] + [
        astgrep.Match(rule_id="role-enum-ts", file="src/roles.ts", line=4,
                      text="enum ProjectRole { Member }")
    ]
    current = list(matches)
    monkeypatch.setattr(astgrep, "available", lambda: True)
    monkeypatch.setattr(astgrep, "probe", lambda **_k: probe)
    monkeypatch.setattr(astgrep, "scan", lambda *_a, **_k: list(current))

    forward = access_model.generate(tmp_path, "sample").to_dict()
    current[:] = reversed(current)
    reversed_result = access_model.generate(tmp_path, "sample").to_dict()

    assert forward == reversed_result
    assert forward["authz_checks"]["count"] == 12
    assert len(forward["authz_checks"]["sample"]) == 8


@pytest.mark.skipif(not astgrep.available(), reason="ast-grep not installed")
def test_locates_all_access_categories():
    d = access_model.generate(str(FIX), "acc-fix").to_dict()
    assert "WidgetRole" in d["role_catalog_names"]          # go type + ts enum
    assert d["authz_checks"]["count"] >= 2                  # hasPermission + CheckPermission
    assert d["middleware"]["count"] >= 1                    # r.Use(mw)
    assert d["route_guards"]["count"] >= 1                  # <AuthGuard>
    assert d["contextual_identity"]["count"] >= 1           # widget.OwnerID == userID
    kinds = {p["kind"] for p in d["policy_artifacts"]}
    assert "casbin-model" in kinds and "casbin-policy" in kinds
    # sample locations are cited; counts are never interpreted
    assert all(":" in s for s in d["authz_checks"]["sample"])
