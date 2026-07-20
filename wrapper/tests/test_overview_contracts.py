"""Effort-independent overview contracts — domain-neutral fixtures only."""

import json

import pytest

from analysis_wrapper import (capabilities, coverage_render, module_map,
                              module_render, overview_audit, synthesis_input)
from analysis_wrapper.system_model import assemble as sm
from system_model_fixtures import write_run


def _prepared(run):
    signals = run / "signals"
    signals.mkdir()
    manifest_name = "structure-api.manifest.json"
    (signals / manifest_name).write_text(json.dumps({
        "tool": "structure", "status": "complete",
        "repos": [{"repo_id": "api-11111111"}],
    }), "utf-8")
    (signals / "x.view.txt").write_text("items: 1\n", "utf-8")
    (signals / "run-summary.json").write_text(json.dumps({
        "aggregate_status": "complete",
        "signals": [{"tool": "structure", "repo_id": "api-11111111",
                     "status": "complete", "reason": "", "view": "x.view.txt",
                     "manifest": manifest_name}],
    }), "utf-8")
    # The fixture's dependency map intentionally has no coverage sidecar; add
    # a truthful one for the canonical pipeline contract.
    imports = run / "imports"
    imports.mkdir(exist_ok=True)
    maps = sorted(imports.glob("*.json"))
    (imports / "depmap-coverage.json").write_text(json.dumps({
        "scan_date": "2026-02-02",
        "repos": [{"repo_id": "web-22222222", "lane": "js",
                   "status": "complete", "map_file": maps[0].name, "units": 1}]
        if maps else [],
    }), "utf-8")
    model = sm.assemble(run)
    sm.dump(model, run)
    module_map.write_candidates(run, model.to_dict())
    capabilities.write(run)
    coverage_render.write(run)
    synthesis_input.write(run)
    return run


def _complete_map(run):
    candidates = json.loads((run / "module-candidates.json").read_text())
    ids = [row["candidate_id"] for row in candidates["candidates"]]
    payload = {
        "schema_version": module_map.MAP_SCHEMA_VERSION,
        "modules": [{"module_id": "sample-capability", "name": "Sample capability",
                     "classification": "business", "confidence": "medium",
                     "aliases": []}],
        "candidate_dispositions": [
            {"candidate_id": candidate_id, "disposition": "merged",
             "module_ids": ["sample-capability"], "reason": "shared evidence boundary"}
            for candidate_id in ids],
    }
    (run / "module-map.json").write_text(json.dumps(payload), "utf-8")
    return len(ids)


def test_capability_manifest_and_packet_are_byte_deterministic(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    first_cap = capabilities.write(run).read_bytes()
    first_packet = synthesis_input.write(run).read_bytes()
    assert capabilities.write(run).read_bytes() == first_cap
    assert synthesis_input.write(run).read_bytes() == first_packet
    statuses = {row["capability_id"]: row["status"]
                for row in json.loads(first_cap)["capabilities"]}
    assert statuses["callgraph"] == "complete"
    assert statuses["dependency-map"] == "complete"


def test_ui_linkage_is_not_applicable_for_go_only_backend(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True,
                              route_liveness=False))
    report = json.loads((run / "discovery-report.json").read_text())
    report["repos"] = [row for row in report["repos"]
                       if row["repo_id"] == "api-11111111"]
    (run / "discovery-report.json").write_text(json.dumps(report), "utf-8")
    states = {row["capability_id"]: row["status"]
              for row in capabilities.build(run)["capabilities"]}
    assert states["route-inventory"] == "unavailable"
    assert states["ui-route-linkage"] == "not-applicable"


def test_full_stack_node_shape_never_claims_ui_not_applicable(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True,
                              route_liveness=False))
    report = json.loads((run / "discovery-report.json").read_text())
    api = next(row for row in report["repos"]
               if row["repo_id"] == "api-11111111")
    api["stacks"] = {"stacks": ["ts", "tsx"], "frameworks": [], "evidence": []}
    api["module_signals"]["folders"] = ["src"]
    report["repos"] = [api]
    (run / "discovery-report.json").write_text(json.dumps(report), "utf-8")
    states = {row["capability_id"]: row["status"]
              for row in capabilities.build(run)["capabilities"]}
    assert states["ui-route-linkage"] == "unavailable"


