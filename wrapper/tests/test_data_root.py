"""57B-89 Phase 2: the persistent-data root, generated-runtime paths, and the
legacy-layout migration shim.

``conftest.py``'s autouse ``_isolated_data_root`` fixture already points
``$PROJECT_ANALYSIS_HOME`` at a throwaway directory for every test in this
suite; tests below that need to exercise a DIFFERENT precedence tier
explicitly ``delenv``/``setenv`` around it. Nothing here writes into a real
target or a real machine data root.
"""

import os
import stat

import pytest

from analysis_wrapper import paths


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------

def test_project_analysis_home_override_wins(tmp_path, monkeypatch):
    override = tmp_path / "custom-home"
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(override))
    assert paths.data_root() == override.resolve()
    assert override.is_dir()


def test_project_analysis_home_is_expanduser_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", "~/via-tilde")
    assert paths.data_root() == tmp_path / "via-tilde"


def test_macos_default_when_no_override(tmp_path, monkeypatch):
    monkeypatch.delenv("PROJECT_ANALYSIS_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.data_root() == (
        tmp_path / "Library" / "Application Support" / "project-analysis")


def test_linux_uses_xdg_data_home_when_set(tmp_path, monkeypatch):
    monkeypatch.delenv("PROJECT_ANALYSIS_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    xdg = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    assert paths.data_root() == xdg / "project-analysis"


def test_linux_falls_back_to_local_share_without_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("PROJECT_ANALYSIS_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.data_root() == tmp_path / ".local" / "share" / "project-analysis"


def test_data_root_created_with_mode_0700(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(tmp_path / "fresh-home"))
    root = paths.data_root()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_data_root_leaves_existing_permissions_alone(tmp_path, monkeypatch):
    existing = tmp_path / "already-here"
    existing.mkdir(mode=0o755)
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(existing))
    paths.data_root()
    # 0755 survives untouched — data_root() only sets the mode it creates.
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755


# --------------------------------------------------------------------------
# validate_data_root rejections
# --------------------------------------------------------------------------

def test_validate_rejects_root_inside_skill_root():
    with pytest.raises(ValueError, match="code tree"):
        paths.validate_data_root(paths.skill_root() / "would-be-data")


def test_validate_rejects_root_equal_to_skill_root():
    with pytest.raises(ValueError, match="code tree"):
        paths.validate_data_root(paths.skill_root())


def test_validate_rejects_root_inside_target(tmp_path):
    target = tmp_path / "target-workspace"
    target.mkdir()
    candidate = target / "nested" / "data"
    with pytest.raises(ValueError, match="analysis target"):
        paths.validate_data_root(candidate, target=target)


def test_validate_rejects_root_equal_to_target(tmp_path):
    target = tmp_path / "target-workspace"
    target.mkdir()
    with pytest.raises(ValueError, match="analysis target"):
        paths.validate_data_root(target, target=target)


def test_validate_allows_safe_root(tmp_path):
    candidate = tmp_path / "safe-root"
    assert paths.validate_data_root(candidate) == candidate


def test_validate_allows_root_when_target_given_and_unrelated(tmp_path):
    target = tmp_path / "target-workspace"
    target.mkdir()
    candidate = tmp_path / "safe-root"
    assert paths.validate_data_root(candidate, target=target) == candidate


def test_validate_rejects_unwritable_root(tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        with pytest.raises(ValueError, match="not writable"):
            paths.validate_data_root(locked / "data")
    finally:
        locked.chmod(0o700)  # let tmp_path clean up afterwards


def test_validate_rejects_symlink_escape_into_code_root(tmp_path):
    link = tmp_path / "escape-link"
    link.symlink_to(paths.skill_root())
    with pytest.raises(ValueError, match="code tree"):
        paths.validate_data_root(link)


# --------------------------------------------------------------------------
# Derived paths: shape + laziness (no mkdir beyond data_root() itself)
# --------------------------------------------------------------------------

def test_output_state_exported_live_under_data_root():
    root = paths.data_root()
    assert paths.output_root() == root / "output"
    assert paths.state_root() == root / "state"
    assert paths.exported_root() == root / "exported"


def test_output_root_does_not_precreate_its_subdirectory():
    out = paths.output_root()
    assert paths.data_root().is_dir()
    assert not out.exists()  # left to the actual writer, same as before


def test_runtime_path_shape():
    assert paths.runtime_root() == (
        paths.data_root() / "runtime" / paths.RUNTIME_CONTRACT)
    assert paths.venv_dir() == paths.runtime_root() / "venv"
    assert paths.node_tools_runtime() == paths.runtime_root() / "node_tools"
    assert paths.go_tools_bin() == paths.runtime_root() / "go_tools" / "bin"


def test_changing_project_analysis_home_moves_every_derived_path(tmp_path, monkeypatch):
    """FIX 4 (57B-89 Phase 2 review): confirms conftest.py's autouse
    ``_isolated_data_root`` fixture genuinely isolates EVERY data/runtime path,
    not just ``data_root()`` itself — this only holds because node_env.py /
    go_tools.py / bootstrap.py no longer freeze these into import-time
    constants (FIX 3); a frozen constant would silently keep pointing at
    whatever ``$PROJECT_ANALYSIS_HOME`` was at import time."""
    from analysis_wrapper import bootstrap, go_tools, node_env

    first_home = tmp_path / "first-home"
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(first_home))
    first_venv = bootstrap.default_venv()
    first_node = node_env.default_node_tools_dir()
    first_go = go_tools.default_bin_dir()
    assert first_venv.is_relative_to(first_home)
    assert first_node.is_relative_to(first_home)
    assert first_go.is_relative_to(first_home)

    second_home = tmp_path / "second-home"
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(second_home))
    assert bootstrap.default_venv().is_relative_to(second_home)
    assert node_env.default_node_tools_dir().is_relative_to(second_home)
    assert go_tools.default_bin_dir().is_relative_to(second_home)


