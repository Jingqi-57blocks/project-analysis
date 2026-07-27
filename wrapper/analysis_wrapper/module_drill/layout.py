"""Filesystem layout and immutable initialization for Module Drill runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..executor import prepare_output_directory, write_new_text
from ..lifecycle import mint_run_id
from ..targetspec import TargetSpec
from .contracts import ModuleScope, ProjectSnapshot

MODULE_RUN_VERSION = "module-run/v1"
_LANGUAGES = frozenset({"en", "zh-CN"})


def _segment(value: str, label: str) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or "/" in value or "\\" in value
            or value.endswith((" ", "."))
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"invalid {label} path segment: {value!r}")
    return value


@dataclass(frozen=True)
class ModuleRunLayout:
    """Canonical paths for exactly one immutable module drill-down run."""

    skill_root: Path
    project_key: str
    module_id: str
    run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "skill_root", Path(self.skill_root).expanduser().resolve())
        _segment(self.project_key, "project key")
        _segment(self.module_id, "module ID")
        _segment(self.run_id, "run ID")

    @property
    def run_dir(self) -> Path:
        return self.skill_root / "output" / self.project_key / "modules" / self.module_id / self.run_id

    @property
    def run_state_path(self) -> Path:
        return self.run_dir / "run-state.json"

    @property
    def provenance_path(self) -> Path:
        return self.run_dir / "provenance.json"

    @property
    def scope_path(self) -> Path:
        return self.run_dir / "module-scope.json"

    @property
    def evidence_path(self) -> Path:
        return self.run_dir / "module-evidence.json"

    @property
    def prd_path(self) -> Path:
        return self.run_dir / "prd.md"

    @property
    def health_path(self) -> Path:
        return self.run_dir / "health.md"

    @property
    def html_export_dir(self) -> Path:
        return (self.skill_root / "exported" / f"{self.project_key}-analysis"
                / "modules" / self.module_id / self.run_id / "html")


def mint_module_run_id(skill_root: str | Path, project_key: str, module_id: str,
                        snapshot: ProjectSnapshot, *, language: str,
                        label: str = "", when: datetime | None = None) -> str:
    """Mint a readable, collision-safe ID for one module under one snapshot.

    This reuses the overview's well-tested portable-label and never-reuse
    policy. The module owns a separate directory, so the snapshot digest does
    not need to encode the module ID as well.
    """
    if not isinstance(snapshot, ProjectSnapshot):
        raise ValueError("mint_module_run_id requires a ProjectSnapshot")
    if language not in _LANGUAGES:
        raise ValueError(f"language must be one of {sorted(_LANGUAGES)}")
    root = ModuleRunLayout(skill_root, project_key, module_id, "placeholder")
    parent = root.run_dir.parent
    heads = [
        f"{item.repository_ref}:{item.revision}:{item.dirty_detail}"
        for item in snapshot.repositories
    ]
    return mint_run_id(heads, language, label=label, when=when,
                       exists=lambda run_id: (parent / run_id).exists())


def create_module_run(layout: ModuleRunLayout, spec: TargetSpec, scope: ModuleScope,
                      *, language: str) -> ModuleRunLayout:
    """Create the immutable Module Drill directory and its two initial records.

    Later tasks own evidence generation and report lifecycle.  This function
    creates only the run envelope plus the canonical `module-scope.json`; it
    never emits placeholder evidence or Markdown files.
    """
    if not isinstance(spec, TargetSpec):
        raise ValueError("create_module_run requires a TargetSpec")
    if not isinstance(scope, ModuleScope):
        raise ValueError("create_module_run requires a ModuleScope")
    if language not in _LANGUAGES:
        raise ValueError(f"language must be one of {sorted(_LANGUAGES)}")
    if layout.module_id != scope.module.module_id:
        raise ValueError("run layout module ID does not match the ModuleScope")
    known_repos = {repo.repo_id for repo in spec.repos}
    if len(known_repos) != len(spec.repos):
        raise ValueError("TargetSpec has duplicate repository IDs")

    prepare_output_directory(layout.run_dir, spec.repos)
    state = {
        "contract_version": MODULE_RUN_VERSION,
        "run_id": layout.run_id,
        "project_key": layout.project_key,
        "module_id": layout.module_id,
        "language": language,
        "source_mode": scope.source_mode,
        "stages": {"scope": "done", "evidence": "pending", "prd": "pending", "health": "pending"},
    }
    provenance = {
        "contract_version": MODULE_RUN_VERSION,
        "project": scope.project.to_dict(),
        "snapshot_id": scope.snapshot_id,
        "inspection_only": scope.project.inspection_only,
        "source_mode": scope.source_mode,
        "overview_lineage": (scope.overview_lineage.to_dict()
                             if scope.overview_lineage else None),
    }
    write_new_text(layout.run_state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")
    write_new_text(layout.provenance_path, json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    from .contracts import write_scope
    write_scope(layout.scope_path, scope)
    return layout
