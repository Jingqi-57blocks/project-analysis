"""Judgment-DAG planner tests (57B-113 / 57B-116, M2): DAG shape on a
synthetic prepared run dir (task count, deps, shard fan-out, budget
sharding), the two-phase dedup planning split, and idempotent re-planning."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper import identity
from analysis_wrapper.orchestrator import planner
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
        "schema_version": "2.0.0",
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
    _view("scc", "web", "JavaScript 5 50\n")
    _view("git-history", "web", "churn: index.js 2\n")
    # a cross-repo view, attributed to a single "primary" member (api) --
    # mirrors cli.py's own jscpd_multi attribution (members[0]).
    _view("jscpd-cross", "api", "clone: web/a.js <-> api/b.js\n")
    # a signal present but not complete/partial -- must never be pulled in.
    view_rows.append({"tool": "osv-scanner", "repository_ref": "api", "status": "skipped",
                      "reason": "network lane not authorized", "view": "", "manifest": ""})

    (signals / "run-summary.json").write_text(json.dumps({
        "schema_version": "2.0.0", "aggregate_status": "complete", "signals": view_rows,
    }), "utf-8")

    (run / "module-candidates.json").write_text(json.dumps({
        "schema_version": "2.0.0", "project_ref": mapping.project.reference,
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
    boundary_tasks = [t for t in planned if t.task_type == "boundary-resolution"]
    assert len(boundary_tasks) == 1
    assert boundary_tasks[0].task_id == "boundary-resolution"
    assert boundary_tasks[0].shard == ""

    # 4 repo-sharded lenses x 2 repos + 5 workspace-sharded lenses = 13.
    assert len(lens_tasks) == 4 * 2 + 5
    by_lens = {}
    for task in lens_tasks:
        by_lens.setdefault(task.lens_id, []).append(task)
    assert set(by_lens) == REPO_SHARDED_LENSES | WORKSPACE_SHARDED_LENSES
    for lens_id in REPO_SHARDED_LENSES:
        assert {t.shard for t in by_lens[lens_id]} == {"repo"}
        assert {t.repository_ref for t in by_lens[lens_id]} == {"api", "web"}
        assert len(by_lens[lens_id]) == 2
    for lens_id in WORKSPACE_SHARDED_LENSES:
        assert len(by_lens[lens_id]) == 1
        assert by_lens[lens_id][0].shard == "workspace"
        assert by_lens[lens_id][0].repository_ref == ""

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


def test_boundary_resolution_has_no_depends_on_and_runs_independently(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    # Independent of every lens task -- it must be ready immediately,
    # alongside every (unclaimed) lens task, with nothing claimed yet.
    assert "boundary-resolution" in engine.ready_task_ids()


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
    doc = {"schema_version": "2.0.0", "project_ref": "proj", "aggregate_status": "complete",
          "capabilities": [{"capability_id": "routing", "status": "complete"}]}
    rows, meta = _split_capabilities(doc)
    assert rows == [{"capability_id": "routing", "status": "complete"}]
    assert meta == {"schema_version": "2.0.0", "project_ref": "proj",
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
                      output_schema_id="lens-findings.v1", context_budget_tokens=3000)
    assert len(packets) > 1
    assert all("-shard-" in packet.task_id for packet in packets)
    assert all(_packet_tokens(packet) <= 3000 for packet in packets)
    # graph-meta.json (the small sibling) rides on every shard, unsplit.
    for packet in packets:
        assert "graph-meta.json" in packet.inputs


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

    tight = compose(task_id="t", template_id="t", template_version=template.version,
                    task_type="lens-findings", instructions=instructions, inputs=inputs,
                    output_schema_id="lens-findings.v1", context_budget_tokens=2000)
    assert len(tight) > 1, "expected the inflated view to force composer sharding"
    assert all("-shard-" in packet.task_id for packet in tight)
    assert all(_packet_tokens(packet) <= 2000 for packet in tight)


# --------------------------------------------------------------------------- #
# two-phase dedup planning
# --------------------------------------------------------------------------- #

# boundary-resolution has no depends_on -- it is ALWAYS ready alongside
# every lens task from the moment plan_judgment registers it, so a plain
# ``engine.claim(1)`` (sorted task_id order) can offer it ahead of whatever
# lens task a test cares about ("boundary-resolution" < "lens-..."
# alphabetically). The helpers below always claim a whole READY BATCH at
# once and dispatch each claimed item by its own task_type/task_id, so no
# test ever depends on the engine's claim ordering.

_BOUNDARY_PLACEHOLDER_OUTPUT = {"dispositions": [
    {"candidate_id": "mc-placeholder", "disposition": "unresolved", "module_ids": [],
     "reason": "placeholder -- these planner tests do not exercise boundary-resolution's "
               "own content"},
]}


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


def _submit(engine, item, output, *, status="ok"):
    at = now_iso()
    result = TaskResult(
        task_id=item.packet.task_id, status=status, output=output,
        executor=ExecutorInfo(kind="manual", model="test", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.1),
        tokens=None, validation=ValidationOutcome(passed=(status == "ok"), failures=(
            () if status == "ok" else ({"check": "x", "detail": "y", "location": ""},))),
        attempt=item.attempt)
    return engine.submit(item.packet.task_id, result.to_dict())


def _validate_all_lens_tasks(run, planned):
    """Claim and validate every ready task in one batch (every lens task plus
    boundary-resolution are mutually independent, so all become ready at
    once): real content for each lens task, a placeholder for
    boundary-resolution."""
    engine = Engine(run)
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    assert {item.packet.task_id for item in claimed} == set(
        task.task_id for task in planned)
    for item in claimed:
        if item.packet.task_type == "lens-findings":
            outcome = _submit(engine, item, _lens_output(item.packet.task_id))
            assert outcome["status"] == "validated", outcome
        else:
            _submit(engine, item, _BOUNDARY_PLACEHOLDER_OUTPUT)
    return engine


def test_plan_dedup_refuses_before_any_lens_task_validated(tmp_path):
    # plan_judgment was never even called -- no ledger, nothing validated.
    run, _ = _build_run(tmp_path)
    with pytest.raises(planner.PlannerError, match="no validated lens-findings"):
        planner.plan_dedup(run)


def test_plan_dedup_refuses_while_a_lens_task_is_still_pending(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    # Validate the whole ready batch except deliberately leave ONE
    # lens-findings task un-submitted (still claimed/outstanding).
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    lens_items = [item for item in claimed if item.packet.task_type == "lens-findings"]
    left_pending = lens_items[0].packet.task_id
    for item in claimed:
        if item.packet.task_id == left_pending:
            continue
        if item.packet.task_type == "lens-findings":
            _submit(engine, item, _lens_output(item.packet.task_id))
        else:
            _submit(engine, item, _BOUNDARY_PLACEHOLDER_OUTPUT)
    with pytest.raises(planner.PlannerError, match="still pending"):
        planner.plan_dedup(run)


def test_plan_dedup_composes_from_every_validated_lens_output(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    lens_ids = _lens_task_ids(planned)
    _validate_all_lens_tasks(run, planned)

    task = planner.plan_dedup(run)
    assert task.task_id == "dedup-rank"
    assert task.created is True

    engine = Engine(run)
    records = engine._read_records()
    created = next(rec for rec in records
                  if rec.event == "created" and rec.task_id == "dedup-rank")
    packet = created.detail["task"]
    assert set(packet["depends_on"]) == lens_ids
    finding_ids = json.loads(packet["inputs"]["input-finding-ids.json"]["content"])
    assert sorted(finding_ids) == sorted(f"finding-{tid}" for tid in lens_ids)


def test_plan_dedup_survives_a_permanently_failed_lens_shard(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    engine = Engine(run)
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    doomed = next(item.packet.task_id for item in claimed
                 if item.packet.task_type == "lens-findings")
    for item in claimed:
        if item.packet.task_id == doomed:
            _submit(engine, item, {"findings": "not-a-list", "coverage": []})
        elif item.packet.task_type == "lens-findings":
            _submit(engine, item, _lens_output(item.packet.task_id))
        else:
            _submit(engine, item, _BOUNDARY_PLACEHOLDER_OUTPUT)

    # doomed's first attempt failed (malformed) but is not yet exhausted; it
    # is the ONLY thing ready now (everything else already validated) --
    # fail it twice more to permanently exhaust it.
    for _ in range(2):
        retry = engine.claim(1, executor_kind="manual", model="test")
        assert retry and retry[0].packet.task_id == doomed
        _submit(engine, retry[0], {"findings": "not-a-list", "coverage": []})
    assert engine.task_states()[doomed] == "failed"

    task = planner.plan_dedup(run)
    assert task.created is True
    created = next(rec for rec in engine._read_records()
                  if rec.event == "created" and rec.task_id == "dedup-rank")
    assert doomed not in set(created.detail["task"]["depends_on"])


def test_plan_dedup_rejects_colliding_finding_ids_across_lens_outputs(tmp_path):
    run, _ = _build_run(tmp_path)
    planner.plan_judgment(run)
    engine = Engine(run)
    claimed = engine.claim(len(engine.ready_task_ids()), executor_kind="manual", model="test")
    collision = "finding-duplicate-across-lenses"
    for item in claimed:
        if item.packet.task_type == "lens-findings":
            output = _lens_output(item.packet.task_id)
            output["findings"][0]["finding_id"] = collision
            outcome = _submit(engine, item, output)
            assert outcome["status"] == "validated"
        else:
            _submit(engine, item, _BOUNDARY_PLACEHOLDER_OUTPUT)
    with pytest.raises(planner.PlannerError, match="globally unique"):
        planner.plan_dedup(run)


def test_plan_dedup_is_idempotent(tmp_path):
    run, _ = _build_run(tmp_path)
    planned = planner.plan_judgment(run)
    _validate_all_lens_tasks(run, planned)
    first = planner.plan_dedup(run)
    assert first.created is True
    second = planner.plan_dedup(run)
    assert second.created is False


def test_boundary_resolution_instructions_carry_synthesis_md_granularity_rules(tmp_path):
    instructions = planner._boundary_resolution_instructions()
    assert "Form modules from candidates" in instructions
    assert "not by itself a business module" in instructions  # granularity contract bullet


def test_dedup_rank_instructions_carry_synthesis_md_rank_rules(tmp_path):
    instructions = planner._dedup_rank_instructions()
    assert "Merge same-root-cause findings" in instructions
    assert "blast radius" in instructions
