import json
import subprocess
from pathlib import Path

import pytest

from analysis_wrapper.bootstrap import bootstrap, editable_install_target, environment_python


def test_install_target_keeps_runtime_and_dev_extras_explicit(tmp_path):
    root = tmp_path / "wrapper"
    assert editable_install_target(root, False) == f"{root.resolve()}[history,sql,report]"
    assert editable_install_target(root, True) == f"{root.resolve()}[history,sql,report,dev]"


def test_bootstrap_installs_with_only_the_venv_interpreter(tmp_path):
    environment = tmp_path / ".venv"
    wrapper_root = tmp_path / "wrapper"
    wrapper_root.mkdir()
    calls = []

    def create(path: Path) -> None:
        python = environment_python(path)
        python.parent.mkdir(parents=True)
        python.write_text("")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1] == "-c":
            return subprocess.CompletedProcess(
                argv, 0,
                stdout=json.dumps({"prefix": str(environment.resolve()), "base": "/host"}),
            )
        return subprocess.CompletedProcess(argv, 0)

    python = bootstrap(
        environment,
        include_dev=True,
        wrapper_root=wrapper_root,
        create=create,
        run=run,
    )

    assert python == environment_python(environment.resolve())
    assert calls[0][0][1] == "-c"
    assert calls[0][1] == {"check": True, "capture_output": True, "text": True}
    assert calls[1:] == [
        ([
            str(python), "-m", "pip", "install", "--disable-pip-version-check",
            "-e", f"{wrapper_root.resolve()}[history,sql,report,dev]",
        ], {"check": True})
    ]


def test_existing_environment_is_reused(tmp_path):
    environment = tmp_path / ".venv"
    python = environment_python(environment)
    python.parent.mkdir(parents=True)
    python.write_text("")

    created = []
    bootstrap(
        environment,
        wrapper_root=tmp_path,
        create=lambda path: created.append(path),
        run=lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 0,
            stdout=json.dumps({"prefix": str(environment.resolve()), "base": "/host"})
            if argv[1] == "-c" else "",
        ),
    )

    assert created == []


def test_existing_non_venv_interpreter_is_rejected_before_install(tmp_path):
    environment = tmp_path / ".venv"
    python = environment_python(environment)
    python.parent.mkdir(parents=True)
    python.write_text("")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0,
            stdout=json.dumps({"prefix": "/host", "base": "/host"}),
        )

    with pytest.raises(RuntimeError, match="not isolated"):
        bootstrap(environment, wrapper_root=tmp_path, run=run)
    assert len(calls) == 1 and calls[0][1] == "-c"
