"""Revision-checked semantic source-span fetcher for Module Drill.

Unlike overview selections, a span is the smallest complete syntactic block
around an already-evidenced anchor.  There is intentionally no nearby-line
fallback: an unrecognised or oversized boundary is disclosed as unresolved.
"""

from __future__ import annotations

import hashlib
import json
import re
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


def _brace_pairs(lines: list[str], opens: list[int], closes: list[int]) -> tuple[tuple[int, int], ...]:
    """Return balanced lexical block pairs without guessing across closures.

    The previous backwards scan could cross a close brace that appeared before
    the requested anchor, then select the preceding block.  A route
    registration immediately after another inline callback therefore inherited
    the *previous* callback's body.  Matching pairs forward first makes
    containment explicit: only ``start <= anchor <= end`` is a valid enclosing
    block.
    """
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for index in range(len(lines)):
        # Counts are produced by the conservative lexer.  Treating opens
        # before closes preserves same-line constructs such as ``{}`` while
        # still refusing unmatched closures as enclosing evidence.
        stack.extend([index] * opens[index])
        for _ in range(closes[index]):
            if not stack:
                continue
            start = stack.pop()
            if index - start + 1 <= MAX_SPAN_LINES:
                pairs.append((start, index))
    return tuple(pairs)


def _brace_bounds(lines: list[str], anchor: int,
                  opens: list[int], closes: list[int]) -> tuple[int, int] | None:
    """Find the innermost lexical brace block that actually contains an anchor."""
    candidates = [pair for pair in _brace_pairs(lines, opens, closes)
                  if pair[0] <= anchor <= pair[1]]
    return min(candidates, key=lambda pair: (pair[1] - pair[0], -pair[0])) if candidates else None


_CONTROL_HEADER = re.compile(r"^(?:else\s+)?(?:if|for|while|switch|case|catch|try|do)\b")
_HANDLER_MARKER = re.compile(r"\b(?:func|function)\b|=>|\bwrapAsync\b")
_ROUTE_REGISTRATION = re.compile(
    r"\b[A-Za-z_$][\w$]*\s*\.\s*(?:GET|POST|PUT|PATCH|DELETE|get|post|put|patch|delete)\s*\(")
_MAX_FORWARD_HANDLER_HEADER_LINES = 128


def _handler_header(lines: list[str], start: int) -> bool:
    """Whether a brace line introduces a callable rather than a control block.

    Route and call-graph anchors frequently point at a statement inside an
    ``if``/``for`` block.  The nearest brace then proves only the control path,
    not the handler that contains the rule.  This intentionally small lexical
    recognizer selects the nearest enclosing callable header without trying to
    infer arbitrary language semantics.
    """
    local = lines[start].strip().lstrip("}").strip()
    if _CONTROL_HEADER.match(local):
        return False
    window = " ".join(lines[max(0, start - 8):start + 1])
    if _HANDLER_MARKER.search(window):
        return True
    # A conventional named method can omit both ``function`` and an arrow
    # marker.  Keep this conservative by requiring a parameter list on the
    # opening line and by having already excluded control headers above.
    return "(" in local and ")" in local


def _handler_brace_bounds(lines: list[str], anchor: int,
                          opens: list[int], closes: list[int]) -> tuple[int, int] | None:
    """Find a callable block containing an anchor or its exact route callback.

    A route registration usually anchors the line before its inline callback.
    If no callable block contains that registration, scan only its bounded
    header for the next callback brace.  The scan is allowed only for a route
    registration or a directly callable declaration; an arbitrary source line
    never borrows a later function as its semantic scope.
    """
    pairs = _brace_pairs(lines, opens, closes)
    enclosing = [pair for pair in pairs if pair[0] <= anchor <= pair[1]
                 and _handler_header(lines, pair[0])]
    if enclosing:
        return min(enclosing, key=lambda pair: (pair[1] - pair[0], -pair[0]))

    header_end = min(len(lines), anchor + _MAX_FORWARD_HANDLER_HEADER_LINES + 1)
    if not (_HANDLER_MARKER.search(lines[anchor]) or
            _ROUTE_REGISTRATION.search(lines[anchor]) or
            re.match(r"^\s*(?:async\s+)?(?:function|func)\b", lines[anchor])):
        return None
    for start in range(anchor, header_end):
        if not opens[start]:
            continue
        prefix = "\n".join(lines[anchor:start + 1])
        if not _HANDLER_MARKER.search(prefix):
            continue
        candidate = next((pair for pair in pairs if pair[0] == start), None)
        if candidate is not None:
            return candidate
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


def _bounds(lines: list[str], anchor: int, *, kind: str) -> tuple[tuple[int, int], str] | None:
    """Return one complete lexical construct around an already-evidenced line."""
    opens, closes, semicolons = _syntax_tokens(lines)
    brace = (_handler_brace_bounds(lines, anchor, opens, closes)
             if kind == "handler" else _brace_bounds(lines, anchor, opens, closes))
    if brace is not None:
        return brace, "brace-block"
    statement = _statement_bounds(lines, anchor, semicolons)
    if statement is not None:
        return statement, "statement"
    # A declaration anchor may legitimately be one syntax-level declaration
    # inside a Go const/var block, where neither braces nor semicolons delimit
    # the individual line.  Returning precisely that evidenced, nonblank line
    # is not a nearby-line fallback: it is the smallest available declaration
    # span and keeps the original source reference intact.
    if kind == "declaration" and lines[anchor].strip():
        return (anchor, anchor), "anchored-line"
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
    bounds = _bounds(lines, anchor, kind=request.kind)
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


def fetch_rows(module_run: str | Path, requests: list[dict[str, Any]]) -> list[dict[str, str | int]]:
    """Fetch validated request rows without choosing their persistence format."""
    run = Path(module_run).expanduser().resolve()
    if not isinstance(requests, list):
        raise ContractError("semantic span requests must be a list")
    parsed = [SpanRequest.from_dict(value, f"requests[{index}]")
              for index, value in enumerate(requests)]
    if len({item.span_id for item in parsed}) != len(parsed):
        raise ContractError("semantic span requests must have unique span_id values")
    context = load_source_context(run)
    return [_fetch(item, context.source_spec, context.identities) for item in parsed]


def fetch(module_run: str | Path, requests: list[dict[str, Any]], *,
          out: str | Path | None = None) -> Path:
    run = Path(module_run).expanduser().resolve()
    destination = Path(out).expanduser().resolve() if out else run / "semantic-spans.json"
    if not destination.is_relative_to(run):
        raise ContractError("semantic span output must stay inside the module run")
    rows = fetch_rows(run, requests)
    write_new_text(destination, json.dumps(rows, indent=2, sort_keys=True) + "\n")
    return destination
