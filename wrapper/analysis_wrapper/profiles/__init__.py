"""Lightweight, bundled analysis profile contracts.

This package is deliberately not a plugin platform.  Production definitions are
imported explicitly by :mod:`analysis_wrapper.profiles.bundled`; target projects
cannot register code, modules, or callbacks.
"""

from .contracts import (
    ArtifactRef,
    CapabilityProvider,
    CapabilityResult,
    Fingerprint,
    Profile,
    RunContext,
    ToolAccess,
    run_provider,
)
from .registry import ProfileRegistry
from .tool_access import ExecutorToolAccess

__all__ = [
    "ArtifactRef",
    "CapabilityProvider",
    "CapabilityResult",
    "ExecutorToolAccess",
    "Fingerprint",
    "Profile",
    "ProfileRegistry",
    "RunContext",
    "ToolAccess",
    "run_provider",
]
