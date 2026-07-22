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
    # Each base makes >=2 distinct calls that land on this backend, clearing the
    # base-resolution evidence floor so the base is credited to it (a single
    # coincidental path match is deliberately too thin — see the base-resolution
    # test below).
    _write(fe / "src" / "api.ts",
           "get(`${mainApi}/leaves/123`);\n"
           "get(`${mainApi}/leaves/456`);\n"
           "post(`${appRunnerApi}/v2/billing`);\n"
           "post(`${appRunnerApi}/v2/billing/9`);\n")
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
    assert ledger["appRunnerApi"] == ["billing", "billing/9"]


def test_ui_called_credited_only_to_the_base_correct_backend(tmp_path):
    # Two backends registering the SAME leaf route (`/leaves/:id`). The frontend
    # calls it via `apiA` only; `apiA`'s other calls prove it binds to svc-a.
    # svc-b must NOT be credited for `/leaves` on path shape alone (57B-15).
    fe = tmp_path / "ui"
    _write(fe / "src" / "a.ts",
           "get(`${config.apiA}/a-only/1`);\n"
           "get(`${config.apiA}/a-two/2`);\n"
           "get(`${config.apiA}/leaves/9`);\n")
    _write(fe / "src" / "b.ts",
           "const b = config.apiB;\n"          # LOCAL alias: ${b} binds to apiB
           "get(`${b}/b-only/1`);\n"
           "get(`${b}/b-two/2`);\n")
    svc_a = tmp_path / "svca"
    _write(svc_a / "app.js",
           "router.get('/a-only/:id', h);\n"
           "router.get('/a-two/:id', h);\n"
           "router.get('/leaves/:id', h);\n")
    svc_b = tmp_path / "svcb"
    _write(svc_b / "app.js",
           "router.get('/b-only/:id', h);\n"
           "router.get('/b-two/:id', h);\n"
           "router.get('/leaves/:id', h);\n")
    report = liveness.liveness(
        fe, [("svc-a", str(svc_a), []), ("svc-b", str(svc_b), [])])
    rows = {(r.repo_id, r.path): r for r in report.rows}
    # svc-a owns the caller's base -> its /leaves is ui-called.
    assert rows[("svc-a", "/leaves/:id")].status == "ui-called"
    assert rows[("svc-a", "/leaves/:id")].caller_evidence
    # svc-b matches the SAME path shape but the caller binds to svc-a -> NOT
    # ui-called (the false-positive the fix removes), disclosed as base-unresolved.
    assert rows[("svc-b", "/leaves/:id")].status == "base-unresolved"
    assert rows[("svc-b", "/leaves/:id")].caller_evidence == []
    # Each base's own distinctive routes are credited to it.
    assert rows[("svc-a", "/a-only/:id")].status == "ui-called"
    assert rows[("svc-b", "/b-only/:id")].status == "ui-called"


def test_local_alias_binds_to_the_real_config_base(tmp_path):
    # `const mainApi = config.reviewApi` and a destructure-rename both make a bare
    # `${mainApi}` resolve to its underlying base, so calls do NOT inflate the
    # global `mainApi` ledger (57B-15 performanceReviewApi/clientApi defect).
    fe = tmp_path / "ui"
    _write(fe / "src" / "review.ts",
           "const mainApi = config.reviewApi;\n"
           "get(`${mainApi}/review/1`);\n"
           "get(`${mainApi}/review/2`);\n")
    _write(fe / "src" / "client.ts",
           "const { clientApi: mainApi } = config;\n"
           "get(`${mainApi}/client/1`);\n")
    report = liveness.liveness(fe, [])
    ledger = report.calls_by_base()
    # The bare `${mainApi}` calls are attributed to their real bases, not to a
    # global `mainApi` that never appears here.
    assert "mainApi" not in ledger
    assert ledger["reviewApi"] == ["review/1", "review/2"]
    assert ledger["clientApi"] == ["client/1"]


def test_explicit_config_prefix_bypasses_a_local_alias(tmp_path):
    # A file may rebind a name locally AND still use the global base explicitly;
    # `${config.mainApi}` is always the global base, never the local alias.
    fe = tmp_path / "ui"
    _write(fe / "src" / "mixed.ts",
           "const mainApi = config.otherApi;\n"
           "get(`${mainApi}/aliased/1`);\n"
           "get(`${config.mainApi}/global/1`);\n")
    calls = liveness.ui_call_sites(fe)
    by_path = {c.path: c.base for c in calls}
    assert by_path["/aliased/1"] == "otherApi"
    assert by_path["/global/1"] == "mainApi"


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


def test_mounts_are_separate_from_leaf_endpoints(tmp_path):
    backend = tmp_path / "svc"
    _write(backend / "app.js",
           "app.use('/api', router); router.get('/items', h);\n")
    leaf = liveness.route_registrations(backend)
    complete = liveness.route_registrations(backend, include_mounts=True)
    assert {(row.method, row.path) for row in leaf} == {("GET", "/items")}
    assert {(row.method, row.path) for row in complete} == {
        ("USE", "/api"), ("GET", "/items")}


def test_ui_linkage_requires_compatible_http_method(tmp_path):
    frontend = tmp_path / "web"
    _write(frontend / "src" / "api.ts",
           "get(`${api}/items`); get(`${api}/health`);\n")
    backend = tmp_path / "svc"
    _write(backend / "app.js",
           "router.get('/items', h); router.post('/items', h); "
           "router.get('/health', h);\n")
    report = liveness.liveness(frontend, [("svc", str(backend), [])])
    rows = {(row.method, row.path): row for row in report.rows}
    assert rows[("GET", "/items")].status == "ui-called"
    assert rows[("POST", "/items")].status == "method-unresolved"


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
