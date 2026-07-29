"""Lens template loader for the orchestrator's judgment-planning stage
(57B-113 / 57B-116, M2).

Each of the nine ``lenses/*.md`` files now starts with a small YAML
frontmatter block (``shard: repo | workspace``, ``signals: [tool, ...]``,
each with an inline comment justifying the choice against the lens's own
prose and ``lenses/coverage-map.json``'s tested tool catalog -- see those
files for the reasoning). This module is the ONE place that parses that
frontmatter, so a future lens edit only ever needs updating in one spot.

The markdown BODY after the frontmatter is untouched byte-for-byte relative
to before 57B-116 -- the old SKILL.md-driven flow still reads these files
directly and must keep working (57B-113's planning layer is additive
infrastructure until acceptance switches flows).

``render_instructions`` assembles one lens task's full instructions as
``_shared.md`` + the lens's own body + a fixed, factual output-contract
preamble -- nothing else. ``LensTemplate.version`` is a content digest of
the raw lens file bytes + ``_shared.md`` bytes, so editing either one changes
every packet composed from it, and the orchestrator engine (digest-keyed
generations, see ``engine.py``) treats that as new work to (re)dispatch.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

LENSES_DIRNAME = "lenses"
SHARED_FILENAME = "_shared.md"
_EXCLUDED_STEMS = {"README", "_shared"}
SHARD_KINDS = ("repo", "workspace")

# wrapper/analysis_wrapper/orchestrator/templates.py -> repo root (mirrors
# rule_gate.py's identical SYNTHESIS_MD_PATH derivation one directory up).
_DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[3]

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

LENS_OUTPUT_SCHEMA_ID = "lens-findings.v1"

# Bounded, factual -- states the return contract only; the actual analysis
# rules live entirely in _shared.md + the lens body appended after this.
# The coverage-shape/citation-grammar paragraphs below were added after a
# live run's first generation: 16 of 16 lens results violated exactly these
# points (a prose coverage status, extra coverage keys, or a ref that named
# a candidate_id / an input's own file name instead of a citable location)
# even though _shared.md already documents the same rules in prose --
# stating them again here, concretely and with one literal example each, in
# the FIRST thing a lens task reads is a deliberate, evidence-driven
# reinforcement, not a duplicate.
LENS_OUTPUT_CONTRACT_PREAMBLE = (
    "Return a single JSON object matching the lens-findings output schema: "
    '{"findings": [...], "coverage": [...], "input_dispositions": [...], '
    '"checklist_dispositions": [...]}. Every finding uses the exact '
    "atomic shape given below, including the required changeability_question "
    "field. Return ONLY this JSON object -- no prose outside it, no code "
    "fence unless it wraps exactly this JSON.\n"
    "\n"
    "Coverage row shape is EXACT -- one object per signal you were asked to "
    'read: {"signal": "<name>", "status": "complete" | "partial" | "failed" '
    '| "skipped", "note": "<string, may be empty>"}. No other keys. status '
    "is one of exactly those four words -- never a prose sentence describing "
    "the status, never any other value.\n"
    "\n"
    "Citations (every evidence[].refs entry) use exactly one of three "
    "grammars, one example each:\n"
    "  - source: repo@revision:path:line\n"
    "    example: api@4f1c9a2b3d5e6f708192a3b4c5d6e7f809192a3b:internal/handler.go:42\n"
    "  - signal: signals/<view-file>:line\n"
    "    example: signals/lizard-api.view.txt:13\n"
    "  - metric: metric:<metric_ref>\n"
    "    example: metric:code.analyzed-scope.total\n"
    "A ref is NEVER a candidate_id (module-candidates.json's own ids, e.g. "
    '"mc-1a2b3c4d") and NEVER an input\'s own file or section name (e.g. '
    '"module-candidates.json", "graph-nodes.json") -- those identify WHICH '
    "input you read, not a citable location inside it.\n"
    "\n"
    "requirements.json is authoritative. Return exactly one input_disposition "
    "for every input_requirements[].input_id and one checklist_disposition "
    "for every checklist_requirements[].dimension_id. input_disposition uses "
    "input_id, status (examined|unavailable|failed|not-applicable), "
    "evidence_refs, and note. checklist_disposition uses dimension_id, outcome "
    "(finding|positive-evidence|no-concern-observed|unknown|not-applicable), "
    "finding_ids, evidence_refs, and limitation. A no-concern or "
    "not-applicable conclusion still needs evidence_refs. coverage must name "
    "every coverage_requirements[].coverage_id exactly once. When "
    "selection-role-results.json is present, it is a deterministic coverage "
    "projection: for every role copy roles[].coverage_status EXACTLY into the "
    "coverage row whose signal is source-selection/<role_id>; do not infer or "
    "downgrade/upgrade that status. If any supplied role has fetch_status "
    "partial or failed, fetched-evidence.json must be unavailable or failed "
    "with evidence_refs [], never examined.\n"
    "\n"
    "A requirements input with kind `bounded-evidence-metadata` or "
    "`partition-metadata` has no citable source location. For that input "
    "only, record status `examined`, evidence_refs `[]`, and a note describing "
    "what metadata was accounted for; never fabricate a citation. For a "
    "checklist item that has only such metadata, use outcome `unknown` with "
    "empty finding_ids/evidence_refs and state the limitation."
)

# Appended (57B-116) ONLY to a source_reads lens's FINAL lens-findings
# instructions -- once its paired selection-fetch task has validated and
# fetch-selections has fetched real source excerpts for it (see planner.py's
# two-phase select/finalize flow: plan_judgment composes only the select
# task for a source_reads lens; a later plan-lens-finalize step composes
# this final task, adding fetched-evidence.json as an input and this
# addendum to the instructions -- a normal, non-source_reads lens task
# never sees either).
SOURCE_VERIFIED_ADDENDUM = (
    "fetched-evidence.json below contains the verified source excerpts you "
    "requested in this lens's own paired selection-fetch task, each row "
    "carrying the exact ref you asked for (repo@revision:path:line) plus "
    "roughly 80 lines of surrounding context -- or, when a location could "
    'not be read, a disclosed reason in the excerpt field itself, starting '
    '"NOT FETCHED:". Cite these using the SAME ref given in each row -- do '
    "not re-derive a different line number for the same fact."
)

# selection-fetch: a source_reads lens's REQUEST for source locations to
# verify -- a separate, later step (fetch-selections) does the actual
# reading; quoted_text stays empty here (schemas.py's request state).
SELECTION_FETCH_OUTPUT_SCHEMA_ID = "selection-fetch.v1"

# 57B-116 round 2: the "up to N" cap is now PER LENS (a lens's own
# frontmatter max_selections field), not a flat "up to 12" for every lens --
# open-lens's cross-repo systemic-condition catches structurally needed more
# than 12 (round-2 spot-check evidence; see open-lens.md's frontmatter
# comment). ``{max_selections}`` is filled in by ``selection_fetch_preamble``
# below, called with the SAME template a lens task's own instructions use --
# never a second, independently-typed number.
_SELECTION_FETCH_PREAMBLE_TEMPLATE = (
    "Return a single JSON object matching the selection-fetch output schema: "
    '{{"selections": [...], "role_dispositions": [...]}}. This is a REQUEST, not a fetch -- a later, '
    "separate step reads the actual source and fills in quoted_text; leave "
    'quoted_text EMPTY ("") on every row here. Name UP TO {max_selections} source '
    "locations (repo@revision:path:line -- take revision from the "
    "repositories.json input's own git block: the commit hash when clean, "
    '"WORKTREE" when dirty, "NON-GIT" for a non-git target, exactly as '
    "_shared.md's citation rules describe) that you need to read IN FULL to "
    "VERIFY a lens-critical fact the bounded signal views only hint at -- "
    "the locations that would most change a confidence or priority call if "
    "confirmed, not a mechanical sample. One selection per location; "
    "selection_id is a stable kebab-case slug; purpose is one line stating "
    "what you are trying to confirm. selection-requirements.json is authoritative: "
    "return one role_disposition for every listed role, with role_id, status "
    "(selected|unavailable|not-applicable|unresolved), selection_ids, and note. "
    "A selected role names source-ref selection_ids; unavailable, not-applicable, "
    "or unresolved roles name none and explain the limit. Use only the typed "
    "evidence_input_ids and inventory_paths attached to the role, not keyword "
    "similarity. Return ONLY this JSON object -- no "
    "prose outside it."
)


def selection_fetch_preamble(max_selections: int) -> str:
    """The selection-fetch task's output-contract preamble, parameterized by
    THIS lens's own ``max_selections`` cap. ``render_selection_instructions``
    uses it to build a select task's instructions; ``selection.py``'s
    ``fetch`` uses the SAME lens's ``max_selections`` (looked up via the
    select task's own ``template_id``, never re-typed) to enforce the
    matching cap -- so the number the model was asked for and the number
    fetch-selections enforces can never independently drift."""
    return _SELECTION_FETCH_PREAMBLE_TEMPLATE.format(max_selections=max_selections)


class TemplateError(ValueError):
    """A malformed lens file: missing/invalid frontmatter. Fail closed --
    there is no reasonable default shard/signals to fall back to."""


def content_digest(*parts: str) -> str:
    """A stable sha256 over an ordered sequence of text parts, NUL-joined so
    e.g. ``("ab", "c")`` and ``("a", "bc")`` never collide. Shared by every
    orchestrator module that needs a "did this source text change" digest
    (lens templates here; the formation-proposal/dedup-rank instruction
    text in ``planner.py``) so the same digest recipe is used everywhere."""
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _parse_frontmatter(text: str, label: str) -> tuple[dict[str, object], str]:
    """``({"shard": ..., "signals": [...]}, body_after_frontmatter)``.
    Frontmatter comment lines (``#...``) may appear standalone between
    fields; a value's own trailing ``# ...`` comment is also stripped. A
    bracketed value (``[a, b]``) parses as a list of strings; a bare ``true``/
    ``false`` (any case) parses as a Python bool; a bare non-negative integer
    (``24``) parses as a Python int (57B-116 round 2: ``max_selections``);
    anything else is a bare string."""
    match = _FRONTMATTER.match(text)
    if not match:
        raise TemplateError(f"{label}: missing YAML frontmatter (expected a "
                            "leading '---\\n...\\n---\\n' block)")
    body = text[match.end():]
    fields: dict[str, object] = {}
    for raw_line in match.group(1).splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise TemplateError(f"{label}: malformed frontmatter line: {stripped!r}")
        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        raw_value = raw_value.split("#", 1)[0].strip()
        if raw_value.startswith("[") and raw_value.endswith("]"):
            inner = raw_value[1:-1].strip()
            value: object = [item.strip() for item in inner.split(",") if item.strip()] \
                if inner else []
        elif raw_value.lower() in ("true", "false"):
            value = raw_value.lower() == "true"
        elif raw_value.isdigit():
            value = int(raw_value)
        else:
            value = raw_value
        if key in fields:
            raise TemplateError(f"{label}: duplicate frontmatter key: {key!r}")
        fields[key] = value
    return fields, body


@dataclass(frozen=True)
class LensTemplate:
    lens_id: str
    shard: str                  # "repo" | "workspace"
    signals: tuple[str, ...]    # tool names; EMPTY means "every tool this run
                                # recorded" (open-lens's deliberate choice --
                                # never "no tool"; see open-lens.md's frontmatter)
    body_md: str                # the lens file's content AFTER its frontmatter
    version: str                # content_digest(raw lens file text, _shared.md text)
    source_reads: bool = False  # 57B-116: this lens gets a paired selection-fetch
                                # task (planner.py's two-phase select/finalize
                                # flow) -- see each flagged lens file's own
                                # frontmatter comment for why; defaults False
                                # (absent in frontmatter = no source reads)
    max_selections: int = 12    # 57B-116 round 2: this lens's own selection-fetch
                                # cap (the number its select task's instructions
                                # ask for, and the number fetch-selections
                                # enforces -- selection._max_selections_for looks
                                # this up so the two can never drift apart).
                                # Default matches the prior flat global; a lens
                                # that structurally needs more overrides it in
                                # its own frontmatter (see open-lens.md's comment).

    def __post_init__(self) -> None:
        if self.shard not in SHARD_KINDS:
            raise TemplateError(
                f"{self.lens_id}: frontmatter shard must be one of {SHARD_KINDS}, "
                f"got {self.shard!r}")
        if not isinstance(self.signals, tuple) or not all(
                isinstance(item, str) and item for item in self.signals):
            raise TemplateError(
                f"{self.lens_id}: frontmatter signals must be a list of non-empty strings")
        if not isinstance(self.source_reads, bool):
            raise TemplateError(
                f"{self.lens_id}: frontmatter source_reads must be true or false, "
                f"got {self.source_reads!r}")
        if isinstance(self.max_selections, bool) or not isinstance(self.max_selections, int) \
                or self.max_selections < 1:
            raise TemplateError(
                f"{self.lens_id}: frontmatter max_selections must be a positive integer, "
                f"got {self.max_selections!r}")


def discover_lens_ids(skill_root: str | Path | None = None) -> tuple[str, ...]:
    """Every lens id present in ``lenses/*.md`` (excluding README/_shared),
    sorted. Discovered from disk rather than hardcoded so this module can
    never silently drift from the installed lens set (test_skill_hygiene.py
    separately asserts that set stays at nine)."""
    root = Path(skill_root).expanduser().resolve() if skill_root else _DEFAULT_SKILL_ROOT
    lenses_dir = root / LENSES_DIRNAME
    return tuple(sorted(p.stem for p in lenses_dir.glob("*.md")
                        if p.stem not in _EXCLUDED_STEMS))


def load_shared_body(skill_root: str | Path | None = None) -> str:
    root = Path(skill_root).expanduser().resolve() if skill_root else _DEFAULT_SKILL_ROOT
    return (root / LENSES_DIRNAME / SHARED_FILENAME).read_text("utf-8")


def load_lens_templates(skill_root: str | Path | None = None) -> dict[str, LensTemplate]:
    """``{lens_id: LensTemplate}`` for every lens file found under
    ``lenses/`` -- fails closed (``TemplateError``) on any lens missing or
    malforming its frontmatter rather than silently skipping it."""
    root = Path(skill_root).expanduser().resolve() if skill_root else _DEFAULT_SKILL_ROOT
    lenses_dir = root / LENSES_DIRNAME
    shared_text = (lenses_dir / SHARED_FILENAME).read_text("utf-8")
    templates: dict[str, LensTemplate] = {}
    for lens_id in discover_lens_ids(root):
        path = lenses_dir / f"{lens_id}.md"
        text = path.read_text("utf-8")
        fields, body = _parse_frontmatter(text, path.name)
        shard = fields.get("shard")
        signals = fields.get("signals")
        if not isinstance(signals, list):
            raise TemplateError(f"{path.name}: frontmatter 'signals' must be a list "
                                "(use [] for none)")
        templates[lens_id] = LensTemplate(
            lens_id=lens_id,
            shard=shard if isinstance(shard, str) else "",
            signals=tuple(signals),
            body_md=body,
            version=content_digest(text, shared_text),
            source_reads=fields.get("source_reads", False),
            max_selections=fields.get("max_selections", 12),
        )
    return templates


def render_instructions(template: LensTemplate, shared_body: str, *,
                        source_verified: bool = False) -> str:
    """``_shared.md`` + this lens's own body + the fixed output-contract
    preamble, in that order -- the ONE way a lens task's full instructions
    are assembled, so every lens task and every test builds them identically.
    ``source_verified=True`` (57B-116) appends ``SOURCE_VERIFIED_ADDENDUM``
    -- only ``plan_lens_finalize`` (planner.py) ever passes it, for a
    source_reads lens's FINAL task, once fetched-evidence.json exists."""
    parts = [LENS_OUTPUT_CONTRACT_PREAMBLE, shared_body.strip(), template.body_md.strip()]
    if source_verified:
        parts.append(SOURCE_VERIFIED_ADDENDUM)
    return "\n\n".join(parts) + "\n"


def render_selection_instructions(template: LensTemplate, shared_body: str) -> str:
    """The paired selection-fetch task's instructions for a source_reads
    lens: the selection-fetch output contract + _shared.md + the lens's own
    body (so it can judge which facts are lens-critical enough to need
    verification) -- structurally identical assembly to
    ``render_instructions``, just a different (selection-specific)
    preamble."""
    return "\n\n".join(
        (selection_fetch_preamble(template.max_selections), shared_body.strip(),
         template.body_md.strip())
    ) + "\n"


def matches_signal(tool: str, signals: tuple[str, ...]) -> bool:
    """A lens's own signal-membership test: an EMPTY ``signals`` tuple means
    "every tool this run recorded" (open-lens's deliberate choice -- see its
    frontmatter), never "no tool"."""
    return not signals or tool in signals
