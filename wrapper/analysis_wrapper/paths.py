"""Canonical, environment-independent resolution of the skill's own directories.

New code should call these helpers instead of recomputing
``Path(__file__).resolve().parents[...]`` at each site. Resolution is by package
location, so it works from any working directory and needs no environment variable;
``CLAUDE_SKILL_DIR`` and similar host hints are optional conveniences, never required.

Resolution assumes the source/editable layout the skill ships and runs in
(``<skill-root>/wrapper/analysis_wrapper``). A non-editable install into a
site-packages tree would not place ``VERSION``/templates beside the package; the skill
is designed to run from its checkout.
"""
from __future__ import annotations

from pathlib import Path

import analysis_wrapper


def wrapper_root() -> Path:
    """The ``wrapper/`` directory that contains the ``analysis_wrapper`` package."""
    return Path(analysis_wrapper.__file__).resolve().parents[1]


def skill_root() -> Path:
    """The skill base directory (the parent of ``wrapper/``)."""
    return wrapper_root().parent
