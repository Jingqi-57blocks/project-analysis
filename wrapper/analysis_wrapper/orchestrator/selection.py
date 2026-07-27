"""Deterministic +/-N-line source-evidence fetcher (57B-113 / 57B-116, M2).

Reads the run's single VALIDATED ``selection-fetch`` output for one specific
select task_id (a source_reads lens's own REQUEST for source locations to
verify -- ``planner.py``'s two-phase select/finalize pairing) and fetches
each cited location's surrounding context from the actual repo file --
revision-checked exactly the way ``validators.validate_citations`` already
checks a citation, path-safety-checked exactly the way ``findings.py``'s
``_safe_relative`` already is.

Writes ``fetched-evidence.json``: one row per requested selection, in
request order, ALWAYS exactly ``{selection_id, purpose, ref, excerpt}`` --
a selection that could not be fetched (wrong grammar, unknown repo, revision
mismatch, unsafe/missing/env path, out-of-range line, over the per-run cap,
or over the total byte budget) still gets a row; its ``excerpt`` is a
disclosed ``"NOT FETCHED: <reason>"`` string instead of real content. Never
a silently dropped row, never a raised exception for a single bad selection
-- see ``planner.plan_lens_finalize``, the next step that consumes this
file.

Three independent size guards apply to every excerpt, each catching a
different pathological input: ``MAX_LINE_CHARS`` bounds any ONE line;
``MAX_EXCERPT_BYTES`` bounds the WHOLE excerpt regardless (a file whose
lines are each individually long -- a minified or generated file -- can
still blow well past a sane single-excerpt size even with every line
individually under its own cap); ``MAX_TOTAL_BYTES`` bounds the sum across
every selection in one fetch-selections call.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from .. import identity
from ..sanitize import sanitize_text
from ..targetspec import TargetSpec
from . import templates as tpl
from .engine import Engine
from .planner import fetch_selections_output_path
from .results import validated_outputs
from .schemas import source_ref_parts

# 57B-116 round 2: the per-run selection cap is now PER LENS (that lens's own
# frontmatter max_selections field -- e.g. open-lens's 24), not a single flat
# number. DEFAULT_MAX_SELECTIONS is only the fallback used when a select
# task's own template_id cannot be resolved to a known lens (a synthetic/test
# task_id, or a select task predating this lookup) -- see
# _max_selections_for, the one place that decides the cap actually enforced.
DEFAULT_MAX_SELECTIONS = 12
CONTEXT_LINES = 40           # +/- this many lines of surrounding context
MAX_LINE_CHARS = 2000        # per-line truncation guard (mirrors synthesis_input.py's own cap)
MAX_TOTAL_BYTES = 200_000    # total fetched-evidence.json excerpt budget for one run
# A hard cap on ONE excerpt, independent of MAX_LINE_CHARS and MAX_TOTAL_BYTES:
# +/-40 lines of a file whose lines are each individually long (a minified or
# generated file -- not hypothetical; this is a real live hazard, just not
# the one that actually fired) would still total up to
# ~81 * MAX_LINE_CHARS well past a sane single-excerpt size even though each
# INDIVIDUAL line stayed under its own cap. Applied AFTER sanitize_text (never
# truncate before redaction -- a secret split across the cut could survive
# half-redacted) and byte-safe (never splits a multi-byte UTF-8 character).
MAX_EXCERPT_BYTES = 8192
_TRUNCATION_MARKER = "\n... [truncated: excerpt exceeds the {cap}-byte per-excerpt cap]"

_ENV_FILE_PREFIX = ".env"
_SKIP_PREFIX = "NOT FETCHED: "


def _truncate_to_byte_cap(text: str, cap: int) -> str:
    """``text``, unchanged if it already fits ``cap`` bytes (UTF-8); else
    truncated to fit alongside a disclosed marker, never splitting a
    multi-byte character."""
    marker = _TRUNCATION_MARKER.format(cap=cap)
    encoded = text.encode("utf-8")
    if len(encoded) <= cap:
        return text
    budget = max(0, cap - len(marker.encode("utf-8")))
    truncated = encoded[:budget]
    while truncated:
        try:
            return truncated.decode("utf-8") + marker
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return marker.lstrip("\n")


class SelectionFetchError(ValueError):
    """The run's ledger does not hold a validated selection-fetch output for
    the given task_id. Fail closed -- there is no reasonable partial fetch
    to run instead."""


def _skip(reason: str) -> str:
    return f"{_SKIP_PREFIX}{reason}"


def _is_env_file(name: str) -> bool:
    """Names-only policy (57B-116): a file literally named like an env file
    is excluded regardless of its directory or content -- no content
    sniffing, matching every other bounded-evidence guard in this package."""
    return name == _ENV_FILE_PREFIX or name.startswith(_ENV_FILE_PREFIX + ".")


def _fetch_source_excerpt(ref: str, spec: TargetSpec, identities: identity.IdentityMap) -> str:
    """Either the bounded, sanitized excerpt, or a ``_skip(...)`` string --
    NEVER raises; every failure mode here is a disclosed, per-selection
    skip, not an aborted run."""
    parts = source_ref_parts(ref)
    if parts is None:
        return _skip("not a source ref (signal/metric refs have no fetchable "
                     f"source context): {ref}")
    repository_ref, revision, relative, line_text = parts
    try:
        target = spec.repo(identities.internal_id_for(repository_ref))
    except KeyError:
        return _skip(f"unknown repository reference: {repository_ref}")

    expected = ("NON-GIT" if not target.git.is_git else
               "WORKTREE" if target.git.dirty_detail != "no" else target.git.head.lower())
    if revision.lower() != expected.lower():
        return _skip(f"revision mismatch for {repository_ref}: {revision} "
                     f"(expected {expected})")

    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts:
        return _skip(f"unsafe relative path: {relative}")
    if _is_env_file(relative_path.name):
        return _skip("environment file excluded by policy")

    root = Path(target.path).expanduser().resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return _skip(f"file missing or outside target: {relative}")

    line = int(line_text)
    lines = candidate.read_text("utf-8", errors="replace").splitlines()
    if line < 1 or line > len(lines):
        return _skip(f"cited line out of range: {line} (file has {len(lines)} lines)")

    start = max(1, line - CONTEXT_LINES)
    end = min(len(lines), line + CONTEXT_LINES)
    window = [entry[:MAX_LINE_CHARS] for entry in lines[start - 1:end]]
    excerpt = sanitize_text("\n".join(window))
    return _truncate_to_byte_cap(excerpt, MAX_EXCERPT_BYTES)


def _lens_id_for_select_task(run: Path, select_task_id: str) -> str | None:
    """The lens_id this select task belongs to, recovered from its OWN
    packet's ``template_id`` -- ``planner.py``'s ``plan_judgment`` composes a
    select task's ``template_id`` as ``f"lens-{lens_id}-select"``. Unlike
    ``task_id`` (a repo-sharded task_id carries an unreversible hash
    fragment -- see ``_LensTaskSpec``'s own comment on why that string is
    never parsed), ``template_id`` never includes ``repository_ref`` at all,
    so recovering ``lens_id`` from it this way is safe. Returns ``None``
    when no ``created`` record exists for this task_id, or its
    ``template_id`` does not match the lens-select convention (a synthetic
    task_id, typically from a test) -- either way the caller falls back to
    ``DEFAULT_MAX_SELECTIONS``."""
    engine = Engine(run)
    if not engine.ledger_exists():
        return None
    for record in engine._read_records():
        if record.event == "created" and record.task_id == select_task_id:
            template_id = record.detail["task"].get("template_id", "")
            if template_id.startswith("lens-") and template_id.endswith("-select"):
                return template_id[len("lens-"):-len("-select")]
            return None
    return None


def _max_selections_for(run: Path, select_task_id: str,
                        skill_root: str | Path | None = None) -> int:
    """This select task's OWN lens's ``max_selections`` cap -- the same
    number ``templates.render_selection_instructions`` used to phrase that
    lens's select task's own instructions ("Name UP TO N source
    locations..."), looked up fresh via the lens's ``template_id`` rather
    than re-typed anywhere, so the number the model was asked for and the
    number enforced here can never independently drift. Falls back to
    ``DEFAULT_MAX_SELECTIONS`` when the task's ``template_id`` does not
    resolve to a known lens."""
    lens_id = _lens_id_for_select_task(run, select_task_id)
    if lens_id is None:
        return DEFAULT_MAX_SELECTIONS
    template = tpl.load_lens_templates(skill_root).get(lens_id)
    return template.max_selections if template is not None else DEFAULT_MAX_SELECTIONS


def fetch(run_dir: str | Path, select_task_id: str, *,
         out: str | Path | None = None, skill_root: str | Path | None = None) -> Path:
    """Fetch bounded source context for every selection in the run's
    validated ``select_task_id`` selection-fetch output; writes
    fetched-evidence.json (``out``, when given, overrides the canonical
    path) and returns the path written. The per-run selection cap enforced
    is THIS select task's own lens's ``max_selections`` (see
    ``_max_selections_for``), not a single flat number."""
    run = Path(run_dir).expanduser().resolve()
    outputs = validated_outputs(run, task_type="selection-fetch")
    output = outputs.get(select_task_id)
    if output is None:
        raise SelectionFetchError(
            f"no validated selection-fetch task {select_task_id!r} found -- run "
            "plan-judgment and its executor to completion before fetch-selections")

    spec = TargetSpec.load(run / "targets.json")
    identities = identity.load(run)
    selections = output.get("selections", [])
    max_selections = _max_selections_for(run, select_task_id, skill_root)

    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for index, selection in enumerate(selections):
        selection_id = selection.get("selection_id", f"selection-{index}")
        purpose = selection.get("purpose", "")
        ref = selection.get("ref", "")
        if index >= max_selections:
            rows.append({"selection_id": selection_id, "purpose": purpose, "ref": ref,
                        "excerpt": _skip(f"exceeds the per-run selection cap "
                                         f"({max_selections})")})
            continue
        excerpt = _fetch_source_excerpt(ref, spec, identities)
        encoded_len = len(excerpt.encode("utf-8"))
        if total_bytes + encoded_len > MAX_TOTAL_BYTES:
            excerpt = _skip("total fetched-evidence byte budget exceeded")
            encoded_len = len(excerpt.encode("utf-8"))
        total_bytes += encoded_len
        rows.append({"selection_id": selection_id, "purpose": purpose, "ref": ref,
                    "excerpt": excerpt})

    out_path = (Path(out).expanduser().resolve() if out
               else fetch_selections_output_path(run, select_task_id))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", "utf-8")
    return out_path
