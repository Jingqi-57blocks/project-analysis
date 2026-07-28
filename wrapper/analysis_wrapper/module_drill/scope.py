"""Feature-scope and frontier contracts for Module Drill v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .coverage import CLOSURE_STATUS, Coverage
from .validation import ContractError, enum, exact_object, ref_list, sha256, slug, string_list, text, unique_ids

MODULE_SCOPE_VERSION = "module-scope/v3"
SEED_KINDS = frozenset({
    "ui-action", "route", "symbol", "package", "datastore", "job-event", "path", "module",
})
CANDIDATE_DISPOSITIONS = frozenset({"selected", "alternative", "ambiguous", "rejected", "no-match"})
FRONTIER_DIRECTIONS = frozenset({"inbound", "outbound"})
FRONTIER_STATES = frozenset({"pending", "expanded", "terminal", "excluded", "unresolved", "blocked"})


@dataclass(frozen=True)
class FeatureSeed:
    seed_id: str
    kind: str
    repository_ref: str
    evidence_refs: tuple[str, ...]
    coverage: Coverage

    def __post_init__(self) -> None:
        slug(self.seed_id, "seed_id")
        enum(self.kind, SEED_KINDS, "seed kind")
        text(self.repository_ref, "seed repository_ref")

    def to_dict(self) -> dict[str, Any]:
        return {"seed_id": self.seed_id, "kind": self.kind,
                "repository_ref": self.repository_ref,
                "evidence_refs": list(self.evidence_refs),
                "coverage": self.coverage.to_dict()}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "FeatureSeed":
        row = exact_object(value, {"seed_id", "kind", "repository_ref", "evidence_refs", "coverage"}, label)
        return cls(slug(row["seed_id"], f"{label}.seed_id"),
                   enum(row["kind"], SEED_KINDS, f"{label}.kind"),
                   text(row["repository_ref"], f"{label}.repository_ref"),
                   ref_list(row["evidence_refs"], f"{label}.evidence_refs"),
                   Coverage.from_dict(row["coverage"], f"{label}.coverage"))


@dataclass(frozen=True)
class ScopeCandidate:
    """A deterministic candidate before an LLM may rank existing IDs."""

    candidate_id: str
    seed_ids: tuple[str, ...]
    repository_refs: tuple[str, ...]
    disposition: str
    reason: str

    def __post_init__(self) -> None:
        slug(self.candidate_id, "candidate_id")
        if not self.seed_ids:
            raise ContractError("scope candidate requires at least one seed")
        enum(self.disposition, CANDIDATE_DISPOSITIONS, "candidate disposition")
        text(self.reason, "candidate reason")

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, "seed_ids": list(self.seed_ids),
                "repository_refs": list(self.repository_refs), "disposition": self.disposition,
                "reason": self.reason}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "ScopeCandidate":
        row = exact_object(value, {
            "candidate_id", "seed_ids", "repository_refs", "disposition", "reason",
        }, label)
        return cls(
            candidate_id=slug(row["candidate_id"], f"{label}.candidate_id"),
            seed_ids=string_list(row["seed_ids"], f"{label}.seed_ids", allow_empty=False),
            repository_refs=string_list(row["repository_refs"], f"{label}.repository_refs", allow_empty=False),
            disposition=enum(row["disposition"], CANDIDATE_DISPOSITIONS, f"{label}.disposition"),
            reason=text(row["reason"], f"{label}.reason"),
        )


@dataclass(frozen=True)
class FrontierWorkItem:
    frontier_id: str
    anchor_id: str
    edge_kind: str
    direction: str
    wave: int
    cycle_key: str
    evidence_refs: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        slug(self.frontier_id, "frontier_id")
        slug(self.anchor_id, "frontier anchor_id")
        slug(self.edge_kind, "frontier edge_kind")
        enum(self.direction, FRONTIER_DIRECTIONS, "frontier direction")
        if isinstance(self.wave, bool) or not isinstance(self.wave, int) or self.wave < 0:
            raise ContractError("frontier wave must be a non-negative integer")
        slug(self.cycle_key, "frontier cycle_key")
        text(self.reason, "frontier reason")

    def to_dict(self) -> dict[str, Any]:
        return {"frontier_id": self.frontier_id, "anchor_id": self.anchor_id,
                "edge_kind": self.edge_kind, "direction": self.direction,
                "wave": self.wave, "cycle_key": self.cycle_key,
                "evidence_refs": list(self.evidence_refs), "reason": self.reason}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "FrontierWorkItem":
        row = exact_object(value, {
            "frontier_id", "anchor_id", "edge_kind", "direction", "wave", "cycle_key",
            "evidence_refs", "reason",
        }, label)
        return cls(slug(row["frontier_id"], f"{label}.frontier_id"),
                   slug(row["anchor_id"], f"{label}.anchor_id"),
                   slug(row["edge_kind"], f"{label}.edge_kind"),
                   enum(row["direction"], FRONTIER_DIRECTIONS, f"{label}.direction"),
                   row["wave"],
                   slug(row["cycle_key"], f"{label}.cycle_key"),
                   ref_list(row["evidence_refs"], f"{label}.evidence_refs"),
                   text(row["reason"], f"{label}.reason"))


@dataclass(frozen=True)
class FrontierDisposition:
    frontier_id: str
    state: str
    reason: str
    resulting_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        slug(self.frontier_id, "frontier disposition frontier_id")
        enum(self.state, FRONTIER_STATES - {"pending"}, "frontier disposition state")
        text(self.reason, "frontier disposition reason")

    def to_dict(self) -> dict[str, Any]:
        return {"frontier_id": self.frontier_id, "state": self.state,
                "reason": self.reason, "resulting_ids": list(self.resulting_ids)}

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "FrontierDisposition":
        row = exact_object(value, {"frontier_id", "state", "reason", "resulting_ids"}, label)
        return cls(slug(row["frontier_id"], f"{label}.frontier_id"),
                   enum(row["state"], FRONTIER_STATES - {"pending"}, f"{label}.state"),
                   text(row["reason"], f"{label}.reason"),
                   string_list(row["resulting_ids"], f"{label}.resulting_ids", allow_empty=True))


@dataclass(frozen=True)
class ModuleScope:
    feature_id: str
    selector: str
    source_manifest_digest: str
    selected_candidate_id: str
    candidates: tuple[ScopeCandidate, ...]
    seeds: tuple[FeatureSeed, ...]
    frontiers: tuple[FrontierWorkItem, ...]
    closure_status: str

    def __post_init__(self) -> None:
        slug(self.feature_id, "feature_id")
        text(self.selector, "selector", multiline=True)
        sha256(self.source_manifest_digest, "source_manifest_digest")
        slug(self.selected_candidate_id, "selected_candidate_id")
        if not self.candidates:
            raise ContractError("module scope requires deterministic scope candidates")
        if not self.seeds:
            raise ContractError("module scope requires at least one feature seed")
        unique_ids((candidate.candidate_id for candidate in self.candidates),
                   "module scope candidates")
        unique_ids((seed.seed_id for seed in self.seeds), "module scope seeds")
        unique_ids((item.frontier_id for item in self.frontiers), "module scope frontiers")
        candidate_by_id = {candidate.candidate_id: candidate for candidate in self.candidates}
        selected = candidate_by_id.get(self.selected_candidate_id)
        if selected is None:
            raise ContractError("module scope selected_candidate_id must name a scope candidate")
        if selected.disposition != "selected":
            raise ContractError("module scope selected candidate must have selected disposition")
        selected_ids = {candidate.candidate_id for candidate in self.candidates
                        if candidate.disposition == "selected"}
        if selected_ids != {self.selected_candidate_id}:
            raise ContractError("module scope must have exactly one selected candidate")
        seed_ids = {seed.seed_id for seed in self.seeds}
        for candidate in self.candidates:
            if not set(candidate.seed_ids) <= seed_ids:
                raise ContractError(
                    f"scope candidate {candidate.candidate_id} references an unknown seed")
        enum(self.closure_status, CLOSURE_STATUS, "module scope closure_status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MODULE_SCOPE_VERSION,
            "feature_id": self.feature_id,
            "selector": self.selector,
            "source_manifest_digest": self.source_manifest_digest,
            "selected_candidate_id": self.selected_candidate_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "seeds": [seed.to_dict() for seed in self.seeds],
            "frontiers": [item.to_dict() for item in self.frontiers],
            "closure_status": self.closure_status,
        }

    @classmethod
    def from_dict(cls, value: Any, label: str = "module scope") -> "ModuleScope":
        row = exact_object(value, {
            "schema_version", "feature_id", "selector", "source_manifest_digest",
            "selected_candidate_id", "candidates", "seeds", "frontiers", "closure_status",
        }, label)
        if row["schema_version"] != MODULE_SCOPE_VERSION:
            raise ContractError(f"{label}.schema_version must be {MODULE_SCOPE_VERSION!r}")
        if not all(isinstance(row[field], list) for field in ("candidates", "seeds", "frontiers")):
            raise ContractError(f"{label}.candidates, seeds and frontiers must be lists")
        return cls(
            feature_id=slug(row["feature_id"], f"{label}.feature_id"),
            selector=text(row["selector"], f"{label}.selector", multiline=True),
            source_manifest_digest=sha256(row["source_manifest_digest"],
                                          f"{label}.source_manifest_digest"),
            selected_candidate_id=slug(row["selected_candidate_id"],
                                       f"{label}.selected_candidate_id"),
            candidates=tuple(ScopeCandidate.from_dict(item, f"{label}.candidates[{index}]")
                             for index, item in enumerate(row["candidates"])),
            seeds=tuple(FeatureSeed.from_dict(item, f"{label}.seeds[{index}]")
                        for index, item in enumerate(row["seeds"])),
            frontiers=tuple(FrontierWorkItem.from_dict(item, f"{label}.frontiers[{index}]")
                            for index, item in enumerate(row["frontiers"])),
            closure_status=enum(row["closure_status"], CLOSURE_STATUS,
                                f"{label}.closure_status"),
        )
