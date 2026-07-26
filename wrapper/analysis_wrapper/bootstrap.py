"""Create an isolated virtual environment for the Project Analysis wrapper.

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

from . import paths

WRAPPER_ROOT = Path(__file__).resolve().parents[1]


def default_venv() -> Path:
    """The venv's default location: GENERATED RUNTIME, not code — it lives
    under the data root (rebuilt fresh there, never inside the checkout) so a
    skill upgrade/reinstall never disturbs an already-bootstrapped environment.

    Deliberately a function, not a module-level constant: ``paths.venv_dir()``
    resolves (and validates) the data root, which can raise ``ValueError`` on a
    misconfigured machine. Evaluating it at import time would make merely
    importing this module fail — including ``--help`` and this module's own
    friendly error handling. Call this only from inside ``parser()`` /
    ``main()``, never from a module-level expression.
    """
    return paths.venv_dir()


def environment_python(environment: Path) -> Path:
    """Return the interpreter path for a venv on this platform."""
    if sys.platform == "win32":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def editable_install_target(wrapper_root: Path, include_dev: bool) -> str:
    extras = "history,sql,report,dev" if include_dev else "history,sql,report"
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
        raise RuntimeError("Project Analysis requires Python 3.11 or newer")

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
        description="install Project Analysis Python dependencies into an isolated venv"
    )
    # ``default=None`` deliberately, not ``default_venv()``: argparse resolves
    # `--help` (and exits) INSIDE ``parse_args()``, before any code after this
    # call runs — but building the parser itself still runs unconditionally.
    # Computing the real default here would call ``paths.venv_dir()`` (which
    # can raise on a misconfigured machine) merely to print `--help`, defeating
    # the whole point of making this lazy. ``main()`` resolves the real default
    # only once it actually needs it (i.e. only when NOT just printing help).
    result.add_argument(
        "--venv",
        type=Path,
        default=None,
        help="virtual environment path (default: <data-root>/runtime/"
             f"{paths.RUNTIME_CONTRACT}/venv, resolved via $PROJECT_ANALYSIS_HOME "
             "— see paths.py)",
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
        # ``default_venv()`` (only reached when --venv was not given) can raise
        # ValueError on a misconfigured data root — folded into the same
        # friendly-error path as every other bootstrap failure, never a raw
        # traceback.
        venv = args.venv if args.venv is not None else default_venv()
        python = bootstrap(venv, include_dev=args.dev)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        return 1

    executable = python.parent / (
        "project-analysis-wrapper.exe" if sys.platform == "win32"
        else "project-analysis-wrapper"
    )
    print(f"virtual environment: {python.parent.parent}")
    print(f"wrapper: {executable}")

    # External runtimes and analysis tools are intentionally never installed by
    # bootstrap. Report their absence; the README leaves installation and version
    # management to the developer.
    from . import astgrep
    if astgrep.available():
        print(f"ast-grep: {astgrep.binary()}")
    else:
        print("WARNING: ast-grep not found — see README.md for developer-managed "
              "prerequisites; route/HTTP/client structural rules degrade to regex "
              "or SKIP", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
