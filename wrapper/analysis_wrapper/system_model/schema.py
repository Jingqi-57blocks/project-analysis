"""System-model contract (57B-31): versioned node/edge shape + container.

Plain dataclasses + JSON, no schema framework — matching the rest of the wrapper.
``schema_version`` is bumped on any breaking change to this shape. Determinism is
structural: :meth:`SystemModel.to_dict` sorts nodes and edges by ID and dumps
with ``sort_keys``, so identical inputs yield byte-identical output.

Edge TYPES are kept DISTINCT on purpose (issue constraint): a language call edge
(from the 57B-30 call graph) must never be merged with an import/dependency,
route/API, persistence, deployment, or module-boundary edge. Each lives under a
different ``EdgeType`` and is normalized by a different producer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..contract_version import CONTRACT_VERSION as SCHEMA_VERSION  # noqa: single shared contract version (57B-118, M4)

# What a node can be. Only materialized where evidence exists (issue: "external
# boundaries when evidence exists"); a kind with no evidence yields no nodes and
# a disclosed coverage partition, never a fabricated placeholder.
NODE_KINDS = (
    "repository",
    "module",            # inferred elsewhere (synthesis/LLM) — never computed here
    "file",
    "symbol",
    "route",
    "data-store",
    "external-boundary",
    "deployable-unit",
)

# Relationship families, kept strictly separate (see module docstring).
EDGE_TYPES = (
    "containment",       # parent contains child (repo->file->symbol, repo->route, ...)
    "dependency",        # import / package dependency (dependency-cruiser / go list)
    "call",              # function/method call (57B-30) — the ONLY language call edge
    "route-linkage",     # caller -> route/endpoint (route/API linkage)
    "data",              # file/table read|write|declaration|join_ref (persistence)
    "boundary",          # code -> external system (integration candidate)
)

# Evidence status. ``observed`` = mechanically extracted; ``inferred`` =
# synthesis/analyzer-inferred (module boundaries, Go VTA dynamic dispatch);
# ``unresolved`` = a relationship whose target could not be resolved to a node.
STATUSES = ("observed", "inferred", "unresolved")

# What the evidence can establish.  This is deliberately orthogonal to status:
# a mechanically observed config declaration is still configuration evidence,
# not proof of runtime execution.
EVIDENCE_BASES = (
    "static-reference",
    "declaration",
    "configuration",
    "history",
    "inferred-linkage",
    "runtime-observation",
    "user-confirmed",
)


@dataclass
class Node:
    id: str
    kind: str
    label: str                                   # human-readable natural name
    status: str
    repository_ref: str = ""                     # owning repo ("" = cross/global)
    key: list[str] = field(default_factory=list)  # natural-key parts (ID source)
    producers: list[str] = field(default_factory=list)   # source producer(s)
    evidence: list[str] = field(default_factory=list)    # citations
    evidence_basis: str = "static-reference"
    confidence: float | None = None
    attrs: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in NODE_KINDS:
            raise ValueError(f"Node.kind unsupported: {self.kind!r}")
        if self.status not in STATUSES:
            raise ValueError(f"Node.status unsupported: {self.status!r}")
        if self.evidence_basis not in EVIDENCE_BASES:
            raise ValueError(f"Node.evidence_basis unsupported: {self.evidence_basis!r}")

    def to_dict(self) -> dict:
        out = {
            "id": self.id, "kind": self.kind, "label": self.label,
            "status": self.status, "repository_ref": self.repository_ref,
            "key": list(self.key),
            "producers": sorted(set(self.producers)),
            "evidence": sorted(set(self.evidence)),
            "evidence_basis": self.evidence_basis,
            "attrs": self.attrs,
        }
        if self.confidence is not None:
            out["confidence"] = self.confidence
        return out


@dataclass
class Edge:
    id: str
    type: str
    src: str                                     # source node id
    dst: str                                     # target node id
    status: str
    producer: str = ""
    evidence: list[str] = field(default_factory=list)
    evidence_basis: str = "static-reference"
    confidence: float | None = None
    attrs: dict = field(default_factory=dict)
    unresolved_target: dict | None = None        # natural key of dst when unresolved

    def __post_init__(self) -> None:
        if self.type not in EDGE_TYPES:
            raise ValueError(f"Edge.type unsupported: {self.type!r}")
        if self.status not in STATUSES:
            raise ValueError(f"Edge.status unsupported: {self.status!r}")
        if self.evidence_basis not in EVIDENCE_BASES:
            raise ValueError(f"Edge.evidence_basis unsupported: {self.evidence_basis!r}")

    def to_dict(self) -> dict:
        out = {
            "id": self.id, "type": self.type, "src": self.src, "dst": self.dst,
            "status": self.status, "producer": self.producer,
            "evidence": sorted(set(self.evidence)),
            "evidence_basis": self.evidence_basis,
            "attrs": self.attrs,
        }
        if self.confidence is not None:
            out["confidence"] = self.confidence
        if self.unresolved_target is not None:
            out["unresolved_target"] = self.unresolved_target
        return out


@dataclass
class SystemModel:
    """The canonical ``system-model.json`` payload.

    ``scan_date`` and ``project_ref`` are RECORDED inputs (never generated here),
    so the artifact stays deterministic — no wall time enters the model.
    """

    scan_date: str
    project_ref: str
    generator: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)

    def _stats(self) -> dict:
        def tally(items, attr):
            counts: dict[str, int] = {}
            for item in items:
                key = getattr(item, attr)
                counts[key] = counts.get(key, 0) + 1
            return dict(sorted(counts.items()))
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes_by_kind": tally(self.nodes, "kind"),
            "nodes_by_status": tally(self.nodes, "status"),
            "edges_by_type": tally(self.edges, "type"),
            "edges_by_status": tally(self.edges, "status"),
        }

    def to_dict(self) -> dict:
        nodes = sorted(self.nodes, key=lambda n: n.id)
        edges = sorted(self.edges, key=lambda e: e.id)
        return {
            "schema_version": SCHEMA_VERSION,
            "generator": self.generator,
            "scan_date": self.scan_date,
            "project_ref": self.project_ref,
            "stats": self._stats(),
            "coverage": self.coverage,
            "nodes": [n.to_dict() for n in nodes],
            "edges": [e.to_dict() for e in edges],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
