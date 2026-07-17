"""Production definitions for the Phase-0 validated toolchain.

Definitions are constructed only from TargetSpec fields. No project names or
business vocabulary occur here, and no target-owned executable configuration is
loaded.
"""

from __future__ import annotations

import os
import re
import shutil
import site
import socket
import subprocess
import sys
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from . import parsers
from .depcruise_lane import dependency_cruiser
from .exclusions import (
    NODE_ENV_REMOVALS, TIER1_DIRS, TIER1_FILE_GLOBS, _excluded_dirs,
    _jscpd_ignores,
)
from .targetspec import RepoTarget
from .tooldefs import COREPACK_GUARD_ENV, ToolDef, yarn_exec_vector_guard


PACKAGE_ENV_PREFIXES = ["NPM_CONFIG_", "npm_config_", "YARN_", "COREPACK_"]
# Remove only the vars our SAFE_GO_ENV pins (they are re-set explicitly).
# GOPRIVATE and friends stay untouched: with GOPROXY=off nothing is fetched,
# so they are inert but harmless, and scrubbing them would be undisclosed.
GO_ENV_REMOVALS = ["GOFLAGS", "GOTOOLCHAIN", "GOWORK"]
# OFFLINE-FIRST Go lane: GOPROXY=off means the run touches NO network host —
# no private module paths leaked to a public proxy, no undisclosed host-proxy
# destinations, deterministic warm-cache runs. A cold cache fails loudly with
# an actionable note; the operator warms the cache under their own approval.
SAFE_GO_ENV = {
    "GOFLAGS": "-mod=readonly",   # never rewrite go.mod/go.sum
    "GOTOOLCHAIN": "local",       # never auto-download another toolchain
    "GOWORK": "off",              # ignore workspace files outside the target
    "GOPROXY": "off",             # offline-first: zero network destinations
    "GOSUMDB": "off",             # no sumdb lookups (nothing is downloaded)
}
GO_ENV_NOTE = ("OFFLINE-FIRST: GOPROXY=off/GOSUMDB=off — no network destination is "
               "contacted; -mod=readonly, local toolchain, workspaces off. A cold "
               "module cache / missing dep / load failure fails LOUDLY (never a clean "
               "no-findings result): warm the cache under approval "
               "(`python3 -m analysis_wrapper.bootstrap --warm-go <repo>`) and rerun")

# Build settings the offline Go lane analyzes under, recorded in every manifest
# so the analyzed universe is explicit: the LOCAL toolchain's GOOS/GOARCH/CGO and
# NO extra build tags, so files excluded by build constraints are outside scope.
_GO_BUILD_CACHE: dict[str, dict[str, str]] = {}


def _go_build_settings(go_binary: str) -> dict[str, str]:
    if go_binary in _GO_BUILD_CACHE:
        return _GO_BUILD_CACHE[go_binary]
    settings: dict[str, str] = {}
    try:
        # Pins on ALL go invocations, `go env` included (offline, no side effects).
        out = subprocess.run([go_binary, "env", "GOOS", "GOARCH", "CGO_ENABLED"],
                             capture_output=True, text=True, timeout=30,
                             env={**os.environ, **SAFE_GO_ENV})
        if out.returncode == 0:
            for key, value in zip(("GOOS", "GOARCH", "CGO_ENABLED"), out.stdout.split()):
                if value:
                    settings[key] = value
    except (OSError, subprocess.TimeoutExpired):
        pass
    _GO_BUILD_CACHE[go_binary] = settings
    return settings


def _go_env(go_binary: str) -> dict[str, str]:
    """Offline Go env + the local build settings pinned (and thus recorded)."""
    return {**SAFE_GO_ENV, **_go_build_settings(go_binary)}


def _go_notes(go_binary: str) -> str:
    build = _go_build_settings(go_binary)
    settings = ", ".join(f"{k}={build[k]}" for k in ("GOOS", "GOARCH", "CGO_ENABLED")
                         if k in build) or "unavailable"
    return (f"{GO_ENV_NOTE}; build settings: {settings}, build tags: none (default) — "
            "code excluded by build constraints (GOOS/GOARCH///go:build) is outside "
            "the analyzed universe")


