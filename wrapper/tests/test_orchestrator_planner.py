"""Judgment-DAG planner tests (57B-113 / 57B-116, M2): DAG shape on a
synthetic prepared run dir (task count, deps, shard fan-out, budget
sharding), the two-phase dedup planning split, and idempotent re-planning."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper import identity
from analysis_wrapper.orchestrator import formation, planner
from analysis_wrapper.orchestrator import schemas
from analysis_wrapper.orchestrator import templates as tpl
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo, TaskResult, TaskTiming, ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import Engine, now_iso
from analysis_wrapper.targetspec import TargetSpec, stable_repo_id

# The nine real lenses split repo/workspace exactly as documented in each
# lens file's own frontmatter (see test_orchestrator_templates.py's
# EXPECTED_SHARD, kept independently here so a change to that split is
# caught from two angles).
REPO_SHARDED_LENSES = {"complexity", "dead-code", "hotspots-change-friction", "safety-net"}
WORKSPACE_SHARDED_LENSES = {
    "structure-inventory", "duplication", "dependencies-cycles",
    "dependency-risk", "open-lens",
}
# 57B-116: source_reads lenses get a select/finalize task PAIR instead of a
# direct lens-findings task (see test_orchestrator_templates.py's
# EXPECTED_SOURCE_READS, kept independently here for the same reason).
SOURCE_READS_LENSES = {"safety-net", "open-lens", "dependencies-cycles", "structure-inventory"}
DIRECT_LENSES = (REPO_SHARDED_LENSES | WORKSPACE_SHARDED_LENSES) - SOURCE_READS_LENSES


def _build_run(tmp_path, *, repo_ids=("api-11111111", "web-22222222"),
              inflate_lizard_lines: int = 0) -> tuple[Path, dict]:
    """A minimal, prepared two-repo run dir: identity + targets.json +
    signals/run-summary.json (with real view files on disk) +
    synthesis-input.json + module-candidates.json -- everything
    plan_judgment/plan_dedup read. Mirrors test_orchestrator_validators.py's
    _build_run fixture (57B-114 M0), extended with the M2 prepared-run
    artifacts."""
    workspace = tmp_path / "ws"
    names = ["api", "web"]
    repo_roots = {}
    for name in names:
        root = workspace / name
        (root / "internal").mkdir(parents=True)
        (root / "internal" / "service.go").write_text(
            f"package internal\nfunc {name.title()}Work() {{}}\n", "utf-8")
        repo_roots[name] = root

    head = "a" * 40
    targets = {
        "schema_version": "3.0.0",
        "repos": [
            {"repo_id": repo_ids[0], "path": str(repo_roots["api"]),
             "git": {"head": head, "branch": "main", "commit_count": 1}},
            {"repo_id": repo_ids[1], "path": str(repo_roots["web"]),
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

    signals = run / "signals"
    signals.mkdir()
    view_rows = []

    def _view(tool, repo_ref, text):
        name = f"{tool}-{repo_ref}.view.txt"
        (signals / name).write_text(text, "utf-8")
        view_rows.append({"tool": tool, "repository_ref": repo_ref, "status": "complete",
                          "reason": "", "view": name, "manifest": f"{name}.manifest.json"})

    lizard_text = "func ApiWork CCN 3\n"
    if inflate_lizard_lines:
        # Still genuinely line-oriented text (composer.compose's own
        # sharding needs that), just large enough to force a shard split at
        # a small context_budget_tokens without touching any other packet.
        lizard_text += "".join(f"func Extra{i} CCN 1\n" for i in range(inflate_lizard_lines))
    _view("lizard", "api", lizard_text)
    _view("scc", "api", "Go 10 100\n")
    _view("git-history", "api", "churn: service.go 5\n")
    # A reduced staticcheck scan is still lens evidence: the limitation must
    # reach both Go quality consumers instead of disappearing as no findings.
    _view("staticcheck", "api", "coverage_limitation: staticcheck-no-package-universe: package pattern matched no packages\n")
    view_rows[-1]["status"] = "partial"
    view_rows[-1]["reason"] = "staticcheck-no-package-universe: package pattern matched no packages"
    _view("scc", "web", "JavaScript 5 50\n")
    _view("git-history", "web", "churn: index.js 2\n")
    # a cross-repo view, attributed to a single "primary" member (api) --
    # mirrors cli.py's own jscpd_multi attribution (members[0]).
    _view("jscpd-cross", "api", "clone: web/a.js <-> api/b.js\n")
    # a signal present but not complete/partial -- must never be pulled in.
    view_rows.append({"tool": "osv-scanner", "repository_ref": "api", "status": "skipped",
                      "reason": "network lane not authorized", "view": "", "manifest": ""})

    (signals / "run-summary.json").write_text(json.dumps({
        "schema_version": "3.0.0", "aggregate_status": "complete", "signals": view_rows,
    }), "utf-8")

    (run / "module-candidates.json").write_text(json.dumps({
        "schema_version": "3.0.0", "project_ref": mapping.project.reference,
        "candidate_count": 2,
        "candidates": [
            {"candidate_id": "mc-api-folder", "repository_ref": "api",
             "signal_kind": "folder", "value": "internal",
             "evidence": ["discovery-report.json:x"], "node_ids": []},
            {"candidate_id": "mc-web-folder", "repository_ref": "web",
             "signal_kind": "folder", "value": "internal",
             "evidence": ["discovery-report.json:y"], "node_ids": []},
        ],
    }), "utf-8")

    (run / "synthesis-input.json").write_text(json.dumps({
        "repositories": {"items": [
            {"repository_ref": "api", "git": {"head": head, "dirty_detail": "no"}},
            {"repository_ref": "web", "git": {"head": "", "dirty_detail": "no"}},
        ]},
        "graph": {"nodes": {"route": {"items": [
            {"repository_ref": "api", "id": "n1", "label": "GET /x"}]}}},
        "route_inventory": {"rows": {"items": [
            {"repository_ref": "api", "method": "GET", "path": "/x"}]}},
        "ui_route_linkage": {"rows": {"items": []}},
        "integration_candidates": {"items": []},
        "role_catalog_by_repository": {"items": []},
        "capabilities": {"capabilities": []},
    }), "utf-8")
    return run, mapping.to_dict()


def _lens_task_ids(planned):
    return {task.task_id for task in planned if task.task_type == "lens-findings"}


# --------------------------------------------------------------------------- #
# plan_judgment DAG shape
# --------------------------------------------------------------------------- #

def test_plan_judgment_creates_the_expected_task_count_and_shard_fanout(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)

    lens_tasks = [t for t in planned if t.task_type == "lens-findings"]
    select_tasks = [t for t in planned if t.task_type == "selection-fetch"]
    formation_tasks = [t for t in planned if t.task_type == "formation-proposal"]
    assert len(formation_tasks) == 2
    assert {task.task_id for task in formation_tasks} == {
        "formation-formation-0000", "formation-formation-0001"}
    assert {task.shard for task in formation_tasks} == {"formation-0000", "formation-0001"}

    # DIRECT lens-findings tasks: 3 repo-sharded (complexity, dead-code,
    # hotspots-change-friction) x 2 repos + 2 workspace-sharded (duplication,
    # dependency-risk) = 8. The 4 source_reads lenses get a select pair
    # INSTEAD of a direct lens-findings task (checked separately below).
    assert len(lens_tasks) == 8
    by_lens = {}
    for task in lens_tasks:
        by_lens.setdefault(task.lens_id, []).append(task)
    assert set(by_lens) == DIRECT_LENSES
    for lens_id in DIRECT_LENSES & REPO_SHARDED_LENSES:
        assert {t.shard for t in by_lens[lens_id]} == {"repo"}
        assert {t.repository_ref for t in by_lens[lens_id]} == {"api", "web"}
        assert len(by_lens[lens_id]) == 2
    for lens_id in DIRECT_LENSES & WORKSPACE_SHARDED_LENSES:
        assert len(by_lens[lens_id]) == 1
        assert by_lens[lens_id][0].shard == "workspace"
        assert by_lens[lens_id][0].repository_ref == ""

    # select tasks: safety-net (repo x 2 repos) + open-lens/dependencies-
    # cycles/structure-inventory (workspace) = 2 + 3 = 5.
    assert len(select_tasks) == 5
    by_select_lens = {}
    for task in select_tasks:
        assert task.task_id.endswith("-select")
        by_select_lens.setdefault(task.lens_id, []).append(task)
    assert set(by_select_lens) == SOURCE_READS_LENSES
    assert len(by_select_lens["safety-net"]) == 2
    assert {t.repository_ref for t in by_select_lens["safety-net"]} == {"api", "web"}
    for lens_id in SOURCE_READS_LENSES - {"safety-net"}:
        assert len(by_select_lens[lens_id]) == 1
        assert by_select_lens[lens_id][0].repository_ref == ""

    assert all(task.created for task in planned)
    assert all(task.estimated_tokens > 0 for task in planned)


def test_plan_judgment_registers_every_task_in_the_engine_ledger(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    engine = Engine(run)
    states = engine.task_states()
    all_packet_ids = {pid for task in planned for pid in task.packet_ids}
    assert set(states) == all_packet_ids
    assert all(state == "pending" for state in states.values())


def test_formation_task_has_no_depends_on_and_runs_independently(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    engine = Engine(run)
    formation_packet_ids = {packet_id for task in planned
                            if task.task_type == "formation-proposal"
                            for packet_id in task.packet_ids}
    # Every partition is independent of the lens tasks and ready immediately.
    assert formation_packet_ids <= set(engine.ready_task_ids())


def test_formation_task_is_formation_proposal_not_boundary_resolution(tmp_path):
    """The exact live-run gap this fix addresses: boundary-resolution's M0
    schema validates only a bare `dispositions` list, which cannot by
    itself produce module-map.json's required `modules` rows."""
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    task_types = {task.task_type for task in planned}
    assert "formation-proposal" in task_types
    assert "boundary-resolution" not in task_types


