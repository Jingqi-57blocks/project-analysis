"""fetch-selections tests (57B-113 / 57B-116, M2, Part B): bounded context
window, sanitize-on-fetch, path/revision safety mirroring findings.py's
_safe_relative and validators.validate_citations, .env exclusion, the
per-run selection cap, the total-byte budget, and fail-closed-per-selection
(never a raised exception, never a silently dropped row)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper import identity
from analysis_wrapper.orchestrator import planner, selection
from analysis_wrapper.orchestrator.composer import compose
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine, now_iso
from analysis_wrapper.targetspec import TargetSpec, stable_repo_id

CLEAN_HEAD = "a" * 40


def _build_run(tmp_path) -> Path:
    """A minimal prepared run: identity + targets.json, with a REAL
    multi-line source file on disk for api (clean, git) and a non-git web
    repo -- selection.fetch reads actual file content, revision-checked."""
    workspace = tmp_path / "ws"
    api_root = workspace / "api"
    web_root = workspace / "web"
    (api_root / "internal").mkdir(parents=True)
    lines = [f"line {i}: some source content here" for i in range(1, 121)]
    (api_root / "internal" / "service.go").write_text("\n".join(lines) + "\n", "utf-8")
    (api_root / ".env").write_text("DB_PASSWORD=hunter2\n", "utf-8")
    web_root.mkdir(parents=True)
    (web_root / "index.js").write_text("console.log('hi');\n", "utf-8")

    targets = {
        "schema_version": "2.0.0",
        "repos": [
            {"repo_id": "api-11111111", "path": str(api_root),
             "git": {"head": CLEAN_HEAD, "branch": "main", "commit_count": 1}},
            {"repo_id": "web-22222222", "path": str(web_root),
             "git": {"head": "", "branch": "", "commit_count": 0}},
        ],
    }
    run = tmp_path / "run"
    run.mkdir()
    (run / "targets.json").write_text(json.dumps(targets), "utf-8")
    spec = TargetSpec.from_dict(targets)
    project_id = stable_repo_id(str(workspace))
    mapping = identity.build(spec, workspace_root=workspace, project_id=project_id)
    identity.write_mapping(run, mapping)
    (run / "discovery-report.json").write_text(
        json.dumps({"project_ref": mapping.project.reference}), "utf-8")
    return run


def _register_and_validate_select(run: Path, task_id: str, selections: list[dict]) -> None:
    engine = Engine(run)
    packets = compose(
        task_id=task_id, template_id="t", template_version="1", task_type="selection-fetch",
        instructions="request", inputs={"a": "x"},
        output_schema_id="selection-fetch.v1", context_budget_tokens=8000)
    engine.create_tasks(packets)
    claimed = engine.claim(1, executor_kind="manual", model="test")
    assert claimed and claimed[0].packet.task_id == task_id
    item = claimed[0]
    at = now_iso()
    result = TaskResult(
        task_id=task_id, status="ok", output={"selections": selections},
        executor=ExecutorInfo(kind="manual", model="test", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.1),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()),
        attempt=item.attempt)
    outcome = engine.submit(task_id, result.to_dict())
    assert outcome["status"] == "validated", outcome


def _selection(selection_id, ref, *, purpose="verify a fact"):
    return {"selection_id": selection_id, "purpose": purpose, "ref": ref, "quoted_text": ""}


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #

def test_fetch_returns_bounded_context_around_the_cited_line(tmp_path):
    run = _build_run(tmp_path)
    ref = f"api@{CLEAN_HEAD}:internal/service.go:60"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    out_path = selection.fetch(run, "lens-x-select")
    rows = json.loads(out_path.read_text("utf-8"))
    assert len(rows) == 1
    row = rows[0]
    assert row == {"selection_id": "s1", "purpose": "verify a fact", "ref": ref,
                  "excerpt": row["excerpt"]}
    assert "line 60:" in row["excerpt"]
    assert "line 20:" in row["excerpt"]   # 60 - 40 = line 20
    assert "line 100:" in row["excerpt"]  # 60 + 40 = line 100
    assert "line 19:" not in row["excerpt"]
    assert "line 101:" not in row["excerpt"]


def test_fetch_writes_to_the_canonical_path_by_default(tmp_path):
    run = _build_run(tmp_path)
    ref = f"api@{CLEAN_HEAD}:internal/service.go:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    out_path = selection.fetch(run, "lens-x-select")
    assert out_path == planner.fetch_selections_output_path(run, "lens-x-select")


def test_fetch_honors_an_out_override(tmp_path):
    run = _build_run(tmp_path)
    ref = f"api@{CLEAN_HEAD}:internal/service.go:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    custom = tmp_path / "custom" / "out.json"
    custom.parent.mkdir()
    out_path = selection.fetch(run, "lens-x-select", out=custom)
    assert out_path == custom
    assert custom.is_file()


def test_fetch_raises_when_no_validated_selection_fetch_task(tmp_path):
    run = _build_run(tmp_path)
    with pytest.raises(selection.SelectionFetchError, match="no validated selection-fetch"):
        selection.fetch(run, "lens-x-select")


# --------------------------------------------------------------------------- #
# sanitize on fetch
# --------------------------------------------------------------------------- #

def test_fetch_sanitizes_the_excerpt(tmp_path):
    run = _build_run(tmp_path)
    api_root = Path(json.loads((run / "targets.json").read_text())["repos"][0]["path"])
    (api_root / "internal" / "secret.go").write_text(
        "package internal\nconst DB_PASSWORD = \"hunter2\"\n", "utf-8")
    ref = f"api@{CLEAN_HEAD}:internal/secret.go:2"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert "hunter2" not in rows[0]["excerpt"]
    assert "REDACTED" in rows[0]["excerpt"]


# --------------------------------------------------------------------------- #
# fail-closed per selection -- every failure mode is a disclosed skip, never
# a raised exception and never a dropped row.
# --------------------------------------------------------------------------- #

def test_fetch_skips_a_non_source_ref_with_a_disclosed_reason(tmp_path):
    run = _build_run(tmp_path)
    _register_and_validate_select(
        run, "lens-x-select", [_selection("s1", "metric:code.analyzed-scope.total")])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert rows[0]["excerpt"].startswith("NOT FETCHED: not a source ref")


def test_fetch_skips_an_unknown_repository_reference(tmp_path):
    run = _build_run(tmp_path)
    ref = f"nope@{CLEAN_HEAD}:internal/service.go:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert rows[0]["excerpt"].startswith("NOT FETCHED: unknown repository reference")


def test_fetch_skips_a_revision_mismatch(tmp_path):
    run = _build_run(tmp_path)
    ref = f"api@{'b' * 40}:internal/service.go:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert rows[0]["excerpt"].startswith("NOT FETCHED: revision mismatch")


def test_fetch_accepts_non_git_marker_for_a_non_git_repo(tmp_path):
    run = _build_run(tmp_path)
    ref = "web@NON-GIT:index.js:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert not rows[0]["excerpt"].startswith("NOT FETCHED")
    assert "console.log" in rows[0]["excerpt"]


def test_fetch_skips_wrong_marker_for_a_non_git_repo(tmp_path):
    run = _build_run(tmp_path)
    ref = f"web@{CLEAN_HEAD}:index.js:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert rows[0]["excerpt"].startswith("NOT FETCHED: revision mismatch")


def test_fetch_skips_an_unsafe_relative_path(tmp_path):
    run = _build_run(tmp_path)
    ref = f"api@{CLEAN_HEAD}:../../etc/passwd:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert rows[0]["excerpt"].startswith("NOT FETCHED: unsafe relative path")


def test_fetch_excludes_env_files_by_name_even_when_otherwise_valid(tmp_path):
    run = _build_run(tmp_path)
    ref = f"api@{CLEAN_HEAD}:.env:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert rows[0]["excerpt"] == "NOT FETCHED: environment file excluded by policy"


def test_fetch_skips_a_missing_file(tmp_path):
    run = _build_run(tmp_path)
    ref = f"api@{CLEAN_HEAD}:internal/nope.go:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert rows[0]["excerpt"].startswith("NOT FETCHED: file missing or outside target")


def test_fetch_skips_an_out_of_range_line(tmp_path):
    run = _build_run(tmp_path)
    ref = f"api@{CLEAN_HEAD}:internal/service.go:9999"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert rows[0]["excerpt"].startswith("NOT FETCHED: cited line out of range")


def test_fetch_never_raises_and_never_drops_a_row_across_mixed_outcomes(tmp_path):
    run = _build_run(tmp_path)
    selections = [
        _selection("good", f"api@{CLEAN_HEAD}:internal/service.go:1"),
        _selection("bad-grammar", "metric:x"),
        _selection("bad-repo", f"nope@{CLEAN_HEAD}:x:1"),
    ]
    _register_and_validate_select(run, "lens-x-select", selections)
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert [row["selection_id"] for row in rows] == ["good", "bad-grammar", "bad-repo"]
    assert not rows[0]["excerpt"].startswith("NOT FETCHED")
    assert rows[1]["excerpt"].startswith("NOT FETCHED")
    assert rows[2]["excerpt"].startswith("NOT FETCHED")


# --------------------------------------------------------------------------- #
# bounds: per-run selection cap, total byte budget, per-line truncation
# --------------------------------------------------------------------------- #

def test_fetch_caps_at_max_selections_disclosing_the_rest(tmp_path):
    run = _build_run(tmp_path)
    selections = [_selection(f"s{i}", f"api@{CLEAN_HEAD}:internal/service.go:1")
                 for i in range(selection.MAX_SELECTIONS + 3)]
    _register_and_validate_select(run, "lens-x-select", selections)
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert len(rows) == selection.MAX_SELECTIONS + 3
    fetched = [row for row in rows if not row["excerpt"].startswith("NOT FETCHED")]
    capped = [row for row in rows if "per-run selection cap" in row["excerpt"]]
    assert len(fetched) == selection.MAX_SELECTIONS
    assert len(capped) == 3


def test_fetch_enforces_the_total_byte_budget(tmp_path, monkeypatch):
    run = _build_run(tmp_path)
    ref = f"api@{CLEAN_HEAD}:internal/service.go:60"
    selections = [_selection("s1", ref), _selection("s2", ref)]
    _register_and_validate_select(run, "lens-x-select", selections)

    # Measure one excerpt's real size first, then set a budget that fits
    # exactly one but not two -- avoids a brittle guessed byte constant.
    one_excerpt_size = len(selection._fetch_source_excerpt(
        ref, TargetSpec.load(run / "targets.json"), identity.load(run)).encode("utf-8"))
    monkeypatch.setattr(selection, "MAX_TOTAL_BYTES", int(one_excerpt_size * 1.5))

    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert not rows[0]["excerpt"].startswith("NOT FETCHED")
    assert rows[1]["excerpt"] == "NOT FETCHED: total fetched-evidence byte budget exceeded"


def test_fetch_truncates_an_oversized_line_per_the_char_cap(tmp_path, monkeypatch):
    run = _build_run(tmp_path)
    monkeypatch.setattr(selection, "MAX_LINE_CHARS", 10)
    api_root = Path(json.loads((run / "targets.json").read_text())["repos"][0]["path"])
    (api_root / "internal" / "long.go").write_text("x" * 500 + "\n", "utf-8")
    ref = f"api@{CLEAN_HEAD}:internal/long.go:1"
    _register_and_validate_select(run, "lens-x-select", [_selection("s1", ref)])
    rows = json.loads(selection.fetch(run, "lens-x-select").read_text("utf-8"))
    assert rows[0]["excerpt"] == "x" * 10
