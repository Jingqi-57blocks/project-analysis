"""Graph, semantic claim, and finalized ModuleModel contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .coverage import CLOSURE_STATUS, CoverageStatus
from .scope import FrontierDisposition
from .validation import ContractError, enum, exact_object, ref_list, slug, string_list, text, unique_ids

MODULE_MODEL_VERSION = "module-model/v2"
OBSERVATION_STATES = frozenset({"observed", "inferred", "unresolved"})


@dataclass(frozen=True)
class FeatureNode:
    node_id: str
    kind: str
    repository_ref: str
    observation: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        slug(self.node_id, "node_id")
        slug(self.kind, "node kind")
        text(self.repository_ref, "node repository_ref")
        enum(self.observation, OBSERVATION_STATES, "node observation")

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "kind": self.kind,
                "repository_ref": self.repository_ref, "observation": self.observation,
                "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "FeatureNode":
        row = exact_object(value, {"node_id", "kind", "repository_ref", "observation", "evidence_refs"}, label)
        return cls(slug(row["node_id"], f"{label}.node_id"),
                   slug(row["kind"], f"{label}.kind"),
                   text(row["repository_ref"], f"{label}.repository_ref"),
                   enum(row["observation"], OBSERVATION_STATES, f"{label}.observation"),
                   ref_list(row["evidence_refs"], f"{label}.evidence_refs"))


@dataclass(frozen=True)
class FeatureEdge:
    edge_id: str
    kind: str
    source_node_id: str
    target_node_id: str
    observation: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        slug(self.edge_id, "edge_id")
        slug(self.kind, "edge kind")
        slug(self.source_node_id, "edge source_node_id")
        slug(self.target_node_id, "edge target_node_id")
        enum(self.observation, OBSERVATION_STATES, "edge observation")

    def to_dict(self) -> dict[str, Any]:
        return {"edge_id": self.edge_id, "kind": self.kind,
                "source_node_id": self.source_node_id, "target_node_id": self.target_node_id,
                "observation": self.observation, "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "FeatureEdge":
        row = exact_object(value, {"edge_id", "kind", "source_node_id", "target_node_id", "observation", "evidence_refs"}, label)
        return cls(slug(row["edge_id"], f"{label}.edge_id"),
                   slug(row["kind"], f"{label}.kind"),
                   slug(row["source_node_id"], f"{label}.source_node_id"),
                   slug(row["target_node_id"], f"{label}.target_node_id"),
                   enum(row["observation"], OBSERVATION_STATES, f"{label}.observation"),
                   ref_list(row["evidence_refs"], f"{label}.evidence_refs"))


@dataclass(frozen=True)
class FeatureClaim:
    claim_id: str
    kind: str
    anchor_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    support_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        slug(self.claim_id, "claim_id")
        slug(self.kind, "claim kind")
        if not self.anchor_ids:
            raise ContractError("claim requires at least one graph anchor")

    def to_dict(self) -> dict[str, Any]:
        return {"claim_id": self.claim_id, "kind": self.kind,
                "anchor_ids": list(self.anchor_ids), "evidence_refs": list(self.evidence_refs),
                "support_roles": list(self.support_roles)}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "FeatureClaim":
        row = exact_object(value, {"claim_id", "kind", "anchor_ids", "evidence_refs", "support_roles"}, label)
        return cls(slug(row["claim_id"], f"{label}.claim_id"),
                   slug(row["kind"], f"{label}.kind"),
                   string_list(row["anchor_ids"], f"{label}.anchor_ids", allow_empty=False),
                   ref_list(row["evidence_refs"], f"{label}.evidence_refs"),
                   string_list(row["support_roles"], f"{label}.support_roles", allow_empty=False))


@dataclass(frozen=True)
class FeatureFlow:
    flow_id: str
    edge_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        slug(self.flow_id, "flow_id")
        if not self.edge_ids:
            raise ContractError("flow requires at least one edge")

    def to_dict(self) -> dict[str, Any]:
        return {"flow_id": self.flow_id, "edge_ids": list(self.edge_ids),
                "claim_ids": list(self.claim_ids)}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "FeatureFlow":
        row = exact_object(value, {"flow_id", "edge_ids", "claim_ids"}, label)
        return cls(slug(row["flow_id"], f"{label}.flow_id"),
                   string_list(row["edge_ids"], f"{label}.edge_ids", allow_empty=False),
                   string_list(row["claim_ids"], f"{label}.claim_ids", allow_empty=True))


@dataclass(frozen=True)
class ModuleModel:
    feature_id: str
    nodes: tuple[FeatureNode, ...]
    edges: tuple[FeatureEdge, ...]
    claims: tuple[FeatureClaim, ...]
    flows: tuple[FeatureFlow, ...]
    dispositions: tuple[FrontierDisposition, ...]
    dimension_coverage: dict[str, CoverageStatus]
    closure_status: str

    def __post_init__(self) -> None:
        slug(self.feature_id, "module model feature_id")
        enum(self.closure_status, CLOSURE_STATUS, "module model closure_status")
        unique_ids((item.node_id for item in self.nodes), "module model nodes")
        unique_ids((item.edge_id for item in self.edges), "module model edges")
        unique_ids((item.claim_id for item in self.claims), "module model claims")
        unique_ids((item.flow_id for item in self.flows), "module model flows")
        unique_ids((item.frontier_id for item in self.dispositions), "module model dispositions")
        node_ids = {item.node_id for item in self.nodes}
        edge_ids = {item.edge_id for item in self.edges}
        claim_ids = {item.claim_id for item in self.claims}
        for edge in self.edges:
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
                raise ContractError(f"edge {edge.edge_id} references an unknown node")
        for claim in self.claims:
            if not set(claim.anchor_ids) <= node_ids | edge_ids:
                raise ContractError(f"claim {claim.claim_id} references an unknown graph anchor")
        for flow in self.flows:
            if not set(flow.edge_ids) <= edge_ids or not set(flow.claim_ids) <= claim_ids:
                raise ContractError(f"flow {flow.flow_id} references an unknown edge or claim")
        if self.closure_status == "closed" and any(
                item.state in {"unresolved", "blocked"} for item in self.dispositions):
            raise ContractError("a closed module model cannot have unresolved or blocked frontiers")
        for dimension, coverage in self.dimension_coverage.items():
            slug(dimension, "dimension coverage key")
            if coverage.closure_status != self.closure_status and coverage.closure_status == "closed":
                raise ContractError("a closed dimension cannot exist in an open or blocked module model")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MODULE_MODEL_VERSION,
            "feature_id": self.feature_id,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
            "claims": [item.to_dict() for item in self.claims],
            "flows": [item.to_dict() for item in self.flows],
            "dispositions": [item.to_dict() for item in self.dispositions],
            "dimension_coverage": {key: value.to_dict()
                                   for key, value in sorted(self.dimension_coverage.items())},
            "closure_status": self.closure_status,
        }

    @classmethod
    def from_dict(cls, value: Any, label: str = "module model") -> "ModuleModel":
        row = exact_object(value, {
            "schema_version", "feature_id", "nodes", "edges", "claims", "flows",
            "dispositions", "dimension_coverage", "closure_status",
        }, label)
        if row["schema_version"] != MODULE_MODEL_VERSION:
            raise ContractError(f"{label}.schema_version must be {MODULE_MODEL_VERSION!r}")
        for field in ("nodes", "edges", "claims", "flows", "dispositions"):
            if not isinstance(row[field], list):
                raise ContractError(f"{label}.{field} must be a list")
        if not isinstance(row["dimension_coverage"], dict):
            raise ContractError(f"{label}.dimension_coverage must be an object")
        return cls(
            feature_id=slug(row["feature_id"], f"{label}.feature_id"),
            nodes=tuple(FeatureNode.from_dict(item, f"{label}.nodes[{index}]")
                        for index, item in enumerate(row["nodes"])),
            edges=tuple(FeatureEdge.from_dict(item, f"{label}.edges[{index}]")
                        for index, item in enumerate(row["edges"])),
            claims=tuple(FeatureClaim.from_dict(item, f"{label}.claims[{index}]")
                         for index, item in enumerate(row["claims"])),
            flows=tuple(FeatureFlow.from_dict(item, f"{label}.flows[{index}]")
                        for index, item in enumerate(row["flows"])),
            dispositions=tuple(FrontierDisposition.from_dict(item, f"{label}.dispositions[{index}]")
                               for index, item in enumerate(row["dispositions"])),
            dimension_coverage={
                slug(key, f"{label}.dimension_coverage key"): CoverageStatus.from_dict(
                    item, f"{label}.dimension_coverage[{key!r}]")
                for key, item in row["dimension_coverage"].items()
            },
            closure_status=enum(row["closure_status"], CLOSURE_STATUS,
                                f"{label}.closure_status"),
        )
