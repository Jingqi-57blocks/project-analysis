"""Preflight / readiness check ("doctor") for Project Analysis.

Stdlib-only by design: this module must import and run on the HOST
interpreter before any venv, node_modules, or Go toolchain exists — it is
part of the pre-first-run story, invoked through ``bin/project-analysis``
(the low-syntax-floor launcher) or directly via
``analysis_wrapper.cli doctor``. Never import a third-party package here.

``doctor`` reports; it never installs, never writes to the analyzed target,
and never touches the network beyond probing already-installed local
binaries for their version string (``--version``-style invocations only).

Single source of truth
-----------------------
The tool inventory comes from ``tools/manifest.json`` (validated against
``tools/manifest.schema.json``) — doctor reads it, it never hardcodes a tool
list. Adding/removing/reclassifying a tool only touches the manifest.

Lane-sniff bias (READ THIS BEFORE CHANGING THE HEURISTICS)
------------------------------------------------------------
When ``--workspace`` is given, doctor walks the tree with a cheap, STDLIB,
CONSERVATIVE/SUPERSET heuristic to decide which analysis lanes are plausibly
relevant, so that (for example) a pure-JS target reports Go/callgraph/
staticcheck as ``not-applicable`` rather than ``unavailable`` (missing).

The heuristic is deliberately biased to OVER-INCLUSION: when unsure, a lane
is marked applicable. Under-detection would make the later ``setup`` phase
under-provision a lane the target actually needs; over-detection only costs
an extra "needed-for-this-target but absent -> disclosed reduced coverage"
line, which is harmless and honest. This sniff is NOT the analyzer's real
discovery (``discovery/emit.py``) and never claims to be — it is a fast,
best-effort classifier for a preflight report, run before any real analysis
exists to consult. Real discovery remains authoritative.

Concretely:
- ``js``/``go`` lanes are sniffed directly (manifest markers below).
- ``sql`` is sniffed directly (any ``*.sql`` file).
- ``history`` is applicable iff at least one repository marker (a ``.git``
  directory OR a ``.git`` file — worktrees/submodules) was found anywhere in
  the walk, including nested repos.
- ``complexity`` (lizard covers JS/TS/Go) mirrors ``js or go``.
- ``network`` (dependency-audit tools) mirrors ``js or go`` — the ecosystems
  those scanners understand.
- ``structural`` (scc, ast-grep) and ``duplication`` (jscpd) are treated as
  always applicable once ANY workspace is given: both operate across
  virtually any source language, so gating them on a file-type sniff would
  only risk under-inclusion for no real benefit.
- ``report`` (the offline HTML export) is always applicable — it is a
  rendering feature of the wrapper itself, not gated by the target's
  language at all.
- ``core`` is always applicable.

With no ``--workspace``, every lane is reported applicable-unknown (``None``)
— doctor does not guess in the absence of a target to look at.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import paths

# --------------------------------------------------------------------------
# Exit codes (documented, distinct, consistent with bin/project-analysis:
# 3 = environment-incomplete, 4 = installation-corrupt there too).
# --------------------------------------------------------------------------
EXIT_OK = 0
EXIT_INTERNAL_FAILURE = 1
EXIT_INVALID_INVOCATION = 2
EXIT_ENVIRONMENT_INCOMPLETE = 3
EXIT_INSTALLATION_CORRUPT = 4

SCHEMA_VERSION = "1.0.0"
MIN_PYTHON = (3, 11)

# Bounded walk: keep the sniff cheap even on a large workspace. Only
# defensible build-artifact conventions belong here (plus ``.git``, handled
# specially below) — NOT ``state``/``output``/``exported``: those names are
# only ever the analyzer's OWN data-root subdirectories, which live outside
# the analyzed workspace entirely (post data/code separation), so a directory
# with one of those names found INSIDE a target is always the user's own
# source and must never be pruned (57B-91 review FIX 2).
_SKIP_DIR_NAMES = {"node_modules", "vendor", "dist", "build", "coverage",
                   ".git"}
_MAX_WALK_ENTRIES = 20000
_MAX_WALK_DEPTH = 12

_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_TSCONFIG_RE = re.compile(r"^tsconfig.*\.json$")
_VERSION_RE = re.compile(r"(\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.]+)?)")

_SNIFFED_LANES = ("js", "go")
_ALWAYS_APPLICABLE_LANES = ("core", "structural", "duplication", "report")


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

class ManifestError(ValueError):
    """The tool manifest is missing or malformed — an installation-corrupt
    condition, not an ordinary ValueError."""


_REQUIRED_TOOL_FIELDS = ("id", "name", "lanes", "requirement", "ownership")


def read_manifest() -> dict:
    """Read and validate ``tools/manifest.json``.

    Validates every field ``build_report`` actually dereferences on each tool
    entry (``id``, ``name``, ``lanes``, ``requirement``, ``ownership`` —
    see ``_REQUIRED_TOOL_FIELDS``), not just ``id``/``lanes``: a manifest
    entry missing any of these used to raise a bare ``KeyError`` deep inside
    ``build_report`` (57B-91 review FIX 6), which fell through to the generic
    internal-failure handler (exit 1) instead of the correct
    installation-corrupt code (exit 4). Raising ``ManifestError`` here for
    every field the rest of this module depends on ensures that mapping is
    always taken.
    """
    path = paths.skill_root() / "tools" / "manifest.json"
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        raise ManifestError(f"could not read tool manifest at {path}: {exc}") from exc
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"tool manifest at {path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("tools"), list) or not doc["tools"]:
        raise ManifestError(f"tool manifest at {path} is missing a non-empty 'tools' array")
    for tool in doc["tools"]:
        if not isinstance(tool, dict):
            raise ManifestError(f"tool manifest at {path} has a malformed tool entry")
        missing = [field for field in _REQUIRED_TOOL_FIELDS if field not in tool]
        if missing:
            raise ManifestError(
                f"tool manifest at {path} has an entry missing required "
                f"field(s) {missing}: {tool!r}")
    return doc


# --------------------------------------------------------------------------
# Lane sniff
# --------------------------------------------------------------------------

def sniff_lanes(workspace: str | Path) -> dict:
    """Bounded, stdlib, superset-biased scan of ``workspace``.

    Returns ``{"js": bool, "go": bool, "sql": bool, "has_repo": bool,
    "truncated": bool}``. ``truncated`` means the walk hit its bound before
    finishing; per the over-inclusion bias, every lane sniffed directly is
    forced ``True`` in that case rather than reporting a possibly-incomplete
    ``False``.
    """
    root = Path(workspace)
    js_hit = go_hit = sql_hit = has_repo = False
    entries_seen = 0
    truncated = False
    for current, dirs, files in os.walk(root):
        try:
            depth = len(Path(current).resolve().relative_to(root.resolve()).parts)
        except ValueError:
            depth = 0
        entries_seen += len(dirs) + len(files)
        if ".git" in dirs or (Path(current) / ".git").is_file():
            has_repo = True
        # Prune AFTER the repo-marker check above so a repo's own ``.git``
        # directory is detected but never recursed into; other skip-listed
        # directories (node_modules, vendor, ...) are pruned outright.
        dirs[:] = [d for d in dirs if d not in _SKIP_DIR_NAMES]
        if depth >= _MAX_WALK_DEPTH:
            # A depth-prune is just as incomplete a scan as the entries-bound
            # below (57B-91 review FIX 1): anything past this depth is
            # unexamined, so the over-inclusion failsafe must fire here too
            # rather than silently reporting a possibly-wrong ``False``.
            dirs[:] = []
            truncated = True
        for name in files:
            if name == "package.json" or _TSCONFIG_RE.match(name):
                js_hit = True
            elif name.endswith(_JS_SUFFIXES):
                js_hit = True
            elif name in ("go.mod", "go.sum"):
                go_hit = True
            elif name.endswith(".go"):
                go_hit = True
            elif name.endswith(".sql"):
                sql_hit = True
        if entries_seen > _MAX_WALK_ENTRIES:
            truncated = True
            break
    if truncated:
        # Over-inclusion bias: an incomplete scan must never under-report.
        js_hit = go_hit = sql_hit = has_repo = True
    return {"js": js_hit, "go": go_hit, "sql": sql_hit, "has_repo": has_repo,
            "truncated": truncated}


def _lane_applicable(lane: str, sniff: dict | None) -> bool | None:
    """``True``/``False`` when decidable, ``None`` for applicable-unknown
    (no workspace given — doctor does not guess)."""
    if sniff is None:
        return None
    if lane == "js":
        return sniff["js"]
    if lane == "go":
        return sniff["go"]
    if lane == "sql":
        return sniff["sql"]
    if lane == "history":
        return sniff["has_repo"]
    if lane == "complexity":
        return sniff["js"] or sniff["go"]
    if lane == "network":
        return sniff["js"] or sniff["go"]
    if lane in _ALWAYS_APPLICABLE_LANES:
        return True
    # Unknown lane name (manifest evolved ahead of this module): bias to
    # over-inclusion rather than silently hiding a new tool.
    return True


def _tool_applicable(lanes: list[str], sniff: dict | None) -> bool | None:
    """``True`` if ANY lane is applicable, ``False`` if ALL are
    not-applicable, ``None`` if at least one is unknown and none is True."""
    results = [_lane_applicable(lane, sniff) for lane in lanes]
    if any(result is True for result in results):
        return True
    if all(result is False for result in results):
        return False
    return None


# --------------------------------------------------------------------------
# Version probing
# --------------------------------------------------------------------------

def _extract_version(text: str) -> str:
    match = _VERSION_RE.search(text or "")
    return match.group(1) if match else ""


def _run_probe(cmd: str, timeout: float = 5.0) -> str:
    """Run a whitespace-splittable probe command; never raise. Returns the
    combined, stripped stdout+stderr, or ``""`` on any failure (missing
    binary, timeout, odd/garbled output — none of these should ever crash
    doctor)."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return ""
    if not parts:
        return ""
    try:
        proc = subprocess.run(parts, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()


def _dist_info_version(venv_dir: Path, dist_name: str) -> str:
    """Best-effort version lookup for a pip-installed library with no CLI
    (``executable: null`` in the manifest) inside the wrapper's own venv, by
    reading its ``*.dist-info`` directory name — no import needed, so this
    stays valid from the host interpreter even though the venv belongs to a
    different (or not-yet-existing) interpreter."""
    if sys.platform == "win32":
        site_dirs = [venv_dir / "Lib" / "site-packages"]
    else:
        lib = venv_dir / "lib"
        site_dirs = ([p / "site-packages" for p in sorted(lib.glob("python3.*"))]
                     if lib.is_dir() else [])
    normalized_target = dist_name.replace("-", "_").lower()
    for site in site_dirs:
        if not site.is_dir():
            continue
        try:
            entries = list(site.glob("*.dist-info"))
        except OSError:
            continue
        for entry in entries:
            stem = entry.name[: -len(".dist-info")]
            name_part, _, version_part = stem.rpartition("-")
            if not name_part:
                continue
            if name_part.replace("-", "_").lower() == normalized_target:
                return version_part
    return ""


# --------------------------------------------------------------------------
# Per-tool probing
# --------------------------------------------------------------------------

def _probe_analysis_wrapper() -> tuple[bool, str, str]:
    from . import bootstrap
    venv_dir = paths.venv_dir()
    python = bootstrap.environment_python(venv_dir)
    script = python.parent / (
        "project-analysis-wrapper.exe" if sys.platform == "win32"
        else "project-analysis-wrapper")
    if script.is_file():
        out = _run_probe(f"{shlex.quote(str(script))} --version")
        return True, str(script), _extract_version(out)
    which = shutil.which("project-analysis-wrapper")
    if which:
        out = _run_probe(f"{shlex.quote(which)} --version")
        return True, which, _extract_version(out)
    return False, "", ""


def _probe_node_tool(tool_id: str) -> tuple[bool, str, str, str]:
    """Returns ``(found, resolved_path, version, note)``, reusing
    ``node_env``'s own resolution (the analyzer-owned ``node_tools`` runtime
    location, falling back to the legacy in-code path) rather than
    duplicating it."""
    from . import node_env
    node_dir = node_env.default_node_tools_dir()
    info = node_env.probe(node_dir)
    if tool_id == "dependency-cruiser":
        binary = node_env.expected_depcruise_binary(node_dir)
        found = info.available
        version = info.depcruise_version
    else:
        found = info.available and bool(info.typescript_version)
        version = info.typescript_version
        binary = node_env.typescript_lib(node_dir)
    resolved = str(binary) if found else ""
    return found, resolved, version, ("" if found else info.reason)


def _probe_go_callgraph() -> tuple[bool, str, str, str]:
    """Reuses ``go_tools``'s own resolution (analyzer-owned GOBIN first,
    PATH fallback disclosed as unpinned) instead of duplicating it."""
    from . import go_tools
    bin_dir = go_tools.default_bin_dir()
    binary, note = go_tools.resolve(bin_dir)
    if binary is None:
        return False, "", "", note
    version = go_tools.installed_version(binary)
    return True, str(binary), version, note


def _probe_venv_library(tool_id: str) -> tuple[bool, str, str]:
    dist_name = {"markdown-it-py": "markdown_it_py"}.get(tool_id, tool_id)
    venv_dir = paths.venv_dir()
    version = _dist_info_version(venv_dir, dist_name)
    return bool(version), (str(venv_dir) if version else ""), version


def _probe_path_tool(tool: dict) -> tuple[bool, str, str]:
    executable = tool.get("executable")
    if not executable:
        return False, "", ""
    which = shutil.which(executable)
    if not which:
        return False, "", ""
    version = ""
    if tool.get("version_probe"):
        version = _extract_version(_run_probe(tool["version_probe"]))
    return True, which, version


def _probe_tool(tool: dict) -> dict:
    """Resolve found/path/version/note for one manifest tool entry. Never
    raises — any probing failure degrades to "not found" rather than
    crashing doctor (offline/local-only; odd tool output is always
    tolerated)."""
    tool_id = tool["id"]
    note = ""
    try:
        if tool_id == "python":
            found, path, version = True, sys.executable, ".".join(
                str(part) for part in sys.version_info[:3])
        elif tool_id == "analysis-wrapper":
            found, path, version = _probe_analysis_wrapper()
        elif tool_id in ("dependency-cruiser", "typescript"):
            found, path, version, note = _probe_node_tool(tool_id)
        elif tool_id == "go-callgraph":
            found, path, version, note = _probe_go_callgraph()
        elif tool.get("ownership") == "analyzer-managed" and tool.get("executable") is None:
            found, path, version = _probe_venv_library(tool_id)
        else:
            found, path, version = _probe_path_tool(tool)
    except Exception as exc:  # pragma: no cover - defensive; probing must never crash doctor
        found, path, version = False, "", ""
        note = f"probe error (treated as not found): {exc!r}"
    return {"found": found, "path": path, "version": version, "note": note}


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

_SIMPLE_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
_ACCEPTED_RANGE_CLAUSE_RE = re.compile(r"^(>=|==)\s*v?(\d+(?:\.\d+)*)$")


def _version_tuple(text: str) -> tuple[int, ...] | None:
    text = text.strip()
    if not _SIMPLE_VERSION_RE.match(text):
        return None
    return tuple(int(part) for part in text.split("."))


def _range_satisfied(detected: str, accepted_range: str) -> bool | None:
    """``True``/``False`` when ``accepted_range`` can be parsed and checked
    against ``detected``, ``None`` when it cannot (caller must then fall back
    to the prior, more conservative behavior rather than wrongly suppressing
    a real drift note).

    Supports single ``>=X.Y[.Z]`` and ``==X.Y[.Z]`` clauses (the ``v``-prefixed
    form too, e.g. ``==v0.48.0``), compared as tuples of numeric components —
    good enough for the one hard requirement (python) this exists to quiet,
    without pretending to be a general semver/npm-range parser. Anything more
    exotic (``22.x || 24.x``, OR-clauses, ``~``/``^``) is deliberately left
    unparsed: a wrong "satisfied" here would suppress a genuine mismatch.
    """
    detected_tuple = _version_tuple(detected.lstrip("vV"))
    if detected_tuple is None:
        return None
    match = _ACCEPTED_RANGE_CLAUSE_RE.match(accepted_range.strip())
    if not match:
        return None
    op, bound_text = match.groups()
    bound_tuple = _version_tuple(bound_text)
    if bound_tuple is None:
        return None
    # Compare on the shared prefix length so "3.11" vs "3.11.6" lines up.
    width = max(len(detected_tuple), len(bound_tuple))
    detected_padded = detected_tuple + (0,) * (width - len(detected_tuple))
    bound_padded = bound_tuple + (0,) * (width - len(bound_tuple))
    if op == ">=":
        return detected_padded >= bound_padded
    return detected_padded == bound_padded


def _drift_note(validated: str | None, detected: str,
                 accepted_range: str | None = None) -> str:
    if not validated or not detected:
        return ""
    if validated == detected:
        return ""
    # Tolerate a common form of "close enough": validated "3.11" vs detected
    # "3.11.6" is not drift (accepted_range governs that); only an outright
    # different value is flagged.
    if detected.startswith(validated + ".") or validated.startswith(detected + "."):
        return ""
    # Suppress the false alarm when the manifest's own accepted_range says the
    # detected version is fine (57B-91 review FIX 4) — e.g. python
    # validated_version "3.11", accepted_range ">=3.11", host on 3.13.5.
    # Conservative: if the range can't be parsed, fall back to flagging.
    if accepted_range and _range_satisfied(detected, accepted_range) is True:
        return ""
    return f"drift: detected {detected}, validated {validated}"


def _what_you_lose(tool: dict) -> str:
    notes = tool.get("notes") or ""
    # First sentence is generally the "what this buys you" line in the
    # manifest; good enough for a terse doctor line without re-authoring text.
    first_sentence = notes.split(". ")[0].strip()
    return first_sentence + ("." if first_sentence and not first_sentence.endswith(".") else "")


def build_report(workspace: str | Path | None = None, *,
                 tool_ids: "frozenset[str] | None" = None) -> dict:
    """Assemble the full readiness report.

    ``tool_ids`` (57B-95 review FIX 6, optional/keyword-only, default
    ``None`` = every manifest tool, unchanged behavior for every existing
    caller): restricts probing to just this subset of tool ids. Used by
    ``compat.runtime_reconciliation`` to only spawn subprocesses for the
    handful of PINNED analyzer-managed tools it actually cares about,
    instead of the full manifest (developer-managed tools like ``go``/
    ``node``/``ast-grep`` included) on every single gated CLI invocation.
    Verdict/coverage fields (``core_ok``, ``verdict``, lane applicability)
    are computed only over the filtered subset when this is given, so this
    is NOT meant for the ``doctor`` command's own full report -- callers
    that need the true overall readiness verdict must leave it ``None``.
    """
    manifest = read_manifest()
    sniff = sniff_lanes(workspace) if workspace is not None else None
    manifest_tools = (manifest["tools"] if tool_ids is None
                      else [t for t in manifest["tools"] if t["id"] in tool_ids])

    python_ok = tuple(sys.version_info[:2]) >= MIN_PYTHON
    tools: list[dict] = []
    setup_needed = False
    network_required_for_setup = False

    for tool in manifest_tools:
        applicable = _tool_applicable(tool["lanes"], sniff)
        requirement = tool["requirement"]
        if requirement == "required":
            classification = "required"
        elif applicable is False:
            classification = "not-applicable"
        else:
            classification = "needed-for-this-target"

        if classification == "not-applicable":
            probed = {"found": False, "path": "", "version": "", "note": ""}
        else:
            probed = _probe_tool(tool)

        state = ("not-applicable" if classification == "not-applicable" else
                 "present" if probed["found"] else "unavailable")

        if state == "unavailable" and tool["ownership"] == "analyzer-managed":
            setup_needed = True
            if tool.get("network_host"):
                network_required_for_setup = True

        tools.append({
            "id": tool["id"],
            "name": tool["name"],
            "ownership": tool["ownership"],
            "requirement": requirement,
            "lanes": tool["lanes"],
            "classification": classification,
            "state": state,
            "resolved_path": probed["path"],
            "detected_version": probed["version"],
            "validated_version": tool.get("validated_version"),
            "accepted_range": tool.get("accepted_range"),
            "drift": _drift_note(tool.get("validated_version"), probed["version"],
                                 tool.get("accepted_range")),
            "network_host": tool.get("network_host"),
            "note": probed["note"],
            "what_you_lose": ("" if state != "unavailable" or requirement != "optional"
                              else _what_you_lose(tool)),
        })

    lanes_seen = sorted({lane for tool in manifest_tools for lane in tool["lanes"]})
    lane_applicability = {lane: _lane_applicable(lane, sniff) for lane in lanes_seen}

    # Generic over the manifest's required set (57B-91 review FIX 3): any
    # ``requirement: required`` tool that is not present sinks ``core_ok``,
    # not just a hardcoded ``python`` id — a future required tool that goes
    # missing must not be silently ignored here.
    core_required_missing = [
        t for t in tools if t["classification"] == "required" and t["state"] != "present"]
    core_ok = python_ok and not core_required_missing

    if not python_ok:
        verdict = "blocked"
    elif any(t["state"] == "unavailable" and t["requirement"] == "required" for t in tools):
        verdict = "setup-needed"
    elif setup_needed:
        verdict = "setup-needed"
    elif any(t["state"] == "unavailable" for t in tools):
        verdict = "ready-reduced-coverage"
    else:
        verdict = "ready"

    try:
        # Pure resolution only (57B-91 review FIX 7): doctor is read-only and
        # must not mkdir the data root as a side effect of merely reporting
        # where it would resolve to.
        data_root_str = str(paths.resolved_data_root())
        data_root_error = ""
    except (OSError, ValueError) as exc:
        data_root_str = ""
        data_root_error = str(exc)

    try:
        skill_version = (paths.skill_root() / "VERSION").read_text("utf-8").strip() or "unknown"
    except OSError:
        skill_version = "unknown"

    return {
        "schema_version": SCHEMA_VERSION,
        "skill_version": skill_version,
        "data_root": data_root_str,
        "data_root_error": data_root_error,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "python_ok": python_ok,
        "workspace": str(Path(workspace).expanduser().resolve()) if workspace is not None else None,
        "lane_sniff": sniff,
        "lane_applicability": lane_applicability,
        "tools": tools,
        "verdict": verdict,
        "core_ok": core_ok,
        "setup_needed": setup_needed,
        "network_required_for_setup": network_required_for_setup,
    }


# --------------------------------------------------------------------------
# Human-readable rendering
# --------------------------------------------------------------------------

_VERDICT_LINES = {
    "ready": "READY — every applicable tool is present.",
    "ready-reduced-coverage": "READY (reduced coverage) — optional tools are "
                              "absent; analysis will run with disclosed gaps.",
    "setup-needed": "SETUP NEEDED — a required or analyzer-managed tool is "
                    "not yet installed; the first run will provision it.",
    "blocked": "BLOCKED — Python 3.11+ is required and was not found.",
}


def render_human(report: dict) -> str:
    lines: list[str] = []
    lines.append(f"Project Analysis doctor — skill {report['skill_version']}")
    lines.append(f"data root: {report['data_root'] or '(unavailable: ' + report['data_root_error'] + ')'}")
    lines.append(f"python: {report['python_version']} "
                 f"({'ok' if report['python_ok'] else 'TOO OLD, need 3.11+'})")
    if report["workspace"] is not None:
        lines.append(f"workspace: {report['workspace']}")
        if report["lane_sniff"]["truncated"]:
            lines.append("  (lane sniff hit its scan bound — over-inclusive result assumed)")
    else:
        lines.append("workspace: (none given — lane applicability is unknown, not guessed)")
    lines.append("")

    groups = [
        ("required", "REQUIRED"),
        ("needed-for-this-target", "NEEDED FOR THIS TARGET"),
        ("not-applicable", "NOT APPLICABLE FOR THIS TARGET"),
    ]
    for key, title in groups:
        rows = [t for t in report["tools"] if t["classification"] == key]
        if not rows:
            continue
        lines.append(f"[{title}]")
        for t in sorted(rows, key=lambda r: r["id"]):
            state_label = {"present": "present", "unavailable": "MISSING",
                          "not-applicable": "n/a"}[t["state"]]
            version_bit = f" v{t['detected_version']}" if t["detected_version"] else ""
            drift_bit = f" ({t['drift']})" if t["drift"] else ""
            line = f"  {t['name']:<40} {state_label}{version_bit}{drift_bit}"
            lines.append(line)
            if t["state"] == "unavailable" and t["what_you_lose"]:
                lines.append(f"      -> lose: {t['what_you_lose']}")
        lines.append("")

    lines.append(_VERDICT_LINES[report["verdict"]])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _python_remedy() -> str:
    have = ".".join(str(part) for part in sys.version_info[:3])
    return (
        "Project Analysis requires Python %d.%d+ (found %s).\n"
        "It is the only hard prerequisite — everything else is set up on first "
        "run or gracefully degraded.\n"
        "  macOS:          brew install python@3.11\n"
        "  Debian/Ubuntu:  sudo apt-get install python3.11\n"
        "  or use pyenv:   https://github.com/pyenv/pyenv\n"
        % (MIN_PYTHON[0], MIN_PYTHON[1], have)
    )


def run(workspace: str | None, *, as_json: bool) -> int:
    """Implements the ``doctor`` subcommand. Always returns one of the
    documented exit codes; never raises to its caller."""
    if workspace:
        candidate = Path(workspace).expanduser()
        if not candidate.is_dir():
            print(f"doctor: --workspace {workspace!r} is not a directory", file=sys.stderr)
            return EXIT_INVALID_INVOCATION

    try:
        report = build_report(workspace)
    except ManifestError as exc:
        print(f"doctor: installation looks corrupt — {exc}", file=sys.stderr)
        return EXIT_INSTALLATION_CORRUPT
    except Exception as exc:  # pragma: no cover - last-resort guard
        print(f"doctor: internal failure — {exc!r}", file=sys.stderr)
        return EXIT_INTERNAL_FAILURE

    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_human(report), end="")

    if not report["python_ok"]:
        sys.stderr.write(_python_remedy())
        return EXIT_ENVIRONMENT_INCOMPLETE
    return EXIT_OK
