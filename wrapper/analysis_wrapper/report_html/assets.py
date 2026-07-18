"""Bundle the report's local assets (no CDN, no network at view time).

All assets are copied into ``<report>/assets/`` and referenced by relative path,
so the report opens over ``file://`` with zero external requests. The vendored
mermaid runtime is a self-contained UMD bundle; ``report.css`` / ``report.js``
are hand-written presentation glue.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

MERMAID_VERSION = "11.4.1"

# (package-relative source, destination filename under assets/)
_ASSETS: tuple[tuple[str, str], ...] = (
    ("static/report.css", "report.css"),
    ("static/report.js", "report.js"),
    ("vendor/mermaid.min.js", "mermaid.min.js"),
)

STYLESHEET = "assets/report.css"
APP_SCRIPT = "assets/report.js"
MERMAID_SCRIPT = "assets/mermaid.min.js"


def _read_bytes(relpath: str) -> bytes:
    parts = relpath.split("/")
    resource = resources.files("analysis_wrapper.report_html")
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_bytes()


def copy_assets(report_dir: Path) -> list[str]:
    """Write bundled assets into ``<report_dir>/assets/``; return relative refs."""
    assets_dir = report_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for src, dest in _ASSETS:
        (assets_dir / dest).write_bytes(_read_bytes(src))
        written.append(f"assets/{dest}")
    return sorted(written)
