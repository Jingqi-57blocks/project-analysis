"""Explicit production technology catalog.

Profiles contain detection data only.  Capability providers migrate in later
issues, so a detected profile may intentionally have no provider yet.
"""

from __future__ import annotations

from .contracts import CapabilityProvider, Fingerprint, Profile
from .providers import (CallgraphGoProvider, CallgraphJsProvider,
                        DepmapGoProvider, DepmapJsProvider)
from .registry import ProfileRegistry


def _profile(
    profile_id: str,
    kind: str,
    display_name: str,
    fingerprints: tuple[tuple[str, str], ...],
    capabilities: tuple[str, ...] = (),
) -> Profile:
    return Profile(
        profile_id=profile_id,
        kind=kind,
        display_name=display_name,
        fingerprints=tuple(Fingerprint(*item) for item in fingerprints),
        capability_ids=capabilities,
    )


_JS_FRAMEWORKS = (
    ("angular", "@angular/core", "ui-route-linkage"),
    ("express", "express", "route-inventory"),
    ("fastify", "fastify", "route-inventory"),
    ("hapi", "hapi", "route-inventory"),
    ("koa", "koa", "route-inventory"),
    ("nestjs", "@nestjs/core", "route-inventory"),
    ("next", "next", "ui-route-linkage"),
    ("nuxt", "nuxt", "ui-route-linkage"),
    ("react", "react", "ui-route-linkage"),
    ("svelte", "svelte", "ui-route-linkage"),
    ("vite", "vite", "ui-route-linkage"),
    ("vue", "vue", "ui-route-linkage"),
    ("webpack", "webpack", "ui-route-linkage"),
)
_GO_FRAMEWORKS = (
    ("chi", "github.com/go-chi/chi"),
    ("echo", "github.com/labstack/echo"),
    ("fiber", "github.com/gofiber/fiber"),
    ("gin", "github.com/gin-gonic/gin"),
    ("gorilla-mux", "github.com/gorilla/mux"),
    ("gorm", "gorm.io/gorm"),
)


BUNDLED_PROFILES: tuple[Profile, ...] = (
    _profile(
        "ecosystem.go-module", "ecosystem", "go-module",
        (("manifest-file", "go.mod"),),
    ),
    _profile(
        "ecosystem.node", "ecosystem", "node",
        (("manifest-file", "package.json"),),
    ),
    _profile(
        "language.go", "language", "go",
        (("manifest-file", "go.mod"), ("source-extension", ".go")),
        ("callgraph", "dependency-map"),
    ),
    _profile(
        "language.javascript", "language", "js",
        tuple(("source-extension", extension) for extension in
              (".js", ".jsx", ".mjs", ".cjs"))
        + (("manifest-default", "package.json"),),
        ("callgraph", "dependency-map"),
    ),
    _profile(
        "language.typescript", "language", "ts",
        tuple(("source-extension", extension) for extension in
              (".ts", ".tsx", ".mts", ".cts"))
        + tuple(("config-file", name) for name in
                ("tsconfig.json", "tsconfig.app.json", "tsconfig.base.json")),
        ("callgraph", "dependency-map"),
    ),
    _profile(
        "repository.unclassified", "repository-trait", "unclassified",
        (("fallback", "unclassified-files"),),
    ),
    *tuple(
        _profile(
            f"framework.{profile_id}", "framework", dependency,
            (("package-dependency", dependency),), (capability,),
        )
        for profile_id, dependency, capability in _JS_FRAMEWORKS
    ),
    *tuple(
        _profile(
            f"framework.{profile_id}", "framework", dependency,
            (("go-require", dependency),),
            (() if profile_id == "gorm" else ("route-inventory",)),
        )
        for profile_id, dependency in _GO_FRAMEWORKS
    ),
)
BUNDLED_PROVIDERS: tuple[CapabilityProvider, ...] = (
    CallgraphGoProvider(), CallgraphJsProvider(),
    DepmapGoProvider(), DepmapJsProvider(),
)


def bundled_registry() -> ProfileRegistry:
    return ProfileRegistry(BUNDLED_PROFILES, BUNDLED_PROVIDERS)
