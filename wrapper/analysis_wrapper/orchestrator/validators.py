"""Semantic/evidence validators for the orchestrator (57B-113 / 57B-114, M0).

Where ``schemas.py`` checks pure JSON SHAPE with no I/O, everything here
either resolves a claim against real evidence (``validate_citations`` reads
the run directory) or polices NARRATIVE PROSE against the "Hard output
rules"/"Hard accuracy rules" in ``synthesis.md`` (the rest of this module).
Every validator returns a list of structured failures shaped
``{"check": str, "detail": str, "location": str}`` (empty = no problems)
EXCEPT :func:`reading_budget_report`, which always returns one report dict —
see its docstring for why a bare failures list would be the wrong shape
there — and :func:`apply_edit_ops`, which returns ``(new_text, failures)``.

None of this module executes an LLM call or writes anything; it is a pure
inspection library an orchestrator (a later milestone) calls after a task
executor returns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .. import identity
from ..overview_audit import _pm_reading_minutes
from ..targetspec import TargetSpec
from .schemas import citation_grammar_kind, metric_ref_id, signal_ref_parts, source_ref_parts

Failure = dict[str, str]


def _failure(check: str, detail: str, location: str = "") -> Failure:
    return {"check": check, "detail": detail, "location": location}


# --------------------------------------------------------------------------- #
# validate_citations
# --------------------------------------------------------------------------- #

def _load_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _indexed_views(run: Path) -> dict[str, str]:
    """Signal views a citation may reference: complete/partial rows only,
    mirrors findings.py's identical restriction (an independent
    re-implementation against the same grammar, per 57B-114's scope — see the
    module docstring in ``findings.py`` for why that module itself is not
    touched here)."""
    summary = _load_json_object(run / "signals" / "run-summary.json", "signals/run-summary.json")
    return {
        str(row.get("view")): str(row.get("tool", "unknown"))
        for row in summary.get("signals", [])
        if isinstance(row, dict) and row.get("status") in {"complete", "partial"}
        and str(row.get("view", "")).endswith(".view.txt")
    }


def _metric_ref_set(run: Path) -> set[str]:
    metrics = _load_json_object(run / "workspace-metrics.json", "workspace-metrics.json")
    return {str(row.get("metric_ref", "")) for row in metrics.get("metrics", [])
            if isinstance(row, dict)}


def _safe_relative(value: str) -> PurePosixPath | None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return path


def _line_text(path: Path, line: int) -> str | None:
    if line < 1:
        return None
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for index, text in enumerate(stream, start=1):
            if index == line:
                return text
    return None


def _check_quote(quote: Any, line_text: str | None, location: str,
                 failures: list[Failure]) -> None:
    if quote is None:
        return
    if not isinstance(quote, str) or not quote:
        failures.append(_failure("citation-quote-shape",
                                  "a supplied quote must be a non-empty string", location))
        return
    if line_text is None or quote not in line_text:
        failures.append(_failure(
            "citation-quote", f"quoted text not found at the cited line: {quote!r}", location))


def validate_citations(refs: Sequence[str | Mapping[str, Any]],
                       run_dir: str | Path) -> list[Failure]:
    """Batch-validate citations against the recorded run: grammar, revision
    matches the run's recorded identity, the cited file exists, the line is
    in range, and — when an entry supplies a ``quote`` — that the quoted text
    is actually present on the cited line.

    Each entry in ``refs`` is either a bare citation string, or a
    ``{"ref": <citation>, "quote": <text>}`` mapping when the caller also
    wants the quoted-text check. ``metric:`` refs are checked against
    ``workspace-metrics.json`` only (they name no line, so a supplied quote
    on one is itself reported as a failure).
    """
    run = Path(run_dir).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    identities = identity.load(run)
    allowed_views = _indexed_views(run)
    metric_refs = _metric_ref_set(run)

    failures: list[Failure] = []
    for index, entry in enumerate(refs):
        location = f"refs[{index}]"
        if isinstance(entry, str):
            ref, quote = entry, None
        elif isinstance(entry, Mapping):
            ref, quote = entry.get("ref"), entry.get("quote")
        else:
            failures.append(_failure(
                "citation-shape",
                "a citation entry must be a string or a {ref, quote} mapping", location))
            continue
        if not isinstance(ref, str) or not ref:
            failures.append(_failure("citation-shape", "ref must be a non-empty string",
                                     location))
            continue

        kind = citation_grammar_kind(ref)
        if kind is None:
            failures.append(_failure(
                "citation-grammar", f"unrecognized citation grammar: {ref!r}", location))
            continue

        if kind == "metric":
            metric_id = metric_ref_id(ref)
            if metric_id not in metric_refs:
                failures.append(_failure(
                    "citation-metric-unknown", f"unknown metric ref: {ref!r}", location))
            if quote is not None:
                failures.append(_failure(
                    "citation-quote-unsupported",
                    "quoted-text verification is not supported for metric refs", location))
            continue

        if kind == "signal":
            relative, line_part = signal_ref_parts(ref)
            if relative not in allowed_views:
                failures.append(_failure(
                    "citation-signal-not-indexed",
                    f"signal ref is not an indexed sanitized view: {ref!r}", location))
                continue
            path = (run / "signals" / relative).resolve()
            signals_root = (run / "signals").resolve()
            if not path.is_relative_to(signals_root) or not path.is_file():
                failures.append(_failure(
                    "citation-signal-missing",
                    f"signal ref file missing or outside signals/: {ref!r}", location))
                continue
            text = _line_text(path, int(line_part))
            if text is None:
                failures.append(_failure(
                    "citation-line-range", f"signal ref line out of range: {ref!r}", location))
                continue
            _check_quote(quote, text, location, failures)
            continue

        # kind == "source"
        parts = source_ref_parts(ref)
        if parts is None:
            failures.append(_failure(
                "citation-grammar", f"unrecognized source citation: {ref!r}", location))
            continue
        repository_ref, revision, relative, line_text = parts
        line = int(line_text)
        try:
            target = spec.repo(identities.internal_id_for(repository_ref))
        except KeyError:
            failures.append(_failure(
                "citation-repo-unknown",
                f"unknown repository reference: {repository_ref!r}", location))
            continue
        expected = ("NON-GIT" if not target.git.is_git else
                    "WORKTREE" if target.git.dirty_detail != "no" else target.git.head.lower())
        if revision.lower() != expected.lower():
            failures.append(_failure(
                "citation-revision-mismatch",
                f"source ref revision mismatch for {repository_ref}: {revision}", location))
            continue
        relative_path = _safe_relative(relative)
        if relative_path is None:
            failures.append(_failure(
                "citation-path-unsafe", f"source ref path is unsafe: {ref!r}", location))
            continue
        root = Path(target.path).expanduser().resolve()
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            failures.append(_failure(
                "citation-file-missing",
                f"source ref file missing or outside target: {ref!r}", location))
            continue
        text = _line_text(candidate, line)
        if text is None:
            failures.append(_failure(
                "citation-line-range", f"source ref line out of range: {ref!r}", location))
            continue
        _check_quote(quote, text, location, failures)
    return failures


# --------------------------------------------------------------------------- #
# numeric_provenance
# --------------------------------------------------------------------------- #

_NUMERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(\d[\d,]*(?:\.\d+)?)(%)?(?![A-Za-z0-9_])")


def _normalize_number(value: Any) -> str:
    text = str(value).strip()
    percent = text.endswith("%")
    text = text[:-1] if percent else text
    text = text.replace(",", "")
    try:
        number = float(text)
        text = str(int(number)) if number == int(number) else str(number)
    except ValueError:
        pass
    return text + ("%" if percent else "")


def numeric_provenance(prose: str, allowed_numbers: Iterable[Any],
                       allowlist_patterns: Iterable[str] = ()) -> list[Failure]:
    """Flag numerals (including percentages and ranges such as ``12-34``, whose
    two sides are checked independently) in ``prose`` that are neither in
    ``allowed_numbers`` (the canonical values this claim may cite — e.g. the
    exact figures pulled from ``workspace-metrics.json``) nor matched by an
    ``allowlist_patterns`` regex (for things that look numeric but are not a
    metric — section markers, dates, version strings).

    This is a lexical floor, not full arithmetic verification: it catches a
    number that was never sourced from evidence, not a wrong CALCULATION
    performed on numbers that were all individually legitimate — that remains
    a judgment call for the reviewer (synthesis.md's "Use canonical metrics;
    do not calculate in prose" rule).
    """
    allowed = {_normalize_number(value) for value in allowed_numbers}
    patterns = [re.compile(pattern) for pattern in allowlist_patterns]
    failures: list[Failure] = []
    for match in _NUMERIC_TOKEN.finditer(prose):
        token = match.group(0)
        normalized = _normalize_number(match.group(1) + (match.group(2) or ""))
        if normalized in allowed:
            continue
        context = prose[max(0, match.start() - 20):match.end() + 20]
        if any(pattern.search(context) for pattern in patterns):
            continue
        failures.append(_failure(
            "numeric-provenance",
            f"numeral {token!r} is not in the allowed evidence set",
            f"offset {match.start()}"))
    return failures


# --------------------------------------------------------------------------- #
# forbidden_vocabulary
# --------------------------------------------------------------------------- #

# Default patterns: the wellness label (English + the common zh-CN
# equivalent, excluding the legitimate "health check(s)" operational-aspect
# compound so overview.md §15 is never a false positive) and the composite
# 0-100 score pattern (synthesis.md's "No artificial health scores" /
# "Never render absence-of-findings as healthy").
DEFAULT_FORBIDDEN_VOCABULARY: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bhealthy\b"), "wellness label"),
    (re.compile(r"健康(?!检查)"), "wellness label"),
    (re.compile(r"\b\d{1,3}\s*/\s*100\b"), "composite-score pattern"),
)

# Ready-made pattern set for synthesis.md's "Static call paths are code
# references, never usage" rule; callers pass this explicitly (it is not part
# of the default set, which stays scoped to wellness/composite-score labels).
STATIC_BASIS_OVERREACH_VOCABULARY: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bproduction traffic\b"), "static-basis overreach"),
    (re.compile(r"(?i)\breal usage\b"), "static-basis overreach"),
    (re.compile(r"(?i)\btraffic\b"), "static-basis overreach"),
)


def _as_pattern(item: Any) -> tuple[re.Pattern[str], str]:
    if isinstance(item, str):
        return re.compile(item), "forbidden pattern"
    pattern, label = item
    return (pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)), label


def forbidden_vocabulary(prose: str,
                         patterns: Iterable[Any] | None = None) -> list[Failure]:
    """Flag every occurrence of a forbidden lexical pattern in ``prose``.

    ``patterns`` items are either a raw regex string or a
    ``(compiled_or_raw_pattern, label)`` pair; defaults to
    :data:`DEFAULT_FORBIDDEN_VOCABULARY` (wellness labels + composite scores).
    """
    checks = [_as_pattern(item) for item in patterns] if patterns is not None \
        else list(DEFAULT_FORBIDDEN_VOCABULARY)
    failures: list[Failure] = []
    for pattern, label in checks:
        for match in pattern.finditer(prose):
            failures.append(_failure(
                "forbidden-vocabulary", f"{label}: {match.group(0)!r}",
                f"offset {match.start()}"))
    return failures


# --------------------------------------------------------------------------- #
# relocation_invariant
# --------------------------------------------------------------------------- #

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")
_WHITESPACE = re.compile(r"\s+")


def _normalize_block(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _content_blocks(text: str) -> set[str]:
    """Split into whitespace-normalized "content blocks": a Markdown table
    row is one block; any other line is split into sentences (English and
    zh-CN/full-width terminators). Empty blocks are dropped."""
    blocks: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("|"):
            blocks.add(_normalize_block(stripped))
            continue
        for sentence in _SENTENCE_SPLIT.split(stripped):
            normalized = _normalize_block(sentence)
            if normalized:
                blocks.add(normalized)
    return blocks


def relocation_invariant(before_text: str, after_text: str,
                         companion_text: str) -> list[Failure]:
    """Every content block (sentence/table row, whitespace-normalized)
    present in ``before_text`` but no longer in ``after_text`` — i.e. content
    an edit REMOVED — must still appear somewhere in ``companion_text``.
    Backs synthesis.md's "simpler document is DERIVED from the fuller one"
    invariant: reorganizing content across documents must never silently drop
    it. A verbatim, whitespace-normalized substring match only — a companion
    that keeps the same fact in different words is not detected as present.
    """
    removed = _content_blocks(before_text) - _content_blocks(after_text)
    companion_normalized = _normalize_block(companion_text)
    failures: list[Failure] = []
    for block in sorted(removed):
        if block not in companion_normalized:
            failures.append(_failure(
                "relocation-invariant",
                f"removed content not found in companion: {block[:120]!r}"))
    return failures


# --------------------------------------------------------------------------- #
# reading_budget_report
# --------------------------------------------------------------------------- #

# Mirrors overview_audit.py's own "pm-reading-budget" check (10.5 minutes);
# kept as a separate named constant here rather than importing a private
# literal, since overview_audit.py exposes no such constant.
READING_CEILING_MINUTES = 10.5

_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s")


def reading_budget_report(overview_md: str, floors_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Report BOTH sides of the output budget in synthesis.md at once: the
    reading-time CEILING (via ``overview_audit._pm_reading_minutes``, reused
    rather than reimplemented) and the required-section FLOORS (every
    required heading present with substance beyond the heading itself, and
    every protected machine-marker block intact).

    This function never returns a ceiling-only result — that is a deliberate
    design invariant, not an oversight: a report that only says "too long" is
    useless for catching the opposite failure mode (a rushed synthesis pass
    that stays short by silently dropping a required section or a machine
    marker). Callers that only care about one side still get both and can
    read the field they need; ``floors_spec`` supplies
    ``required_headings`` (exact heading text, already in the run's own
    language) and ``machine_markers`` (a list of ``(begin, end)`` marker
    pairs, e.g. findings.py's ``PM_BEGIN``/``PM_END``).
    """
    minutes = _pm_reading_minutes(overview_md)
    ceiling_exceeded = minutes > READING_CEILING_MINUTES
    failures: list[Failure] = []
    if ceiling_exceeded:
        failures.append(_failure(
            "reading-ceiling",
            f"estimated reading time {minutes:.1f} minutes exceeds the "
            f"{READING_CEILING_MINUTES} minute ceiling"))

    required_headings = list(floors_spec.get("required_headings", []))
    machine_markers = list(floors_spec.get("machine_markers", []))
    lines = overview_md.splitlines()

    missing_headings: list[str] = []
    empty_headings: list[str] = []
    for heading in required_headings:
        index = overview_md.find(heading)
        if index == -1:
            missing_headings.append(heading)
            failures.append(_failure(
                "floor-heading-missing", f"required heading not found: {heading!r}", heading))
            continue
        line_no = overview_md.count("\n", 0, index)
        has_substance = False
        for line in lines[line_no + 1:]:
            if _HEADING_LINE.match(line):
                break
            if line.strip():
                has_substance = True
                break
        if not has_substance:
            empty_headings.append(heading)
            failures.append(_failure(
                "floor-heading-empty",
                f"heading has no substance before the next heading: {heading!r}", heading))

    marker_problems: list[str] = []
    for begin, end in machine_markers:
        begin_count, end_count = overview_md.count(begin), overview_md.count(end)
        problem = ""
        if begin_count != 1 or end_count != 1:
            problem = f"marker pair must each appear exactly once: begin={begin_count}, end={end_count}"
        else:
            start = overview_md.find(begin)
            finish = overview_md.find(end, start + len(begin))
            if finish == -1:
                problem = "end marker does not follow begin marker"
            elif not overview_md[start + len(begin):finish].strip():
                problem = "protected block between markers is empty"
        if problem:
            marker_problems.append(f"{begin!r}/{end!r}: {problem}")
            failures.append(_failure("floor-marker-integrity", problem, begin))

    return {
        "reading_minutes": minutes,
        "reading_ceiling_minutes": READING_CEILING_MINUTES,
        "ceiling_exceeded": ceiling_exceeded,
        "floors": {
            "missing_headings": missing_headings,
            "empty_headings": empty_headings,
            "marker_problems": marker_problems,
        },
        "failures": failures,
    }


# --------------------------------------------------------------------------- #
# apply_edit_ops
# --------------------------------------------------------------------------- #

DEFAULT_MAX_WORD_DELTA = 40


def apply_edit_ops(text: str, ops: Sequence[Mapping[str, Any]],
                   failed_check_ids: Iterable[str],
                   policy: Mapping[str, Mapping[str, Any]] | None = None,
                   ) -> tuple[str, list[Failure]]:
    """Apply a batch of ``{locate, replace, fixes}`` edit ops (the
    ``repair-edit-ops``/``coherence-check`` output shape) to ``text``.

    Each op is applied only when: its ``locate`` string matches ``text``
    (as most recently edited) EXACTLY ONCE (zero or multiple matches reject
    the op, leaving that op's text unchanged — earlier successful ops still
    apply); its ``fixes`` id names one of the caller's outstanding
    ``failed_check_ids`` (an edit that does not fix a real failure is
    rejected); and the edit's size stays within a per-check diff guard —
    ``policy[fixes]["max_word_delta"]`` (default :data:`DEFAULT_MAX_WORD_DELTA`)
    — UNLESS ``policy[fixes]["requires_relocation"]`` is true, which is the
    escape hatch for edits that legitimately move a large block of prose
    from one place to another (synthesis.md's "simpler document is DERIVED"
    relocations are expected to have a large word-count delta by nature).

    Returns the edited text and the list of rejected ops as structured
    failures (an empty list means every op applied cleanly).
    """
    allowed_checks = set(failed_check_ids)
    policy = policy or {}
    result = text
    failures: list[Failure] = []
    for index, op in enumerate(ops):
        location = f"edits[{index}]"
        locate = op.get("locate")
        replace = op.get("replace")
        fixes = op.get("fixes")
        if not isinstance(locate, str) or not locate:
            failures.append(_failure(
                "edit-op-locate-shape", "locate must be a non-empty string", location))
            continue
        if not isinstance(replace, str):
            failures.append(_failure(
                "edit-op-replace-shape", "replace must be a string", location))
            continue
        if not isinstance(fixes, str) or not fixes:
            failures.append(_failure(
                "edit-op-fixes-shape", "fixes must name a check id", location))
            continue
        if fixes not in allowed_checks:
            failures.append(_failure(
                "edit-op-unmapped",
                f"fixes {fixes!r} is not an outstanding failed check", location))
            continue
        count = result.count(locate)
        if count != 1:
            failures.append(_failure(
                "edit-op-locate",
                f"locate matched {count} time(s) in the current text (must match exactly once)",
                location))
            continue
        fix_policy = policy.get(fixes) or {}
        requires_relocation = bool(fix_policy.get("requires_relocation", False))
        if not requires_relocation:
            max_delta = fix_policy.get("max_word_delta", DEFAULT_MAX_WORD_DELTA)
            delta = abs(len(replace.split()) - len(locate.split()))
            if delta > max_delta:
                failures.append(_failure(
                    "edit-op-diff-guard",
                    f"word-count delta {delta} exceeds the policy limit {max_delta} "
                    f"for {fixes!r}",
                    location))
                continue
        result = result.replace(locate, replace, 1)
    return result, failures
