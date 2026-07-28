"""Safe tool execution + status classification (the executor, 57B-10 core).

Flow per signal:
  1. availability + allowlist (argv[0] MUST be the approved binary) + guards —
     a refusal with no fallback => SKIPPED manifest, tool never invoked;
  2. TargetSpec staleness check (recorded HEAD vs live HEAD) + pre-run
     git-visible snapshot; an unavailable snapshot on a git target = FAILED
     (fail-closed — never assume clean);
  3. subprocess with explicit argv/cwd/env and a per-tool timeout — never a
     shell string, never target-owned config;
  4. classification: native exit semantics -> stderr network/auth signatures ->
     output-shape validation -> tooldef-specific degraders (partial);
  5. post-run snapshot compare — ANY git-visible delta forces FAILED loudly;
  6. outputs: RAW stdout/stderr under a self-gitignoring raw/ containment zone,
     plus a SANITIZED BOUNDED VIEW (the only artifact agents may read);
  7. manifest written for every attempt, including skipped ones.

The executor invokes, classifies, records, redacts, bounds. It never interprets
findings (plan §2.7).
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import gitinfo
from .manifest import Manifest, RepoStamp
from .sanitize import bound
from .status import Status
from .targetspec import RepoTarget
from .tooldefs import PrepareResult, ToolDef, approved_argv0

if TYPE_CHECKING:
    from .identity import RepositoryIdentity

_NET_ERR = re.compile(
    r"ENOTFOUND|ETIMEDOUT|EAI_AGAIN|ECONNREFUSED|ENETUNREACH|"
    r"no such host|lookup [^\n]+:|request failed|dial tcp|connection refused|"
    r"network (?:error|failure|unreachable)|offline(?: mode| error)?"
    r"|\bE(?:4\d\d|5\d\d)\b|(^|[^0-9])(401|403|404|5\d\d)([^0-9]|$)",
    re.IGNORECASE,
)
class WrapperSafetyError(ValueError):
    """A SAFETY refusal (write-into-target, binary-inside-target, unsafe path)
    — distinct from ordinary bad input so callers can report and exit
    differently. Subclasses ValueError so existing handlers stay safe."""


@dataclass
class SignalResult:
    tool: str
    repo_id: str
    repository_ref: str
    status: Status
    reason: str
    manifest: Manifest
    raw_path: Path | None       # containment zone — never model-read, never shipped
    view_path: Path | None      # sanitized bounded view — the readable artifact
    manifest_path: Path | None = None


def _stamp(target: RepoTarget, repo_identity: RepositoryIdentity) -> RepoStamp:
    if repo_identity.internal_id != target.repo_id:
        raise ValueError("repository identity does not match execution target")
    return RepoStamp(
        repository_ref=repo_identity.reference,
        repo_path=target.path,
        repo_head=gitinfo.head(target.path),
        branch=gitinfo.branch(target.path),
        dirty_detail=gitinfo.dirty_detail(target.path),
        shallow=target.git.shallow,
        commit_count=target.git.commit_count,
        oldest_commit_date=target.git.oldest_commit_date,
    )


def _containment_dir(out: Path) -> Path:
    """raw/ is self-protecting: it always carries a `*` .gitignore, so even a
    misplaced output directory cannot leak raw content into version control."""
    raw_dir = out / "raw"
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise ValueError(f"output directory is not a real directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    if raw_dir.is_symlink() or (raw_dir.exists() and not raw_dir.is_dir()):
        raise WrapperSafetyError(f"raw containment path is unsafe: {raw_dir}")
    raw_dir.mkdir(exist_ok=True)
    raw_dir.chmod(0o700)
    keep = raw_dir / ".gitignore"
    # Always enforce the containment rule. An existing weaker file must not turn
    # raw output into a trackable artifact.
    if not keep.exists() or keep.read_text("utf-8", errors="replace") != "*\n":
        keep.write_text("*\n", "utf-8")
    return raw_dir


def _write_private(path: Path, text: str) -> None:
    """Create a raw artifact once with owner-only permissions."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def create_stage_dir(path: Path) -> Path:
    """mkdir a stage output dir (e.g. ``imports/``, ``callgraph/``), refusing a
    symlink or non-directory at the path first — a symlinked stage dir would
    redirect every file written under it into the link target, potentially a
    read-only analyzed repo. Returns the directory."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise WrapperSafetyError(
            "stage directory is a symlink or non-directory (containment risk): "
            f"{path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_new_text(path: Path, text: str) -> None:
    """Create a NEW run-dir artifact, refusing to follow a symlink or clobber.

    ``O_CREAT | O_EXCL`` never opens THROUGH a symlink (POSIX: it fails with
    EEXIST when the path is a symbolic link, even a dangling one), so a planted
    ``imports/<file> -> <path inside a target repo>`` link is refused, not
    followed — the read-only-target containment guarantee holds for every
    stage-emitted file. Default (umask) permissions, since these are normal
    shipped run-dir artifacts, not raw containment."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def replace_artifact_text(path: Path, text: str) -> None:
    """Atomically write a regenerable run artifact without following symlinks.

    Some deterministic projections are refreshed after module synthesis, so the
    create-once helper is too strict.  Refuse symlink/non-file destinations,
    write a sibling with O_EXCL, then atomically replace the regular file.
    """
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise WrapperSafetyError(f"artifact path is a symlink or non-file: {path}")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise WrapperSafetyError(f"artifact parent is unsafe: {path.parent}")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _assert_output_outside_targets(out: Path, targets: list[RepoTarget]) -> None:
    resolved = out.expanduser().resolve()
    for target in targets:
        root = Path(target.path).expanduser().resolve()
        if resolved == root or resolved.is_relative_to(root):
            raise WrapperSafetyError(
                f"output directory {resolved} is inside target {target.repo_id}; "
                "refusing to write into an analyzed repository"
            )


