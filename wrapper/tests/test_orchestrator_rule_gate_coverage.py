"""The rule->gate coverage table is the fidelity contract for the whole
57B-113 orchestrator workstream (57B-114 M0): every bolded hard rule in
synthesis.md must map to exactly one table entry, every referenced gate must
actually exist, and every prose_only entry must point at a real task type.

This test re-parses synthesis.md FRESH on every run rather than trusting the
table's transcription, so a future edit to synthesis.md that adds, removes,
or rewords a bolded rule fails this test loudly instead of silently losing
coverage.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator import rule_gate
from analysis_wrapper.orchestrator.contracts import TASK_TYPES


def test_synthesis_md_is_readable_and_has_the_two_rule_sections():
    text = rule_gate.synthesis_md_text()
    assert "**Hard output rules" in text
    assert "## Hard accuracy rules" in text


def test_extraction_finds_a_non_trivial_bullet_count_in_each_section():
    extracted = rule_gate.extract_rule_bullets(rule_gate.synthesis_md_text())
    assert len(extracted["output"]) >= 5
    assert len(extracted["accuracy"]) >= 15
    # Every extracted bullet key is non-empty prose, not stray markdown noise.
    for section in rule_gate.SECTIONS:
        for text in extracted[section]:
            assert text.strip() == text
            assert text


def test_rule_gate_table_covers_every_synthesis_md_rule_bidirectionally():
    problems = rule_gate.verify_coverage()
    assert problems == []


def test_rule_gate_table_has_no_duplicate_entries_per_section():
    seen: dict[str, set[str]] = {name: set() for name in rule_gate.SECTIONS}
    for entry in rule_gate.RULE_GATE_TABLE:
        assert entry.rule_text not in seen[entry.section], (
            f"duplicate table entry: {entry.section}/{entry.rule_text!r}")
        seen[entry.section].add(entry.rule_text)


def test_every_gate_name_referenced_by_the_table_actually_exists():
    audit_codes = rule_gate._overview_audit_check_codes()
    validator_names = rule_gate._validator_names()
    assert audit_codes  # sanity: extraction actually found check codes
    assert validator_names  # sanity: validators.py exports callables
    for entry in rule_gate.RULE_GATE_TABLE:
        if entry.gate_kind == "audit-check":
            for gate in entry.gates:
                assert gate in audit_codes, f"{entry.rule_text!r}: missing audit check {gate!r}"
        elif entry.gate_kind == "validator":
            for gate in entry.gates:
                assert gate in validator_names, f"{entry.rule_text!r}: missing validator {gate!r}"


def test_every_prose_only_entry_names_a_known_task_type():
    for entry in rule_gate.RULE_GATE_TABLE:
        if entry.gate_kind == "prose_only":
            task_type = entry.prose_tag.split(":", 1)[0]
            assert task_type in TASK_TYPES, (
                f"{entry.rule_text!r}: prose_tag {entry.prose_tag!r} names an unknown task_type")


def test_rule_gate_entry_construction_is_fail_closed():
    with pytest.raises(ValueError, match="gate_kind must be one of"):
        rule_gate.RuleGateEntry(section="output", rule_text="x", gate_kind="not-a-kind")
    with pytest.raises(ValueError, match="cannot carry gates"):
        rule_gate.RuleGateEntry(section="output", rule_text="x", gate_kind="prose_only",
                                gates=("some-gate",), prose_tag="lens-findings:x")
    with pytest.raises(ValueError, match="prose_tag"):
        rule_gate.RuleGateEntry(section="output", rule_text="x", gate_kind="prose_only")
    with pytest.raises(ValueError, match="is not a known task_type"):
        rule_gate.RuleGateEntry(section="output", rule_text="x", gate_kind="prose_only",
                                prose_tag="not-a-task-type:x")
    with pytest.raises(ValueError, match="needs at least one gate name"):
        rule_gate.RuleGateEntry(section="output", rule_text="x", gate_kind="validator")
    with pytest.raises(ValueError, match="cannot carry a prose_tag"):
        rule_gate.RuleGateEntry(section="output", rule_text="x", gate_kind="validator",
                                gates=("numeric_provenance",), prose_tag="lens-findings:x")


def test_verify_coverage_flags_a_missing_table_entry():
    # A rule text that is not in the table (synthetically injected) reports a
    # "no table entry" problem via the same extraction/lookup path the real
    # coverage test uses, without needing to actually edit synthesis.md.
    fake_text = (
        rule_gate.synthesis_md_text().replace(
            "No absolute machine paths anywhere",
            "No absolute machine paths anywhere at all here",
        )
    )
    problems = rule_gate.verify_coverage(fake_text)
    assert any("no table entry" in problem for problem in problems)
    assert any("not found in synthesis.md" in problem for problem in problems)
