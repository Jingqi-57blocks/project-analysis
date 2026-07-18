"""Route liveness (change-friction round): which backend routes are actually
called, and by whom.

Mechanical join of frontend call sites against backend route registrations —
the evidence that answers "which parts of a parallel-rewritten service are
still real". This module RECORDS and MATCHES with disclosed heuristics; it
never concludes "dead". A route with no caller found in the ANALYZED repos is
`no-caller-found` (candidate unused) — mobile apps, external API consumers,
and ops scripts are invisible to repository evidence (standing disclaimer).

A path-shape match alone does NOT credit a backend: each `${base}/path` call's
base identifier is resolved to the backend it actually targets (``base_map``,
evidence-based), and a route is `ui-called` only when a caller whose resolved
base maps to THAT backend hits it — so a route shared by two parallel backends
is not falsely credited to the one the caller does not bind (57B-15).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import astgrep
from .base_map import resolve_base_backends

_SKIP_DIRS = {"node_modules", "vendor", ".git", "dist", "build", "coverage"}
_MAX_FILES = 6000
_MAX_BYTES = 262_144
_SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go"}

# Frontend HTTP call sites: `${base}/path...` — capture whether the base was
# written `config.X` (explicit global base) vs a bare `X` (which may be a LOCAL
# alias of a config base, resolved per-file below), plus the literal path head.
_UI_CALL = re.compile(
    r"\$\{\s*(config\.)?(\w+)\s*\}(/[A-Za-z0-9_\-./:${}]*)")
# Per-file rebindings of a config base to a local name — a `${localName}/path`
# call binds to the REAL underlying config base, not to a global identifier that
# shares the local name (57B-15: `const x = config.someBase` and `const {
# someBase: x } = config` both make a bare `${x}` resolve to `someBase`). Only
# right-hand sides that are the imported `config` object are captured; the config
# identifier and base names are discovered from the frontend, never allowlisted.
_ALIAS_ASSIGN = re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*config\.(\w+)")
_ALIAS_ASSIGN_TMPL = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*=\s*`\$\{\s*config\.(\w+)")
_ALIAS_DESTRUCTURE = re.compile(
    r"(?:const|let|var)\s*\{([^{}]*)\}\s*=\s*config\b(?!\s*\.)", re.DOTALL)
_DESTR_ITEM = re.compile(r"(\w+)\s*(?::\s*(\w+))?")
# Server route registrations (Express/Koa + gin/echo/chi/mux).
_JS_ROUTE = re.compile(
    r"(?:router|app)\s*\.\s*(get|post|put|patch|delete|use)\s*\(\s*['\"]([/][^'\"]*)['\"]")
_GO_ROUTE = re.compile(
    r"\.\s*(GET|POST|PUT|PATCH|DELETE|Handle|HandleFunc|Group)\s*\(\s*\"([/][^\"]*)\"")
_VERSION_SEG = re.compile(r"^v\d+$")


@dataclass
class RouteHit:
    method: str
    path: str
    evidence: str  # repo-relative file:line


@dataclass
class CallHit:
    base: str
    path: str
    evidence: str


@dataclass
class LivenessRow:
    repo_id: str
    method: str
    path: str
    route_evidence: str
    status: str            # ui-called | internal-called | no-caller-found
    caller_evidence: list = field(default_factory=list)


@dataclass
class LivenessReport:
    rows: list = field(default_factory=list)
    ui_calls: list = field(default_factory=list)   # CallHit (disclosed raw)
    notes: list = field(default_factory=list)
    # ast-grep version/path/drift for the route-registration scan (57B-37). When
    # ast-grep is absent, routes come from the disclosed regex fallback below and
    # this records the version as unavailable.
    astgrep: dict = field(default_factory=astgrep.unavailable_provenance)

    def calls_by_base(self) -> dict:
        """The reliable migration ledger: how many distinct paths the frontend
        calls per base identifier (mainApi vs appRunnerApi vs ...). Answers
        'which backend is the UI actually using' without needing mount
        prefixes."""
        by_base: dict[str, set] = {}
        for c in self.ui_calls:
            by_base.setdefault(c.base, set()).add("/".join(_norm_segments(c.path)))
        return {base: sorted(paths) for base, paths in sorted(by_base.items())}


def _iter_source(root: Path, stats: dict | None = None):
    count = 0
    stack = [root]
    while stack:
        base = stack.pop()
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
                continue
            if entry.suffix not in _SOURCE_EXT:
                continue
            count += 1
            if count > _MAX_FILES:
                if stats is not None:            # disclose the hit cap upstream
                    stats["file_cap_hit"] = True
                return
            yield entry


def _read(path: Path, stats: dict | None = None) -> str:
    try:
        if path.stat().st_size > _MAX_BYTES:
            if stats is not None:                # a skipped oversized file is disclosed
                stats["oversized"] = stats.get("oversized", 0) + 1
            return ""
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _norm_segments(path: str) -> list[str]:
    """Path → comparable segments: query/hash stripped, params → '*', a single
    leading version segment (v1/v2) dropped so base-prefixed calls line up with
    prefix-less registrations."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    segs = [s for s in path.split("/") if s]
    # A leading base-URL interpolation token (${mainApi}) is not a path segment.
    while segs and (segs[0].startswith("${") or segs[0] == "*"):
        segs = segs[1:]
    if segs and _VERSION_SEG.match(segs[0]):
        segs = segs[1:]
    out = []
    for s in segs:
        if s.startswith((":", "{")) or s.startswith("${") or s.startswith("$"):
            out.append("*")
        else:
            out.append(s)
    return out


