"""Tests for the upgrade/compatibility mechanism (57B-95).

Hermetic: no network, tmp_path only. The autouse ``_isolated_data_root``
fixture in conftest.py already points ``$PROJECT_ANALYSIS_HOME`` at a
throwaway directory.
"""

import os
import stat

import pytest

from analysis_wrapper import compat, lifecycle, run_provenance
from analysis_wrapper.cli import main
from analysis_wrapper.targetspec import TargetSpec


def _empty_path(monkeypatch, tmp_path):
    """Point PATH at an empty directory so no developer-managed tool is ever
    accidentally found on the machine running the suite (mirrors
    test_doctor.py's helper of the same name)."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return empty


def _fake_bin(bin_dir, name: str, version_line: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_run(tmp_path, target, *, analyzer_root=None, done_stages=()):
    """Mint a run directory with a real run-provenance.json + run-state.json,
    without going through the full new-run CLI pipeline."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    spec = TargetSpec([target])
    document = run_provenance.create_document(
        spec, analyzer_root=analyzer_root or tmp_path, language="en",
        analyzed_at="2026-01-01T00:00:00+00:00")
    run_provenance.write(run_dir, document)
    state = lifecycle.RunState.create("run-id", "project", spec)
    for stage in done_stages:
        state.mark(stage)
    state.save(run_dir)
    return run_dir


# --------------------------------------------------------------------------
# Version stamping
# --------------------------------------------------------------------------

def test_provenance_stamps_compat_block(tmp_path, target):
    run_dir = _make_run(tmp_path, target)
    document = run_provenance.load(run_dir)
    assert document["compat"] == {
        "skill_version": compat.skill_version(),
        "artifact_contract_version": compat.ARTIFACT_CONTRACT_VERSION,
        "runtime_contract": compat.paths.RUNTIME_CONTRACT,
    }


def test_legacy_run_without_compat_block_is_pre_3_0_0_family(tmp_path, target):
    """A run minted before this stamping existed simply lacks ``compat`` —
    it must still load and classify into the pre-3.0.0 family, never crash."""
    run_dir = _make_run(tmp_path, target)
    document = run_provenance.load(run_dir)
    del document["compat"]
    run_provenance.write(run_dir, document)
    assert compat.run_schema_family(run_dir) == "pre-3.0.0"


# --------------------------------------------------------------------------
# Runtime drift
# --------------------------------------------------------------------------

def test_runtime_drift_detected_for_pinned_present_tool(monkeypatch, tmp_path):
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")
    report = compat.runtime_reconciliation(None)
    row = next(r for r in report["rows"] if r["id"] == "analysis-wrapper")
    assert row["present"] is True
    assert row["drift"] != ""
    assert report["any_drift"] is True
    outcome, detail = compat.runtime_outcome(None)
    assert outcome == "reconcile"
    assert "analysis-wrapper" in detail


def _fake_build_report_all_pinned_present(monkeypatch, *, present_ids=None):
    """Fully controlled stand-in for ``doctor.build_report`` (57B-95 review
    FIX 3 test support): faking every PINNED tool's real presence would need
    a real venv/node_tools/go bin layout, which is exactly the kind of setup
    ``doctor``'s OWN test suite already covers -- this module's tests only
    need to exercise ``compat.runtime_reconciliation``'s OWN present/absent/
    drift bookkeeping, so the report is synthesized directly from the real
    manifest (still read for real -- only the probing is stubbed).
    ``present_ids=None`` means every pinned tool is present and matching its
    pin (the true "no drift, fully reconciled" shape); otherwise only the
    given ids are present (matching), the rest absent."""
    from analysis_wrapper import doctor as doctor_mod
    manifest_tools = doctor_mod.read_manifest()["tools"]

    def fake_build_report(workspace=None, *, tool_ids=None):
        tools = []
        for tool in manifest_tools:
            if tool_ids is not None and tool["id"] not in tool_ids:
                continue
            pinned = bool(tool.get("pinned"))
            is_present = pinned and (
                present_ids is None or tool["id"] in present_ids)
            version = tool.get("validated_version") or "" if is_present else ""
            tools.append({
                "id": tool["id"],
                "name": tool["name"],
                "state": "present" if is_present else "unavailable",
                "detected_version": version,
                "drift": "",
            })
        return {"tools": tools}

    monkeypatch.setattr(doctor_mod, "build_report", fake_build_report)


