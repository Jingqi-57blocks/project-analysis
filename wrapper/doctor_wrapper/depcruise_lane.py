"""Dependency-cruiser lane — the doctor-owned JS/TS coupling-graph signal.

Split out of registry so the alias-resolution + config-generation machinery
(TS targets only) lives with the tool it configures, and registry stays lean.
The binary is the pinned doctor env (``node_env``), never a global depcruise and
never one resolved from a target.

Resolution strategy (item 2): for a TS target the prepare step runs the Node
helper (official TypeScript compiler API + static vite-alias AST extraction) and
writes a doctor-owned depcruise config UNDER THE RUN OUTPUT DIR that wires the
resolved aliases into a generated tsconfig. ``argv`` then switches to
``--config <that file>``. Without a prepared config (plain JS, or the resolver
was unavailable) ``argv`` keeps the safe ``--no-config`` scan, and the >15%
internal-unresolved degrader still reports partial coverage.
"""

from __future__ import annotations

from pathlib import Path

from . import node_env, parsers
from .exclusions import NODE_ENV_REMOVALS, _excluded_dirs, _js_exclude_re
from .resolvers import ts_aliases
from .targetspec import RepoTarget
from .tooldefs import PrepareResult, ToolDef

_TS_STACKS = {"ts", "tsx", "typescript"}


def _is_ts_target(target: RepoTarget) -> bool:
    if {s.lower() for s in target.stacks} & _TS_STACKS:
        return True
    root = Path(target.path)
    return any((root / n).is_file() for n in ("tsconfig.json", "tsconfig.app.json"))


def _ts_support_guard(target: RepoTarget) -> str:
    """A TS target needs the env to resolve ``.tsx``. If it cannot (env absent or
    no TypeScript), the dependency signal is UNAVAILABLE (fail-closed) rather than
    a misleadingly under-resolved graph. Plain-JS targets proceed without TS."""
    if not _is_ts_target(target):
        return ""
    info = node_env.probe()
    if not info.available:
        return f"dependency signal unavailable: {info.reason}"
    if not (info.supports_ts and info.supports_tsx):
        return ("dependency signal unavailable: doctor node_tools env lacks "
                "TypeScript/.tsx support (fail-closed for a TS target)")
    return ""


def dependency_cruiser(target: RepoTarget) -> ToolDef:
    binary = str(node_env.expected_depcruise_binary())
    root = Path(target.path)
    tsconfig = ""
    for name in ("tsconfig.app.json", "tsconfig.json"):
        if (root / name).is_file():
            tsconfig = name
            break
    # Analysis roots come from TargetSpec (discovery). Only when discovery
    # derived none do we fall back to a src/ heuristic — and we disclose it.
    notes = ""
    if target.analysis_roots:
        bases = list(target.analysis_roots)
    elif tsconfig and (root / "src").is_dir():
        bases = ["src"]
        notes = ("analysis_roots not derived; heuristic fallback scanned src/ only — "
                 "other source dirs are NOT SCANNED")
    else:
        bases = ["."]
    if tsconfig:
        # Glob mode: depcruise directory scans silently drop .tsx (Phase 0 F11).
        sources = [f"{b}/**/*.{ext}" if b != "." else f"**/*.{ext}"
                   for b in bases for ext in ("ts", "tsx", "js", "jsx")]
    else:
        sources = bases
    exclude_re = _js_exclude_re(target)
    reads = ["package.json"] + ([tsconfig] if tsconfig else [])

    # prepare (TS targets) fills this cell; argv reads it.
    prepared: dict[str, str] = {"config": ""}

    def _prepare(t: RepoTarget, out: Path) -> PrepareResult:
        if not tsconfig or not _is_ts_target(t):
            return PrepareResult()
        result = ts_aliases.resolve_and_generate(
            t, out, tsconfig=tsconfig, exclude_re=exclude_re)
        prepared["config"] = str(result.config_path) if result.config_path else ""
        return PrepareResult(notes=result.notes, reads=result.reads)

    def _argv(_t: RepoTarget) -> list[str]:
        config = prepared["config"]
        if config:
            return [binary, "--config", config, "--output-type", "json", *sources]
        extra = ["--ts-config", tsconfig] if tsconfig else []
        return [binary, "--no-config", *extra, "--do-not-follow", "node_modules",
                "--exclude", exclude_re, "--output-type", "json", *sources]

    def _annotate(t: RepoTarget, stdout: str, _stderr: str) -> str:
        return parsers.depcruise_resolution_note(stdout)

    return ToolDef(
        name="dependency-cruiser", binary=binary, validated_version="18.1.0",
        version_argv=[binary, "--version"], normal_exits=frozenset({0}),
        argv_builder=_argv,
        output_validator=parsers.validate_depcruise,
        degraders=[parsers.depcruise_degraded], view_builder=parsers.depcruise_view,
        view_lines=260, reads_declared=reads,
        applied_exclusions=_excluded_dirs(target),
        cwd_mode="target", timeout_s=300,
        remove_env=NODE_ENV_REMOVALS,
        guards=[_ts_support_guard],
        prepare=_prepare, annotate=_annotate,
        extra_notes="; ".join(x for x in (
            "coupling graph via the doctor-owned pinned depcruise+typescript env "
            "(node_tools) — never a global or target-resolved binary",
            notes) if x),
    )