def _matches(route: list[str], call: list[str]) -> bool:
    """A call matches a route if the route's segments are a prefix of the call's
    (routers mount sub-paths), wildcards align with anything, AND at least one
    concrete segment agrees. The concrete-segment requirement avoids the
    router-mount-prefix trap: a leaf route registered as `/:id` normalizes to
    all-wildcard and would otherwise match almost any single-segment call,
    because its mount prefix (`app.use('/positions', r)`) is not on the
    registration."""
    if not route or len(call) < len(route):
        return False
    concrete_agree = False
    for r, c in zip(route, call):
        if r == "*" or c == "*":
            continue
        if r != c:
            return False
        concrete_agree = True
    return concrete_agree


def _config_aliases(text: str) -> dict[str, str]:
    """Local name -> underlying config base, for this file only. Covers simple
    assignment (`const x = config.base`), a template rebind (`const x =
    `${config.base}/v2``), and object destructuring with or without rename
    (`const { base, other: x } = config`; unrenamed maps a name to itself)."""
    aliases: dict[str, str] = {}
    for m in _ALIAS_ASSIGN.finditer(text):
        aliases[m.group(1)] = m.group(2)
    for m in _ALIAS_ASSIGN_TMPL.finditer(text):
        aliases[m.group(1)] = m.group(2)
    for m in _ALIAS_DESTRUCTURE.finditer(text):
        for item in m.group(1).split(","):
            got = _DESTR_ITEM.match(item.strip())
            if not got:
                continue
            key, renamed = got.group(1), got.group(2)
            aliases[renamed or key] = key
    return aliases


def ui_call_sites(repo_path: str | Path, stats: dict | None = None) -> list[CallHit]:
    root = Path(repo_path).expanduser().resolve()
    hits: list[CallHit] = []
    for path in _iter_source(root, stats):
        text = _read(path, stats)
        if "${" not in text:
            continue
        rel = path.relative_to(root).as_posix()
        aliases = _config_aliases(text)
        for m in _UI_CALL.finditer(text):
            cfg_prefix, name, raw = m.group(1), m.group(2), m.group(3)
            if len(_norm_segments(raw)) == 0:
                continue
            # An explicit `config.X` is always the global base X. A bare `${X}`
            # binds to a per-file alias when one exists, else stands as written.
            base = name if cfg_prefix else aliases.get(name, name)
            hits.append(CallHit(base=base, path=raw,
                                evidence=f"{rel}:{_line_of(text, m.start())}"))
    return hits


_ROUTE_RULE = "route-registration.yml"
# Method + path from a matched Go route call's text (the leading "/…" literal).
_GO_ROUTE_TEXT = re.compile(
    r'\.\s*(GET|POST|PUT|PATCH|DELETE|Handle|HandleFunc)\s*\(\s*"([/][^"]*)"')
