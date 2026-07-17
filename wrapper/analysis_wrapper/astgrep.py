"""Thin runner over ast-grep — the OSS structural matcher.

The analyzer owns declarative YAML rules under ``wrapper/rules/`` (one file per
concern, multi-document for multi-language coverage). This module NEVER parses
source itself; it invokes ``ast-grep scan`` and shapes the JSON matches. It is
used for HIGH-RISK structural extraction (route registration, HTTP call sites,
client construction, ORM/table usage) that regex handled brittly.

D1 boundary: import edges come from dependency-cruiser / go list, NEVER from
ast-grep — no rule here re-parses imports.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

RULES_DIR = Path(__file__).resolve().parents[1] / "rules"


@dataclass
class Match:
    rule_id: str
    file: str                          # repo-relative
    line: int                          # 1-based
    text: str
    vars: dict[str, str] = field(default_factory=dict)  # metavar -> unquoted text


def binary() -> str | None:
    return shutil.which("ast-grep") or shutil.which("sg")


def available() -> bool:
    return binary() is not None


def rule_path(name: str) -> Path:
    return RULES_DIR / name


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "'\"`" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _shape(data: list, root: Path, rule_stem: str) -> list[Match]:
    out: list[Match] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        single = (item.get("metaVariables") or {}).get("single") or {}
        variables = {k: _unquote(v.get("text", "")) for k, v in single.items()
                     if isinstance(v, dict)}
        raw_file = item.get("file", "")
        try:
            rel = str(Path(raw_file).resolve().relative_to(root))
        except (OSError, ValueError):
            rel = raw_file
        line = (item.get("range") or {}).get("start", {}).get("line", 0) + 1
        out.append(Match(
            rule_id=item.get("ruleId") or rule_stem,
            file=rel, line=line, text=item.get("text", ""), vars=variables))
    return out


def scan(repo: str | Path, rule_files: Iterable[str | Path], *,
         run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
         timeout_s: int = 180) -> list[Match]:
    """Run each rule file against the repo; return shaped matches. Missing
    ast-grep or a failed rule yields no matches for that rule (callers disclose
    unavailability via ``available()``)."""
    exe = binary()
    root = Path(repo).expanduser().resolve()
    if not exe:
        return []
    matches: list[Match] = []
    for rule in rule_files:
        rule_path_ = Path(rule)
        if not rule_path_.is_file():
            continue
        try:
            proc = run([exe, "scan", "--rule", str(rule_path_), "--json", str(root)],
                       capture_output=True, text=True, timeout=timeout_s)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if not (proc.stdout or "").strip():
            continue
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            continue
        if isinstance(data, list):
            matches.extend(_shape(data, root, rule_path_.stem))
    return matches
