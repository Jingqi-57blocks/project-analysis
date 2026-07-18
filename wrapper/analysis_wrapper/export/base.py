"""Exporter interface + result types for the pluggable export framework.

Each output format is an :class:`Exporter` that turns the shared source layer
(:class:`RunInputs`) into a self-contained artifact folder. An exporter declares
the converter it needs and fails CLOSED — with a clear, actionable message —
when that converter is not installed, the same posture the wrapper takes for
optional analysis tools.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..report_html.run_inputs import RunInputs


class ExporterUnavailable(RuntimeError):
    """Raised when an exporter's required converter is not installed."""


@dataclass
class ExportResult:
    format: str
    out_dir: Path
    detail: object | None = None  # format-specific result (e.g. GenerateResult)


class Exporter(ABC):
    """Turn a completed run's :class:`RunInputs` into one output format."""

    format_name: str = ""
    required_converter: str = ""

    @abstractmethod
    def check_available(self) -> tuple[bool, str]:
        """Return ``(True, "")`` if usable, else ``(False, reason)``."""

    @abstractmethod
    def export(self, inputs: RunInputs, out_dir: Path) -> ExportResult:
        """Render the format into ``out_dir`` (created/overwritten) and report."""