_MOUNTS = {"USE", "GROUP", "HANDLE"}  # mounts/groups, not leaf routes


def route_registrations(repo_path: str | Path,
                        tier2_exclusions: list[str] | None = None,
                        stats: dict | None = None) -> list[RouteHit]:
    """Structural route extraction via ast-grep (route-registration.yml). Falls
    back to the transparent regex scan when ast-grep is unavailable, so the
    signal degrades rather than disappears; the fallback is disclosed in
    ``liveness()``'s report notes (and thus the discovery report).

    ``stats`` (when given) accumulates scan-cap hits for the regex fallback, so a
    truncated walk is disclosed rather than silently short. The ast-grep path
    scans independently and is not bounded by the doctor-owned file/byte caps."""
    tier2 = set(tier2_exclusions or [])
    if not astgrep.available():
        return _route_registrations_regex(repo_path, tier2, stats)
    hits: list[RouteHit] = []
    # Stable order so route rows (and any downstream cap/sample) are deterministic
    # regardless of ast-grep scan order.
    matches = sorted(astgrep.scan(repo_path, [astgrep.rule_path(_ROUTE_RULE)]),
                     key=lambda m: (m.file, m.line, m.rule_id, m.text))
    for match in matches:
        parts = PurePosixPath(match.file).parts
        if parts and parts[0] in tier2:
            continue
        if match.rule_id == "route-go":
            found = _GO_ROUTE_TEXT.search(match.text)
            if not found:
                continue
            method, path = found.group(1).upper(), found.group(2)
        else:  # route-js / route-ts / route-tsx expose method + path as metavars
            method, path = match.vars.get("M", "").upper(), match.vars.get("P", "")
        if method in _MOUNTS or not path.startswith("/"):
            continue
        hits.append(RouteHit(method=method, path=path,
                             evidence=f"{match.file}:{match.line}"))
    return hits


def _route_registrations_regex(repo_path: str | Path, tier2: set[str],
                               stats: dict | None = None) -> list[RouteHit]:
    root = Path(repo_path).expanduser().resolve()
    hits: list[RouteHit] = []
    for path in _iter_source(root, stats):
        if path.relative_to(root).parts and path.relative_to(root).parts[0] in tier2:
            continue
        text = _read(path, stats)
        rel = path.relative_to(root).as_posix()
        pattern = _GO_ROUTE if path.suffix == ".go" else _JS_ROUTE
        for m in pattern.finditer(text):
            method = m.group(1).upper()
            if method in _MOUNTS:
                continue
            hits.append(RouteHit(method=method, path=m.group(2),
                                 evidence=f"{rel}:{_line_of(text, m.start())}"))
    return hits


def _paths_by_base(calls: list[CallHit]) -> dict:
    """{base: set of normalized concrete-bearing call-path tuples} — the per-base
    call inventory the association is computed from (all-wildcard paths dropped)."""
    by_base: dict[str, set] = {}
    for c in calls:
        segs = _norm_segments(c.path)
        if any(s != "*" for s in segs):
            by_base.setdefault(c.base, set()).add(tuple(segs))
    return by_base


