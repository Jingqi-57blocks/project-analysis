"""Explicit production technology catalog.

Profiles contain detection data only.  Capability providers migrate in later
issues, so a detected profile may intentionally have no provider yet.
"""

from __future__ import annotations

from .contracts import CapabilityProvider, Fingerprint, Profile
from .providers import (AccessEvidenceProvider, CallgraphGoProvider,
                        CallgraphJsProvider, DatastoreEvidenceProvider,
                        DependencyRiskProvider, DeployUnitsProvider,
                        DepmapGoProvider, DepmapJsProvider, GitHistoryProvider,
                        IntegrationEvidenceProvider, RouteInventoryProvider,
                        UiRouteLinkageProvider)
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

# Datastore/ORM catalog (57B-80 PR1; unified with discovery/tables.py in PR3).
# (a) The package-derived families below are now the SOLE source of the
# family map ``discovery/tables.py``'s detector uses (``_package_families()``
# derives it from these profiles' own fingerprints instead of a second,
# hand-maintained literal); ``sql`` is the source-extension family from that
# module's own ``SUPPORTED_FAMILIES`` instead (it has no package to key on).
# The five SUPPORTED families carry ("data-model",) because an extractor
# already runs for them in ``discovery/tables.py`` (wrapped, unmodified, by
# the ``datastore-evidence`` capability provider); the rest are detection-only
# until an extractor is wired, matching that module's own "intentionally
# detection-only" comment. (b) ``framework.gorm`` above is capability-less
# (kept out of route-inventory) and coexists DELIBERATELY with
# ``datastore.gorm`` below: same ``gorm.io/gorm`` package, two independent
# capability lenses (route plane vs. datastore plane).
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
    AccessEvidenceProvider(),
    CallgraphGoProvider(), CallgraphJsProvider(),
    DatastoreEvidenceProvider(),
    # DependencyRiskProvider/DeployUnitsProvider/GitHistoryProvider (57B-82
    # A1/A2) are bundled providers with empty profile_ids: no detected facet
    # predicts a deploy artifact's presence, a dependency-risk ecosystem, or
    # a git checkout, so they have nothing to link to. Each is still
    # selected on every target via `universal=True` — registry.py's own
    # carve-out for exactly this shape (a universal provider can never be
    # "dead" the way an unlinked, non-universal one would be).
    DependencyRiskProvider(),
    DeployUnitsProvider(),
    DepmapGoProvider(), DepmapJsProvider(),
    GitHistoryProvider(),
    IntegrationEvidenceProvider(),
    # RouteInventoryProvider/UiRouteLinkageProvider (57B-84 B2): also
    # zero-profile universal, same registry carve-out as DeployUnitsProvider
    # above — no detected facet predicts a route registration/UI call site's
    # presence on its own (an explicit route-inventory/ui-route-linkage
    # profile match is one INPUT to each provider's own applicability gate,
    # not a selection precondition; universal=True is what selects them).
    RouteInventoryProvider(), UiRouteLinkageProvider(),
)


def bundled_registry() -> ProfileRegistry:
    return ProfileRegistry(BUNDLED_PROFILES, BUNDLED_PROVIDERS)


def technology_names(technology_facets: list, kind: str) -> list[str]:
    """Sorted, deduplicated display names for the RESOLVED facets of one
    ``kind`` (e.g. ``"framework"``) in a repo's ``technology_facets`` list.

    Direct replacement for the legacy per-repo "stacks" display block's
    identical ``registry.profile(facet.profile_id).display_name`` derivation
    (``discovery/stacks.py``'s frozen ``StackReport`` -- same values, same
    sort) -- but reads the CURRENT facet list directly, so it picks up every
    facet kind bundled since that block was frozen (e.g. ``datastore``,
    57B-80) rather than the fixed kind set ``STACK_REPORT_FACET_KINDS`` froze
    for byte-identical legacy parity (57B-112 §4 / 57B-118 M4). Only
    ``state == "resolved"`` facets count as a confirmed technology; a
    ``conflicting`` or otherwise unresolved facet is not a name the reader
    should see stated as fact.
    """
    registry = bundled_registry()
    names: set[str] = set()
    for facet in technology_facets:
        if not isinstance(facet, dict):
            continue
        if facet.get("kind") != kind or facet.get("state") != "resolved":
            continue
        profile_id = facet.get("profile_id")
        if not isinstance(profile_id, str):
            continue
        try:
            names.add(registry.profile(profile_id).display_name)
        except KeyError:
            continue  # a facet naming a profile this registry build doesn't carry
    return sorted(names)