def test_no_drift_when_installed_version_matches_manifest_pin(monkeypatch, tmp_path):
    """Every pinned tool present and matching its manifest pin: the true
    fully-reconciled shape, distinct from the partial-install shape below."""
    _fake_build_report_all_pinned_present(monkeypatch)
    report = compat.runtime_reconciliation(None)
    row = next(r for r in report["rows"] if r["id"] == "analysis-wrapper")
    assert row["drift"] == ""
    assert report["any_drift"] is False
    assert report["partial_install"] is False
    assert compat.runtime_outcome(None) == ("ok", "")


def test_missing_pinned_tool_is_not_counted_as_drift(monkeypatch, tmp_path):
    """A pinned tool that was never installed is a first-run/setup-needed
    situation, not an upgrade-compat hazard — it must not, by itself, flip
    ``any_drift``, as long as EVERY pinned tool is equally absent."""
    _empty_path(monkeypatch, tmp_path)  # nothing on PATH at all
    report = compat.runtime_reconciliation(None)
    row = next(r for r in report["rows"] if r["id"] == "analysis-wrapper")
    assert row["present"] is False
    assert row["drift"] == ""
    assert report["any_drift"] is False
    assert report["partial_install"] is False


def test_partial_install_is_counted_as_drift(monkeypatch, tmp_path):
    """(57B-95 review FIX 3): SOME pinned tools present (and matching their
    own pin) while OTHERS are absent is a half-reconciled runtime -- new code
    needing a pinned tool this checkout's `setup` never provisioned. This
    must flip ``any_drift`` even though no single tool's own version
    disagrees with its pin, distinguishing it from the "nothing installed
    yet" shape which must stay drift-free."""
    _fake_build_report_all_pinned_present(
        monkeypatch, present_ids={"analysis-wrapper"})
    report = compat.runtime_reconciliation(None)
    assert report["partial_install"] is True
    assert report["any_drift"] is True
    absent_row = next(r for r in report["rows"] if r["id"] != "analysis-wrapper")
    assert absent_row["present"] is False
    assert absent_row["drift"] != ""
    outcome, detail = compat.runtime_outcome(None)
    assert outcome == "reconcile"
    assert absent_row["id"] in detail


# --------------------------------------------------------------------------
# Entry-point guard: gates analysis commands, exempts read-only ones
# --------------------------------------------------------------------------

def test_guard_refuses_analysis_command_when_runtime_drifted(monkeypatch, tmp_path):
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")
    with pytest.raises(compat.RuntimeCompatRefusal, match="analysis-wrapper"):
        compat.guard_entry("new-run")


def test_guard_exempts_read_only_commands(monkeypatch, tmp_path):
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")
    for command in ("doctor", "setup", "migrate", "list", "help", "status", "export",
                     "compare-runs"):
        compat.guard_entry(command)  # must not raise


def test_every_subcommand_is_classified():
    """Every subcommand registered in ``cli.parser()`` must be explicitly
    listed in ``compat.COMMAND_CLASSIFICATION``. This is the regression guard
    for the original bug: a new command silently falling outside BOTH an
    exempt list and a gated list (and so being wrongly gated, or -- worse --
    wrongly left ungated) must fail this test, not ship unnoticed."""
    from analysis_wrapper.cli import parser as cli_parser
    subparsers_action = next(
        action for action in cli_parser()._subparsers._group_actions
        if action.dest == "command"
    )
    registered = set(subparsers_action.choices.keys())
    classified = set(compat.COMMAND_CLASSIFICATION.keys())
    missing = registered - classified
    assert not missing, (
        f"cli.parser() registers {sorted(missing)} but compat.COMMAND_CLASSIFICATION "
        "does not classify it as gated/not-gated -- add an explicit entry "
        "(with a one-line rationale comment) before merging."
    )
    # (57B-95 review FIX 6): the reverse direction matters too -- a STALE
    # classified-but-unregistered entry (a command renamed/removed from
    # cli.parser() without its old classification being cleaned up) was never
    # caught by the ``missing`` check above. ``help`` was registered as a real
    # ``sub.add_parser`` subcommand in 57B-120 (previously it was pre-
    # classified ahead of registration); like ``list`` before it, it is now
    # covered by the ``missing``/normal path instead, so no allowlist entry is
    # needed for it any more. Kept as an empty, explicit set (rather than
    # deleted outright) so a genuinely upcoming command has an obvious place
    # to be added -- not a loophole for a real stale entry to hide behind.
    not_yet_registered: set[str] = set()
    stale = classified - registered - not_yet_registered
    assert not stale, (
        f"compat.COMMAND_CLASSIFICATION classifies {sorted(stale)} but "
        "cli.parser() no longer registers it -- remove the stale entry, or "
        "add it to the documented not_yet_registered allowlist if it is a "
        "genuinely upcoming command."
    )


