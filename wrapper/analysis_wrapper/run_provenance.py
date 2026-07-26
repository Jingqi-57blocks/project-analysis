"""Simple, local run provenance for fresh overview runs.

This is a record and resume guard, not a cache identity.  It deliberately has
no content hashes, replay keys, receipts, or cross-run lookup behavior.
"""

from __future__ import annotations

import json
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__, compat, gitinfo, locale
from .executor import replace_artifact_text
from .exclusions import is_excluded_relative
from .targetspec import RepoTarget, TargetSpec


FILENAME = "run-provenance.json"
SCHEMA_VERSION = 1
UNKNOWN = "unknown"
_ANALYZER_IGNORED_DIRS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules",
    "vendor", "dist", "build", "coverage", "state", "output", "exported",
}


def _source_tree_digest(
    root: Path,
    *,
    excluded: Callable[[str], bool],
) -> str:
    digest = hashlib.sha256()
    for directory, dirnames, filenames in os.walk(root):
        directory_path = Path(directory)
        relative_dir = directory_path.relative_to(root)
        dirnames[:] = sorted(
            name for name in dirnames
            if not excluded((relative_dir / name).as_posix())
        )
        for filename in sorted(filenames):
            path = directory_path / filename
            relative = path.relative_to(root).as_posix()
            if excluded(relative) or path.is_symlink():
                continue
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            try:
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                raise ValueError(f"cannot snapshot source file {path}: {exc}") from exc
            digest.update(b"\0")
    return digest.hexdigest()


def analyzer_source_state(analyzer_root: str | Path) -> str:
    root = Path(analyzer_root).expanduser().resolve()
    return _source_tree_digest(
        root,
        excluded=lambda relative: any(
            part in _ANALYZER_IGNORED_DIRS for part in Path(relative).parts
        ),
    )


def non_git_source_state(target: RepoTarget) -> str:
    """Return a deterministic source-state digest for a NON-GIT target.

    Git targets already have a commit plus dirty-state contract.  A plain
    source folder has no version identifier, so this small local digest is the
    minimum equivalent needed to keep a resumed run from mixing two source
    snapshots.  It is not used for caching or cross-run reuse.
    """
    if target.git.is_git:
        return ""
    root = Path(target.path).expanduser().resolve()
    digest = hashlib.sha256()
    for analysis_root in sorted(target.root_paths(), key=lambda item: str(item)):
        if not analysis_root.is_dir():
            digest.update(f"missing\0{analysis_root.relative_to(root)}\0".encode())
            continue
        for directory, dirnames, filenames in os.walk(analysis_root):
            directory_path = Path(directory)
            relative_dir = directory_path.relative_to(root)
            dirnames[:] = sorted(
                name for name in dirnames
                if not is_excluded_relative(
                    target, (relative_dir / name).as_posix())
            )
            for filename in sorted(filenames):
                path = directory_path / filename
                relative = path.relative_to(root).as_posix()
                if is_excluded_relative(target, relative) or path.is_symlink():
                    continue
                digest.update(relative.encode("utf-8", errors="surrogateescape"))
                digest.update(b"\0")
                try:
                    with path.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                except OSError as exc:
                    raise ValueError(
                        f"cannot snapshot NON-GIT target {target.repo_id}: {relative}: {exc}"
                    ) from exc
                digest.update(b"\0")
    return digest.hexdigest()


def non_git_source_states(spec: TargetSpec) -> dict[str, str]:
    return {
        target.repo_id: non_git_source_state(target)
        for target in sorted(spec.repos, key=lambda item: item.repo_id)
        if not target.git.is_git
    }


def metadata_value(value: str | None, label: str) -> str:
    """Record an honest opaque model/effort label, or ``unknown``."""
    result = (value or "").strip() or UNKNOWN
    if len(result) > 128 or any(ord(char) < 32 for char in result):
        raise ValueError(f"{label} must be at most 128 printable characters")
    return result


