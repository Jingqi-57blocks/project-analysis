"""Deterministic cohesion-bundle producer (57B-116, M2).

Wrapper-boundary rule (SKILL.md: "tools measure, the model judges"): this
module MEASURES structural relationships already present in the run's own
canonical artifacts — it never proposes a module boundary, never assigns a
disposition, and never ranks or scores a cluster's importance. Every cluster
row is a factual grouping ("these candidates share X") with the evidence that
established it; the judgment of whether a cluster IS a real business boundary
belongs entirely to a later synthesis/judgment pass (module-map candidate
dispositions), not to this producer.

Five measurement "lanes" (cluster ``kind``s), each derived from artifacts the
run already has — no new tool invocation happens here:

- ``route-prefix``  — module-candidates.json's route/route-mount rows grouped
  by a bounded-depth literal path-prefix.
- ``folder``        — every candidate's own evidence file paths (plus a
  folder candidate's own value) grouped by a bounded-depth directory prefix.
- ``import``        — system-model.json's ``dependency`` edges, mapped from
  file-node endpoints to the module-candidate(s) linked to those files, then
  reduced to connected components (a simple, deterministic graph clustering —
  see :class:`_UnionFind`).
- ``co-change``     — git-history's own sanitized signal view (the "coupling:"
  section), file paths mapped to folder candidates by prefix, reduced to
  connected components the same way as ``import``. Absent when no
  complete/partial git-history signal view exists in this run — never
  fabricated; the absence itself is disclosed under ``kinds["co-change"]``.
- ``table-ownership`` — system-model.json's ``data`` edges (file -> table),
  mapped to candidates the same way as ``import``.

Bounded and disclosed: every lane caps at ``_MAX_CLUSTERS_PER_KIND`` clusters
(keeping the largest first, a deterministic tie-broken sort) and every
cluster's ``evidence_refs`` at ``_MAX_EVIDENCE_PER_CLUSTER``; both caps and
whether a lane was truncated are recorded under the top-level ``kinds`` block,
never silently dropped.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from . import identity
from .executor import replace_artifact_text
from .overview_audit import SCHEMA_VERSION
from .sanitize import sanitize_text
from .system_model import ids

FILENAME = "cohesion-bundle.json"

_FOLDER_DEPTH = 2         # bounded directory-prefix depth for the folder lane
_ROUTE_PREFIX_DEPTH = 2   # bounded path-segment depth for the route-prefix lane
_MAX_CLUSTERS_PER_KIND = 200   # deterministic cap; disclosed under `kinds`
_MAX_EVIDENCE_PER_CLUSTER = 20

KINDS = ("route-prefix", "folder", "import", "co-change", "table-ownership")


def _load(path: Path) -> dict:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #

def _segments(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def _bounded_prefix(path: str, depth: int) -> str:
    return "/".join(_segments(path)[:depth])


def _real_evidence_path(ref: str) -> str:
    """The repo-relative file a genuine ``repo@rev:path:line`` citation
    names; ``""`` for anything else. A folder candidate's own evidence is a
    structured artifact pointer (``discovery-report.json:repos[...]``), not a
    citation — it must never be mistaken for a source path."""
    return ids.citation_file(ref) if "@" in ref else ""


def _cluster_row(kind: str, measure: str, members: set[str],
                 evidence_refs: set[str]) -> dict:
    return {
        "kind": kind,
        "measure": measure,
        "members": sorted(members),
        "evidence_refs": sorted(evidence_refs)[:_MAX_EVIDENCE_PER_CLUSTER],
    }


def _cap(rows: list[dict], limit: int) -> tuple[list[dict], int, bool]:
    """Keep the largest clusters first (a deterministic, disclosed rule) —
    ties broken by kind then by the sorted member list itself."""
    ordered = sorted(rows, key=lambda row: (-len(row["members"]), row["kind"], row["members"]))
    selected = ordered[:limit]
    return selected, len(ordered), len(selected) < len(ordered)


def _finish(kinds: dict, kind: str, rows: list[dict]) -> list[dict]:
    selected, total, truncated = _cap(rows, _MAX_CLUSTERS_PER_KIND)
    kinds[kind] = {"available": True, "total_clusters": total,
                  "included_clusters": len(selected), "truncated": truncated}
    return selected


def _node_to_candidates(candidates: list[dict]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for row in candidates:
        candidate_id = str(row.get("candidate_id", ""))
        for node_id in row.get("node_ids", []):
            index[str(node_id)].add(candidate_id)
    return index


# --------------------------------------------------------------------------- #
# connected components (shared by the import / co-change graph lanes)
# --------------------------------------------------------------------------- #

class _UnionFind:
    """Deterministic connected-components accumulator.

    The resulting PARTITION (which candidates end up in the same component)
    is a pure function of which pairs were unioned — an order-independent
    graph property — never of the order edges/pairs are processed in. Union-
    by-minimum-id only keeps the internal parent pointers themselves stable;
    it does not change which members end up together.
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self._parent.setdefault(item, item)
        path: list[str] = []
        while self._parent[item] != item:
            path.append(item)
            item = self._parent[item]
        for node in path:
            self._parent[node] = item
        return item

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        if root_a < root_b:
            self._parent[root_b] = root_a
        else:
            self._parent[root_a] = root_b

    def components(self) -> dict[str, set[str]]:
        groups: dict[str, set[str]] = defaultdict(set)
        for item in list(self._parent):
            groups[self.find(item)].add(item)
        return dict(groups)


