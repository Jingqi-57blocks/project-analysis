"""Consent-gated provisioning ("setup") for analyzer-managed tooling (57B-92).

Stdlib-only by design, like ``doctor.py``: this module is reachable through
``bin/project-analysis`` before the wrapper's own venv exists, so it must
import and run on the HOST interpreter. Never import a third-party package
here (``bootstrap``/``go_tools`` are imported lazily, at call time, for the
same reason they are lazy elsewhere in this package).

THE GOVERNING PRINCIPLE (non-negotiable): never install anything until the
user knows and chooses. The default flow is plan -> consent -> install.
Nothing is ever fetched or written silently, including analyzer-owned
pieces. ``--yes`` means prior authorization, not silent authorization: the
plan is still printed before anything is touched.

Ownership boundary
-------------------
``setup`` installs ONLY ``ownership: analyzer-managed`` tools from
``tools/manifest.json``:

- the Python venv (+ extras ``history,sql,report``; ``--dev`` adds ``dev``),
- the analyzer-owned ``node_tools`` packages (dependency-cruiser + typescript;
  JS/TS lane only),
- the Go ``callgraph`` binary (Go lane only).

It NEVER installs a ``developer-managed`` tool or a language runtime (python,
node, pnpm, go, scc, lizard, jscpd, ast-grep, staticcheck, osv-scanner). When a
lane's developer-managed prerequisite is absent, that lane is reported and
skipped with a clear reason -- never attempted, never a reason to fail the
whole command.

Three analyzer-managed groups, keyed by lane
---------------------------------------------
- ``core`` (always in scope, regardless of ``--lane``): analysis-wrapper +
  its history/sql/report extras (pydriller, sqlglot, markdown-it-py), all
  installed together by one editable pip install (``bootstrap.bootstrap``).
  Prerequisite: python (already required for anything to run at all).
- ``js``: dependency-cruiser + typescript, installed into the runtime
  ``node_tools`` directory exactly per ``wrapper/README.md``'s manual
  procedure (copy the two tracked manifests, then ``pnpm install --dir``).
  Prerequisite: node + pnpm.
- ``go``: the pinned ``callgraph`` binary, installed via ``go install`` into
  an analyzer-owned GOBIN. Prerequisite: go.

Exit codes (documented; 2/3/4 keep doctor's meanings)
------------------------------------------------------
0  EXIT_OK                       -- nothing left to do, or every lane already
                                    up to date / reduced-coverage lanes are
                                    disclosed (a normal, non-fatal outcome).
1  EXIT_INTERNAL_FAILURE         -- last-resort guard; never expected.
2  EXIT_INVALID_INVOCATION       -- bad --workspace, bad --lane, etc.
3  EXIT_RUNTIME_MISSING          -- ONLY when the caller explicitly named a
                                    single non-"all" --lane and that lane's
                                    developer-managed runtime is absent, so
                                    nothing at all could be done for the
                                    scope the caller asked for. The default
                                    (no --lane, or --lane all) NEVER returns
                                    this: a per-lane skip there is reduced
                                    coverage, exit 0, matching doctor's own
                                    "ready-reduced-coverage" precedent.
4  EXIT_INSTALLATION_CORRUPT     -- tools/manifest.json missing/malformed.
5  EXIT_CONSENT_DECLINED         -- at least one lane needed installing or
                                    reconciling and consent was withheld for
                                    it (and nothing else failed worse).
6  EXIT_CACHE_INCOMPATIBLE       -- an install destination exists but is not
                                    a directory (or otherwise cannot host the
                                    generated artifact) -- a corrupt/foreign
                                    object sits where the runtime tree
                                    belongs; never overwritten silently.
7  EXIT_PACKAGE_MANAGER_FAILED   -- pip/pnpm/go actually ran and reported a
                                    non-zero exit.
8  EXIT_DATA_ROOT_NOT_WRITABLE   -- the data root cannot be resolved or
                                    validated (see paths.validate_data_root).
9  EXIT_LOCK_HELD                -- another `setup` holds the exclusive lock.

Priority when several conditions apply at once: internal failure > lock held
> data-root-not-writable > installation-corrupt > invalid invocation >
cache-incompatible > package-manager-failed > consent-declined >
runtime-missing (explicit-lane only) > ok.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from . import doctor, paths

SCHEMA_VERSION = "1.0.0"

EXIT_OK = 0
EXIT_INTERNAL_FAILURE = 1
EXIT_INVALID_INVOCATION = 2
EXIT_RUNTIME_MISSING = 3
EXIT_INSTALLATION_CORRUPT = 4
EXIT_CONSENT_DECLINED = 5
EXIT_CACHE_INCOMPATIBLE = 6
EXIT_PACKAGE_MANAGER_FAILED = 7
EXIT_DATA_ROOT_NOT_WRITABLE = 8
EXIT_LOCK_HELD = 9

# A lock older than this is presumed abandoned (crashed holder, killed
# process) rather than genuinely still running, and may be reclaimed. This is
# a documented policy choice, not silent theft: reclaiming is logged, and a
# live, merely-slow holder is still protected because its lock file's mtime
# is refreshed only at acquisition time today -- a >6h single `setup` run
# would be unusual; if that ever changes, this constant is the one place to
# revisit.
LOCK_STALE_SECONDS = 6 * 60 * 60

_ALL_LANES = ("core", "js", "go")

# Manifest tool ids grouped by lane, plus the developer-managed prerequisite
# tool ids that must be present before this lane is even attempted, and the
# single network host each lane's install step contacts.
_LANE_GROUPS: dict[str, dict] = {
    "core": {
        "tools": ("analysis-wrapper", "pydriller", "sqlglot", "markdown-it-py"),
        "runtime_tools": ("python",),
        "network_host": "pypi.org",
    },
    "js": {
        "tools": ("dependency-cruiser", "typescript"),
        "runtime_tools": ("node", "pnpm"),
        "network_host": "registry.npmjs.org",
    },
    "go": {
        "tools": ("go-callgraph",),
        "runtime_tools": ("go",),
        "network_host": "proxy.golang.org",
    },
}


class PackageManagerError(RuntimeError):
    """pip/pnpm/go actually ran and reported failure."""


class CacheIncompatibleError(RuntimeError):
    """An install destination exists but cannot host the generated artifact."""


class LockHeld(RuntimeError):
    """Another ``setup`` process already holds the exclusive lock."""

    def __init__(self, holder: str):
        super().__init__(f"setup lock is held by {holder}")
        self.holder = holder


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

def _tool_row(report: dict, tool_id: str) -> dict | None:
    return next((t for t in report["tools"] if t["id"] == tool_id), None)


def _destination(lane: str) -> Path:
    """Pure path arithmetic (no mkdir) -- safe to call from ``--plan``."""
    if lane == "core":
        return paths.venv_dir()
    if lane == "js":
        return paths.node_tools_runtime()
    return paths.go_tools_bin()


def _commands(lane: str, *, include_dev: bool) -> list[str]:
    """Disclosure-only command strings, kept IDENTICAL to the manual
    procedure documented in ``wrapper/README.md`` -- ``setup`` automates
    exactly this, never a different mechanism."""
    if lane == "core":
        extras = "history,sql,report,dev" if include_dev else "history,sql,report"
        venv = paths.venv_dir()
        return [
            f"python3 -m venv {venv}  (skipped if already present)",
            f"{venv}/bin/pip install --disable-pip-version-check -e "
            f"<skill-root>/wrapper[{extras}]",
        ]
    if lane == "js":
        dest = paths.node_tools_runtime()
        return [
            f"cp wrapper/node_tools/package.json wrapper/node_tools/pnpm-lock.yaml {dest}/",
            f"pnpm install --dir {dest} --frozen-lockfile --ignore-scripts",
        ]
    from . import go_tools
    dest = paths.go_tools_bin()
    return [f"GOBIN={dest} go install "
            f"{go_tools.CALLGRAPH_PKG}@{go_tools.CALLGRAPH_VERSION}"]


def _resolve_lanes(requested: list[str] | None) -> tuple[frozenset[str], bool]:
    """Returns ``(active_lanes, explicit)``. ``core`` is always active --
    ``--lane`` only ever narrows the js/go scope. ``explicit`` is True only
    when the caller named a SINGLE non-"all" lane (the only case in which a
    missing developer runtime for that lane can turn into
    ``EXIT_RUNTIME_MISSING`` instead of a disclosed, exit-0 skip)."""
    if not requested or "all" in requested:
        return frozenset(_ALL_LANES), False
    chosen = {"core"} | {lane for lane in requested if lane in ("js", "go")}
    explicit = len(set(requested)) == 1 and set(requested) <= {"js", "go"}
    return frozenset(chosen), explicit


def compute_plan(workspace: str | Path | None, *,
                  active_lanes: frozenset[str] = frozenset(_ALL_LANES),
                  include_dev: bool = False) -> dict:
    """Compute the install plan. Read-only: reuses ``doctor.build_report``
    for tool state/classification/drift and never mkdirs or touches the
    network. Lanes not in ``active_lanes`` or not applicable to ``workspace``
    are omitted from ``items`` entirely (listed only in ``excluded``) -- a
    pure-JS target's plan never contains an actionable Go entry."""
    report = doctor.build_report(workspace)
    items: list[dict] = []
    excluded: list[dict] = []
    for lane in _ALL_LANES:
        group = _LANE_GROUPS[lane]
        rows = [r for r in (_tool_row(report, tid) for tid in group["tools"])
                if r is not None]
        if lane not in active_lanes:
            excluded.append({"lane": lane, "tools": list(group["tools"]),
                             "reason": "excluded by --lane filter"})
            continue
        applicable = any(r["classification"] != "not-applicable" for r in rows)
        if not applicable:
            excluded.append({"lane": lane, "tools": list(group["tools"]),
                             "reason": "not applicable to this target"})
            continue

        item = {
            "lane": lane,
            "tools": list(group["tools"]),
            "destination": str(_destination(lane)),
            "network_host": group["network_host"],
            "commands": _commands(lane, include_dev=include_dev),
        }
        missing_runtime = [t for t in group["runtime_tools"]
                           if (_tool_row(report, t) or {}).get("state") != "present"]
        if missing_runtime:
            item["status"] = "unavailable-missing-runtime"
            item["reason"] = (
                "developer-managed prerequisite(s) not found on PATH: "
                + ", ".join(missing_runtime)
                + " -- install manually (see README.md), then re-run setup")
            items.append(item)
            continue

        needs_install = any(r["state"] == "unavailable" for r in rows)
        drifted = [r["drift"] for r in rows if r["state"] == "present" and r["drift"]]
        if needs_install:
            item["status"] = "install"
            item["reason"] = ""
        elif drifted:
            item["status"] = "reconcile"
            item["reason"] = "; ".join(drifted)
        else:
            item["status"] = "up-to-date"
            item["reason"] = ""
        items.append(item)

    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": report["workspace"],
        "data_root": report["data_root"],
        "items": items,
        "excluded": excluded,
    }