def test_setup_still_works_when_runtime_drifted(monkeypatch, tmp_path):
    """The guard's own refusal message tells the user to run `setup` to
    reconcile -- `setup` itself must therefore never be gated, or the
    remedy would be unreachable."""
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")
    rc = main(["setup", "--workspace", str(tmp_path), "--dry-run"])
    assert rc != 4  # never the compat-refusal exit code


def test_export_of_completed_run_still_works_when_runtime_drifted(
    monkeypatch, tmp_path, target
):
    """A completed run's reports are promised `readable` by the compat
    matrix; that guarantee must hold even when the CLI entry guard sees a
    drifted runtime -- `export` must not be gated."""
    from analysis_wrapper import lifecycle

    run_dir = _make_run(tmp_path, target, done_stages=list(lifecycle.STAGES))
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")
    rc = main(["export", "--run", str(run_dir), "--format", "html"])
    assert rc != 4  # never the compat-refusal exit code


def test_status_and_compare_runs_still_work_when_runtime_drifted(
    monkeypatch, tmp_path, target
):
    """Read-only informational commands must remain usable while a user
    diagnoses a runtime-drift refusal."""
    from analysis_wrapper import lifecycle

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    run_a = _make_run(root_a, target, done_stages=["discovery"])
    run_b = _make_run(root_b, target, done_stages=["discovery"])
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")

    rc_status = main(["status", "--run", str(run_a)])
    assert rc_status != 4  # never the compat-refusal exit code

    rc_compare = main(["compare-runs", str(run_a), str(run_b)])
    assert rc_compare != 4  # never the compat-refusal exit code


def test_guard_accept_degraded_env_bypasses_refusal(monkeypatch, tmp_path):
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")
    monkeypatch.setenv(compat.ACCEPT_DEGRADED_RUNTIME_ENV, "1")
    compat.guard_entry("new-run")  # must not raise


def test_degraded_env_bypass_warns_and_returns_detail(monkeypatch, tmp_path, capsys):
    """(57B-95 review FIX 5): the bypass must only fire when there is an
    ACTUAL detected drift to suppress, must print a visible stderr warning
    when it does, and must hand the caller the detail string (so a run
    minted this invocation can stamp it into its own provenance)."""
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")
    monkeypatch.setenv(compat.ACCEPT_DEGRADED_RUNTIME_ENV, "1")

    detail = compat.guard_entry("new-run")
    assert detail != ""
    assert "analysis-wrapper" in detail
    err = capsys.readouterr().err
    assert "wrapper warning" in err
    assert compat.ACCEPT_DEGRADED_RUNTIME_ENV in err


def test_degraded_env_set_but_nothing_drifted_is_silent(monkeypatch, tmp_path, capsys):
    """The bypass env var being SET is not itself news -- only an actual
    suppressed drift is. An inherited env var (shell profile, CI) must not
    print a warning or report a notice when the runtime was already fine."""
    _fake_build_report_all_pinned_present(monkeypatch)
    monkeypatch.setenv(compat.ACCEPT_DEGRADED_RUNTIME_ENV, "1")

    detail = compat.guard_entry("new-run")
    assert detail == ""
    assert capsys.readouterr().err == ""


def test_degraded_runtime_notice_is_stamped_into_a_freshly_minted_run(
    monkeypatch, tmp_path, target
):
    """The provenance stamp of a run minted while the degraded-runtime
    bypass was actually in effect must record that fact, so the run is not
    forensically indistinguishable from one minted under a clean runtime."""
    spec = TargetSpec([target])
    document = run_provenance.create_document(
        spec, analyzer_root=tmp_path, language="en",
        analyzed_at="2026-01-01T00:00:00+00:00",
        degraded_runtime_notice="analysis-wrapper: installed 0.1.0, code expects 0.4.0",
    )
    assert document["compat"]["degraded_runtime_accepted"] == (
        "analysis-wrapper: installed 0.1.0, code expects 0.4.0")

    # No bypass this time: the key must be entirely absent, not present-empty.
    clean_document = run_provenance.create_document(
        spec, analyzer_root=tmp_path, language="en",
        analyzed_at="2026-01-01T00:00:00+00:00",
    )
    assert "degraded_runtime_accepted" not in clean_document["compat"]


