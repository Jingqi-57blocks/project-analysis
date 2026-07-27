"""Incremental finalize-findings failure surface (57B-116, M2) —
domain-neutral fixtures only. The old (flag-absent) path must stay untouched;
see test_overview_contracts.py for that path's own pre-existing coverage,
duplicated here only where needed to prove parity with the new mode.
"""

import json

import pytest

from analysis_wrapper import findings, module_map, workspace_metrics
from analysis_wrapper.cli import main
from analysis_wrapper.system_model import assemble as sm
from system_model_fixtures import write_run


def _prepared(run):
    signals = run / "signals"
    signals.mkdir()
    manifest_name = "structure-api.manifest.json"
    (signals / manifest_name).write_text(json.dumps({
        "schema_version": "2.0.0",
        "tool": "structure", "status": "complete",
        "repos": [{"repository_ref": "api"}],
    }), "utf-8")
    (signals / "x.view.txt").write_text("items: 1\n", "utf-8")
    (signals / "run-summary.json").write_text(json.dumps({
        "schema_version": "2.0.0",
        "aggregate_status": "complete",
        "signals": [{"tool": "structure", "repository_ref": "api",
                     "status": "complete", "reason": "", "view": "x.view.txt",
                     "manifest": manifest_name}],
    }), "utf-8")
    imports = run / "imports"
    imports.mkdir(exist_ok=True)
    maps = sorted(imports.glob("*.json"))
    (imports / "depmap-coverage.json").write_text(json.dumps({
        "schema_version": "2.0.0",
        "scan_date": "2026-02-02",
        "repos": [{"repository_ref": "web", "lane": "js",
                   "status": "complete", "map_file": maps[0].name, "units": 1}]
        if maps else [],
    }), "utf-8")
    model = sm.assemble(run)
    sm.dump(model, run)
    module_map.write_candidates(run, model.to_dict())
    workspace_metrics.write(run)
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


def _valid_finding(finding_id="finding-good"):
    return {
        "finding_id": finding_id,
        "claim": "The sample boundary has observable change friction.",
        "lens": "structure-inventory",
        "affected_modules": ["sample-capability"],
        "evidence": [{
            "fact": "The bounded structure signal contains one observed item.",
            "refs": ["signals/x.view.txt:1"],
            "basis": "static-reference",
        }],
        "evidence_basis": ["static-reference"],
        "impact": "A change crosses the observed boundary.",
        "priority": "medium", "confidence": "medium",
        "limitations": "Static evidence does not establish runtime frequency.",
        "suggested_direction": "Clarify the boundary before changing it.",
    }


def _write_findings(run, rows):
    (run / "findings.json").write_text(json.dumps(
        {"schema_version": findings.SCHEMA_VERSION, "findings": rows}), "utf-8")