def test_runtime_helpers_never_create_directories():
    assert not paths.runtime_root().exists()
    assert not paths.venv_dir().exists()
    assert not paths.node_tools_runtime().exists()
    assert not paths.go_tools_bin().exists()


# --------------------------------------------------------------------------
# migrate_legacy
# --------------------------------------------------------------------------

def _populate(root, name):
    (root / name).mkdir(parents=True)
    (root / name / "marker.txt").write_text("hello")


def test_migrate_legacy_moves_each_subdir_and_is_idempotent(tmp_path):
    legacy = tmp_path / "legacy-skill-root"
    for name in ("output", "state", "exported"):
        _populate(legacy, name)
    # A legacy generated runtime must never be migrated (rebuilt fresh instead).
    (legacy / "wrapper" / ".venv").mkdir(parents=True)
    (legacy / "wrapper" / ".venv" / "sentinel").write_text("do not touch")

    report = paths.migrate_legacy(legacy)
    assert set(report["moved"]) == {"output", "state", "exported"}
    assert report["skipped_absent"] == []
    assert report["skipped_both_present"] == []
    assert report["warnings"] == []
    for name in ("output", "state", "exported"):
        assert not (legacy / name).exists()
        assert (paths.data_root() / name / "marker.txt").read_text() == "hello"
    assert (legacy / "wrapper" / ".venv" / "sentinel").read_text() == "do not touch"

    # Idempotent: nothing left to migrate the second time, no crash.
    second = paths.migrate_legacy(legacy)
    assert set(second["skipped_absent"]) == {"output", "state", "exported"}
    assert second["moved"] == []
    assert second["warnings"] == []


def test_migrate_legacy_keeps_data_root_copy_when_both_populated(tmp_path):
    legacy = tmp_path / "legacy-skill-root"
    _populate(legacy, "output")
    paths.output_root().mkdir(parents=True, exist_ok=True)
    (paths.output_root() / "existing.txt").write_text("already here")

    report = paths.migrate_legacy(legacy)
    assert report["skipped_both_present"] == ["output"]
    assert any("output" in warning for warning in report["warnings"])
    # Never merged: both sides are exactly as they were.
    assert (legacy / "output" / "marker.txt").read_text() == "hello"
    assert (paths.output_root() / "existing.txt").read_text() == "already here"
    assert not (paths.output_root() / "marker.txt").exists()


def test_migrate_legacy_survives_a_read_only_destination(tmp_path, monkeypatch):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    home = tmp_path / "ro-home"
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(home))
    paths.data_root()  # create it normally first (0700)...
    home.chmod(0o500)  # ...then simulate it going read-only
    legacy = tmp_path / "legacy-skill-root"
    _populate(legacy, "state")
    try:
        report = paths.migrate_legacy(legacy)
    finally:
        home.chmod(0o700)  # restore so tmp_path cleanup can proceed

    assert report["moved"] == []
    assert report["warnings"], "a read-only destination must be disclosed, never silent"
    # Nothing lost: the legacy copy is exactly as it was.
    assert (legacy / "state" / "marker.txt").read_text() == "hello"


def test_migrate_cli_returns_nonzero_when_data_root_unusable(tmp_path, monkeypatch):
    """FIX 8 (57B-89 Phase 2 review): the ``migrate`` CLI command must return a
    non-zero exit code when it could not prepare the data root, so scripted
    migration can detect failure instead of reading an unconditional 0."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    from analysis_wrapper.cli import main

    home = tmp_path / "ro-home"
    monkeypatch.delenv("PROJECT_ANALYSIS_HOME", raising=False)
    monkeypatch.setenv("PROJECT_ANALYSIS_HOME", str(home))
    paths.data_root()  # create it normally first...
    home.chmod(0o500)  # ...then simulate it going read-only
    legacy = tmp_path / "legacy-skill-root"
    legacy.mkdir()
    try:
        code = main(["migrate", "--legacy-skill-root", str(legacy)])
    finally:
        home.chmod(0o700)  # restore so tmp_path cleanup can proceed
    assert code != 0


def test_migrate_legacy_absent_legacy_dirs_are_just_skipped(tmp_path):
    legacy = tmp_path / "never-had-any-data"
    legacy.mkdir()
    report = paths.migrate_legacy(legacy)
    assert set(report["skipped_absent"]) == {"output", "state", "exported"}
    assert report["moved"] == report["skipped_both_present"] == report["warnings"] == []
