"""57B-97 Phase 7: v1 scope gating.

- `new-drilldown` is gated OFF at the CLI entry point (overview + diagnosis
  only in v1); the refusal fires before any filesystem access, so an existing
  drilldown run directory is never touched.
- `--language`'s default is auto-detected from the host locale, falling back
  to English for anything undecidable or not a delivered language.
- HTML export is opt-in: nothing auto-produces it; the standalone `export`
  command still works.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper import cli, locale
from analysis_wrapper.cli import main
from test_report_html import make_run


# --------------------------------------------------------------------------- #
# new-drilldown gate
# --------------------------------------------------------------------------- #

def test_new_drilldown_refuses_with_documented_exit_code(tmp_path, capsys):
    code = main(["new-drilldown", "--module", "leave",
                 "--skill-root", str(tmp_path / "skill")])
    assert code == cli.EXIT_DRILLDOWN_UNAVAILABLE
    assert code == 10
    err = capsys.readouterr().err
    assert "not available in this version" in err
    assert "v1 ships overview + diagnosis only" in err


def test_new_drilldown_help_text_mentions_v1_unavailability(capsys):
    try:
        cli.parser().parse_args(["new-drilldown", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:  # pragma: no cover - argparse --help always raises SystemExit
        raise AssertionError("--help did not exit")
    out = capsys.readouterr().out
    assert "NOT AVAILABLE IN v1" in out


def test_new_drilldown_refusal_never_touches_an_existing_drilldown_run(
        tmp_path, target, capsys):
    """A drilldown run minted before the gate landed (or by directly calling
    the still-wired internal implementation) must be completely untouched by
    a later refused `new-drilldown` CLI invocation -- no file added, removed,
    or modified."""
    skill_root = tmp_path / "skill"
    assert main(["new-run", "--workspace", str(Path(target.path).parent),
                 "--skill-root", str(skill_root), "--language", "en"]) == 0
    run_dir = Path(capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1])
    for stage in ("signals", "findings", "map", "overview"):
        assert main(["mark-stage", "--run", str(run_dir), "--stage", stage]) == 0
    capsys.readouterr()

    # Mint a real drilldown run via the still-wired internal implementation
    # (bypassing the CLI gate), simulating "existing drill-down data" that
    # predates v1 scope gating.
    args = cli.parser().parse_args(
        ["new-drilldown", "--skill-root", str(skill_root),
         "--module", "leave", "--from-run", run_dir.name])
    assert cli._new_drilldown(args) == 0
    drill_dir = Path(capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1])
    assert drill_dir.is_dir()

    before = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(drill_dir.rglob("*")) if p.is_file()
    }
    assert before  # sanity: the drilldown run actually has files

    # Now the gated CLI entry point refuses a NEW drill-down attempt for the
    # same module/run -- this must not touch the existing drill-down at all.
    code = main(["new-drilldown", "--skill-root", str(skill_root),
                 "--module", "leave", "--from-run", run_dir.name])
    assert code == cli.EXIT_DRILLDOWN_UNAVAILABLE
    capsys.readouterr()

    after = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(drill_dir.rglob("*")) if p.is_file()
    }
    assert after == before


# --------------------------------------------------------------------------- #
# Language auto-detection
# --------------------------------------------------------------------------- #

def test_detect_default_language_zh_cn():
    assert locale.detect_default_language({"LANG": "zh_CN.UTF-8"}) == "zh-CN"


def test_detect_default_language_en_us():
    assert locale.detect_default_language({"LANG": "en_US.UTF-8"}) == "en"


def test_detect_default_language_unset():
    assert locale.detect_default_language({}) == "en"


def test_detect_default_language_c_and_posix():
    assert locale.detect_default_language({"LANG": "C"}) == "en"
    assert locale.detect_default_language({"LANG": "POSIX"}) == "en"


def test_detect_default_language_non_delivered_locale_falls_back_to_english():
    assert locale.detect_default_language({"LANG": "fr_FR.UTF-8"}) == "en"


def test_detect_default_language_precedence_lc_all_over_lang():
    env = {"LANG": "en_US.UTF-8", "LC_ALL": "zh_CN.UTF-8"}
    assert locale.detect_default_language(env) == "zh-CN"


def test_explicit_language_flag_overrides_detected_default(
        tmp_path, synthetic_repo, capsys, monkeypatch):
    from analysis_wrapper.lifecycle import RunState
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    code = main(["new-run", "--workspace", str(synthetic_repo.parent),
                 "--skill-root", str(tmp_path / "skill"), "--language", "en"])
    assert code == 0
    run_dir = capsys.readouterr().out.splitlines()[0].split("run: ", 1)[1]
    assert RunState.load(run_dir).language == "en"  # explicit flag wins over the zh-CN-detecting locale


# --------------------------------------------------------------------------- #
# HTML export is opt-in
# --------------------------------------------------------------------------- #

def test_completed_overview_produces_no_export_until_requested(tmp_path):
    run = make_run(tmp_path)
    skill_root = tmp_path / "skill"
    exported_root = skill_root / "exported"
    # Nothing about completing an overview run (make_run already marks the
    # `overview` stage "done") writes into exported/ on its own.
    assert not exported_root.exists()


def test_export_command_still_works_standalone(tmp_path):
    from analysis_wrapper import export as export_pkg
    run = make_run(tmp_path)
    skill_root = tmp_path / "skill"
    result = export_pkg.export(run, "html", data_root=skill_root)
    assert (result.out_dir / "index.html").is_file()