def analyzer_observation(analyzer_root: str | Path) -> dict[str, Any]:
    root = Path(analyzer_root).expanduser().resolve()
    head = gitinfo.head(root)
    return {
        "package": "analysis-wrapper",
        "version": __version__,
        "root": str(root),
        "git_head": head,
        "git_branch": gitinfo.branch(root) if head else "",
        "dirty_detail": gitinfo.dirty_detail(root),
        "source_state_sha256": analyzer_source_state(root),
    }


def require_delivered_language(language: str) -> str:
    """Refuse a run language whose label catalog is not key-complete (57B-111).

    A first run in a target language must deliver every reading-facing string
    natively; a partially-translated catalog would silently leak English
    labels into an otherwise-foreign-language report with nothing failing.
    Post-hoc translation is a separate, future feature and must never be how a
    run's primary language is delivered, so an incomplete catalog is refused
    here, at the moment the language is bound to a run, rather than being
    discovered later by a reader.
    """
    if not locale.is_delivered(language):
        delivered = ", ".join(locale.delivered_languages()) or "(none)"
        missing = locale.missing_keys(language)
        sample = ", ".join(missing[:5])
        raise ValueError(
            f"language {language!r} is not a delivered language (its label "
            f"catalog is missing {len(missing)} key(s) against the English "
            f"reference, e.g. {sample}). Delivered languages: {delivered}. "
            f"Post-hoc translation is not a substitute for a complete catalog; "
            f"start the run in a delivered language instead."
        )
    return language


def create_document(
    spec: TargetSpec,
    *,
    analyzer_root: str | Path,
    language: str,
    model: str = "",
    effort: str = "",
    analyzed_at: str | None = None,
    degraded_runtime_notice: str = "",
) -> dict[str, Any]:
    require_delivered_language(language)
    return {
        "schema_version": SCHEMA_VERSION,
        "analyzed_at": analyzed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "analyzer": analyzer_observation(analyzer_root),
        "targets": [
            {
                "repo_id": repo.repo_id,
                "path": repo.path,
                "head": repo.git.head,
                "branch": repo.git.branch,
                "dirty_detail": repo.git.dirty_detail,
                "state": "git" if repo.git.is_git else "NON-GIT",
                "source_state_sha256": non_git_source_state(repo),
            }
            for repo in sorted(spec.repos, key=lambda item: item.repo_id)
        ],
        "generation": {
            "language": language,
            "model": metadata_value(model, "model"),
            "effort": metadata_value(effort, "effort"),
        },
        "preparation": None,
        "tool_versions": [],
        # 57B-95 item 4: stamp the code/artifact-contract/runtime-contract
        # identity a LATER run/tool needs to make a compat decision straight
        # from the artifact, without re-deriving it from the environment.
        # Additive-only (no SCHEMA_VERSION bump): an older reader simply
        # ignores this key, and a run minted before this stamping existed is
        # still loadable — ``compat.run_schema_family`` treats an absent
        # block as the pre-3.0.0 family, same as every other pre-stamp run.
        "compat": compat.compat_stamp(degraded_runtime_notice=degraded_runtime_notice),
    }


def path_for(run_dir: str | Path) -> Path:
    return Path(run_dir) / FILENAME


def load(run_dir: str | Path) -> dict[str, Any]:
    path = path_for(run_dir)
    if not path.is_file():
        raise ValueError(
            "run lacks run-provenance.json and must be regenerated under the current contract")
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("run-provenance.json has an unsupported shape or version")
    if not isinstance(value.get("analyzer"), dict):
        raise ValueError("run-provenance.json analyzer must be an object")
    if not isinstance(value.get("targets"), list):
        raise ValueError("run-provenance.json targets must be a list")
    if not isinstance(value.get("generation"), dict):
        raise ValueError("run-provenance.json generation must be an object")
    if value.get("preparation") is not None and not isinstance(
            value.get("preparation"), dict):
        raise ValueError("run-provenance.json preparation must be null or an object")
    if not isinstance(value.get("tool_versions"), list):
        raise ValueError("run-provenance.json tool_versions must be a list")
    if value.get("compat") is not None and not isinstance(value.get("compat"), dict):
        raise ValueError("run-provenance.json compat must be null/absent or an object")
    return value


