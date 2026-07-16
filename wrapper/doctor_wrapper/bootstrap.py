"""Create an isolated virtual environment for the Project Doctor wrapper.

The host Python is used only for ``venv`` creation. The wrapper and every
Python dependency are installed by the virtual environment's own interpreter,
so bootstrap never writes packages into the host/global Python environment.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import venv
from pathlib import Path
from typing import Callable, Sequence


WRAPPER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENV = WRAPPER_ROOT / ".venv"


def environment_python(environment: Path) -> Path:
    """Return the interpreter path for a venv on this platform."""
    if sys.platform == "win32":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def editable_install_target(wrapper_root: Path, include_dev: bool) -> str:
    extras = "history,dev" if include_dev else "history"
    return f"{wrapper_root.resolve()}[{extras}]"


def bootstrap(
    environment: Path,
    *,
    include_dev: bool = False,
    wrapper_root: Path = WRAPPER_ROOT,
    create: Callable[[Path], None] | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    """Create/update the venv and return its Python interpreter."""
    if sys.version_info < (3, 11):
        raise RuntimeError("Project Doctor requires Python 3.11 or newer")

    environment = environment.expanduser().resolve()
    python = environment_python(environment)
    if not python.is_file():
        creator = create or (lambda path: venv.EnvBuilder(with_pip=True).create(path))
        creator(environment)
    if not python.is_file():
        raise RuntimeError(f"virtual environment did not create an interpreter at {python}")

    probe = run(
        [
            str(python), "-c",
            "import json,sys; print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix}))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        identity = json.loads(probe.stdout)
        prefix = Path(identity["prefix"]).expanduser().resolve()
        base = Path(identity["base"]).expanduser().resolve()
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not verify virtual-environment interpreter: {exc}") from exc
    if prefix != environment or prefix == base:
        raise RuntimeError(
            f"interpreter at {python} is not isolated in requested environment {environment}"
        )

    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-e",
            editable_install_target(wrapper_root, include_dev),
        ],
        check=True,
    )
    return python


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="install Project Doctor Python dependencies into an isolated venv"
    )
    result.add_argument(
        "--venv",
        type=Path,
        default=DEFAULT_VENV,
        help=f"virtual environment path (default: {DEFAULT_VENV})",
    )
    result.add_argument(
        "--dev",
        action="store_true",
        help="also install test dependencies",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        python = bootstrap(args.venv, include_dev=args.dev)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        return 1

    executable = python.parent / (
        "project-doctor-wrapper.exe" if sys.platform == "win32"
        else "project-doctor-wrapper"
    )
    print(f"virtual environment: {python.parent.parent}")
    print(f"wrapper: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
