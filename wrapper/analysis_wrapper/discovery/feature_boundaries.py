"""Mechanically observed feature-boundary anchors.

The extractor deliberately records language-level shapes, not domain intent:
timer/goroutine and event APIs are asynchronous *candidates*; environment
references are configuration *candidates*; test files are test *entry points*.
Later Module Drill stages decide whether any such anchor belongs to the
selected feature.  No value from an environment file is read or persisted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..exclusions import SOURCE_EXT, is_excluded_relative
from ..targetspec import RepoTarget

_MAX_FILES = 4000
_MAX_BYTES = 262_144

_JS_CONFIG = re.compile(r"\b(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]{1,})")
_GO_CONFIG = re.compile(r'\bos\.(?:Getenv|LookupEnv)\(\s*"([A-Z][A-Z0-9_]{1,})"')
_JS_TIMER = re.compile(r"\b(setTimeout|setInterval|queueMicrotask)\s*\(")
_GO_TIMER = re.compile(r"\bgo\s+(?:func\b|[A-Za-z_]\w*\s*\()|\btime\.(AfterFunc|NewTicker|NewTimer)\s*\(")
_JS_EVENT = re.compile(r"\.\s*(emit|publish|subscribe|addListener|on)\s*\(")
_GO_EVENT = re.compile(r"\.\s*(Emit|Publish|Subscribe|On)\s*\(")
_JS_IMPORT = re.compile(r"(?:\bfrom\s*|\bimport\s*\(|\brequire\s*\()\s*['\"]([^'\"]+)['\"]")
_GO_IMPORT = re.compile(r'^\s*(?:import\s+)?(?:[A-Za-z_]\w*\s+)?"([^"\n]+)"', re.M)


@dataclass
class FeatureBoundaries:
    """Full deterministic producer output for one repository."""

    async_boundaries: list[dict] = field(default_factory=list)
    configuration_references: list[dict] = field(default_factory=list)
    test_files: list[dict] = field(default_factory=list)
    test_links: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "available": True,
            "async_boundaries": self.async_boundaries,
            "configuration_references": self.configuration_references,
            "test_files": self.test_files,
            "test_links": self.test_links,
            "notes": self.notes,
        }


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _test_file(relative: str) -> bool:
    name = Path(relative).name.lower()
    return name.endswith("_test.go") or ".test." in name or ".spec." in name


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return ""
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _matches(pattern: re.Pattern[str], text: str, relative: str, category: str) -> list[dict]:
    rows: list[dict] = []
    for match in pattern.finditer(text):
        operation = next((group for group in match.groups() if group), "")
        if not operation and match.group(0).lstrip().startswith("go "):
            operation = "go"
        rows.append({"category": category, "operation": operation,
                     "evidence": f"{relative}:{_line(text, match.start())}"})
    return rows


def generate(target: RepoTarget) -> FeatureBoundaries:
    """Scan the target's source universe once, with explicit cap disclosure."""
    root = Path(target.path).expanduser().resolve()
    result = FeatureBoundaries()
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if is_excluded_relative(target, relative):
            continue
        count += 1
        if count > _MAX_FILES:
            result.notes.append(
                f"COVERAGE CAP: feature-boundary scan stopped after {_MAX_FILES} files")
            break
        if _test_file(relative):
            result.test_files.append({"path": relative, "evidence": f"{relative}:1"})
        if path.suffix not in SOURCE_EXT:
            continue
        text = _read(path)
        if not text:
            continue
        if path.suffix == ".go":
            if _test_file(relative):
                result.test_links.extend(
                    {"path": relative, "specifier": match.group(1),
                     "evidence": f"{relative}:{_line(text, match.start())}"}
                    for match in _GO_IMPORT.finditer(text))
            result.configuration_references.extend(
                {"name": match.group(1), "evidence": f"{relative}:{_line(text, match.start())}"}
                for match in _GO_CONFIG.finditer(text))
            result.async_boundaries.extend(_matches(_GO_TIMER, text, relative, "timer-or-goroutine"))
            result.async_boundaries.extend(_matches(_GO_EVENT, text, relative, "event-operation"))
        else:
            if _test_file(relative):
                result.test_links.extend(
                    {"path": relative, "specifier": match.group(1),
                     "evidence": f"{relative}:{_line(text, match.start())}"}
                    for match in _JS_IMPORT.finditer(text))
            result.configuration_references.extend(
                {"name": match.group(1), "evidence": f"{relative}:{_line(text, match.start())}"}
                for match in _JS_CONFIG.finditer(text))
            result.async_boundaries.extend(_matches(_JS_TIMER, text, relative, "timer"))
            result.async_boundaries.extend(_matches(_JS_EVENT, text, relative, "event-operation"))

    key = lambda row: tuple(str(row.get(field, "")) for field in ("evidence", "category", "operation", "name", "path"))
    result.async_boundaries = sorted({tuple(sorted(row.items())): row for row in result.async_boundaries}.values(), key=key)
    result.configuration_references = sorted(
        {tuple(sorted(row.items())): row for row in result.configuration_references}.values(), key=key)
    result.test_files = sorted({tuple(sorted(row.items())): row for row in result.test_files}.values(), key=key)
    result.test_links = sorted({tuple(sorted(row.items())): row for row in result.test_links}.values(), key=key)
    return result
