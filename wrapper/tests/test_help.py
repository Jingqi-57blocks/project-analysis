"""Tests for the ``help`` command (57B-120): a curated, task-grouped tour of
every subcommand, standing in for argparse's own alphabetical, undifferentiated
``--help`` output.

Hermetic: no network, tmp_path only. The autouse ``_isolated_data_root``
fixture in conftest.py already points ``$PROJECT_ANALYSIS_HOME`` at a
throwaway directory for every test in this module.
"""

from __future__ import annotations

import io
import contextlib

import pytest

from analysis_wrapper import compat, help as help_mod, paths
from analysis_wrapper.cli import main, parser


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def _registered_commands():
    subparsers_action = next(
        action for action in parser()._subparsers._group_actions
        if action.dest == "command"
    )
    return set(subparsers_action.choices.keys())


# --------------------------------------------------------------------------
# Every registered subcommand appears in the output
# --------------------------------------------------------------------------

def test_every_registered_command_appears_in_help_output():
    code, out = _run(["help"])
    assert code == 0
    for command in _registered_commands():
        assert command in out, f"{command!r} missing from `help` output"


def test_help_via_bare_invocation_matches_explicit_help_command():
    code_bare, out_bare = _run([])
    code_explicit, out_explicit = _run(["help"])
    assert code_bare == 0
    assert code_explicit == 0
    assert out_bare == out_explicit


# --------------------------------------------------------------------------
# Drift-proofing: COMMAND_GROUPS completeness, both directions (mirrors
# test_compat.py::test_every_subcommand_is_classified)
# --------------------------------------------------------------------------

def test_every_subcommand_has_a_help_group():
    """Every subcommand registered in cli.parser() must have an explicit
    entry in help.COMMAND_GROUPS -- a new command added without one must fail
    this test, not silently render (or fail to render)."""
    registered = _registered_commands()
    classified = set(help_mod.COMMAND_GROUPS.keys())
    missing = registered - classified
    assert not missing, (
        f"cli.parser() registers {sorted(missing)} but help.COMMAND_GROUPS "
        "does not assign it to a group -- add an explicit entry (and a "
        "one-line blurb in COMMAND_BLURBS) before merging."
    )


def test_help_groups_name_no_stale_command():
    """The reverse direction: a COMMAND_GROUPS entry naming a command that no
    longer exists in cli.parser() is just as much a drift bug as a missing
    one -- it must fail this test, not sit there silently."""
    registered = _registered_commands()
    classified = set(help_mod.COMMAND_GROUPS.keys())
    stale = classified - registered
    assert not stale, (
        f"help.COMMAND_GROUPS assigns {sorted(stale)} to a group but "
        "cli.parser() no longer registers it -- remove the stale entry."
    )


def test_unclassified_command_falls_back_to_default_group():
    """A command with no COMMAND_GROUPS entry at all must still appear
    (under the low-level default group) rather than vanish -- exercised
    directly against group_for(), independent of whether any such gap
    currently exists in the real registry."""
    assert help_mod.group_for("some-future-command-nobody-classified-yet") \
        == help_mod.DEFAULT_GROUP
    assert help_mod.DEFAULT_GROUP == help_mod.LOW_LEVEL


# --------------------------------------------------------------------------
# new-drilldown shown as unavailable in v1
# --------------------------------------------------------------------------

def test_new_drilldown_shown_as_unavailable_in_v1():
    _, out = _run(["help"])
    assert "new-drilldown" in out
    lines = [line for line in out.splitlines() if "new-drilldown" in line]
    assert any("NOT AVAILABLE IN v1" in line for line in lines)


# --------------------------------------------------------------------------
# Low-level group clearly marked as not the normal path
# --------------------------------------------------------------------------

def test_low_level_group_marked_as_not_normal_path():
    _, out = _run(["help"])
    assert "not the normal path" in out
    # Every low-level command must actually be listed under that heading,
    # not just have the heading present somewhere in the output.
    low_level_heading_index = out.index(help_mod.GROUP_TITLES[help_mod.LOW_LEVEL])
    tail = out[low_level_heading_index:]
    for command, group in help_mod.COMMAND_GROUPS.items():
        if group == help_mod.LOW_LEVEL:
            assert command in tail


# --------------------------------------------------------------------------
# Resolved data root + skill version shown
# --------------------------------------------------------------------------

def test_output_includes_resolved_data_root_and_skill_version():
    _, out = _run(["help"])
    assert str(paths.resolved_data_root()) in out
    assert compat.skill_version() in out or "Project Analysis skill" in out


def test_ends_with_a_next_step_pointer():
    _, out = _run(["help"])
    assert "doctor" in out.strip().splitlines()[-1] \
        or "New here" in out


# --------------------------------------------------------------------------
# Read-only: no directory creation, even with a nonexistent data root
# --------------------------------------------------------------------------

def test_help_is_read_only_and_never_creates_the_data_root(monkeypatch, tmp_path):
    nonexistent = tmp_path / "does-not-exist-yet" / "nested"
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(nonexistent))
    code, out = _run(["help"])
    assert code == 0
    assert not nonexistent.exists()
    assert not nonexistent.parent.exists()
    assert str(nonexistent) in out


def test_bare_invocation_is_also_read_only(monkeypatch, tmp_path):
    nonexistent = tmp_path / "still-does-not-exist"
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(nonexistent))
    code, _ = _run([])
    assert code == 0
    assert not nonexistent.exists()


# --------------------------------------------------------------------------
# help works when the runtime is drifted (classified not-gated)
# --------------------------------------------------------------------------

def test_help_is_not_gated_by_runtime_drift(monkeypatch):
    """`help` must stay reachable even when compat.guard_entry would refuse a
    gated command -- it is classified not-gated in COMMAND_CLASSIFICATION."""
    assert compat.COMMAND_CLASSIFICATION.get("help") is False

    def _boom(*_args, **_kwargs):
        raise compat.RuntimeCompatRefusal("simulated drift")

    monkeypatch.setattr(compat, "runtime_outcome", _boom)
    # guard_entry consults COMMAND_CLASSIFICATION first and returns "" for
    # any not-gated command without ever calling runtime_outcome() -- prove
    # that directly, then prove the actual CLI path stays healthy too.
    assert compat.guard_entry("help") == ""
    code, out = _run(["help"])
    assert code == 0
    assert "doctor" in out


def test_help_exit_code_consistent_with_doctor_and_list():
    for argv in (["help"], ["doctor"], ["list"]):
        code, _ = _run(argv)
        assert code == 0, f"{argv} exited {code}"
