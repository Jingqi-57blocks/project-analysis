"""Canonical, environment-independent resolution of the skill's own directories,
plus (57B-89, Phase 2) the PERSISTENT DATA root and GENERATED RUNTIME roots that
live outside the replaceable code tree.

New code should call these helpers instead of recomputing
``Path(__file__).resolve().parents[...]`` at each site. Code-root resolution is by
package location, so it works from any working directory and needs no environment
variable; ``CLAUDE_SKILL_DIR`` and similar host hints are optional conveniences,
never required.

Resolution assumes the source/editable layout the skill ships and runs in
(``<skill-root>/wrapper/analysis_wrapper``). A non-editable install into a
site-packages tree would not place ``VERSION``/templates beside the package; the skill
is designed to run from its checkout.

Two families of helper live here, and they are NOT interchangeable:

- ``skill_root()`` / ``wrapper_root()`` — the CODE tree (this checkout). Install,
  upgrade, and reinstall replace this tree wholesale. Only ship-time assets
  (lenses, templates, `wrapper/node_tools/package.json` + lockfile, ...) live here.
- ``data_root()`` and everything built on it (``output_root()``, ``state_root()``,
  ``exported_root()``, ``runtime_root()`` and its children) — PERSISTENT DATA and
  GENERATED RUNTIMES, anchored OUTSIDE the code tree so install/upgrade/reinstall
  never touches them.

A safety rule threads through the second family: ``data_root()`` is the only
function here that ever CREATES a directory (``mkdir``, mode ``0700``). But it is
NOT the only one that touches the filesystem: ``output_root()``, ``state_root()``,
and ``exported_root()`` all call ``data_root()`` internally (to make sure the data
root itself exists) before returning their subpath, so calling any of THOSE also
mkdirs the data root as a side effect. ``runtime_root()`` and everything built on
it (``venv_dir()``, ``node_tools_runtime()``, ``go_tools_bin()``) are the ones that
are genuinely pure path arithmetic — no mkdir, not even of the data root.

Every one of these getters — pure or not — still runs ``_resolve_data_root()``
(precedence resolution plus ``validate_data_root()``'s read-only safety check),
which can raise ``ValueError`` on a misconfigured machine. That means NONE of them
is safe to call from a module-level constant or a function default-parameter
expression: both are evaluated once, at import time, before any caller (including
a test's fixtures) has had a chance to point ``$PROJECT_ANALYSIS_HOME`` somewhere
safe — an import-time raise would make the CLI itself unimportable. Only call
these from inside a real operation (a CLI command handler, ``migrate_legacy``, a
function body executed after import) — never from an import-time expression.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import analysis_wrapper


def wrapper_root() -> Path:
    """The ``wrapper/`` directory that contains the ``analysis_wrapper`` package."""
    return Path(analysis_wrapper.__file__).resolve().parents[1]


def skill_root() -> Path:
    """The skill base directory (the parent of ``wrapper/``) — the CODE tree."""
    return wrapper_root().parent


# --------------------------------------------------------------------------
# Persistent data root
# --------------------------------------------------------------------------

# Bump this if the on-disk shape of the generated-runtime tree ever changes
# incompatibly: a new contract value gets a fresh ``runtime/<contract>/`` path,
# so an old runtime is simply abandoned (never migrated) rather than corrupted
# in place — consistent with "rebuild, never move" for generated runtimes.
RUNTIME_CONTRACT = "1"


def _xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".local" / "share"


def _platform_default_data_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "project-analysis"
    if sys.platform == "win32":
        # Native Windows is out of scope for v1 (README); this default is a
        # reasonable placeholder consistent with other Windows-native tools
        # (bootstrap.py's win32 branch handles the venv's own layout — this is
        # the analogous choice for the data root) rather than a fully
        # supported/tested path.
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
        return base / "project-analysis"
    # Linux, WSL2 (which reports "linux"), and anything else POSIX-like.
    return _xdg_data_home() / "project-analysis"


def validate_data_root(root: str | Path, *, target: str | Path | None = None) -> Path:
    """Raise ``ValueError`` if ``root`` cannot safely serve as the data root.

    Rejects, with a specific reason:
    - ``root`` resolves inside (or equal to) the skill's own code tree
      (``skill_root()``) — install/upgrade/reinstall would delete persistent data.
    - ``root`` resolves inside (or equal to) ``target`` when given — the analysis
      target must stay read-only and never host wrapper data.
    - ``root`` (or its nearest existing ancestor, when ``root`` itself does not yet
      exist) is not writable.
    - any of the above reached through a symlink — resolving is what would catch
      it, so the message says so explicitly when the literal and resolved paths
      differ.

    Returns the resolved path on success.
    """
    literal = Path(root).expanduser()
    if not literal.is_absolute():
        literal = literal.absolute()
    # Always resolve, even when ``literal`` itself does not exist yet:
    # ``Path.resolve()`` is non-strict by default and still walks/resolves any
    # EXISTING ancestor's symlinks (only the missing tail is appended as-is), so
    # a not-yet-created path whose ancestor symlinks into the code tree or the
    # analysis target must not be let through just because the leaf is absent.
    resolved = literal.resolve()
    via_symlink = resolved != literal
    symlink_note = " (reached through a symlink)" if via_symlink else ""

    code = skill_root().resolve()
    if resolved == code or code in resolved.parents:
        raise ValueError(
            f"data root {literal} resolves inside the skill's own code tree "
            f"({code}){symlink_note} — persistent data must never live where "
            "install/upgrade/reinstall can delete it"
        )

    if target is not None:
        target_literal = Path(target).expanduser()
        target_resolved = target_literal.resolve()  # same non-strict-resolve rationale as above
        if resolved == target_resolved or target_resolved in resolved.parents:
            raise ValueError(
                f"data root {literal} resolves inside the analysis target "
                f"({target_resolved}){symlink_note} — the target tree must stay "
                "read-only and never host wrapper data"
            )

    probe = resolved
    while not probe.exists():
        parent = probe.parent
        if parent == probe:  # reached a filesystem root that still doesn't exist
            break
        probe = parent
    if not os.access(probe, os.W_OK):
        raise ValueError(
            f"data root {literal} is not writable (nearest existing ancestor "
            f"{probe} lacks write permission)"
        )
    return resolved


def _resolve_data_root() -> Path:
    """Pure precedence resolution plus the read-only safety check — never
    mutates the filesystem. NOT safe to call at module import time or from a
    function default-parameter expression: ``validate_data_root()`` can raise
    ``ValueError`` on a misconfigured machine (data root inside the code tree,
    unwritable, ...), and an import-time raise would make the raising module
    itself unimportable (see paths.py's module docstring). Call it only from
    inside a function body that runs after import — e.g. every getter below.

    Precedence (first that applies wins):
    1. ``$PROJECT_ANALYSIS_HOME`` — explicit override (expanduser + resolve).
    2. macOS: ``~/Library/Application Support/project-analysis``.
    3. Linux/WSL2 (and any other POSIX host):
       ``${XDG_DATA_HOME:-~/.local/share}/project-analysis``.
    """
    override = os.environ.get("PROJECT_ANALYSIS_HOME", "").strip()
    root = (Path(override).expanduser().resolve() if override
            else _platform_default_data_root())
    validate_data_root(root)
    return root


def data_root(*, target: str | Path | None = None) -> Path:
    """The root of every persistent artifact: ``state/``, ``output/``,
    ``exported/``, and the generated-runtime tree (see ``runtime_root()``).

    See ``_resolve_data_root()`` for the precedence rule. Created (parents
    included, mode ``0700``) the first time it is missing; an already-existing
    directory is left with whatever permissions it already has.

    Pass ``target=<analysis workspace>`` whenever the caller knows one (e.g.
    ``new-run``) to ALSO fail closed — before anything is created — if the
    resolved data root would land inside that workspace; the analyzed tree
    must stay read-only and never host wrapper data. This re-validates
    (cheap, read-only) even though ``_resolve_data_root()`` already ran the
    code-tree-only check: that check has no way to know the caller's
    workspace, so it cannot catch this case on its own.

    Call this ONLY from inside a real operation (a CLI command handler,
    ``migrate_legacy``) — never from a module-level constant or a function's
    default-parameter expression (both evaluate at import time, before a
    caller — including a test fixture — can steer ``$PROJECT_ANALYSIS_HOME``).
    Every other getter below is the safe, non-mutating alternative for that use.
    """
    root = _resolve_data_root()
    if target is not None:
        validate_data_root(root, target=target)
    if not root.exists():
        # exist_ok=True: TOCTOU-safe when two processes race to create the same
        # data root concurrently (the exists() check above is only a fast path,
        # not a guarantee). mode=0o700 still applies on this process's create
        # attempt; if the other racer wins, its own mode call already set it.
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
    return root


def output_root() -> Path:
    """Run output root (``<data-root>/output``). Ensures the data root exists
    first; the project/run subdirectories themselves are created by their own
    writers (unchanged from before the data root moved)."""
    data_root()
    return _resolve_data_root() / "output"


def state_root() -> Path:
    """Per-project pointers/facts root (``<data-root>/state``)."""
    data_root()
    return _resolve_data_root() / "state"


def exported_root() -> Path:
    """Rendered-export root (``<data-root>/exported``)."""
    data_root()
    return _resolve_data_root() / "exported"


def runtime_root() -> Path:
    """Generated-runtime root (``<data-root>/runtime/<RUNTIME_CONTRACT>``).

    Pure path arithmetic (no mkdir) — safe to reference from module-level
    constants. The venv creator / package managers that populate the children
    below create their own directories on demand; nothing here pre-creates them.
    """
    return _resolve_data_root() / "runtime" / RUNTIME_CONTRACT


def venv_dir() -> Path:
    """Default location for the wrapper's isolated Python virtual environment."""
    return runtime_root() / "venv"


def node_tools_runtime() -> Path:
    """Default location the analyzer-owned Node package install resolves from.

    The tracked ``wrapper/node_tools/package.json`` + lockfile in the code tree
    stay the install SOURCE; only the generated ``node_modules/`` lives here."""
    return runtime_root() / "node_tools"


def go_tools_bin() -> Path:
    """Default ``GOBIN``-equivalent directory for the developer-installed
    ``callgraph`` binary."""
    return runtime_root() / "go_tools" / "bin"


def migrate_legacy(legacy_skill_root: str | Path) -> dict:
    """One-time migration: move a legacy ``--skill-root``'s ``output/``,
    ``state/``, and ``exported/`` into the current data root.

    Idempotent (a legacy subdir already moved away is simply absent on the next
    call), safe on an interrupted run (each of the three subdirs is migrated
    independently; a later call picks up whatever is left), and safe when the
    destination is read-only or otherwise fails partway (the failure is
    recorded as a warning for that one subdir; the legacy copy is left in place
    rather than losing data). When BOTH the legacy and data-root copies exist
    and are non-empty, namespaces are never merged — the data-root copy is kept
    and the legacy copy is left untouched, disclosed as a warning.

    Never touches generated runtimes (venv, node_modules, the Go tool binary):
    those are always rebuilt fresh under ``runtime_root()``, never migrated.

    Returns ``{"moved": [...], "skipped_absent": [...],
    "skipped_both_present": [...], "warnings": [...]}`` naming which of
    ``output``/``state``/``exported`` fell into each bucket.
    """
    legacy_root = Path(legacy_skill_root).expanduser().resolve()
    report: dict[str, list[str]] = {
        "moved": [], "skipped_absent": [], "skipped_both_present": [],
        "warnings": [],
    }
    try:
        target_root = data_root()  # ensure the destination exists (0700) up front
    except (OSError, ValueError) as exc:
        # The destination itself is unusable (e.g. read-only) — disclose and
        # stop rather than raising: nothing has been touched, legacy data is
        # exactly as it was, and the caller can retry once the destination is
        # fixed (this call is safely re-runnable).
        report["warnings"].append(f"could not prepare the data root: {exc}")
        return report
    for name in ("output", "state", "exported"):
        legacy = legacy_root / name
        if not legacy.is_dir():
            report["skipped_absent"].append(name)
            continue
        current = target_root / name
        try:
            populated = current.exists() and (
                current.is_file() or any(current.iterdir()))
            if populated:
                report["skipped_both_present"].append(name)
                report["warnings"].append(
                    f"{name}: both the legacy copy ({legacy}) and the data-root "
                    f"copy ({current}) exist and are non-empty — kept the "
                    "data-root copy; the legacy copy was left untouched "
                    "(namespaces are never merged)"
                )
                continue
            if current.exists():
                current.rmdir()  # empty leftover placeholder only
            current.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(current))
            report["moved"].append(name)
        except OSError as exc:
            report["warnings"].append(
                f"{name}: migration failed ({exc}); left in place")
    return report
