"""57B-80: the datastore/ORM profile catalog.

Two concerns:

1. The bundled catalog carries exactly the 12 documented ``datastore.*``
   profiles, with the right fingerprints and capability split (five
   supported families carry ``("data-model",)``; the rest are
   detection-only, matching ``discovery/tables.py``'s own
   ``_PACKAGE_FAMILIES``/``SUPPORTED_FAMILIES`` split that this catalog
   mirrors).
2. Adding a non-language, source-extension-fingerprinting profile
   (``datastore.sql``) must not perturb the language plane: this file pins
   the two ``detection.py`` scoping fixes (``claimed_extensions`` and
   ``language_hits_by_scope`` restricted to ``kind == "language"``) and
   confirms datastore facets stay invisible to the (callgraph/depmap)
   lane providers, ``profiles.selection`` predicates, and the frameworks
   display list. PR1 pinned that NO provider yet matched any datastore.*
   profile; 57B-80 PR2 adds ``datastore-evidence`` (see
   ``test_exactly_one_bundled_provider_matches_the_datastore_catalog``
   below, and its own dedicated coverage in
   ``test_datastore_evidence_provider.py``), so that invariant is now
   deliberately the opposite one.
"""

from __future__ import annotations

import json

from analysis_wrapper.profiles import selection
from analysis_wrapper.profiles.bundled import bundled_registry
from analysis_wrapper.profiles.contracts import Fingerprint
from analysis_wrapper.profiles.detection import detect
from analysis_wrapper.targetspec import RepoTarget, TechnologyFacet, stable_repo_id

_SUPPORTED_FINGERPRINTS = {
    "datastore.sequelize": (("package-dependency", "sequelize"),),
    "datastore.gorm": (("go-require", "gorm.io/gorm"),),
    "datastore.mongodb-native": (("package-dependency", "mongodb"),),
    "datastore.mongoose": (("package-dependency", "mongoose"),),
    "datastore.sql": (("source-extension", ".sql"),),
}
_DETECTION_ONLY_FINGERPRINTS = {
    "datastore.prisma": (("package-dependency", "@prisma/client"),),
    "datastore.typeorm": (("package-dependency", "typeorm"),),
    "datastore.knex": (("package-dependency", "knex"),),
    "datastore.drizzle": (("package-dependency", "drizzle-orm"),),
    "datastore.sqlite-driver": (("package-dependency", "better-sqlite3"),
                                ("package-dependency", "sqlite3")),
    "datastore.mysql-driver": (("package-dependency", "mysql"),
                               ("package-dependency", "mysql2")),
    "datastore.postgres-driver": (("package-dependency", "pg"),),
}


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _facets(report):
    return {facet.profile_id: facet for facet in report.facets}


# --- (a) catalog pins --------------------------------------------------------

def test_bundled_registry_carries_exactly_the_datastore_catalog():
    registry = bundled_registry()
    datastore_profiles = {
        profile.profile_id: profile for profile in registry.profiles
        if profile.kind == "datastore"
    }
    expected_ids = set(_SUPPORTED_FINGERPRINTS) | set(_DETECTION_ONLY_FINGERPRINTS)
    assert set(datastore_profiles) == expected_ids
    assert len(datastore_profiles) == 12

    for profile_id, fingerprints in _SUPPORTED_FINGERPRINTS.items():
        profile = datastore_profiles[profile_id]
        assert profile.kind == "datastore"
        assert profile.fingerprints == tuple(Fingerprint(*item) for item in fingerprints)
        assert profile.capability_ids == ("data-model",)

    for profile_id, fingerprints in _DETECTION_ONLY_FINGERPRINTS.items():
        profile = datastore_profiles[profile_id]
        assert profile.kind == "datastore"
        assert profile.fingerprints == tuple(Fingerprint(*item) for item in fingerprints)
        assert profile.capability_ids == ()


# --- (b) detection fixture ---------------------------------------------------