def render_plan_human(plan: dict) -> str:
    lines = [f"Project Analysis setup plan (data root: {plan['data_root'] or '(unavailable)'})"]
    if plan["workspace"] is not None:
        lines.append(f"workspace: {plan['workspace']}")
    else:
        lines.append("workspace: (none given -- every lane considered in scope)")
    lines.append("")
    if not plan["items"]:
        lines.append("(nothing to plan -- every lane is excluded or not applicable)")
    for item in plan["items"]:
        lines.append(f"[{item['lane']}] {', '.join(item['tools'])}")
        lines.append(f"  status:      {item['status']}")
        lines.append(f"  destination: {item['destination']}")
        lines.append(f"  network:     {item['network_host']}")
        for cmd in item["commands"]:
            lines.append(f"  $ {cmd}")
        if item["reason"]:
            lines.append(f"  reason: {item['reason']}")
        lines.append("")
    for row in plan["excluded"]:
        lines.append(f"[{row['lane']}] excluded: {row['reason']}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Lock
# --------------------------------------------------------------------------

def _lock_path() -> Path:
    return paths.runtime_root() / ".setup.lock"


def _read_holder(lock_path: Path) -> str:
    try:
        return lock_path.read_text("utf-8").strip() or "(unknown)"
    except OSError:
        return "(unknown)"


def _lock_is_stale(lock_path: Path) -> bool:
    """A live same-host pid is NEVER stale, regardless of age -- pid liveness
    is checked FIRST and is authoritative whenever it can be determined. Age
    (``LOCK_STALE_SECONDS``) is only a fallback signal for payloads that
    cannot be attributed to a checkable pid (unparseable, or recorded against
    a different host)."""
    try:
        text = lock_path.read_text("utf-8").strip()
    except OSError:
        return True  # unreadable -- treat as abandoned rather than wedge forever
    pid = None
    host = None
    try:
        pid_host, _, _timestamp = text.partition(" ")
        pid_str, _, parsed_host = pid_host.partition("@")
        pid = int(pid_str)
        host = parsed_host
    except (ValueError, IndexError):
        pid = None
    if pid is not None and host == socket.gethostname():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True  # the recorded pid no longer exists on this host
        except PermissionError:
            return False  # exists, owned by someone else -- still live
        except OSError:
            return False
        return False  # live pid on THIS host -- never stale, no matter the age
    # Liveness cannot be determined (unparseable payload, or a different
    # host's pid namespace): fall back to age as the only available signal.
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return True
    return age > LOCK_STALE_SECONDS


# Bound the reclaim retry loop: an existing-but-unreadable, undeletable
# foreign lock must never wedge the process forever. After this many
# contended attempts, give up and report the lock as held.
_LOCK_ACQUIRE_MAX_ATTEMPTS = 200


def _ensure_runtime_dir(directory: Path) -> None:
    """Create ``directory`` (and any missing parents) at mode 0700, routing
    the data root's own creation through ``paths.data_root()`` -- its
    documented sole creator (57B-89 invariant). ``Path.mkdir(parents=True,
    mode=...)`` only applies ``mode`` to the final path component, not to
    intermediate directories it creates along the way, so each missing
    ancestor is created individually here to keep the whole generated-runtime
    tree at 0700 rather than leaking a default (typically 0755) mode."""
    paths.data_root()  # ensures the data root itself exists, at 0700
    missing = []
    probe = directory
    while not probe.exists():
        missing.append(probe)
        probe = probe.parent
    for made in reversed(missing):
        made.mkdir(mode=0o700, exist_ok=True)


@contextmanager
def _exclusive_lock(lock_path: Path):
    """Portable exclusive lock via ``O_CREAT | O_EXCL`` (atomic create-or-fail
    on every POSIX target this skill supports). Released in ``finally`` on
    every exit path, including when the guarded body raises.

    Reclaiming a stale lock never unlinks-then-creates (a contender could
    create a fresh lock in between, which we would then delete out from
    under it): instead the stale file is renamed aside first -- atomic on
    every POSIX target -- and only then do we loop back to attempt our own
    ``O_CREAT | O_EXCL`` create. A failed rename means someone else already
    won the race (reclaimed or replaced it first); we simply retry rather
    than treating that as an error.

    Release is ownership-checked: we only unlink the lock file if it still
    contains the exact payload we wrote at acquire time. If it doesn't
    (someone else's reclaim raced past us, however unlikely), we leave it
    alone rather than deleting a lock we no longer own.
    """
    _ensure_runtime_dir(lock_path.parent)
    payload = f"{os.getpid()}@{socket.gethostname()} {time.time()}\n"
    attempts = 0
    while True:
        attempts += 1
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if attempts > _LOCK_ACQUIRE_MAX_ATTEMPTS:
                raise LockHeld(_read_holder(lock_path))
            if _lock_is_stale(lock_path):
                stale_aside = lock_path.with_name(
                    lock_path.name + f".stale.{os.getpid()}.{attempts}")
                try:
                    lock_path.rename(stale_aside)
                except OSError:
                    continue  # someone else already reclaimed/replaced it -- retry
                try:
                    stale_aside.unlink()
                except OSError:
                    pass
                continue
            raise LockHeld(_read_holder(lock_path))
        else:
            try:
                os.write(fd, payload.encode("utf-8"))
            finally:
                os.close(fd)
            break
    try:
        yield
    finally:
        try:
            current = lock_path.read_text("utf-8")
        except OSError:
            current = None
        if current == payload:
            try:
                lock_path.unlink()
            except OSError:
                pass
        # else: the lock file no longer contains our payload -- someone else
        # owns it now (or it was cleaned up already); leave it alone.


# --------------------------------------------------------------------------
# Install actions (analyzer-managed only; each raises PackageManagerError /
# CacheIncompatibleError on failure, never installs a developer-managed tool
# or language runtime)
# --------------------------------------------------------------------------

def _install_core(*, include_dev: bool,
                  run: Callable[..., subprocess.CompletedProcess],
                  create: Callable[[Path], None] | None = None) -> None:
    from . import bootstrap
    venv = paths.venv_dir()
    if venv.exists() and not venv.is_dir():
        raise CacheIncompatibleError(f"{venv} exists and is not a directory")
    try:
        bootstrap.bootstrap(venv, include_dev=include_dev, create=create, run=run)
    except subprocess.CalledProcessError as exc:
        raise PackageManagerError(f"pip install failed: {exc}") from exc


def _install_node(*, run: Callable[..., subprocess.CompletedProcess]) -> None:
    dest = paths.node_tools_runtime()
    if dest.exists() and not dest.is_dir():
        raise CacheIncompatibleError(f"{dest} exists and is not a directory")
    dest.mkdir(parents=True, exist_ok=True)
    src = paths.wrapper_root() / "node_tools"
    shutil.copy2(src / "package.json", dest / "package.json")
    shutil.copy2(src / "pnpm-lock.yaml", dest / "pnpm-lock.yaml")
    result = run(["pnpm", "install", "--dir", str(dest),
                 "--frozen-lockfile", "--ignore-scripts"],
                capture_output=True, text=True)
    if result.returncode != 0:
        raise PackageManagerError(
            f"pnpm install failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout or '').strip()}")


def _install_go(*, run: Callable[..., subprocess.CompletedProcess]) -> None:
    from . import go_tools
    dest = paths.go_tools_bin()
    if dest.exists() and not dest.is_dir():
        raise CacheIncompatibleError(f"{dest} exists and is not a directory")
    dest.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GOBIN"] = str(dest)
    go_bin = shutil.which("go") or "go"
    result = run([go_bin, "install",
                 f"{go_tools.CALLGRAPH_PKG}@{go_tools.CALLGRAPH_VERSION}"],
                capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise PackageManagerError(
            f"go install failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout or '').strip()}")


_INSTALLERS: dict[str, Callable] = {
    "core": _install_core,
    "js": _install_node,
    "go": _install_go,
}


# --------------------------------------------------------------------------
# Consent
# --------------------------------------------------------------------------

def _default_confirm(item: dict) -> bool:
    """Interactive prompt when attached to a real TTY; otherwise declines by
    default -- non-interactive callers (scripts, agents, CI) must pass
    ``--yes`` for prior authorization, never get a silent install.

    The prompt is written to stderr, never stdout: ``input(prompt)`` would
    otherwise write it to stdout, interleaving with a final ``--json``
    ``print(json.dumps(...))`` and corrupting the machine-readable stream."""
    if not sys.stdin.isatty():
        return False
    prompt = (f"Install {', '.join(item['tools'])} for the {item['lane']} lane "
             f"(will contact {item['network_host']})? [y/N] ")
    try:
        print(prompt, end="", file=sys.stderr)
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

_ACTIONABLE_STATUSES = ("install", "reconcile")


def _execute(plan: dict, *, yes: bool, include_dev: bool,
            confirm: Callable[[dict], bool],
            run_cmd: Callable[..., subprocess.CompletedProcess],
            create_venv: Callable[[Path], None] | None) -> list[dict]:
    results: list[dict] = []
    for item in plan["items"]:
        result = dict(item)
        if item["status"] not in _ACTIONABLE_STATUSES:
            result["action_taken"] = "none"
            results.append(result)
            continue
        if not (yes or confirm(item)):
            result["action_taken"] = "skipped-consent-declined"
            results.append(result)
            continue
        try:
            if item["lane"] == "core":
                _install_core(include_dev=include_dev, run=run_cmd, create=create_venv)
            else:
                _INSTALLERS[item["lane"]](run=run_cmd)
            result["action_taken"] = "installed"
        except CacheIncompatibleError as exc:
            result["action_taken"] = "failed-cache-incompatible"
            result["error"] = str(exc)
        except PackageManagerError as exc:
            result["action_taken"] = "failed-package-manager"
            result["error"] = str(exc)
        except Exception as exc:  # pragma: no cover - defensive safety net;
            # an installer must never abort the whole command or leave the
            # lock held -- an unexpected error degrades to the same
            # documented failure bucket as a package-manager failure.
            result["action_taken"] = "failed-package-manager"
            result["error"] = f"unexpected error: {exc!r}"
        results.append(result)
    return results


def _outcome(results: list[dict], *, explicit_lane: bool) -> tuple[str, int]:
    if any(r["action_taken"] == "failed-cache-incompatible" for r in results):
        return "cache-incompatible", EXIT_CACHE_INCOMPATIBLE
    if any(r["action_taken"] == "failed-package-manager" for r in results):
        return "package-manager-failed", EXIT_PACKAGE_MANAGER_FAILED
    if any(r["action_taken"] == "skipped-consent-declined" for r in results):
        return "consent-declined", EXIT_CONSENT_DECLINED
    if explicit_lane and any(r["status"] == "unavailable-missing-runtime" for r in results):
        return "runtime-missing", EXIT_RUNTIME_MISSING
    if any(r["status"] == "unavailable-missing-runtime" for r in results):
        return "partial", EXIT_OK
    return "ok", EXIT_OK


def run(workspace: str | None, *,
       lanes: list[str] | None = None,
       yes: bool = False,
       dry_run: bool = False,
       include_dev: bool = False,
       as_json: bool = False,
       confirm: Callable[[dict], bool] | None = None,
       run_cmd: Callable[..., subprocess.CompletedProcess] = subprocess.run,
       create_venv: Callable[[Path], None] | None = None) -> int:
    """Implements the ``setup`` subcommand. Always returns one of the
    documented exit codes; never raises to its caller."""
    if not workspace:
        workspace = None
    candidate = Path(workspace).expanduser() if workspace is not None else None
    if candidate is not None and not candidate.is_dir():
        print(f"setup: --workspace {workspace!r} is not a directory", file=sys.stderr)
        return EXIT_INVALID_INVOCATION
    try:
        active_lanes, explicit_lane = _resolve_lanes(lanes)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"setup: invalid --lane: {exc!r}", file=sys.stderr)
        return EXIT_INVALID_INVOCATION

    # Fail closed, read-only, BEFORE any plan/install: the data root must
    # resolve, must not be inside the code tree, and must never be inside the
    # analyzed target (paths.validate_data_root's own checks) -- setup never
    # writes into the workspace it is asked to provision tooling for.
    try:
        root = paths.resolved_data_root()
        if candidate is not None:
            paths.validate_data_root(root, target=candidate)
    except ValueError as exc:
        print(f"setup: {exc}", file=sys.stderr)
        return EXIT_DATA_ROOT_NOT_WRITABLE

    try:
        plan = compute_plan(workspace, active_lanes=active_lanes, include_dev=include_dev)
    except doctor.ManifestError as exc:
        print(f"setup: installation looks corrupt -- {exc}", file=sys.stderr)
        return EXIT_INSTALLATION_CORRUPT
    except Exception as exc:  # pragma: no cover - last-resort guard
        print(f"setup: internal failure -- {exc!r}", file=sys.stderr)
        return EXIT_INTERNAL_FAILURE

    # The plan is ALWAYS shown before anything is touched -- --plan/--dry-run
    # AND --yes both still print it; --yes only skips the per-item prompt.
    # In --json mode the plan doc is written to STDERR (never stdout) so that
    # stdout stays a single clean parseable JSON document -- the apply result
    # printed at the end of this function. --plan/--dry-run is the one case
    # where the plan doc IS the (only) stdout output, printed below.
    plan_json = None
    if as_json:
        plan_json = json.dumps({**plan, "mode": "plan"}, indent=2, sort_keys=True)
        print(plan_json, file=sys.stderr)
    else:
        print(render_plan_human(plan), end="")

    if dry_run:
        if as_json:
            print(plan_json)
        return EXIT_OK

    confirm = confirm or _default_confirm
    lock_path = _lock_path()
    try:
        with _exclusive_lock(lock_path):
            results = _execute(
                plan, yes=yes, include_dev=include_dev, confirm=confirm,
                run_cmd=run_cmd, create_venv=create_venv)
    except LockHeld as exc:
        print(f"setup: {exc}", file=sys.stderr)
        return EXIT_LOCK_HELD
    except OSError as exc:
        # The lock lives under the generated-runtime tree, itself under the
        # data root: an OSError acquiring/writing it (permissions, ENOSPC,
        # ...) is a data-root-writability problem, not an invalid invocation
        # -- never let it escape uncaught to cli.main's generic OSError
        # handler, which would conflate it with exit 2.
        print(f"setup: could not acquire the setup lock -- {exc}", file=sys.stderr)
        return EXIT_DATA_ROOT_NOT_WRITABLE

    outcome, exit_code = _outcome(results, explicit_lane=explicit_lane)

    if as_json:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "workspace": plan["workspace"],
            "data_root": plan["data_root"],
            "mode": "apply",
            "items": results,
            "excluded": plan["excluded"],
            "outcome": outcome,
        }, indent=2, sort_keys=True))
    else:
        for result in results:
            suffix = f": {result.get('error')}" if result.get("error") else ""
            print(f"[{result['lane']}] {result['status']} -> "
                 f"{result['action_taken']}{suffix}")
        print(f"outcome: {outcome}")
    return exit_code