def test_cli_doctor_and_migrate_still_work_when_runtime_drifted(monkeypatch, tmp_path):
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")

    assert main(["doctor", "--json"]) in (0, 3, 4)  # never the compat-refusal path
    legacy = tmp_path / "legacy-skill-root"
    legacy.mkdir()
    assert main(["migrate", "--legacy-skill-root", str(legacy)]) == 0

    # An actual analysis command IS refused, with the dedicated exit code.
    rc = main(["new-run", "--workspace", str(tmp_path)])
    assert rc == 4


def test_cli_version_is_unaffected_by_guard(monkeypatch, tmp_path, capsys):
    empty = _empty_path(monkeypatch, tmp_path)
    _fake_bin(empty, "project-analysis-wrapper", "project-analysis-wrapper 0.1.0")
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "Project Analysis skill" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Resume refusal across the 3.0.0 artifact-contract break
# --------------------------------------------------------------------------

def test_incompatible_incomplete_run_resume_is_refused(monkeypatch, tmp_path, target):
    run_dir = _make_run(tmp_path, target, done_stages=["discovery"])
    assert lifecycle.RunState.load(run_dir).next_stage() != ""  # genuinely incomplete

    # The run was minted under today's (pre-3.0.0) code; simulate the CODE's
    # ARTIFACT CONTRACT having since moved to the post-3.0.0 family without
    # touching the run's own (already-written) provenance. (57B-95 review
    # FIX 2): this monkeypatches ARTIFACT_CONTRACT_VERSION, not skill_version
    # — code_family() is derived from the contract this code actually reads
    # and writes, never from the skill's independently-versioned release tag.
    monkeypatch.setattr(compat, "ARTIFACT_CONTRACT_VERSION", "3.0.0")

    outcome, note = compat.resume_outcome(run_dir)
    assert outcome == compat.UNSUPPORTED
    assert "3.0.0" in note
    with pytest.raises(compat.CompatRefusal, match="cannot be resumed"):
        compat.refuse_incompatible_resume(run_dir)


def test_same_family_incomplete_run_resumes_normally(tmp_path, target):
    run_dir = _make_run(tmp_path, target, done_stages=["discovery"])
    outcome, _note = compat.resume_outcome(run_dir)
    assert outcome == compat.RESUMABLE
    compat.refuse_incompatible_resume(run_dir)  # must not raise


def test_completed_run_under_older_schema_stays_readable(monkeypatch, tmp_path, target):
    run_dir = _make_run(tmp_path, target, done_stages=list(lifecycle.STAGES))
    assert lifecycle.RunState.load(run_dir).next_stage() == ""  # genuinely complete

    monkeypatch.setattr(compat, "ARTIFACT_CONTRACT_VERSION", "3.0.0")
    outcome, note = compat.completed_run_outcome(run_dir)
    assert outcome == compat.READABLE
    assert "NO migration" in note


def test_new_overview_never_blocked_by_incompatible_old_run(monkeypatch, tmp_path, target):
    _make_run(tmp_path, target, done_stages=["discovery"])  # an old, now-stale run sits here
    monkeypatch.setattr(compat, "ARTIFACT_CONTRACT_VERSION", "3.0.0")
    # Minting a new overview never even looks at the old run's schema.
    assert compat.new_overview_outcome() == (
        compat.READABLE, "a new overview never inspects prior runs before minting")


def test_break_row_matches_the_documented_outcomes():
    row = compat.matrix_row("post-3.0.0", "pre-3.0.0")
    assert row["completed_run"] == compat.READABLE
    assert row["incomplete_run"] == compat.UNSUPPORTED
    assert row["new_overview"] == compat.READABLE
    assert "NO migration" in row["note"]


def test_forward_break_row_completed_run_stays_readable():
    """(57B-95 review FIX 4): the row for OLDER code encountering a run made
    by NEWER code (`pre-3.0.0` code, `post-3.0.0` schema) must say the same
    thing ``completed_run_outcome`` actually returns for it: READABLE. This
    row used to say ``unsupported`` here while the function unconditionally
    returned ``readable`` regardless — a straight contradiction between the
    matrix and the code that consults it."""
    row = compat.matrix_row("pre-3.0.0", "post-3.0.0")
    assert row["completed_run"] == compat.READABLE
    assert row["incomplete_run"] == compat.UNSUPPORTED
    assert row["new_overview"] == compat.READABLE


