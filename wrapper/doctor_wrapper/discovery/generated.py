"""Generated-file discovery → derived Tier-2 exclusions (57B-11 S4).

Every exclusion is a repo-root directory NAME with recorded evidence — nothing
here is generic (docs/public/migrations are excluded for a repo only when THIS
repo shows the generated/asset pattern). Universal exclusions (node_modules,
dist, lockfiles, *.min.*) are Tier-1 and live in the wrapper registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go"}
_GENERATED_MARKER = re.compile(r"Code generated .* DO NOT EDIT|@generated", re.I)
_MIGRATION_FILE = re.compile(r"^\d{8,}[-_]|\.sql$", re.I)
_SAMPLE_FILES = 40      # files sampled per directory
_MARKER_RATIO = 0.8


@dataclass
class Tier2Report:
    exclusions: list[str] = field(default_factory=list)  # top-level dir names
    evidence: list[str] = field(default_factory=list)    # one line per exclusion


def _files(directory: Path, limit: int = 400) -> list[Path]:
    found: list[Path] = []
    try:
        stack = [directory]
        while stack and len(found) < limit:
            base = stack.pop()
            for entry in sorted(base.iterdir()):
                if entry.is_dir():
                    if not entry.name.startswith("."):
                        stack.append(entry)
                elif entry.is_file():
                    found.append(entry)
                    if len(found) >= limit:
                        break
    except OSError:
        pass
    return found


def _marker_ratio(files: list[Path]) -> float:
    candidates = [f for f in files if f.suffix in _SOURCE_EXT][:_SAMPLE_FILES]
    if not candidates:
        return 0.0
    hits = 0
    for path in candidates:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                head = "".join(handle.readline() for _ in range(3))
        except OSError:
            continue
        if _GENERATED_MARKER.search(head):
            hits += 1
    return hits / len(candidates)


def derive(repo_path: str | Path) -> Tier2Report:
    root = Path(repo_path).expanduser().resolve()
    report = Tier2Report()

    try:
        top_dirs = sorted(
            p for p in root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
            and p.name not in {"node_modules", "vendor", "dist", "build", "coverage"}
        )
    except OSError:
        return report

    for directory in top_dirs:
        name = directory.name
        files = _files(directory)
        if not files:
            continue

        # Generated swagger bundle (gswagger convention: docs.go + swagger.*).
        basenames = {f.name for f in files}
        if "docs.go" in basenames and basenames & {"swagger.json", "swagger.yaml"}:
            report.exclusions.append(name)
            report.evidence.append(
                f"{name}: docs.go + swagger.json/yaml (generated swagger bundle)"
            )
            continue

        # Static asset tree: conventional name AND no non-minified source files.
        if name in {"public", "static", "assets"}:
            real_source = [
                f for f in files
                if f.suffix in _SOURCE_EXT and not f.name.endswith((".min.js", ".min.css"))
            ]
            if not real_source:
                report.exclusions.append(name)
                report.evidence.append(
                    f"{name}: static assets only, no non-minified source files"
                )
                continue

        # Migration tree: conventional name AND timestamped/.sql files dominate.
        if name in {"migrations", "migration", "db_migrations"}:
            matching = [f for f in files if _MIGRATION_FILE.search(f.name)]
            if len(matching) >= 3 and len(matching) >= len(files) * 0.6:
                report.exclusions.append(name)
                report.evidence.append(
                    f"{name}: {len(matching)}/{len(files)} timestamped/SQL migration files"
                )
                continue

        # Machine-generated code markers dominate the directory.
        ratio = _marker_ratio(files)
        if ratio >= _MARKER_RATIO:
            report.exclusions.append(name)
            report.evidence.append(
                f"{name}: {ratio:.0%} of sampled sources carry generated-code markers"
            )

    return report
