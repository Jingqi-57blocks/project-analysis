"""Resolve the developer-provided Go call-graph tool.

The documented preferred location (57B-89 Phase 2) is under the data root's
generated-runtime tree (``paths.go_tools_bin()``) — GENERATED RUNTIME, never
inside the checkout, so a skill upgrade/reinstall never disturbs an already
-installed binary. ``default_bin_dir()`` falls back to the legacy in-code
``wrapper/go_tools/bin`` when ONLY that location already has the binary (a
pre-relocation install); a later installer phase populates the runtime
location going forward. Project Analysis never runs ``go install`` itself.
Developers choose and manage their own Go runtime and install the documented
analyzer version there when they need this lane. We never resolve a binary
from a target repo. When absent, the lane fails CLOSED to a disclosed
``unavailable`` coverage state rather than a silent empty graph.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import paths

WRAPPER_ROOT = Path(__file__).resolve().parents[1]
# The legacy, pre-relocation in-code install location. Still honored as a
# fallback when it alone is populated -- see ``default_bin_dir()``.
LEGACY_GO_TOOLS_BIN = WRAPPER_ROOT / "go_tools" / "bin"


def default_bin_dir() -> Path:
    """Resolve the GOBIN-equivalent dir: prefer the (57B-89 Phase 2) runtime
    location; fall back to the legacy in-code ``wrapper/go_tools/bin`` ONLY
    when the binary already exists there and not at the runtime location.

    This fallback exists for pre-relocation installs: a machine that already
    ran ``go install ... -o wrapper/go_tools/bin`` before the runtime-root move
    must not lose the Go call-graph lane. A later installer phase populates the
    runtime location going forward; once that lands, fresh installs prefer it
    and this branch simply stops firing.

    Deliberately a function, not a module-level constant: it calls
    ``paths.go_tools_bin()``, which resolves (and validates) the data root and
    can raise ValueError on a misconfigured machine; evaluating that at import
    time would make importing this module fail. Call this only at call time,
    never from a module-level expression.
    """
    preferred = paths.go_tools_bin()
    legacy_binary = LEGACY_GO_TOOLS_BIN / "callgraph"
    if not (preferred / "callgraph").is_file() and legacy_binary.is_file():
        return LEGACY_GO_TOOLS_BIN
    return preferred


CALLGRAPH_PKG = "golang.org/x/tools/cmd/callgraph"
CALLGRAPH_VERSION = "v0.48.0"          # pinned; recorded in every coverage manifest

# Install env: -mod=readonly + local toolchain + no workspace, CGO off. Network is
# ALLOWED here (this is the one approved install step) so GOPROXY/GOSUMDB are left
# at their defaults; the analysis env (registry.SAFE_GO_ENV) turns them off.
_MOD_VERSION = re.compile(r"^\s*mod\s+golang\.org/x/tools\s+(\S+)", re.MULTILINE)


def expected_callgraph_binary(bin_dir: Path | None = None) -> Path:
    """Where the binary WOULD live — whether or not it is installed. The lane
    uses this so its own availability check fails closed when absent, never
    silently falling back to an unpinned tool."""
    bin_dir = bin_dir if bin_dir is not None else default_bin_dir()
    return bin_dir / "callgraph"


def callgraph_binary(bin_dir: Path | None = None) -> Path | None:
    candidate = expected_callgraph_binary(bin_dir)
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def resolve(bin_dir: Path | None = None) -> tuple[Path | None, str]:
    """Resolve the callgraph binary. Returns ``(path, note)``: the analyzer-owned
    copy with no note; else a PATH copy with a disclosure note; else ``(None,
    reason)`` so the lane can record an ``unavailable`` coverage state."""
    bin_dir = bin_dir if bin_dir is not None else default_bin_dir()
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
