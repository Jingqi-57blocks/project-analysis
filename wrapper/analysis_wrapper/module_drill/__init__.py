"""Technology-neutral contracts shared by Module Drill stages.

The package deliberately owns only normalized module scope and run-layout
contracts. Scope discovery, evidence extraction, lifecycle orchestration, and
report generation are separate follow-up responsibilities.
"""

from .contracts import (
    MODULE_SCOPE_VERSION,
    Boundary,
    FindingHint,
    ModuleCoverage,
    ModuleIdentity,
    ModuleScope,
    ModuleScopeRequest,
    OverviewLineage,
    OwnedLocation,
    ProjectSnapshot,
    RepositorySnapshot,
    ScopeAlternative,
    ScopeResolutionError,
    Selector,
    ScopeProvider,
    load_scope,
    resolve_scope,
    write_scope,
)
from .layout import MODULE_RUN_VERSION, ModuleRunLayout, create_module_run, mint_module_run_id
from .overview import OverviewScopeProvider
from .standalone import StandaloneScopeProvider
from .evidence import (MODULE_EVIDENCE_VERSION, ModuleEvidence, ModuleFact, SourceRead,
                       VerifiedBoundary, build_module_evidence, load_module_evidence,
                       write_module_evidence)

__all__ = [
    "MODULE_RUN_VERSION",
    "MODULE_SCOPE_VERSION",
    "MODULE_EVIDENCE_VERSION",
    "Boundary",
    "FindingHint",
    "ModuleCoverage",
    "ModuleEvidence",
    "ModuleFact",
    "ModuleIdentity",
    "ModuleRunLayout",
    "ModuleScope",
    "ModuleScopeRequest",
    "OverviewLineage",
    "OverviewScopeProvider",
    "OwnedLocation",
    "ProjectSnapshot",
    "RepositorySnapshot",
    "ScopeAlternative",
    "ScopeProvider",
    "ScopeResolutionError",
    "StandaloneScopeProvider",
    "SourceRead",
    "VerifiedBoundary",
    "build_module_evidence",
    "load_module_evidence",
    "write_module_evidence",
    "Selector",
    "create_module_run",
    "mint_module_run_id",
    "load_scope",
    "resolve_scope",
    "write_scope",
]
