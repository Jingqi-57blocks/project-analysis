"""Route liveness (change-friction round): which backend routes are actually
called, and by whom.

Mechanical join of frontend call sites against backend route registrations —
the evidence that answers "which parts of a parallel-rewritten service are
still real". This module RECORDS and MATCHES with disclosed heuristics; it
never concludes "dead". A route with no caller found in the ANALYZED repos is
`no-caller-found` (candidate unused) — mobile apps, external API consumers,
and ops scripts are invisible to repository evidence (standing disclaimer).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {"node_modules", "vendor", ".git", "dist", "build", "coverage"}
_MAX_FILES = 6000
_MAX_BYTES = 262_144
_SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go"}

# Frontend HTTP call sites: `${base}/path...` or `base + '/path'` inside a
# template literal — capture the base identifier and the literal path head.
_UI_CALL = re.compile(
    r"\$\{\s*(?:config\.)?(\w+)\s*\}(/[A-Za-z0-9_\-./:${}]*)")
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

    def calls_by_base(self) -> dict:
        """The reliable migration ledger: how many distinct paths the frontend
        calls per base identifier (mainApi vs appRunnerApi vs ...). Answers
        'which backend is the UI actually using' without needing mount
        prefixes."""
        by_base: dict[str, set] = {}
        for c in self.ui_calls:
            by_base.setdefault(c.base, set()).add("/".join(_norm_segments(c.path)))
        return {base: sorted(paths) for base, paths in sorted(by_base.items())}


def _iter_source(root: Path):
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
                return
            yield entry


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_BYTES:
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


def ui_call_sites(repo_path: str | Path) -> list[CallHit]:
    root = Path(repo_path).expanduser().resolve()
    hits: list[CallHit] = []
    for path in _iter_source(root):
        text = _read(path)
        if "${" not in text:
            continue
        rel = path.relative_to(root).as_posix()
        for m in _UI_CALL.finditer(text):
            base, raw = m.group(1), m.group(2)
            if len(_norm_segments(raw)) == 0:
                continue
            hits.append(CallHit(base=base, path=raw,
                                evidence=f"{rel}:{_line_of(text, m.start())}"))
    return hits


def route_registrations(repo_path: str | Path,
                        tier2_exclusions: list[str] | None = None) -> list[RouteHit]:
    root = Path(repo_path).expanduser().resolve()
    tier2 = set(tier2_exclusions or [])
    hits: list[RouteHit] = []
    for path in _iter_source(root):
        if path.relative_to(root).parts and path.relative_to(root).parts[0] in tier2:
            continue
        text = _read(path)
        rel = path.relative_to(root).as_posix()
        pattern = _GO_ROUTE if path.suffix == ".go" else _JS_ROUTE
        for m in pattern.finditer(text):
            method = m.group(1).upper()
            if method in {"USE", "GROUP", "HANDLE"}:  # mounts/groups, not leaf routes
                continue
            hits.append(RouteHit(method=method, path=m.group(2),
                                 evidence=f"{rel}:{_line_of(text, m.start())}"))
    return hits


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
        "prefix. Nothing here is ever labeled 'dead': mobile/external/ops "
        "callers are invisible to repository evidence (standing disclaimer).",
        "match heuristic: version-prefix-tolerant, param wildcards, route is a "
        "prefix of the call, at least one concrete segment must agree.",
    ])
    calls = ui_call_sites(frontend_repo) if frontend_repo else []
    report.ui_calls = calls
    norm_calls = [(_norm_segments(c.path), c) for c in calls]
    internal_callers = internal_callers or {}

    for repo_id, path, tier2 in backends:
        internal = internal_callers.get(repo_id, [])
        norm_internal = [(_norm_segments(c.path), c) for c in internal]
        for route in route_registrations(path, tier2):
            rsegs = _norm_segments(route.path)
            # A route with no concrete segment (e.g. leaf `/:id` shorn of its
            # router mount prefix) cannot be matched reliably — report it as
            # ambiguous rather than guess a caller or a false orphan.
            if not any(s != "*" for s in rsegs):
                report.rows.append(LivenessRow(
                    repo_id, route.method, route.path, route.evidence,
                    "match-ambiguous", []))
                continue
            ui_hits = [c.evidence for segs, c in norm_calls if _matches(rsegs, segs)]
            if ui_hits:
                report.rows.append(LivenessRow(
                    repo_id, route.method, route.path, route.evidence,
                    "ui-called", ui_hits[:3]))
                continue
            int_hits = [c.evidence for segs, c in norm_internal if _matches(rsegs, segs)]
            if int_hits:
                report.rows.append(LivenessRow(
                    repo_id, route.method, route.path, route.evidence,
                    "internal-called", int_hits[:3]))
                continue
            report.rows.append(LivenessRow(
                repo_id, route.method, route.path, route.evidence,
                "no-direct-path-match", []))
    return report
