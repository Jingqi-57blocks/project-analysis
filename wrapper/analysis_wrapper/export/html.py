"""HTML exporter — the offline data-driven report (report_html) behind the
exporter interface. Its converter is markdown-it-py (the ``report`` extra)."""

from __future__ import annotations

from pathlib import Path

from ..report_html.run_inputs import RunInputs
from .base import Exporter, ExportResult


class HtmlExporter(Exporter):
    format_name = "html"
    required_converter = "markdown-it-py"

    def check_available(self) -> tuple[bool, str]:
        try:
            import markdown_it  # noqa: F401
        except ModuleNotFoundError:
            return False, f"{self.required_converter} not installed"
        return True, ""

    def export(self, inputs: RunInputs, out_dir: Path) -> ExportResult:
        from ..report_html.generate import generate_from_inputs

        result = generate_from_inputs(inputs, out_dir)
        return ExportResult(self.format_name, Path(out_dir), detail=result)
