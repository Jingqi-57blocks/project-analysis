"""Database table extraction with an access-type ladder (item 6, v3.6).

ORM table declarations + access sites come from ast-grep rules (Sequelize
queryInterface.*/define tableName, GORM TableName constants + methods); raw SQL
DDL comes from SQLGlot. Every finding is tagged on the ladder —
``declaration`` / ``schema_write`` / ``write`` / ``read`` / ``join_ref`` /
``same_name`` / ``unresolved`` — so schema changes cannot be promoted to
application data writers, and NAME MATCHING ALONE is never emitted as confirmed
shared persistence. Cross-file constant/model→table binding is NOT attempted (the table
of a constant- or model-bound access is recorded ``unresolved``).

SQL coverage (attempted dialect, parse failures, unparsed files) is explicit:
best-effort is never reported complete.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import astgrep

_RULE = "orm-table.yml"
_SKIP_DIRS = {"node_modules", "vendor", ".git", "dist", "build", "coverage"}
_SQL_MAX_BYTES = 1_000_000

# The ladder. `same_name` = the name occurs but in no recognized access; a bare
# name match must land here (or `unresolved`), never in a stronger bucket.
ACCESS_TYPES = (
    "declaration", "schema_write", "write", "read", "join_ref", "same_name",
    "unresolved",
)

# Migration / DDL / schema directories carry first-class table evidence
# (createTable, CREATE TABLE) — the TABLE lane scans them DELIBERATELY even when
# discovery's tier2 marks them generated noise for other lanes.
_DDL_EXEMPT = re.compile(r"(?i)(migrat|schema|ddl|(?:^|[-_])sql(?:$|[-_]))")
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,62}$")
_STR_ARG = re.compile(r"""['"`]([^'"`]+)['"`]""")
_GO_CONST_SPEC = re.compile(r'^(\w+)\s+TableName\s*=\s*"([^"]+)"')
_GO_METHOD_LIT = re.compile(r'return\s+"([^"]+)"')
_GO_METHOD_CONST = re.compile(r'return\s+constant\.(\w+)')
_GO_TABLE_LIT = re.compile(r'\.Table\(\s*"([^"]+)"')
_GO_TABLE_CONST = re.compile(r'\.Table\(\s*constant\.(\w+)')

SUPPORTED_FAMILIES = ("gorm", "sequelize", "sql")
_PACKAGE_FAMILIES = {
    "sequelize": "sequelize",
    "gorm.io/gorm": "gorm",
    # These are intentionally detection-only until their extractors are wired.
    "@prisma/client": "prisma",
    "better-sqlite3": "sqlite-driver",
    "drizzle-orm": "drizzle",
    "knex": "knex",
    "mongodb": "mongodb-native",
    "mongoose": "mongoose",
    "mysql": "mysql-driver",
    "mysql2": "mysql-driver",
    "pg": "postgres-driver",
    "sqlite3": "sqlite-driver",
    "typeorm": "typeorm",
}


@dataclass
class TableEvidence:
    available: bool
    tables: dict = field(default_factory=dict)          # name -> {access -> [evidence]}
    unresolved: list = field(default_factory=list)      # bindings/accesses w/o a resolvable table
    registry_coverage: dict = field(default_factory=dict)
    sql_coverage: dict = field(default_factory=dict)
    detector_coverage: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    # ast-grep version/path/drift for this scan()-derived signal (57B-37). The
    # SQL sub-lane (SQLGlot) records its own coverage under ``sql_coverage``.
    astgrep: dict = field(default_factory=astgrep.unavailable_provenance)

    def to_dict(self) -> dict:
        # Distinct tables are deduped by name HERE, before any downstream view cap,
        # so a cap can never truncate the set of distinct tables.
        return {
            "available": self.available,
            "distinct_table_count": len(self.tables),
            "tables": {name: {a: ev for a, ev in sorted(buckets.items())}
                       for name, buckets in sorted(self.tables.items())},
            "unresolved": self.unresolved,
            "registry_coverage": self.registry_coverage,
            "sql_coverage": self.sql_coverage,
            "detector_coverage": self.detector_coverage,
            "notes": self.notes,
            **self.astgrep,
        }


def _detect_families(root: Path, tier2: set[str]) -> dict:
    """Detect datastore families independently from extraction success.

    The detector deliberately uses only manifests and file types.  Completing a
    filesystem walk does not mean every datastore syntax is understood; unknown
    families are therefore explicit instead of being mistaken for absence.
    """
    detected: set[str] = set()
    evidence: dict[str, list[str]] = defaultdict(list)
    errors: list[str] = []

    def add(family: str, where: str) -> None:
        detected.add(family)
        if len(evidence[family]) < 8 and where not in evidence[family]:
            evidence[family].append(where)

    manifest_paths: list[Path] = []
    stack = [root]
    while stack:
        base = stack.pop()
        try:
            entries = sorted(base.iterdir())
        except OSError as exc:
            errors.append(f"{base.relative_to(root) or '.'}: {exc}")
            continue
        for entry in entries:
            if entry.is_dir():
                rel0 = entry.relative_to(root).parts[0]
                if entry.name not in _SKIP_DIRS and rel0 not in tier2 \
                        and not entry.name.startswith("."):
                    stack.append(entry)
            elif entry.name in {"package.json", "go.mod"}:
                manifest_paths.append(entry)

    for path in sorted(manifest_paths):
        name = path.name
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        if name == "package.json":
            try:
                import json
                payload = json.loads(text)
                deps = set()
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    value = payload.get(key, {})
                    if isinstance(value, dict):
                        deps.update(str(item) for item in value)
                for package, family in _PACKAGE_FAMILIES.items():
                    if package in deps:
                        add(family, rel)
            except (ValueError, TypeError) as exc:
                errors.append(f"{rel}: invalid JSON ({exc})")
        else:
            for package, family in _PACKAGE_FAMILIES.items():
                if package in text:
                    add(family, rel)

    try:
        for path in _iter_sql(root, tier2):
            add("sql", path.relative_to(root).as_posix())
    except OSError as exc:
        errors.append(f"source scan: {exc}")

    return {
        "complete": not errors,
        "detected_families": sorted(detected),
        "supported_families": sorted(set(SUPPORTED_FAMILIES) & detected),
        "unsupported_families": sorted(detected - set(SUPPORTED_FAMILIES)),
        "evidence": {name: sorted(rows) for name, rows in sorted(evidence.items())},
        "errors": errors,
    }


def _excluded(rel: str, tier2: set[str]) -> bool:
    parts = PurePosixPath(rel).parts
    return bool(parts) and (parts[0] in tier2 or parts[0] in _SKIP_DIRS)


def _classify_astgrep(matches, tier2: set[str]):
    """Two-phase: (1) build the Go typed-constant registry (identifier→table) and
    collect all matches; (2) resolve constant-referencing declarations/accesses
    against the registry by EXACT identifier match (structural join, not
    data-flow). Returns (tables, unresolved, registry, referenced_constants)."""
    tables: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    unresolved: list[dict] = []
    registry: dict[str, str] = {}          # Go const identifier -> table literal
    referenced: set[str] = set()           # registry constants actually referenced
    method_bindings: list[tuple[str, str]] = []   # (where, const_id)
    accesses: list[tuple[str, str, str]] = []      # (access, where, text)
    flags = {"truncated": False}           # a dropped >8th site is disclosed upstream

    def add(name: str, access: str, where: str) -> None:
        if _TABLE_NAME.match(name):
            bucket = tables[name][access]
            if where in bucket:
                return
            if len(bucket) < 8:
                bucket.append(where)
            else:
                flags["truncated"] = True

    # phase 1 — declarations + registry
    for match in matches:
        if _excluded(match.file, tier2):
            continue
        where = f"{match.file}:{match.line}"
        rid = match.rule_id
        if rid in ("sequelize-create-table", "sequelize-create-table-ts"):
            name = match.vars.get("N", "").strip("'\"`")
            add(name, "declaration", where)
            add(name, "schema_write", where)
        elif rid == "sequelize-schema-write":
            found = _STR_ARG.search(match.text)
            if found:
                add(found.group(1), "schema_write", where)
        elif rid == "sequelize-data-write":
            found = _STR_ARG.search(match.text)
            if found:
                add(found.group(1), "write", where)
        elif rid in ("sequelize-tablename", "sequelize-tablename-ts"):
            found = _STR_ARG.search(match.text)
            if found:
                add(found.group(1), "declaration", where)
        elif rid == "go-table-const":
            spec = _GO_CONST_SPEC.match(match.text.strip())
            if spec and _TABLE_NAME.match(spec.group(2)):
                registry[spec.group(1)] = spec.group(2)
                add(spec.group(2), "declaration", where)
            else:  # `TableName = OtherConst` — non-literal, recorded unresolved
                unresolved.append({"kind": "go-const", "evidence": where,
                                   "text": match.text[:80]})
        elif rid == "go-tablename-method":
            lit = _GO_METHOD_LIT.search(match.text)
            if lit and _TABLE_NAME.match(lit.group(1)):
                add(lit.group(1), "declaration", where)   # literal return
            else:
                cref = _GO_METHOD_CONST.search(match.text)
                if cref:
                    method_bindings.append((where, cref.group(1)))
                else:
                    unresolved.append({"kind": "go-model-binding",
                                       "evidence": where, "text": match.text[-70:]})
        elif rid in ("gorm-access-write", "gorm-access-read"):
            accesses.append(("write" if rid == "gorm-access-write" else "read",
                             where, match.text))

    # phase 2 — resolve constant references against the registry (exact match)
    for where, const_id in method_bindings:
        table = registry.get(const_id)
        if table:
            add(table, "declaration", where)   # model→const→table binding resolved
            referenced.add(const_id)
        else:
            unresolved.append({"kind": "go-model-binding", "evidence": where,
                               "constant": const_id})
    for access, where, text in accesses:
        lit = _GO_TABLE_LIT.search(text)
        if lit and _TABLE_NAME.match(lit.group(1)):
            add(lit.group(1), access, where)
            continue
        cref = _GO_TABLE_CONST.search(text)
        if cref and registry.get(cref.group(1)):
            add(registry[cref.group(1)], access, where)
            referenced.add(cref.group(1))
        else:  # dynamic table expression / constant outside the registry
            unresolved.append({"kind": "gorm-access", "access": access,
                               "evidence": where,
                               "constant": cref.group(1) if cref else None})
    return tables, unresolved, registry, referenced, flags["truncated"]


def _iter_sql(root: Path, tier2: set[str]):
    stack = [root]
    while stack:
        base = stack.pop()
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                rel0 = entry.relative_to(root).parts[0]
                if entry.name not in _SKIP_DIRS and rel0 not in tier2 \
                        and not entry.name.startswith("."):
                    stack.append(entry)
            elif entry.suffix == ".sql":
                yield entry


def _sql_coverage(root: Path, tables, tier2: set[str], dialect: str = "mysql") -> dict:
    try:
        import sqlglot
        from sqlglot import expressions as exp
    except ImportError:
        return {"available": False, "reason": "sqlglot not installed (pip install "
                "sqlglot / bootstrap [sql] extra) — raw SQL DDL NOT parsed"}
    files = list(_iter_sql(root, tier2))
    parsed, parse_failures, unparsed = 0, [], []
    trunc = {"hit": False}                  # a dropped >8th site is disclosed upstream

    def add(name, access, where):
        if name and _TABLE_NAME.match(name):
            bucket = tables[name][access]
            if where in bucket:
                return
            if len(bucket) < 8:
                bucket.append(where)
            else:
                trunc["hit"] = True

    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            if path.stat().st_size > _SQL_MAX_BYTES:
                unparsed.append({"file": rel, "reason": "exceeds size cap"})
                continue
            text = path.read_text("utf-8", errors="replace")
        except OSError as exc:
            unparsed.append({"file": rel, "reason": str(exc)})
            continue
        try:
            statements = sqlglot.parse(text, read=dialect)
        except Exception as exc:  # sqlglot raises varied ParseError subclasses
            parse_failures.append({"file": rel, "error": str(exc)[:120]})
            continue
        parsed += 1
        for stmt in statements:
            if stmt is None:
                continue
            where = f"{rel}"
            if isinstance(stmt, exp.Create) and (stmt.args.get("kind") or "").upper() == "TABLE":
                target = stmt.find(exp.Table)
                if target is not None:
                    add(target.name, "declaration", where)
                    add(target.name, "schema_write", where)
                for fk in stmt.find_all(exp.ForeignKey):
                    ref = fk.find(exp.Table)
                    if ref is not None:
                        add(ref.name, "join_ref", where)
            elif isinstance(stmt, (exp.Insert, exp.Update, exp.Delete)):
                target = stmt.find(exp.Table)
                if target is not None:
                    add(target.name, "write", where)
            elif isinstance(stmt, exp.Select):
                for tbl in stmt.find_all(exp.Table):
                    add(tbl.name, "read", where)
    return {
        "available": True, "dialect": dialect, "sql_files": len(files),
        "parsed_files": parsed, "parse_failures": parse_failures,
        "unparsed": unparsed,
        "complete": not parse_failures and not unparsed,
        "evidence_truncated": trunc["hit"],
    }


def generate(repo_path: str | Path, repo_id: str, *,
             tier2_exclusions: list[str] | None = None,
             sql_dialect: str = "mysql") -> TableEvidence:
    tier2 = set(tier2_exclusions or [])
    # DDL/migration dirs carry createTable / CREATE TABLE declarations and schema
    # writes (distinct from application data writes), which this lane exists to
    # find — scan them even when tier2 excludes them for other
    # lanes, and disclose the deliberate inclusion.
    ddl_kept = {d for d in tier2 if _DDL_EXEMPT.search(d)}
    scan_tier2 = tier2 - ddl_kept
    root = Path(repo_path).expanduser().resolve()
    detector = _detect_families(root, scan_tier2)
    provenance = astgrep.probe().provenance()
    if not astgrep.available():
        sql_cov = _sql_coverage(root, defaultdict(lambda: defaultdict(list)),
                                scan_tier2, sql_dialect)
        fc_notes = ["ast-grep unavailable: ORM table declarations NOT extracted "
                    "(fail-closed)"]
        if sql_cov.get("evidence_truncated"):
            fc_notes.append("COVERAGE CAP: per-(table, access-type) evidence capped "
                            "at 8 sites — further sites were NOT recorded (sampled).")
        extracted = ["sql"] if sql_cov.get("parsed_files", 0) else []
        detector["extracted_families"] = extracted
        return TableEvidence(available=False, notes=fc_notes,
                             sql_coverage=sql_cov, detector_coverage=detector,
                             astgrep=provenance)
    # Sort matches into a stable order BEFORE classification so the per-bucket
    # 8-site cap keeps a deterministic sample (ast-grep scan order is not stable;
    # an unsorted cap would silently retain a different subset each run).
    matches = sorted(astgrep.scan(repo_path, [astgrep.RULES_DIR / _RULE]),
                     key=lambda m: (m.file, m.line, m.rule_id, m.text))
    tables, unresolved, registry, referenced, astgrep_truncated = \
        _classify_astgrep(matches, scan_tier2)
    sql_coverage = _sql_coverage(root, tables, scan_tier2, sql_dialect)
    extracted_families = set()
    if any(match.rule_id.startswith("sequelize-") for match in matches):
        extracted_families.add("sequelize")
    if any(match.rule_id.startswith("go-") or "gorm" in match.rule_id
           for match in matches):
        extracted_families.add("gorm")
    if sql_coverage.get("parsed_files", 0):
        extracted_families.add("sql")
    detector["extracted_families"] = sorted(extracted_families)
    registry_coverage = {
        "typed_constants": len(registry),
        "referenced": len(referenced),
        "unreferenced": sorted(set(registry) - referenced)[:40],
    }
    notes = [
        "access-type ladder: declaration / schema_write / write / read / join_ref / "
        "same_name / unresolved — schema mutation is not application data writing, "
        "and name matching alone is NEVER confirmed shared persistence.",
        "Go typed-constant registry (constant/table.go) is extracted; "
        "`.Table(constant.X)` and `TableName()` constant returns are linked to it by "
        "EXACT identifier match (structural join, not data-flow). A table accessed "
        "through a non-registry constant or a model object (e.g. db.Create(&Model{})) "
        "stays unresolved.",
        "SQL coverage is best-effort and explicit; parse failures / unparsed files "
        "are disclosed and never counted as complete.",
    ]
    if ddl_kept:
        notes.append("DDL/migration dirs scanned deliberately for this lane "
                     "(first-class table evidence, exempt from tier2): "
                     + ", ".join(sorted(ddl_kept)))
    if astgrep_truncated or sql_coverage.get("evidence_truncated"):
        notes.append(
            "COVERAGE CAP: per-(table, access-type) evidence capped at 8 sites — "
            "further access sites for at least one bucket were NOT recorded "
            "(the distinct table set is complete; per-site evidence is sampled).")
    return TableEvidence(
        available=True,
        tables={name: {a: ev for a, ev in buckets.items()}
                for name, buckets in tables.items()},
        unresolved=unresolved, registry_coverage=registry_coverage,
        sql_coverage=sql_coverage, detector_coverage=detector,
        notes=notes, astgrep=provenance)
