"""Task-oriented command tour (the ``help`` subcommand, 57B-120).

``--help`` (argparse's own output) lists every subcommand alphabetically and
undifferentiated — it mixes the two commands a newcomer needs with roughly a
dozen low-level stage/diagnostic commands that are driven by the skill
itself, never by hand. The project's guiding principle is "user-friendly
above all", so ``help`` is the curated, task-grouped entry point: one screen,
grouped by what the user is trying to DO, not the alphabet.

Read-only, like ``doctor``/``list``: this module must never create the data
root or any directory, touch the network, or write anything. It uses
``paths.resolved_data_root()`` (the non-mutating resolver), never
``paths.data_root()``/``output_root()``/etc. (every one of those mkdirs the
data root as a side effect).

Drift-proofing (mirrors ``compat.COMMAND_CLASSIFICATION`` and its
``test_every_subcommand_is_classified``): every command registered in
``cli.parser()`` must have an explicit entry in ``COMMAND_GROUPS`` below, plus
a one-line, flag-free description in ``COMMAND_BLURBS``. ``test_help.py``'s
completeness test fails the moment a new command is added without a group
(missing direction), or an old group entry outlives its command (stale
direction) — the same two-directional regression guard as
``test_compat.py::test_every_subcommand_is_classified``. ``group_for()``
additionally falls back to ``DEFAULT_GROUP`` at RUNTIME for any command that
somehow still reaches it unclassified, so a drifted build degrades to "shown
under low-level" rather than silently dropping the command from the screen —
belt-and-suspenders on top of the test, never a replacement for it.

Localization (57B-120): kept English-only for v1. Translating ~23 one-line
command blurbs and keeping them perfectly in sync on every future command
addition was judged not worth it for this slice — and ``locale.py``'s
``mirrored_keys()`` gate hard-fails on any new catalog key whose non-English
value is an uncaught copy of English, so half-translating this screen would
be worse than not touching the catalog at all. This module does not import
``locale`` and does not add any catalog key.
"""
from __future__ import annotations

from . import paths

# --------------------------------------------------------------------------
# Group identities, order, and titles
# --------------------------------------------------------------------------

GET_STARTED = "get_started"
ANALYZE = "analyze"
FIND_RESULTS = "find_results"
MAINTAIN = "maintain"
LOW_LEVEL = "low_level"

#: Fallback group for a registered command with no explicit ``COMMAND_GROUPS``
#: entry (see the module docstring's drift-proofing note). Low-level rather
#: than any user-facing group: an unclassified command is, by construction,
#: one this module's author has not yet vetted as newcomer-safe.
DEFAULT_GROUP = LOW_LEVEL

GROUP_ORDER: tuple[str, ...] = (GET_STARTED, ANALYZE, FIND_RESULTS, MAINTAIN, LOW_LEVEL)

GROUP_TITLES: dict[str, str] = {
    GET_STARTED: "Get started",
    ANALYZE: "Analyze",
    FIND_RESULTS: "Find results",
    MAINTAIN: "Maintain",
    LOW_LEVEL: ("Low-level / diagnostic — not the normal path "
                "(these are driven by the skill, not by hand)"),
}

# --------------------------------------------------------------------------
# Every registered subcommand -> its group. One explicit entry per command
# (see module docstring). Keep this and ``_GROUP_DISPLAY_ORDER``/
# ``COMMAND_BLURBS`` in sync — ``test_help.py`` enforces the group mapping's
# completeness in both directions; a missing blurb degrades gracefully (see
# ``render()``) rather than crashing, but should not happen in practice.
# --------------------------------------------------------------------------
COMMAND_GROUPS: dict[str, str] = {
    "doctor": GET_STARTED,
    "setup": GET_STARTED,
    "help": GET_STARTED,

    "new-run": ANALYZE,
    "new-drilldown": ANALYZE,

    "list": FIND_RESULTS,
    "export": FIND_RESULTS,
    "status": FIND_RESULTS,
    "compare-runs": FIND_RESULTS,

    "migrate": MAINTAIN,

    "discover": LOW_LEVEL,
    "run": LOW_LEVEL,
    "sweep": LOW_LEVEL,
    "callgraph": LOW_LEVEL,
    "dependency-map": LOW_LEVEL,
    "system-model": LOW_LEVEL,
    "prepare-overview": LOW_LEVEL,
    "finalize-module-map": LOW_LEVEL,
    "finalize-findings": LOW_LEVEL,
    "audit-overview": LOW_LEVEL,
    "mark-stage": LOW_LEVEL,
    "rollback": LOW_LEVEL,
    "accept": LOW_LEVEL,
}

# Curated display order WITHIN each group (this is display polish only — the
# completeness guarantee lives in ``COMMAND_GROUPS``/``group_for()``, not
# here). A command assigned to a group but missing from its tuple here still
# renders — ``_ordered_commands_for`` appends it, sorted, rather than
# dropping it silently.
_GROUP_DISPLAY_ORDER: dict[str, tuple[str, ...]] = {
    GET_STARTED: ("doctor", "setup", "help"),
    ANALYZE: ("new-run", "new-drilldown"),
    FIND_RESULTS: ("list", "export", "status", "compare-runs"),
    MAINTAIN: ("migrate",),
    LOW_LEVEL: (
        "discover", "run", "sweep", "callgraph", "dependency-map",
        "system-model", "prepare-overview", "finalize-module-map",
        "finalize-findings", "audit-overview", "mark-stage", "rollback",
        "accept",
    ),
}

