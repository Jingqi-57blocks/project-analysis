"""The rule-to-gate coverage table (57B-113 / 57B-114, M0).

``synthesis.md`` carries two lists of bolded, non-negotiable rules ("Hard
output rules" and "Hard accuracy rules") — each one fixed a real defect in a
past run and must never regress. This module is the FIDELITY CONTRACT for
the whole 57B-113 orchestrator workstream: every one of those bolded bullets
is mapped here to exactly one of:

  - ``{"gate_kind": "audit-check", "gates": (...)}`` — already enforced
    mechanically by one or more ``overview_audit.py`` check codes;
  - ``{"gate_kind": "validator", "gates": (...)}`` — enforced mechanically by
    one or more of this package's ``validators.py`` functions;
  - ``{"gate_kind": "prose_only", "prose_tag": "<task_type>:<pointer>"}`` — no
    mechanical gate exists (or can honestly exist without deep semantic
    understanding); the rule is carried by careful prompt-following in the
    named task template/section, and enforcement is a human/LLM judgment
    call, not a machine check.

``RULE_GATE_TABLE`` is intentionally an explicit, hand-classified list rather
than something inferred from the rule text — the classification (mechanical
vs. judgment) is the actual engineering decision this table records. The
accompanying pytest (``test_orchestrator_rule_gate_coverage.py``) parses
``synthesis.md`` fresh on every run and asserts this table stays a complete,
1:1, existence-checked mapping — so if a future editor rewords or adds a
rule in ``synthesis.md`` without updating this table, the test fails loudly
rather than silently losing coverage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import validators
from .. import overview_audit
from .contracts import TASK_TYPES

SYNTHESIS_MD_PATH = Path(__file__).resolve().parents[3] / "synthesis.md"

_OUTPUT_RULES_START = "**Hard output rules"
_OUTPUT_RULES_END = "## The six changeability questions"
_ACCURACY_RULES_START = "## Hard accuracy rules"
_NEXT_HEADING = re.compile(r"^## ", re.MULTILINE)

SECTIONS = ("output", "accuracy")
GATE_KINDS = ("audit-check", "validator", "prose_only")


def _bullet_texts(section_text: str) -> list[str]:
    """Top-level ``- **bold lead-in** ...`` bullets in one section's raw text,
    returning just each bullet's leading bold phrase (its stable key).

    A bullet's bold lead-in occasionally soft-wraps across the bullet's first
    two source lines (synthesis.md is hand-formatted prose, not one-bullet-
    per-line) — so continuation lines are joined with a single space before
    the leading ``**...**`` span is extracted, rather than regexing line by
    line.
    """
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in section_text.splitlines():
        if line.startswith("- "):
            current = [line[2:].strip()]
            blocks.append(current)
        elif current is not None and line.strip():
            current.append(line.strip())
        elif not line.strip():
            continue  # blank line inside a bullet's wrapped continuation
        else:
            current = None
    texts: list[str] = []
    for block in blocks:
        match = re.match(r"^\*\*(.+?)\*\*", " ".join(block))
        if match:
            texts.append(match.group(1))
    return texts


def _extract_sections(text: str) -> dict[str, str]:
    output_start = text.index(_OUTPUT_RULES_START)
    output_body_start = text.index("\n", output_start) + 1
    output_end = text.index(_OUTPUT_RULES_END, output_body_start)

    accuracy_start = text.index(_ACCURACY_RULES_START)
    accuracy_body_start = text.index("\n", accuracy_start) + 1
    next_heading = _NEXT_HEADING.search(text, accuracy_body_start)
    accuracy_end = next_heading.start() if next_heading else len(text)

    return {
        "output": text[output_body_start:output_end],
        "accuracy": text[accuracy_body_start:accuracy_end],
    }


def extract_rule_bullets(text: str) -> dict[str, list[str]]:
    """``{"output": [...bullet keys...], "accuracy": [...bullet keys...]}``
    parsed fresh from ``synthesis.md``'s current text."""
    return {name: _bullet_texts(body) for name, body in _extract_sections(text).items()}


def synthesis_md_text() -> str:
    return SYNTHESIS_MD_PATH.read_text("utf-8")


