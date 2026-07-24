"""Portability foundation (57B-93): the wrapper self-locates, and the pre-venv
launcher runs by absolute path from any working directory with no environment set."""
import os
import subprocess
import sys
from pathlib import Path

from analysis_wrapper import paths

SKILL_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = SKILL_ROOT / "bin" / "project-analysis"


def test_paths_resolve_to_skill_root():
    assert paths.wrapper_root() == SKILL_ROOT / "wrapper"
    assert paths.skill_root() == SKILL_ROOT
    assert (paths.skill_root() / "SKILL.md").is_file()
    assert (paths.skill_root() / "VERSION").is_file()


def test_launcher_runs_from_foreign_cwd_without_env(tmp_path):
    assert LAUNCHER.is_file(), "pre-venv launcher missing"
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_SKILL_DIR"}
    # Absolute path, unrelated cwd, no CLAUDE_SKILL_DIR: must still work.
    proc = subprocess.run(
        [sys.executable, str(LAUNCHER), "--version"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Project Analysis skill" in proc.stdout


def test_launcher_path_has_no_spaces_assumption(tmp_path):
    # Copy the launcher under a path containing a space; it must still resolve
    # and run (guards against unquoted-path regressions).
    spaced = tmp_path / "a b" / "skill"
    (spaced / "bin").mkdir(parents=True)
    # Symlink the real skill tree pieces the launcher needs.
    (spaced / "wrapper").symlink_to(SKILL_ROOT / "wrapper")
    (spaced / "bin" / "project-analysis").symlink_to(LAUNCHER)
    proc = subprocess.run(
        [sys.executable, str(spaced / "bin" / "project-analysis"), "--version"],
        cwd=str(tmp_path), env={k: v for k, v in os.environ.items()
                                if k != "CLAUDE_SKILL_DIR"},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Project Analysis skill" in proc.stdout
