"""Versioned contracts for evidence-backed Module Drill runs.

The package is deliberately separate from the overview pipeline.  It defines
the technology-neutral data boundary that later Module Drill phases consume;
providers and report generation remain in their owning modules.
"""

from .coverage import Coverage, CoverageStatus
from .model import FeatureClaim, FeatureEdge, FeatureFlow, FeatureNode, ModuleModel
from .protocol import MODULE_TASK_TYPES, schema_for_task_type
from .scope import FeatureSeed, FrontierDisposition, FrontierWorkItem, ModuleScope, ScopeCandidate
from .source import SourceManifest

__all__ = [
    "Coverage",
    "CoverageStatus",
    "FeatureClaim",
    "FeatureEdge",
    "FeatureFlow",
    "FeatureNode",
    "FeatureSeed",
    "FrontierDisposition",
    "FrontierWorkItem",
    "MODULE_TASK_TYPES",
    "ModuleModel",
    "ModuleScope",
    "ScopeCandidate",
    "SourceManifest",
    "schema_for_task_type",
]