_NOT_AVAILABLE_PREFIX = "[NOT AVAILABLE IN v1] "

# One short, plain-language, flag-free line per command — no internals; point
# to ``<command> --help`` for detail (added once, in the closing line of
# ``render()``, not repeated per command).
COMMAND_BLURBS: dict[str, str] = {
    "doctor": "Check what's installed and what a target needs before you run anything.",
    "setup": "Install just what a target needs — nothing more.",
    "help": "This screen — a task-oriented tour of every command.",

    "new-run": "Start a fresh project overview (module map + ranked findings).",
    "new-drilldown": (
        _NOT_AVAILABLE_PREFIX
        + "Drill into one module for a PRD + dev-facing health report."
    ),

    "list": "See every run you've produced, grouped by project.",
    "export": "Render a completed run to a shareable format (HTML by default).",
    "status": "Check a run's resume point, and whether it has gone stale.",
    "compare-runs": "Diff two completed runs for parity (developer use).",

    "migrate": "One-time move of a legacy install's runs into the current data root.",

    "discover": "Inventory a workspace's repositories (stage 1 of a run).",
    "run": "Run one analysis tool against one repository.",
    "sweep": "Run every applicable tool against a run.",
    "callgraph": "Extract function/method call edges.",
    "dependency-map": "Extract per-repository import maps.",
    "system-model": "Assemble the system model from a run's evidence.",
    "prepare-overview": "Run/resume the deterministic overview pipeline.",
    "finalize-module-map": "Validate and lock in the module map.",
    "finalize-findings": "Validate and render the findings blocks.",
    "audit-overview": "Audit a run's structured artifacts before completion.",
    "mark-stage": "Record a stage checkpoint as done.",
    "rollback": "Re-open a stage — and everything after it.",
    "accept": "Set a run as the project's `current` pointer.",
}


def registered_commands() -> list[str]:
    """Every subcommand ``cli.parser()`` actually registers, in argparse's own
    registration order — the single source of truth this module's grouping is
    checked against (never a hand-maintained copy). Imports ``cli`` lazily
    (function-local): ``cli.py`` imports this module to render ``help``, so a
    module-level import here would create a cycle."""
    from .cli import parser as cli_parser
    subparsers_action = next(
        action for action in cli_parser()._subparsers._group_actions
        if action.dest == "command"
    )
    return list(subparsers_action.choices.keys())


def group_for(command: str) -> str:
    """The group ``command`` belongs to; ``DEFAULT_GROUP`` for anything not
    (yet) given an explicit entry in ``COMMAND_GROUPS`` — see the module
    docstring's drift-proofing note. A real function (not inlined) so both
    ``render()`` and the completeness test can call the same logic."""
    return COMMAND_GROUPS.get(command, DEFAULT_GROUP)


def _ordered_commands_for(group: str, registered: list[str]) -> list[str]:
    curated = [cmd for cmd in _GROUP_DISPLAY_ORDER.get(group, ()) if cmd in registered]
    curated_set = set(curated)
    # Anything registered and assigned to this group (via COMMAND_GROUPS or
    # the DEFAULT_GROUP fallback) but missing from the curated order above —
    # appended, sorted, rather than silently dropped.
    leftover = sorted(
        cmd for cmd in registered
        if group_for(cmd) == group and cmd not in curated_set
    )
    return curated + leftover


def _version_string() -> str:
    from .cli import _version_string as cli_version_string
    return cli_version_string()


def render() -> str:
    """The full ``help`` screen: skill version + resolved data root, then
    every registered command grouped by task, ending with an obvious next
    step. Read-only — never creates the data root or any directory."""
    registered = registered_commands()
    try:
        data_root = str(paths.resolved_data_root())
    except (OSError, ValueError) as exc:
        data_root = f"(unavailable: {exc})"

    lines: list[str] = []
    lines.append(_version_string())
    lines.append(f"data root: {data_root}")
    lines.append("")

    for group in GROUP_ORDER:
        commands = _ordered_commands_for(group, registered)
        if not commands:
            continue
        lines.append(GROUP_TITLES[group])
        for command in commands:
            blurb = COMMAND_BLURBS.get(command, "(no description yet)")
            lines.append(f"  {command:<20} {blurb}")
        if group == ANALYZE:
            lines.append(
                "  (normal path: invoke through the Project Analysis skill "
                "itself — it drives new-run and every stage after it; "
                "hand-running these is the exception, not the rule)"
            )
        if group == MAINTAIN:
            lines.append(
                "  (after an upgrade, also run `setup` to reconcile the "
                "installed runtime with the new code)"
            )
        lines.append("")

    lines.append("Run `<command> --help` for flags and details on any command.")
    lines.append("New here? Start with `doctor`.")
    return "\n".join(lines) + "\n"
