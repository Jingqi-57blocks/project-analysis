"""Explicit production technology catalog.

Profiles contain detection data only.  Capability providers migrate in later
issues, so a detected profile may intentionally have no provider yet.
"""

from __future__ import annotations

from .contracts import CapabilityProvider, Fingerprint, Profile
from .providers import (CallgraphGoProvider, CallgraphJsProvider,
                        DatastoreEvidenceProvider, DepmapGoProvider,
                        DepmapJsProvider)
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

# Datastore/ORM catalog (57B-80 PR1). (a) The package-derived families below
# mirror the extraction plane's family map in ``discovery/tables.py``
# (``_PACKAGE_FAMILIES``); ``sql`` is the source-extension family from that
# module's ``SUPPORTED_FAMILIES`` instead (it has no package to key on). PR3
# of 57B-80 will derive ``_PACKAGE_FAMILIES``/``SUPPORTED_FAMILIES`` FROM
# these profiles instead of maintaining both by hand. The five SUPPORTED
# families carry ("data-model",) because an extractor already runs for them
# in ``discovery/tables.py``; the rest are detection-only until an extractor
# is wired, matching that module's own "intentionally detection-only"
# comment. (b) ``framework.gorm`` above is capability-less (kept out of
# route-inventory) and coexists DELIBERATELY with ``datastore.gorm`` below:
# same ``gorm.io/gorm`` package, two independent capability lenses (route
# plane vs. datastore plane).
_DATASTORE_SUPPORTED = (
    ("sequelize", "package-dependency", "sequelize"),
    ("gorm", "go-require", "gorm.io/gorm"),
    ("mongodb-native", "package-dependency", "mongodb"),
    ("mongoose", "package-dependency", "mongoose"),
    ("sql", "source-extension", ".sql"),
)
_DATASTORE_DETECTION_ONLY = (
    ("prisma", (("package-dependency", "@prisma/client"),)),
    ("typeorm", (("package-dependency", "typeorm"),)),
    ("knex", (("package-dependency", "knex"),)),
    ("drizzle", (("package-dependency", "drizzle-orm"),)),
    ("sqlite-driver", (("package-dependency", "better-sqlite3"),
                        ("package-dependency", "sqlite3"))),
    ("mysql-driver", (("package-dependency", "mysql"),
                       ("package-dependency", "mysql2"))),
    ("postgres-driver", (("package-dependency", "pg"),)),
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
    *tuple(
        _profile(
            f"datastore.{profile_id}", "datastore", profile_id,
            ((fingerprint_kind, fingerprint_value),), ("data-model",),
        )
        for profile_id, fingerprint_kind, fingerprint_value in _DATASTORE_SUPPORTED
    ),
    *tuple(
        _profile(f"datastore.{profile_id}", "datastore", profile_id, fingerprints)
        for profile_id, fingerprints in _DATASTORE_DETECTION_ONLY
    ),
)
BUNDLED_PROVIDERS: tuple[CapabilityProvider, ...] = (
    CallgraphGoProvider(), CallgraphJsProvider(),
    DatastoreEvidenceProvider(),
    DepmapGoProvider(), DepmapJsProvider(),
)


def bundled_registry() -> ProfileRegistry:
    return ProfileRegistry(BUNDLED_PROFILES, BUNDLED_PROVIDERS)
