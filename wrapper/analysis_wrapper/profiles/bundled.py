"""Explicit production catalog.

Capability migration happens in later issues.  Keeping the initial tuples empty
is intentional: 57B-76 establishes the trusted boundary without changing which
production tools run today.
"""

from __future__ import annotations

from .contracts import CapabilityProvider, Profile
from .registry import ProfileRegistry


BUNDLED_PROFILES: tuple[Profile, ...] = ()
BUNDLED_PROVIDERS: tuple[CapabilityProvider, ...] = ()


def bundled_registry() -> ProfileRegistry:
    return ProfileRegistry(BUNDLED_PROFILES, BUNDLED_PROVIDERS)
