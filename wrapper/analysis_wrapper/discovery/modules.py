"""Module-candidate signals (input to the canonical module-candidates.json).

Mechanical extraction of the four signal families the preliminary module list
is built from: route registrations, source folder structure, persistence table
names, and committed API config files. The model clusters these into candidate
modules at run time — this module only records signals with evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {"node_modules", "vendor", ".git", "dist", "build", "coverage"}
_MAX_FILES = 4000
_MAX_BYTES = 262_144
_MAX_PER_KIND = 200

# Route registrations — Express/Koa style and Go gin/echo/chi/mux style.
_JS_ROUTE = re.compile(
    r"(?:router|app)\s*\.\s*(?:get|post|put|patch|delete|use)\s*\(\s*['\"]([/][^'\"]*)['\"]")
_GO_ROUTE = re.compile(
    r"\.\s*(?:GET|POST|PUT|PATCH|DELETE|Handle|HandleFunc|Group)\s*\(\s*\"([/][^\"]*)\"")
# Persistence tables — SQL DDL, sequelize migrations, gorm TableName.
_SQL_TABLE = re.compile(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+[`\"']?(\w+)", re.I)
_SEQUELIZE_TABLE = re.compile(r"createTable\(\s*['\"](\w+)['\"]")
_GORM_TABLE = re.compile(r"func\s*\([^)]*\)\s*TableName\(\)\s*string\s*{\s*return\s*\"(\w+)\"")
# Committed API config files (declarative, read as data only).
_API_CONFIG_NAMES = re.compile(
    r"^(openapi|swagger|api)[-_.].*\.(json|ya?ml)$|^(openapi|swagger)\.(json|ya?ml)$", re.I)


@dataclass
class ModuleSignals:
    folders: list[str] = field(default_factory=list)      # top-level source dirs
    routes: list[dict] = field(default_factory=list)      # {path, evidence}
    tables: list[dict] = field(default_factory=list)      # {name, evidence}
    api_configs: list[str] = field(default_factory=list)  # repo-relative paths
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "folders": self.folders, "routes": self.routes, "tables": self.tables,
            "api_configs": self.api_configs, "notes": self.notes,
        }


def _capped_append(rows: list, item, notes: list[str], kind: str) -> None:
    if len(rows) < _MAX_PER_KIND:
        if item not in rows:
            rows.append(item)
    elif not any(kind in n for n in notes):
        notes.append(f"{kind} cap hit at {_MAX_PER_KIND}: further signals not recorded (disclosed)")


def extract(repo_path: str | Path, tier2_exclusions: list[str] | None = None) -> ModuleSignals:
    root = Path(repo_path).expanduser().resolve()
    tier2 = set(tier2_exclusions or [])
    signals = ModuleSignals()

    try:
        signals.folders = sorted(
            p.name for p in root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
            and p.name not in _SKIP_DIRS and p.name not in tier2
        )
    except OSError:
        return signals

    count = 0
    stack = [root]
    while stack:
        base = stack.pop()
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
                    # Tier-2 trees are excluded from route/code scanning, but
                    # migrations still carry table DDL — scan only migration
                    # trees among the excluded ones.
                    if entry.name not in tier2 or "migration" in entry.name.lower():
                        stack.append(entry)
                continue
            count += 1
            if count > _MAX_FILES:
                signals.notes.append(
                    f"file cap hit: only first {_MAX_FILES} files scanned (disclosed)")
                return signals

            rel = entry.relative_to(root).as_posix()
            if _API_CONFIG_NAMES.match(entry.name):
                _capped_append(signals.api_configs, rel, signals.notes, "api_config")
                continue
            suffix = entry.suffix
            if suffix not in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".sql"}:
                continue
            try:
                if entry.stat().st_size > _MAX_BYTES:
                    continue
                text = entry.read_text("utf-8", errors="replace")
            except OSError:
                continue

            if suffix != ".sql":
                pattern = _GO_ROUTE if suffix == ".go" else _JS_ROUTE
                for m in pattern.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    _capped_append(signals.routes,
                                   {"path": m.group(1), "evidence": f"{rel}:{line}"},
                                   signals.notes, "route")
            for pattern in (_SQL_TABLE, _SEQUELIZE_TABLE, _GORM_TABLE):
                for m in pattern.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    _capped_append(signals.tables,
                                   {"name": m.group(1), "evidence": f"{rel}:{line}"},
                                   signals.notes, "table")
    return signals
