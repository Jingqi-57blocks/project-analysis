"""Structured report components.

Every function here builds an HTML fragment from *typed machine-readable data
only* (system-model nodes/edges/coverage/stats, coverage reports, run-state
provenance, discovery counts). No prose is parsed and no business semantics are
invented: when the backing artifact is absent or a lens is unavailable, the
component says so honestly rather than fabricating content.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field

from .htmlutil import attr, esc
from .markdown_render import mermaid_figure
from .run_inputs import RunInputs


@dataclass
class StructuredComponent:
    key: str
    title: str
    anchor: str
    html: str
    sources: list[str] = field(default_factory=list)
    mermaid_source: str | None = None  # set for diagram components


def status_badge(status: str) -> str:
    """A coloured status pill. The label is the verbatim status token."""
    key = (status or "unknown").lower()
    return f'<span class="badge badge-{esc(key)}">{esc(key)}</span>'


def _tiles(items: list[tuple[str, object, str]]) -> str:
    cells = []
    for label, value, note in items:
        note_html = f'<span class="tile-note">{esc(note)}</span>' if note else ""
        cells.append(
            f'<div class="tile"><span class="tile-value">{esc(value)}</span>'
            f'<span class="tile-label">{esc(label)}</span>{note_html}</div>'
        )
    return f'<div class="tiles">{"".join(cells)}</div>'


def _table(headers: list[str], rows: list[list[str]], *, cls: str = "") -> str:
    if not rows:
        return '<p class="muted">No rows.</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    extra = f" {cls}" if cls else ""
    return (
        f'<div class="table-scroll"><table class="data{extra}">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _searchable_table(
    table_id: str, placeholder: str, headers: list[str], rows: list[list[str]]
) -> str:
    """A table with a live client-side row filter (offline, no dependencies)."""
    table = _table(headers, rows)
    return (
        f'<div class="filter-bar"><input type="search" name="{attr(table_id)}-filter" '
        f'class="filter-input" data-filter-target="{attr(table_id)}" '
        f'placeholder="{attr(placeholder)}" aria-label="{attr(placeholder)}"></div>'
        f'<div id="{attr(table_id)}">{table}</div>'
    )


def _repos(inputs: RunInputs) -> list[dict]:
    sm = inputs.system_model or {}
    repos = [n for n in sm.get("nodes", []) if n.get("kind") == "repository"]
    return sorted(repos, key=lambda n: n.get("repo_id", ""))


def _nid_to_repo(sm: dict) -> dict[str, str]:
    return {n["id"]: n.get("repo_id", "") for n in sm.get("nodes", [])}


def _unavailable(key: str, title: str, anchor: str, reason: str) -> StructuredComponent:
    html = (
        f'<div class="unavailable"><p><strong>{esc(title)}:</strong> '
        f"{esc(reason)}</p></div>"
    )
    return StructuredComponent(key=key, title=title, anchor=anchor, html=html)


# --------------------------------------------------------------------------- #
# snapshot + identity
# --------------------------------------------------------------------------- #

def system_snapshot(inputs: RunInputs) -> StructuredComponent:
    anchor = "system-snapshot"
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "snapshot", "System snapshot", anchor,
            "system-model.json is absent; the compact snapshot is unavailable.",
        )
    stats = sm.get("stats", {})
    by_kind = stats.get("nodes_by_kind", {})
    repos = _repos(inputs)
    languages = sorted({s for r in repos for s in r.get("attrs", {}).get("stacks", [])})
    modules_cov = sm.get("coverage", {}).get("modules", {})
    modules_count = modules_cov.get("counts", {}).get("modules", 0)
    modules_status = modules_cov.get("status", "unknown")
    modules_note = "synthesis-inferred; not machine-computed" if modules_status == "unavailable" else ""

    tiles = _tiles([
        ("repositories", by_kind.get("repository", len(repos)), ""),
        ("modules", modules_count if modules_status != "unavailable" else "unavailable", modules_note),
        ("deployable units", by_kind.get("deployable-unit", 0), ""),
        ("languages", len(languages), ", ".join(languages)),
        ("data stores", by_kind.get("data-store", 0), "distinct tables"),
        ("routes", by_kind.get("route", 0), ""),
        ("external boundaries", by_kind.get("external-boundary", 0), "incl. dependency candidates"),
        ("symbols", by_kind.get("symbol", 0), ""),
    ])

    # HEAD revision per repo, joined by repo_id (the stable id's trailing
    # path-hash suffix is dropped for a clean name; the real commit gets its
    # own column).
    heads = {p.repo_id: (p.head[:8] if p.head else "—") for p in inputs.provenance()}
    rows = []
    for r in repos:
        a = r.get("attrs", {})
        rid = r.get("repo_id", "")
        name = re.sub(r"-[0-9a-f]{6,}$", "", rid) or rid
        rows.append([
            esc(name),
            esc(heads.get(rid, "—")),
            esc(", ".join(a.get("stacks", [])) or "—"),
            esc(", ".join(a.get("frameworks", [])) or "—"),
            esc(a.get("package_manager", "—")),
            esc(a.get("commit_count", "—")),
        ])
    table = _table(
        ["repository", "commit", "stacks", "frameworks", "package manager", "commits"],
        rows,
    )
    html = tiles + table
    return StructuredComponent(
        key="snapshot", title="System snapshot", anchor=anchor, html=html,
        sources=["system-model.json", "run-state.json"],
    )


def provenance_table(inputs: RunInputs) -> StructuredComponent:
    anchor = "provenance"
    rows = []
    for p in inputs.provenance():
        head_short = p.head[:12] if p.head else "—"
        rows.append([
            esc(p.repo_id),
            f'<code title="{attr(p.head)}">{esc(head_short)}</code>' if p.head else "—",
            esc(p.dirty),
        ])
    if not rows:
        return _unavailable(
            "provenance", "Analyzed revisions", anchor,
            "run-state.json carries no provenance rows.",
        )
    table = _table(["repository", "HEAD", "working tree dirty"], rows)
    meta = (
        f'<p class="muted">Analyzed at <code>{esc(inputs.analyzed_at)}</code> · '
        f"language <code>{esc(inputs.language)}</code> · run "
        f"<code>{esc(inputs.run_id)}</code></p>"
    )
    return StructuredComponent(
        key="provenance", title="Analyzed revisions", anchor=anchor,
        html=meta + table, sources=["run-state.json"],
    )


# --------------------------------------------------------------------------- #
# coverage + evidence
# --------------------------------------------------------------------------- #

def coverage_matrix(inputs: RunInputs) -> StructuredComponent:
    anchor = "coverage-lenses"
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "coverage", "Lens coverage", anchor,
            "system-model.json is absent; per-lens coverage is unavailable.",
        )
    coverage = sm.get("coverage", {})
    rows = []
    for lens in sorted(coverage):
        rec = coverage[lens]
        counts = rec.get("counts", {})
        counts_txt = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "—"
        unresolved = rec.get("unresolved", {})
        unresolved_txt = ", ".join(f"{k}={v}" for k, v in sorted(unresolved.items())) or "—"
        caps = rec.get("caps", [])
        caps_html = (
            f'<details><summary>{len(caps)} cap(s)</summary><ul>'
            + "".join(f"<li>{esc(c)}</li>" for c in caps)
            + "</ul></details>"
            if caps else "—"
        )
        rows.append([
            esc(lens),
            status_badge(rec.get("status", "unknown")),
            esc(counts_txt),
            esc(unresolved_txt),
            caps_html,
        ])
    table = _table(["lens", "status", "counts", "unresolved", "caps"], rows)
    return StructuredComponent(
        key="coverage", title="Lens coverage", anchor=anchor, html=table,
        sources=["system-model.json"],
    )


def relationship_legend(inputs: RunInputs) -> StructuredComponent:
    anchor = "relationship-status"
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "legend", "Relationship status", anchor,
            "system-model.json is absent; edge status counts are unavailable.",
        )
    stats = sm.get("stats", {})
    by_status = stats.get("edges_by_status", {})
    by_type = stats.get("edges_by_type", {})
    legend = {
        "observed": "recorded directly by a tool (e.g. a resolved call/import edge)",
        "inferred": "derived by a producer with lower certainty",
        "unresolved": "a call/import site seen but its target not resolvable",
        "unavailable": "no producer supplied this relationship class",
    }
    rows = []
    for status_key, meaning in legend.items():
        rows.append([
            status_badge(status_key),
            esc(by_status.get(status_key, 0)),
            esc(meaning),
        ])
    edge_types = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
    table = _table(["status", "edge count", "meaning"], rows)
    note = f'<p class="muted">Edge types: {esc(edge_types)}.</p>'
    return StructuredComponent(
        key="legend", title="Relationship status", anchor=anchor,
        html=table + note, sources=["system-model.json"],
    )


def _per_repo_coverage(report: dict | None, title_key: str) -> str:
    if not report:
        return f'<p class="muted">{esc(title_key)} coverage report absent.</p>'
    rows = []
    for repo in sorted(report.get("repos", []), key=lambda r: r.get("repo_id", "")):
        detail = {k: v for k, v in repo.items() if k not in ("notes", "repo_id")}
        detail_txt = ", ".join(
            f"{k}={v}" for k, v in sorted(detail.items())
            if isinstance(v, (str, int, float, bool))
        )
        rows.append([
            esc(repo.get("repo_id", "")),
            status_badge(repo.get("status", "unknown")),
            esc(repo.get("tool", "—")),
            esc(detail_txt),
        ])
    return _table(["repository", "status", "tool", "detail"], rows)


def callgraph_coverage_table(inputs: RunInputs) -> StructuredComponent:
    html = _per_repo_coverage(inputs.callgraph_coverage, "call-graph")
    return StructuredComponent(
        key="callgraph-coverage", title="Call-graph coverage (per repository)",
        anchor="callgraph-coverage", html=html, sources=["callgraph-coverage.json"],
    )


def depmap_coverage_table(inputs: RunInputs) -> StructuredComponent:
    html = _per_repo_coverage(inputs.depmap_coverage, "dependency-map")
    return StructuredComponent(
        key="depmap-coverage", title="Dependency-map coverage (per repository)",
        anchor="depmap-coverage", html=html, sources=["imports/depmap-coverage.json"],
    )


# --------------------------------------------------------------------------- #
# topology (structured) + boundary/data tables
# --------------------------------------------------------------------------- #

def topology_structured(inputs: RunInputs) -> StructuredComponent:
    anchor = "topology-structured"
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "topology", "System topology (structured)", anchor,
            "system-model.json is absent; the structured topology is unavailable.",
        )
    nid2repo = _nid_to_repo(sm)
    repos = [r.get("repo_id", "") for r in _repos(inputs)]
    rid = {r: f"r{i}" for i, r in enumerate(repos)}

    cross = collections.Counter()   # (src_repo, dst_repo) -> route-linkage count
    tables_by_repo = collections.defaultdict(set)
    for e in sm.get("edges", []):
        etype = e.get("type")
        if etype == "route-linkage":
            s, t = nid2repo.get(e["src"], ""), nid2repo.get(e["dst"], "")
            if s in rid and t in rid and s != t:
                cross[(s, t)] += 1
        elif etype == "data":
            s = nid2repo.get(e["src"], "")
            if s in rid:
                tables_by_repo[s].add(e["dst"])

    lines = ["graph LR"]
    stacks = {r.get("repo_id", ""): ", ".join(r.get("attrs", {}).get("stacks", []))
              for r in _repos(inputs)}
    for r in repos:
        lines.append(f'  {rid[r]}["{r}<br/>{stacks.get(r, "")}"]')
    has_data = any(tables_by_repo.values())
    if has_data:
        lines.append('  DB[("shared data layer<br/>(per-repo table access)")]')
    for (s, t), n in sorted(cross.items()):
        lines.append(f'  {rid[s]} -->|"{n} linked routes"| {rid[t]}')
    for r in repos:
        if tables_by_repo.get(r):
            lines.append(f'  {rid[r]} -->|"{len(tables_by_repo[r])} tables"| DB')
    mermaid_source = "\n".join(lines)

    figure = mermaid_figure(mermaid_source, dom_id="topo")
    note = (
        '<p class="muted">Built from structured <code>route-linkage</code> and '
        "<code>data</code> edges (which routes the frontend calls; which repos "
        "touch persistence). Inter-service business roles and named external "
        "systems are synthesis-inferred narrative — see the authored topology "
        "and the Project Map.</p>"
    )
    return StructuredComponent(
        key="topology", title="System topology (structured)", anchor=anchor,
        html=figure + note, sources=["system-model.json"],
        mermaid_source=mermaid_source,
    )


def external_boundaries_table(inputs: RunInputs) -> StructuredComponent:
    anchor = "external-boundaries"
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "externals", "External boundaries", anchor,
            "system-model.json is absent; external boundaries are unavailable.",
        )
    ext = [n for n in sm.get("nodes", []) if n.get("kind") == "external-boundary"]
    by_kind = collections.defaultdict(list)
    for n in ext:
        by_kind[n.get("attrs", {}).get("kind", "unknown")].append(n.get("label", ""))
    rows = []
    for kind in sorted(by_kind):
        labels = sorted(set(by_kind[kind]))
        preview = ", ".join(labels[:24]) + (" …" if len(labels) > 24 else "")
        rows.append([esc(kind), esc(len(labels)), f'<span class="wrap">{esc(preview)}</span>'])
    table = _searchable_table(
        "external-boundaries-table", "filter boundary kinds…",
        ["boundary kind", "count", "labels"], rows,
    )
    note = (
        '<p class="muted">Resolved hosts/packages (<code>host-fragment</code>, '
        "<code>integration-package</code>) are named external systems; "
        "<code>integration-candidate</code> entries are dependency-manifest "
        "candidates, not confirmed runtime integrations.</p>"
    )
    return StructuredComponent(
        key="externals", title="External boundaries", anchor=anchor,
        html=note + table, sources=["system-model.json"],
    )


def data_stores_table(inputs: RunInputs) -> StructuredComponent:
    anchor = "data-stores"
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "datastores", "Data stores", anchor,
            "system-model.json is absent; data stores are unavailable.",
        )
    ds = [n for n in sm.get("nodes", []) if n.get("kind") == "data-store"]
    rows = []
    for n in sorted(ds, key=lambda n: n.get("attrs", {}).get("table", n.get("label", ""))):
        a = n.get("attrs", {})
        rows.append([
            esc(a.get("table", n.get("label", ""))),
            esc(", ".join(a.get("access_types", [])) or "—"),
            esc(n.get("repo_id", "—")),
        ])
    table = _searchable_table(
        "data-stores-table", "filter tables…",
        ["table", "access types", "repository"], rows,
    )
    return StructuredComponent(
        key="datastores", title="Data stores", anchor=anchor,
        html=table, sources=["system-model.json"],
    )
