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

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import astgrep
from ..exclusions import SOURCE_EXT as _SOURCE_EXT
from .base_map import resolve_base_backends

_SKIP_DIRS = {"node_modules", "vendor", ".git", "dist", "build", "coverage"}
_MAX_FILES = 6000
_MAX_BYTES = 262_144

# Frontend HTTP call sites: `${base}/path...` — capture whether the base was
# written `config.X` (explicit global base) vs a bare `X` (which may be a LOCAL
# alias of a config base, resolved per-file below), plus the literal path head.
_UI_CALL = re.compile(
    r"\$\{\s*(config\.)?(\w+)\s*\}(/[A-Za-z0-9_\-./:${}]*)")
_UI_METHOD_BEFORE = re.compile(
    r"(?:^|[.\s(])(get|post|put|patch|delete)\s*\(\s*[`'\"]?\s*$", re.I)
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

# A separate, conservative adapter for router variables produced by Go's
# ``Group`` pattern. The generic route inventory retains each registration's
# declared path; this adapter adds a source-proven full path only when the
# receiver-to-group chain is statically visible in the same file.
_GO_GROUP_ASSIGN = re.compile(
    r"\b(?P<child>[A-Za-z_]\w*)\s*(?::=|=)\s*"
    r"(?P<parent>[A-Za-z_]\w*)\.Group\s*\(\s*\"(?P<path>/[^\"]*)\"")
_GO_GROUP_ENDPOINT = re.compile(
    r"\b(?P<receiver>[A-Za-z_]\w*)\."
    r"(?P<method>GET|POST|PUT|PATCH|DELETE)\s*\(\s*\"(?P<path>[^\"]*)\"")

# This parser only follows the final handler argument of endpoint calls which
# the route inventory has already recognized.  It never turns a route-shaped
# method call into a route by itself.
_ROUTE_CALL_START = re.compile(
    r"\b(?P<receiver>[A-Za-z_$][\w$]*)\s*\.\s*"
    r"(?P<method>GET|POST|PUT|PATCH|DELETE|get|post|put|patch|delete)\s*\(")
