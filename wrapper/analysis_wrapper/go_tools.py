"""Resolve the developer-provided Go call-graph tool.

The documented preferred location is ``wrapper/go_tools/bin`` (gitignored), but
Project Analysis never runs ``go install``. Developers choose and manage their own
Go runtime and install the documented analyzer version when they need this lane.
We never resolve a binary from a target repo. When absent, the lane fails CLOSED
to a disclosed ``unavailable`` coverage state rather than a silent empty graph.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

WRAPPER_ROOT = Path(__file__).resolve().parents[1]
GO_TOOLS_DIR = WRAPPER_ROOT / "go_tools"
GO_TOOLS_BIN = GO_TOOLS_DIR / "bin"

CALLGRAPH_PKG = "golang.org/x/tools/cmd/callgraph"
CALLGRAPH_VERSION = "v0.48.0"          # pinned; recorded in every coverage manifest

# Install env: -mod=readonly + local toolchain + no workspace, CGO off. Network is
# ALLOWED here (this is the one approved install step) so GOPROXY/GOSUMDB are left
# at their defaults; the analysis env (registry.SAFE_GO_ENV) turns them off.
_MOD_VERSION = re.compile(r"^\s*mod\s+golang\.org/x/tools\s+(\S+)", re.MULTILINE)


def expected_callgraph_binary(bin_dir: Path = GO_TOOLS_BIN) -> Path:
    """Where the binary WOULD live — whether or not it is installed. The lane
    uses this so its own availability check fails closed when absent, never
    silently falling back to an unpinned tool."""
    return bin_dir / "callgraph"


def callgraph_binary(bin_dir: Path = GO_TOOLS_BIN) -> Path | None:
    candidate = expected_callgraph_binary(bin_dir)
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def resolve(bin_dir: Path = GO_TOOLS_BIN) -> tuple[Path | None, str]:
    """Resolve the callgraph binary. Returns ``(path, note)``: the analyzer-owned
    copy with no note; else a PATH copy with a disclosure note; else ``(None,
    reason)`` so the lane can record an ``unavailable`` coverage state."""
    owned = callgraph_binary(bin_dir)
    if owned is not None:
        return owned, ""
    found = shutil.which("callgraph")
    if found:
        return Path(found), ("callgraph resolved from PATH (analyzer-owned GOBIN "
                             "empty) — version not guaranteed to be the pinned "
                             f"{CALLGRAPH_VERSION}")
    return None, (f"callgraph not installed: follow README.md to install "
                  f"{CALLGRAPH_PKG}@{CALLGRAPH_VERSION} with your own Go runtime "
                  f"into {bin_dir}")


def installed_version(binary: Path, *,
                      go: str | None = None,
                      run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """The golang.org/x/tools version baked into ``binary`` (via ``go version
    -m``), or ``""`` when it cannot be determined."""
    go_bin = go or shutil.which("go")
    if not go_bin:
        return ""
    try:
        proc = run([go_bin, "version", "-m", str(binary)],
                   capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    match = _MOD_VERSION.search(proc.stdout or "")
    return match.group(1) if match else ""