def test_every_candidate_must_be_dispositioned_exactly_once(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    count = _complete_map(run)
    candidates, mapping = module_map.validate(run)
    assert len(mapping["candidate_dispositions"]) == count
    mapping["candidate_dispositions"].pop()
    (run / "module-map.json").write_text(json.dumps(mapping), "utf-8")
    with pytest.raises(ValueError, match="omits"):
        module_map.validate(run)


def _rule_map(run, rules):
    payload = {
        "schema_version": module_map.MAP_SCHEMA_VERSION,
        "modules": [{"module_id": "sample-capability", "name": "Sample capability",
                     "classification": "business", "confidence": "medium",
                     "aliases": []}],
        "candidate_dispositions": [],
        "candidate_rules": rules,
    }
    (run / "module-map.json").write_text(json.dumps(payload), "utf-8")


def test_compact_candidate_rules_expand_to_canonical_lineage(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    candidates = json.loads((run / "module-candidates.json").read_text())["candidates"]
    _rule_map(run, [{
        "rule_id": "all-sample-candidates",
        "selectors": [{"candidate_ids": [row["candidate_id"] for row in candidates]}],
        "disposition": "merged", "module_ids": ["sample-capability"],
        "reason": "one evidence-backed fixture boundary",
    }])
    module_map.expand_candidate_rules(run)
    written = json.loads((run / "module-map.json").read_text())
    assert "candidate_rules" not in written
    assert [row["candidate_id"] for row in written["candidate_dispositions"]] == sorted(
        row["candidate_id"] for row in candidates)
    _, mapping = module_map.validate(run)
    assert len(mapping["candidate_dispositions"]) == len(candidates)


def test_candidate_rules_reject_overlap_and_omission(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    candidate_ids = [row["candidate_id"] for row in json.loads(
        (run / "module-candidates.json").read_text())["candidates"]]
    duplicate = {
        "selectors": [{"candidate_ids": candidate_ids}],
        "disposition": "merged", "module_ids": ["sample-capability"],
        "reason": "fixture boundary",
    }
    _rule_map(run, [dict(duplicate, rule_id="first-rule"),
                    dict(duplicate, rule_id="second-rule")])
    with pytest.raises(ValueError, match="matched multiple"):
        module_map.expand_candidate_rules(run)

    _rule_map(run, [{
        "rule_id": "partial-rule",
        "selectors": [{"candidate_ids": candidate_ids[:1]}],
        "disposition": "merged", "module_ids": ["sample-capability"],
        "reason": "partial fixture boundary",
    }])
    with pytest.raises(ValueError, match="omit"):
        module_map.expand_candidate_rules(run)


def test_candidate_rules_allow_an_honest_unresolved_remainder(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    candidate_ids = [row["candidate_id"] for row in json.loads(
        (run / "module-candidates.json").read_text())["candidates"]]
    _rule_map(run, [{
        "rule_id": "known-boundary",
        "selectors": [{"candidate_ids": candidate_ids[:1]}],
        "disposition": "merged", "module_ids": ["sample-capability"],
        "reason": "direct fixture boundary evidence",
    }, {
        "rule_id": "unresolved-remainder", "remaining": True,
        "disposition": "unresolved", "module_ids": [],
        "reason": "available evidence does not justify a stable boundary",
    }])
    module_map.expand_candidate_rules(run)
    _, mapping = module_map.validate(run)
    counts = {}
    for row in mapping["candidate_dispositions"]:
        counts[row["disposition"]] = counts.get(row["disposition"], 0) + 1
    assert counts == {"merged": 1, "unresolved": len(candidate_ids) - 1}


def test_candidate_rule_selectors_are_structural_and_fail_closed():
    candidate = {
        "candidate_id": "mc-123", "repo_id": "api-11111111",
        "signal_kind": "route", "value": "/items/:id",
        "evidence": [f"api-11111111@{'a' * 40}:internal/items.go:12"],
        "node_ids": ["route:abc"],
    }
    assert module_map._selector_matches({
        "repo_ids": ["api-11111111"], "signal_kinds": ["route"],
        "value_prefixes": ["/items"],
        "evidence_path_prefixes": ["internal/"],
        "node_ids": ["route:abc"],
    }, candidate)
    with pytest.raises(ValueError, match="unsupported fields"):
        module_map._selector_matches({"business_keywords": ["items"]}, candidate)
    with pytest.raises(ValueError, match="cannot be empty"):
        module_map._selector_matches({}, candidate)


def test_valid_module_map_materializes_inferred_nodes_and_lineage(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    count = _complete_map(run)
    model = sm.assemble(run)
    modules = [node for node in model.nodes if node.kind == "module"]
    assert len(modules) == 1
    assert modules[0].status == "inferred"
    assert modules[0].evidence_basis == "inferred-linkage"
    assert len(modules[0].attrs["candidate_ids"]) == count
    assert model.coverage["modules"]["status"] == "complete"


def test_audit_rejects_misplaced_artifact_and_accepts_canonical_consumption(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    result = overview_audit.audit(run)
    assert result["status"] == "passed"
    (run / "signals" / "callgraph-coverage.json").write_text("{}", "utf-8")
    result = overview_audit.audit(run)
    failed = {row["check"] for row in result["checks"] if row["status"] == "fail"}
    assert "canonical-placement" in failed


def test_model_edges_and_nodes_carry_evidence_basis(tmp_path):
    model = sm.assemble(write_run(tmp_path / "run", with_imports=True))
    assert model.nodes and model.edges
    assert all(node.evidence_basis for node in model.nodes)
    assert all(edge.evidence_basis for edge in model.edges)


def test_final_audit_requires_exact_machine_coverage_block(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    sm.dump(sm.assemble(run), run)
    module_render.write(run)
    synthesis_input.write(run)
    (run / "project-map.md").write_text("# Map\n\n" + module_render.render(run), "utf-8")
    (run / "overview.md").write_text("# Overview\n", "utf-8")
    (run / "technical-overview.md").write_text(
        "# Technical\n\n" + coverage_render.render(run), "utf-8")
    assert overview_audit.audit(
        run, require_module_map=True, require_reports=True)["status"] == "passed"
    (run / "technical-overview.md").write_text(
        "# Technical\n\n" + coverage_render.render(run).replace(
            "`callgraph` | `complete`", "`callgraph` | `unavailable`"), "utf-8")
    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    assert any(row["check"] == "machine-capability-coverage"
               and row["status"] == "fail" for row in result["checks"])


def test_final_audit_rejects_plain_source_path_in_pm_overview(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    sm.dump(sm.assemble(run), run)
    module_render.write(run)
    synthesis_input.write(run)
    (run / "project-map.md").write_text("# Map\n\n" + module_render.render(run), "utf-8")
    file_label = next(node["label"] for node in json.loads(
        (run / "system-model.json").read_text())["nodes"] if node["kind"] == "file")
    (run / "overview.md").write_text(f"# Overview\n\nObserved in {file_label}.\n", "utf-8")
    (run / "technical-overview.md").write_text(
        "# Technical\n\n" + coverage_render.render(run), "utf-8")
    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    assert any(row["check"] == "pm-abstraction-boundary"
               and row["status"] == "fail" for row in result["checks"])


def test_pm_abstraction_path_labels_do_not_treat_package_words_as_paths():
    for label in (".", "unknown", "config", "init", "path"):
        assert not overview_audit._is_source_path_label(label)
    for label in ("src/client.ts", "internal/handler", "app.js", "Dockerfile"):
        assert overview_audit._is_source_path_label(label)


def test_final_audit_rejects_html_entity_obfuscation(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    sm.dump(sm.assemble(run), run)
    module_render.write(run)
    synthesis_input.write(run)
    (run / "project-map.md").write_text("# Map\n\n" + module_render.render(run), "utf-8")
    (run / "overview.md").write_text("# Overv&#105;ew\n", "utf-8")
    (run / "technical-overview.md").write_text(
        "# Technical\n\n" + coverage_render.render(run), "utf-8")
    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    assert any(row["check"] == "pm-text-integrity" and row["status"] == "fail"
               for row in result["checks"])


@pytest.mark.parametrize("bad_text", [
    "[details](technical-overview%2Emd)",
    "```mermaid\nflowchart LR\nA -; unresolved ;-> B\n```",
    "```mermaid\nflowchart LR\nA -->|unterminated B\n```",
])
def test_final_audit_rejects_encoded_links_and_malformed_mermaid(tmp_path, bad_text):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    sm.dump(sm.assemble(run), run)
    module_render.write(run)
    synthesis_input.write(run)
    (run / "project-map.md").write_text("# Map\n\n" + module_render.render(run), "utf-8")
    (run / "overview.md").write_text("# Overview\n\n" + bad_text + "\n", "utf-8")
    (run / "technical-overview.md").write_text(
        "# Technical\n\n" + coverage_render.render(run), "utf-8")
    result = overview_audit.audit(run, require_module_map=True, require_reports=True)
    assert result["status"] == "failed"
    assert any(row["check"] in {"pm-text-integrity", "mermaid-text-integrity"}
               and row["status"] == "fail" for row in result["checks"])


def test_synthesis_packet_bounds_large_inventories_with_disclosure(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    doc = json.loads((run / "module-candidates.json").read_text())
    doc["candidates"] = [
        {"candidate_id": f"mc-{i:04d}", "repo_id": "api-11111111",
         "signal_kind": "folder", "value": f"part-{i}",
         "evidence": [], "node_ids": []}
        for i in range(750)]
    doc["candidate_count"] = len(doc["candidates"])
    (run / "module-candidates.json").write_text(json.dumps(doc), "utf-8")
    packet = synthesis_input.build(run)
    candidates = packet["module_candidates"]
    assert candidates["total_count"] == 750
    assert candidates["included_count"] == 500
    assert candidates["truncated"] is True
    assert candidates["full_universe_required_for_module_map"] is True


def test_evidence_backed_added_candidate_is_accounted_and_materialized(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    mapping = json.loads((run / "module-map.json").read_text())
    mapping["additional_candidates"] = [{
        "candidate_id": "mc-added-cross-cutting",
        "repo_id": "api-11111111",
        "value": "cross-cutting",
        "evidence": [f"api-11111111@{'a' * 40}:internal/x.go:1"],
        "node_ids": [],
    }]
    mapping["candidate_dispositions"].append({
        "candidate_id": "mc-added-cross-cutting", "disposition": "merged",
        "module_ids": ["sample-capability"], "reason": "cited cross-cutting boundary",
    })
    (run / "module-map.json").write_text(json.dumps(mapping), "utf-8")
    candidates, _ = module_map.validate(run)
    assert candidates["additional_candidate_count"] == 1
    model = sm.assemble(run)
    module = next(node for node in model.nodes if node.kind == "module")
    assert "mc-added-cross-cutting" in module.attrs["candidate_ids"]