def test_no_auto_migration_run_directory_is_never_rewritten(monkeypatch, tmp_path, target):
    run_dir = _make_run(tmp_path, target, done_stages=["discovery"])
    provenance_path = run_dir / "run-provenance.json"
    state_path = run_dir / lifecycle.RunState.FILENAME
    before = {
        provenance_path: provenance_path.read_bytes(),
        state_path: state_path.read_bytes(),
    }

    monkeypatch.setattr(compat, "ARTIFACT_CONTRACT_VERSION", "3.0.0")
    with pytest.raises(compat.CompatRefusal):
        compat.refuse_incompatible_resume(run_dir)
    compat.completed_run_outcome(run_dir)
    compat.resume_outcome(run_dir)

    for path, content in before.items():
        assert path.read_bytes() == content, f"{path} was mutated by a compat check"


# --------------------------------------------------------------------------
# FIX 2: code_family() tracks the artifact contract, not the skill release
# --------------------------------------------------------------------------

def test_code_family_tracks_artifact_contract_version_not_skill_version(monkeypatch):
    """Bumping the skill's release VERSION alone must never change the code
    family; only ARTIFACT_CONTRACT_VERSION may."""
    monkeypatch.setattr(compat, "skill_version", lambda: "9.9.9")
    assert compat.code_family() == compat.artifact_contract_family(
        compat.ARTIFACT_CONTRACT_VERSION)

    monkeypatch.setattr(compat, "ARTIFACT_CONTRACT_VERSION", "3.0.0")
    assert compat.code_family() == "post-3.0.0"


@pytest.mark.parametrize("contract_version", ["1.0.0", "2.0.0", "3.0.0", "4.5.6"])
def test_self_minted_run_is_always_resumable_under_this_code(
    monkeypatch, tmp_path, target, contract_version
):
    """(57B-95 review FIX 2): a run minted by THIS code must be resumable
    under THIS code no matter what ARTIFACT_CONTRACT_VERSION happens to be —
    bumping the contract can never make the code reject its own fresh runs.
    Holds for any contract value because ``code_family()`` and
    ``compat_stamp()`` both read the same global at call time."""
    monkeypatch.setattr(compat, "ARTIFACT_CONTRACT_VERSION", contract_version)
    run_dir = _make_run(tmp_path, target, done_stages=["discovery"])
    outcome, _note = compat.resume_outcome(run_dir)
    assert outcome == compat.RESUMABLE
    compat.refuse_incompatible_resume(run_dir)  # must not raise


# --------------------------------------------------------------------------
# Path change is NOT staleness (the regression this module must prevent)
# --------------------------------------------------------------------------

def test_same_code_at_a_different_absolute_path_is_not_stale(tmp_path):
    """Same code/content at two different absolute install paths must hash
    identically — ``analyzer_source_state``/``analyzer_observation`` key
    only on relative paths + file content, never the absolute root."""
    first_root = tmp_path / "checkout-a" / "wrapper"
    second_root = tmp_path / "somewhere" / "else" / "wrapper-v2"
    for root in (first_root, second_root):
        root.mkdir(parents=True)
        (root / "module.py").write_text("print('hello')\n")

    recorded = run_provenance.analyzer_observation(first_root)
    moved = run_provenance.analyzer_observation(second_root)
    assert recorded["root"] != moved["root"]  # sanity: the two roots really differ
    assert recorded["source_state_sha256"] == moved["source_state_sha256"]


def test_analyzer_staleness_never_compares_the_recorded_root(monkeypatch):
    """``analyzer_staleness`` only diffs
    ``("version", "git_head", "dirty_detail", "source_state_sha256")`` — a
    re-observation that reports a DIFFERENT absolute root (a git-clone-moved,
    symlink-swapped, or new-version-directory install) but identical
    version/content must never be flagged stale."""
    recorded = {
        "root": "/old/checkout/path", "version": "1.0.0-dev",
        "git_head": "", "dirty_detail": "no",
        "source_state_sha256": "deadbeef",
    }
    monkeypatch.setattr(run_provenance, "analyzer_observation", lambda root: {
        "root": "/new/checkout/path-v2",  # different absolute path
        "version": recorded["version"], "git_head": recorded["git_head"],
        "dirty_detail": recorded["dirty_detail"],
        "source_state_sha256": recorded["source_state_sha256"],
    })
    assert run_provenance.analyzer_staleness(recorded) == []


# --------------------------------------------------------------------------
# FIX 1: the run-level guard is actually wired into the CLI dispatch path
# (CLI-level tests only -- a direct call to a compat function proved nothing
# about whether any real command actually invokes it, which is exactly how
# the original bug shipped).
# --------------------------------------------------------------------------

