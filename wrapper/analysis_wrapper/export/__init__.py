"""Pluggable export framework.

A shared source layer (:class:`RunInputs`, loaded once) plus a small registry of
format exporters. New formats register an :class:`Exporter`; the CLI and callers
go through :func:`export` / :func:`available_formats` and never import a specific
exporter directly.

Exports are written to ``<skill-root>/exported/{project-name}-analysis/{format}/``
where ``{project-name}`` is the run's project id with its trailing ``-<hash>``
stripped (e.g. ``myapp-1a2b3c4d`` -> ``myapp``). That tree is gitignored —
generated artifacts never enter the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..report_html import run_inputs
from .base import Exporter, ExporterUnavailable, ExportResult
from .html import HtmlExporter

DEFAULT_FORMAT = "html"

_REGISTRY: dict[str, Exporter] = {}


def register(exporter: Exporter) -> None:
    _REGISTRY[exporter.format_name] = exporter


register(HtmlExporter())


def available_formats() -> list[str]:
    return sorted(_REGISTRY)


def get_exporter(fmt: str) -> Exporter:
    try:
        return _REGISTRY[fmt]
    except KeyError:
        raise ValueError(
            f"unknown export format: {fmt!r} "
            f"(available: {', '.join(available_formats())})"
        ) from None


def project_name(project_id: str) -> str:
    """Strip the trailing ``-<hash>`` from a project id (``myapp-1a2b3c4d`` -> ``myapp``)."""
    stripped = re.sub(r"-[0-9a-f]{6,}$", "", project_id or "")
    return stripped or (project_id or "project")


def export_output_dir(skill_root: str | Path, project_id: str, fmt: str) -> Path:
    """The canonical export destination for a project + format."""
    return (
        Path(skill_root) / "exported" / f"{project_name(project_id)}-analysis" / fmt
    )


def export(
    run_dir: str | Path,
    fmt: str = DEFAULT_FORMAT,
    *,
    out_dir: str | Path | None = None,
    skill_root: str | Path | None = None,
) -> ExportResult:
    """Export a completed run in ``fmt``.

    The run is loaded once through the shared source layer. ``out_dir`` wins if
    given; otherwise it is derived from ``skill_root`` and the run's project id.
    Raises :class:`ExporterUnavailable` (fail-closed) if the format's converter
    is missing, and :class:`ValueError` for an unknown format.
    """
    exporter = get_exporter(fmt)
    ok, reason = exporter.check_available()
    if not ok:
        raise ExporterUnavailable(f"format unavailable: {reason}")
    inputs = run_inputs.load(run_dir)
    if out_dir is None:
        if skill_root is None:
            raise ValueError("export needs either out_dir or skill_root")
        out_dir = export_output_dir(skill_root, inputs.project_id, fmt)
    return exporter.export(inputs, Path(out_dir))


__all__ = [
    "Exporter",
    "ExporterUnavailable",
    "ExportResult",
    "DEFAULT_FORMAT",
    "register",
    "available_formats",
    "get_exporter",
    "project_name",
    "export_output_dir",
    "export",
]
