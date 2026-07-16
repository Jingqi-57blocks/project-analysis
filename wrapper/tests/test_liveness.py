"""Route liveness join — matching, classification, honest no-caller status."""

from doctor_wrapper.discovery import liveness
from doctor_wrapper.discovery.liveness import _matches, _norm_segments


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_norm_drops_version_prefix_and_wildcards_params():
    assert _norm_segments("/v2/leaves/:id") == ["leaves", "*"]
    assert _norm_segments("${appRunnerApi}/v2/leaves/123?x=1") == ["leaves", "123"]


def test_match_is_prefix_with_wildcards():
    assert _matches(["leaves"], ["leaves", "123"])          # router mounts subpath
    assert _matches(["leaves", "*"], ["leaves", "123"])
    assert not _matches(["leaves", "5"], ["leaves"])        # call shorter than route
    assert not _matches(["billing"], ["leaves"])
    assert not _matches(["*"], ["anything"])                # no concrete segment -> no match


def test_liveness_classifies_ui_internal_and_orphan(tmp_path):
    fe = tmp_path / "ui"
    _write(fe / "src" / "api.ts",
           "get(`${mainApi}/leaves/123`);\n"
           "post(`${appRunnerApi}/v2/billing`);\n")
    be = tmp_path / "svc"
    _write(be / "app.js",
           "router.get('/leaves/:id', h);\n"      # ui-called
           "router.post('/billing', h);\n"        # ui-called (version-tolerant)
           "router.get('/internal/reap', h);\n"   # internal-called
           "router.get('/orphan/thing', h);\n")   # no caller
    internal = liveness.ui_call_sites  # reuse extractor for internal caller list
    mcp_calls = [liveness.CallHit("self", "/internal/reap", "app.js:99")]
    report = liveness.liveness(
        fe, [("svc-1", str(be), [])],
        internal_callers={"svc-1": mcp_calls})
    by_path = {r.path: r for r in report.rows}
    assert by_path["/leaves/:id"].status == "ui-called"
    assert by_path["/leaves/:id"].caller_evidence
    assert by_path["/billing"].status == "ui-called"
    assert by_path["/internal/reap"].status == "internal-called"
    assert by_path["/orphan/thing"].status == "no-direct-path-match"
    assert by_path["/orphan/thing"].caller_evidence == []
    assert any("NOT an orphan" in n or "never labeled 'dead'" in n for n in report.notes)
    ledger = report.calls_by_base()
    assert set(ledger) == {"mainApi", "appRunnerApi"}
    assert ledger["appRunnerApi"] == ["billing"]


def test_no_frontend_yields_all_no_caller_but_never_dead(tmp_path):
    be = tmp_path / "svc"
    _write(be / "main.go",
           'package main\nfunc r(){ e.GET("/v2/thing", h); e.POST("/thing/:id", h) }\n')
    report = liveness.liveness(None, [("svc-1", str(be), [])])
    assert {r.status for r in report.rows} <= {"no-direct-path-match", "match-ambiguous"}


def test_tier2_excluded_dir_not_scanned_for_routes(tmp_path):
    be = tmp_path / "svc"
    _write(be / "docs" / "gen.go", 'e.GET("/generated", h)\n')
    _write(be / "main.go", 'e.GET("/real", h)\n')
    routes = liveness.route_registrations(be, tier2_exclusions=["docs"])
    paths = {r.path for r in routes}
    assert "/real" in paths and "/generated" not in paths
