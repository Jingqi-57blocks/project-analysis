"""Deterministic registry for explicitly imported bundled definitions."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import CapabilityProvider, Profile, _validated_id


@dataclass(frozen=True)
class ProfileRegistry:
    """Immutable catalog; deliberately exposes no runtime registration API."""

    profiles: tuple[Profile, ...]
    providers: tuple[CapabilityProvider, ...]

    def __post_init__(self) -> None:
        raw_profiles = tuple(self.profiles)
        raw_providers = tuple(self.providers)
        if not all(isinstance(item, Profile) for item in raw_profiles):
            raise ValueError("registry profiles must be explicit Profile values")
        if not all(isinstance(item, CapabilityProvider) for item in raw_providers):
            raise ValueError("registry providers must implement CapabilityProvider")
        profiles = tuple(sorted(raw_profiles, key=lambda item: item.profile_id))
        providers = tuple(sorted(raw_providers, key=lambda item: item.provider_id))
        object.__setattr__(self, "profiles", profiles)
        object.__setattr__(self, "providers", providers)

        profile_ids = [item.profile_id for item in profiles]
        provider_ids = [item.provider_id for item in providers]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("duplicate profile_id in bundled registry")
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("duplicate provider_id in bundled registry")

        declared_capabilities = {
            capability_id
            for profile in profiles
            for capability_id in profile.capability_ids
        }
        profile_id_set = set(profile_ids)
        provider_capabilities: list[str] = []
        for provider in providers:
            _validated_id(provider.provider_id, "provider_id")
            _validated_id(provider.capability_id, "capability_id")
            try:
                linked_profiles = tuple(provider.profile_ids)
            except TypeError as exc:
                raise ValueError(
                    f"provider {provider.provider_id!r} profile_ids must be iterable"
                ) from exc
            if not linked_profiles:
                raise ValueError(f"provider {provider.provider_id!r} has no profile")
            for profile_id in linked_profiles:
                _validated_id(profile_id, "provider profile_id")
            if len(set(linked_profiles)) != len(linked_profiles):
                raise ValueError(
                    f"provider {provider.provider_id!r} has duplicate profile IDs"
                )
            unknown = sorted(set(linked_profiles) - profile_id_set)
            if unknown:
                raise ValueError(
                    f"provider {provider.provider_id!r} references unknown profiles: "
                    + ", ".join(unknown)
                )
            for profile_id in linked_profiles:
                profile = next(item for item in profiles if item.profile_id == profile_id)
                if provider.capability_id not in profile.capability_ids:
                    raise ValueError(
                        f"provider {provider.provider_id!r} capability is not declared "
                        f"by profile {profile_id!r}"
                    )
            provider_capabilities.append(provider.capability_id)

        missing = sorted(declared_capabilities - set(provider_capabilities))
        if missing:
            raise ValueError("capabilities without bundled providers: " + ", ".join(missing))

    def profile(self, profile_id: str) -> Profile:
        for profile in self.profiles:
            if profile.profile_id == profile_id:
                return profile
        raise KeyError(f"unknown profile {profile_id!r}")

    def provider(self, provider_id: str) -> CapabilityProvider:
        for provider in self.providers:
            if provider.provider_id == provider_id:
                return provider
        raise KeyError(f"unknown provider {provider_id!r}")

    def providers_for_profile(self, profile_id: str) -> tuple[CapabilityProvider, ...]:
        self.profile(profile_id)
        return tuple(
            provider for provider in self.providers if profile_id in provider.profile_ids
        )
