"""Call-edge + coverage contract — the single source of truth for the shape.

Plain dataclasses + JSON, no schema framework (matches the rest of the wrapper):
``from_dict``/``__post_init__`` raise ValueError with a precise message and that
is the entire validation story. One :class:`CallEdge` is emitted per resolved
INTERNAL call site — never for ambiguous, external, or unresolved sites, which
are accounted for in :class:`RepoCoverage` only.

A citation is ``repo@commit:path:line[:col]`` with a repository-RELATIVE path, so
an edge is reproducible and carries no absolute machine path.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..contract_version import CONTRACT_VERSION

# Resolution certainty: an edge the analysis proved (static dispatch, a concrete
# TypeScript declaration) is ``observed``; a dispatch candidate the analysis
# inferred (Go dynamic/interface dispatch via VTA) is ``inferred`` — never
# presented as certain.
RESOLUTIONS = ("observed", "inferred")
# Syntactic form of the call site.
KINDS = ("static-call", "method-dispatch", "constructor", "import-call")
# Per-repo/lane coverage verdict.
COVERAGE_STATES = ("complete", "partial", "failed", "unavailable")
# Languages this lane emits for.
LANGS = ("go", "js", "ts")

_NO_COMMIT = "nogit"


def citation(repo_id: str, commit: str, rel_path: str, line: int,
             col: int | None = None) -> str:
    """Build ``repo@commit:path:line[:col]``.

    ``commit`` is the repo HEAD from provenance; non-git targets have none, so a
    stable ``nogit`` sentinel is used (their citations are, by disclosure, not
    reproducible across checkouts). ``rel_path`` MUST already be repository
    relative — callers relativize before building the citation.
    """
    ref = commit or _NO_COMMIT
    base = f"{repo_id}@{ref}:{rel_path}:{line}"
    return f"{base}:{col}" if col else base


def _split_position(pos: str) -> tuple[str, int, int | None]:
    """Split a tool position ``path:line[:col]`` into its parts. Trailing numeric
    fields are line/col; everything before is the path (POSIX source paths have
    no colons)."""
    triple = pos.rsplit(":", 2)
    if len(triple) == 3 and triple[1].isdigit() and triple[2].isdigit():
        return triple[0], int(triple[1]), int(triple[2])
    pair = pos.rsplit(":", 1)
    if len(pair) == 2 and pair[1].isdigit():
        return pair[0], int(pair[1]), None
    return pos, 0, None


def position_file(pos: str) -> str:
    """The file component of a ``path:line[:col]`` tool position ("" if none)."""
    return _split_position(pos)[0]


def citation_from_position(pos: str, repo_id: str, commit: str,
                           repo_root: Path) -> str:
    """Turn an absolute tool position into a repo-relative citation. A path that
    does not resolve under ``repo_root`` is left as-is (the sanitizer relativizes
    $WORKSPACE/$HOME at write time as a backstop)."""
    path, line, col = _split_position(pos)
    rel = "unknown"
    if path:
        try:
            rel = str(Path(path).resolve().relative_to(repo_root))
        except (OSError, ValueError):
            rel = path
    return citation(repo_id, commit, rel, line, col)


@dataclass(frozen=True)
class CallEdge:
    """One caller -> callee function/method call edge."""

    lang: str                 # go | js | ts
    resolution: str           # observed | inferred
    kind: str                 # static-call | method-dispatch | constructor | import-call
    caller_symbol: str
    caller_citation: str      # where the caller is DECLARED
    callee_symbol: str
    callee_citation: str      # where the callee is DECLARED
    callsite_citation: str    # where the call OCCURS (primary evidence)

    def __post_init__(self) -> None:
        if self.lang not in LANGS:
            raise ValueError(f"CallEdge.lang unsupported: {self.lang!r}")
        if self.resolution not in RESOLUTIONS:
            raise ValueError(f"CallEdge.resolution unsupported: {self.resolution!r}")
        if self.kind not in KINDS:
            raise ValueError(f"CallEdge.kind unsupported: {self.kind!r}")
        for name in ("caller_symbol", "callee_symbol", "callsite_citation"):
            if not getattr(self, name):
                raise ValueError(f"CallEdge.{name} must be non-empty")

    def sort_key(self) -> tuple:
        """Stable order for deterministic output under identical inputs.

        Covers EVERY field so distinct edges never tie: synthetic callers (Go
        package ``init`` thunks) share a positionless ``caller_citation``, so
        without ``caller_symbol`` as the final discriminator such edges tie and
        their written order follows hash-seed-dependent ``set`` iteration —
        byte-nondeterministic across processes though the normalized model is
        unaffected. ``caller_symbol`` last keeps the primary declaration-site
        ordering unchanged."""
        return (self.caller_citation, self.callsite_citation, self.callee_citation,
                self.callee_symbol, self.kind, self.resolution, self.lang,
                self.caller_symbol)

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict) -> "CallEdge":
        fields = ("lang", "resolution", "kind", "caller_symbol", "caller_citation",
                  "callee_symbol", "callee_citation", "callsite_citation")
        missing = [f for f in fields if f not in data]
        if missing:
            raise ValueError(f"CallEdge missing fields: {missing}")
        return cls(**{f: data[f] for f in fields})


@dataclass
class CallSiteCounts:
    """Call sites by resolution class (57B-30 coverage contract)."""

    resolved: int = 0        # concrete internal callee -> an edge was emitted
    ambiguous: int = 0       # bare signature/interface/union -> never emitted
    external: int = 0        # callee in a dependency / stdlib -> never emitted
    unresolved: int = 0      # no signature / dynamic dispatch -> never emitted

    @property
    def total(self) -> int:
        return self.resolved + self.ambiguous + self.external + self.unresolved

    def to_dict(self) -> dict:
        return {"resolved": self.resolved, "ambiguous": self.ambiguous,
                "external": self.external, "unresolved": self.unresolved,
                "total": self.total}


@dataclass
class RepoCoverage:
    """Per-repo, per-language coverage — every tracked candidate is accounted
    for, so an eligible-but-unanalyzed file can never disappear silently."""

    repository_ref: str
    lang: str
    status: str                                   # one of COVERAGE_STATES
    tool: str = ""
    tool_version: str = ""
    algorithm: str = ""                           # go: "vta"; js: "tsconfig"|"inferred"
    warm_cache: str = "n/a"                       # go: "warm"|"cold"|"n/a"
    reason: str = ""
    candidates_by_ext: dict[str, int] = field(default_factory=dict)
    analyzed_by_ext: dict[str, int] = field(default_factory=dict)
    excluded_by_reason: dict[str, int] = field(default_factory=dict)
    parse_load_failures: int = 0
    call_sites: CallSiteCounts = field(default_factory=CallSiteCounts)
    edges_emitted: int = 0
    notes: str = ""

    def __post_init__(self) -> None:
        if self.status not in COVERAGE_STATES:
            raise ValueError(f"RepoCoverage.status unsupported: {self.status!r}")

    def to_dict(self) -> dict:
        return {
            "repository_ref": self.repository_ref,
            "lang": self.lang, "status": self.status,
            "tool": self.tool, "tool_version": self.tool_version,
            "algorithm": self.algorithm, "warm_cache": self.warm_cache,
            "reason": self.reason,
            "candidates_by_ext": dict(sorted(self.candidates_by_ext.items())),
            "analyzed_by_ext": dict(sorted(self.analyzed_by_ext.items())),
            "excluded_by_reason": dict(sorted(self.excluded_by_reason.items())),
            "parse_load_failures": self.parse_load_failures,
            "call_sites": self.call_sites.to_dict(),
            "edges_emitted": self.edges_emitted,
            "notes": self.notes,
        }


@dataclass
class CoverageReport:
    """The ``callgraph-coverage.json`` payload: one entry per repo+language.

    Deterministic by construction — no wall time, no timestamps generated here;
    ``scan_date`` is a recorded input, so identical runs produce identical bytes.
    """

    scan_date: str
    repos: list[RepoCoverage] = field(default_factory=list)
    determinism: str = ("edges sorted by (caller, callsite, callee); identical "
                        "inputs yield identical bytes")

    def to_dict(self) -> dict:
        ordered = sorted(self.repos, key=lambda c: (c.repository_ref, c.lang))
        return {
            "schema_version": CONTRACT_VERSION,
            "scan_date": self.scan_date,
            "determinism": self.determinism,
            "repos": [c.to_dict() for c in ordered],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def coverage_status(candidates: dict[str, int], analyzed: dict[str, int],
                    failures: int) -> str:
    """``complete`` only when every eligible extension was fully analyzed with no
    load failures; otherwise ``partial``. An eligible extension present in
    ``candidates`` but with fewer (or no) files ``analyzed`` degrades to
    ``partial`` — it is never dropped silently. (``failed``/``unavailable`` are
    decided by the lane before this is reached.)"""
    if failures > 0:
        return "partial"
    for ext, count in candidates.items():
        if analyzed.get(ext, 0) < count:
            return "partial"
    return "complete"
