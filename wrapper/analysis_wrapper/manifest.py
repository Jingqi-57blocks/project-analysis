"""Revision-anchored per-signal manifests (plan §9, 57B-10 spec).

A manifest is written for EVERY signal attempt — including skipped ones — so an
absent result can never masquerade as a clean one. Structured fields (argv as a
real list, env as a dict) are stored as JSON; a human-readable .txt rendering
sits alongside. The wrapper writes manifests; nothing in v1 validates them
(plan §2.6 — conventions, not checker subsystems).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .sanitize import sanitize_text
from .status import Status


@dataclass
class RepoStamp:
    """Provenance for one scanned repo; multi-repo tools carry several."""
    repository_ref: str
    repo_path: str
    repo_head: str
    branch: str
    dirty_detail: str
    shallow: bool = False
    commit_count: int = 0
    oldest_commit_date: str = ""

@dataclass
class Manifest:
    tool: str
    tool_version: str
    argv: list[str]                       # structured, not a shell string
    cwd: str
    env: dict[str, str]                   # only the vars we explicitly set
    repos: list[RepoStamp]
    status: str                           # Status.value
    reason: str                           # why partial/failed/skipped ("" if complete)
    exit_code: int | None                 # None when never invoked (skipped)
    wall_time_s: float | None
    scope: str                            # what was scanned, incl. source universe + roots
    exclusions: str                       # both tiers, disclosed
    network: bool
    scan_date: str                        # recorded by caller, not generated here
    output_files: list[str] = field(default_factory=list)
    declared_reads: list[str] = field(default_factory=list)  # target data files read
    version_drift: str = ""               # "" or "validated X, found Y"
    notes: str = ""
    structured_metrics: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"schema_version": "2.0.0", **asdict(self)},
                          indent=2, sort_keys=True) + "\n"

    def normalized_json(self) -> str:
        """Deterministic comparison artifact.

        Wall time and scan date are deliberately volatile. Output locations are
        reduced to basenames so two equivalent runs in different run directories
        compare byte-for-byte. The full manifest remains the provenance record.
        """
        data = asdict(self)
        data["schema_version"] = "2.0.0"
        data.pop("wall_time_s", None)
        data.pop("scan_date", None)
        cwd = Path(self.cwd).expanduser().resolve() if self.cwd else None
        if cwd and any(Path(p).expanduser().resolve().parent == cwd for p in self.output_files):
            data["cwd"] = "<output>"
        data["output_files"] = [Path(p).name for p in self.output_files]
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

    def render_text(self) -> str:
        lines = [
            f"tool:            {self.tool}",
            f"version:         {self.tool_version}"
            + (f"  [DRIFT: {self.version_drift}]" if self.version_drift else ""),
            f"argv:            {json.dumps(self.argv)}",
            f"cwd:             {self.cwd}",
            f"env:             {json.dumps(self.env, sort_keys=True)}",
        ]
        for r in self.repos:
            lines.append(
                f"repo:            {r.repository_ref} @ {r.repo_head or '(non-git)'}"
                f" [{r.branch or '-'}] dirty={r.dirty_detail}  ({r.repo_path})"
                f" history=shallow:{str(r.shallow).lower()},commits:{r.commit_count},"
                f"oldest:{r.oldest_commit_date or '?'}"
            )
        lines += [
            f"status:          {self.status}"
            + (f"  ({self.reason})" if self.reason else ""),
            f"exit_code:       {'-' if self.exit_code is None else self.exit_code}",
            f"wall_time_s:     {'-' if self.wall_time_s is None else f'{self.wall_time_s:.2f}'}",
            f"scope:           {self.scope}",
            f"exclusions:      {self.exclusions}",
            f"network:         {'yes' if self.network else 'no'}",
            f"scan_date:       {self.scan_date}",
            f"outputs:         {', '.join(self.output_files) or '-'}",
            f"declared_reads:  {', '.join(self.declared_reads) or '-'}",
        ]
        if self.notes:
            lines.append(f"notes:           {self.notes}")
        if self.structured_metrics:
            lines.append(
                "structured_metrics: " + json.dumps(
                    self.structured_metrics, sort_keys=True, separators=(",", ":")))
        return "\n".join(lines) + "\n"

    def write(self, directory: str | Path, name: str) -> tuple[Path, Path]:
        """Write full, normalized, and text manifests (all sanitized)."""
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        jpath, tpath = d / f"{name}.manifest.json", d / f"{name}.manifest.txt"
        jpath.write_text(sanitize_text(self.to_json()), "utf-8")
        (d / f"{name}.manifest.normalized.json").write_text(
            sanitize_text(self.normalized_json()), "utf-8"
        )
        tpath.write_text(sanitize_text(self.render_text()), "utf-8")
        return jpath, tpath


def status_of(m: Manifest) -> Status:
    return Status(m.status)
