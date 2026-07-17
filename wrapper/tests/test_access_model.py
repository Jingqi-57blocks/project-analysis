"""Access-model locate+count signal view (item 12), domain-neutral fixtures."""

import pytest

from analysis_wrapper import astgrep
from analysis_wrapper.discovery import access_model

FIX = astgrep.RULES_DIR / "fixtures" / "access"


def test_fail_closed_without_astgrep(monkeypatch):
    monkeypatch.setattr(astgrep, "binary", lambda: None)
    am = access_model.generate(str(FIX), "acc")
    assert not am.available and any("SKIPPED" in n for n in am.notes)


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
