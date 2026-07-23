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
from .. import identity
from ..executor import replace_artifact_text
from .. import module_map
from ..sanitize import sanitize_text
from ..targetspec import TargetSpec
from . import (coverage, from_callgraph, from_discovery, from_go_imports,
               from_imports)
from .builder import ModelBuilder
from .schema import SystemModel

GENERATOR = f"analysis-system-model/{__version__}"
FILENAME = "system-model.json"


def _resolve_scan_date(run: Path, cg: dict, override: str) -> str:
    if override:
        return override
    cov = cg.get("coverage") or {}
    return cov.get("scan_date", "")


def _merge_imports(spec: TargetSpec, identities: identity.IdentityMap,
                   js: dict, go: dict) -> dict:
    """Fold the JS (dependency-cruiser) and Go (go list) import summaries into the
    one dict coverage consumes. ``expected_repos`` is every dependency-map-eligible
    repo (the stage's own lane selection); ``mapped_repos`` is those that actually
    produced a map — the gap is what makes the partition ``partial`` with
    disclosure."""
    mapped = sorted(set(js.get("repos", [])) | set(go.get("repos", [])))
    expected = sorted(identities.reference_for(r.repo_id)
                      for r in spec.repos if r.profiles_for_capability("dependency-map"))
    return {
        "present": bool(js.get("present") or go.get("present")),
        "js": js, "go": go,
        "repos": mapped, "mapped_repos": mapped, "expected_repos": expected,
        "unresolved": js.get("unresolved", 0) + go.get("unresolved", 0),
        "stdlib_omitted": go.get("stdlib_omitted", 0),
    }


def assemble(run_dir: str | Path, *, scan_date: str = "") -> SystemModel:
    """Build the in-memory :class:`SystemModel` for ``run_dir``."""
    run = Path(run_dir).expanduser().resolve()
    spec = TargetSpec.load(run / "targets.json")
    identities = identity.load(run)
    heads = {identities.reference_for(r.repo_id): (r.git.head or "")
             for r in spec.repos}
    report = identity.load_discovery_report(run, identities)
    table_evidence_by_repo = identity.load_table_evidence_by_repo(run, identities)

    builder = ModelBuilder()
    disc = from_discovery.load(builder, spec, report, identities,
                              table_evidence_by_repo=table_evidence_by_repo)
    cg = from_callgraph.load(builder, run, identities)
    imports = _merge_imports(
        spec, identities,
        from_imports.load(builder, run, heads, identities),
        from_go_imports.load(builder, run, heads, identities))
    dep_coverage = run / "imports" / "depmap-coverage.json"
    if dep_coverage.is_file():
        try:
            imports["coverage_repos"] = json.loads(
                dep_coverage.read_text("utf-8")).get("repos", [])
        except (OSError, ValueError):
            imports["coverage_repos"] = [{"status": "failed"}]
    modules = module_map.load_into(
        builder, run, identities.project.reference)
    builder.resolve()

    resolved_scan_date = _resolve_scan_date(run, cg, scan_date)
    cov = coverage.build(spec, report, builder, cg, disc, imports, modules,
                         identities=identities,
                         scan_date=resolved_scan_date,
                         table_evidence_by_repo=table_evidence_by_repo)
    return SystemModel(
        scan_date=resolved_scan_date,
        project_ref=identities.project.reference,
        generator=GENERATOR,
        nodes=builder.nodes, edges=builder.edges, coverage=cov)


def dump(model: SystemModel, run_dir: str | Path) -> Path:
    """Serialize ``model`` (sanitized) to ``<run_dir>/system-model.json``."""
    out = Path(run_dir).expanduser().resolve() / FILENAME
    replace_artifact_text(out, sanitize_text(model.to_json()))
    return out


def write_system_model(run_dir: str | Path, *, scan_date: str = "") -> Path:
    """Assemble and write ``<run_dir>/system-model.json`` (sanitized). Returns
    the output path."""
    return dump(assemble(run_dir, scan_date=scan_date), run_dir)