def write(run_dir: str | Path, document: dict[str, Any]) -> Path:
    path = path_for(run_dir)
    replace_artifact_text(
        path,
        json.dumps(document, indent=2, sort_keys=True) + "\n",
    )
    return path


def bind_preparation(run_dir: str | Path, options: dict[str, Any]) -> dict[str, Any]:
    """Bind run-affecting deterministic options on first preparation."""
    document = load(run_dir)
    normalized = {
        "scan_date": str(options["scan_date"]),
        "history_since": str(options["history_since"]),
        "coupling_sample_cap": int(options["coupling_sample_cap"]),
        "network_authorized": bool(options["network_authorized"]),
        "allowed_hosts": sorted(set(options.get("allowed_hosts", []))),
    }
    existing = document.get("preparation")
    if existing is not None and existing != normalized:
        differences = sorted(
            key for key in set(existing) | set(normalized)
            if existing.get(key) != normalized.get(key)
        )
        raise ValueError(
            "run preparation options changed ("
            + ", ".join(differences)
            + "); mint a new run instead of reusing canonical outputs"
        )
    if existing is None:
        document["preparation"] = normalized
        write(run_dir, document)
    return document


def _tool_rows(document: Any, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            tool = value.get("tool")
            version = value.get("tool_version")
            if isinstance(tool, str) and tool and isinstance(version, str) and version:
                rows.append({
                    "tool": tool,
                    "version": version,
                    "version_drift": str(value.get("version_drift") or ""),
                    "source": source,
                })
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return rows


def collect_tool_versions(run_dir: str | Path) -> list[dict[str, Any]]:
    run = Path(run_dir)
    rows: list[dict[str, Any]] = []
    for path in sorted((run / "signals").glob("*.manifest.json")):
        if path.name.endswith(".manifest.normalized.json"):
            continue
        try:
            document = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read tool provenance from {path}: {exc}") from exc
        rows.extend(_tool_rows(document, str(path.relative_to(run))))
    for relative in (
        "discovery-report.json",
        "callgraph-coverage.json",
        "imports/depmap-coverage.json",
    ):
        path = run / relative
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read tool provenance from {path}: {exc}") from exc
        rows.extend(_tool_rows(document, relative))

    grouped: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        key = (row["tool"], row["version"], row["version_drift"])
        grouped.setdefault(key, set()).add(row["source"])
    return [
        {
            "tool": tool,
            "version": version,
            "version_drift": drift,
            "sources": sorted(sources),
        }
        for (tool, version, drift), sources in sorted(grouped.items())
    ]


def refresh_tool_versions(run_dir: str | Path) -> dict[str, Any]:
    document = load(run_dir)
    document["tool_versions"] = collect_tool_versions(run_dir)
    write(run_dir, document)
    return document


def analyzer_staleness(recorded: dict[str, Any]) -> list[str]:
    root = recorded.get("root")
    if not isinstance(root, str) or not root:
        return ["analyzer provenance is incomplete"]
    current = analyzer_observation(root)
    compared = ("version", "git_head", "dirty_detail", "source_state_sha256")
    changed = [key for key in compared if recorded.get(key) != current.get(key)]
    if not changed:
        return []
    return [
        "analyzer changed since the run started (" + ", ".join(changed) + ")"
    ]


def target_source_staleness(document: dict[str, Any], spec: TargetSpec) -> list[str]:
    """Compare NON-GIT source folders with their run-start snapshots."""
    recorded = {
        row.get("repo_id"): row.get("source_state_sha256")
        for row in document.get("targets", [])
        if isinstance(row, dict) and row.get("state") == "NON-GIT"
    }
    problems: list[str] = []
    for target in spec.repos:
        if target.git.is_git:
            continue
        before = recorded.get(target.repo_id)
        if not isinstance(before, str) or not before:
            problems.append(f"{target.repo_id}: NON-GIT source snapshot is missing")
        elif non_git_source_state(target) != before:
            problems.append(f"{target.repo_id}: NON-GIT source files changed")
    return problems