def test_report_failures_absent_flag_keeps_the_old_behavior_byte_identical(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    _write_findings(run, [_valid_finding()])

    assert main(["finalize-findings", "--run", str(run)]) == 0
    technical_first = (run / findings.TECHNICAL_FILE).read_bytes()
    pm_first = (run / findings.PM_FILE).read_bytes()

    assert main(["finalize-findings", "--run", str(run)]) == 0
    assert (run / findings.TECHNICAL_FILE).read_bytes() == technical_first
    assert (run / findings.PM_FILE).read_bytes() == pm_first
    assert findings.validate_report_failures(run) == {}


def test_validate_report_failures_gathers_every_bad_finding_not_just_the_first(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    bad_priority = dict(_valid_finding("finding-bad-priority"), priority="urgent")
    bad_module = dict(_valid_finding("finding-bad-module"),
                      affected_modules=["no-such-module"])
    _write_findings(run, [_valid_finding("finding-good"), bad_priority, bad_module])

    failures = findings.validate_report_failures(run)
    assert set(failures) == {"finding-bad-priority", "finding-bad-module"}
    assert "priority" in failures["finding-bad-priority"][0]["detail"]
    assert "finalized module" in failures["finding-bad-module"][0]["detail"]
    for rows in failures.values():
        for row in rows:
            assert set(row) == {"check", "detail", "location"}

    # The old, still-untouched raising path only ever surfaces the FIRST
    # problem across the whole document -- the improvement this slice adds
    # is exactly the gap between these two calls.
    with pytest.raises(ValueError):
        findings.validate(run)


def test_report_failures_mode_keys_a_malformed_row_by_synthetic_index(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    _write_findings(run, [_valid_finding("finding-good"), "oops-not-an-object"])

    failures = findings.validate_report_failures(run)
    assert set(failures) == {"<findings[1]>"}


def test_finalize_findings_cli_report_failures_mode(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    bad_priority = dict(_valid_finding("finding-bad-priority"), priority="urgent")
    _write_findings(run, [_valid_finding("finding-good"), bad_priority])

    report_path = tmp_path / "failures.json"
    exit_code = main(["finalize-findings", "--run", str(run),
                      "--report-failures", str(report_path)])
    assert exit_code == 3
    result = json.loads(report_path.read_text())
    assert set(result) == {"finding-bad-priority"}
    # report-failures mode never renders the protected findings blocks.
    assert not (run / findings.TECHNICAL_FILE).exists()
    assert not (run / findings.PM_FILE).exists()


def test_finalize_findings_cli_report_failures_mode_exit_zero_when_clean(tmp_path):
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    _write_findings(run, [_valid_finding()])

    report_path = tmp_path / "failures.json"
    assert main(["finalize-findings", "--run", str(run),
                "--report-failures", str(report_path)]) == 0
    assert json.loads(report_path.read_text()) == {}


def test_report_failures_gathers_every_problem_within_one_finding(tmp_path):
    """A repairer must be able to fix a finding in ONE pass: reporting only
    its first bad ref cost a live acceptance run several avoidable repair
    rounds (57B-116)."""
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    row = _valid_finding("finding-many-problems")
    row["priority"] = "urgent"                      # independent problem 1
    row["affected_modules"] = ["no-such-module"]    # independent problem 2
    row["evidence"] = [{
        "fact": "Two refs in one row are both unusable.",
        "refs": ["signals/nope-a.view.txt:1", "signals/nope-b.view.txt:1"],
        "basis": "static-reference",
    }]                                              # independent problems 3 and 4
    _write_findings(run, [row])

    details = [entry["detail"]
               for entry in findings.validate_report_failures(run)["finding-many-problems"]]
    assert any("priority" in detail for detail in details)
    assert any("finalized module" in detail for detail in details)
    assert sum("nope-a" in detail for detail in details) == 1
    assert sum("nope-b" in detail for detail in details) == 1, (
        "the SECOND bad ref must be reported too, not hidden behind the first")


def test_report_failures_skips_aggregate_checks_that_would_report_derived_nonsense(tmp_path):
    """An unresolvable ref undercounts the independent-signal set, and a
    rejected basis undercounts the basis set: neither may be turned into a
    bogus high-confidence rejection or evidence_basis mismatch, which would
    send a repairer after a problem that does not exist."""
    run = _prepared(write_run(tmp_path / "run", with_imports=True))
    _complete_map(run)
    row = _valid_finding("finding-cascade")
    row["confidence"] = "high"
    row["evidence"] = [
        {"fact": "One resolvable ref.", "refs": ["signals/x.view.txt:1"],
         "basis": "static-reference"},
        {"fact": "One unresolvable ref.", "refs": ["signals/nope.view.txt:1"],
         "basis": "declaration"},
    ]
    row["evidence_basis"] = ["declaration", "static-reference"]
    _write_findings(run, [row])

    details = [entry["detail"]
               for entry in findings.validate_report_failures(run)["finding-cascade"]]
    assert any("nope" in detail for detail in details)
    assert not any("two independent signals" in detail for detail in details)
    assert not any("evidence_basis" in detail for detail in details)
