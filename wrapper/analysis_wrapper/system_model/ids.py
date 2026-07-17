"""Deterministic stable-ID scheme for the system model (57B-31).

Every node and edge carries an ID that is a function of its NATURAL KEY only —
never of iteration order, wall time, or machine paths. Two runs over the same
repository revision therefore produce byte-identical IDs (the acceptance
requirement). IDs are opaque ``{prefix}:{16-hex}`` handles; the human-readable
identity lives in each node's ``label``/``key`` and its provenance citations.

A citation is the callgraph contract's ``repo_id@commit:relpath:line[:col]`` — a
repository-RELATIVE path, so identity carries no absolute machine path.
"""

from __future__ import annotations

import hashlib

# Node/edge kind -> short, stable ID prefix. The prefix is cosmetic (it aids
# debugging); collision resistance comes from the hashed natural key.
_PREFIX = {
    "repository": "repo",
    "module": "mod",
    "file": "file",
    "symbol": "sym",
    "route": "route",
    "data-store": "data",
    "external-boundary": "ext",
    "deployable-unit": "unit",
    "edge": "edge",
}

_SEP = "\x00"  # NUL cannot occur in any natural-key part, so joins are unambiguous.


def stable_id(kind: str, *parts: str) -> str:
    """``{prefix}:{sha1(parts)[:16]}`` — deterministic in the parts, order-fixed.

    ``parts`` are the natural-key fields for the entity (e.g. a symbol's repo,
    name, and declaration citation). The caller fixes their order; this function
    never sorts them, so callers must pass a canonical order.
    """
    if kind not in _PREFIX:
        raise ValueError(f"unknown id kind: {kind!r}")
    digest = hashlib.sha1(_SEP.join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{_PREFIX[kind]}:{digest}"


def split_position(pos: str) -> tuple[str, int, int | None]:
    """Split ``relpath:line[:col]`` — trailing numeric fields are line/col, the
    rest is the path (POSIX source paths carry no colons)."""
    triple = pos.rsplit(":", 2)
    if len(triple) == 3 and triple[1].isdigit() and triple[2].isdigit():
        return triple[0], int(triple[1]), int(triple[2])
    pair = pos.rsplit(":", 1)
    if len(pair) == 2 and pair[1].isdigit():
        return pair[0], int(pair[1]), None
    return pos, 0, None


def parse_citation(citation: str) -> tuple[str, str, str, int, int | None]:
    """``repo_id@commit:relpath:line[:col]`` -> (repo_id, commit, relpath, line, col).

    Tolerant: a citation missing the ``@commit`` or position tail degrades to
    empty/zero components rather than raising, so a malformed upstream evidence
    string can never crash the assembler (it becomes a weaker, still-explicit
    reference)."""
    repo_id, at, rest = citation.partition("@")
    if not at:
        return "", "", citation, 0, None
    commit, colon, tail = rest.partition(":")
    if not colon:
        return repo_id, commit, "", 0, None
    path, line, col = split_position(tail)
    return repo_id, commit, path, line, col


def make_citation(repo_id: str, commit: str, pos: str) -> str:
    """Build a full ``repo_id@commit:relpath:line[:col]`` citation from a
    repository-relative ``file[:line[:col]]`` position (as emitted by the
    discovery producers). Non-git repos use the ``nogit`` commit sentinel."""
    path, line, col = split_position(pos)
    ref = commit or "nogit"
    if not line:
        return f"{repo_id}@{ref}:{path}"
    base = f"{repo_id}@{ref}:{path}:{line}"
    return f"{base}:{col}" if col else base


def citation_file(citation: str) -> str:
    """The repository-relative file component of a citation ("" if none)."""
    return parse_citation(citation)[2]
