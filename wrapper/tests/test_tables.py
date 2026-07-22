"""DB table extraction + access-type ladder (item 6, v3.6), domain-neutral."""

import importlib.util

import pytest

from analysis_wrapper import astgrep
from analysis_wrapper.discovery import tables

FIXDB = astgrep.RULES_DIR / "fixtures" / "db"
_HAS_SQLGLOT = importlib.util.find_spec("sqlglot") is not None
pytestmark = pytest.mark.skipif(not astgrep.available(), reason="ast-grep not installed")


def test_orm_declaration_and_unresolved_binding():
    ev = tables.generate(str(FIXDB), "db-fix")
    assert ev.available
    # createTable + Go const literal + Go TableName() literal all declare `widgets`.
    assert "declaration" in ev.tables["widgets"]
    assert "schema_write" in ev.tables["widgets"]
    assert not any("create-table" in site
                   for site in ev.tables["widgets"].get("write", []))
    assert "declaration" in ev.tables["gadgets"]  # tableName: 'gadgets'
    kinds = {u["kind"] for u in ev.unresolved}
    assert "go-const" in kinds           # TableName = OtherConst (non-literal)
    assert "gorm-access" in kinds        # db.Table(dynamicVar) — dynamic expression


def test_typed_constant_registry_join():
    ev = tables.generate(str(FIXDB), "db-fix")
    rc = ev.registry_coverage
    assert rc["typed_constants"] == 2 and rc["referenced"] == 2  # TbWidget, TbGadget
    # .Table(constant.TbWidget)…Updates → WRITE widgets (structural join, not literal)
    assert any("access.go" in e for e in ev.tables["widgets"]["write"])
    # .Table(constant.TbGadget)…Find → READ gadgets
    assert any("access.go" in e for e in ev.tables["gadgets"]["read"])
    # Gadget.TableName() returns constant.TbGadget → resolves to a declaration
    assert "declaration" in ev.tables["gadgets"]


def test_distinct_tables_survive_before_view_caps():
    ev = tables.generate(str(FIXDB), "db-fix")
    # Dedup to distinct table NAMES happens at the evidence layer (to_dict),
    # before any downstream view cap — this identity holds in every environment.
    assert ev.to_dict()["distinct_table_count"] == len(ev.tables)
    # ast-grep alone declares widgets + gadgets; the SQL-only `sprockets`
    # (SELECT ... FROM sprockets, with no ORM/const binding anywhere) can only be
    # contributed by sqlglot. So the meaningful multi-table floor is 3 with
    # sqlglot present and 2 without it — the SQL lane is optional (`[sql]` extra).
    assert len(ev.tables) >= (3 if _HAS_SQLGLOT else 2)


def test_evidence_cap_is_disclosed(tmp_path, monkeypatch):
    # generate() must surface the per-bucket 8-site truncation flag from
    # _classify_astgrep as a COVERAGE CAP note (57B-31 canonical-completeness).
    monkeypatch.setattr(tables, "_classify_astgrep",
                        lambda *a, **k: ({}, [], {}, set(), True))
    ev = tables.generate(str(tmp_path), "db-fix")
    assert any("COVERAGE CAP" in n and "8 sites" in n for n in ev.notes)


def test_no_evidence_cap_note_when_within_budget():
    ev = tables.generate(str(FIXDB), "db-fix")   # small fixture, buckets < 8
    assert not any("COVERAGE CAP" in n for n in ev.notes)


def test_every_bucket_is_on_the_ladder():
    ev = tables.generate(str(FIXDB), "db-fix")
    for buckets in ev.tables.values():
        assert set(buckets) <= set(tables.ACCESS_TYPES)


def test_signal_records_astgrep_version_and_path():
    # The ORM/table scan()-derived signal carries the resolved ast-grep version
    # and path, matching the actual per-run probe (57B-37).
    d = tables.generate(str(FIXDB), "db-fix").to_dict()
    p = astgrep.probe()
    assert d["tool"] == "ast-grep"
    assert d["tool_version"] == p.version and d["tool_path"] == p.path
    assert d["version_drift"] == p.drift


def test_detector_reports_family_coverage_separately_from_extraction():
    coverage = tables.generate(str(FIXDB), "db-fix").detector_coverage
    assert coverage["complete"]
    assert "sql" in coverage["detected_families"]
    assert "sql" in coverage["supported_families"]
    assert "sql" in coverage["extracted_families"]


def test_detector_recognizes_family_even_without_extractable_source(tmp_path):
    nested = tmp_path / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text(
        '{"dependencies":{"mongoose":"1.0.0"}}', "utf-8")
    coverage = tables.generate(tmp_path, "sample").detector_coverage
    assert coverage["complete"]
    assert coverage["detected_families"] == ["mongoose"]
    assert coverage["supported_families"] == ["mongoose"]
    assert coverage["extracted_families"] == []


def test_document_store_physical_names_and_logical_models_are_separate():
    ev = tables.generate(str(FIXDB), "db-fix")
    assert "inventory_items" in ev.tables
    metadata = ev.store_metadata["inventory_items"]
    assert metadata["kind"] == "collection"
    assert metadata["physical_name"] == "inventory_items"
    assert metadata["logical_names"] == ["InventoryItem"]
    assert set(metadata["families"]) == {"mongodb-native", "mongoose"}
    logical = [row for row in ev.unresolved
               if row.get("kind") == "mongoose-logical-model"]
    assert logical and logical[0]["logical_name"] == "UnresolvedItem"
    assert "UnresolvedItem" not in ev.tables
    assert ev.store_metadata["runtime_settings"]["families"] == ["mongoose"]


def test_dynamic_document_collection_is_not_guessed():
    ev = tables.generate(str(FIXDB), "db-fix")
    assert "collectionName" not in ev.tables
    kinds = {row["kind"] for row in ev.unresolved}
    assert "mongodb-dynamic-collection" in kinds
    assert "mongoose-dynamic-collection" in kinds


def test_document_metadata_is_order_independent_and_never_defaults_to_relational():
    ev = tables.generate(str(FIXDB), "db-fix")
    assert ev.store_metadata["runtime_settings"] == {
        "kind": "collection", "families": ["mongoose"],
        "physical_name": "runtime_settings", "logical_names": []}


@pytest.mark.skipif(not _HAS_SQLGLOT, reason="sqlglot not installed")
def test_sqlglot_read_write_join_and_explicit_coverage():
    ev = tables.generate(str(FIXDB), "db-fix")
    assert ev.sql_coverage.get("available") and ev.sql_coverage.get("complete")
    assert ev.tables["sprockets"].get("read")        # SELECT FROM sprockets
    assert ev.tables["gadgets"].get("join_ref")      # FOREIGN KEY … REFERENCES gadgets
    assert ev.tables["gadgets"].get("write")         # UPDATE gadgets
    assert ev.tables["widgets"].get("read")          # SELECT … widgets


def test_sql_coverage_failclosed_when_sqlglot_absent(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sqlglot":
            raise ImportError("simulated missing sqlglot")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    ev = tables.generate(str(FIXDB), "db-fix")
    assert ev.sql_coverage.get("available") is False
    assert "NOT parsed" in ev.sql_coverage.get("reason", "")
