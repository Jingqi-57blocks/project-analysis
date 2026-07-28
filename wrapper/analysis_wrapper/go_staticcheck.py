"""Repository-local invocation planning for the offline staticcheck lane.

The Go toolchain resolves ``./...`` from its current module, not from an
arbitrary wrapper process directory.  This module finds that module from the
recorded target/analysis roots without executing target-owned code or changing
the target tree, then returns a logical, reproducible execution identity for
the signal manifest.
"""

from __future__ import annotations

from pathlib import Path

from .exclusions import is_excluded_relative
from .targetspec import RepoTarget
from .tooldefs import InvocationPlan


def _relative(repo: Path, path: Path) -> str:
    value = path.relative_to(repo).as_posix()
    return "." if value == "." else value


def _module_roots(target: RepoTarget) -> list[Path]:
    """Detect module roots that can govern the declared analysis roots."""
    repo = Path(target.path).expanduser().resolve()
    found: set[Path] = set()
    for analysis_root in target.root_paths():
        current = analysis_root
        while current == repo or current.is_relative_to(repo):
            if (current / "go.mod").is_file():
                found.add(current)
                break  # closest governing module for this analysis root
            if current == repo:
                break
            current = current.parent
        # An analysis root may itself contain a nested module even when its
        # own directory has no go.mod.  Discover it deterministically, with
        # exactly the wrapper's normal exclusion policy.
        for go_mod in sorted(analysis_root.rglob("go.mod")):
            try:
                relative = go_mod.relative_to(repo).as_posix()
            except ValueError:
                continue
            if not is_excluded_relative(target, relative):
                found.add(go_mod.parent.resolve())
    return sorted(found, key=lambda path: path.as_posix())


def _patterns(module_root: Path, analysis_roots: list[Path]) -> list[str]:
    patterns: list[str] = []
    for analysis_root in analysis_roots:
        if analysis_root == module_root or module_root.is_relative_to(analysis_root):
            pattern = "./..."
        elif analysis_root.is_relative_to(module_root):
            relative = analysis_root.relative_to(module_root).as_posix()
            pattern = f"./{relative}/..."
        else:
            continue
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns or ["./..."]


def invocation(target: RepoTarget, binary: str) -> InvocationPlan:
    """Plan a single-module staticcheck invocation for one repository.

    Multiple independent module roots are deliberately classified instead of
    picking one accidentally.  The executor returns that condition as PARTIAL,
    so no lens can turn it into an absence claim.
    """
    repo = Path(target.path).expanduser().resolve()
    analysis_roots = target.root_paths()
    roots = _module_roots(target)
    logical_analysis_roots = [_relative(repo, root) for root in analysis_roots]
    go_work = (repo / "go.work").is_file()
    if not roots:
        identity = {
            "schema": "go-staticcheck-invocation-v1",
            "cwd": "repo",
            "module_root": "",
            "package_patterns": [],
            "analysis_roots": logical_analysis_roots,
            "workspace_mode": "off",
            "go_work_present": go_work,
            "layout": "no-module-detected",
        }
        return InvocationPlan(
            argv=[binary], cwd=repo, manifest_cwd="repo", identity=identity,
            reads=["go.work"] if go_work else [],
            reason="staticcheck-no-go-module-detected: supported Go target has no module under its analysis roots",
        )
    if len(roots) != 1:
        logical_roots = [_relative(repo, root) for root in roots]
        identity = {
            "schema": "go-staticcheck-invocation-v1",
            "cwd": "repo",
            "module_root": "",
            "package_patterns": [],
            "analysis_roots": logical_analysis_roots,
            "workspace_mode": "off",
            "go_work_present": go_work,
            "layout": "multiple-modules",
            "detected_module_roots": logical_roots,
        }
        reads = [f"{root}/go.mod" if root != "." else "go.mod" for root in logical_roots]
        if go_work:
            reads.append("go.work")
        return InvocationPlan(
            argv=[binary], cwd=repo, manifest_cwd="repo", identity=identity, reads=reads,
            reason="staticcheck-unsupported-multiple-modules: target has more than one module root",
        )

    module_root = roots[0]
    module_relative = _relative(repo, module_root)
    patterns = _patterns(module_root, analysis_roots)
    logical_cwd = "repo" if module_relative == "." else f"module:{module_relative}"
    identity = {
        "schema": "go-staticcheck-invocation-v1",
        "cwd": logical_cwd,
        "module_root": module_relative,
        "package_patterns": patterns,
        "analysis_roots": logical_analysis_roots,
        "workspace_mode": "off",
        "go_work_present": go_work,
        "layout": "single-module",
    }
    reads = ["go.mod" if module_relative == "." else f"{module_relative}/go.mod"]
    if go_work:
        reads.append("go.work")
    return InvocationPlan(
        argv=[binary, *patterns], cwd=module_root, manifest_cwd=logical_cwd,
        identity=identity, reads=reads)
