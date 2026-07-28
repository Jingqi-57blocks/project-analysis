"""Domain-neutral acceptance-fixture contract for Module Drill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .coverage import CLOSURE_STATUS
from .model import ModuleModel
from .scope import ModuleScope
from .source import SourceManifest
from .validation import ContractError, exact_object, sha256_json, slug, string_list

FIXTURE_VERSION = "module-drill-fixture/v1"


@dataclass(frozen=True)
class LinkageMutation:
    mutation_id: str
    remove_edge_id: str
    expected_closure_status: str
    expected_execution_status: str

    @classmethod
    def from_dict(cls, value: Any, label: str) -> "LinkageMutation":
        row = exact_object(value, {
            "mutation_id", "remove_edge_id", "expected_closure_status", "expected_execution_status",
        }, label)
        closure = row["expected_closure_status"]
        if closure not in CLOSURE_STATUS - {"closed"}:
            raise ContractError(f"{label}.expected_closure_status must be open or blocked")
        if row["expected_execution_status"] not in {"partial", "unavailable", "failed"}:
            raise ContractError(f"{label}.expected_execution_status must be reduced coverage")
        return cls(slug(row["mutation_id"], f"{label}.mutation_id"),
                   slug(row["remove_edge_id"], f"{label}.remove_edge_id"),
                   closure, row["expected_execution_status"])


@dataclass(frozen=True)
class AcceptanceFixture:
    manifest: SourceManifest
    scope: ModuleScope
    model: ModuleModel
    expected_path_edge_ids: tuple[str, ...]
    mutations: tuple[LinkageMutation, ...]

    def __post_init__(self) -> None:
        if self.scope.source_manifest_digest != sha256_json(self.manifest.to_dict()):
            raise ContractError("fixture scope does not bind the supplied source manifest")
        if self.scope.feature_id != self.model.feature_id:
            raise ContractError("fixture scope and model must describe the same feature")
        repository_refs = self.manifest.repository_refs
        scope_repositories = {
            seed.repository_ref for seed in self.scope.seeds
        } | {
            repository_ref for candidate in self.scope.candidates
            for repository_ref in candidate.repository_refs
        }
        model_repositories = {node.repository_ref for node in self.model.nodes}
        if not scope_repositories <= repository_refs:
            raise ContractError("fixture scope references a repository outside its source manifest")
        if not model_repositories <= repository_refs:
            raise ContractError("fixture model references a repository outside its source manifest")
        edge_ids = {edge.edge_id for edge in self.model.edges}
        if not set(self.expected_path_edge_ids) <= edge_ids:
            raise ContractError("fixture expected path references an unknown edge")
        if not self.expected_path_edge_ids:
            raise ContractError("fixture expected path must not be empty")
        disposition_ids = {item.frontier_id for item in self.model.dispositions}
        scope_ids = {item.frontier_id for item in self.scope.frontiers}
        if disposition_ids != scope_ids:
            raise ContractError("fixture must dispose every scope frontier exactly once")
        for mutation in self.mutations:
            if mutation.remove_edge_id not in edge_ids:
                raise ContractError(f"fixture mutation {mutation.mutation_id} references an unknown edge")

    @classmethod
    def from_dict(cls, value: Any) -> "AcceptanceFixture":
        row = exact_object(value, {
            "schema_version", "source_manifest", "module_scope", "module_model",
            "expected_path_edge_ids", "mutations",
        }, "module drill fixture")
        if row["schema_version"] != FIXTURE_VERSION:
            raise ContractError(f"fixture schema_version must be {FIXTURE_VERSION!r}")
        if not isinstance(row["mutations"], list):
            raise ContractError("fixture mutations must be a list")
        return cls(
            manifest=SourceManifest.from_dict(row["source_manifest"], "fixture.source_manifest"),
            scope=ModuleScope.from_dict(row["module_scope"], "fixture.module_scope"),
            model=ModuleModel.from_dict(row["module_model"], "fixture.module_model"),
            expected_path_edge_ids=string_list(row["expected_path_edge_ids"],
                                               "fixture.expected_path_edge_ids", allow_empty=False),
            mutations=tuple(LinkageMutation.from_dict(item, f"fixture.mutations[{index}]")
                            for index, item in enumerate(row["mutations"])),
        )