def _binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if name == "lizard":
        candidates = [Path(site.getuserbase()) / "bin" / "lizard"]
        candidates += [Path(p) / "lizard" for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return name


def _roots(target: RepoTarget) -> list[str]:
    return [str(p.resolve()) for p in target.root_paths()]


def _dns_preflight(host: str):
    def check() -> str:
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            return ""
        except OSError as exc:
            return f"offline/DNS for {host}: {exc}"
    return check


# Configuration-aware endpoint policy for the package network lane.
#
# Forced --registry + userconfig=devnull + env scrubbing do NOT neutralize the
# PROJECT-level .npmrc (npm has no ignore-project-config switch) and cannot
# override scoped registries (@scope:registry beats --registry). So the policy
# is: BENIGN project config keys proceed with a note; any key that can alter
# endpoints, auth, or TLS — or any dependency host outside the approved set —
# means the signal is SKIPPED, never silently contacted (review round: guards
# too broad / removal unsafe).

REGISTRY_HOSTS = {"registry.npmjs.org", "registry.yarnpkg.com"}
_BENIGN_NPMRC_KEYS = {
    "save-exact", "save-prefix", "engine-strict", "loglevel", "fund", "audit",
    "package-lock", "legacy-peer-deps", "progress", "update-notifier",
    "prefer-offline", "ignore-scripts", "color", "lockfile-version",
}


def _approved_hosts() -> set[str]:
    extra = os.environ.get("PROJECT_ANALYSIS_ALLOW_HOSTS", "")
    return REGISTRY_HOSTS | {h.strip().lower() for h in extra.split(",") if h.strip()}


def _pm_config_endpoint_guard(target: RepoTarget, *, manager: str) -> str:
    """Refuse only endpoint/auth/TLS-affecting keys in PROJECT-level PM config
    (which our neutralization cannot override). Benign keys pass."""
    root = Path(target.path)
    offending: list[str] = []
    npmrc = root / ".npmrc"
    if npmrc.is_file():
        for line in npmrc.read_text("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith((";", "#")):
                continue
            key = line.split("=", 1)[0].strip().lower()
            if key not in _BENIGN_NPMRC_KEYS:
                offending.append(f".npmrc:{key}")
    if manager == "yarn":
        yarnrc = root / ".yarnrc"
        if yarnrc.is_file():
            for line in yarnrc.read_text("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key = line.split(None, 1)[0].strip('"').lower()
                if key not in _BENIGN_NPMRC_KEYS:
                    offending.append(f".yarnrc:{key}")
    if offending:
        return ("project package-manager config can alter endpoints/auth and "
                "cannot be neutralized (scoped registries beat --registry): "
                + ", ".join(sorted(offending)[:5]))
    return ""


def _dependency_hosts(target: RepoTarget) -> set[str]:
    """Hosts the package manager MAY contact beyond the forced registry."""
    root = Path(target.path)
    hosts: set[str] = set()
    try:
        data = json.loads((root / "package.json").read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        for spec in (data.get(section) or {}).values():
            if not isinstance(spec, str):
                continue
            spec = spec.strip()
            low = spec.lower()
            if low.startswith(("github:",)) or re.fullmatch(r"[\w.-]+/[\w.-]+(#.*)?", spec):
                hosts.add("github.com")
            elif low.startswith(("git+", "git:", "ssh:", "http:", "https:")):
                host = urlparse(spec.split("+", 1)[-1]).hostname
                hosts.add(host or "(unparsed-url-dep)")
            elif low.startswith("git@"):
                hosts.add(spec.split("@", 1)[1].split(":", 1)[0].lower())
    lockfile = root / target.pm.lockfile if target.pm.lockfile else None
    if lockfile and lockfile.is_file():
        try:
            text = lockfile.read_text("utf-8", errors="replace")
        except OSError:
            text = ""
        for match in re.findall(r"https?://[^\s\"']+", text):
            host = urlparse(match).hostname
            if host:
                hosts.add(host.lower())
    return hosts - REGISTRY_HOSTS


def _dependency_host_guard(target: RepoTarget) -> str:
    """Unapproved dependency hosts => SKIPPED. Recording a host in a manifest
    does not prove the tool will not contact it — approval is explicit."""
    unapproved = sorted(_dependency_hosts(target) - _approved_hosts())
    if unapproved:
        return ("dependency hosts outside the approved endpoint set: "
                + ", ".join(unapproved[:5])
                + " — approve explicitly via --allow-hosts if intended")
    return ""


def _package_network_notes(target: RepoTarget, *, manager: str) -> str:
    """Benign disclosures for runs that DO proceed."""
    root = Path(target.path)
    notes: list[str] = []
    config_names = [".npmrc"] if manager == "npm" else [".yarnrc", ".yarnrc.yml", ".npmrc"]
    present = [name for name in config_names if (root / name).is_file()]
    if present:
        notes.append("project PM config present (benign keys only, endpoint keys "
                     "would have refused the run): " + ", ".join(present))
    approved_extra = sorted(_dependency_hosts(target) & (_approved_hosts() - REGISTRY_HOSTS))
    if approved_extra:
        notes.append("explicitly approved non-registry dependency hosts: "
                     + ", ".join(approved_extra[:5]))
    return "; ".join(notes)


def _language_args(target: RepoTarget) -> list[str]:
    aliases = {
        "go": "go", "js": "javascript", "javascript": "javascript",
        "ts": "typescript", "typescript": "typescript", "tsx": "tsx",
    }
    langs = sorted({aliases[x.lower()] for x in target.stacks if x.lower() in aliases})
    # TypeScript implies TSX: a React repo whose discovery emitted only "ts"
    # must not silently lose .tsx complexity coverage (review P3-13).
    if "typescript" in langs and "tsx" not in langs:
        langs = sorted({*langs, "tsx"})
    if not langs:
        if (Path(target.path) / "go.mod").is_file():
            langs = ["go"]
        elif any((Path(target.path) / x).is_file() for x in ("tsconfig.json", "tsconfig.app.json")):
            langs = ["typescript", "tsx"]
        else:
            langs = ["javascript"]
    args: list[str] = []
    for lang in langs:
        args += ["-l", lang]
    return args


def scc(target: RepoTarget) -> ToolDef:
    binary = _binary("scc")
    excluded = _excluded_dirs(target)
    return ToolDef(
        name="scc", binary=binary, validated_version="3.7.0",
        version_argv=[binary, "--version"], normal_exits=frozenset({0}),
        argv_builder=lambda _t: [binary, "--no-cocomo", "--exclude-dir",
                                 ",".join(excluded), "--format", "json", *_roots(target)],
        output_validator=parsers.validate_scc, view_builder=parsers.scc_view,
        view_lines=120, applied_exclusions=excluded, cwd_mode="output",
    )


def lizard(target: RepoTarget) -> ToolDef:
    binary = _binary("lizard")
    excluded = _excluded_dirs(target)
    exclude_args: list[str] = []
    for name in excluded:
        exclude_args += ["-x", f"*/{name}/*"]
    exclude_args += ["-x", "*.min.js"]
    return ToolDef(
        name="lizard", binary=binary, validated_version="1.23.0",
        version_argv=[binary, "--version"], normal_exits=frozenset({0, 1}),
        argv_builder=lambda _t: [binary, *_language_args(target), *exclude_args, *_roots(target)],
        output_validator=parsers.nonempty_output, view_builder=parsers.lizard_view,
        view_lines=260, applied_exclusions=excluded + ["*.min.js"], cwd_mode="output",
    )


def jscpd(target: RepoTarget) -> ToolDef:
    binary = _binary("jscpd")
    ignores = _jscpd_ignores([target])
    return ToolDef(
        name="jscpd", binary=binary, validated_version="5.0.12",
        version_argv=[binary, "--version"], normal_exits=frozenset({0}),
        argv_builder=lambda _t: [binary, "--min-tokens", "50", "--mode", "strict",
                                 "--reporters", "console", "--no-colors", "--ignore",
                                 ",".join(ignores), *_roots(target)],
        output_validator=parsers.validate_jscpd, view_builder=parsers.jscpd_view,
        view_lines=200, applied_exclusions=ignores, cwd_mode="output", timeout_s=300,
        remove_env=NODE_ENV_REMOVALS,
    )


def jscpd_multi(targets: list[RepoTarget]) -> ToolDef:
    if not targets:
        raise ValueError("jscpd_multi requires at least one target")
    binary = _binary("jscpd")
    ignores = _jscpd_ignores(targets)
    roots = [str(path.resolve()) for target in targets for path in target.root_paths()]
    return ToolDef(
        name="jscpd-cross", binary=binary, validated_version="5.0.12",
        version_argv=[binary, "--version"], normal_exits=frozenset({0}),
        argv_builder=lambda _t: [binary, "--min-tokens", "50", "--mode", "strict",
                                 "--reporters", "console", "--no-colors", "--ignore",
                                 ",".join(ignores), *roots],
        output_validator=parsers.validate_jscpd, view_builder=parsers.jscpd_view,
        view_lines=220, applied_exclusions=ignores, cwd_mode="output", timeout_s=600,
        remove_env=NODE_ENV_REMOVALS,
    )


def staticcheck(target: RepoTarget) -> ToolDef:
    binary = _binary("staticcheck")
    go_binary = _binary("go")
    return ToolDef(
        name="staticcheck", binary=binary, validated_version="2026.1",
        version_argv=[binary, "--version"], normal_exits=frozenset({0, 1}),
        argv_builder=lambda _t: [binary, "./..."], env=_go_env(go_binary),
        remove_env=GO_ENV_REMOVALS,
        degraders=[parsers.staticcheck_degraded], view_builder=parsers.staticcheck_view,
        view_lines=260, applied_exclusions=["generated docs/ findings (view only)"],
        network=False, cwd_mode="target", timeout_s=600,
        extra_notes=_go_notes(go_binary),
        # No DNS preflight: the Go lane only conditionally needs network (cold
        # module cache). Attempt-and-classify — offline downloads fail loudly.
    )


def go_list(target: RepoTarget) -> ToolDef:
    binary = _binary("go")
    return ToolDef(
        name="go-list", binary=binary, validated_version="go1.26.5",
        version_argv=[binary, "version"], normal_exits=frozenset({0}),
        argv_builder=lambda _t: [binary, "list", "-deps", "-json", "./..."],
        env=_go_env(binary), remove_env=GO_ENV_REMOVALS,
        output_validator=parsers.validate_go_list,
        degraders=[parsers.go_list_degraded],
        view_builder=parsers.go_list_view, view_lines=300, reads_declared=["go.mod", "go.sum"],
        applied_exclusions=["stdlib and third-party packages excluded from internal edge set"],
        network=False, cwd_mode="target", timeout_s=600,
        extra_notes=_go_notes(binary),
        # No DNS preflight — see staticcheck; warm-cache offline runs must succeed.
    )


def osv(target: RepoTarget) -> ToolDef:
    binary = _binary("osv-scanner")
    lock = target.pm.lockfile or ("go.mod" if (Path(target.path) / "go.mod").is_file() else "")
    return ToolDef(
        name="osv-scanner", binary=binary, validated_version="2.4.0",
        version_argv=[binary, "--version"], normal_exits=frozenset({0, 1}), network=True,
        argv_builder=lambda _t: [binary, "scan", "source", "--lockfile",
                                 str(Path(target.path) / lock), "--data-source", "native",
                                 "--no-resolve", "--config", os.devnull,
                                 "--format", "table"],
        output_validator=parsers.nonempty_output, view_builder=parsers.osv_view,
        view_lines=260, reads_declared=[lock] if lock else [], cwd_mode="output", timeout_s=300,
        preflight=_dns_preflight("api.osv.dev"),
        extra_notes="declared endpoint: api.osv.dev (lockfile is read locally; "
                    "only vulnerability queries leave the machine)",
    )


def outdated(target: RepoTarget) -> ToolDef:
    vector = yarn_exec_vector_guard(target)
    use_yarn = target.pm.name == "yarn" and not vector
    if use_yarn:
        binary = _binary("yarn")
        env = {
            **COREPACK_GUARD_ENV,
            "NPM_CONFIG_USERCONFIG": os.devnull,
            "NPM_CONFIG_GLOBALCONFIG": os.devnull,
            "NPM_CONFIG_REGISTRY": "https://registry.yarnpkg.com/",
            "YARN_REGISTRY": "https://registry.yarnpkg.com/",
        }
        return ToolDef(
            name="outdated", binary=binary, validated_version="1.22.22",
            version_argv=[binary, "--version"], normal_exits=frozenset({0, 1}), network=True,
            argv_builder=lambda _t: [binary, "outdated", "--json", "--registry",
                                     "https://registry.yarnpkg.com/"], env=env,
            guards=[lambda t: _pm_config_endpoint_guard(t, manager="yarn"),
                    _dependency_host_guard],
            output_validator=parsers.validate_yarn_outdated, view_builder=parsers.yarn_outdated_view,
            view_lines=300, reads_declared=["package.json", target.pm.lockfile],
            cwd_mode="target", timeout_s=300, preflight=_dns_preflight("registry.yarnpkg.com"),
            remove_env=NODE_ENV_REMOVALS,
            remove_env_prefixes=PACKAGE_ENV_PREFIXES,
            extra_notes="; ".join(x for x in (
                "declared endpoint: registry.yarnpkg.com",
                _package_network_notes(target, manager="yarn")) if x),
        )
    binary = _binary("npm")

    def fallback(_target: RepoTarget, _combined: str, _exit: int) -> str:
        if target.pm.name != "npm":
            reason = f"package-manager fallback: npm approximates {target.pm.name}"
            return reason + (f"; yarn refused because {vector}" if vector else "")
        return ""

    return ToolDef(
        name="outdated", binary=binary, validated_version="10.9.2",
        version_argv=[binary, "--version"], normal_exits=frozenset({0, 1}), network=True,
        argv_builder=lambda _t: [binary, "outdated", "--json", "--long",
                                 "--registry=https://registry.npmjs.org/", "--ignore-scripts"],
        env={
            "NPM_CONFIG_USERCONFIG": os.devnull,
            "NPM_CONFIG_GLOBALCONFIG": os.devnull,
            "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/",
            "NPM_CONFIG_IGNORE_SCRIPTS": "true",
        },
        guards=[lambda t: _pm_config_endpoint_guard(t, manager="npm"),
                _dependency_host_guard],
        output_validator=parsers.validate_npm_outdated, degraders=[fallback],
        view_builder=parsers.npm_outdated_view, view_lines=300,
        reads_declared=["package.json", target.pm.lockfile], cwd_mode="target", timeout_s=300,
        preflight=_dns_preflight("registry.npmjs.org"),
        remove_env=NODE_ENV_REMOVALS,
        remove_env_prefixes=PACKAGE_ENV_PREFIXES,
        extra_notes="; ".join(x for x in (
            "declared endpoint: registry.npmjs.org",
            _package_network_notes(target, manager="npm")) if x),
    )


def git_history(target: RepoTarget, since: str | None = None,
                coupling_sample_cap: int = 0) -> ToolDef:
    since = since or (date.today() - timedelta(days=730)).isoformat()
    requested = os.environ.get("PROJECT_ANALYSIS_PYDRILLER_PYTHON", "")
    binary = requested if requested and Path(requested).is_file() else sys.executable
    package_root = str(Path(__file__).resolve().parents[1])
    git_binary = str(Path(shutil.which("git") or "git").resolve())
    return ToolDef(
        name="git-history", binary=binary, validated_version="pydriller 2.10",
        version_argv=[binary, "-m", "analysis_wrapper.git_history.worker", "--version"],
        normal_exits=frozenset({0}),
        # coupling-sample-cap 0 = no cap (default: unchanged behavior).
        argv_builder=lambda _t: [binary, "-m", "analysis_wrapper.git_history.worker",
                                 "--repo", target.path, "--since", since,
                                 "--top", "20", "--min-shared", "5", "--bulk-limit", "50",
                                 "--coupling-sample-cap", str(coupling_sample_cap)],
        env={
            "PYTHONPATH": package_root,
            "PROJECT_ANALYSIS_GIT_BINARY": git_binary,
            "GIT_PYTHON_GIT_EXECUTABLE": git_binary,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }, output_validator=parsers.validate_history,
        remove_env=["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"],
        remove_env_prefixes=["GIT_"],
        degraders=[parsers.history_degraded], view_builder=parsers.history_view,
        view_lines=300, applied_exclusions=TIER1_DIRS + ["lockfiles", "*.min.*", "*.map"],
        cwd_mode="output", timeout_s=900,
    )


def local_tools(target: RepoTarget) -> list[ToolDef]:
    tools = [scc(target), lizard(target), jscpd(target)]
    stacks = {x.lower() for x in target.stacks}
    is_go = "go" in stacks or (Path(target.path) / "go.mod").is_file()
    is_node = bool(stacks & {"js", "ts", "tsx", "javascript", "typescript"}) or \
        (Path(target.path) / "package.json").is_file()
    if is_node:
        tools.append(dependency_cruiser(target))
    if is_go:
        tools.extend([staticcheck(target), go_list(target)])
    if target.git.is_git:
        tools.append(git_history(target))
    return tools


def network_tools(target: RepoTarget) -> list[ToolDef]:
    tools: list[ToolDef] = []
    if target.pm.lockfile or (Path(target.path) / "go.mod").is_file():
        tools.append(osv(target))
    if (Path(target.path) / "package.json").is_file():
        tools.append(outdated(target))
    return tools


def tool_for(name: str, target: RepoTarget) -> ToolDef:
    definitions = {t.name: t for t in local_tools(target) + network_tools(target)}
    if name not in definitions:
        raise KeyError(f"tool {name!r} is not applicable to {target.repo_id}")
    return definitions[name]