@dataclass(frozen=True)
class RuleGateEntry:
    section: str            # "output" | "accuracy"
    rule_text: str           # exact bold lead-in text extract_rule_bullets() returns
    gate_kind: str           # "audit-check" | "validator" | "prose_only"
    gates: tuple[str, ...] = ()
    prose_tag: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.section not in SECTIONS:
            raise ValueError(f"rule gate entry section must be one of {SECTIONS}")
        if self.gate_kind not in GATE_KINDS:
            raise ValueError(f"rule gate entry gate_kind must be one of {GATE_KINDS}")
        if self.gate_kind == "prose_only":
            if self.gates:
                raise ValueError("a prose_only entry cannot carry gates")
            if not self.prose_tag or ":" not in self.prose_tag:
                raise ValueError("a prose_only entry needs a '<task_type>:<pointer>' prose_tag")
            if self.prose_tag.split(":", 1)[0] not in TASK_TYPES:
                raise ValueError(
                    f"prose_tag task_type {self.prose_tag.split(':', 1)[0]!r} is not a known task_type")
        else:
            if not self.gates:
                raise ValueError(f"a {self.gate_kind} entry needs at least one gate name")
            if self.prose_tag:
                raise ValueError(f"a {self.gate_kind} entry cannot carry a prose_tag")


def _entry(section: str, rule_text: str, *, gate_kind: str,
          gates: tuple[str, ...] = (), prose_tag: str = "", note: str = "") -> RuleGateEntry:
    return RuleGateEntry(section=section, rule_text=rule_text, gate_kind=gate_kind,
                        gates=gates, prose_tag=prose_tag, note=note)


# --------------------------------------------------------------------------- #
# The table. Every rule_text below is the EXACT string extract_rule_bullets()
# returns for the current synthesis.md — verified by the coverage test, which
# re-parses the file on every run rather than trusting this transcription.
# --------------------------------------------------------------------------- #

RULE_GATE_TABLE: tuple[RuleGateEntry, ...] = (
    # -- Hard output rules (8) -----------------------------------------------
    _entry("output", "No absolute machine paths anywhere", gate_kind="audit-check",
          gates=("revision-and-path-citations",)),
    _entry("output", "Plain Markdown punctuation only.", gate_kind="audit-check",
          gates=("pm-text-integrity", "mermaid-text-integrity")),
    _entry("output", "Run language governs EVERY output of this stage (default `zh-CN`).",
          gate_kind="prose_only", prose_tag="section-generate:all-sections",
          note="Verifying arbitrary generated prose is actually written in the "
               "declared run language is not mechanically checkable in M0."),
    _entry("output", "Use canonical metrics; do not calculate in prose.",
          gate_kind="validator", gates=("numeric_provenance",)),
    _entry("output", "`ui→api` edges need a call-site check:", gate_kind="prose_only",
          prose_tag="lens-findings:ui-api-edges",
          note="Requires reading the actual frontend call site and matching config "
               "binding — semantic, not lexical."),
    _entry("output", 'Derive "Referenced but NOT analyzed"', gate_kind="prose_only",
          prose_tag="section-generate:project-map.md#external-systems"),
    _entry("output",
          "These documents are pipeline output and will be frozen when the stage is "
          "marked done.",
          gate_kind="audit-check", gates=("module-disposition-accounting",),
          note="Covers only the 'disposition counts must sum to the candidate total' "
               "half. The 'every mermaid edge is backed by a relationship-table row' "
               "half has no mechanical gate yet (would need a new mermaid/table "
               "cross-reference validator, out of 57B-114's fixed scope of six "
               "validators) — flagged for a follow-up slice, not silently assumed "
               "covered."),
    _entry("output", "Simplicity is a rule, not a preference.", gate_kind="audit-check",
          gates=("pm-abstraction-boundary",),
          note="Covers the operationalizable half (no tool/scanner names or source "
               "paths in overview.md's main text). 'Prefer plain sentences' / "
               "'minimize what a human must verify' stay judgment calls."),

    # -- Hard accuracy rules (20) ---------------------------------------------
    _entry("accuracy", "Static call paths are code references, never usage.",
          gate_kind="validator", gates=("forbidden_vocabulary",),
          note="Use validators.STATIC_BASIS_OVERREACH_VOCABULARY as the patterns "
               "argument."),
    _entry("accuracy", "An unresolved provider stays unresolved at first mention.",
          gate_kind="prose_only",
          prose_tag="section-generate:technical-overview.md#interface-boundaries"),
    _entry("accuracy", "Evidence basis limits the verb.", gate_kind="prose_only",
          prose_tag="section-generate:all-sections"),
    _entry("accuracy", "Never render absence-of-findings as healthy.",
          gate_kind="validator", gates=("forbidden_vocabulary",),
          note="Covered by the DEFAULT_FORBIDDEN_VOCABULARY wellness-label patterns."),
    _entry("accuracy", "Superlatives are computed, not guessed.", gate_kind="prose_only",
          prose_tag="section-generate:overview.md#section-2"),
    _entry("accuracy",
          'An aggregated "no notable concerns" row must not cover a module that has a '
          "finding.",
          gate_kind="prose_only", prose_tag="section-generate:overview.md#section-13"),
    _entry("accuracy", "Lens coverage status is computed over REQUIRED signals.",
          gate_kind="prose_only", prose_tag="lens-findings:coverage"),
    _entry("accuracy", "One disposition per external candidate.", gate_kind="prose_only",
          prose_tag="section-generate:technical-overview.md#external-disposition",
          note="No wrapper module currently enforces included/unresolved/excluded "
               "accounting for integration (external-system) candidates the way "
               "module_map.py enforces it for MODULE candidates — verified by grep, "
               "not assumed."),
    _entry("accuracy", "Candidate-disposition coverage is not integration completeness.",
          gate_kind="prose_only",
          prose_tag="section-generate:technical-overview.md#external-disposition"),
    _entry("accuracy", "No artificial health scores.", gate_kind="validator",
          gates=("forbidden_vocabulary",),
          note="Covered by the DEFAULT_FORBIDDEN_VOCABULARY composite-score pattern."),
    _entry("accuracy", "No hardcoded domain assumptions.", gate_kind="prose_only",
          prose_tag="section-generate:overview.md#section-3"),
    _entry("accuracy", "A route is not a UI entry.", gate_kind="prose_only",
          prose_tag="lens-findings:ui-route-linkage"),
    _entry("accuracy", "Frontend permission checks are not backend authorization.",
          gate_kind="prose_only", prose_tag="section-generate:overview.md#section-4"),
    _entry("accuracy", "A definition is not activation.", gate_kind="prose_only",
          prose_tag="lens-findings:activation-labels"),
    _entry("accuracy", "Partial repos are not the whole system.", gate_kind="prose_only",
          prose_tag="lens-findings:scope-guard"),
    _entry("accuracy", "Naming is not migration proof.", gate_kind="prose_only",
          prose_tag="section-generate:overview.md#section-3"),
    _entry("accuracy",
          "Module attribution is path-exact; clone and co-change stay separate.",
          gate_kind="prose_only", prose_tag="lens-findings:module-attribution"),
    _entry("accuracy", "A journey's write step cites the store it actually writes.",
          gate_kind="prose_only", prose_tag="section-generate:overview.md#section-5"),
    _entry("accuracy", "Shared-table claims are hedged at FIRST mention.",
          gate_kind="prose_only", prose_tag="section-generate:overview.md#section-2"),
    _entry("accuracy", "Table access does not establish a source of truth.",
          gate_kind="prose_only", prose_tag="section-generate:overview.md#section-8"),
)