_IDENTIFIER = re.compile(r"\b[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)?\b")
_GO_FUNC = re.compile(r"(?m)^\s*func\s+(?P<name>[A-Za-z_]\w*)\s*\(")
_JS_FUNC = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(")
_JS_VALUE = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\b")
_GO_IMPORT = re.compile(
    r"(?m)^\s*(?:(?P<alias>[A-Za-z_]\w*)\s+)?\"(?P<path>[^\"]+)\"\s*$")
_JS_IMPORT = re.compile(
    r"(?m)^\s*import\s+(?P<bindings>[^;\n]+?)\s+from\s+[\"'](?P<path>[^\"']+)[\"']")
_KEYWORDS = frozenset({
    "async", "await", "const", "delete", "export", "false", "function", "new",
    "null", "return", "true", "undefined", "void",
})


@dataclass
class RouteHit:
    method: str
    path: str
    evidence: str  # repo-relative file:line


@dataclass(frozen=True)
class ComposedRouteHit:
    """A Go endpoint whose full path is justified by local group calls."""

    method: str
    path: str
    full_path: str
    evidence: str
    composition_evidence: tuple[str, ...]


@dataclass(frozen=True)
class RouteHandlerReference:
    """Syntactic handler references and exactly resolved local definitions.

    ``symbols`` are observed text tokens only.  An ``anchor`` is emitted only
    when its definition can be uniquely located in the analyzed repository.
    Callers must keep the two categories distinct.
    """

    method: str
    path: str
    evidence: str
    symbols: tuple[str, ...]
    anchors: tuple[tuple[str, str], ...]


@dataclass
class CallHit:
    base: str
    path: str
    evidence: str
    method: str = ""       # empty = path observed, HTTP method not proven


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
        """Static-reference ledger: distinct frontend call paths per configured
        base identifier. It establishes code references, never runtime use."""
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


def _join_route_path(prefix: str, suffix: str) -> str:
    """Join literal route parts without changing parameter/wildcard syntax."""
    parts = [part.strip("/") for part in (prefix, suffix) if part.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def compose_go_route_paths(repo_path: str | Path,
                           tier2_exclusions: list[str] | None = None,
                           stats: dict | None = None) -> tuple[ComposedRouteHit, ...]:
    """Resolve same-file Go ``Group`` receivers into complete endpoint paths.

    Every emitted row needs literal group assignments, a literal endpoint
    call, and an acyclic receiver chain in one file. Missing, duplicate, or
    dynamic chains are deliberately left unresolved rather than guessed.
    """
    root = Path(repo_path).expanduser().resolve()
    tier2 = set(tier2_exclusions or [])
    rows: list[ComposedRouteHit] = []
    for path in _iter_source(root, stats):
        relative = path.relative_to(root)
        if (relative.parts and relative.parts[0] in tier2) or path.suffix != ".go":
            continue
        text = _read(path, stats)
        if not text:
            continue
        rel = relative.as_posix()
        groups: dict[str, tuple[str, str, str]] = {}
        duplicates: set[str] = set()
        for match in _GO_GROUP_ASSIGN.finditer(text):
            child = match.group("child")
            group = (match.group("parent"), match.group("path"),
                     f"{rel}:{_line_of(text, match.start())}")
            if child in groups:
                duplicates.add(child)
            else:
                groups[child] = group
        for child in duplicates:
            groups.pop(child, None)
        if duplicates and stats is not None:
            stats["group_chain_ambiguous"] = stats.get("group_chain_ambiguous", 0) + len(duplicates)

        def resolve(receiver: str, seen: frozenset[str] = frozenset()) -> tuple[str, tuple[str, ...]] | None:
            if receiver in seen:
                return None
            group = groups.get(receiver)
            if group is None:
                return "", ()
            parent, segment, evidence = group
            parent_result = resolve(parent, seen | {receiver})
            if parent_result is None:
                return None
            parent_path, parent_evidence = parent_result
            return _join_route_path(parent_path, segment), parent_evidence + (evidence,)

        for match in _GO_GROUP_ENDPOINT.finditer(text):
            receiver, raw_path = match.group("receiver"), match.group("path")
            # An empty literal is a route only when its receiver was visibly
            # created by Group; arbitrary method calls never become endpoints.
            if (raw_path and not raw_path.startswith("/")) or receiver not in groups:
                continue
            composed = resolve(receiver)
            if composed is None:
                continue
            prefix, evidence_chain = composed
            rows.append(ComposedRouteHit(
                method=match.group("method").upper(), path=raw_path,
                full_path=_join_route_path(prefix, raw_path),
                evidence=f"{rel}:{_line_of(text, match.start())}",
                composition_evidence=evidence_chain,
            ))
    return tuple(sorted(rows, key=lambda row: (row.evidence, row.method, row.full_path)))


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _call_arguments(text: str, open_paren: int) -> tuple[tuple[int, int], ...] | None:
    """Return top-level argument spans for one syntactically complete call.

    This is deliberately a small lexical reader rather than a language parser.
    It understands quoted strings and balanced delimiters so that a wrapper
    around a handler (or a middleware list before it) does not split the final
    argument at an inner comma.  Dynamic/unclosed expressions return ``None``.
    """
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    depth = 1
    start = open_paren + 1
    spans: list[tuple[int, int]] = []
    quote = ""
    escaped = False
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] == "*":
            end_comment = text.find("*/", index + 2)
            if end_comment < 0:
                return None
            index = end_comment + 2
            continue
        if char in {"'", '\"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                if text[start:index].strip():
                    spans.append((start, index))
                return tuple(spans)
        elif char == "," and depth == 1:
            spans.append((start, index))
            start = index + 1
        index += 1
    return None


def _literal_route_path(text: str, span: tuple[int, int]) -> str | None:
    raw = text[span[0]:span[1]].strip()
    if len(raw) < 2 or raw[0] not in {"'", '\"'} or raw[-1] != raw[0]:
        return None
    value = raw[1:-1]
    return value if value.startswith("/") or value == "" else None


def _handler_symbols(text: str, span: tuple[int, int]) -> tuple[str, ...]:
    """Extract identifier-shaped references, not arbitrary expression text."""
    symbols = {
        match.group(0).replace(" ", "")
        for match in _IDENTIFIER.finditer(text[span[0]:span[1]])
        if match.group(0).replace(" ", "") not in _KEYWORDS
    }
    return tuple(sorted(symbols))


def _route_calls(text: str, relative: str) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    rows: list[tuple[str, str, str, tuple[str, ...]]] = []
    for match in _ROUTE_CALL_START.finditer(text):
        arguments = _call_arguments(text, match.end() - 1)
        if arguments is None or len(arguments) < 2:
            continue
        path = _literal_route_path(text, arguments[0])
        if path is None:
            continue
        symbols = _handler_symbols(text, arguments[-1])
        rows.append((match.group("method").upper(), path,
                     f"{relative}:{_line_of(text, match.start())}", symbols))
    return tuple(rows)


def _definitions(text: str, suffix: str) -> dict[str, tuple[int, ...]]:
    patterns = (_GO_FUNC,) if suffix == ".go" else (_JS_FUNC, _JS_VALUE)
    found: dict[str, set[int]] = {}
    for pattern in patterns:
        for match in pattern.finditer(text):
            found.setdefault(match.group("name"), set()).add(_line_of(text, match.start("name")))
    return {name: tuple(sorted(lines)) for name, lines in found.items()}


def _go_module_path(root: Path) -> str:
    try:
        for line in (root / "go.mod").read_text("utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("module "):
                return stripped.removeprefix("module ").strip()
    except OSError:
        pass
    return ""


def _go_imports(text: str) -> dict[str, str]:
    """Map explicit or conventional Go import aliases to their import paths."""
    rows: dict[str, str] = {}
    block = re.search(r"(?ms)^\s*import\s*\((?P<body>.*?)^\s*\)", text)
    sources = [block.group("body")] if block else []
    for match in re.finditer(r'(?m)^\s*import\s+"(?P<path>[^"]+)"', text):
        sources.append(f'"{match.group("path")}"')
    for source in sources:
        for match in _GO_IMPORT.finditer(source):
            path = match.group("path")
            alias = match.group("alias") or path.rstrip("/").rsplit("/", 1)[-1]
            if alias not in {"_", "."}:
                rows.setdefault(alias, path)
    return rows


def _source_candidates(relative: str) -> tuple[str, ...]:
    path = Path(relative)
    if path.suffix:
        return (path.as_posix(),)
    return tuple((Path(relative + suffix)).as_posix()
                 for suffix in (".ts", ".tsx", ".js", ".jsx")) + (
                     (path / "index.ts").as_posix(), (path / "index.tsx").as_posix(),
                     (path / "index.js").as_posix(), (path / "index.jsx").as_posix(),
                 )


def _js_import_bindings(text: str, relative: str) -> dict[str, tuple[str, str]]:
    """Map a simple local import binding to (source file, exported name)."""
    rows: dict[str, tuple[str, str]] = {}
    base = Path(relative).parent
    for match in _JS_IMPORT.finditer(text):
        imported = match.group("path")
        if not imported.startswith("."):
            continue
        resolved = posixpath.normpath((base / imported).as_posix())
        bindings = match.group("bindings").strip()
        if bindings.startswith("{") and bindings.endswith("}"):
            for entry in bindings[1:-1].split(","):
                bits = [part.strip() for part in entry.split(" as ", 1)]
                if not bits or not bits[0]:
                    continue
                rows[bits[-1]] = (resolved, bits[0])
        elif re.fullmatch(r"[A-Za-z_$][\w$]*", bindings):
            rows[bindings] = (resolved, "default")
    return rows


def _unique_anchor(candidates: list[tuple[str, int]]) -> tuple[str, int] | None:
    return candidates[0] if len(candidates) == 1 else None


def _resolve_go_symbol(symbol: str, *, relative: str, text: str,
                       sources: dict[str, str], module_path: str) -> tuple[str, int] | None:
    pieces = symbol.split(".")
    if len(pieces) == 1:
        return _unique_anchor([(relative, line) for line in _definitions(text, ".go").get(symbol, ())])
    if len(pieces) != 2 or not module_path:
        return None
    imported = _go_imports(text).get(pieces[0])
    prefix = module_path.rstrip("/") + "/"
    if not imported or not imported.startswith(prefix):
        return None
    package_dir = imported.removeprefix(prefix).strip("/")
    candidates: list[tuple[str, int]] = []
    for candidate_relative, candidate_text in sources.items():
        path = Path(candidate_relative)
        if path.suffix != ".go" or path.parent.as_posix() != package_dir:
            continue
        candidates.extend((candidate_relative, line)
                          for line in _definitions(candidate_text, ".go").get(pieces[1], ()))
    return _unique_anchor(candidates)


def _resolve_js_symbol(symbol: str, *, relative: str, text: str,
                       sources: dict[str, str]) -> tuple[str, int] | None:
    if "." in symbol:
        return None
    direct = _unique_anchor([(relative, line)
                             for line in _definitions(text, Path(relative).suffix).get(symbol, ())])
    if direct is not None:
        return direct
    binding = _js_import_bindings(text, relative).get(symbol)
    if binding is None:
        return None
    imported, exported = binding
    if exported == "default":
        return None
    candidates: list[tuple[str, int]] = []
    for candidate_relative in _source_candidates(imported):
        candidate_text = sources.get(candidate_relative)
        if candidate_text is None:
            continue
        candidates.extend((candidate_relative, line) for line in
                          _definitions(candidate_text, Path(candidate_relative).suffix).get(exported, ()))
    return _unique_anchor(candidates)


def route_handler_references(repo_path: str | Path,
                             tier2_exclusions: list[str] | None = None,
                             stats: dict | None = None) -> tuple[RouteHandlerReference, ...]:
    """Recover exact local handler anchors for literal endpoint registrations.

    A handler spelling without a uniquely located local definition remains a
    reference only.  This provides an explicit graph frontier instead of a
    fabricated implementation edge for wrappers, dynamic dispatch, external
    packages, or unsupported source shapes.
    """
    root = Path(repo_path).expanduser().resolve()
    tier2 = set(tier2_exclusions or [])
    sources: dict[str, str] = {}
    for path in _iter_source(root, stats):
        relative = path.relative_to(root)
        if (relative.parts and relative.parts[0] in tier2) or path.suffix not in {".go", ".js", ".jsx", ".ts", ".tsx"}:
            continue
        text = _read(path, stats)
        if text:
            sources[relative.as_posix()] = text
    module_path = _go_module_path(root)
    rows: list[RouteHandlerReference] = []
    for relative, text in sorted(sources.items()):
        suffix = Path(relative).suffix
        for method, path, evidence, symbols in _route_calls(text, relative):
            anchors: list[tuple[str, str]] = []
            for symbol in symbols:
                target = (_resolve_go_symbol(symbol, relative=relative, text=text,
                                             sources=sources, module_path=module_path)
                          if suffix == ".go" else
                          _resolve_js_symbol(symbol, relative=relative, text=text, sources=sources))
                if target is not None:
                    target_relative, line = target
                    anchors.append((symbol, f"{target_relative}:{line}"))
            rows.append(RouteHandlerReference(
                method=method, path=path, evidence=evidence, symbols=symbols,
                anchors=tuple(sorted(set(anchors))),
            ))
    return tuple(sorted(rows, key=lambda row: (row.evidence, row.method, row.path, row.symbols)))


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
            prefix = text[max(0, m.start() - 120):m.start()]
            method_match = _UI_METHOD_BEFORE.search(prefix)
            hits.append(CallHit(base=base, path=raw,
                                evidence=f"{rel}:{_line_of(text, m.start())}",
                                method=(method_match.group(1).upper()
                                        if method_match else "")))
    return hits


_ROUTE_RULE = "route-registration.yml"
# Method + path from a matched Go route call's text (the leading "/…" literal).
_GO_ROUTE_TEXT = re.compile(
    r'\.\s*(GET|POST|PUT|PATCH|DELETE|Handle|HandleFunc|Group)\s*\(\s*"([/][^"]*)"')
_MOUNTS = {"USE", "GROUP"}  # unresolved mounts/groups, not leaf endpoints


def route_registrations(repo_path: str | Path,
                        tier2_exclusions: list[str] | None = None,
                        stats: dict | None = None,
                        include_mounts: bool = False) -> list[RouteHit]:
    """Structural route extraction via ast-grep (route-registration.yml). Falls
    back to the transparent regex scan when ast-grep is unavailable, so the
    signal degrades rather than disappears; the fallback is disclosed in
    ``liveness()``'s report notes (and thus the discovery report).

    ``stats`` (when given) accumulates scan-cap hits for the regex fallback, so a
    truncated walk is disclosed rather than silently short. The ast-grep path
    scans independently and is not bounded by the doctor-owned file/byte caps."""
    tier2 = set(tier2_exclusions or [])
    if not astgrep.available():
        return _route_registrations_regex(repo_path, tier2, stats, include_mounts)
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
        if (method in _MOUNTS and not include_mounts) or not path.startswith("/"):
            continue
        hits.append(RouteHit(method=method, path=path,
                             evidence=f"{match.file}:{match.line}"))
    return hits


def _route_registrations_regex(repo_path: str | Path, tier2: set[str],
                               stats: dict | None = None,
                               include_mounts: bool = False) -> list[RouteHit]:
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
            if method in _MOUNTS and not include_mounts:
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
        "HTTP method must also be structurally observed and compatible before a "
        "UI call is credited; unknown or conflicting methods remain method-unresolved.",
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
            bound_here = [c for c in matching if base_backend.get(c.base) == repo_id]
            here = sorted(c.evidence for c in bound_here
                          if c.method and c.method == route.method.upper())
            if here:
                report.rows.append(LivenessRow(
                    repo_id, route.method, route.path, route.evidence,
                    "ui-called", here[:3]))
                continue
            unknown_or_mismatch = sorted(c.evidence for c in bound_here)
            if unknown_or_mismatch:
                report.rows.append(LivenessRow(
                    repo_id, route.method, route.path, route.evidence,
                    "method-unresolved", unknown_or_mismatch[:3]))
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
