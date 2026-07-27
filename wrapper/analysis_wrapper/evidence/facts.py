"""Citation-grounded fact evidence (57B-79).

``SourceRef`` mirrors ``analysis_wrapper.findings``'s citation grammar exactly
(``<repository_ref>@<revision>:<relative_path>:<line>``, where ``revision`` is
a 40-character lowercase hex git SHA, ``NON-GIT``, or ``WORKTREE``) so a
fact's provenance can always be rendered as, or parsed from, the same string a
human review already sees in a finding. The parsing itself is reused from
``findings.py`` (not re-derived) so the two grammars cannot drift apart.

``Fact`` is a technology-neutral evidence unit: ``kind``/``data`` are free-form
so no provider is forced through a fixed per-language shape, but ``data`` must
still be plain JSON (mirroring ``profiles/contracts.py``'s own JSON-safety
discipline for provider output).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

# findings.py owns the canonical `<repository_ref>@<revision>:<path>:<line>`
# citation grammar (see its `_source_parts`/`_validate_source_ref`); reused
# here rather than re-derived so the two never drift.
from ..findings import _source_parts

# Mirrors profiles/contracts.py's own `_SAFE_ID`/`_json_safe`; duplicated (not
# imported) because evidence/ must not import from analysis_wrapper.profiles.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_FACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_NON_GIT = "NON-GIT"
_WORKTREE = "WORKTREE"


def _validated_id(value: Any, label: str, pattern: "re.Pattern[str]" = _SAFE_ID) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label} must use 1-128 letters, digits, dot, underscore, or hyphen")
    return value


def _json_safe(value: Any, label: str) -> None:
    try:
        json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain JSON-safe data: {exc}") from exc


@dataclass(frozen=True)
class SourceRef:
    """One citation in findings.py's ``repo@revision:path:line`` grammar."""

    repository_ref: str
    revision: str
    path: str
    line: int

    def __post_init__(self) -> None:
        if not isinstance(self.repository_ref, str) or not self.repository_ref:
            raise ValueError("SourceRef.repository_ref must be a non-empty string")
        if self.revision not in (_NON_GIT, _WORKTREE) and not _REVISION.fullmatch(self.revision):
            raise ValueError(
                "SourceRef.revision must be a 40-character lowercase hex SHA, "
                f"{_NON_GIT!r}, or {_WORKTREE!r}"
            )
        path = PurePosixPath(self.path)
        if not self.path or path.is_absolute() or ".." in path.parts:
            raise ValueError("SourceRef.path must be relative with no '..' segments")
        if isinstance(self.line, bool) or not isinstance(self.line, int) or self.line < 1:
            raise ValueError("SourceRef.line must be an integer >= 1")

    def to_string(self) -> str:
        return f"{self.repository_ref}@{self.revision}:{self.path}:{self.line}"

    @classmethod
    def from_string(cls, value: str) -> "SourceRef":
        parts = _source_parts(value)
        if not parts:
            raise ValueError(f"invalid source ref: {value}")
        repository_ref, revision, relative, line_text = parts
        return cls(repository_ref=repository_ref, revision=revision,
                   path=relative, line=int(line_text))

    def to_dict(self) -> dict[str, Any]:
        return {"repository_ref": self.repository_ref, "revision": self.revision,
                "path": self.path, "line": self.line}


@dataclass(frozen=True)
class Fact:
    """One technology-neutral evidence unit a capability provider emits."""

    fact_id: str
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    source_refs: tuple[SourceRef, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _validated_id(self.fact_id, "fact_id", _SAFE_FACT_ID)
        _validated_id(self.kind, "kind")
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        if not isinstance(self.data, dict):
            raise ValueError("Fact.data must be a JSON object")
        if not all(isinstance(item, SourceRef) for item in self.source_refs):
            raise ValueError("Fact.source_refs must contain SourceRef values")
        _json_safe(self.data, "Fact.data")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "kind": self.kind,
            "data": self.data,
            "source_refs": sorted(ref.to_string() for ref in self.source_refs),
        }


def make_fact_id(capability_id: str, repository_ref: str, kind: str,
                 natural_key: tuple[str, ...]) -> str:
    """Deterministic fact identifier for cross-artifact traceability.

    This ID is for TRACEABILITY within one run's evidence, NOT for
    cross-run caching: two runs over the same natural key reproducing the
    same ID is incidental to this being a pure function of its inputs, not a
    cache key, replay token, or content-addressed lookup that anything here
    honors.

    ``repository_ref`` is the STABLE human-readable identity reference, not
    the internal ``repo_id`` (a hash derived from the analyzed machine's
    absolute path — 57B-112 §5 / 57B-118 M4). The internal id is fine for
    same-machine parity chains but would make this id diverge across
    machines for byte-identical evidence of the same repository; the
    reference is what stays stable there.
    """
    digest = hashlib.sha1(
        "|".join([capability_id, repository_ref, kind, *natural_key]).encode("utf-8")
    ).hexdigest()[:16]
    return f"fact:{digest}"
