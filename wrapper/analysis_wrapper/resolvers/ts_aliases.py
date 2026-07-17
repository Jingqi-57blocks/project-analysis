"""TS alias resolution → an analyzer-owned dependency-cruiser config (item 2).

Invokes the Node helper (official TypeScript compiler API; reads tsconfig
baseUrl/paths/references + static vite aliases — never executes target config),
then writes a depcruise config UNDER THE RUN OUTPUT DIR (never in the target)
that wires the resolved aliases into enhancedResolveOptions plus the tsConfig.
The generated config is what lets internal `src/*`/relative edges resolve, so the
coupling graph is no longer badly undercounted on Vite/TS repos.

When node, the helper, or the analyzer TypeScript lib is unavailable, resolution
degrades to a disclosed note and depcruise falls back to its safe `--no-config`
scan; the >15% internal-unresolved degrader then still reports partial coverage.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import node_env
from ..targetspec import RepoTarget

_HELPER = node_env.NODE_HELPERS_DIR / "resolve-ts-config.mjs"
_VITE_NAMES = ("vite.config.ts", "vite.config.mts", "vite.config.cts",
               "vite.config.js", "vite.config.mjs", "vite.config.cjs")
# Extensions depcruise's resolver must try (bundler-mode TS + JSON).
_EXTENSIONS = [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts",
               ".d.ts", ".json"]


@dataclass
class AliasResult:
    config_path: Path | None
    notes: str
    reads: list[str] = field(default_factory=list)


def _find_vite(root: Path) -> str:
    for name in _VITE_NAMES:
        if (root / name).is_file():
            return name
    return ""


def resolve_and_generate(
    target: RepoTarget,
    out: Path,
    *,
    tsconfig: str,
    exclude_re: str,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    node: str | None = None,
) -> AliasResult:
    root = Path(target.path)
    info = node_env.probe()
    node_bin = node or shutil.which("node")
    if not node_bin or not _HELPER.is_file() or not node_env.typescript_lib().exists():
        return AliasResult(
            None,
            "alias resolution unavailable (node/helper/typescript-lib missing) — "
            "depcruise ran without an alias config; internal resolution may be partial")

    vite = _find_vite(root)
    argv = [node_bin, str(_HELPER), "--repo", str(root), "--tsconfig", tsconfig]
    if vite:
        argv += ["--vite", vite]
    env = dict(os.environ)
    env["ANALYSIS_TS_LIB"] = str(node_env.typescript_lib())
    try:
        proc = run(argv, capture_output=True, text=True, timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AliasResult(None, f"alias resolver did not run: {exc}; "
                                 "depcruise ran without an alias config")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return AliasResult(None, f"alias resolver error: {detail}; "
                                 "depcruise ran without an alias config")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        return AliasResult(None, f"alias resolver output invalid: {exc}")

    aliases = data.get("aliases", {}) or {}
    unresolved = data.get("unresolved", []) or []
    read_sources = data.get("sources", []) or []
    ts_version = data.get("typescriptVersion") or info.typescript_version or "?"
    base_url = data.get("baseUrl") or str(root)

    # depcruise resolves tsconfig `paths` natively but rejects an enhanced-resolve
    # `alias` map, so BOTH tsconfig paths and static vite aliases are fed through
    # one analyzer-owned tsconfig: it EXTENDS the target (inheriting include/options),
    # pins baseUrl explicitly, and replaces `paths` with the merged, resolvable set.
    merged_paths: dict[str, list[str]] = {}
    for key, abs_target in aliases.items():
        try:
            rel = os.path.relpath(abs_target, base_url)
        except ValueError:
            rel = abs_target
        if key.endswith("$"):                       # exact mapping
            merged_paths.setdefault(key[:-1], [rel])
        else:                                       # prefix mapping
            merged_paths[f"{key}/*"] = [f"{rel}/*"]
            merged_paths.setdefault(key, [rel])

    analysis_tsconfig = {
        "extends": str(root / tsconfig),
        "compilerOptions": {"baseUrl": base_url, "paths": merged_paths},
    }
    tsconfig_path = Path(out) / f"tsconfig-analysis-{target.repo_id}.json"
    tsconfig_path.write_text(
        json.dumps(analysis_tsconfig, indent=2, sort_keys=True) + "\n", "utf-8")

    config = {
        "forbidden": [],
        "options": {
            "doNotFollow": {"path": "node_modules"},
            "exclude": {"path": exclude_re},
            "tsConfig": {"fileName": str(tsconfig_path)},
            "enhancedResolveOptions": {
                "extensions": _EXTENSIONS,
                "conditionNames": ["import", "require", "node", "default", "types"],
            },
        },
    }
    config_path = Path(out) / f"depcruise-config-{target.repo_id}.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", "utf-8")

    notes = (
        f"alias config {config_path.name} (+ {tsconfig_path.name}); inputs "
        f"{', '.join(read_sources) or 'none'}; {len(aliases)} alias(es) resolved, "
        f"{len(unresolved)} unresolved"
        + (" (" + "; ".join(unresolved[:3]) + ")" if unresolved else "")
        + f"; env depcruise {info.depcruise_version or '?'}, typescript {ts_version}"
    )
    return AliasResult(config_path, notes, reads=list(read_sources))
