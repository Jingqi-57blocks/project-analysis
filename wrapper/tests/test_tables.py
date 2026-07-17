"""DB table extraction + access-type ladder (item 6, v3.6), domain-neutral."""

import importlib.util

import pytest

from doctor_wrapper import astgrep
from doctor_wrapper.discovery import tables

FIXDB = astgrep.RULES_DIR / "fixtures" / "db"
_HAS_SQLGLOT = importlib.util.find_spec("sqlglot") is not None
pytestmark = pytest.mark.skipif(not astgrep.available(), reason="ast-grep not installed")


def test_orm_declaration_and_unresolved_binding():
    ev = tables.generate(str(FIXDB), "db-fix")
    assert ev.available
    # createTable + Go const literal + Go TableName() literal all declare `widgets`.
    assert "declaration" in ev.tables["widgets"]
    assert "write" in ev.tables["widgets"]        # createTable/dropTable are DDL writes
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
    assert ev.to_dict()["distinct_table_count"] == len(ev.tables) >= 3


def test_every_bucket_is_on_the_ladder():
    ev = tables.generate(str(FIXDB), "db-fix")
    for buckets in ev.tables.values():
        assert set(buckets) <= set(tables.ACCESS_TYPES)


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
