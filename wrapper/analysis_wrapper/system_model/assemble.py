"""Assemble ``system-model.json`` from a completed run directory.

Thin, deterministic orchestrator: read the run-dir artifacts (``targets.json``,
``discovery-report.json``, ``callgraph/``, optional ``imports/``), run each
source normalizer into a shared :class:`ModelBuilder`, resolve references, build
per-producer coverage, and serialize. It never parses source, never infers
business meaning, and never generates wall time — ``scan_date`` is a RECORDED
input read from ``callgraph-coverage.json`` so identical inputs yield
byte-identical output.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import __version__
from ..sanitize import sanitize_text
from ..targetspec import TargetSpec
from . import coverage, from_callgraph, from_discovery, from_imports
from .builder import ModelBuilder
from .schema import SystemModel

GENERATOR = f"analysis-system-model/{__version__}"
FILENAME = "system-model.json"


def _resolve_scan_date(run: Path, cg: dict, override: str) -> str:
    if override:
        return override
    cov = cg.get("coverage") or {}
    return cov.get("scan_date", "")


def assemble(run_dir: str | Path, *, scan_date: str = "") -> SystemModel:
    """Build the in-memory :class:`SystemModel` for ``run_dir``."""
    run = Path(run_dir).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    report = json.loads((run / "discovery-report.json").read_text("utf-8"))
    heads = {r.repo_id: (r.git.head or "") for r in spec.repos}

    builder = ModelBuilder()
    disc = from_discovery.load(builder, spec, report)
    cg = from_callgraph.load(builder, run)
    imports = from_imports.load(builder, run, heads)
    builder.resolve()

    resolved_scan_date = _resolve_scan_date(run, cg, scan_date)
    cov = coverage.build(spec, report, builder, cg, disc, imports,
                         scan_date=resolved_scan_date)
    return SystemModel(
        scan_date=resolved_scan_date,
        project_id=report.get("project_id", ""),
        generator=GENERATOR,
        nodes=builder.nodes, edges=builder.edges, coverage=cov)


def dump(model: SystemModel, run_dir: str | Path) -> Path:
    """Serialize ``model`` (sanitized) to ``<run_dir>/system-model.json``."""
    out = Path(run_dir).expanduser().resolve() / FILENAME
    out.write_text(sanitize_text(model.to_json()), "utf-8")
    return out


def write_system_model(run_dir: str | Path, *, scan_date: str = "") -> Path:
    """Assemble and write ``<run_dir>/system-model.json`` (sanitized). Returns
    the output path."""
    return dump(assemble(run_dir, scan_date=scan_date), run_dir)
