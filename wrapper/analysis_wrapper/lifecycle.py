"""Run lifecycle (57B-14): run IDs, stage checkpoints, pointers, staleness.

A run directory is a resumable pipeline of stage checkpoints:

    discovery -> signals -> findings -> map -> overview

`run-state.json` records which stages are done plus the provenance the run
was minted against; re-invocations resume from the first incomplete stage —
but ONLY while the workspace still matches that provenance. Any mismatch
means a NEW run (immutability), and the refusal names exactly which repos
moved and which are dirty.

Pointers per project (`state/<project-id>/pointers.json`):
- ``latest_completed`` — set automatically when the overview stage finishes;
  inspection-only, never an implicit drill-down source.
- ``current`` — set ONLY by explicit user acceptance; refused for
  inspection-only (dirty/non-git) runs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import gitinfo
from .targetspec import TargetSpec

STAGES = ["discovery", "signals", "findings", "map", "overview"]
DRILLDOWN_STAGES = ["resolve", "prd", "health"]

# A run label becomes part of a directory name. Keep it portable across macOS,
# Linux and common archive/file-server boundaries, and leave room for the input
# digest plus a never-reuse suffix.
_RUN_LABEL_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,46}[A-Za-z0-9])?$"
)


def _input_digest(heads: list[str], language: str) -> str:
    return hashlib.sha256(
        ("\n".join(sorted(heads)) + f"\n{language}").encode("utf-8")
    ).hexdigest()[:6]


def validate_run_label(label: str) -> str:
    """Validate a human-readable run label before it reaches the filesystem."""
    if not _RUN_LABEL_RE.fullmatch(label):
        raise ValueError(
            "invalid --run-id label: use 1-48 letters, digits, '.', '_' or '-'; "
            "start and end with a letter or digit"
        )
    return label


def mint_run_id(heads: list[str], language: str, *, label: str = "",
                when: datetime | None = None,
                exists: "callable" = lambda run_id: False) -> str:
    """Readable label (or timestamp) + digest, with never-reuse uniqueness."""
    when = when or datetime.now(timezone.utc)
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    digest = _input_digest(heads, language)
    prefix = validate_run_label(label) if label else stamp
    base = f"{prefix}-{digest}"
    candidate, n = base, 1
    while exists(candidate):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _spec_heads(spec: TargetSpec) -> list[str]:
    return [f"{r.repo_id}:{r.git.head or 'NON-GIT'}:{r.git.dirty_detail}"
            for r in spec.repos]


@dataclass
class RunState:
    run_id: str
    project_id: str
    language: str = "en"
    analyzed_at: str = ""
    inspection_only: bool = False
    stages: dict = field(default_factory=lambda: {s: "pending" for s in STAGES})
    stage_order: list = field(default_factory=list)  # persisted; sort_keys loses dict order
    provenance: list = field(default_factory=list)  # {repo_id, path, head, dirty_detail}
    analysis_identity: dict = field(default_factory=dict)

    FILENAME = "run-state.json"

    @classmethod
    def create(cls, run_id: str, project_id: str, spec: TargetSpec, *,
               language: str = "en", analysis_identity: dict | None = None,
               when: datetime | None = None,
               stage_names: list[str] | None = None) -> "RunState":
        when = when or datetime.now(timezone.utc)
        dirty = any(r.git.dirty_detail != "no" or not r.git.is_git for r in spec.repos)
        return cls(
            run_id=run_id, project_id=project_id, language=language,
            analyzed_at=when.isoformat(timespec="seconds"),
            inspection_only=dirty,
            stages={s: "pending" for s in (stage_names or STAGES)},
            stage_order=list(stage_names or STAGES),
            provenance=[{"repo_id": r.repo_id, "path": r.path,
                         "head": r.git.head, "dirty_detail": r.git.dirty_detail}
                        for r in spec.repos],
            analysis_identity=analysis_identity or {},
        )

    @classmethod
    def create_drilldown(cls, run_id: str, source: "RunState", module: str, *,
                         language: str | None = None,
                         when: datetime | None = None) -> "RunState":
        """A drill-down run inherits the SOURCE overview run's provenance and
        inspection-only status; its stages are resolve -> prd -> health."""
        when = when or datetime.now(timezone.utc)
        return cls(
            run_id=run_id, project_id=source.project_id,
            language=language or source.language,
            analyzed_at=when.isoformat(timespec="seconds"),
            inspection_only=source.inspection_only,
            stages={s: "pending" for s in DRILLDOWN_STAGES},
            stage_order=list(DRILLDOWN_STAGES),
            provenance=list(source.provenance),
            analysis_identity={"module": module,
                               "source_overview_run": source.run_id},
        )

    @classmethod
    def load(cls, run_dir: str | Path) -> "RunState":
        data = json.loads((Path(run_dir) / cls.FILENAME).read_text("utf-8"))
        state = cls(run_id=data["run_id"], project_id=data["project_id"])
        for key, value in data.items():
            setattr(state, key, value)
        return state

    def save(self, run_dir: str | Path) -> None:
        payload = {
            "run_id": self.run_id, "project_id": self.project_id,
            "language": self.language, "analyzed_at": self.analyzed_at,
            "inspection_only": self.inspection_only, "stages": self.stages,
            "stage_order": self.ordered_stages(),
            "provenance": self.provenance,
            "analysis_identity": self.analysis_identity,
        }
        (Path(run_dir) / self.FILENAME).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8")

    def mark(self, stage: str) -> None:
        if stage not in self.stages:
            raise ValueError(f"unknown stage {stage!r} (stages: {STAGES})")
        self.stages[stage] = "done"

    def ordered_stages(self) -> list[str]:
        """The run's own stage sequence (overview and drill-down runs differ).
        Older run-state files lack the persisted order — recover it from the
        known stage sets."""
        if self.stage_order:
            return list(self.stage_order)
        for known in (STAGES, DRILLDOWN_STAGES):
            if set(self.stages) == set(known):
                return list(known)
        return list(self.stages)

    def next_stage(self) -> str:
        for stage in self.ordered_stages():
            if self.stages.get(stage) != "done":
                return stage
        return ""  # run complete

    def rollback(self, stage: str) -> list[str]:
        """Re-open a stage AND every later stage (their artifacts may embed the
        rolled-back stage's outputs — the stale-count lesson: regenerating
        stage 1 without cascading left a superseded figure in a stage-2
        artifact). Returns the stages re-opened."""
        order = self.ordered_stages()
        if stage not in order:
            raise ValueError(f"unknown stage {stage!r} (stages: {order})")
        reopened = order[order.index(stage):]
        for name in reopened:
            self.stages[name] = "pending"
        return reopened

    def staleness(self) -> list[str]:
        """Names exactly which repos moved and which are dirty (empty = fresh)."""
        problems: list[str] = []
        for row in self.provenance:
            path = row["path"]
            head_now = gitinfo.head(path)
            if head_now != row["head"]:
                problems.append(
                    f"{row['repo_id']}: {row['head'][:8] or 'NON-GIT'} -> "
                    f"{head_now[:8] or 'NON-GIT'}")
            dirty_now = gitinfo.dirty_detail(path) if head_now else row["dirty_detail"]
            if dirty_now != row["dirty_detail"]:
                problems.append(f"{row['repo_id']}: dirty state changed "
                                f"({row['dirty_detail']!r} -> {dirty_now!r})")
        return problems


class Pointers:
    """latest_completed / current for one project."""

    def __init__(self, state_dir: str | Path):
        self.path = Path(state_dir) / "pointers.json"

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {"latest_completed": None, "current": None}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")

    def set_latest_completed(self, run_id: str) -> None:
        data = self.read()
        data["latest_completed"] = run_id
        self._write(data)

    def accept(self, state: RunState) -> None:
        """Explicit user acceptance — the ONLY writer of `current`."""
        if state.inspection_only:
            raise ValueError(
                "inspection-only run (dirty/non-git targets) can never be "
                "accepted — commit/stash and run a new overview")
        if state.next_stage() != "":
            raise ValueError(
                f"run is incomplete (next stage: {state.next_stage()}) — "
                f"only completed overviews can be accepted")
        data = self.read()
        data["current"] = state.run_id
        self._write(data)
