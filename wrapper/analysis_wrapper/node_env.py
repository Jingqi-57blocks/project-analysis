"""Analyzer-owned pinned Node packages (dependency-cruiser + typescript).

The environment lives under ``wrapper/node_tools`` and is installed with pnpm
from a committed lockfile by the developer. We NEVER
install into, or resolve a binary from, a target repository, and NEVER use a
globally-installed dependency-cruiser: only this pinned, lockfile-frozen copy.

The registry's dependency-cruiser definition points at ``depcruise_binary()``;
when the env is absent the executor fails closed (SKIPPED "not installed"), and a
TypeScript target additionally reports the dependency signal ``unavailable`` when
the env cannot resolve ``.tsx`` (see the depcruise lane's TS-support guard).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

WRAPPER_ROOT = Path(__file__).resolve().parents[1]
NODE_TOOLS_DIR = WRAPPER_ROOT / "node_tools"
NODE_HELPERS_DIR = WRAPPER_ROOT / "node_helpers"


def bin_dir(node_tools: Path = NODE_TOOLS_DIR) -> Path:
    return node_tools / "node_modules" / ".bin"


def expected_depcruise_binary(node_tools: Path = NODE_TOOLS_DIR) -> Path:
    """The path the env binary WOULD live at — whether or not it is installed.

    The registry uses this as the ToolDef binary so the executor's own
    availability check (``shutil.which``) fails closed when the env is missing,
    and never silently falls back to a global depcruise."""
    return bin_dir(node_tools) / "depcruise"


def depcruise_binary(node_tools: Path = NODE_TOOLS_DIR) -> Path | None:
    candidate = expected_depcruise_binary(node_tools)
    return candidate if candidate.is_file() else None


def typescript_lib(node_tools: Path = NODE_TOOLS_DIR) -> Path:
    """The analyzer-owned TypeScript package dir — passed to the Node helper so it
    requires OUR compiler API, never one resolved from a target."""
    return node_tools / "node_modules" / "typescript"


@dataclass
class NodeToolInfo:
    available: bool                 # env present + `depcruise --info` succeeded
    reason: str                     # "" when available
    depcruise_version: str = ""
    typescript_version: str = ""
    supports_ts: bool = False
    supports_tsx: bool = False


_INFO_TS = re.compile(r"^([✔x])\s+typescript\b")
_INFO_TSX = re.compile(r"^([✔x])\s+\.tsx\b")
_DEP_VER = re.compile(r"dependency-cruiser@([0-9][\w.+-]*)")
_TS_VER = re.compile(r"typescript@([0-9][\w.+-]*)")


def _parse_info(text: str) -> NodeToolInfo:
    dep = _DEP_VER.search(text)
    ts_version = ""
    supports_ts = supports_tsx = False
    for raw in text.splitlines():
        line = raw.strip()
        tsm = _INFO_TS.match(line)
        if tsm:
            ver = _TS_VER.search(line)
            supports_ts = tsm.group(1) == "✔" and bool(ver)
            if ver:
                ts_version = ver.group(1)
        tsxm = _INFO_TSX.match(line)
        if tsxm:
            supports_tsx = tsxm.group(1) == "✔"
    return NodeToolInfo(
        available=True, reason="",
        depcruise_version=dep.group(1) if dep else "",
        typescript_version=ts_version,
        supports_ts=supports_ts, supports_tsx=supports_tsx,
    )


_probe_cache: dict[str, NodeToolInfo] = {}


def probe(node_tools: Path = NODE_TOOLS_DIR,
          run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
          use_cache: bool = True) -> NodeToolInfo:
    """Report the env's transpiler capability via ``depcruise --info``.

    Cached per node_tools path because construction and the TS guard may both
    ask. Pass ``use_cache=False`` (and a custom ``run``) in tests."""
    key = str(node_tools)
    if use_cache and key in _probe_cache:
        return _probe_cache[key]
    binary = depcruise_binary(node_tools)
    if binary is None:
        info = NodeToolInfo(
            available=False,
            reason="analyzer node_tools env not installed — follow the manual "
                   "JS/TS prerequisite step in README.md")
    else:
        try:
            proc = run([str(binary), "--info"], capture_output=True, text=True,
                       timeout=60, cwd=str(node_tools))
        except (OSError, subprocess.TimeoutExpired) as exc:
            info = NodeToolInfo(available=False,
                                reason=f"depcruise --info failed: {exc}")
        else:
            if proc.returncode != 0:
                info = NodeToolInfo(
                    available=False,
                    reason=f"depcruise --info exit {proc.returncode}")
            else:
                info = _parse_info(proc.stdout or "")
    if use_cache:
        _probe_cache[key] = info
    return info
