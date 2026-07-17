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

# ast-grep is DELIBERATELY unpinned (brew formula; brew cannot pin a version —
# tools/README §1). Reproducibility for the scan() lanes therefore rests on the
# RUNTIME version check: each scan()-derived signal records the version below was
# validated against and discloses drift. Keep in sync with tools/README.
VALIDATED_VERSION = "0.44.1"


@dataclass
class Match:
    rule_id: str
    file: str                          # repo-relative
    line: int                          # 1-based
    text: str
    vars: dict[str, str] = field(default_factory=dict)  # metavar -> unquoted text


@dataclass(frozen=True)
class Probe:
    """Resolved ast-grep identity for one run: its ``--version`` string and the
    binary path scan() would invoke. ``version`` is None only when ast-grep is
    unavailable (absent, or unable to report a version)."""
    version: str | None                # full --version first line, e.g. "ast-grep 0.44.1"
    path: str | None                   # resolved binary path

    @property
    def available(self) -> bool:
        return self.version is not None

    @property
    def drift(self) -> str:
        """Disclosed drift vs the validated version (never a hard failure —
        ast-grep is unpinned by design). Mirrors the executor path's wording."""
        if self.version and VALIDATED_VERSION not in self.version:
            return f"validated {VALIDATED_VERSION}, found {self.version}"
        return ""

    def provenance(self) -> dict:
        """Signal-entry provenance using the executor path's field names
        (``tool_version`` / ``version_drift``) plus the resolved binary path, so
        downstream consumers read ast-grep's version uniformly with every other
        analyzer (57B-37)."""
        return {
            "tool": "ast-grep",
            "tool_version": self.version or "(not installed)",
            "tool_path": self.path or "",
            "version_drift": self.drift,
        }


def binary() -> str | None:
    return shutil.which("ast-grep") or shutil.which("sg")


def available() -> bool:
    return binary() is not None


_PROBE_CACHE: dict[str | None, Probe] = {}


def probe(*, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Probe:
    """Version-probe the resolved ast-grep ONCE per run (cached on the resolved
    binary path), so ``ast-grep --version`` is not spawned per scan() call. The
    scan() lanes call this to record which ast-grep produced their signals."""
    exe = binary()
    if exe not in _PROBE_CACHE:
        _PROBE_CACHE[exe] = _do_probe(exe, run)
    return _PROBE_CACHE[exe]


def _do_probe(exe: str | None,
              run: Callable[..., subprocess.CompletedProcess]) -> Probe:
    """Any failure to obtain a version (absent, spawn error, non-zero exit)
    fails closed to unavailable — an unversionable binary cannot anchor
    reproducibility, so it is recorded as not installed rather than guessed."""
    if not exe:
        return Probe(version=None, path=None)
    try:
        out = run([exe, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return Probe(version=None, path=None)
    if out.returncode != 0:
        return Probe(version=None, path=None)
    lines = (out.stdout or out.stderr or "").strip().splitlines()
    return Probe(version=lines[0] if lines else "(unknown)", path=exe)


def unavailable_provenance() -> dict:
    """Provenance shape for a signal produced without ast-grep (fallback or
    skipped) — records the version as unavailable while the lane keeps its own
    fallback/skip disclosure."""
    return Probe(version=None, path=None).provenance()


def _reset_probe_cache() -> None:
    """Test hook: clear the per-run version cache."""
    _PROBE_CACHE.clear()


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
