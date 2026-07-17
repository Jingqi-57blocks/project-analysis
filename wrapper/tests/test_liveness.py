"""Route liveness join — matching, classification, honest no-caller status."""

from analysis_wrapper import astgrep
from analysis_wrapper.discovery import liveness
from analysis_wrapper.discovery.liveness import _matches, _norm_segments


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


def test_file_cap_hit_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(liveness, "_MAX_FILES", 1)
    fe = tmp_path / "ui"
    _write(fe / "src" / "a.ts", "get(`${api}/x/1`);\n")
    _write(fe / "src" / "b.ts", "get(`${api}/y/2`);\n")
    report = liveness.liveness(str(fe), [])
    assert any("COVERAGE CAP" in n and "source scan stopped" in n
               for n in report.notes)


def test_oversized_file_cap_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(liveness, "_MAX_BYTES", 10)
    fe = tmp_path / "ui"
    _write(fe / "src" / "a.ts", "get(`${api}/x/1`);\n")   # >10 bytes -> skipped
    report = liveness.liveness(str(fe), [])
    assert any("COVERAGE CAP" in n for n in report.notes)


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


def test_astgrep_fallback_note_disclosed(tmp_path, monkeypatch):
    be = tmp_path / "svc"
    _write(be / "main.go", 'package main\nfunc r(){ e.GET("/v2/thing", h) }\n')
    monkeypatch.setattr("analysis_wrapper.astgrep.binary", lambda: None)
    astgrep._reset_probe_cache()
    report = liveness.liveness(None, [("svc-1", str(be), [])])
    assert any("ROUTE EXTRACTION FALLBACK" in n for n in report.notes)
    assert any(r.path == "/v2/thing" for r in report.rows)  # regex rows still flow
    # Fallback disclosure is kept AND the version is recorded as unavailable.
    assert report.astgrep["tool_version"] == "(not installed)"
    assert report.astgrep["version_drift"] == ""


def test_no_fallback_note_when_astgrep_present():
    if not astgrep.available():
        return  # environment without ast-grep: covered by the fallback test above
    report = liveness.liveness(None, [])
    assert not any("FALLBACK" in n for n in report.notes)


def test_liveness_report_records_astgrep_version():
    if not astgrep.available():
        return  # unavailable case is covered by the fallback test above
    report = liveness.liveness(None, [])
    p = astgrep.probe()
    assert report.astgrep["tool"] == "ast-grep"
    assert report.astgrep["tool_version"] == p.version
    assert report.astgrep["tool_path"] == p.path