def test_formation_packet_carries_a_deterministic_partition_plan(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    engine = Engine(run)
    formation_task_ids = {task.task_id for task in planned
                          if task.task_type == "formation-proposal"}
    packets = [rec.detail["task"] for rec in engine._read_records()
               if rec.event == "created" and rec.task_id in formation_task_ids]
    plan = json.loads((run / "tasks" / formation.PARTITION_PLAN_FILENAME).read_text("utf-8"))
    assert {packet["task_id"] for packet in packets} == formation_task_ids
    assert plan["merge_order"] == [row["partition_id"] for row in plan["partitions"]]
    assert plan["global_identity"]["candidate_universe_digest"] == plan["candidate_universe_digest"]
    for packet in packets:
        context = json.loads(packet["inputs"]["formation-partition-context.json"]["content"])
        candidates = json.loads(packet["inputs"]["module-candidates.json"]["content"])
        assert {row["candidate_id"] for row in candidates} <= set(context["partition"]["candidate_ids"])
        assert context["partition"]["partition_id"] in context["merge_order"]


# --------------------------------------------------------------------------- #
# 57B-116: source_reads select-pair mechanics
# --------------------------------------------------------------------------- #

def test_source_reads_lens_gets_a_select_pair_not_a_direct_lens_task(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    task_ids = {task.task_id for task in planned}
    assert "lens-safety-net-api-14c2529e-select" in task_ids
    assert "lens-safety-net-api-14c2529e" not in task_ids
    assert "lens-dependencies-cycles-select" in task_ids
    assert "lens-dependencies-cycles" not in task_ids


def test_select_task_gets_the_exact_same_inputs_the_lens_task_itself_would(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    created = {rec.task_id: rec.detail["task"] for rec in engine._read_records()
              if rec.event == "created"}
    select_packet = created["lens-dependencies-cycles-select"]
    assert select_packet["task_type"] == "selection-fetch"
    assert select_packet["output_schema_id"] == "selection-fetch.v1"

    from analysis_wrapper.orchestrator.planner import _load_json, _lens_inputs
    lens_templates = tpl.load_lens_templates()
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")
    expected_inputs = _lens_inputs(run, lens_templates["dependencies-cycles"], synthesis_doc,
                                   module_candidates_doc, run_summary, None)
    assert set(select_packet["inputs"]) == set(expected_inputs) | {"selection-requirements.json"}


def test_requirements_packet_names_exact_inputs_scope_limits_and_typed_source_roles(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    created = {rec.task_id: rec.detail["task"] for rec in engine._read_records()
               if rec.event == "created"}

    direct = created["lens-complexity-api-14c2529e"]
    contract = json.loads(direct["inputs"]["requirements.json"]["content"])
    assert contract["parent_task_id"] == "lens-complexity-api-14c2529e"
    assert contract["expected_shard_scope"] == {"shard": "repo", "repository_ref": "api"}
    assert contract["inherited_limits"]["max_selections"] == 0
    assert {row["input_id"] for row in contract["input_requirements"]} == {
        name for name in direct["inputs"] if name != "requirements.json"
    }
    assert {row["coverage_id"] for row in contract["coverage_requirements"]} == {
        name for name in direct["inputs"] if name.startswith("signals/")
    }

    select = created["lens-safety-net-api-14c2529e-select"]
    selection_contract = json.loads(select["inputs"]["selection-requirements.json"]["content"])
    roles = {row["role_id"]: row for row in selection_contract["roles"]}
    assert {"lens-critical-source", "test-source", "ci-config", "declared-validation-tooling"} <= set(roles)
    assert roles["test-source"]["inventory_paths"] == ["test_files.paths"]
    assert roles["ci-config"]["inventory_paths"] == ["ci_configs.path"]
    assert selection_contract["parent_requirements_digest"]


def test_select_task_instructions_request_up_to_its_own_lens_cap_with_empty_quoted_text(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    created = {rec.task_id: rec.detail["task"] for rec in engine._read_records()
              if rec.event == "created"}
    # open-lens's own frontmatter raises its cap to 24 (round-2 strengthener) --
    # its select task's instructions must ask for ITS number, not the flat default.
    instructions = created["lens-open-lens-select"]["instructions"]
    open_lens = tpl.load_lens_templates()["open-lens"]
    assert open_lens.max_selections == 24
    assert instructions.index(tpl.selection_fetch_preamble(open_lens.max_selections)) == 0
    assert "up to 24" in instructions.lower()
    assert 'EMPTY ("")' in instructions
    assert "# Lens: open-lens" in instructions  # the lens's own body still rides along

    # a lens that keeps the flat default (dependencies-cycles is source_reads
    # but never overrides max_selections) asks for "up to 12" instead.
    dep_instructions = created["lens-dependencies-cycles-select"]["instructions"]
    assert "up to 12" in dep_instructions.lower()


def test_repo_sharded_lens_task_only_sees_its_own_repos_signal_views(tmp_path):
    run, _ = _build_run(tmp_path)
    lens_templates = tpl.load_lens_templates()
    shared_body = tpl.load_shared_body()
    template = lens_templates["complexity"]
    from analysis_wrapper.orchestrator.planner import _load_json, _lens_inputs
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")

    api_inputs = _lens_inputs(run, template, synthesis_doc, module_candidates_doc,
                              run_summary, "api")
    web_inputs = _lens_inputs(run, template, synthesis_doc, module_candidates_doc,
                              run_summary, "web")
    assert any("lizard-api" in name for name in api_inputs)
    assert not any("lizard" in name for name in web_inputs)  # web has no lizard view
    # the cross-repo jscpd-cross view is not in complexity's signals list at
    # all, but even if it were, it is attributed to "api" only -- never leaks
    # into web's per-repo packet.
    assert not any("jscpd" in name for name in web_inputs)

    # module-candidates.json is a BARE ARRAY (composer-shardable), never a
    # dict wrapping the array -- see _split_module_candidates.
    api_candidates = json.loads(api_inputs["module-candidates.json"])
    assert isinstance(api_candidates, list)
    assert {row["candidate_id"] for row in api_candidates} == {"mc-api-folder"}
    api_meta = json.loads(api_inputs["module-candidates-meta.json"])
    assert api_meta["candidate_count"] == 1
    web_candidates = json.loads(web_inputs["module-candidates.json"])
    assert {row["candidate_id"] for row in web_candidates} == {"mc-web-folder"}


# --------------------------------------------------------------------------- #
# 57B-116 Part A: test-ci-evidence.json -- safety-net and open-lens ONLY
# --------------------------------------------------------------------------- #

def test_test_ci_evidence_reaches_only_safety_net_and_open_lens(tmp_path):
    run, _ = _build_run(tmp_path)
    lens_templates = tpl.load_lens_templates()
    from analysis_wrapper.orchestrator.planner import (
        _lens_inputs, _load_json, _test_ci_evidence_rows,
    )
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")
    identities = identity.load(run)
    target_spec = TargetSpec.load(run / "targets.json")
    test_ci_rows = _test_ci_evidence_rows(target_spec, identities)
    assert set(test_ci_rows) == {"api", "web"}

    for lens_id in ("complexity", "dead-code", "duplication", "dependency-risk",
                   "dependencies-cycles", "structure-inventory"):
        template = lens_templates[lens_id]
        repository_ref = "api" if template.shard == "repo" else None
        inputs = _lens_inputs(run, template, synthesis_doc, module_candidates_doc,
                              run_summary, repository_ref, test_ci_rows=test_ci_rows)
        assert "test-ci-evidence.json" not in inputs, lens_id

    api_safety_net = _lens_inputs(run, lens_templates["safety-net"], synthesis_doc,
                                  module_candidates_doc, run_summary, "api",
                                  test_ci_rows=test_ci_rows)
    rows = json.loads(api_safety_net["test-ci-evidence.json"])
    assert len(rows) == 1 and rows[0]["repository_ref"] == "api"

    open_lens_inputs = _lens_inputs(run, lens_templates["open-lens"], synthesis_doc,
                                    module_candidates_doc, run_summary, None,
                                    test_ci_rows=test_ci_rows)
    all_rows = json.loads(open_lens_inputs["test-ci-evidence.json"])
    assert {row["repository_ref"] for row in all_rows} == {"api", "web"}


def test_test_ci_evidence_absent_when_test_ci_rows_not_supplied(tmp_path):
    """_lens_inputs's own default (test_ci_rows=None) still emits a valid,
    empty test-ci-evidence.json for safety-net/open-lens rather than
    omitting the input entirely -- existing callers that do not pass
    test_ci_rows (e.g. earlier tests written before Part A) keep working."""
    run, _ = _build_run(tmp_path)
    lens_templates = tpl.load_lens_templates()
    from analysis_wrapper.orchestrator.planner import _lens_inputs, _load_json
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")
    inputs = _lens_inputs(run, lens_templates["safety-net"], synthesis_doc,
                         module_candidates_doc, run_summary, "api")
    assert json.loads(inputs["test-ci-evidence.json"]) == []


def test_plan_judgment_wires_real_test_ci_evidence_into_safety_net_select_task(tmp_path):
    """End-to-end: plan_judgment itself (not a direct _lens_inputs call)
    computes test_ci_rows from the real workspace and threads it into
    safety-net's select task (safety-net is BOTH source_reads and a
    _TEST_CI_EVIDENCE_LENSES member)."""
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    created = {rec.task_id: rec.detail["task"] for rec in engine._read_records()
              if rec.event == "created"}
    api_select = created["lens-safety-net-api-14c2529e-select"]
    assert "test-ci-evidence.json" in api_select["inputs"]
    rows = json.loads(api_select["inputs"]["test-ci-evidence.json"]["content"])
    assert len(rows) == 1 and rows[0]["repository_ref"] == "api"
    # _build_run's fixture repos have no test files/CI configs of their own
    # (only internal/service.go) -- the row is still present, just empty.
    assert rows[0]["test_files"]["total_count"] == 0
    assert rows[0]["ci_configs"] == []


# --------------------------------------------------------------------------- #
# view inputs are named by their own canonical citable path -- a live run
# surfaced executors citing the packet's own input NAME verbatim (they cite
# what they see as the section header), so a `view:<tool>:<repo>:<file>`
# naming leaked straight into citations like
# `view:lizard:wcp-auth:lizard-wcp-auth.view.txt:13`, which fails
# citation_grammar_kind (does not start with exactly "signals/").
# --------------------------------------------------------------------------- #

def test_view_inputs_are_named_by_their_exact_signals_path(tmp_path):
    run, _ = _build_run(tmp_path)
    lens_templates = tpl.load_lens_templates()
    from analysis_wrapper.orchestrator.planner import _load_json, _lens_inputs
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")

    api_inputs = _lens_inputs(run, lens_templates["complexity"], synthesis_doc,
                              module_candidates_doc, run_summary, "api")
    assert "signals/lizard-api.view.txt" in api_inputs
    assert api_inputs["signals/lizard-api.view.txt"] == (
        (run / "signals" / "lizard-api.view.txt").read_text("utf-8"))
    # no input name uses the old "view:<tool>:<repo>:<file>" scheme.
    assert not any(name.startswith("view:") for name in api_inputs)
    # every signal-view input name matches _shared.md's own citation grammar
    # prefix exactly -- schemas.SIGNAL_REF is `signals/([^:]+):(\d+)`, so the
    # bare input name (no line number yet) must start with "signals/" and
    # contain no further colon.
    view_input_names = [name for name in api_inputs if name.startswith("signals/")]
    assert view_input_names  # at least one view made it through
    assert all(":" not in name[len("signals/"):] for name in view_input_names)


def test_view_filenames_never_collide_across_distinct_tool_repo_rows(tmp_path):
    """Verifies the assumption the naming fix above relies on: a view
    filename already embeds tool + repo (executor.py's run_tool names each
    view "<tool>-<repo-artifact-key>.view.txt"), so two DIFFERENT
    (tool, repository_ref) rows can never collapse onto the same
    "signals/<file>" input name and silently overwrite each other."""
    run, _ = _build_run(tmp_path)
    from analysis_wrapper.orchestrator.planner import _load_json
    run_summary = _load_json(run / "signals" / "run-summary.json")
    rows = run_summary["signals"]
    seen_views: dict[str, tuple] = {}
    for row in rows:
        view = row.get("view")
        if not view:
            continue
        key = (row.get("tool"), row.get("repository_ref"))
        assert view not in seen_views or seen_views[view] == key, (
            f"view {view!r} shared by two different (tool, repo) rows: "
            f"{seen_views.get(view)} and {key}")
        seen_views[view] = key
    assert len(seen_views) == len({row.get("view") for row in rows if row.get("view")})

    # And the planner's own dict construction never drops one: every
    # complete/partial view row for a lens with signals=[] (open-lens, "every
    # tool") lands in the inputs dict, one per row, none overwritten.
    lens_templates = tpl.load_lens_templates()
    from analysis_wrapper.orchestrator.planner import _lens_inputs
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    inputs = _lens_inputs(run, lens_templates["open-lens"], synthesis_doc,
                         module_candidates_doc, run_summary, None)
    expected_views = {f"signals/{row['view']}" for row in rows
                      if row.get("status") in {"complete", "partial"} and row.get("view")}
    actual_views = {name for name in inputs if name.startswith("signals/")}
    assert actual_views == expected_views


def test_duplication_workspace_task_includes_the_cross_repo_view(tmp_path):
    run, _ = _build_run(tmp_path)
    lens_templates = tpl.load_lens_templates()
    from analysis_wrapper.orchestrator.planner import _load_json, _lens_inputs
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")

    inputs = _lens_inputs(run, lens_templates["duplication"], synthesis_doc,
                          module_candidates_doc, run_summary, None)
    assert any("jscpd-cross" in name for name in inputs)
    all_candidates = json.loads(inputs["module-candidates.json"])
    assert isinstance(all_candidates, list)
    assert {row["candidate_id"] for row in all_candidates} == {
        "mc-api-folder", "mc-web-folder"}
    all_meta = json.loads(inputs["module-candidates-meta.json"])
    assert all_meta["candidate_count"] == 2


def test_dead_code_route_evidence_is_trimmed_to_its_own_repo(tmp_path):
    run, _ = _build_run(tmp_path)
    lens_templates = tpl.load_lens_templates()
    from analysis_wrapper.orchestrator.planner import _load_json, _lens_inputs
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")

    api_inputs = _lens_inputs(run, lens_templates["dead-code"], synthesis_doc,
                              module_candidates_doc, run_summary, "api")
    web_inputs = _lens_inputs(run, lens_templates["dead-code"], synthesis_doc,
                              module_candidates_doc, run_summary, "web")
    api_route_nodes = json.loads(api_inputs["dead-code-graph-route-nodes.json"])
    web_route_nodes = json.loads(web_inputs["dead-code-graph-route-nodes.json"])
    assert isinstance(api_route_nodes, list) and isinstance(web_route_nodes, list)
    assert len(api_route_nodes) == 1
    assert web_route_nodes == []
    assert json.loads(web_inputs["dead-code-route-inventory-rows.json"]) == []


def test_staticcheck_coverage_limitation_reaches_dead_code_and_safety_net(tmp_path):
    """57B-151: a provider coverage limit must survive into both Go quality
    lenses, without leaking one repository's result to a sibling packet."""
    run, _ = _build_run(tmp_path)
    lens_templates = tpl.load_lens_templates()
    from analysis_wrapper.orchestrator.planner import _load_json, _lens_inputs
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")

    for lens_id in ("dead-code", "safety-net"):
        inputs = _lens_inputs(run, lens_templates[lens_id], synthesis_doc,
                              module_candidates_doc, run_summary, "api")
        staticcheck_inputs = {
            name: content for name, content in inputs.items()
            if name.startswith("signals/staticcheck-")
        }
        assert len(staticcheck_inputs) == 1
        assert "coverage_limitation: staticcheck-no-package-universe" in next(
            iter(staticcheck_inputs.values()))
        requirements = json.loads(inputs["requirements.json"])
        assert {row["coverage_id"] for row in requirements["coverage_requirements"]} >= set(staticcheck_inputs)

    web_inputs = _lens_inputs(run, lens_templates["safety-net"], synthesis_doc,
                              module_candidates_doc, run_summary, "web")
    assert not any(name.startswith("signals/staticcheck-") for name in web_inputs)


# --------------------------------------------------------------------------- #
# every list-carrying input is a BARE JSON ARRAY (composer-shardable), never
# a dict wrapping it -- the exact defect a real prepared WCP run surfaced:
# lens-dependencies-cycles/lens-open-lens both failed composition with
# "input 'module-candidates.json' is ... neither a JSON array nor
# line-oriented text" the moment that dict became the packet's largest input.
# --------------------------------------------------------------------------- #

def test_every_extra_section_input_is_a_bare_array_with_a_meta_sibling(tmp_path):
    run, _ = _build_run(tmp_path)
    lens_templates = tpl.load_lens_templates()
    from analysis_wrapper.orchestrator.planner import _load_json, _lens_inputs
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")

    # open-lens pulls in every EXTRA section this table knows about.
    inputs = _lens_inputs(run, lens_templates["open-lens"], synthesis_doc,
                         module_candidates_doc, run_summary, None)
    array_meta_pairs = {
        "graph-nodes.json": "graph-meta.json",
        "route-inventory.json": "route-inventory-meta.json",
        "ui-route-linkage.json": "ui-route-linkage-meta.json",
        "integration-candidates.json": "integration-candidates-meta.json",
        "role-catalog-by-repository.json": "role-catalog-by-repository-meta.json",
        "capabilities.json": "capabilities-meta.json",
    }
    for array_name, meta_name in array_meta_pairs.items():
        assert array_name in inputs, array_name
        assert meta_name in inputs, meta_name
        assert isinstance(json.loads(inputs[array_name]), list), array_name
        assert isinstance(json.loads(inputs[meta_name]), dict), meta_name
    # repositories.json is likewise a bare array (no "items" wrapper).
    assert isinstance(json.loads(inputs["repositories.json"]), list)


def test_split_graph_flattens_every_node_kind_with_kind_tagged_and_keeps_hub_meta():
    from analysis_wrapper.orchestrator.planner import _split_graph
    graph = {
        "stats": {"x": 1}, "coverage": {"y": 2},
        "nodes": {
            "route": {"total_count": 1, "included_count": 1, "truncated": False,
                     "items": [{"id": "n1", "repository_ref": "api", "label": "GET /x"}]},
            "module": {"total_count": 1, "included_count": 1, "truncated": False,
                      "items": [{"id": "n2", "repository_ref": "api", "label": "core"}]},
        },
        "edges_by_type_and_status": {"sync-api": {"observed": 3}},
        "highest_degree_nodes": {"total_count": 1, "included_count": 1, "truncated": False,
                                 "items": [{"node_id": "n2", "degree": 5}]},
    }
    flattened, meta = _split_graph(graph)
    assert {row["id"]: row["kind"] for row in flattened} == {"n1": "route", "n2": "module"}
    assert meta["stats"] == {"x": 1}
    assert meta["nodes_summary"]["route"]["total_count"] == 1
    assert meta["highest_degree_nodes"]["items"][0]["node_id"] == "n2"


def test_split_bounded_list_separates_counts_from_items():
    from analysis_wrapper.orchestrator.planner import _split_bounded_list
    section = {"total_count": 5, "included_count": 2, "truncated": True,
              "items": [{"a": 1}, {"a": 2}]}
    items, meta = _split_bounded_list(section)
    assert items == [{"a": 1}, {"a": 2}]
    assert meta == {"total_count": 5, "included_count": 2, "truncated": True}


def test_split_capabilities_keeps_non_capabilities_fields_in_meta():
    from analysis_wrapper.orchestrator.planner import _split_capabilities
    doc = {"schema_version": "3.0.0", "project_ref": "proj", "aggregate_status": "complete",
          "capabilities": [{"capability_id": "routing", "status": "complete"}]}
    rows, meta = _split_capabilities(doc)
    assert rows == [{"capability_id": "routing", "status": "complete"}]
    assert meta == {"schema_version": "3.0.0", "project_ref": "proj",
                    "aggregate_status": "complete"}


def test_a_large_extra_section_now_composes_and_shards_instead_of_failing(tmp_path):
    """The exact production failure: a section serialized as a JSON OBJECT
    (not an array) was, before this fix, unshardable the moment it became a
    packet's largest input -- ComposerError, not a smaller packet. Build a
    graph large enough to dominate a tight budget and confirm it now shards
    cleanly instead of raising."""
    run, _ = _build_run(tmp_path)
    lens_templates = tpl.load_lens_templates()
    shared_body = tpl.load_shared_body()
    from analysis_wrapper.orchestrator.composer import compose
    from analysis_wrapper.orchestrator.planner import _load_json, _packet_tokens

    synthesis_doc = _load_json(run / "synthesis-input.json")
    # Inflate the graph's route-node bucket well beyond every other input.
    synthesis_doc["graph"]["nodes"]["route"]["items"] = [
        {"id": f"n{i}", "repository_ref": "api", "label": f"GET /x{i}",
        "status": "observed", "evidence_basis": "static-reference",
        "evidence": [f"api@HEAD:routes.go:{i}"], "attrs": {}}
        for i in range(300)
    ]
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")
    template = lens_templates["structure-inventory"]  # workspace-shard, consumes "graph"
    from analysis_wrapper.orchestrator.planner import _lens_inputs
    inputs = _lens_inputs(run, template, synthesis_doc, module_candidates_doc,
                         run_summary, None)
    instructions = tpl.render_instructions(template, shared_body)

    # Would previously raise ComposerError once graph-nodes.json dominated a
    # tight budget (it was a dict, not an array); now it shards cleanly.
    packets = compose(task_id="t", template_id="t", template_version=template.version,
                      task_type="lens-findings", instructions=instructions, inputs=inputs,
                          output_schema_id="lens-findings.v1", context_budget_tokens=6000)
    assert len(packets) > 1
    assert all("-shard-" in packet.task_id for packet in packets)
    assert all(_packet_tokens(packet) <= 6000 for packet in packets)
    for index, packet in enumerate(packets, start=1):
        contract = json.loads(packet.inputs["requirements.json"].content)
        assert contract["parent_task_id"] == "t"
        assert contract["parent_requirements_digest"]
        assert contract["shard_local_scope"] == {
            "index": index, "total": len(packets), "split_input_id": "graph-nodes.json",
        }
    # graph-meta.json (the small sibling) rides on every shard, unsplit.
    for packet in packets:
        assert "graph-meta.json" in packet.inputs


def test_normal_budget_large_workspace_uses_semantic_partitions_and_compact_select_indexes(tmp_path):
    """57B-150: semantic work items precede composer.py's generic fallback.

    The fixture's long api signal makes the workspace open lens genuinely
    oversized at a normal (not artificial 6k test) budget.  Its source
    selection packets must receive local compact indexes, while the persisted
    graph proves every final-evidence input has one deterministic owner.
    """
    run, _ = _build_run(tmp_path, inflate_lizard_lines=8000)
    planned = planner.plan_judgment(run, context_budget_tokens=24_000)
    assert planned

    from analysis_wrapper.orchestrator import semantic_partitions
    manifest = json.loads((run / "tasks" / semantic_partitions.PLAN_FILENAME).read_text("utf-8"))
    by_task = {row["task_id"]: row for row in manifest["plans"]}
    open_plan = by_task["lens-open-lens"]
    assert open_plan["active"] is True
    assert {row["kind"] for row in open_plan["partitions"]} == {
        "repository", "cross-boundary",
    }
    assert all(row["source_input_digests"] for row in open_plan["partitions"])
    assert semantic_partitions.validate_manifest(run) == []

    engine = Engine(run)
    created = [record.detail["task"] for record in engine._read_records()
               if record.event == "created"]
    open_selects = [packet for packet in created
                    if packet["task_type"] == "selection-fetch"
                    and packet["task_id"].startswith("lens-open-lens-sp-")]
    assert len(open_selects) == len(open_plan["partitions"])
    for packet in open_selects:
        # The final lens contract/evidence is intentionally not copied into
        # selection work.  Typed input names remain, but their contents are
        # small locator indexes and the actual partition descriptor is exact.
        assert "requirements.json" not in packet["inputs"]
        descriptor = json.loads(packet["inputs"]["semantic-partition.json"]["content"])
        assert descriptor["active"] is True
        assert descriptor["parent_task_id"] == "lens-open-lens"
        for input_id, item in packet["inputs"].items():
            if "lizard-api" in input_id:
                index = json.loads(item["content"])
                assert len(index) <= 1 + semantic_partitions.MAX_SELECTION_INDEX_ROWS
                assert index[0]["index_summary"]["truncated"] is True


def test_semantic_source_partitions_finalize_before_dedup(tmp_path):
    """A semantic select child is a real select/fetch/finalize pair, not an
    advisory index that later stages can accidentally omit."""
    run, _ = _build_run(tmp_path, inflate_lizard_lines=8000)
    planner.plan_judgment(run, context_budget_tokens=24_000)
    validated_lenses = _drive_dag_to_completion(run, context_budget_tokens=24_000)
    assert any("-sp-" in task_id for task_id in validated_lenses)
    dedup = planner.plan_dedup(run, context_budget_tokens=24_000)
    assert dedup.task_id == "dedup-rank"


# --------------------------------------------------------------------------- #
# idempotent re-planning
# --------------------------------------------------------------------------- #

def test_replanning_with_unchanged_lenses_is_a_no_op(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    second = planner.plan_judgment(run)
    assert all(not task.created for task in second)


# --------------------------------------------------------------------------- #
# budget sharding (composer's automatic mechanical split)
# --------------------------------------------------------------------------- #

def test_a_tiny_context_budget_forces_composer_sharding(tmp_path):
    """Exercises the exact (inputs, compose) path plan_judgment uses for one
    lens task in isolation -- a global run-wide context_budget_tokens is a
    single knob shared by every heterogeneous lens/workspace task at once
    (see plan_judgment's own signature), so tuning it tight enough to force
    JUST one task's sharding while every sibling task still fits is fragile
    across 14 differently-sized tasks; testing the composition function
    directly with the same real inputs isolates the behavior cleanly."""
    run, _ = _build_run(tmp_path, inflate_lizard_lines=2000)
    from analysis_wrapper.orchestrator.composer import compose
    from analysis_wrapper.orchestrator.planner import _lens_inputs, _load_json, _packet_tokens

    lens_templates = tpl.load_lens_templates()
    shared_body = tpl.load_shared_body()
    template = lens_templates["complexity"]
    synthesis_doc = _load_json(run / "synthesis-input.json")
    module_candidates_doc = _load_json(run / "module-candidates.json")
    run_summary = _load_json(run / "signals" / "run-summary.json")
    inputs = _lens_inputs(run, template, synthesis_doc, module_candidates_doc,
                          run_summary, "api")
    instructions = tpl.render_instructions(template, shared_body)

    generous = compose(task_id="t", template_id="t", template_version=template.version,
                       task_type="lens-findings", instructions=instructions, inputs=inputs,
                       output_schema_id="lens-findings.v1", context_budget_tokens=200_000)
    assert len(generous) == 1

    # 6000, not 2000: at 2000 the fixed cost leaves so little per shard that
    # the composer's own MAX_SHARD_COUNT sanity guard (57B-116 hardening)
    # now correctly refuses rather than emitting ~78 shards -- 6000 still
    # forces real sharding while staying a sane, realistic shard count.
    tight = compose(task_id="t", template_id="t", template_version=template.version,
                    task_type="lens-findings", instructions=instructions, inputs=inputs,
                    output_schema_id="lens-findings.v1", context_budget_tokens=6000)
    assert len(tight) > 1, "expected the inflated view to force composer sharding"
    assert all("-shard-" in packet.task_id for packet in tight)
    assert all(_packet_tokens(packet) <= 6000 for packet in tight)


# --------------------------------------------------------------------------- #
# two-phase dedup planning
# --------------------------------------------------------------------------- #

# The formation task has no depends_on -- it is ALWAYS ready alongside
# every lens/select task from the moment plan_judgment registers it, so a
# plain ``engine.claim(1)`` (sorted task_id order) can offer it ahead of
# whatever task a test cares about. The helpers below always claim a whole
# READY BATCH at once and dispatch each claimed item by its own
# task_type/task_id, so no test ever depends on the engine's claim ordering.
#
# A source_reads lens's select task is now ALSO part of the DAG plan_judgment
# produces -- _drive_dag_to_completion drives the FULL two-phase pipeline
# (select -> fetch-selections -> plan_lens_finalize -> the real lens-findings
# task) using the real selection.py module (not a stub), so this also
# exercises fetch-selections end to end for every planner test that needs a
# fully-validated DAG.

_FORMATION_PLACEHOLDER_OUTPUT = {"modules": [
    {"module_id": "placeholder", "name": "Placeholder", "classification": "unresolved",
     "confidence": "low", "aliases": []},
]}

# Names a real, resolvable location in _build_run's own fixture (api's
# internal/service.go, at its recorded clean-HEAD revision) so
# selection.fetch() genuinely fetches something rather than only ever
# producing "NOT FETCHED" rows.
_SELECT_OUTPUT = {"selections": [{
    "selection_id": "verify-service-body",
    "purpose": "confirm the service function body",
    "ref": "api@" + "a" * 40 + ":internal/service.go:1",
    "quoted_text": "",
}]}


def _lens_output(task_id):
    return {
        "findings": [{
            "finding_id": f"finding-{task_id}",
            "claim": "sample claim", "lens": "complexity",
            "affected_modules": ["mc-api-folder"],
            "evidence": [{"fact": "one fact", "refs": ["signals/lizard-api.view.txt:1"],
                         "basis": "static-reference"}],
            "evidence_basis": ["static-reference"],
            "impact": "impact text", "priority": "medium", "confidence": "medium",
            "limitations": "none", "suggested_direction": "direction",
            "changeability_question": "none",
        }],
        "coverage": [{"signal": "lizard", "status": "complete", "note": ""}],
    }


def _collision_lens_output(task_id):
    output = _lens_output(task_id)
    output["findings"][0]["finding_id"] = "finding-duplicate-across-lenses"
    return output


def _submit(engine, item, output, *, status="ok"):
    if item.packet.task_type == "formation-proposal" and isinstance(output, dict):
        partition_input = item.packet.inputs.get("formation-partition-context.json")
        candidates_input = item.packet.inputs.get("module-candidates.json")
        if partition_input is not None and candidates_input is not None:
            output = dict(output)
            output["candidate_dispositions"] = [{
                "candidate_id": row["candidate_id"], "disposition": "standalone",
                "module_ids": [output["modules"][0]["module_id"]], "reason": "fixture",
            } for row in json.loads(candidates_input.content)]
    if item.packet.task_type == "lens-findings" and isinstance(output, dict) \
            and isinstance(output.get("findings"), list):
        raw = item.packet.inputs.get("requirements.json")
        if raw is not None:
            contract = json.loads(raw.content)
            output = dict(output)
            coverage = contract["coverage_requirements"]
            output["coverage"] = [{
                "signal": row["coverage_id"], "status": "complete",
                "note": "fixture",
            } for row in coverage]
            role_results = item.packet.inputs.get("selection-role-results.json")
            if role_results is not None:
                for result in json.loads(role_results.content)["roles"]:
                    for row in output["coverage"]:
                        if row["signal"] == f"source-selection/{result['role_id']}":
                            row["status"] = result["coverage_status"]
            output["input_dispositions"] = [{
                "input_id": row["input_id"], "status": "examined",
                "evidence_refs": ["metric:code.analyzed-scope.total"], "note": "fixture",
            } for row in contract["input_requirements"]]
            if role_results is not None:
                results = json.loads(role_results.content)["roles"]
                if any(row["fetch_status"] in {"partial", "failed"} for row in results):
                    for row in output["input_dispositions"]:
                        if row["input_id"] == "fetched-evidence.json":
                            row["status"] = "failed"
                            row["evidence_refs"] = []
            finding_ids = [row["finding_id"] for row in output["findings"]]
            output["checklist_dispositions"] = [{
                "dimension_id": row["dimension_id"],
                "outcome": "finding" if index == 0 else "unknown",
                "finding_ids": finding_ids if index == 0 else [],
                "evidence_refs": ["metric:code.analyzed-scope.total"] if index == 0 else [],
                "limitation": "fixture",
            } for index, row in enumerate(contract["checklist_requirements"])]
    if item.packet.task_type == "selection-fetch" and isinstance(output, dict):
        raw = item.packet.inputs.get("selection-requirements.json")
        if raw is not None:
            contract = json.loads(raw.content)
            output = dict(output)
            selections = output.get("selections", [])
            if not isinstance(selections, list):
                # Deliberately malformed fixtures exercise the engine's
                # schema-failure/retry path; do not try to decorate them.
                selection_ids = []
            else:
                selection_ids = [row["selection_id"] for row in selections
                                 if isinstance(row, dict) and "selection_id" in row]
            output["role_dispositions"] = [{
                "role_id": row["role_id"],
                "status": "selected" if selection_ids else "unavailable",
                "selection_ids": selection_ids if selection_ids else [],
                "note": "fixture",
            } for row in contract["roles"]]
    at = now_iso()
    result = TaskResult(
        task_id=item.packet.task_id, status=status, output=output,
        executor=ExecutorInfo(kind="manual", model="test", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.1),
        tokens=None, validation=ValidationOutcome(passed=(status == "ok"), failures=(
            () if status == "ok" else ({"check": "x", "detail": "y", "location": ""},))),
        attempt=item.attempt)
    return engine.submit(item.packet.task_id, result.to_dict())


def _drive_dag_to_completion(run, *, hold_back_lens_task_ids=frozenset(),
                            fail_lens_task_ids=frozenset(),
                            lens_output_fn=_lens_output,
                            context_budget_tokens=planner.DEFAULT_CONTEXT_BUDGET_TOKENS):
    """Claims and validates every ready task (formation, direct lens-findings,
    and select tasks), running the REAL fetch-selections + plan_lens_finalize
    as soon as a select task validates so its paired lens-findings task
    appears and gets claimed/validated too -- looping until nothing is ready
    and nothing is left to finalize.

    - A task_id in ``hold_back_lens_task_ids`` is claimed (so nothing else
      can claim it) but never submitted, leaving it permanently outstanding
      ("pending") -- for tests needing exactly one lens left un-validated.
    - A task_id in ``fail_lens_task_ids`` is always submitted a malformed
      (schema-invalid) output; the loop's own repeated claim/fail naturally
      exhausts it after the engine's max_attempts, permanently failing it.
    - ``lens_output_fn`` builds the OK output for every OTHER lens-findings
      task (default: a unique finding per task_id).

    Returns the set of every lens-findings task_id (direct AND finalized
    via plan_lens_finalize) that ended up VALIDATED.
    """
    from analysis_wrapper.orchestrator import selection
    engine = Engine(run)
    validated_lens_ids: set[str] = set()
    pending_finalize: set[str] = set()

    while True:
        progressed = False
        ready = engine.ready_task_ids()
        if ready:
            claimed = engine.claim(len(ready), executor_kind="manual", model="test")
            for item in claimed:
                task_id = item.packet.task_id
                task_type = item.packet.task_type
                if task_id in hold_back_lens_task_ids:
                    continue
                progressed = True
                if task_id in fail_lens_task_ids:
                    _submit(engine, item, {"findings": "not-a-list", "coverage": []})
                elif task_type == "lens-findings":
                    outcome = _submit(engine, item, lens_output_fn(task_id))
                    if outcome["status"] == "validated":
                        validated_lens_ids.add(task_id)
                elif task_type == "formation-proposal":
                    _submit(engine, item, _FORMATION_PLACEHOLDER_OUTPUT)
                elif task_type == "selection-fetch":
                    selection_contract = json.loads(
                        item.packet.inputs["selection-requirements.json"].content)
                    selection_output = (_SELECT_OUTPUT if selection_contract["roles"]
                                        else {"selections": []})
                    outcome = _submit(engine, item, selection_output)
                    assert outcome["status"] == "validated", outcome
                    pending_finalize.add(task_id)
                else:
                    raise AssertionError(f"unexpected task_type: {task_type}")

        for select_task_id in list(pending_finalize):
            lens_task_id = select_task_id[:-len("-select")]
            selection.fetch(run, select_task_id)
            planner.plan_lens_finalize(run, lens_task_id,
                                       context_budget_tokens=context_budget_tokens)
            pending_finalize.discard(select_task_id)
            progressed = True

        if not progressed:
            break
    return validated_lens_ids


def test_plan_dedup_refuses_before_any_lens_task_validated(tmp_path):
    # plan_judgment was never even called -- no ledger, nothing validated.
    run, _ = _build_run(tmp_path)
    with pytest.raises(planner.PlannerError, match="no validated lens-findings"):
        planner.plan_dedup(run)


def test_plan_dedup_refuses_while_a_lens_task_is_still_pending(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    held_back = sorted(_lens_task_ids(planned))[0]  # one of the 8 DIRECT lens tasks
    _drive_dag_to_completion(run, hold_back_lens_task_ids={held_back})
    with pytest.raises(planner.PlannerError, match="still pending"):
        planner.plan_dedup(run)


def test_plan_dedup_refuses_while_a_select_task_is_not_yet_finalized(tmp_path):
    """The new gap this fix closes: a source_reads lens's select task
    validating does not by itself count as done -- plan_dedup must not
    silently proceed without that lens's (not yet finalized) findings."""
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    # Validate every ready task EXCEPT run the select tasks' own
    # fetch-selections/plan_lens_finalize follow-up -- simulate an operator
    # who validated the select tasks but has not run the next step yet.
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    for item in claimed:
        if item.packet.task_type == "lens-findings":
            _submit(engine, item, _lens_output(item.packet.task_id))
        elif item.packet.task_type == "formation-proposal":
            _submit(engine, item, _FORMATION_PLACEHOLDER_OUTPUT)
        elif item.packet.task_type == "selection-fetch":
            outcome = _submit(engine, item, _SELECT_OUTPUT)
            assert outcome["status"] == "validated", outcome
    with pytest.raises(planner.PlannerError, match="still pending"):
        planner.plan_dedup(run)


def test_plan_dedup_composes_from_every_validated_lens_output(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    validated_lens_ids = _drive_dag_to_completion(run)
    assert len(validated_lens_ids) == 13  # 8 direct + 5 select-finalized

    task = planner.plan_dedup(run)
    assert task.task_id == "dedup-rank"
    assert task.created is True

    engine = Engine(run)
    records = engine._read_records()
    created = next(rec for rec in records
                  if rec.event == "created" and rec.task_id == "dedup-rank")
    packet = created.detail["task"]
    assert set(packet["depends_on"]) == validated_lens_ids
    finding_ids = json.loads(packet["inputs"]["input-finding-ids.json"]["content"])
    assert sorted(finding_ids) == sorted(f"finding-{tid}" for tid in validated_lens_ids)


def test_plan_dedup_survives_a_permanently_failed_lens_shard(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    doomed = sorted(_lens_task_ids(planned))[0]  # a DIRECT lens task
    validated_lens_ids = _drive_dag_to_completion(run, fail_lens_task_ids={doomed})
    assert doomed not in validated_lens_ids
    assert len(validated_lens_ids) == 12  # 13 - the one permanently failed

    task = planner.plan_dedup(run)
    assert task.created is True
    engine = Engine(run)
    created = next(rec for rec in engine._read_records()
                  if rec.event == "created" and rec.task_id == "dedup-rank")
    assert doomed not in set(created.detail["task"]["depends_on"])


def test_plan_dedup_rejects_colliding_finding_ids_across_lens_outputs(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    _drive_dag_to_completion(run, lens_output_fn=_collision_lens_output)
    with pytest.raises(planner.PlannerError, match="globally unique"):
        planner.plan_dedup(run)


def test_plan_dedup_is_idempotent(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    _drive_dag_to_completion(run)
    first = planner.plan_dedup(run)
    assert first.created is True
    second = planner.plan_dedup(run)
    assert second.created is False


# --------------------------------------------------------------------------- #
# plan_lens_finalize -- phase 2 of the source_reads select/finalize pair
# --------------------------------------------------------------------------- #

def test_select_shard_task_ids_returns_empty_when_nothing_created(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    assert planner._select_shard_task_ids(run, "lens-open-lens-select") == []


def test_select_shard_task_ids_returns_the_bare_id_when_never_sharded(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)  # default 96000 budget -- nothing needs sharding
    assert planner._select_shard_task_ids(run, "lens-open-lens-select") == [
        "lens-open-lens-select"]


def test_select_shard_task_ids_finds_and_sorts_shards_numerically(tmp_path):
    run, _ = _build_run(tmp_path, inflate_lizard_lines=2000)
    planner.plan_judgment(run, context_budget_tokens=6000)
    shard_ids = planner._select_shard_task_ids(run, "lens-open-lens-select")
    assert len(shard_ids) >= 2
    assert shard_ids == sorted(shard_ids, key=lambda tid: int(tid.rsplit("-", 1)[1]))
    assert all(tid.startswith("lens-open-lens-select-shard-") for tid in shard_ids)


def test_plan_lens_finalize_raises_for_an_unknown_lens_task_id(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    with pytest.raises(planner.PlannerError, match="unknown lens task_id"):
        planner.plan_lens_finalize(run, "lens-not-a-real-lens")


def test_plan_lens_finalize_raises_for_a_non_source_reads_lens(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    direct_task_id = sorted(_lens_task_ids(planned))[0]  # already created directly
    with pytest.raises(planner.PlannerError, match="not a source_reads lens task"):
        planner.plan_lens_finalize(run, direct_task_id)


def test_plan_lens_finalize_raises_when_select_task_not_yet_validated(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    with pytest.raises(planner.PlannerError, match="not yet validated"):
        planner.plan_lens_finalize(run, "lens-open-lens")


def test_plan_lens_finalize_raises_when_fetched_evidence_missing(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    for item in claimed:
        if item.packet.task_id == "lens-open-lens-select":
            outcome = _submit(engine, item, _SELECT_OUTPUT)
            assert outcome["status"] == "validated"
        elif item.packet.task_type == "formation-proposal":
            _submit(engine, item, _FORMATION_PLACEHOLDER_OUTPUT)
        elif item.packet.task_type == "lens-findings":
            _submit(engine, item, _lens_output(item.packet.task_id))
        elif item.packet.task_type == "selection-fetch":
            _submit(engine, item, _SELECT_OUTPUT)
    # Its select task validated, but fetch-selections never ran for it.
    with pytest.raises(planner.PlannerError, match="run 'fetch-selections"):
        planner.plan_lens_finalize(run, "lens-open-lens")


def test_plan_lens_finalize_composes_the_real_lens_task_with_fetched_evidence(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    for item in claimed:
        if item.packet.task_type == "selection-fetch":
            outcome = _submit(engine, item, _SELECT_OUTPUT)
            assert outcome["status"] == "validated"
        elif item.packet.task_type == "formation-proposal":
            _submit(engine, item, _FORMATION_PLACEHOLDER_OUTPUT)
        elif item.packet.task_type == "lens-findings":
            _submit(engine, item, _lens_output(item.packet.task_id))

    from analysis_wrapper.orchestrator import selection
    fetched_path = selection.fetch(run, "lens-open-lens-select")
    assert fetched_path == planner.fetch_selections_output_path(run, "lens-open-lens-select")
    fetched_evidence = json.loads(fetched_path.read_text("utf-8"))
    assert isinstance(fetched_evidence, list) and len(fetched_evidence) == 1
    assert fetched_evidence[0]["selection_id"] == "verify-service-body"
    assert "func" in fetched_evidence[0]["excerpt"] or "package" in fetched_evidence[0]["excerpt"]

    task = planner.plan_lens_finalize(run, "lens-open-lens")
    assert task.task_id == "lens-open-lens"
    assert task.task_type == "lens-findings"
    assert task.lens_id == "open-lens"
    assert task.created is True

    records = engine._read_records()
    created = next(rec for rec in records
                  if rec.event == "created" and rec.task_id == "lens-open-lens")
    packet = created.detail["task"]
    assert packet["depends_on"] == ["lens-open-lens-select"]
    assert "fetched-evidence.json" in packet["inputs"]
    assert json.loads(packet["inputs"]["fetched-evidence.json"]["content"]) == fetched_evidence
    assert tpl.SOURCE_VERIFIED_ADDENDUM in packet["instructions"]

    # And it validates: claim + submit it like any other lens-findings task.
    ready = engine.claim(1, executor_kind="manual", model="test")
    assert ready and ready[0].packet.task_id == "lens-open-lens"
    outcome = _submit(engine, ready[0], _lens_output("lens-open-lens"))
    assert outcome["status"] == "validated", outcome


def test_fetch_failure_becomes_final_lens_coverage_gap_and_rejects_a_clean_projection(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    bad_ref_output = {"selections": [{
        "selection_id": "missing-source", "purpose": "fixture source",
        "ref": "api@" + "a" * 40 + ":internal/missing.go:1", "quoted_text": "",
    }]}
    for item in claimed:
        if item.packet.task_id == "lens-open-lens-select":
            assert _submit(engine, item, bad_ref_output)["status"] == "validated"

    from analysis_wrapper.orchestrator import selection
    rows = json.loads(selection.fetch(run, "lens-open-lens-select").read_text("utf-8"))
    assert rows[0]["excerpt"].startswith("NOT FETCHED:")
    planner.plan_lens_finalize(run, "lens-open-lens")
    record = next(rec for rec in engine._read_records()
                  if rec.event == "created" and rec.task_id == "lens-open-lens")
    packet_inputs = {name: value["content"]
                     for name, value in record.detail["task"]["inputs"].items()}
    role_results = json.loads(packet_inputs["selection-role-results.json"])["roles"]
    assert role_results and all(row["coverage_status"] == "failed" for row in role_results)

    contract = json.loads(packet_inputs["requirements.json"])
    clean_output = {
        "findings": [],
        "coverage": [{"signal": row["coverage_id"], "status": "complete", "note": "clean"}
                     for row in contract["coverage_requirements"]],
        "input_dispositions": [{
            "input_id": row["input_id"], "status": "examined",
            "evidence_refs": ["metric:code.analyzed-scope.total"], "note": "clean",
        } for row in contract["input_requirements"]],
        "checklist_dispositions": [{
            "dimension_id": row["dimension_id"], "outcome": "no-concern-observed",
            "finding_ids": [], "evidence_refs": ["metric:code.analyzed-scope.total"],
            "limitation": "clean",
        } for row in contract["checklist_requirements"]],
    }
    checks = {row["check"] for row in schemas.validate_output(
        "lens-findings", clean_output, packet_inputs=packet_inputs)}
    assert {"selection-role-coverage-projection", "fetched-evidence-coverage-gap"} <= checks


def test_permanently_failed_selection_is_finalized_as_a_source_coverage_gap(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    target = "lens-open-lens-select"
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    target_item = next(item for item in claimed if item.packet.task_id == target)
    _submit(engine, target_item, {"selections": "invalid"})
    while engine.task_states()[target] == "pending":
        item = engine.claim(1, executor_kind="manual", model="test")[0]
        assert item.packet.task_id == target
        _submit(engine, item, {"selections": "invalid"})
    assert engine.task_states()[target] == "failed"

    finalized = planner.plan_lens_finalize(run, "lens-open-lens")
    assert finalized.created is True
    record = next(rec for rec in engine._read_records()
                  if rec.event == "created" and rec.task_id == "lens-open-lens")
    role_results = json.loads(record.detail["task"]["inputs"]
                              ["selection-role-results.json"]["content"])["roles"]
    assert role_results and all(row["status"] == "unavailable" for row in role_results)
    assert all(row["coverage_status"] == "failed" for row in role_results)


def test_plan_lens_finalize_is_idempotent(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    for item in claimed:
        if item.packet.task_type == "selection-fetch":
            _submit(engine, item, _SELECT_OUTPUT)
        elif item.packet.task_type == "formation-proposal":
            _submit(engine, item, _FORMATION_PLACEHOLDER_OUTPUT)
        elif item.packet.task_type == "lens-findings":
            _submit(engine, item, _lens_output(item.packet.task_id))
    from analysis_wrapper.orchestrator import selection
    selection.fetch(run, "lens-open-lens-select")

    first = planner.plan_lens_finalize(run, "lens-open-lens")
    assert first.created is True
    second = planner.plan_lens_finalize(run, "lens-open-lens")
    assert second.created is False


def test_plan_lens_finalize_aggregates_a_sharded_select_task(tmp_path):
    """57B-116 hardening: a select task's own packet can ALSO be composer-
    sharded (it carries the SAME inputs the lens task itself would) --
    plan_lens_finalize must discover every shard, require ALL validated and
    fetched, and merge their evidence in shard order, rather than looking up
    the (in this case never-created) bare select_task_id and failing closed
    even though every shard had validated and been fetched."""
    run, _ = _build_run(tmp_path, inflate_lizard_lines=2000)
    planner.plan_judgment(run, context_budget_tokens=6000)
    engine = Engine(run)
    created = {rec.task_id for rec in engine._read_records() if rec.event == "created"}
    shard_ids = sorted(
        (tid for tid in created if tid.startswith("lens-open-lens-select-shard-")),
        key=lambda tid: int(tid.rsplit("-", 1)[1]))
    assert len(shard_ids) >= 2, "expected the inflated view to force select-task sharding too"
    assert "lens-open-lens-select" not in created

    from analysis_wrapper.orchestrator import selection

    def _shard_output(index):
        return {"selections": [{
            "selection_id": f"verify-shard-{index}",
            "purpose": "confirm a fact from this shard",
            "ref": "api@" + "a" * 40 + ":internal/service.go:1",
            "quoted_text": "",
        }]}

    remaining_shards = set(shard_ids)
    while True:
        ready = engine.ready_task_ids()
        if not ready:
            break
        claimed = engine.claim(len(ready), executor_kind="manual", model="test")
        for item in claimed:
            task_id = item.packet.task_id
            if task_id in remaining_shards:
                index = int(task_id.rsplit("-", 1)[1])
                _submit(engine, item, _shard_output(index))
                remaining_shards.discard(task_id)
            elif item.packet.task_type == "lens-findings":
                _submit(engine, item, _lens_output(task_id))
            elif item.packet.task_type == "formation-proposal":
                _submit(engine, item, _FORMATION_PLACEHOLDER_OUTPUT)
            elif item.packet.task_type == "selection-fetch":
                _submit(engine, item, _SELECT_OUTPUT)
    assert not remaining_shards, f"never validated: {remaining_shards}"

    for shard_id in shard_ids:
        fetched_path = selection.fetch(run, shard_id)
        assert fetched_path == planner.fetch_selections_output_path(run, shard_id)

    task = planner.plan_lens_finalize(run, "lens-open-lens")
    assert task.created is True

    records = engine._read_records()
    created_record = next(rec for rec in records
                         if rec.event == "created" and rec.task_id == "lens-open-lens")
    packet = created_record.detail["task"]
    assert set(packet["depends_on"]) == set(shard_ids)
    fetched_evidence = json.loads(packet["inputs"]["fetched-evidence.json"]["content"])
    assert [row["selection_id"] for row in fetched_evidence] == [
        f"verify-shard-{int(tid.rsplit('-', 1)[1])}" for tid in shard_ids]


def test_plan_lens_finalize_raises_when_only_some_select_shards_validated(tmp_path):
    run, _ = _build_run(tmp_path, inflate_lizard_lines=2000)
    planner.plan_judgment(run, context_budget_tokens=6000)
    engine = Engine(run)
    created = {rec.task_id for rec in engine._read_records() if rec.event == "created"}
    shard_ids = sorted(
        (tid for tid in created if tid.startswith("lens-open-lens-select-shard-")),
        key=lambda tid: int(tid.rsplit("-", 1)[1]))
    assert len(shard_ids) >= 2

    # Claim EVERY ready task in one batch (formation, other lens/select
    # tasks, and every open-lens select shard); submit ONLY the first
    # shard -- everything else, including the other open-lens shards,
    # stays claimed-but-outstanding, deliberately never submitted.
    ready = engine.ready_task_ids()
    claimed = engine.claim(len(ready), executor_kind="manual", model="test")
    for item in claimed:
        if item.packet.task_id == shard_ids[0]:
            _submit(engine, item, _SELECT_OUTPUT)

    with pytest.raises(planner.PlannerError, match="not yet validated"):
        planner.plan_lens_finalize(run, "lens-open-lens")


def test_formation_instructions_carry_synthesis_md_granularity_rules(tmp_path):
    instructions = planner._formation_instructions()
    assert "Form modules from candidates" in instructions
    assert "not by itself a business module" in instructions  # granularity contract bullet


def test_dedup_rank_instructions_carry_synthesis_md_rank_rules(tmp_path):
    instructions = planner._dedup_rank_instructions()
    assert "Merge same-root-cause findings" in instructions
    assert "blast radius" in instructions