def _connected_components(pairs: list[tuple[str, str, str]],
                          ) -> list[tuple[set[str], set[str]]]:
    """``pairs``: (candidate_a, candidate_b, evidence_ref) triples for every
    observed connection (self-pairs are ignored). Returns ``(members,
    evidence_refs)`` for every resulting component with >=2 distinct
    candidates — ``evidence_refs`` is every pair's ref whose two endpoints
    both landed in that component."""
    uf = _UnionFind()
    for candidate_a, candidate_b, _ref in pairs:
        if candidate_a != candidate_b:
            uf.union(candidate_a, candidate_b)
    components = uf.components()
    root_of = {member: root for root, members in components.items() for member in members}
    evidence_by_root: dict[str, set[str]] = defaultdict(set)
    for candidate_a, candidate_b, ref in pairs:
        if candidate_a == candidate_b:
            continue
        root = root_of.get(candidate_a)
        if root is not None and root_of.get(candidate_b) == root:
            evidence_by_root[root].add(ref)
    return [(members, evidence_by_root[root]) for root, members in components.items()
            if len(members) >= 2]


# --------------------------------------------------------------------------- #
# route-prefix
# --------------------------------------------------------------------------- #

def _route_prefix_clusters(candidates: list[dict], by_id: dict[str, dict]) -> list[dict]:
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in candidates:
        if row.get("signal_kind") not in {"route", "route-mount"}:
            continue
        repository_ref = str(row.get("repository_ref", ""))
        _method, _sep, path = str(row.get("value", "")).partition(" ")
        prefix = _bounded_prefix(path, _ROUTE_PREFIX_DEPTH)
        if not prefix:
            continue
        groups[(repository_ref, prefix)].add(str(row["candidate_id"]))
    rows = []
    for (repository_ref, prefix), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        evidence = {cite for cid in members for cite in by_id[cid].get("evidence", [])}
        measure = (
            f"{len(members)} route candidate(s) in {repository_ref!r} share the literal "
            f"path-prefix text '/{prefix}' (bounded to depth {_ROUTE_PREFIX_DEPTH}) — a "
            "textual match only: router mount prefixes are not resolved onto their leaf "
            "routes (see routes/ui-route-linkage.json's own limitation note), so this is "
            "not a confirmed containment relationship.")
        rows.append(_cluster_row("route-prefix", measure, members, evidence))
    return rows


# --------------------------------------------------------------------------- #
# folder
# --------------------------------------------------------------------------- #

def _folder_directories(row: dict) -> set[str]:
    if row.get("signal_kind") == "folder" and row.get("value"):
        return {str(row["value"])}
    dirs = set()
    for item in row.get("evidence", []):
        path = _real_evidence_path(str(item))
        if path and "/" in path:
            dirs.add(path.rsplit("/", 1)[0])
    return dirs


def _folder_clusters(candidates: list[dict], by_id: dict[str, dict]) -> list[dict]:
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in candidates:
        candidate_id = str(row.get("candidate_id", ""))
        repository_ref = str(row.get("repository_ref", ""))
        for directory in _folder_directories(row):
            prefix = _bounded_prefix(directory, _FOLDER_DEPTH)
            if not prefix:
                continue
            groups[(repository_ref, prefix)].add(candidate_id)
    rows = []
    for (repository_ref, prefix), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        evidence = {cite for cid in members for cite in by_id[cid].get("evidence", [])}
        measure = (
            f"{len(members)} module candidate(s) in {repository_ref!r} carry evidence "
            f"under the directory-prefix '{prefix}' (bounded to depth {_FOLDER_DEPTH}).")
        rows.append(_cluster_row("folder", measure, members, evidence))
    return rows


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #

def _import_pairs(model: dict, node_to_candidates: dict[str, set[str]],
                  ) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for edge in model.get("edges", []):
        if edge.get("type") != "dependency" or edge.get("status") != "observed":
            continue
        src_candidates = node_to_candidates.get(str(edge.get("src", "")), set())
        dst_candidates = node_to_candidates.get(str(edge.get("dst", "")), set())
        if not src_candidates or not dst_candidates:
            continue
        evidence = edge.get("evidence", [])
        ref = str(sorted(evidence)[0]) if evidence else str(edge.get("id", ""))
        for candidate_a in src_candidates:
            for candidate_b in dst_candidates:
                if candidate_a != candidate_b:
                    pairs.append((candidate_a, candidate_b, ref))
    return pairs


def _import_clusters(model: dict, node_to_candidates: dict[str, set[str]]) -> list[dict]:
    rows = []
    for members, evidence in _connected_components(_import_pairs(model, node_to_candidates)):
        measure = (
            f"{len(members)} module candidate(s) are connected through in-repo "
            "import/dependency edges in the assembled dependency graph.")
        rows.append(_cluster_row("import", measure, members, evidence))
    return rows


# --------------------------------------------------------------------------- #
# co-change
# --------------------------------------------------------------------------- #

def _view_lines(run: Path, filename: str) -> list[str]:
    path = run / "signals" / filename
    if not path.is_file():
        return []
    return path.read_text("utf-8", errors="replace").splitlines()


def _section_rows(lines: list[str], header: str) -> list[tuple[int, str]]:
    """1-based (line_number, line_text) pairs for the body of a ``header``
    section: every line after it up to (not including) the next blank line —
    mirrors exactly how ``parsers.history_view`` lays its sections out."""
    if header not in lines:
        return []
    start = lines.index(header)
    rows = []
    for offset, line in enumerate(lines[start + 1:], start=start + 2):
        if not line.strip():
            break
        rows.append((offset, line))
    return rows


def _folder_index(candidates: list[dict]) -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in candidates:
        if row.get("signal_kind") == "folder" and row.get("value"):
            index[str(row.get("repository_ref", ""))].append(
                (str(row["value"]), str(row["candidate_id"])))
    return index


def _folder_matches(path: str, folders: list[tuple[str, str]]) -> set[str]:
    return {candidate_id for value, candidate_id in folders
            if path == value or path.startswith(value + "/")}


def _co_change_pairs(run: Path, candidates: list[dict]) -> tuple[list[tuple[str, str, str]], bool]:
    summary_path = run / "signals" / "run-summary.json"
    if not summary_path.is_file():
        return [], False
    summary = _load(summary_path)
    history_rows = [row for row in summary.get("signals", [])
                    if isinstance(row, dict) and row.get("tool") == "git-history"
                    and row.get("status") in {"complete", "partial"} and row.get("view")]
    if not history_rows:
        return [], False
    folder_index = _folder_index(candidates)
    pairs: list[tuple[str, str, str]] = []
    for row in sorted(history_rows, key=lambda r: (
            str(r.get("repository_ref", "")), str(r.get("view", "")))):
        repository_ref = str(row.get("repository_ref", ""))
        view = str(row.get("view", ""))
        folders = folder_index.get(repository_ref, [])
        if not folders:
            continue
        for line_no, line in _section_rows(_view_lines(run, view), "coupling:"):
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            _pct, _shared, file_a, file_b = parts
            matches_a = _folder_matches(file_a, folders)
            matches_b = _folder_matches(file_b, folders)
            if not matches_a or not matches_b:
                continue
            ref = f"signals/{view}:{line_no}"
            for candidate_a in matches_a:
                for candidate_b in matches_b:
                    if candidate_a != candidate_b:
                        pairs.append((candidate_a, candidate_b, ref))
    return pairs, True


def _co_change_clusters(run: Path, candidates: list[dict],
                        ) -> tuple[list[dict], bool, str]:
    pairs, available = _co_change_pairs(run, candidates)
    if not available:
        return [], False, "no complete/partial git-history signal view is present in this run"
    rows = []
    for members, evidence in _connected_components(pairs):
        measure = (
            f"{len(members)} module candidate(s) are connected via git-history co-change "
            "(shared-commit) file pairs recorded in the git-history signal view.")
        rows.append(_cluster_row("co-change", measure, members, evidence))
    return rows, True, ""


# --------------------------------------------------------------------------- #
# table-ownership
# --------------------------------------------------------------------------- #

def _table_ownership_clusters(model: dict, node_to_candidates: dict[str, set[str]],
                              nodes_by_id: dict[str, dict],
                              by_id: dict[str, dict]) -> list[dict]:
    table_members: dict[str, set[str]] = defaultdict(set)
    table_evidence: dict[str, set[str]] = defaultdict(set)
    for edge in model.get("edges", []):
        if edge.get("type") != "data" or edge.get("status") != "observed":
            continue
        table_id = str(edge.get("dst", ""))
        if nodes_by_id.get(table_id, {}).get("kind") != "data-store":
            continue
        file_id = str(edge.get("src", ""))
        table_members[table_id] |= node_to_candidates.get(file_id, set())
        table_evidence[table_id] |= {str(item) for item in edge.get("evidence", [])}

    rows = []
    for table_id in sorted(table_members):
        members = set(table_members[table_id]) | node_to_candidates.get(table_id, set())
        if len(members) < 2:
            continue
        node = nodes_by_id.get(table_id, {})
        table_label = str(node.get("label", table_id))
        repository_ref = str(node.get("repository_ref", ""))
        evidence = set(table_evidence[table_id])
        for candidate_id in node_to_candidates.get(table_id, set()):
            evidence |= {str(item) for item in by_id.get(candidate_id, {}).get("evidence", [])}
        measure = (
            f"{len(members)} module candidate(s) carry evidence-linked access to "
            f"data-store {table_label!r} in {repository_ref!r}.")
        rows.append(_cluster_row("table-ownership", measure, members, evidence))
    return rows


# --------------------------------------------------------------------------- #
# build / write
# --------------------------------------------------------------------------- #

def build(run_dir: str | Path, model: dict | None = None) -> dict:
    run = Path(run_dir).expanduser().resolve()
    identities = identity.load(run)
    model = model or _load(run / "system-model.json")
    candidates_doc = _load(run / "module-candidates.json")
    candidates = candidates_doc.get("candidates", [])
    by_id = {str(row["candidate_id"]): row for row in candidates}
    nodes_by_id = {str(node.get("id", "")): node for node in model.get("nodes", [])}
    node_to_candidates = _node_to_candidates(candidates)

    kinds: dict[str, dict] = {}
    clusters: list[dict] = []

    clusters.extend(_finish(kinds, "route-prefix",
                            _route_prefix_clusters(candidates, by_id)))
    clusters.extend(_finish(kinds, "folder", _folder_clusters(candidates, by_id)))
    clusters.extend(_finish(kinds, "import",
                            _import_clusters(model, node_to_candidates)))

    co_change_rows, co_change_available, co_change_reason = _co_change_clusters(
        run, candidates)
    if co_change_available:
        clusters.extend(_finish(kinds, "co-change", co_change_rows))
    else:
        kinds["co-change"] = {"available": False, "reason": co_change_reason}

    clusters.extend(_finish(kinds, "table-ownership", _table_ownership_clusters(
        model, node_to_candidates, nodes_by_id, by_id)))

    return {
        "schema_version": SCHEMA_VERSION,
        "project_ref": identities.project.reference,
        "limits": {
            "folder_depth": _FOLDER_DEPTH,
            "route_prefix_depth": _ROUTE_PREFIX_DEPTH,
            "max_clusters_per_kind": _MAX_CLUSTERS_PER_KIND,
            "max_evidence_refs_per_cluster": _MAX_EVIDENCE_PER_CLUSTER,
        },
        "kinds": kinds,
        "clusters": sorted(clusters, key=lambda row: (row["kind"], row["members"])),
    }


def write(run_dir: str | Path, model: dict | None = None) -> Path:
    run = Path(run_dir).expanduser().resolve()
    out = run / FILENAME
    replace_artifact_text(out, sanitize_text(json.dumps(
        build(run, model), indent=2, sort_keys=True) + "\n"))
    return out