def test_datastore_facets_join_the_language_and_ecosystem_facets_already_emitted(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"dependencies": {
        "sequelize": "6.35.0", "mongoose": "7.6.0", "@prisma/client": "5.6.0",
    }}))
    _write(tmp_path / "backend" / "go.mod",
           "module example.com/backend\n\nrequire gorm.io/gorm v1.25.5\n")
    _write(tmp_path / "migrations" / "x.sql", "CREATE TABLE widgets (id INT);\n")

    facets = _facets(detect(tmp_path))

    # New, additive datastore facets.
    for profile_id in ("datastore.sequelize", "datastore.mongoose",
                       "datastore.prisma", "datastore.gorm", "datastore.sql"):
        assert profile_id in facets
        assert facets[profile_id].kind == "datastore"

    # Facets this fixture already emitted before this PR are untouched.
    assert "ecosystem.node" in facets
    assert "language.javascript" in facets
    assert "ecosystem.go-module" in facets
    assert "language.go" in facets


# --- (c) NO-LEAK pins --------------------------------------------------------

def test_manifest_default_javascript_facet_survives_a_sibling_sql_file(tmp_path):
    """The language_hits_by_scope suppression fix: a datastore.sql source hit
    in the same directory as a package.json must not mask the
    language.javascript manifest-default fallback."""
    _write(tmp_path / "package.json", "{}")
    _write(tmp_path / "schema" / "init.sql", "CREATE TABLE t (id INT);\n")

    facets = _facets(detect(tmp_path))

    assert "language.javascript" in facets
    assert facets["language.javascript"].confidence == "medium"
    assert "datastore.sql" in facets


def test_sql_files_remain_in_unclassified_inventory_despite_the_datastore_facet(tmp_path):
    """The claimed_extensions fix: unclassified_file_inventory tracks what no
    LANGUAGE profile claims, so a non-language datastore.sql fingerprint must
    not exempt .sql files from it — inventory is unchanged vs before this PR."""
    _write(tmp_path / "package.json", "{}")
    _write(tmp_path / "schema" / "init.sql", "CREATE TABLE t (id INT);\n")

    report = detect(tmp_path)

    assert any(row["extension"] == ".sql" for row in report.unclassified_inventory)


def test_datastore_only_facets_do_not_perturb_selection_predicates(tmp_path):
    repo = tmp_path / "datastore-only"
    repo.mkdir()
    target = RepoTarget(
        repo_id=stable_repo_id(str(repo)), path=str(repo),
        facets=[TechnologyFacet(
            "datastore.sequelize", "datastore", ["."],
            ["package.json#dependency:sequelize"],
        )],
    )
    assert not selection.is_go_target(target)
    assert not selection.is_node_target(target)
    assert not selection.is_ts_target(target)
    assert selection.family(target) == "other"


def test_exactly_one_bundled_provider_matches_the_datastore_catalog():
    """PR1 pinned "no provider exists yet" for every datastore.* profile;
    57B-80 PR2 deliberately ends that by adding ``datastore-evidence``, so
    this test now pins the OPPOSITE, equally explicit invariant: exactly one
    bundled provider is linked to datastore.* profiles (the five SUPPORTED
    ones, per ``discovery/tables.py``'s own extractor set — the seven
    detection-only ones carry no capability at all, so no provider could
    validly link to them; see ``ProfileRegistry``'s own capability-linkage
    check), and no OTHER bundled provider (still just the four lane
    providers) unexpectedly matches any of them."""
    registry = bundled_registry()
    datastore_ids = {
        profile.profile_id for profile in registry.profiles if profile.kind == "datastore"
    }
    assert datastore_ids  # sanity: the catalog under test is non-empty
    matches_by_provider = {
        provider.provider_id: set(provider.profile_ids) & datastore_ids
        for provider in registry.providers
    }
    matching_providers = {
        provider_id: matched for provider_id, matched in matches_by_provider.items() if matched
    }
    assert set(matching_providers) == {"datastore-evidence"}
    assert matching_providers["datastore-evidence"] == {
        "datastore.sequelize", "datastore.gorm", "datastore.mongodb-native",
        "datastore.mongoose", "datastore.sql",
    }


def test_no_datastore_profile_has_kind_framework():
    registry = bundled_registry()
    framework_ids = {p.profile_id for p in registry.profiles if p.kind == "framework"}
    datastore_ids = {p.profile_id for p in registry.profiles if p.kind == "datastore"}
    assert not (framework_ids & datastore_ids)
