"""Approved Go module-cache warm step (bootstrap-time, network-gated).

The offline Go lane (registry) runs with GOPROXY=off so no network destination is
ever contacted; that requires a warm module cache. This is the ONE approved Go
network operation: ``go list -deps -json ./...`` WITHOUT GOPROXY=off, run once
against a target under user-approved network. It stays read-only
(``-mod=readonly`` — go.mod/go.sum are never modified) and downloads only into
the shared module cache, never into the target.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

# Network ALLOWED here (no GOPROXY=off), but still read-only with no workspace or
# toolchain auto-dispatch so the target stays git-clean and reproducible.
WARM_ENV = {
    "GOFLAGS": "-mod=readonly",
    "GOTOOLCHAIN": "local",
    "GOWORK": "off",
}


def warm(
    repo: str | Path,
    *,
    go_binary: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout_s: int = 600,
) -> tuple[bool, str]:
    """Populate the module cache for one Go module. Returns (ok, detail)."""
    root = Path(repo).expanduser().resolve()
    if not (root / "go.mod").is_file():
        return False, f"no go.mod at {root} (not a Go module)"
    binary = go_binary or shutil.which("go")
    if not binary:
        return False, "go not found on PATH"
    env = {**os.environ, **WARM_ENV}
    try:
        proc = run([binary, "list", "-deps", "-json", "./..."], cwd=str(root),
                   env=env, capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"warm did not run: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "(no output)").strip()[:300]
    return True, "module cache warmed"