# --------------------------------------------------------------------------
# Entry point (argparse handler; wired from cli.py)
# --------------------------------------------------------------------------

def add_subparser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = sub.add_parser(
        "setup",
        help="consent-gated provisioning of analyzer-managed tooling (the "
            "Python venv + extras, the JS/TS node_tools packages, the Go "
            "callgraph binary). Never installs a developer-managed tool or "
            "language runtime; a missing runtime skips that lane with a "
            "disclosed reason rather than failing the command. Always "
            "shows the install plan before touching anything, even with "
            "--yes. Exit codes: 0 ok/partial (reduced coverage is a normal "
            "outcome), 2 invalid invocation, 3 runtime missing (only when "
            "--lane names a single lane whose runtime is absent), 4 "
            "installation corrupt, 5 consent declined, 6 cache/artifact "
            "incompatible, 7 package-manager failed, 8 data root not "
            "writable, 9 setup lock held, 1 internal failure.")
    parser.add_argument("--workspace", default="",
                        help="target workspace to sniff lane applicability for "
                             "(optional; without it every lane is in scope)")
    parser.add_argument(
        "--lane", action="append", choices=("js", "go", "all"), default=None,
        help="restrict the js/go scope (repeatable); default: all. `core` "
            "(the Python venv) is always in scope regardless of this flag")
    plan_group = parser.add_mutually_exclusive_group()
    plan_group.add_argument("--plan", dest="dry_run", action="store_true",
                            help="print the plan and exit without installing "
                                 "or touching the network")
    plan_group.add_argument("--dry-run", dest="dry_run", action="store_true",
                            help="alias for --plan")
    parser.add_argument("--yes", action="store_true",
                        help="prior authorization for every lane's install "
                             "(the plan is still printed first)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable structured output. With --plan/"
                            "--dry-run, stdout is the plan doc (mode: \"plan\"). "
                            "Otherwise the plan is still shown before anything "
                            "is touched -- written to STDERR as the same JSON "
                            "doc -- and stdout carries only the final apply "
                            "result (mode: \"apply\"), so stdout always stays "
                            "a single parseable document")
    parser.add_argument("--dev", action="store_true",
                        help="also install pytest into the venv (adds the "
                             "`dev` extra)")
    return parser


def main_from_args(args: argparse.Namespace) -> int:
    return run(
        args.workspace or None,
        lanes=args.lane,
        yes=args.yes,
        dry_run=args.dry_run,
        include_dev=args.dev,
        as_json=args.json,
    )