_RUN_ARG_GATED_COMMANDS = [
    ("mark-stage", ["--stage", "signals"]),
    ("rollback", ["--stage", "signals"]),
    ("system-model", []),
    ("prepare-overview", []),
    ("finalize-module-map", []),
    ("finalize-findings", []),
    ("audit-overview", []),
]


def _snapshot(run_dir):
    return {
        path: path.read_bytes()
        for path in sorted(run_dir.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("command,extra_args", _RUN_ARG_GATED_COMMANDS)
def test_cli_refuses_to_advance_incompatible_run(
    monkeypatch, tmp_path, target, command, extra_args
):
    """Reproduces the FIX 1 bug directly: with the CODE's family moved past
    the artifact-contract break and an old, genuinely INCOMPLETE run sitting
    on disk, every command that advances an EXISTING run must refuse — not
    just ``compat.refuse_incompatible_resume`` called directly (that already
    passed before this fix; the missing piece was that NOTHING in the CLI
    dispatch path ever called it)."""
    run_dir = _make_run(tmp_path, target, done_stages=["discovery"])
    before = _snapshot(run_dir)

    monkeypatch.setattr(compat, "ARTIFACT_CONTRACT_VERSION", "3.0.0")
    rc = main([command, "--run", str(run_dir), *extra_args])
    assert rc == 4

    assert _snapshot(run_dir) == before, (
        f"{command} mutated the refused run directory")


@pytest.mark.parametrize("command,extra_args", _RUN_ARG_GATED_COMMANDS)
def test_cli_advances_compatible_run_without_compat_refusal(
    monkeypatch, tmp_path, target, command, extra_args
):
    """The same commands must NOT be refused by the compat guard when the
    run's family matches this code's -- whatever they do fail on afterwards
    (missing targets.json, unmet stage preconditions, ...) must be a
    different, ordinary error, never exit 4 / a ``CompatRefusal``."""
    run_dir = _make_run(tmp_path, target, done_stages=["discovery"])
    rc = main([command, "--run", str(run_dir), *extra_args])
    assert rc != 4


def test_cli_refuses_callgraph_and_dependency_map_over_incompatible_existing_run(
    monkeypatch, tmp_path, target
):
    """``callgraph``/``dependency-map`` reach an existing run directory via
    ``--out`` (post-discovery layering, ``executor.use_existing_run_directory``),
    not ``--run`` -- a second, distinct wiring point that needs its own
    coverage."""
    for command in ("callgraph", "dependency-map"):
        run_dir = tmp_path / f"run-{command}"
        run_dir.mkdir()
        TargetSpec([target], produced_by="cli-test").save(run_dir / "targets.json")
        run_provenance.write(run_dir, run_provenance.create_document(
            TargetSpec([target]), analyzer_root=tmp_path, language="en",
            analyzed_at="2026-01-01T00:00:00+00:00"))
        before = _snapshot(run_dir)

        monkeypatch.setattr(compat, "ARTIFACT_CONTRACT_VERSION", "3.0.0")
        rc = main(["--targets", str(run_dir / "targets.json"), "--out", str(run_dir),
                   command])
        assert rc == 4
        assert _snapshot(run_dir) == before, f"{command} mutated the refused run"
        monkeypatch.undo()


def test_cli_export_status_compare_runs_new_run_unaffected_by_incompatible_run(
    monkeypatch, tmp_path, target
):
    """The flip side of the refusal: a completed run must remain
    readable/exportable, and minting a brand-new run must never be blocked
    by an old incompatible run sitting elsewhere on disk (guardrail
    requirement, not merely the matrix's own promise)."""
    root_complete = tmp_path / "complete"
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_complete.mkdir()
    root_a.mkdir()
    root_b.mkdir()
    complete_run = _make_run(root_complete, target, done_stages=list(lifecycle.STAGES))
    run_a = _make_run(root_a, target, done_stages=["discovery"])
    run_b = _make_run(root_b, target, done_stages=["discovery"])
    monkeypatch.setattr(compat, "ARTIFACT_CONTRACT_VERSION", "3.0.0")

    assert main(["status", "--run", str(complete_run)]) != 4
    assert main(["export", "--run", str(complete_run), "--format", "html"]) != 4
    assert main(["compare-runs", str(run_a), str(run_b)]) != 4

    workspace = tmp_path / "fresh-workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", "utf-8")
    assert main(["new-run", "--workspace", str(workspace)]) != 4
