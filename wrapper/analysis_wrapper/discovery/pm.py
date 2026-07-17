"""Package-manager identity (57B-11 S3).

Precedence per spec: `packageManager` field (JSON parse, never executed) →
single-lockfile evidence → npm default. Conflicting evidence (multiple
lockfiles, field/lockfile disagreement) is ALWAYS disclosed in the evidence
string and resolved by the fixed rule — never silently.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..targetspec import PackageManager

_LOCKFILES = {
    "package-lock.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
}
_KNOWN = {"npm", "yarn", "pnpm"}


def identify(repo_path: str | Path) -> PackageManager:
    root = Path(repo_path).expanduser().resolve()

    locks = {name: manager for name, manager in _LOCKFILES.items()
             if (root / name).is_file()}
    lock_for = {manager: name for name, manager in locks.items()}

    field = ""
    manifest = root / "package.json"
    if manifest.is_file():
        try:
            raw = json.loads(manifest.read_text("utf-8")).get("packageManager", "")
        except (OSError, ValueError):
            raw = ""
        if isinstance(raw, str) and raw:
            field = raw.split("@", 1)[0].strip().lower()

    # Node identity (manifest or node lockfiles present).
    if manifest.is_file() or locks:
        conflict = ""
        if len(locks) > 1:
            conflict = "conflicting lockfiles present: " + ", ".join(sorted(locks))
        if field in _KNOWN:
            evidence = f"packageManager field: {field}"
            if conflict:
                evidence += f"; {conflict} (field wins, conflict disclosed)"
            elif locks and field not in lock_for:
                evidence += ("; lockfile disagrees: " + ", ".join(sorted(locks))
                             + " (field wins, conflict disclosed)")
            return PackageManager(field, lock_for.get(field, ""), evidence)
        if len(locks) == 1:
            name, manager = next(iter(locks.items()))
            return PackageManager(manager, name, f"single lockfile: {name}")
        if conflict:
            lockfile = "package-lock.json" if "npm" in lock_for else sorted(locks)[0]
            return PackageManager(
                "npm", lockfile,
                conflict + " and no packageManager field — npm default applied "
                           "(conflict disclosed, never silently preferred)",
            )
        return PackageManager("npm", "", "package.json without lockfile — npm default")

    # Go identity.
    if (root / "go.mod").is_file():
        has_sum = (root / "go.sum").is_file()
        return PackageManager(
            "go", "go.sum" if has_sum else "",
            "go.mod present" + ("" if has_sum else "; go.sum MISSING (disclosed)"),
        )

    return PackageManager("none", "", "no supported package manifest found")
