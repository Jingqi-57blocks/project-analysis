"""Normalize the 57B-30 call graph into ``symbol`` nodes + ``call`` edges.

Reads ``<run>/callgraph/<repo_id>.jsonl`` (one call edge per line, the contract
shape) and ``<run>/callgraph-coverage.json``. Produces exactly ONE edge type —
``call`` — so a language call edge is never confused with an import, route, or
persistence edge. A call whose resolution the analyzer only INFERRED (Go VTA
dynamic dispatch) becomes an ``inferred`` edge with reduced confidence; a proven
static call stays ``observed``.

A missing ``callgraph/`` directory is reported as an absent partition by
:func:`load` (``present=False``) — the caller turns that into a disclosed
coverage partition, never an empty graph passed off as "no calls".
"""

from __future__ import annotations

import json
from pathlib import Path

from ..callgraph.contract import CallEdge
from . import ids
from .builder import ModelBuilder

PRODUCER = "callgraph"
# Fixed confidence for analyzer-INFERRED (non-proven) call resolution.
_INFERRED_CONFIDENCE = 0.5


def _symbol(builder: ModelBuilder, symbol: str, decl_citation: str) -> str:
    """Materialize a symbol node + its file containment, keyed by declaration
    site so two same-named symbols in different files stay distinct."""
    repo_id, _, relpath, _, _ = ids.parse_citation(decl_citation)
    file_id = builder.note_file(repo_id, relpath, producer=PRODUCER,
                                evidence=decl_citation)
    sym_id = builder.add_node(
        "symbol", [repo_id, symbol, decl_citation], label=symbol,
        status="observed", repo_id=repo_id, producer=PRODUCER,
        evidence=[decl_citation])
    builder.add_edge("containment", file_id, sym_id, status="observed",
                     producer=PRODUCER)
    return sym_id


def _add_edge(builder: ModelBuilder, edge: CallEdge) -> None:
    caller = _symbol(builder, edge.caller_symbol, edge.caller_citation)
    callee = _symbol(builder, edge.callee_symbol, edge.callee_citation)
    # The call site is primary evidence; note its file so the containment graph
    # includes it even when the call spans two other files.
    site_repo, _, site_rel, _, _ = ids.parse_citation(edge.callsite_citation)
    builder.note_file(site_repo, site_rel, producer=PRODUCER,
                      evidence=edge.callsite_citation)
    status = "observed" if edge.resolution == "observed" else "inferred"
    builder.add_edge(
        "call", caller, callee, status=status, producer=PRODUCER,
        evidence=[edge.callsite_citation],
        confidence=_INFERRED_CONFIDENCE if status == "inferred" else None,
        attrs={"lang": edge.lang, "kind": edge.kind},
        discriminator=edge.callsite_citation)


def load(builder: ModelBuilder, run_dir: str | Path) -> dict:
    """Populate ``builder`` from the call graph in ``run_dir``.

    Returns a summary used to build the coverage partition:
    ``{present, coverage, jsonl_repos, edges_loaded}``. ``present`` is False when
    the ``callgraph/`` directory is absent (the analyzer did not run / its output
    is not in this run dir)."""
    run = Path(run_dir)
    cg_dir = run / "callgraph"
    coverage_path = run / "callgraph-coverage.json"
    summary: dict = {"present": cg_dir.is_dir(), "coverage": None,
                     "jsonl_repos": [], "edges_loaded": 0}
    if coverage_path.is_file():
        try:
            summary["coverage"] = json.loads(coverage_path.read_text("utf-8"))
        except (OSError, ValueError):
            summary["coverage"] = None
    if not cg_dir.is_dir():
        return summary
    edges_loaded = 0
    for jsonl in sorted(cg_dir.glob("*.jsonl")):
        summary["jsonl_repos"].append(jsonl.stem)
        for line in jsonl.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            _add_edge(builder, CallEdge.from_dict(json.loads(line)))
            edges_loaded += 1
    summary["edges_loaded"] = edges_loaded
    return summary
