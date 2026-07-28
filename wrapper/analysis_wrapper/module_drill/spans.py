"""Revision-checked semantic source-span fetcher for Module Drill.

Unlike overview selections, a span is the smallest complete syntactic block
around an already-evidenced anchor.  There is intentionally no nearby-line
fallback: an unrecognised or oversized boundary is disclosed as unresolved.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .. import identity
from ..executor import write_new_text
from ..sanitize import sanitize_text
from ..targetspec import TargetSpec
from ..orchestrator.schemas import source_ref_parts
from .context import load as load_source_context
from .validation import ContractError, exact_object, ref_list, slug

SPAN_KINDS = frozenset({"function", "class", "handler", "declaration", "config-block"})
MAX_SPAN_LINES = 600
MAX_SPAN_BYTES = 64_000


@dataclass(frozen=True)
class SpanRequest:
    span_id: str
    kind: str
    ref: str
    purpose: str

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "SpanRequest":
        row = exact_object(value, {"span_id", "kind", "ref", "purpose"}, label)
        if not isinstance(row["kind"], str) or row["kind"] not in SPAN_KINDS:
            raise ContractError(f"{label}.kind must be one of {sorted(SPAN_KINDS)}")
        refs = ref_list([row["ref"]], f"{label}.ref", allow_empty=False)
        if not isinstance(row["purpose"], str) or not row["purpose"].strip():
            raise ContractError(f"{label}.purpose must be non-empty")
        return cls(slug(row["span_id"], f"{label}.span_id"), row["kind"], refs[0], row["purpose"])


def _source_lines(ref: str, spec: TargetSpec,
                  identities: identity.IdentityMap) -> tuple[list[str], int] | str:
    parts = source_ref_parts(ref)
    if parts is None:
        return "ref is not a source reference"
    repository_ref, revision, relative, line_text = parts
    try:
        target = spec.repo(identities.internal_id_for(repository_ref))
    except KeyError:
        return f"unknown repository reference: {repository_ref}"
    expected = "NON-GIT" if not target.git.is_git else (
        "WORKTREE" if target.git.dirty_detail != "no" else target.git.head)
    if revision.lower() != expected.lower():
        return f"revision mismatch: expected {expected}"
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.name.startswith(".env"):
        return "unsafe or environment-file path"
    root = Path(target.path).resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return "source file is missing or outside its repository"
    try:
        lines = candidate.read_text("utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"source file cannot be read: {exc.__class__.__name__}"
    line = int(line_text)
    if line < 1 or line > len(lines):
        return "anchor line is outside source file"
    return lines, line - 1


def _syntax_tokens(lines: list[str]) -> tuple[list[int], list[int], list[int]]:
    """Return lexical brace and statement-token counts for common source forms.

    This is intentionally a conservative tokenizer, not an AST parser.  It
    ignores braces and semicolons in quoted strings and line/block comments;
    language-specific providers can later supply richer anchors.  A construct
    whose boundary cannot be established inside this limited grammar stays
    unresolved rather than being approximated by an arbitrary line window.
    """
    opens = [0] * len(lines)
    closes = [0] * len(lines)
    semicolons = [0] * len(lines)
    quote = ""
    block_comment = False
    for line_index, text in enumerate(lines):
        index = 0
        while index < len(text):
            char = text[index]
            following = text[index + 1] if index + 1 < len(text) else ""
            if block_comment:
                if char == "*" and following == "/":
                    block_comment = False
                    index += 2
                    continue
                index += 1
                continue
            if quote:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = ""
                index += 1
                continue
            if char == "/" and following == "*":
                block_comment = True
                index += 2
                continue
            if char == "/" and following == "/":
                break
            if char == "#":
                break
            if char in {"'", '"', "`"}:
                quote = char
            elif char == "{":
                opens[line_index] += 1
            elif char == "}":
                closes[line_index] += 1
            elif char == ";":
                semicolons[line_index] += 1
            index += 1
        # JavaScript/TypeScript and Go quoted strings may not cross an
        # unescaped newline; backticks deliberately remain open.
        if quote in {"'", '"'} and not text.rstrip().endswith("\\"):
            quote = ""
    return opens, closes, semicolons


def _brace_bounds(lines: list[str], anchor: int,
                  opens: list[int], closes: list[int]) -> tuple[int, int] | None:
    """Find the innermost lexical brace block containing an anchor."""
    start = None
    depth = 0
    for index in range(anchor, -1, -1):
        depth += closes[index] - opens[index]
        if opens[index] and depth <= 0:
            start = index
            break
    if start is None:
        return None
    depth = 0
    for index in range(start, len(lines)):
        depth += opens[index] - closes[index]
        if index - start + 1 > MAX_SPAN_LINES:
            return None
        if depth == 0:
            return start, index
    return None


def _statement_bounds(lines: list[str], anchor: int,
                      semicolons: list[int]) -> tuple[int, int] | None:
    """Find a complete simple statement when the anchor has no brace block.

    The span starts at the first nonblank line following the previous lexical
    semicolon or a blank separator, and ends at the next lexical semicolon.
    This supports declaration/handler anchors such as an expression-bodied
    callback while refusing unconstrained source regions.
    """
    start = anchor
    while start > 0 and not semicolons[start - 1] and lines[start - 1].strip():
        start -= 1
        if anchor - start + 1 > MAX_SPAN_LINES:
            return None
    while start < len(lines) and not lines[start].strip():
        start += 1
    for end in range(anchor, len(lines)):
        if end - start + 1 > MAX_SPAN_LINES:
            return None
        if semicolons[end]:
            return start, end
    return None


def _bounds(lines: list[str], anchor: int) -> tuple[tuple[int, int], str] | None:
    """Return one complete lexical construct around an already-evidenced line."""
    opens, closes, semicolons = _syntax_tokens(lines)
    brace = _brace_bounds(lines, anchor, opens, closes)
    if brace is not None:
        return brace, "brace-block"
    statement = _statement_bounds(lines, anchor, semicolons)
    if statement is not None:
        return statement, "statement"
    return None


def _range_ref(ref: str, line: int) -> str:
    parts = source_ref_parts(ref)
    if parts is None:  # Defensive: callers only reach here after _source_lines succeeds.
        raise ContractError("cannot construct a range reference from an invalid source ref")
    repository_ref, revision, relative, _ = parts
    return f"{repository_ref}@{revision}:{relative}:{line}"


def _fetch(request: SpanRequest, spec: TargetSpec,
           identities: identity.IdentityMap) -> dict[str, str | int]:
    loaded = _source_lines(request.ref, spec, identities)
    base = {
        "span_id": request.span_id, "kind": request.kind, "purpose": request.purpose,
        "ref": request.ref, "start_ref": "", "end_ref": "", "boundary": "",
        "line_count": 0, "content_sha256": "",
    }
    if isinstance(loaded, str):
        return {**base, "status": "unresolved", "reason": loaded, "content": ""}
    lines, anchor = loaded
    bounds = _bounds(lines, anchor)
    if bounds is None:
        return {**base, "status": "unresolved", "reason": "semantic boundary cannot be resolved", "content": ""}
    (start, end), boundary = bounds
    content = sanitize_text("\n".join(lines[start:end + 1]))
    if len(content.encode("utf-8")) > MAX_SPAN_BYTES:
        return {**base, "status": "unresolved", "reason": "semantic span exceeds byte budget", "content": ""}
    return {
        **base, "status": "fetched", "reason": "", "start_ref": _range_ref(request.ref, start + 1),
        "end_ref": _range_ref(request.ref, end + 1), "boundary": boundary,
        "line_count": end - start + 1,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "content": content,
    }


def fetch(module_run: str | Path, requests: list[dict[str, Any]], *,
          out: str | Path | None = None) -> Path:
    run = Path(module_run).expanduser().resolve()
    if not isinstance(requests, list):
        raise ContractError("semantic span requests must be a list")
    parsed = [SpanRequest.from_dict(value, f"requests[{index}]")
              for index, value in enumerate(requests)]
    if len({item.span_id for item in parsed}) != len(parsed):
        raise ContractError("semantic span requests must have unique span_id values")
    context = load_source_context(run)
    destination = Path(out).expanduser().resolve() if out else run / "semantic-spans.json"
    if not destination.is_relative_to(run):
        raise ContractError("semantic span output must stay inside the module run")
    rows = [_fetch(item, context.source_spec, context.identities) for item in parsed]
    write_new_text(destination, json.dumps(rows, indent=2, sort_keys=True) + "\n")
    return destination
