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
    '{"findings": [...], "coverage": [...]}. Every finding uses the exact '
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
    "input you read, not a citable location inside it."
)


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
    bracketed value (``[a, b]``) parses as a list of strings; anything else
    is a bare string."""
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

    def __post_init__(self) -> None:
        if self.shard not in SHARD_KINDS:
            raise TemplateError(
                f"{self.lens_id}: frontmatter shard must be one of {SHARD_KINDS}, "
                f"got {self.shard!r}")
        if not isinstance(self.signals, tuple) or not all(
                isinstance(item, str) and item for item in self.signals):
            raise TemplateError(
                f"{self.lens_id}: frontmatter signals must be a list of non-empty strings")


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
        )
    return templates


def render_instructions(template: LensTemplate, shared_body: str) -> str:
    """``_shared.md`` + this lens's own body + the fixed output-contract
    preamble, in that order -- the ONE way a lens task's full instructions
    are assembled, so every lens task and every test builds them identically."""
    return "\n\n".join(
        (LENS_OUTPUT_CONTRACT_PREAMBLE, shared_body.strip(), template.body_md.strip())
    ) + "\n"


def matches_signal(tool: str, signals: tuple[str, ...]) -> bool:
    """A lens's own signal-membership test: an EMPTY ``signals`` tuple means
    "every tool this run recorded" (open-lens's deliberate choice -- see its
    frontmatter), never "no tool"."""
    return not signals or tool in signals
