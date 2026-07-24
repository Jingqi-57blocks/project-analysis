"""Load and normalize a completed run directory into typed report inputs.

This is the *artifact contract* boundary. Everything the report renders comes
from here; missing optional artifacts become honest ``None``/empty values rather
than invented content. Path-bearing fields (provenance checkout paths,
``workspace_root``, ``not_targeted`` lines) are never surfaced raw — only
identity/derived fields are read, so no absolute machine path can reach the UI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import identity

# Canonical narrative documents, in the order they appear in the report nav.
CANONICAL_DOCS: tuple[tuple[str, str, str], ...] = (
    ("overview", "overview.md", "Overview"),
    ("technical-overview", "technical-overview.md", "Technical Overview"),
    ("project-map", "project-map.md", "Project Map"),
)

_MACHINE_COMMENT = re.compile(
    r"(?m)^<!-- (?:BEGIN|END) MACHINE [A-Z][A-Z ]* -->\s*\n?"
)


def _html_markdown(text: str) -> str:
    """Remove known audit-only marker lines from the HTML projection."""
    return _MACHINE_COMMENT.sub("", text)


@dataclass(frozen=True)
class DocSource:
    doc_id: str      # stable id, e.g. "overview"
    filename: str    # source filename, e.g. "overview.md"
    title: str       # human label for nav, e.g. "Overview"
    text: str        # raw markdown (already sanitized upstream)


@dataclass(frozen=True)
class RepoProvenance:
    repository_ref: str
    head: str
    dirty: str       # "no" / "yes" / verbatim dirty_detail; never a path


@dataclass(frozen=True)
class DrilldownModule:
    module_id: str
    has_prd: bool
    has_health: bool
    prd_relpath: str | None      # relative to the run dir; never absolute
    health_relpath: str | None


@dataclass
class RunInputs:
    run_dir: Path
    run_state: dict
    docs: list[DocSource]
    system_model: dict | None
    callgraph_coverage: dict | None
    depmap_coverage: dict | None
    discovery: dict | None
    identity_map: identity.IdentityMap | None = None
    drilldown_modules: list[DrilldownModule] = field(default_factory=list)

    # ---- convenience accessors (all honest about missing data) ----

    @property
    def project_ref(self) -> str:
        return self.identity_map.project.reference

    @property
    def run_id(self) -> str:
        return str(self.run_state.get("run_id") or "unknown")

    @property
    def language(self) -> str:
        return str(self.run_state.get("language") or "en")

    @property
    def analyzed_at(self) -> str:
        return str(self.run_state.get("analyzed_at") or "unknown")

    @property
    def inspection_only(self) -> bool:
        return bool(self.run_state.get("inspection_only", False))

    @property
    def stages(self) -> dict:
        stages = self.run_state.get("stages")
        return stages if isinstance(stages, dict) else {}

    def provenance(self) -> list[RepoProvenance]:
        """Repo identity only (id/head/dirty). Absolute checkout paths dropped."""
        out: list[RepoProvenance] = []
        for row in self.run_state.get("provenance", []) or []:
            if not isinstance(row, dict):
                continue
            out.append(
                RepoProvenance(
                    repository_ref=self.identity_map.reference_for(
                        str(row.get("repo_id"))),
                    head=str(row.get("head") or ""),
                    dirty=str(row.get("dirty_detail") or "unknown"),
                )
            )
        return sorted(out, key=lambda r: r.repository_ref)

    def doc(self, doc_id: str) -> DocSource | None:
        for d in self.docs:
            if d.doc_id == doc_id:
                return d
        return None

    def missing_artifacts(self) -> list[str]:
        """Names of optional artifacts absent from the run (honest coverage)."""
        missing: list[str] = []
        if self.system_model is None:
            missing.append("system-model.json")
        if self.callgraph_coverage is None:
            missing.append("callgraph-coverage.json")
        if self.depmap_coverage is None:
            missing.append("imports/depmap-coverage.json")
        if self.discovery is None:
            missing.append("discovery-report.json")
        present = {d.doc_id for d in self.docs}
        for doc_id, filename, _ in CANONICAL_DOCS:
            if doc_id not in present:
                missing.append(filename)
        return missing

    @property
    def drilldown_available(self) -> bool:
        return any(m.has_prd or m.has_health for m in self.drilldown_modules)


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_current_contract(path: Path) -> dict | None:
    data = _read_json(path)
    if data is not None and data.get("schema_version") != "2.0.0":
        raise ValueError(
            f"{path.name} uses an unsupported artifact contract; regenerate the run")
    return data


def _read_legacy_tolerant_contract(path: Path) -> dict | None:
    """Like ``_read_current_contract``, but for a run already known to predate
    57B-88 (identity had to be derived — see ``load()`` below): an artifact
    using an older/unrecognized contract is treated as honestly absent
    (feeding ``missing_artifacts()``) rather than raising, since regenerating
    a pre-88 run isn't the point of reading it read-only. Never used for a
    run whose identity loaded through the strict path — there, a contract
    mismatch is still a loud regenerate-the-run error, unchanged.
    """
    try:
        return _read_current_contract(path)
    except ValueError:
        return None


def _detect_drilldown(run_dir: Path) -> list[DrilldownModule]:
    """Detect per-module drill-down artifacts under ``<run>/drilldown/``.

    Absent today (Phase 2 work) → empty list → the Modules entrance renders its
    honest stub state. When a module directory holding ``prd.md`` / ``health.md``
    appears, that module lights up automatically. Nothing is fabricated: presence
    is detected, never assumed.
    """
    root = run_dir / "drilldown"
    if not root.is_dir():
        return []
    modules: list[DrilldownModule] = []
    for sub in sorted(root.iterdir(), key=lambda p: p.name):
        if not sub.is_dir():
            continue
        prd = sub / "prd.md"
        health = sub / "health.md"
        if not prd.is_file() and not health.is_file():
            continue
        modules.append(
            DrilldownModule(
                module_id=sub.name,
                has_prd=prd.is_file(),
                has_health=health.is_file(),
                prd_relpath=f"drilldown/{sub.name}/prd.md" if prd.is_file() else None,
                health_relpath=(
                    f"drilldown/{sub.name}/health.md" if health.is_file() else None
                ),
            )
        )
    return modules


def load(run_dir: str | Path) -> RunInputs:
    """Load a completed run directory. Raises if the directory does not exist."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory not found: {run_dir}")

    run_state = _read_json(run_dir / "run-state.json") or {}
    legacy = False
    try:
        identities = identity.load(run_dir)
    except (OSError, ValueError, KeyError) as strict_exc:
        # A pre-57B-88 run is DEFINED by identity-map.json's absence — that is
        # the only condition under which identity.derive_legacy() may be
        # attempted. A run whose identity-map.json IS present but broken
        # (corrupt, truncated, inconsistent with its own TargetSpec/discovery
        # report) must fail loudly like every other present-but-inconsistent
        # artifact in this codebase, not silently fall back to a derived
        # identity. This is the ONLY place that reaches
        # identity.derive_legacy(): it never runs for a run whose strict
        # identity load already succeeded, and no analysis-plane producer
        # calls either this function or that one.
        if (run_dir / identity.FILENAME).is_file():
            raise ValueError(
                f"report input uses an unsupported identity contract: {strict_exc}"
            ) from strict_exc
        try:
            identities = identity.derive_legacy(run_dir)
        except (OSError, ValueError, KeyError) as legacy_exc:
            raise ValueError(
                f"report input uses an unsupported identity contract: {strict_exc}"
            ) from legacy_exc
        legacy = True

    docs: list[DocSource] = []
    for doc_id, filename, title in CANONICAL_DOCS:
        path = run_dir / filename
        if path.is_file():
            docs.append(
                DocSource(
                    doc_id=doc_id,
                    filename=filename,
                    title=title,
                    text=_html_markdown(path.read_text(encoding="utf-8")),
                )
            )

    # A legacy run's structured artifacts (system-model.json etc.) may predate
    # the current 2.0.0 contract too; tolerate that only on the path already
    # known to be pre-88 (an honestly-missing artifact, not a crash). Its
    # discovery-report.json predates the externalized contract
    # load_discovery_report() enforces (different field names throughout, no
    # schema_version) — read it as absent rather than attempting a lossy
    # translation of a shape that isn't this module's contract to interpret.
    contract_reader = _read_legacy_tolerant_contract if legacy else _read_current_contract
    discovery = None
    if not legacy and (run_dir / "discovery-report.json").is_file():
        discovery = identity.load_discovery_report(run_dir, identities)

    return RunInputs(
        run_dir=run_dir,
        run_state=run_state,
        docs=docs,
        system_model=contract_reader(run_dir / "system-model.json"),
        callgraph_coverage=contract_reader(run_dir / "callgraph-coverage.json"),
        depmap_coverage=contract_reader(run_dir / "imports" / "depmap-coverage.json"),
        discovery=discovery,
        identity_map=identities,
        drilldown_modules=_detect_drilldown(run_dir),
    )
