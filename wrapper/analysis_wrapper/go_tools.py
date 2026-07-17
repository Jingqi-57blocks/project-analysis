"""Analyzer-owned pinned Go call-graph tool (golang.org/x/tools/cmd/callgraph).

Mirrors :mod:`node_env`: the tool is installed with ``go install`` at bootstrap
into an analyzer-owned GOBIN (``wrapper/go_tools/bin``, gitignored) at an EXACT
pinned version — a network step, approved once. We NEVER resolve the binary from
a target repo. The go-callgraph lane resolves the binary from this GOBIN and,
only as a disclosed fallback, from PATH; when it is absent the lane fails CLOSED
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
_INSTALL_ENV = {
    "GOFLAGS": "-mod=readonly",
    "GOTOOLCHAIN": "local",
    "GOWORK": "off",
    "CGO_ENABLED": "0",
}

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
    return None, (f"callgraph not installed: run bootstrap to `go install "
                  f"{CALLGRAPH_PKG}@{CALLGRAPH_VERSION}` into {bin_dir}")


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


def setup(bin_dir: Path = GO_TOOLS_BIN, *,
          go: str | None = None,
          run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Path:
    """``go install`` the pinned callgraph into ``bin_dir``. Network required and
    approved at bootstrap. Raises on failure so the caller discloses it; the lane
    then fails closed. Returns the installed binary path."""
    go_bin = go or shutil.which("go")
    if not go_bin:
        raise RuntimeError("go not found on PATH — install Go to set up go_tools")
    bin_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **_INSTALL_ENV, "GOBIN": str(bin_dir)}
    try:
        proc = run([go_bin, "install", f"{CALLGRAPH_PKG}@{CALLGRAPH_VERSION}"],
                   capture_output=True, text=True, env=env, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"go install did not run: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            "go install callgraph failed: "
            + (proc.stderr or proc.stdout or "(no output)").strip())
    binary = callgraph_binary(bin_dir)
    if binary is None:
        raise RuntimeError(
            f"go install completed but binary is absent: {expected_callgraph_binary(bin_dir)}")
    return binary
