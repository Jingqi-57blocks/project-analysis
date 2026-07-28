"""Deterministic, domain-neutral parsers for the validated OSS tools.

These functions summarize tool structure only. They never name business modules,
judge findings, or infer integrations from vendor names.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import identity
from .targetspec import RepoTarget, TargetSpec, stable_repo_id


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


def scc_metrics(_target: RepoTarget, stdout: str, _stderr: str) -> dict:
    data = _json(stdout)
    totals = {key: 0 for key in ("files", "lines", "code", "comments", "complexity")}
    languages = []
    for row in sorted(data, key=lambda item: str(item.get("Name", ""))):
        values = {
            "language": str(row.get("Name", "")),
            "files": int(row.get("Count", 0)),
            "lines": int(row.get("Lines", 0)),
            "code": int(row.get("Code", 0)),
            "comments": int(row.get("Comment", 0)),
            "complexity": int(row.get("Complexity", 0)),
        }
        languages.append(values)
        for key in totals:
            totals[key] += values[key]
    return {"kind": "scc", "totals": totals, "languages": languages}


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


def _repository_references(targets: list[RepoTarget]) -> dict[str, str]:
    paths = [str(Path(target.path).expanduser().resolve()) for target in targets]
    workspace = paths[0] if len(paths) == 1 else os.path.commonpath(paths)
    mapping = identity.build(
        TargetSpec(targets), workspace_root=workspace,
        project_id=stable_repo_id(str(workspace)))
    return {item.internal_id: item.reference for item in mapping.repositories}


def _clone_endpoint(path_text: str, targets: list[RepoTarget],
                    references: dict[str, str]) -> dict:
    raw = path_text.strip()
    path = Path(raw).expanduser()
    display_path = raw
    matches: list[tuple[str, str]] = []
    if path.is_absolute():
        resolved = path.resolve()
        display_path = "<outside-target>"
        for target in targets:
            root = Path(target.path).expanduser().resolve()
            scan_roots = target.root_paths()
            if (resolved == root or resolved.is_relative_to(root)) and any(
                    resolved == scan_root or resolved.is_relative_to(scan_root)
                    for scan_root in scan_roots):
                matches.append((references[target.repo_id],
                                resolved.relative_to(root).as_posix()))
    else:
        for target in targets:
            repo_root = Path(target.path).expanduser().resolve()
            scan_roots = target.root_paths()
            candidates = [repo_root / path, *(scan_root / path for scan_root in scan_roots)]
            for candidate in candidates:
                resolved = candidate.resolve()
                if (resolved.is_file() and resolved.is_relative_to(repo_root) and
                        any(resolved == scan_root or resolved.is_relative_to(scan_root)
                            for scan_root in scan_roots)):
                    matches.append((references[target.repo_id],
                                    resolved.relative_to(repo_root).as_posix()))
    unique = sorted(set(matches))
    return {
        "raw": raw,
        "repository_ref": unique[0][0] if len(unique) == 1 else "",
        "repository_candidates": sorted({repository_ref
                                         for repository_ref, _ in unique}),
        "path": unique[0][1] if len(unique) == 1 else display_path,
        "resolved": len(unique) == 1,
    }


def _qualify_clone_pairs(pairs: list[dict], targets: list[RepoTarget]) -> list[dict]:
    references = _repository_references(targets)
    qualified = []
    for pair in pairs:
        row = dict(pair)
        row["a"] = _clone_endpoint(pair["a_file"], targets, references)
        row["b"] = _clone_endpoint(pair["b_file"], targets, references)
        if not row["a"]["resolved"] or not row["b"]["resolved"]:
            row["scope"] = "ambiguous"
        elif row["a"]["repository_ref"] == row["b"]["repository_ref"]:
            row["scope"] = "within-repo"
        else:
            row["scope"] = "cross-repo"
        qualified.append(row)
    return qualified


def _jscpd_view(targets: list[RepoTarget], stdout: str, stderr: str) -> str:
    formats = Counter(re.findall(r"Clone found \(([^)]+)\)", stdout))
    summary = [line.strip() for line in stdout.splitlines()
               if re.search(r"Found \d+ clones|duplicated lines|duplication", line, re.I)]
    pairs = _qualify_clone_pairs(_parse_clone_pairs(stdout), targets)
    # Cross-FILE clones are the change-friction signal (a fix in one file must be
    # mirrored in another); same-file clones are lower value. Rank by span.
    cross = [p for p in pairs if p["scope"] != "within-repo" or
             p["a"]["path"] != p["b"]["path"]]
    cross.sort(key=lambda p: -p["span"])
    endpoint_rows = [endpoint for pair in pairs for endpoint in (pair["a"], pair["b"])]
    resolved_endpoints = sum(endpoint["resolved"] for endpoint in endpoint_rows)
    ambiguous_endpoints = sum(not endpoint["resolved"] and bool(
        endpoint["repository_candidates"])
                              for endpoint in endpoint_rows)
    unresolved_endpoints = len(endpoint_rows) - resolved_endpoints - ambiguous_endpoints
    scopes = Counter(pair["scope"] for pair in pairs)
    out = ["### repository attribution",
           (f"pairs\t{len(pairs)}\twithin-repo={scopes['within-repo']}\t"
            f"cross-repo={scopes['cross-repo']}\tambiguous={scopes['ambiguous']}"),
           (f"endpoints\t{len(endpoint_rows)}\tresolved={resolved_endpoints}\t"
            f"ambiguous={ambiguous_endpoints}\tunresolved={unresolved_endpoints}"),
           "", "### clone formats"]
    out += [f"{name}\t{count}" for name, count in sorted(formats.items(), key=lambda x: (-x[1], x[0]))]
    out += ["", f"### cross-file clone pairs (top {min(len(cross), 60)} of {len(cross)}; "
                f"lines\tscope\ta_repo:path:from-to\tb_repo:path:from-to)"]
    for p in cross[:60]:
        a_repo = p["a"]["repository_ref"] or "?{" + ",".join(
            p["a"]["repository_candidates"]) + "}"
        b_repo = p["b"]["repository_ref"] or "?{" + ",".join(
            p["b"]["repository_candidates"]) + "}"
        out.append(f"{p['span']}\t{p['scope']}\t"
                   f"{a_repo}:{p['a']['path']}:{p['a_from']}-{p['a_to']}\t"
                   f"{b_repo}:{p['b']['path']}:{p['b_from']}-{p['b_to']}")
    out += ["", f"### summary ({len(pairs)} total pairs, "
                f"{len(pairs) - len(cross)} same-file)", *summary[-30:]]
    if stderr.strip():
        out += ["", "### stderr", *stderr.splitlines()[:30]]
    return "\n".join(out)


def jscpd_view(target: RepoTarget, stdout: str, stderr: str) -> str:
    return _jscpd_view([target], stdout, stderr)


def jscpd_multi_view(targets: list[RepoTarget], stdout: str, stderr: str) -> str:
    return _jscpd_view(targets, stdout, stderr)


def validate_jscpd(text: str, _exit: int) -> str:
    return "" if re.search(r"Found \d+ clones\.", text) \
        else "jscpd console summary is missing 'Found N clones'"


# dependency-cruiser parsers live in parsers_depcruise.py (size-signal split);
# re-exported here so callers keep a single `parsers.*` import surface.
from .parsers_depcruise import (  # noqa: E402,F401
    _depcruise_cycles, _is_internal_spec, depcruise_degraded,
    depcruise_resolution_note, depcruise_stats, depcruise_view, validate_depcruise,
)


_COMPILE_FAILURE = re.compile(
    r"\(compile\)|could not (?:load|analyze)|(?:^|\n)-: |no (?:Go|buildable Go) "
    r"(?:source )?files|matched no packages|no packages to analyze",
    re.I,
)
_NO_PACKAGE_UNIVERSE = re.compile(
    r"(?:\./\.\.\.|package pattern).*matched no packages|no packages to analyze",
    re.I,
)
_NO_BUILDABLE_PACKAGE = re.compile(
    r"no (?:Go|buildable Go) (?:source )?files",
    re.I,
)


def staticcheck_degraded(_target: RepoTarget, combined: str, _exit: int) -> str:
    if _NO_PACKAGE_UNIVERSE.search(combined):
        return ("staticcheck-no-package-universe: package pattern matched no packages; "
                "coverage is reduced, not a clean scan")
    if _NO_BUILDABLE_PACKAGE.search(combined):
        return ("staticcheck-no-buildable-package: module has no buildable Go package "
                "for the configured analysis roots; coverage is reduced")
    if _COMPILE_FAILURE.search(combined):
        return ("staticcheck-compile-or-load-failure: coverage is incomplete")
    return ""


def staticcheck_view(_target: RepoTarget, stdout: str, stderr: str) -> str:
    findings = [x for x in stdout.splitlines() if not re.search(r"(^|/)docs/", x)]
    combined = stdout + "\n" + stderr
    limitation = staticcheck_degraded(_target, combined, 0)
    return "\n".join([
        ("coverage_limitation: " + limitation) if limitation
        else "coverage_limitation: none (scan completed; zero diagnostics is valid)",
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


_GO_LOAD_FAILURE = re.compile(
    r"cannot find package|no required module provides|module lookup disabled|"
    r"missing go\.sum entry|build constraints exclude all Go files|"
    r"cannot load|go: updates to go\.(?:mod|sum) needed",
    re.I,
)


def go_list_degraded(_target: RepoTarget, combined: str, _exit: int) -> str:
    """Fail-closed partial coverage: package load/resolution failures (cold cache,
    missing deps, constraint-excluded packages) must never read as a clean graph.
    A hard failure already exits non-zero (FAILED); this catches the case where a
    per-package Error rides along in an otherwise-accepted run."""
    stdout, _, stderr = combined.partition("\n### STDERR ###\n")
    try:
        if any("Error" in pkg for pkg in decode_json_stream(stdout)):
            return "go list coverage partial: one or more packages reported a load Error"
    except Exception:
        pass
    if _GO_LOAD_FAILURE.search(stderr):
        return "go list coverage partial: package load/resolution failure in diagnostics"
    return ""


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
        f"coupling_sample_cap: {data.get('coupling_sample_cap', 0)} "
        f"(commits_for_coupling: {data.get('coupling_commits_used', data.get('commits_used'))}, "
        f"sampled: {str(data.get('coupling_sampled', False)).lower()})",
        "", "churn:",
    ]
    out += [f"{x['commits']}\t{x['total_lines']}\t{x['file']}" for x in data.get("churn", [])]
    out += ["", "coupling:"]
    out += [f"{x['coupling_pct']}\t{x['shared_commits']}\t{x['file_a']}\t{x['file_b']}"
            for x in data.get("coupling", [])]
    out += ["", "cross_dir_coupling (change-friction: pairs spanning different "
            "top-level areas — ripple signal):"]
    out += [f"{x['coupling_pct']}\t{x['shared_commits']}\t{x['file_a']}\t{x['file_b']}"
            for x in data.get("cross_dir_coupling", [])] or ["(none above min-shared)"]
    out += ["", "ownership:"]
    out += [f"{x['distinct_committers']}\t{x['dominant_commit_share']}\t"
            f"{x['dominant_churn_share']}\t{x['file']}" for x in data.get("ownership", [])]
    # Authors: STRONG-merged roster (exact-email/.mailmap via git check-mailmap),
    # with git shortlog -sne as a mailmap-applied but bot-inclusive cross-check.
    roster = data.get("author_roster", [])
    if roster:
        out += ["", f"authors (strong identity: exact-email / .mailmap merges; "
                    f"{data.get('distinct_authors_strong', len(roster))} distinct; "
                    f"git shortlog -sne cross-check: "
                    f"{data.get('git_shortlog_author_count', '?')} — mailmap-applied, "
                    "bot-inclusive):"]
        out += [f"{x['commits']}\t{x['author']}" for x in roster[:40]]
    if data.get("uncertain_name_matches"):
        out += ["", "uncertain_name_matches (name-only collisions — NOT merged, "
                    "surfaced for confirmation):", *data["uncertain_name_matches"]]
    if stderr.strip():
        out += ["", "stderr:", *stderr.splitlines()[:30]]
    return "\n".join(out)
