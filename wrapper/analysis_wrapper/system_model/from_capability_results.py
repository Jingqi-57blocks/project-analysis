"""Technology-neutral System-Model ingestion from canonical evidence (57B-79).

Bridges :class:`~analysis_wrapper.profiles.contracts.CapabilityResult` into the
canonical :class:`~analysis_wrapper.system_model.builder.ModelBuilder` without
any per-language branching: every decision is driven by ``Fact.kind`` against
the CLOSED node/edge vocabulary already declared in ``system_model/schema.py``
(the "bundled profile + capability provider" architecture must not reintroduce
hardcoded per-language branches). Not wired into
:func:`analysis_wrapper.system_model.assemble.assemble` — callers exercise it
directly until real providers land.

Node/edge identity keys off the MACHINE ``repo_id`` a result carries, never the
human-readable reference from :class:`~analysis_wrapper.identity.IdentityMap`
— that reference is used only for the node's display ``label``/
``repository_ref`` attribute. This is the machine/readable separation the live
``from_discovery.py``/``from_callgraph.py`` ingestion does NOT yet follow (a
known gap outside this issue's scope); this module demonstrates the correct
pattern rather than fixing the live path.

A Fact whose ``kind`` matches a system-model ``EDGE_TYPES`` value (other than
``containment``, which is handled structurally) describes a relationship from
the result's repository node to one more node. Since no concrete provider
exists yet to define that node's shape, the endpoint is described with three
fixed, technology-neutral ``Fact.data`` keys — ``target_kind`` (a NODE_KINDS
value), ``target_key`` (repo-scoped natural-key parts), and ``target_label``
— the same "fixed dict keys, no interpretation" contract every other
``from_*`` normalizer already uses for its own upstream JSON.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from ..identity import IdentityMap, RepositoryIdentity
from .builder import ModelBuilder
from .schema import EDGE_TYPES, NODE_KINDS

if TYPE_CHECKING:
    from ..profiles.contracts import CapabilityResult

PRODUCER = "evidence/from-capability-results"
_STRUCTURAL_NODE_KINDS = {"repository", "module"}
_NODE_FACT_KINDS = frozenset(kind for kind in NODE_KINDS if kind not in _STRUCTURAL_NODE_KINDS)
_EDGE_FACT_KINDS = frozenset(kind for kind in EDGE_TYPES if kind != "containment")


def _repo_node(builder: ModelBuilder, repo_id: str, repo_identity: RepositoryIdentity) -> str:
    return builder.add_node(
        "repository", [repo_id], label=repo_identity.display_name,
        status="observed", repository_ref=repo_identity.reference, producer=PRODUCER)


def _fact_evidence(fact: Any) -> list[str]:
    return [source_ref.to_string() for source_ref in fact.source_refs]


def _materialize_node(builder: ModelBuilder, repo_id: str, repo_node_id: str,
                      fact: Any) -> str:
    label = str(fact.data.get("label", fact.fact_id))
    node_id = builder.add_node(
        fact.kind, [repo_id, fact.kind, fact.fact_id], label=label,
        status="observed", repository_ref=repo_id, producer=PRODUCER,
        evidence=_fact_evidence(fact), attrs=dict(fact.data))
    builder.add_edge("containment", repo_node_id, node_id, status="observed",
                     producer=PRODUCER)
    return node_id


def _materialize_edge(builder: ModelBuilder, repo_id: str, repo_node_id: str,
                      fact: Any) -> None:
    target_kind = fact.data.get("target_kind")
    target_key = fact.data.get("target_key")
    target_label = fact.data.get("target_label", fact.fact_id)
    if (target_kind not in NODE_KINDS or not isinstance(target_key, list)
            or not target_key or not all(isinstance(item, str) for item in target_key)):
        raise ValueError(
            f"edge fact {fact.fact_id!r} of kind {fact.kind!r} needs a valid "
            "target_kind/target_key/target_label in its data"
        )
    target_id = builder.add_node(
        target_kind, [repo_id, *target_key], label=str(target_label),
        status="observed", repository_ref=repo_id, producer=PRODUCER,
        evidence=_fact_evidence(fact))
    builder.add_edge(
        fact.kind, repo_node_id, target_id, status="observed", producer=PRODUCER,
        evidence=_fact_evidence(fact), discriminator=fact.fact_id)


def load(builder: ModelBuilder, results: Iterable["CapabilityResult"],
         identities: IdentityMap) -> dict[str, Any]:
    """Populate ``builder`` from ``results``; returns a small summary dict."""
    results = list(results)
    nodes_created = 0
    edges_created = 0
    for result in results:
        repo_identity = identities.repository(result.repo_id)
        repo_node_id = _repo_node(builder, result.repo_id, repo_identity)
        for fact in result.facts:
            if fact.kind in _NODE_FACT_KINDS:
                _materialize_node(builder, result.repo_id, repo_node_id, fact)
                nodes_created += 1
            elif fact.kind in _EDGE_FACT_KINDS:
                _materialize_edge(builder, result.repo_id, repo_node_id, fact)
                edges_created += 1
    return {
        "present": bool(results),
        "results": len(results),
        "nodes_created": nodes_created,
        "edges_created": edges_created,
    }