def liveness(frontend_repo, backends: list[tuple],
             internal_callers: dict | None = None) -> LivenessReport:
    """frontend_repo: path (or None). backends: list of (repo_id, path,
    tier2_exclusions). internal_callers: optional {repo_id: [CallHit]} for
    same-service internal calls (e.g. an MCP router calling other routes)."""
    report = LivenessReport(notes=[
        "RELIABLE output = the ui_calls inventory (every frontend→backend call "
        "with base + path + citation) and the `ui-called` rows (matches sharing "
        "a concrete path segment).",
        "LIMITATION: leaf route registrations lack their router MOUNT PREFIX "
        "(Express `app.use('/x', r)` / gin `r.Group(...)`), so `no-direct-path-"
        "match` is NOT an orphan/dead list — many such routes are live under a "
        "mount prefix this pass does not resolve. `match-ambiguous` = route "
        "normalized to all-wildcard (e.g. leaf `/:id`), unmatchable without the "
        "prefix. `base-unresolved` = a frontend call matches the route's path "
        "shape, but the caller's resolved base binds to a DIFFERENT backend or "
        "to none, so this backend is not credited (path shape alone never "
        "implies a caller). Nothing here is ever labeled 'dead': mobile/external/"
        "ops callers are invisible to repository evidence (standing disclaimer).",
        "match heuristic: version-prefix-tolerant, param wildcards, route is a "
        "prefix of the call, at least one concrete segment must agree.",
    ])
    report.astgrep = astgrep.probe().provenance()
    if backends and not astgrep.available():
        report.notes.append(
            "ROUTE EXTRACTION FALLBACK: ast-grep unavailable — route registrations "
            "came from the transparent regex scan, not the structural rule "
            "(reduced robustness; disclosed).")
    # Accumulates doctor-owned scan-cap hits across the frontend call scan and
    # any regex-fallback route scan, so a truncated walk is disclosed (57B-31:
    # a cap that silently shortens the canonical graph is a partial partition).
    scan_stats: dict = {"file_cap_hit": False, "oversized": 0}
    calls = ui_call_sites(frontend_repo, scan_stats) if frontend_repo else []
    report.ui_calls = calls
    norm_calls = [(_norm_segments(c.path), c) for c in calls]
    internal_callers = internal_callers or {}

    # Pass 1: extract each backend's routes once and collect its concrete-bearing
    # segments, so base->backend association sees the full route inventory before
    # any route is classified.
    backend_hits: dict[str, list] = {}
    backend_routes: list[tuple] = []
    for repo_id, path, tier2 in backends:
        routes = route_registrations(path, tier2, scan_stats)
        backend_hits[repo_id] = routes
        concrete = []
        for r in routes:
            rsegs = _norm_segments(r.path)
            if any(s != "*" for s in rsegs):
                concrete.append(rsegs)
        backend_routes.append((repo_id, concrete))
    base_backend, base_notes = resolve_base_backends(
        _paths_by_base(calls), backend_routes, _matches)
    if backends and frontend_repo:
        report.notes.extend(base_notes)

    # Pass 2: classify, crediting `ui-called` only to the backend the matching
    # caller's resolved base actually maps to.
    for repo_id, path, tier2 in backends:
        internal = internal_callers.get(repo_id, [])
        norm_internal = [(_norm_segments(c.path), c) for c in internal]
        for route in backend_hits[repo_id]:
            rsegs = _norm_segments(route.path)
            # A route with no concrete segment (e.g. leaf `/:id` shorn of its
            # router mount prefix) cannot be matched reliably — report it as
            # ambiguous rather than guess a caller or a false orphan.
            if not any(s != "*" for s in rsegs):
                report.rows.append(LivenessRow(
                    repo_id, route.method, route.path, route.evidence,
                    "match-ambiguous", []))
                continue
            matching = [c for segs, c in norm_calls if _matches(rsegs, segs)]
            here = sorted(c.evidence for c in matching
                          if base_backend.get(c.base) == repo_id)
            if here:
                report.rows.append(LivenessRow(
                    repo_id, route.method, route.path, route.evidence,
                    "ui-called", here[:3]))
                continue
            int_hits = [c.evidence for segs, c in norm_internal if _matches(rsegs, segs)]
            if int_hits:
                report.rows.append(LivenessRow(
                    repo_id, route.method, route.path, route.evidence,
                    "internal-called", int_hits[:3]))
                continue
            # Path shape matches, but the caller's base binds elsewhere/nowhere.
            if matching:
                report.rows.append(LivenessRow(
                    repo_id, route.method, route.path, route.evidence,
                    "base-unresolved", []))
                continue
            report.rows.append(LivenessRow(
                repo_id, route.method, route.path, route.evidence,
                "no-direct-path-match", []))

    if scan_stats["file_cap_hit"]:
        report.notes.append(
            f"COVERAGE CAP: source scan stopped after {_MAX_FILES} files — call "
            "sites / route registrations beyond the cap were NOT scanned (incomplete).")
    if scan_stats["oversized"]:
        report.notes.append(
            f"COVERAGE CAP: {scan_stats['oversized']} source file(s) exceeded "
            f"{_MAX_BYTES} bytes and were NOT scanned (incomplete).")
    return report
