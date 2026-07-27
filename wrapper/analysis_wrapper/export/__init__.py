"""Pluggable export framework.

A shared source layer (:class:`RunInputs`, loaded once) plus a small registry of
format exporters. New formats register an :class:`Exporter`; the CLI and callers
go through :func:`export` / :func:`available_formats` and never import a specific
exporter directly.

Exports are written to
``<skill-root>/exported/{project-name}-analysis/{run-id}/{format}/``
where ``{project-name}`` is the run's readable, collision-free project
namespace. That tree is gitignored — generated artifacts never enter the repo.
"""

from __future__ import annotations

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


def project_name(project_ref: str) -> str:
    """Return the exact project reference; identity parsing happens upstream."""
    return project_ref or "project"


def _path_segment(value: str, field: str) -> str:
    """Reject traversal/control characters while preserving Unicode names."""
    if (not value or value in {".", ".."} or "/" in value or "\\" in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"invalid {field} for export path: {value!r}")
    return value


def export_output_dir(
    skill_root: str | Path, project_ref: str, run_id: str, fmt: str
) -> Path:
    """The canonical export destination for one immutable run + format."""
    project = _path_segment(project_name(project_ref), "project reference")
    run = _path_segment(run_id, "run id")
    format_name = _path_segment(fmt, "format")
    return (
        Path(skill_root) / "exported" / f"{project}-analysis" / run / format_name
    )


def module_export_output_dir(
    skill_root: str | Path, project_key: str, module_id: str, run_id: str, fmt: str
) -> Path:
    """Canonical isolated export location for one module run."""
    project = _path_segment(project_key, "project key")
    module = _path_segment(module_id, "module id")
    run = _path_segment(run_id, "run id")
    format_name = _path_segment(fmt, "format")
    return (Path(skill_root) / "exported" / f"{project}-analysis" / "modules"
            / module / run / format_name)


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
    run_path = Path(run_dir).expanduser().resolve()
    from ..module_drill.html import (generate_module_html, is_module_run,
                                     load_module_html_inputs)
    if is_module_run(run_path):
        inputs = load_module_html_inputs(run_path)
        if out_dir is None:
            if skill_root is None:
                raise ValueError("export needs either out_dir or skill_root")
            out_dir = module_export_output_dir(skill_root, str(inputs.run_state["project_key"]),
                                        inputs.scope.module.module_id, inputs.run_id, fmt)
        result = generate_module_html(inputs, Path(out_dir))
        return ExportResult(fmt, Path(out_dir), detail=result)
    inputs = run_inputs.load(run_path)
    if out_dir is None:
        if skill_root is None:
            raise ValueError("export needs either out_dir or skill_root")
        project_key = (run_path.parent.parent.name
                       if run_path.parent.name == "overview"
                       else inputs.identity_map.project.artifact_key)
        out_dir = export_output_dir(skill_root, project_key, inputs.run_id, fmt)
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
    "module_export_output_dir",
    "export",
]