def _overview_audit_check_codes() -> set[str]:
    """Every LITERAL (non-f-string) check code passed to ``audit()``'s local
    ``check(code, ...)`` helper in ``overview_audit.py`` — extracted from its
    source text rather than executed, since running the real audit needs a
    full run directory this module never touches."""
    source = Path(overview_audit.__file__).read_text("utf-8")
    return set(re.findall(r'check\(\s*\n?\s*"([^"\n]+)"', source))


def _validator_names() -> set[str]:
    return {name for name in dir(validators) if not name.startswith("_")
            and callable(getattr(validators, name))}


def verify_coverage(text: str | None = None) -> list[str]:
    """Return every coverage problem as a human-readable string (empty = the
    table fully, honestly covers synthesis.md's current bolded hard rules)."""
    problems: list[str] = []
    extracted = extract_rule_bullets(text if text is not None else synthesis_md_text())
    table_by_section: dict[str, dict[str, RuleGateEntry]] = {name: {} for name in SECTIONS}
    for entry in RULE_GATE_TABLE:
        bucket = table_by_section[entry.section]
        if entry.rule_text in bucket:
            problems.append(f"duplicate table entry in {entry.section!r}: {entry.rule_text!r}")
        bucket[entry.rule_text] = entry

    for section in SECTIONS:
        doc_rules = set(extracted.get(section, []))
        table_rules = set(table_by_section[section])
        for missing in sorted(doc_rules - table_rules):
            problems.append(f"synthesis.md {section} rule has no table entry: {missing!r}")
        for stale in sorted(table_rules - doc_rules):
            problems.append(f"table entry not found in synthesis.md {section} rules: {stale!r}")

    audit_codes = _overview_audit_check_codes()
    validator_names = _validator_names()
    for entry in RULE_GATE_TABLE:
        if entry.gate_kind == "audit-check":
            for gate in entry.gates:
                if gate not in audit_codes:
                    problems.append(
                        f"{entry.rule_text!r}: audit check {gate!r} not found in "
                        "overview_audit.py")
        elif entry.gate_kind == "validator":
            for gate in entry.gates:
                if gate not in validator_names:
                    problems.append(
                        f"{entry.rule_text!r}: validator {gate!r} not found in validators.py")
    return problems