def prepare_output_directory(out_dir: str | Path, targets: list[RepoTarget]) -> Path:
    """Validate the whole run before its first write and create a fresh output.

    A run directory is immutable evidence. Refusing existing paths avoids stale
    artifacts, signal-name collisions, and symlink redirection.
    """
    if not targets:
        raise ValueError("TargetSpec contains no repositories")
    seen: set[Path] = set()
    for target in targets:
        root = Path(target.path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"target repository is not a directory: {target.path}")
        if root in seen:
            raise ValueError(f"multiple repo IDs resolve to the same target: {root}")
        seen.add(root)
    out = Path(out_dir).expanduser()
    _assert_output_outside_targets(out, targets)
    if out.is_symlink() or out.exists():
        raise ValueError(f"output directory already exists; choose a fresh path: {out}")
    out.mkdir(parents=True, exist_ok=False)
    return out.resolve()


def use_existing_run_directory(out_dir: str | Path, targets: list[RepoTarget], *,
                               stage_marker: str = "") -> Path:
    """Prepare a run dir for a post-discovery stage (callgraph / dependency-map).

    Unlike :func:`prepare_output_directory`, a post-discovery stage layers into an
    EXISTING run dir (discovery created it and wrote targets.json), so an existing
    directory is expected, not refused — but every safety check still holds
    (targets valid + distinct, output outside every target, no symlink
    redirection, a real directory). A non-empty ``stage_marker`` that already
    exists means THIS stage already ran; we refuse rather than clobber its
    immutable evidence.
    """
    if not targets:
        raise ValueError("TargetSpec contains no repositories")
    seen: set[Path] = set()
    for target in targets:
        root = Path(target.path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"target repository is not a directory: {target.path}")
        if root in seen:
            raise ValueError(f"multiple repo IDs resolve to the same target: {root}")
        seen.add(root)
    out = Path(out_dir).expanduser()
    _assert_output_outside_targets(out, targets)
    if out.is_symlink():
        raise WrapperSafetyError(f"output directory is a symlink: {out}")
    if out.exists() and not out.is_dir():
        raise ValueError(f"output path is not a directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    resolved = out.resolve()
    if stage_marker:
        # Guard the stage SUBDIR and the marker itself against symlink
        # redirection. A dangling symlink is invisible to a bare ``.exists()``
        # (False) yet ``write_text`` FOLLOWS it — so a planted
        # ``imports/depmap-coverage.json -> <target repo path>`` would write into
        # a read-only analyzed repo. Refuse a symlinked stage subdir (its writes
        # would be redirected) and a symlinked-or-existing marker.
        parts = Path(stage_marker).parts
        if len(parts) > 1:
            subdir = resolved / parts[0]
            if subdir.is_symlink() or (subdir.exists() and not subdir.is_dir()):
                raise WrapperSafetyError(
                    f"stage subdirectory is a symlink or non-directory "
                    f"(containment risk): {subdir}")
        marker = resolved / stage_marker
        if marker.is_symlink():
            raise WrapperSafetyError(
                f"stage marker is a symlink (containment risk): {marker}")
        if marker.exists():
            raise ValueError(
                "stage output already exists in this run dir (refusing to "
                f"clobber): {marker}")
    return resolved


def _assert_binary_outside_targets(binary: Path, targets: list[RepoTarget]) -> None:
    for target in targets:
        root = Path(target.path).expanduser().resolve()
        if binary == root or binary.is_relative_to(root):
            raise WrapperSafetyError(
                f"approved binary resolves inside target {target.repo_id}: {binary}"
            )


def _assert_path_outside_targets(env: dict[str, str], targets: list[RepoTarget]) -> None:
    for entry in env.get("PATH", "").split(os.pathsep):
        if not entry:
            raise WrapperSafetyError("PATH contains an empty current-directory entry")
        candidate = Path(entry).expanduser().resolve()
        for target in targets:
            root = Path(target.path).expanduser().resolve()
            if candidate == root or candidate.is_relative_to(root):
                raise WrapperSafetyError(
                    f"PATH contains target-controlled directory for {target.repo_id}: {candidate}"
                )


def _assert_signal_paths_available(out: Path, raw_dir: Path, name: str) -> None:
    if (not name or len(name.encode("utf-8")) > 255 or name in {".", ".."}
            or "/" in name or "\\" in name or Path(name).name != name
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise ValueError(f"unsafe signal id: {name!r}")
    paths = [
        raw_dir / f"{name}.out",
        raw_dir / f"{name}.err",
        out / f"{name}.view.txt",
        out / f"{name}.manifest.json",
        out / f"{name}.manifest.normalized.json",
        out / f"{name}.manifest.txt",
    ]
    collisions = [str(path) for path in paths if path.exists() or path.is_symlink()]
    if collisions:
        raise ValueError(
            "refusing to overwrite existing signal artifacts: " + ", ".join(collisions)
        )


def run_tool(
    tooldef: ToolDef,
    target: RepoTarget,
    out_dir: str | Path,
    scan_date: str,
    repository_identity: RepositoryIdentity,
    additional_targets: list[RepoTarget] | None = None,
    additional_repository_identities: list[RepositoryIdentity] | None = None,
    signal_id: str | None = None,
    allow_network: bool = False,
) -> SignalResult:
    out = Path(out_dir)
    targets = [target, *(additional_targets or [])]
    identities = [repository_identity, *(additional_repository_identities or [])]
    if len(identities) != len(targets):
        raise ValueError("every execution target requires a repository identity")
    for recorded_target, recorded_identity in zip(targets, identities):
        if recorded_target.repo_id != recorded_identity.internal_id:
            raise ValueError("repository identities do not match execution targets")
    identity_by_internal = {item.internal_id: item for item in identities}
    _assert_output_outside_targets(out, targets)
    raw_dir = _containment_dir(out)
    name = signal_id or f"{tooldef.name}-{repository_identity.artifact_key}"
    _assert_signal_paths_available(out, raw_dir, name)

    argv: list[str] | None = None
    cwd = tooldef.working_directory(target, out.resolve())
    version: str | None = None
    drift = ""
    prep = PrepareResult()
    structured_metrics: dict = {}

    def finish(status: Status, reason: str, *, exit_code=None, wall=None,
               raw: Path | None = None, view: Path | None = None,
               notes: str = "") -> SignalResult:
        outputs = [str(p) for p in (raw, view) if p]
        if raw:  # stderr sits next to stdout in containment
            outputs.append(str(raw.with_suffix(".err")))
        manifest = Manifest(
            tool=tooldef.name,
            tool_version=version or "(not installed)",
            argv=argv or [],
            cwd=str(cwd) if argv else "",
            env=dict(tooldef.env),
            repos=[_stamp(item, item_identity)
                   for item, item_identity in zip(targets, identities)],
            status=status.value,
            reason=reason,
            exit_code=exit_code,
            wall_time_s=wall,
            scope=tooldef.scope_description(target)
            + ("; repositories: " + ", ".join(
                item.reference for item in identities) if len(targets) > 1 else ""),
            exclusions=tooldef.exclusions_description(target),
            network=tooldef.network,
            scan_date=scan_date,
            output_files=outputs,
            declared_reads=list(dict.fromkeys(tooldef.declared_reads(target) + list(prep.reads))),
            version_drift=drift,
            notes="; ".join(x for x in (tooldef.extra_notes, prep.notes, notes) if x),
            structured_metrics=structured_metrics,
        )
        manifest_path, _ = manifest.write(out, name)
        return SignalResult(tooldef.name, target.repo_id,
                            repository_identity.reference, status, reason,
                            manifest, raw, view, manifest_path)

    # 1. authorization + guards BEFORE every invocation, including version probes.
    if tooldef.network and not allow_network:
        return finish(Status.SKIPPED, "network-capable tool requires explicit authorization")
    refusal = tooldef.check_guards(target)
    if refusal:
        return finish(Status.SKIPPED, f"guard refusal: {refusal}")

    resolved_binary = tooldef.resolved_binary()
    if resolved_binary is None:
        return finish(Status.SKIPPED, f"tool not installed: {tooldef.name}")
    try:
        _assert_binary_outside_targets(resolved_binary, targets)
        _assert_path_outside_targets(tooldef.merged_env(), targets)
    except ValueError as exc:
        return finish(Status.FAILED, str(exc))
    version = tooldef.probe_version(resolved_binary)
    if version is None:
        return finish(Status.FAILED, f"version probe failed: {tooldef.name}")
    if tooldef.validated_version and tooldef.validated_version not in version:
        drift = f"validated {tooldef.validated_version}, found {version}"
    preflight = tooldef.check_preflight()
    if preflight:
        return finish(Status.SKIPPED, f"preflight unavailable: {preflight}")

    # Per-run preparation (e.g. depcruise alias resolution → analyzer-owned config
    # written UNDER the output dir). Runs after authorization so it never touches
    # anything on a refused signal; a declined prepare fails closed (SKIPPED).
    if tooldef.prepare:
        try:
            prep = tooldef.run_prepare(
                target, out.resolve(), repository_identity.artifact_key)
        except Exception as exc:  # never let input generation crash the run
            return finish(Status.FAILED, f"prepare step failed: {type(exc).__name__}: {exc}")
        if not prep.ok:
            return finish(Status.SKIPPED, prep.reason or "prepare step declined")

    argv = tooldef.build_argv(target)
    if not approved_argv0(tooldef, argv, resolved_binary):
        got = argv[0] if argv else "(empty)"
        return finish(
            Status.FAILED,
            f"wrapper misconfiguration: argv[0]={got!r} is not the approved "
            f"binary {tooldef.binary!r} — refusing to execute",
        )

    # 2. staleness + pre-run snapshot (fail-closed on git targets) ---------------
    for item in targets:
        if not item.git.is_git:
            continue
        live_head = gitinfo.head(item.path)
        if live_head != item.git.head:
            return finish(
                Status.FAILED,
                f"TargetSpec stale for {identity_by_internal[item.repo_id].reference}: "
                f"recorded HEAD {item.git.head[:12]} but live "
                f"HEAD is {live_head[:12] or '(unavailable)'} — rerun discovery",
            )
        if not gitinfo.matches_recorded_dirty(item.path, item.git.dirty_detail):
            return finish(
                Status.FAILED,
                f"TargetSpec stale for {identity_by_internal[item.repo_id].reference}: "
                "dirty worktree state changed "
                "after discovery — "
                "rerun discovery",
            )
    pre = {item.repo_id: gitinfo.porcelain_snapshot(item.path) for item in targets}
    for item in targets:
        if item.git.is_git and pre[item.repo_id] is None:
            return finish(
                Status.FAILED,
                "git-visible snapshot unavailable on git target "
                f"{identity_by_internal[item.repo_id].reference} (fail-closed)",
            )

    # 3. invoke -------------------------------------------------------------------
    raw_path = raw_dir / f"{name}.out"
    err_path = raw_path.with_suffix(".err")
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), env=tooldef.merged_env(),
            capture_output=True, text=True, timeout=tooldef.timeout_s,
        )
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code = -1
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        timed_out = True
    except OSError as exc:
        return finish(Status.FAILED, f"spawn error: {exc}")
    wall = time.monotonic() - started
    _write_private(raw_path, stdout)
    _write_private(err_path, stderr)

    # 6 (early). sanitized bounded view — produced for every invoked run, even
    # failed ones (a failed view is still the safe thing to show a human).
    view_path = out / f"{name}.view.txt"
    view_error = ""
    try:
        view_text = tooldef.build_view(target, stdout, stderr)
        view_path.write_text(bound(view_text, tooldef.view_lines), "utf-8")
    except Exception as exc:  # parser/view failure reduces coverage; raw is retained
        view_error = f"bounded-view failure: {type(exc).__name__}: {exc}"
        view_path = None

    # 4. post-run immutability (git-visible; skipped for non-git targets) ---------
    for item in targets:
        if not item.git.is_git:
            continue
        post = gitinfo.porcelain_snapshot(item.path)
        if post is None or post != pre[item.repo_id]:
            return finish(
                Status.FAILED,
                "TARGET MUTATED: git-visible state changed during run (wrapper bug)"
                if post is not None else
                "git-visible snapshot unavailable after run (fail-closed)",
                exit_code=exit_code, wall=wall, raw=raw_path, view=view_path,
                notes=("pre/post porcelain delta on "
                       f"{identity_by_internal[item.repo_id].reference}"),
            )

    # 5. classification -----------------------------------------------------------
    if timed_out:
        return finish(Status.FAILED, f"timeout after {tooldef.timeout_s}s",
                      exit_code=None, wall=wall, raw=raw_path, view=view_path)
    # Only network-capable definitions may be reclassified this way, and only
    # diagnostics are scanned. Source/tool data containing words such as
    # "network" must never become a false execution failure.
    if tooldef.network and _NET_ERR.search(stderr or ""):
        return finish(Status.FAILED, "network/auth error during attempted run",
                      exit_code=exit_code, wall=wall, raw=raw_path, view=view_path)
    if exit_code not in tooldef.normal_exits:
        return finish(Status.FAILED, f"tool error exit {exit_code}",
                      exit_code=exit_code, wall=wall, raw=raw_path, view=view_path)

    shape_err = tooldef.validate_output(stdout, exit_code)
    if shape_err:
        return finish(Status.FAILED, f"malformed output: {shape_err}",
                      exit_code=exit_code, wall=wall, raw=raw_path, view=view_path)

    if view_error:
        return finish(Status.PARTIAL, view_error,
                      exit_code=exit_code, wall=wall, raw=raw_path, view=None)

    try:
        structured_metrics = tooldef.build_metrics(target, stdout, stderr)
    except Exception as exc:
        return finish(Status.PARTIAL,
                      f"structured-metrics failure: {type(exc).__name__}: {exc}",
                      exit_code=exit_code, wall=wall, raw=raw_path, view=view_path)

    # Post-run manifest annotation (metrics that only exist after the run, e.g.
    # depcruise edge-resolution ratios). Best-effort — never fails the run.
    try:
        annotation = tooldef.run_annotate(target, stdout, stderr)
    except Exception:
        annotation = ""

    degraded = tooldef.check_degraded(
        target, stdout + "\n### STDERR ###\n" + stderr, exit_code
    )
    if degraded:
        return finish(Status.PARTIAL, degraded,
                      exit_code=exit_code, wall=wall, raw=raw_path, view=view_path,
                      notes=annotation)

    nongit = [identity_by_internal[item.repo_id].reference
              for item in targets if not item.git.is_git]
    notes = ("non-git targets: immutability compare skipped (reduced-coverage mode): "
             + ", ".join(nongit)) if nongit else ""
    return finish(Status.COMPLETE, "", exit_code=exit_code, wall=wall,
                  raw=raw_path, view=view_path,
                  notes="; ".join(x for x in (notes, annotation) if x))
