"""ModelBuilder — idempotent node/edge accumulation + reference resolution.

Normalizers push nodes and edges through this one object so that (a) a node
referenced by several producers is merged, not duplicated, (b) every file that
appears in any citation gets a ``file`` node and a ``repository -> file``
containment edge (the containment graph is grounded in evidence, built once
here), and (c) an edge whose endpoints do not resolve to a materialized node is
kept EXPLICIT with ``status = unresolved`` rather than silently dropped (issue:
"unresolved relationships remain explicit").
"""

from __future__ import annotations

from . import ids
from .schema import Edge, Node


class ModelBuilder:
    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, Edge] = {}

    # ---- nodes ---------------------------------------------------------------

    def add_node(self, kind: str, key: list[str], *, label: str, status: str,
                 repo_id: str = "", producer: str = "", evidence: list[str] | None = None,
                 confidence: float | None = None, attrs: dict | None = None) -> str:
        """Materialize (or merge into) a node identified by ``kind`` + ``key``.

        Re-adding the same identity unions the producers/evidence and keeps the
        first non-empty label/attrs — so order of discovery cannot change the
        result. Returns the node id."""
        node_id = ids.stable_id(kind, *key)
        existing = self._nodes.get(node_id)
        if existing is None:
            self._nodes[node_id] = Node(
                id=node_id, kind=kind, label=label, status=status,
                repo_id=repo_id, key=list(key),
                producers=[producer] if producer else [],
                evidence=list(evidence or []),
                confidence=confidence, attrs=dict(attrs or {}))
            return node_id
        if producer and producer not in existing.producers:
            existing.producers.append(producer)
        for cite in evidence or []:
            if cite not in existing.evidence:
                existing.evidence.append(cite)
        for akey, aval in (attrs or {}).items():
            existing.attrs.setdefault(akey, aval)
        return node_id

    def note_file(self, repo_id: str, relpath: str, *, producer: str,
                  evidence: str = "") -> str:
        """Ensure a ``file`` node + ``repository -> file`` containment edge exist
        for ``relpath`` in ``repo_id``. Central so every citation-referenced file
        is in the graph exactly once."""
        if not relpath:
            relpath = "(unknown)"
        file_id = self.add_node(
            "file", [repo_id, relpath], label=relpath, status="observed",
            repo_id=repo_id, producer=producer,
            evidence=[evidence] if evidence else [])
        repo_id_node = ids.stable_id("repository", repo_id)
        self.add_edge("containment", repo_id_node, file_id, status="observed",
                      producer=producer)
        return file_id

    # ---- edges ---------------------------------------------------------------

    def add_edge(self, edge_type: str, src: str, dst: str, *, status: str,
                 producer: str = "", evidence: list[str] | None = None,
                 confidence: float | None = None, attrs: dict | None = None,
                 discriminator: str = "") -> str:
        """Add (or merge) an edge. ``discriminator`` distinguishes parallel edges
        of the same type between the same pair (e.g. per-callsite call edges, or
        a read vs. write data edge). Returns the edge id.

        Merge semantics: the edge id is a pure function of
        (type, src, dst, discriminator), and every caller passes a discriminator
        that fully identifies the relationship (call site, access type, signal
        kind). A re-add is therefore the SAME relationship, so status/confidence/
        attrs are consistent by construction and only ``evidence`` is unioned —
        no silent status downgrade can occur."""
        edge_id = ids.stable_id("edge", edge_type, src, dst, discriminator)
        existing = self._edges.get(edge_id)
        if existing is None:
            self._edges[edge_id] = Edge(
                id=edge_id, type=edge_type, src=src, dst=dst, status=status,
                producer=producer, evidence=list(evidence or []),
                confidence=confidence, attrs=dict(attrs or {}))
            return edge_id
        for cite in evidence or []:
            if cite not in existing.evidence:
                existing.evidence.append(cite)
        return edge_id

    def add_unresolved_edge(self, edge_type: str, src: str, target_key: dict, *,
                            producer: str = "", evidence: list[str] | None = None,
                            attrs: dict | None = None, discriminator: str = "") -> str:
        """Record a relationship whose target is not a materialized node. The
        edge is kept with ``status = unresolved`` and the raw target key, so the
        relationship is never dropped."""
        placeholder = "unresolved:" + "|".join(f"{k}={target_key[k]}"
                                               for k in sorted(target_key))
        edge_id = ids.stable_id("edge", edge_type, src, placeholder, discriminator)
        if edge_id not in self._edges:
            self._edges[edge_id] = Edge(
                id=edge_id, type=edge_type, src=src, dst="", status="unresolved",
                producer=producer, evidence=list(evidence or []),
                attrs=dict(attrs or {}), unresolved_target=dict(target_key))
        return edge_id

    # ---- finalize ------------------------------------------------------------

    def resolve(self) -> None:
        """Backstop: any edge whose src/dst id is not a materialized node is
        downgraded to ``unresolved`` with the dangling id recorded. In normal
        assembly every endpoint is created on demand, so this only fires on a
        genuine reference gap — which then stays explicit instead of lying."""
        for edge in self._edges.values():
            if edge.status == "unresolved":
                continue
            missing = {}
            if edge.src and edge.src not in self._nodes:
                missing["src"] = edge.src
            if edge.dst and edge.dst not in self._nodes:
                missing["dst"] = edge.dst
            if missing:
                edge.status = "unresolved"
                edge.unresolved_target = {**(edge.unresolved_target or {}), **missing}

    @property
    def nodes(self) -> list[Node]:
        return list(self._nodes.values())

    @property
    def edges(self) -> list[Edge]:
        return list(self._edges.values())

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes
