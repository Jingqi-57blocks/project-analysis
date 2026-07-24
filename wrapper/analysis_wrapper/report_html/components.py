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

from .. import locale
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
        f'<div class="filter-bar"><input type="text" name="{attr(table_id)}-filter" '
        f'class="filter-input" data-filter-target="{attr(table_id)}" '
        f'placeholder="{attr(placeholder)}" aria-label="{attr(placeholder)}"></div>'
        f'<div id="{attr(table_id)}">{table}</div>'
    )


def _repos(inputs: RunInputs) -> list[dict]:
    sm = inputs.system_model or {}
    repos = [n for n in sm.get("nodes", []) if n.get("kind") == "repository"]
    return sorted(repos, key=lambda n: n.get("repository_ref", ""))


def _nid_to_repo(sm: dict) -> dict[str, str]:
    return {n["id"]: n.get("repository_ref", "")
            for n in sm.get("nodes", [])}


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
    cat = locale.labels(inputs.language)
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "snapshot", cat["components.snapshot.title"], anchor,
            cat["components.snapshot.unavailable"],
        )
    stats = sm.get("stats", {})
    by_kind = stats.get("nodes_by_kind", {})
    repos = _repos(inputs)
    languages = sorted({s for r in repos for s in r.get("attrs", {}).get("stacks", [])})
    modules_cov = sm.get("coverage", {}).get("modules", {})
    modules_count = modules_cov.get("counts", {}).get("modules", 0)
    modules_status = modules_cov.get("status", "unknown")
    modules_note = cat["components.snapshot.modules_note"] if modules_status == "unavailable" else ""

    tiles = _tiles([
        (cat["components.snapshot.tile.repositories"], by_kind.get("repository", len(repos)), ""),
        (cat["components.snapshot.tile.modules"],
         modules_count if modules_status != "unavailable" else "unavailable", modules_note),
        (cat["components.snapshot.tile.deployable_units"], by_kind.get("deployable-unit", 0), ""),
        (cat["components.snapshot.tile.languages"], len(languages), ", ".join(languages)),
        (cat["components.snapshot.tile.data_stores"], by_kind.get("data-store", 0),
         cat["components.snapshot.tile.data_stores_note"]),
        (cat["components.snapshot.tile.routes"], by_kind.get("route", 0), ""),
        (cat["components.snapshot.tile.external_boundaries"], by_kind.get("external-boundary", 0),
         cat["components.snapshot.tile.external_boundaries_note"]),
        (cat["components.snapshot.tile.symbols"], by_kind.get("symbol", 0), ""),
    ])

    # HEAD revision per repository reference; the real commit has its own column.
    heads = {p.repository_ref: (p.head[:8] if p.head else "—")
             for p in inputs.provenance()}
    rows = []
    for r in repos:
        a = r.get("attrs", {})
        rid = r.get("repository_ref", "")
        name = rid
        rows.append([
            esc(name),
            esc(heads.get(rid, "—")),
            esc(", ".join(a.get("stacks", [])) or "—"),
            esc(", ".join(a.get("frameworks", [])) or "—"),
            esc(a.get("package_manager", "—")),
            esc(a.get("commit_count", "—")),
        ])
    table = _table(
        [cat["components.header.repository"], cat["components.header.commit"],
         cat["components.header.stacks"], cat["components.header.frameworks"],
         cat["components.header.package_manager"], cat["components.header.commits"]],
        rows,
    )
    html = tiles + table
    return StructuredComponent(
        key="snapshot", title=cat["components.snapshot.title"], anchor=anchor, html=html,
        sources=["system-model.json", "run-state.json"],
    )


def provenance_table(inputs: RunInputs) -> StructuredComponent:
    anchor = "provenance"
    cat = locale.labels(inputs.language)
    rows = []
    for p in inputs.provenance():
        head_short = p.head[:12] if p.head else "—"
        rows.append([
            esc(p.repository_ref),
            f'<code title="{attr(p.head)}">{esc(head_short)}</code>' if p.head else "—",
            esc(p.dirty),
        ])
    if not rows:
        return _unavailable(
            "provenance", cat["components.provenance.title"], anchor,
            cat["components.provenance.unavailable"],
        )
    table = _table(
        [cat["components.header.repository"], cat["components.header.head"],
         cat["components.header.working_tree_dirty"]],
        rows,
    )
    meta = (
        f'<p class="muted">{esc(cat["components.provenance.analyzed_at"])} '
        f'<code>{esc(inputs.analyzed_at)}</code> · '
        f'{esc(cat["components.provenance.language"])} <code>{esc(inputs.language)}</code> · '
        f'{esc(cat["components.provenance.run"])} '
        f"<code>{esc(inputs.run_id)}</code></p>"
    )
    return StructuredComponent(
        key="provenance", title=cat["components.provenance.title"], anchor=anchor,
        html=meta + table, sources=["run-state.json"],
    )


# --------------------------------------------------------------------------- #
# coverage + evidence
# --------------------------------------------------------------------------- #

def coverage_matrix(inputs: RunInputs) -> StructuredComponent:
    anchor = "coverage-lenses"
    cat = locale.labels(inputs.language)
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "coverage", cat["components.coverage.title"], anchor,
            cat["components.coverage.unavailable"],
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
            f'<details><summary>{len(caps)} {cat["components.coverage.cap_label"]}</summary><ul>'
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
    table = _table(
        [cat["components.header.lens"], cat["components.header.status"],
         cat["components.header.counts"], cat["components.header.unresolved"],
         cat["components.header.caps"]],
        rows,
    )
    return StructuredComponent(
        key="coverage", title=cat["components.coverage.title"], anchor=anchor, html=table,
        sources=["system-model.json"],
    )


def relationship_legend(inputs: RunInputs) -> StructuredComponent:
    anchor = "relationship-status"
    cat = locale.labels(inputs.language)
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "legend", cat["components.legend.title"], anchor,
            cat["components.legend.unavailable"],
        )
    stats = sm.get("stats", {})
    by_status = stats.get("edges_by_status", {})
    by_type = stats.get("edges_by_type", {})
    legend = {
        "observed": cat["components.legend.meaning.observed"],
        "inferred": cat["components.legend.meaning.inferred"],
        "unresolved": cat["components.legend.meaning.unresolved"],
        "unavailable": cat["components.legend.meaning.unavailable"],
    }
    rows = []
    for status_key, meaning in legend.items():
        rows.append([
            status_badge(status_key),
            esc(by_status.get(status_key, 0)),
            esc(meaning),
        ])
    edge_types = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
    table = _table(
        [cat["components.header.status"], cat["components.header.edge_count"],
         cat["components.header.meaning"]],
        rows,
    )
    note = (
        f'<p class="muted">{esc(cat["components.legend.edge_types_prefix"])} '
        f'{esc(edge_types)}.</p>'
    )
    return StructuredComponent(
        key="legend", title=cat["components.legend.title"], anchor=anchor,
        html=table + note, sources=["system-model.json"],
    )


def _per_repo_coverage(report: dict | None, title_key: str, cat: dict) -> str:
    if not report:
        return (
            f'<p class="muted">{esc(title_key)} '
            f'{esc(cat["components.per_repo.absent_suffix"])}</p>'
        )
    rows = []
    for repo in sorted(report.get("repos", []), key=lambda r: r.get(
            "repository_ref", "")):
        detail = {k: v for k, v in repo.items()
                  if k not in ("notes", "repository_ref")}
        detail_txt = ", ".join(
            f"{k}={v}" for k, v in sorted(detail.items())
            if isinstance(v, (str, int, float, bool))
        )
        rows.append([
            esc(repo.get("repository_ref", "")),
            status_badge(repo.get("status", "unknown")),
            esc(repo.get("tool", "—")),
            esc(detail_txt),
        ])
    return _table(
        [cat["components.header.repository"], cat["components.header.status"],
         cat["components.header.tool"], cat["components.header.detail"]],
        rows,
    )


def callgraph_coverage_table(inputs: RunInputs) -> StructuredComponent:
    cat = locale.labels(inputs.language)
    html = _per_repo_coverage(inputs.callgraph_coverage, "call-graph", cat)
    return StructuredComponent(
        key="callgraph-coverage", title=cat["components.callgraph_coverage.title"],
        anchor="callgraph-coverage", html=html, sources=["callgraph-coverage.json"],
    )


def depmap_coverage_table(inputs: RunInputs) -> StructuredComponent:
    cat = locale.labels(inputs.language)
    html = _per_repo_coverage(inputs.depmap_coverage, "dependency-map", cat)
    return StructuredComponent(
        key="depmap-coverage", title=cat["components.depmap_coverage.title"],
        anchor="depmap-coverage", html=html, sources=["imports/depmap-coverage.json"],
    )


# --------------------------------------------------------------------------- #
# topology (structured) + boundary/data tables
# --------------------------------------------------------------------------- #

def topology_structured(inputs: RunInputs) -> StructuredComponent:
    anchor = "topology-structured"
    cat = locale.labels(inputs.language)
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "topology", cat["components.topology.title"], anchor,
            cat["components.topology.unavailable"],
        )
    nid2repo = _nid_to_repo(sm)
    repos = [r.get("repository_ref", "") for r in _repos(inputs)]
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
    stacks = {r.get("repository_ref", ""):
              ", ".join(r.get("attrs", {}).get("stacks", []))
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
    note = f'<p class="muted">{cat["components.topology.note"]}</p>'
    return StructuredComponent(
        key="topology", title=cat["components.topology.title"], anchor=anchor,
        html=figure + note, sources=["system-model.json"],
        mermaid_source=mermaid_source,
    )


def external_boundaries_table(inputs: RunInputs) -> StructuredComponent:
    anchor = "external-boundaries"
    cat = locale.labels(inputs.language)
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "externals", cat["components.externals.title"], anchor,
            cat["components.externals.unavailable"],
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
        "external-boundaries-table", cat["components.externals.placeholder"],
        [cat["components.header.boundary_kind"], cat["components.header.count"],
         cat["components.header.labels"]],
        rows,
    )
    note = f'<p class="muted">{cat["components.externals.note"]}</p>'
    return StructuredComponent(
        key="externals", title=cat["components.externals.title"], anchor=anchor,
        html=note + table, sources=["system-model.json"],
    )


def data_stores_table(inputs: RunInputs) -> StructuredComponent:
    anchor = "data-stores"
    cat = locale.labels(inputs.language)
    sm = inputs.system_model
    if not sm:
        return _unavailable(
            "datastores", cat["components.datastores.title"], anchor,
            cat["components.datastores.unavailable"],
        )
    ds = [n for n in sm.get("nodes", []) if n.get("kind") == "data-store"]
    rows = []
    for n in sorted(ds, key=lambda n: n.get("attrs", {}).get("table", n.get("label", ""))):
        a = n.get("attrs", {})
        rows.append([
            esc(a.get("table", n.get("label", ""))),
            esc(", ".join(a.get("access_types", [])) or "—"),
            esc(n.get("repository_ref", "—")),
        ])
    table = _searchable_table(
        "data-stores-table", cat["components.datastores.placeholder"],
        [cat["components.header.table"], cat["components.header.access_types"],
         cat["components.header.repository"]],
        rows,
    )
    return StructuredComponent(
        key="datastores", title=cat["components.datastores.title"], anchor=anchor,
        html=table, sources=["system-model.json"],
    )
