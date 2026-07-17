"""Self-exclusion: keep the analyzer's OWN checkout out of target discovery.

The analyzer must never analyze itself. Discovery is handed the analyzer's
canonical root (resolved from the package's own install location by default) and
excludes any discovered repository whose CANONICAL path is that same root.

Identity is by resolved filesystem path — never repository name, never a
workspace-specific list. So an unrelated repo that merely shares the basename
(``project-analysis``) is still analyzed, and a checkout reached through a
different spelling (e.g. the skill registered at
``~/.claude/skills/project-analysis`` -> the repo root) is still recognized
because ``resolve()`` collapses the symlink to the real path.

If the analyzer root sits STRICTLY INSIDE a discovered repository that is not
itself the analyzer, that repo's scan would sweep the analyzer's tree and there
is no clean repo-granular way to remove it. That is a boundary conflict: we FAIL
CLOSED with a clear error rather than silently drop the whole legitimate target.
"""

from __future__ import annotations

import os
from pathlib import Path

import analysis_wrapper

# Stable reason recorded in the discovery report's ``not_targeted`` channel.
# Kept as a literal so downstream consumers can match on it deterministically.
SELF_EXCLUSION_REASON = "analyzer-owned checkout"

# Verdicts returned by ``classify``.
SELF = "self"          # this repo IS the analyzer checkout -> exclude it
CONFLICT = "conflict"  # analyzer is strictly inside this repo -> fail closed
ADMIT = ""             # unrelated -> admit normally


class AnalyzerBoundaryConflict(ValueError):
    """The analyzer root is embedded inside a legitimate target repository.

    A subclass of ``ValueError`` so the CLI's existing input-error handling
    surfaces it, while callers that care can catch it specifically.
    """


def default_analyzer_root() -> Path:
    """Canonical repo root of THIS analyzer install.

    ``analysis_wrapper`` lives at ``<repo-root>/wrapper/analysis_wrapper``, so
    the repo root is two parents above the package directory. Resolving the
    package file first collapses the skill-registration symlink, so the identity
    is the real checkout no matter how the package was imported.

    FAIL CLOSED on a layout this arithmetic cannot vouch for (e.g. a
    non-editable install, where ``parents[2]`` lands in a site-packages tree):
    a wrong default here would silently ADMIT the analyzer checkout — the exact
    contamination this module exists to prevent.
    """
    root = Path(analysis_wrapper.__file__).resolve().parents[2]
    if not (root / "wrapper" / "analysis_wrapper").is_dir():
        raise AnalyzerBoundaryConflict(
            f"cannot derive the analyzer root: {root} does not look like the "
            f"analyzer checkout (no wrapper/analysis_wrapper inside). Pass "
            f"--analyzer-root explicitly."
        )
    return root


def resolve_analyzer_root(analyzer_root: str | os.PathLike | None) -> Path:
    """Canonical analyzer root: the caller's override, else the package default.

    Always returns a resolved (symlink-collapsed) path so comparisons are pure
    path identity.
    """
    if analyzer_root is None:
        return default_analyzer_root()
    return Path(analyzer_root).expanduser().resolve()


def classify(repo_path: str | os.PathLike, analyzer_root: Path) -> str:
    """Classify one discovered repo root against the analyzer identity.

    Returns ``SELF``, ``CONFLICT``, or ``ADMIT``. ``analyzer_root`` must already
    be resolved (see :func:`resolve_analyzer_root`); ``repo_path`` is resolved
    here so a workspace that reaches the repo through a different spelling still
    matches by canonical identity.
    """
    repo = Path(repo_path).resolve()
    if repo == analyzer_root:
        return SELF
    if repo in analyzer_root.parents:
        return CONFLICT
    return ADMIT


def conflict_message(analyzer_root: Path, enclosing_repo: str | os.PathLike) -> str:
    """Human-readable boundary-conflict error naming both roots."""
    enclosing = Path(enclosing_repo).resolve()
    return (
        f"analyzer boundary conflict: the analyzer checkout {analyzer_root} is "
        f"embedded inside discovered repository {enclosing}. Excluding the "
        f"analyzer would require dropping that legitimate target, so discovery "
        f"refuses to run. Move the analyzer checkout outside the workspace (or "
        f"outside {enclosing}) and re-run."
    )
