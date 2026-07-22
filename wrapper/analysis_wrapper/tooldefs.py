"""Data-driven tool definitions (plain data + small hook functions).

A ToolDef is a dataclass, NOT a plugin system (plan §2.6 complexity guard):
the hooks are ordinary callables defined in this module or the tests. Real
tool definitions for the validated toolchain are added incrementally as their
parsers land; the executor is complete without them.

Guard/degrade semantics (status contract §17.3):
  guards   -> a non-empty refusal string means the tool is NEVER invoked
              (SKIPPED, unless a fallback tooldef is run instead — the caller
              decides fallbacks; one signal per attempt).
  degrade  -> a non-empty string demotes an otherwise-complete run to PARTIAL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .targetspec import RepoTarget

# Tier-1 universal exclusions — safe on ANY repo (plan / tools README §2).
TIER1_EXCLUSIONS = [
    "node_modules", "vendor", ".git", "dist", "build", "coverage",
    "*.min.js", "*.min.css",
]

GuardFn = Callable[[RepoTarget], str]                       # "" = pass
DegradeFn = Callable[[RepoTarget, str, int], str]           # "" = complete
ValidateFn = Callable[[str, int], str]                      # "" = shape ok
ArgvFn = Callable[[RepoTarget], list[str]]
PreflightFn = Callable[[], str]                             # "" = online/ready


@dataclass
class PrepareResult:
    """Outcome of a tool's per-run preparation step (e.g. depcruise's
    alias-resolution + config generation). ``ok=False`` makes the signal SKIPPED
    with ``reason``; otherwise ``notes``/``reads`` are folded into the manifest."""
    notes: str = ""
    reads: list[str] = field(default_factory=list)
    ok: bool = True
    reason: str = ""


PrepareFn = Callable[[RepoTarget, "Path"], PrepareResult]   # (target, out_dir)
AnnotateFn = Callable[[RepoTarget, str, str], str]          # (target, stdout, stderr) -> note
MetricsFn = Callable[[RepoTarget, str, str], dict]          # deterministic structured output


@dataclass
class ToolDef:
    name: str
    binary: str                             # executable probed on PATH
    argv_builder: ArgvFn
    normal_exits: frozenset[int] = frozenset({0})   # incl. findings exits (e.g. 1)
    timeout_s: int = 120
    network: bool = False
    env: dict[str, str] = field(default_factory=dict)  # explicit additions only
    remove_env: list[str] = field(default_factory=list)
    remove_env_prefixes: list[str] = field(default_factory=list)
    version_argv: list[str] | None = None   # default: [binary, "--version"]
    validated_version: str = ""             # from tools/README; drift disclosed
    guards: list[GuardFn] = field(default_factory=list)
    degraders: list[DegradeFn] = field(default_factory=list)
    output_validator: ValidateFn | None = None
    view_builder: Callable[[RepoTarget, str, str], str] | None = None  # (target, stdout, stderr)
    view_lines: int = 200
    reads_declared: list[str] = field(default_factory=list)  # target data files read
    applied_exclusions: list[str] = field(default_factory=list)
    cwd_mode: str = "target"                 # target | output
    preflight: PreflightFn | None = None
    prepare: PrepareFn | None = None         # per-run input generation (given the out dir)
    annotate: AnnotateFn | None = None       # post-run manifest note (metrics)
    metrics_builder: MetricsFn | None = None # full validated output -> structured metrics
    extra_notes: str = ""                    # standing disclosures for the manifest

    # ---- executor-facing API --------------------------------------------------

    def resolved_binary(self) -> Path | None:
        found = shutil.which(self.binary)
        return Path(found).resolve() if found else None

    def probe_version(self, resolved_binary: Path | None = None) -> str | None:
        """Tool version string, or None when not installed. Probes are guarded
        with the same env isolation as real invocations (corepack etc.)."""
        resolved = resolved_binary or self.resolved_binary()
        if resolved is None:
            return None
        argv = self.version_argv or [self.binary, "--version"]
        probe = _resolve_executable(argv[0])
        if probe is None or probe != resolved:
            return None
        try:
            out = subprocess.run(
                argv, capture_output=True, text=True, timeout=30,
                env=self.merged_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0:
            return None
        line = (out.stdout or out.stderr).strip().splitlines()
        return line[0] if line else "(unknown)"

    def merged_env(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in self.remove_env:
            env.pop(key, None)
        for prefix in self.remove_env_prefixes:
            for key in [name for name in env if name.startswith(prefix)]:
                env.pop(key, None)
        env.update(self.env)
        return env

    def build_argv(self, target: RepoTarget) -> list[str]:
        return self.argv_builder(target)

    def check_guards(self, target: RepoTarget) -> str:
        for g in self.guards:
            msg = g(target)
            if msg:
                return msg
        return ""

    def check_degraded(self, target: RepoTarget, stdout: str, exit_code: int) -> str:
        for d in self.degraders:
            msg = d(target, stdout, exit_code)
            if msg:
                return msg
        return ""

    def validate_output(self, stdout: str, exit_code: int) -> str:
        return self.output_validator(stdout, exit_code) if self.output_validator else ""

    def build_view(self, target: RepoTarget, stdout: str, stderr: str) -> str:
        """Parser-ordered view text; the executor sanitizes + bounds it.
        Default: stdout followed by a stderr tail marker when stderr exists."""
        if self.view_builder:
            return self.view_builder(target, stdout, stderr)
        if stderr.strip():
            return stdout + "\n### stderr ###\n" + stderr
        return stdout

    def declared_reads(self, target: RepoTarget) -> list[str]:
        return list(self.reads_declared)

    def check_preflight(self) -> str:
        return self.preflight() if self.preflight else ""

    def run_prepare(self, target: RepoTarget, out: Path) -> PrepareResult:
        return self.prepare(target, out) if self.prepare else PrepareResult()

    def run_annotate(self, target: RepoTarget, stdout: str, stderr: str) -> str:
        return self.annotate(target, stdout, stderr) if self.annotate else ""

    def build_metrics(self, target: RepoTarget, stdout: str, stderr: str) -> dict:
        return self.metrics_builder(target, stdout, stderr) if self.metrics_builder else {}

    def scope_description(self, target: RepoTarget) -> str:
        roots = ", ".join(target.analysis_roots) or "<repo root>"
        universe = "worktree" if target.git.dirty_detail != "no" else \
            "git-clean worktree (ignored files may still be present)"
        return f"roots: {roots}; source universe: {universe}; stacks: {','.join(target.stacks) or '?'}"

    def exclusions_description(self, target: RepoTarget) -> str:
        t2 = ", ".join(target.tier2_exclusions) or "(none derived)"
        applied = self.applied_exclusions or TIER1_EXCLUSIONS
        return f"applied: {', '.join(applied)}; tier2(derived): {t2}"


# --- shared guards -------------------------------------------------------------

def yarn_exec_vector_guard(target: RepoTarget) -> str:
    """Repo-owned yarn interpreters would EXECUTE target JavaScript (spike
    yarn_exec_vector, ported). Returns a refusal description or ''."""
    root = target.path
    vectors = []
    yrcyml = os.path.join(root, ".yarnrc.yml")
    if os.path.isfile(yrcyml):
        try:
            if "yarnPath" in open(yrcyml, encoding="utf-8", errors="replace").read():
                vectors.append(".yarnrc.yml yarnPath")
        except OSError:
            vectors.append(".yarnrc.yml unreadable (treated as vector)")
    if os.path.isdir(os.path.join(root, ".yarn", "releases")):
        vectors.append("vendored .yarn/releases")
    yrc = os.path.join(root, ".yarnrc")
    if os.path.isfile(yrc):
        try:
            if any(l.lstrip().startswith("yarn-path")
                   for l in open(yrc, encoding="utf-8", errors="replace")):
                vectors.append(".yarnrc yarn-path")
        except OSError:
            vectors.append(".yarnrc unreadable (treated as vector)")
    return f"yarn execution vector: {'; '.join(vectors)}" if vectors else ""


COREPACK_GUARD_ENV = {
    "COREPACK_ENABLE_AUTO_PIN": "0",
    "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
    "COREPACK_ENABLE_PROJECT_SPEC": "0",
    "COREPACK_ENABLE_NETWORK": "0",
    "COREPACK_DEFAULT_TO_LATEST": "0",
}


def _resolve_executable(value: str) -> Path | None:
    """Resolve an argv[0] exactly as subprocess would, following symlinks."""
    if os.sep in value or (os.altsep and os.altsep in value):
        p = Path(value).expanduser()
        return p.resolve() if p.exists() else None
    found = shutil.which(value)
    return Path(found).resolve() if found else None


def approved_argv0(tooldef: ToolDef, argv: list[str], resolved_binary: Path) -> bool:
    return bool(argv) and _resolve_executable(argv[0]) == resolved_binary
