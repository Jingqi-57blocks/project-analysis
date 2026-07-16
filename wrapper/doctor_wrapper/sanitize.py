"""Redaction + bounded views — the single sanitization implementation.

Ported from the Phase 0 spike (`spike/_sanitize.py`), whose fixture cases are
this module's floor (57B-10 acceptance). Scope (plan §10, raw-at-rest policy):
everything that persists in git, enters model context, or ships is sanitized
through here; RAW tool output is protected by containment instead (local-only,
gitignored, never read by a model, never packaged).

Passes:
  1. strip ANSI / VT escape sequences;
  2. redact credential-shaped strings (segment-boundary keys: DB_PASSWORD and
     AWS_SECRET_ACCESS_KEY match; jsonwebtoken / "302 tokens" / password-in-path
     do not), quoted and unquoted values, Bearer/Basic schemes;
  3. relativize machine-specific absolute paths.

`bound()` produces the head-bounded view with a `sample: <retained> of <total>
lines (bound=N)` header. Relevance-ordered bounding is per-parser (the parser
orders, then bounds) — this module only enforces the cap + header + redaction.
"""

from __future__ import annotations

import os
import re

REDACTED = "<REDACTED>"

_ANSI = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")

_KEYS = (
    r"passwords?|passwd|pwd|secret|client[_-]?secret|token|auth[_-]?token|"
    r"access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|access[_-]?key|"
    r"secret[_-]?key|private[_-]?key|authorization|session[_-]?id|x-api-key"
)
# Segment boundary: not preceded by a letter/digit ("jsonwebtoken" never matches)
# but `_`/`-` prefixes DO match (DB_PASSWORD, AWS_SECRET_ACCESS_KEY, authToken
# via the auth[_-]?token alias).
_KEY_BOUNDARY = r"(?<![A-Za-z0-9])"
_SEP = r'(?:["\']?\s*[:=]\s*)'
_QUOTED = re.compile(
    r"(" + _KEY_BOUNDARY + r"(?:" + _KEYS + r")" + _SEP
    + r')(["\'])((?:\\.|(?!\2)[\s\S])*)\2',
    re.IGNORECASE,
)
_UNQUOTED = re.compile(
    r"(" + _KEY_BOUNDARY + r"(?:" + _KEYS + r")" + _SEP + r')([^\s,;"\'}{)\]]+)',
    re.IGNORECASE,
)
_SCHEME = re.compile(
    r"((?<![A-Za-z0-9_])(?:Bearer|Basic)\s+)([A-Za-z0-9._~+/=-]+)", re.IGNORECASE
)
# Credentials embedded in URLs (git remotes, registries). The ENTIRE userinfo is
# redacted: tokens routinely travel in the username position with no password
# (https://ghp_xxx@github.com), so keeping the username would keep the secret.
_URL_CRED = re.compile(r"(://)[^/@\s]+(?::[^/@\s]*)?@")


def _unquoted_sub(m: re.Match) -> str:
    if m.group(2).lower() in ("bearer", "basic"):
        return m.group(0)  # let _SCHEME redact the following token instead
    return m.group(1) + REDACTED


def redact(text: str) -> str:
    text = _SCHEME.sub(r"\1" + REDACTED, text)
    text = _QUOTED.sub(lambda m: m.group(1) + m.group(2) + REDACTED + m.group(2), text)
    text = _UNQUOTED.sub(_unquoted_sub, text)
    text = _URL_CRED.sub(r"\1" + REDACTED + "@", text)
    return text


def relativize(text: str, workspace_root: str | None = None) -> str:
    ws = workspace_root or os.environ.get("WORKSPACE_ROOT", "")
    home = os.environ.get("HOME", "")
    if ws:
        text = _replace_absolute_path(text, ws, "$WORKSPACE")
        text = _replace_rootless_absolute_path(text, ws, "$WORKSPACE")
    if home and home != ws:
        text = _replace_absolute_path(text, home, "$HOME")
        text = _replace_rootless_absolute_path(text, home, "$HOME")
    return text


def _replace_absolute_path(text: str, path: str, marker: str) -> str:
    normalized = path.rstrip("/\\")
    if not normalized:
        return text
    # $ must anchor at LINE ends (re.M), and a path followed by whitespace
    # mid-line is still a path — otherwise line-final spellings leak.
    pattern = re.compile(re.escape(normalized) + r"(?=[/\\]|\s|$)", re.M)
    return pattern.sub(lambda _match: marker, text)


def _replace_rootless_absolute_path(text: str, path: str, marker: str) -> str:
    """Redact renderers that drop the leading slash from absolute paths.

    Some table formatters (notably OSV-Scanner's table output) turn
    ``/Users/alice/project/file`` into ``Users/alice/project/file``.  Replacing
    only the normal absolute spelling therefore leaks the user and workspace
    path into an otherwise sanitized view.  Require at least two path segments
    and path boundaries so a single-component home such as ``/root`` never
    causes ordinary prose containing "root" to be rewritten.
    """
    rootless = path.lstrip("/\\")
    if not rootless or not re.search(r"[/\\]", rootless):
        return text
    pattern = re.compile(
        r"(?<![A-Za-z0-9_.-])" + re.escape(rootless) + r"(?=[/\\]|\s|$)", re.M
    )
    return pattern.sub(lambda _match: marker, text)


def sanitize_text(text: str, workspace_root: str | None = None) -> str:
    return relativize(redact(_ANSI.sub("", text)), workspace_root)


def bound(text: str, max_lines: int, workspace_root: str | None = None) -> str:
    """Head-bound + sanitize, with the retained/total provenance header."""
    lines = text.splitlines()
    total = len(lines)
    retained = min(total, max_lines)
    body = "\n".join(lines[:retained])
    header = f"sample: {retained} of {total} lines (bound={max_lines})"
    return header + "\n" + sanitize_text(body, workspace_root) + ("\n" if body else "")
