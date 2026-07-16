"""Deterministic, domain-neutral parsers for the validated OSS tools.

These functions summarize tool structure only. They never name business modules,
judge findings, or infer integrations from vendor names.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .targetspec import RepoTarget


def _json(text: str) -> Any:
    return json.loads(text)


def validate_json(text: str, _exit: int) -> str:
    try:
        _json(text)
        return ""
    except Exception as exc:
        return f"invalid JSON: {exc}"


def validate_scc(text: str, _exit: int) -> str:
    try:
        data = _json(text)
    except Exception as exc:
        return f"invalid scc JSON: {exc}"
    if not isinstance(data, list) or not all(
        isinstance(x, dict) and "Name" in x and "Code" in x for x in data
    ):
        return "scc output must be a list of language rows"
    return ""


def scc_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    data = _json(stdout)
    if not isinstance(data, list):
        raise ValueError("scc JSON must be a list")
    lines = ["language\tfiles\tlines\tcode\tcomments\tcomplexity"]
    for row in sorted(data, key=lambda x: (-int(x.get("Code", 0)), str(x.get("Name", "")))):
        lines.append(
            "\t".join(str(row.get(k, 0)) for k in
                      ("Name", "Count", "Lines", "Code", "Comment", "Complexity"))
        )
    if stderr.strip():
        lines.extend(["", "stderr:", stderr.strip()])
    return "\n".join(lines)


def nonempty_output(text: str, _exit: int) -> str:
    return "" if text.strip() else "expected non-empty output"


def lizard_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    warning = re.compile(r"\bwarning\b|CCN|cyclomatic|function length", re.I)
    selected = [line for line in stdout.splitlines() if warning.search(line)]
    tail = stdout.splitlines()[-8:]
    out = ["### complexity warnings"] + selected[:180] + ["", "### summary"] + tail
    if stderr.strip():
        out += ["", "### stderr", *stderr.splitlines()[:30]]
    return "\n".join(out)


_CLONE_HEAD = re.compile(r"Clone found \(([^)]+)\)")
_CLONE_A = re.compile(r"^\s*-\s+(\S.*?)\s+\[(\d+):\d+\s*-\s*(\d+):\d+\]\s*\((\d+)\s*lines")
_CLONE_B = re.compile(r"^\s{2,}(\S.*?)\s+\[(\d+):\d+\s*-\s*(\d+):\d+\]\s*$")


def _parse_clone_pairs(stdout: str) -> list[dict]:
    """Console clone blocks -> structured pairs (both sides, line span)."""
    pairs: list[dict] = []
    lines = stdout.splitlines()
    fmt = ""
    for i, line in enumerate(lines):
        head = _CLONE_HEAD.search(line)
        if head:
            fmt = head.group(1)
            continue
        a = _CLONE_A.match(line)
        if not a:
            continue
        b = _CLONE_B.match(lines[i + 1]) if i + 1 < len(lines) else None
        if not b:
            continue
        pairs.append({
            "format": fmt, "span": int(a.group(4)),
            "a_file": a.group(1), "a_from": int(a.group(2)), "a_to": int(a.group(3)),
            "b_file": b.group(1), "b_from": int(b.group(2)), "b_to": int(b.group(3)),
        })
    return pairs


def jscpd_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    formats = Counter(re.findall(r"Clone found \(([^)]+)\)", stdout))
    summary = [line.strip() for line in stdout.splitlines()
               if re.search(r"Found \d+ clones|duplicated lines|duplication", line, re.I)]
    pairs = _parse_clone_pairs(stdout)
    # Cross-FILE clones are the change-friction signal (a fix in one file must be
    # mirrored in another); same-file clones are lower value. Rank by span.
    cross = [p for p in pairs if p["a_file"] != p["b_file"]]
    cross.sort(key=lambda p: -p["span"])
    out = ["### clone formats"]
    out += [f"{name}\t{count}" for name, count in sorted(formats.items(), key=lambda x: (-x[1], x[0]))]
    out += ["", f"### cross-file clone pairs (top {min(len(cross), 60)} of {len(cross)}; "
                f"lines\ta_file:a_from-a_to\tb_file:b_from-b_to)"]
    for p in cross[:60]:
        out.append(f"{p['span']}\t{p['a_file']}:{p['a_from']}-{p['a_to']}\t"
                   f"{p['b_file']}:{p['b_from']}-{p['b_to']}")
    out += ["", f"### summary ({len(pairs)} total pairs, "
                f"{len(pairs) - len(cross)} same-file)", *summary[-30:]]
    if stderr.strip():
        out += ["", "### stderr", *stderr.splitlines()[:30]]
    return "\n".join(out)


def validate_jscpd(text: str, _exit: int) -> str:
    return "" if re.search(r"Found \d+ clones\.", text) \
        else "jscpd console summary is missing 'Found N clones'"


def validate_depcruise(text: str, _exit: int) -> str:
    try:
        data = _json(text)
    except Exception as exc:
        return f"invalid dependency-cruiser JSON: {exc}"
    return "" if isinstance(data, dict) and isinstance(data.get("modules"), list) \
        else "expected an object with a modules list"


def _is_internal_spec(module: str) -> bool:
    """A relative/alias import that SHOULD resolve inside the repo. External
    npm specifiers (bare/scoped package names) are not internal — their
    non-resolution under `--do-not-follow node_modules` is a classification
    limitation, not a broken internal graph."""
    if not module:
        return False
    if module.startswith((".", "/")):
        return True
    # Common tsconfig path-alias roots for in-repo imports.
    first = module.split("/", 1)[0]
    return first in {"src", "app", "lib", "@", "@src", "@app"} or module.startswith("~/")


def depcruise_stats(text: str):
    """Returns (modules, edges, unresolved, circular, externals,
    internal_edges, internal_unresolved)."""
    data = _json(text)
    modules = data.get("modules", [])
    edges = unresolved = circular = 0
    internal_edges = internal_unresolved = 0
    externals: set[str] = set()
    for module in modules:
        for dep in module.get("dependencies", []):
            edges += 1
            spec = str(dep.get("module", ""))
            internal = _is_internal_spec(spec)
            if internal:
                internal_edges += 1
            if dep.get("couldNotResolve"):
                unresolved += 1
                if internal:
                    internal_unresolved += 1
            if dep.get("circular"):
                circular += 1
            if any(str(kind).startswith("npm") for kind in dep.get("dependencyTypes", [])):
                externals.add(spec)
    return (len(modules), edges, unresolved, circular,
            sorted(x for x in externals if x), internal_edges, internal_unresolved)


def depcruise_degraded(_target: RepoTarget, combined: str, _exit: int) -> str:
    # stdout is placed before the sentinel by the executor.
    stdout = combined.split("\n### STDERR ###\n", 1)[0]
    try:
        _, _, _, _, _, internal_edges, internal_unresolved = depcruise_stats(stdout)
    except Exception:
        return ""
    # Coverage is measured over INTERNAL edges only — unresolved external npm
    # subpaths (antd/es/*, @dnd-kit/*) are a known limit of the safe
    # `--no-config`/`--do-not-follow` run, not a hole in the coupling graph.
    if internal_edges and internal_unresolved / internal_edges > 0.15:
        return ("dependency coverage partial: "
                f"{internal_unresolved}/{internal_edges} INTERNAL edges unresolved (>15%)")
    return ""


def depcruise_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    target = _target
    (modules, edges, unresolved, circular, externals,
     internal_edges, internal_unresolved) = depcruise_stats(stdout)
    production: list[str] = []
    dev_test: list[str] = []
    unclassified: list[str] = []
    package_file = Path(target.path) / "package.json"
    package_data = _json(package_file.read_text("utf-8")) if package_file.is_file() else {}
    deps = set(package_data.get("dependencies", {}))
    dev_deps = set(package_data.get("devDependencies", {}))

    def package_root(value: str) -> str:
        parts = value.split("/")
        return "/".join(parts[:2]) if value.startswith("@") else parts[0]

    for value in externals:
        root = package_root(value)
        if root in deps:
            production.append(value)
        elif root in dev_deps:
            dev_test.append(value)
        else:
            unclassified.append(value)
    out = [
        f"modules: {modules}", f"edges: {edges}", f"unresolved_edges: {unresolved}",
        f"internal_edges: {internal_edges}",
        f"internal_unresolved_edges: {internal_unresolved} "
        f"(coupling-graph coverage; external npm subpaths excluded)",
        f"circular_edges: {circular}", "", "external_imports_production:", *production,
        "", "external_imports_dev_test:", *dev_test,
        "", "external_imports_unclassified:", *unclassified,
    ]
    if stderr.strip():
        out += ["", "stderr:", *stderr.splitlines()[:30]]
    return "\n".join(out)


_COMPILE_FAILURE = re.compile(
    r"\(compile\)|could not (?:load|analyze)|(?:^|\n)-: |no (?:Go|buildable Go) "
    r"(?:source )?files",
    re.I,
)


def staticcheck_degraded(_target: RepoTarget, combined: str, _exit: int) -> str:
    return "compile/load failures detected; staticcheck coverage is incomplete" \
        if _COMPILE_FAILURE.search(combined) else ""


def staticcheck_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    findings = [x for x in stdout.splitlines() if not re.search(r"(^|/)docs/", x)]
    return "\n".join([
        f"findings_total: {len(stdout.splitlines())}",
        f"findings_after_generated_filter: {len(findings)}",
        *findings[:180],
        "", "stderr:", *stderr.splitlines()[:30],
    ])


def decode_json_stream(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    idx = 0
    values: list[dict[str, Any]] = []
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        value, idx = decoder.raw_decode(text, idx)
        if not isinstance(value, dict):
            raise ValueError("stream item is not an object")
        values.append(value)
    return values


def validate_go_list(text: str, _exit: int) -> str:
    try:
        values = decode_json_stream(text)
    except Exception as exc:
        return f"invalid go list JSON stream: {exc}"
    return "" if values and all("ImportPath" in x for x in values) \
        else "expected one or more package objects with ImportPath"


def _go_module(target: RepoTarget) -> str:
    gomod = Path(target.path) / "go.mod"
    for line in gomod.read_text("utf-8", errors="replace").splitlines():
        if line.startswith("module "):
            return line.split(None, 1)[1].strip()
    raise ValueError("go.mod has no module directive")


def go_list_view(target: RepoTarget, stdout: str, stderr: str) -> str:
    packages = decode_json_stream(stdout)
    module = _go_module(target)
    internal = {
        p["ImportPath"]: p for p in packages
        if p.get("ImportPath", "") == module
        or p.get("ImportPath", "").startswith(module + "/")
    }
    edges: list[tuple[str, str]] = []
    external: set[str] = set()
    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    for src, package in internal.items():
        for dst in package.get("Imports", []):
            if dst in internal:
                edges.append((src, dst)); fan_out[src] += 1; fan_in[dst] += 1
            elif "." in dst.split("/", 1)[0]:
                external.add(dst)
    out = [
        f"module: {module}", f"internal_packages: {len(internal)}",
        f"internal_edges: {len(edges)}", "", "top_fan_out:",
        *[f"{n}\t{p}" for p, n in sorted(fan_out.items(), key=lambda x: (-x[1], x[0]))[:20]],
        "", "top_fan_in:",
        *[f"{n}\t{p}" for p, n in sorted(fan_in.items(), key=lambda x: (-x[1], x[0]))[:20]],
        "", "external_imports:", *sorted(external),
    ]
    if stderr.strip():
        # Go's stat-cache warning embeds a random temporary-file suffix; retain
        # the warning while removing only that volatile identifier.
        stable_err = re.sub(r"(\.info)\d+(\.tmp)", r"\1<TMP>\2", stderr)
        out += ["", "stderr:", *stable_err.splitlines()[:30]]
    return "\n".join(out)


def osv_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    important = [line for line in stdout.splitlines()
                 if re.search(r"affected by|can be fixed|packages?$|GHSA-|CVE-", line, re.I)]
    return "\n".join(important[:220] + (["", "stderr:", *stderr.splitlines()[:30]] if stderr.strip() else []))


def validate_npm_outdated(text: str, exit_code: int) -> str:
    if not text.strip():
        return "" if exit_code == 0 else "empty npm output is valid only for exit 0"
    try:
        data = _json(text)
    except Exception as exc:
        return f"invalid npm JSON: {exc}"
    if not isinstance(data, dict):
        return "npm outdated output must be an object"
    error = data.get("error")
    if error is not None:
        return "npm emitted a structured error object"
    if any(key in data for key in ("code", "summary", "detail")) and not all(
        isinstance(value, dict) for value in data.values()
    ):
        return "npm output resembles an error response"
    if not all(isinstance(value, dict) for value in data.values()):
        return "npm package rows must be objects"
    return ""


def npm_outdated_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    data = _json(stdout) if stdout.strip() else {}
    out = [f"outdated_packages: {len(data)}", "package\tcurrent\twanted\tlatest\ttype"]
    for name in sorted(data):
        row = data[name] if isinstance(data[name], dict) else {}
        out.append("\t".join(str(x) for x in (
            name, row.get("current", ""), row.get("wanted", ""),
            row.get("latest", ""), row.get("type", ""))))
    if stderr.strip():
        out += ["", "stderr:", *stderr.splitlines()[:30]]
    return "\n".join(out)


def _yarn_objects(text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    values = []
    for line in text.splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("NDJSON item is not an object")
        values.append(value)
    return values


def validate_yarn_outdated(text: str, exit_code: int) -> str:
    try:
        values = _yarn_objects(text)
    except Exception as exc:
        return f"invalid yarn NDJSON: {exc}"
    if any(x.get("type") == "error" for x in values):
        return "yarn emitted an error object"
    if not values and exit_code == 0:
        return ""  # Yarn 1 valid all-current representation.
    if any(x.get("type") == "table" for x in values):
        return ""
    return "expected a table object, or an empty exit-0 stream"


def yarn_outdated_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    table = next((x for x in _yarn_objects(stdout) if x.get("type") == "table"), None)
    body = table.get("data", {}).get("body", []) if table else []
    out = [f"outdated_packages: {len(body)}", "package\tcurrent\twanted\tlatest\ttype"]
    out += ["\t".join(str(x) for x in row[:5]) for row in sorted(body)]
    if stderr.strip():
        out += ["", "stderr:", *stderr.splitlines()[:30]]
    return "\n".join(out)


def validate_history(text: str, _exit: int) -> str:
    try:
        data = _json(text)
    except Exception as exc:
        return f"invalid history JSON: {exc}"
    required = {"backend", "coverage_status", "history_completeness", "churn", "coupling", "ownership"}
    return "" if isinstance(data, dict) and required <= set(data) \
        else "history output is missing required fields"


def history_degraded(_target: RepoTarget, combined: str, _exit: int) -> str:
    stdout = combined.split("\n### STDERR ###\n", 1)[0]
    try:
        data = _json(stdout)
    except Exception:
        return ""
    return "history coverage partial: " + str(data.get("backend", "unknown")) \
        if data.get("coverage_status") == "partial" else ""


def history_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    data = _json(stdout)
    complete = data.get("history_completeness", {})
    out = [
        f"backend: {data.get('backend')}", f"coverage_status: {data.get('coverage_status')}",
        f"since: {data.get('since')}", f"commits_used: {data.get('commits_used')}",
        f"shallow: {complete.get('shallow')}",
        f"bulk_changesets_excluded_from_coupling: {data.get('bulk_changesets_excluded_from_coupling')}",
        "", "churn:",
    ]
    out += [f"{x['commits']}\t{x['total_lines']}\t{x['file']}" for x in data.get("churn", [])]
    out += ["", "coupling:"]
    out += [f"{x['coupling_pct']}\t{x['shared_commits']}\t{x['file_a']}\t{x['file_b']}"
            for x in data.get("coupling", [])]
    out += ["", "ownership:"]
    out += [f"{x['distinct_committers']}\t{x['dominant_commit_share']}\t"
            f"{x['dominant_churn_share']}\t{x['file']}" for x in data.get("ownership", [])]
    if data.get("uncertain_name_matches"):
        out += ["", "uncertain_name_matches:", *data["uncertain_name_matches"]]
    if stderr.strip():
        out += ["", "stderr:", *stderr.splitlines()[:30]]
    return "\n".join(out)
