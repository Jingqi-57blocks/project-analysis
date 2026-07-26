"""Dev-only deterministic parity comparator (57B-86).

Reads TWO completed, deterministic run directories produced by this wrapper
and itemizes every semantic difference between them — added, removed,
reclassified, or plain conflicting facts — across capability records, the
evidence catalog, the System-Model's node/edge/coverage shape, per-lane
coverage, run signals, and discovery facets/evidence. Counts alone are never
reported on their own: this exists precisely because "N differences" hides
*which* facts moved (57B-86).

Only three categories of noise are normalized away before comparing:
timestamps (``scan_date``, ``analyzed_at``), machine-local absolute paths
(workspace root, analyzer root, the run directory itself), and analyzer/tool
version identity (surfaced separately as an informational ``tool_drift``
section instead of being silently dropped). Everything else — including
``targets[].head``/``branch``/``dirty_detail`` — is compared as-is: this tool
assumes both runs were taken over the same repository state, and says so
loudly in ``warnings`` when that assumption looks false, rather than
"fixing" the comparison to hide it.

This module is a companion to the 57B-75 refactor migration: as capability
providers move off the legacy pipeline and onto the 57B-78 execution loop,
this comparator is how a migration is checked for behavioral drift against
its predecessor. It is deliberately NOT a cache, a replay engine, an LLM
consumer, or a cross-run store — it reads two directories and reports; it
keeps no state and consults no history beyond the two runs given to it.

``compare()`` is pure: it only reads files under the two given run
directories. The CLI (`` compare-runs``, wired in :mod:`analysis_wrapper.cli`)
is the only caller that writes anything, and only when ``--report`` is given.

``compare_semantic()`` (57B-114, ``--semantic``) is a substance-level
companion to ``compare()``, equally pure. Where ``compare()`` reports every
byte-shaped fact difference -- including ones a migration is EXPECTED to
cause, like a finding's id or a module's name churning -- semantic mode is
tolerant of exactly that churn and reports only substance: do the SAME
underlying findings still exist (matched by evidence overlap, not id), does
the module map still partition the same candidates the same way, do the
narrative reports still cite real evidence and cover every section at least
as thoroughly. It is layered strictly on top: its ``coverage`` section reads
``compare()``'s own byte-level sections rather than re-deriving the same
check a second way.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# citation grammar (see its `_source_parts`/`_validate_source_ref`); reused
# (not re-derived) exactly as ``evidence/facts.py`` already does for the same
# reason -- parity.py's --semantic citation extraction below classifies a
# narrative-report citation as grammar-valid using this SAME parser.
from .findings import _source_parts
from .module_map import DISPOSITIONS as _DISPOSITIONS

SCHEMA_VERSION = "1.0.0"
SEMANTIC_SCHEMA_VERSION = "1.0.0"

# A "changed" entry is tagged reclassified=True only when every field that
# differs is one of these outcome-shaped fields — the same fact re-judged —
# as opposed to a genuinely different fact (conflicting). "applicable" is
# capabilities.json's own bool spelling of the same axis "applicability"
# names elsewhere (evidence-catalog coverage, discovery facets, ...).
_RECLASSIFICATION_FIELDS = frozenset(
    {"status", "applicability", "applicable", "outcome", "state"})

# Fields stripped from lane-coverage rows before comparison: tool_version and
# version_drift are tool identity (surfaced separately, informationally, as
# tool_drift); warm_cache is build-cache state (warm/cold/n-a) — neither a
# fact about the target nor a tool version, so it is dropped entirely rather
# than routed anywhere.
_STRIPPED_LANE_FIELDS = frozenset({"tool_version", "version_drift", "warm_cache"})


# ---------------------------------------------------------------------------
# Small generic helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any] | None:
    """Read one JSON object artifact, or ``None`` when it is simply absent.

    A present-but-unreadable/malformed artifact is a genuine input error
    (surfaced to the CLI as exit code 2), never silently treated as absent.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot parse {path} as JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _key_text(key: Any) -> str:
    """Human-readable rendering of a (possibly nested-tuple) section key."""
    if isinstance(key, tuple):
        return " / ".join(_key_text(part) for part in key)
    return str(key)


def _diff_keyed(base: dict[Any, Any], candidate: dict[Any, Any]) -> dict[str, list[dict]]:
    """Itemized added/removed/changed between two key -> value maps.

    ``changed`` entries carry both sides in full (never counts-only) plus a
    ``reclassified`` flag: True when every differing field is outcome-shaped
    (see ``_RECLASSIFICATION_FIELDS``) — the same fact re-judged, not a
    conflicting one.

    Ordering sorts on the raw key itself (every key here is a string or a
    tuple of strings/tuples-of-strings, so it orders directly) rather than on
    its rendered ``_key_text`` — the render is for display only, and sorting
    by it risked falling back to hash-seed-dependent set-iteration order on a
    (rare, but possible) text collision between two distinct keys.
    """
    base_keys = set(base)
    candidate_keys = set(candidate)
    added = sorted(candidate_keys - base_keys)
    removed = sorted(base_keys - candidate_keys)
    changed = []
    for key in sorted(base_keys & candidate_keys):
        base_value = base[key]
        candidate_value = candidate[key]
        if base_value == candidate_value:
            continue
        reclassified = False
        if isinstance(base_value, dict) and isinstance(candidate_value, dict):
            differing = {
                field for field in set(base_value) | set(candidate_value)
                if base_value.get(field) != candidate_value.get(field)
            }
            reclassified = bool(differing) and differing <= _RECLASSIFICATION_FIELDS
        changed.append({
            "key": _key_text(key), "base": base_value, "candidate": candidate_value,
            "reclassified": reclassified,
        })
    return {
        "added": [{"key": _key_text(key), "value": candidate[key]} for key in added],
        "removed": [{"key": _key_text(key), "value": base[key]} for key in removed],
        "changed": changed,
    }


def _section(base_map: dict[Any, Any] | None, candidate_map: dict[Any, Any] | None) -> dict:
    """Wrap ``_diff_keyed`` with the presence disclosure every section carries.

    A missing artifact never crashes the comparison; it is projected to an
    empty map for diffing (so every key on the present side surfaces as
    added/removed already) AND its absence is disclosed via
    ``base_present``/``candidate_present`` so a same-shape-by-coincidence case
    (e.g. both sides empty) still surfaces the presence mismatch.
    """
    return {
        "base_present": base_map is not None,
        "candidate_present": candidate_map is not None,
        **_diff_keyed(base_map or {}, candidate_map or {}),
    }


def _rows_to_set_map(rows: set[str]) -> dict[str, str]:
    """Project a canonical-row set into a key==value map for ``_section``.

    Because the key IS the row's own serialized content, ``_diff_keyed`` can
    never produce a "changed" entry for these rows — only added/removed, i.e.
    exactly the set-diff (no pairing) the discovery-evidence section wants.
    """
    return {row: row for row in rows}


def _scrub_paths(text: str, roots: dict[str, str]) -> str:
    """Replace each side's own machine-local roots with stable placeholders."""
    result = text
    for placeholder, root in roots.items():
        if not root:
            continue
        variants = {root}
        try:
            variants.add(str(Path(root).resolve()))
        except OSError:
            pass
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                result = result.replace(variant, placeholder)
    return result


# ---------------------------------------------------------------------------
# Section extractors — each projects one artifact pair into a keyed map.
# ---------------------------------------------------------------------------


def _capability_records(doc: dict | None) -> dict[str, dict] | None:
    if doc is None:
        return None
    result: dict[str, dict] = {}
    for row in doc.get("capabilities", []):
        result[row.get("capability_id", "")] = {
            "applicable": row.get("applicable"),
            "status": row.get("status"),
            "reason": row.get("reason"),
            "expected_artifacts": row.get("expected_artifacts", []),
            "observed_artifacts": row.get("observed_artifacts", []),
            "missing_artifacts": row.get("missing_artifacts", []),
            # Detail rows are NOT separately keyed: a removed/changed detail
            # row surfaces as a "changed" entry on its owning capability, not
            # as a standalone removed entry (documented; see test_parity.py).
            "details": row.get("details", []),
        }
    return result


def _evidence_catalog(doc: dict | None) -> dict[tuple[str, str, str], dict] | None:
    if doc is None:
        return None
    result: dict[tuple[str, str, str], dict] = {}
    for capability_id, entry in doc.get("capabilities", {}).items():
        for item in entry.get("items", []):
            scope = item.get("scope", "")
            coverage = item.get("coverage", {})
            result[(capability_id, scope, "__coverage__")] = dict(coverage)
            for fact_row in item.get("facts", {}).get("items", []):
                fact_id = fact_row.get("fact_id", "")
                # `fact_id` is only a hash of (capability_id, repo_id, kind,
                # natural_key) — two facts sharing that key with DIFFERENT
                # `data` or `source_refs` must not compare clean, so both are
                # part of the compared value, not just kind/coverage.
                result[(capability_id, scope, fact_id)] = {
                    "kind": fact_row.get("kind"),
                    "data": fact_row.get("data", {}),
                    "source_refs": sorted(fact_row.get("source_refs", [])),
                    "coverage": coverage,
                }
    return result


def _system_model_nodes(
    doc: dict | None,
) -> tuple[dict[tuple[str, tuple], dict], dict[str, tuple]] | None:
    """Returns (key -> node value, node id -> key) or None.

    ``producers`` and ``label`` are deliberately EXCLUDED from the compared
    value: producers are the tool-name lineage of a node and legitimately
    change when a capability migrates from the legacy pipeline onto a 57B-78
    provider (that churn must not read as fact drift, which is exactly what
    this comparator exists to check), and label is a display-only rendering
    of the same natural key already carried in ``key``.
    """
    if doc is None:
        return None
    nodes_by_key: dict[tuple[str, tuple], dict] = {}
    id_to_key: dict[str, tuple] = {}
    for node in doc.get("nodes", []):
        key = (node.get("kind", ""), tuple(node.get("key", [])))
        nodes_by_key[key] = {
            "status": node.get("status"),
            "repository_ref": node.get("repository_ref"),
            "evidence": sorted(node.get("evidence", [])),
            "evidence_basis": node.get("evidence_basis"),
            "attrs": node.get("attrs", {}),
        }
        id_to_key[node.get("id", "")] = key
    return nodes_by_key, id_to_key


def _system_model_edges(doc: dict, id_to_key: dict[str, tuple]) -> dict[tuple, dict]:
    """Returns (type, src_key, dst_key, id) -> edge value.

    The edge ``id`` is included as a fourth key component on purpose: system-
    model edge identity carries a discriminator beyond (type, src, dst) —
    per-callsite call edges, read-vs-write data edges, per-specifier import
    edges — folded into ``id`` but not stored as its own field (see
    ``system_model/builder.py``'s ``add_edge``). Keying by (type, src, dst)
    alone collapses those parallel edges into one, silently hiding a
    dropped or reclassified sibling. ``id`` is a pure function of
    (type, src, dst, discriminator) over natural-key-hashed node ids
    (``system_model/ids.py``), so — like the node keys above — it stays
    portable across machines for the same targets; it is never a raw
    internal id or an absolute path.
    """
    result: dict[tuple, dict] = {}
    for edge in doc.get("edges", []):
        src = edge.get("src", "")
        src_key = id_to_key.get(src, ("__unknown__", src))
        unresolved_target = edge.get("unresolved_target")
        dst = edge.get("dst", "")
        if dst == "" or unresolved_target is not None:
            dst_key = ("__unresolved__", json.dumps(unresolved_target, sort_keys=True))
        else:
            dst_key = id_to_key.get(dst, ("__unknown__", dst))
        key = (edge.get("type", ""), src_key, dst_key, edge.get("id", ""))
        result[key] = {
            "status": edge.get("status"),
            "evidence": sorted(edge.get("evidence", [])),
            "evidence_basis": edge.get("evidence_basis"),
            "attrs": edge.get("attrs", {}),
        }
    return result


def _system_model_partitions(doc: dict | None) -> dict[str, dict] | None:
    if doc is None:
        return None
    result: dict[str, dict] = {}
    for name, partition in doc.get("coverage", {}).items():
        result[name] = {
            "status": partition.get("status"),
            # producers are stable tool-name identity, kept; notes are prose
            # and excluded entirely (never fabricated into a fake prose lane).
            "producers": sorted(partition.get("producers", [])),
            "counts": partition.get("counts", {}),
            "caps": partition.get("caps", []),
            "unresolved": partition.get("unresolved", {}),
        }
    return result


def _row_repo_key(row: dict, index: int) -> str:
    """Repo identity for one coverage/signal row, tolerant of pre-57B-88
    artifacts.

    57B-88 (commit 17f7bb3) renamed this field from ``repo_id`` to
    ``repository_ref`` in-place, in callgraph-coverage.json,
    imports/depmap-coverage.json, AND signals/run-summary.json -- a pure
    rename (the JSON key changes, the value doesn't), never additive: a row
    written by either era carries exactly one of the two keys, never both.
    Keying purely by ``row.get("repository_ref", "")`` therefore reads EVERY
    row of a genuine pre-88 doc as the same empty string, collapsing two
    different repos' rows sharing the same secondary key component (lang/
    lane/tool) onto one dict entry and silently dropping one side's facts.
    Falling back to the legacy field recovers the real identity for exactly
    that case. The positional discriminator is a last resort for a row
    carrying neither (not known to occur in practice) -- it guarantees no
    collapse even then, though it only orders correctly within one doc's own
    (already sorted) row list, not across independently-numbered docs.
    """
    ref = row.get("repository_ref") or row.get("repo_id")
    return ref if ref else f"__row_{index}__"


def _lane_coverage(callgraph_doc: dict | None, depmap_doc: dict | None) -> dict | None:
    if callgraph_doc is None and depmap_doc is None:
        return None
    result: dict[tuple, dict] = {}
    for index, row in enumerate((callgraph_doc or {}).get("repos", [])):
        key = ("callgraph", _row_repo_key(row, index), row.get("lang", ""))
        result[key] = {k: v for k, v in row.items() if k not in _STRIPPED_LANE_FIELDS}
    for index, row in enumerate((depmap_doc or {}).get("repos", [])):
        key = ("depmap", _row_repo_key(row, index), row.get("lane", ""))
        result[key] = {k: v for k, v in row.items() if k not in _STRIPPED_LANE_FIELDS}
    return result


def _signals(doc: dict | None) -> dict[tuple, dict] | None:
    if doc is None:
        return None
    result: dict[tuple, dict] = {}
    for index, row in enumerate(doc.get("signals", [])):
        key = (row.get("tool", ""), _row_repo_key(row, index))
        result[key] = {"status": row.get("status"), "reason": row.get("reason")}
    result[("__aggregate__",)] = {"status": doc.get("aggregate_status")}
    return result


def _provider_execution(doc: dict | None) -> dict[tuple, dict] | None:
    """``reason`` is deliberately excluded here — it is free text, surfaced
    instead (scrubbed) via the ``provider_execution_reasons`` prose lane, see
    ``_provider_execution_reasons`` below."""
    if doc is None:
        return None
    result: dict[tuple, dict] = {}
    for row in doc.get("executions", []):
        key = (row.get("provider_id", ""), row.get("repository_ref", ""))
        result[key] = {
            "capability_id": row.get("capability_id"),
            "matched_profiles": sorted(row.get("matched_profiles", [])),
            "outcome": row.get("outcome"),
            "coverage": row.get("coverage"),
            "tools": row.get("tools", []),
        }
    result[("__network_authorized__",)] = {
        "network_authorized": doc.get("network_authorized")}
    return result


def _provider_execution_reasons(
    doc: dict | None, roots: dict[str, str],
) -> list[str] | None:
    """Free-text ``reason`` strings (usually a failed provider's message),
    scrubbed of machine-local paths.

    Surfaced INFORMATIONALLY only (like ``tool_drift`` and the baseline
    header) — never counted by ``has_semantic_differences`` — because a
    provider's exception text is uncontrolled and environment-volatile
    (temp-directory paths, line numbers, PID-suffixed filenames, ...): the
    same underlying failure can render as different text on every run even
    with zero behavioral drift. The behavioral signal that actually matters
    (``outcome``, ``coverage``) is already compared, and counted, by the
    keyed ``provider_execution`` section; this lane exists only so a human
    reading the report isn't left wondering why the reason changed.
    """
    if doc is None:
        return None
    return sorted({
        _scrub_paths(
            f"{row.get('provider_id', '')} / {row.get('repository_ref', '')}: "
            f"{row.get('reason', '')}", roots)
        for row in doc.get("executions", []) if row.get("reason")
    })


def _noise_strip_discovery(doc: dict) -> dict:
    """Deep copy of ``doc`` with only machine-local/tool-identity fields removed.

    ``route_inventory``/``ui_route_linkage`` are POPPED unconditionally, not
    merely left alone: a PRE-57B-84-B2 run still carries them EMBEDDED here
    (mixed-era comparison — 57B-87's acceptance gate compares against the
    pre-refactor mainline run, exactly this shape), and their facts are
    compared exactly ONCE, via ``_resolve_route_docs``'s dedicated
    route_inventory/ui_route_linkage params below — never also folded into
    this doc's own per-repo remainder/facet extraction. A POST-B2 run never
    has these keys here at all (they live in ``routes/route-inventory.json``/
    ``routes/ui-route-linkage.json`` instead), so the pop is a no-op there."""
    projected = json.loads(json.dumps(doc))
    projected.pop("workspace_root", None)
    projected.pop("route_inventory", None)
    projected.pop("ui_route_linkage", None)
    for repo in projected.get("repos", []):
        if isinstance(repo, dict):
            provenance = repo.get("provenance")
            if isinstance(provenance, dict):
                provenance.pop("path", None)
    return projected


def _noise_strip_route_inventory(doc: dict) -> dict:
    """Deep copy of a route-inventory doc with only its tool-identity fields
    removed (57B-84 B2: split out of ``_noise_strip_discovery`` when
    route_inventory moved to its own artifact). Applies equally to the
    standalone ``routes/route-inventory.json`` shape and the pre-B2
    embedded ``discovery-report.json["route_inventory"]`` shape — both
    carry the same ``tool``/``tool_path``/``tool_version``/``version_drift``
    fields, so one stripping function covers either era."""
    projected = json.loads(json.dumps(doc))
    projected.pop("tool_path", None)
    projected.pop("tool_version", None)
    projected.pop("version_drift", None)
    return projected


def _resolve_route_docs(
    discovery_doc: dict | None, standalone_route_inventory: dict | None,
    standalone_ui_route_linkage: dict | None,
) -> tuple[dict | None, dict | None]:
    """Effective, RAW (tool identity NOT stripped) (route_inventory,
    ui_route_linkage) for one run: the standalone ``routes/*.json`` docs
    when present (57B-84 B2+), else the field EMBEDDED in
    ``discovery-report.json`` (pre-B2 runs) — so a mixed-era comparison
    sees route facts from EITHER shape, never silently drops the older
    run's route rows to zero (which would make every route/linkage row in
    the newer run look like a spurious add, and swallow any REAL semantic
    route difference between the two runs).

    Deliberately RAW: ``_collect_tool_versions`` needs the genuine
    ``tool``/``tool_version`` fields to detect drift; a caller comparing
    row CONTENT strips tool identity separately via
    ``_noise_strip_route_inventory`` — stripping here would make every
    tool-version drift invisible to that check too, silently."""
    inventory = standalone_route_inventory
    if inventory is None and discovery_doc is not None:
        inventory = discovery_doc.get("route_inventory")
    linkage = standalone_ui_route_linkage
    if linkage is None and discovery_doc is not None:
        linkage = discovery_doc.get("ui_route_linkage")
    return inventory, linkage


def _discovery_facets(doc: dict) -> dict[tuple[str, str], dict]:
    result: dict[tuple[str, str], dict] = {}
    for repo in doc.get("repos", []):
        repository_ref = repo.get("repository_ref", "")
        for facet in repo.get("technology_facets", []):
            key = (repository_ref, facet.get("profile_id", ""))
            result[key] = {
                "kind": facet.get("kind"),
                "scope_roots": sorted(facet.get("scope_roots", [])),
                "state": facet.get("state"),
                "confidence": facet.get("confidence"),
            }
    return result


def _discovery_evidence_rows(doc: dict, route_inventory: dict | None = None,
                            ui_route_linkage: dict | None = None) -> set[str]:
    """Canonical-row set for everything in discovery NOT already covered by
    ``_discovery_facets`` (technology_facets) or the prose lanes.

    ``route_inventory``/``ui_route_linkage`` (57B-84 B2) are passed in
    separately — the CALLER (``compare``, via ``_resolve_route_docs``)
    resolves whichever era's doc actually holds them (the standalone
    ``routes.emit.assemble`` artifact, or the pre-B2 embedded
    discovery-report.json field); this function itself is agnostic to
    which. ``doc`` here has already had both keys popped by
    ``_noise_strip_discovery`` regardless of era, so route facts are
    compared exactly once, only through these two params."""
    rows: set[str] = set()
    for repo in doc.get("repos", []):
        if not isinstance(repo, dict):
            continue
        remainder = {k: v for k, v in repo.items()
                     if k not in {"technology_facets", "notes"}}
        rows.add(json.dumps(remainder, sort_keys=True))
    for row in (route_inventory or {}).get("rows", []):
        rows.add(json.dumps(row, sort_keys=True))
    for row in (ui_route_linkage or {}).get("rows", []):
        rows.add(json.dumps(row, sort_keys=True))
    return rows


def _prose_lane(doc: dict | None, field: str, roots: dict[str, str]) -> list[str] | None:
    if doc is None:
        return None
    return sorted({_scrub_paths(str(item), roots) for item in doc.get(field, [])})


def _prose_diff(base: list[str] | None, candidate: list[str] | None) -> dict:
    base_set = set(base or [])
    candidate_set = set(candidate or [])
    return {
        "base_present": base is not None,
        "candidate_present": candidate is not None,
        "added": sorted(candidate_set - base_set),
        "removed": sorted(base_set - candidate_set),
    }


# ---------------------------------------------------------------------------
# Baseline header + tool drift (informational; never counted as differences)
# ---------------------------------------------------------------------------


def _reference_map(identity_doc: dict | None) -> dict[str, str]:
    if identity_doc is None:
        return {}
    return {
        row.get("internal_id", ""): row.get("reference", "")
        for row in identity_doc.get("repositories", [])
    }


def _targets_by_reference(
    provenance_doc: dict | None, identity_doc: dict | None,
) -> dict[str, dict]:
    """run-provenance.json targets, keyed by REFERENCE (not repo_id).

    ``targets[].repo_id`` is itself derived from an absolute canonical path
    (``stable_repo_id``), so it is machine-local — comparing it directly
    across two runs taken on different machines/checkouts would spuriously
    "differ" even for the identical repository at the identical commit.
    Resolving through each side's OWN identity-map.json first keeps the
    comparison anchored to the portable human reference instead.
    """
    if provenance_doc is None:
        return {}
    references = _reference_map(identity_doc)
    result: dict[str, dict] = {}
    for row in provenance_doc.get("targets", []):
        repo_id = row.get("repo_id", "")
        reference = references.get(repo_id, repo_id)
        result[reference] = {
            "head": row.get("head"), "branch": row.get("branch"),
            "dirty_detail": row.get("dirty_detail"), "state": row.get("state"),
        }
    return result


def _identity_block(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    analyzer = doc.get("analyzer", {})
    generation = doc.get("generation", {})
    return {
        "analyzer": {
            key: analyzer.get(key) for key in
            ("package", "version", "git_head", "git_branch", "dirty_detail",
             "source_state_sha256")
        },
        "generation": {key: generation.get(key) for key in ("model", "effort", "language")},
        "preparation": doc.get("preparation"),
    }


def _baseline(
    base_provenance: dict | None, candidate_provenance: dict | None,
    base_identity: dict | None, candidate_identity: dict | None,
    base_sm_generator: str | None, candidate_sm_generator: str | None,
) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    base_block = _identity_block(base_provenance)
    candidate_block = _identity_block(candidate_provenance)

    if base_provenance is None or candidate_provenance is None:
        warnings.append(
            "run-provenance.json missing on one or both sides; baseline "
            "identity/target comparison is incomplete")
    else:
        base_targets = _targets_by_reference(base_provenance, base_identity)
        candidate_targets = _targets_by_reference(candidate_provenance, candidate_identity)
        shared = set(base_targets) & set(candidate_targets)
        if set(base_targets) != set(candidate_targets) or any(
            base_targets[ref] != candidate_targets[ref] for ref in shared
        ):
            warnings.append(
                "TARGETS DIFFER -- comparison unsound: base and candidate runs "
                "were not taken over the same repository state")
        base_prep = {k: v for k, v in (base_block["preparation"] or {}).items()
                     if k != "scan_date"}
        candidate_prep = {k: v for k, v in (candidate_block["preparation"] or {}).items()
                          if k != "scan_date"}
        if base_prep != candidate_prep:
            warnings.append(
                "preparation options differ between runs (excluding scan_date): "
                "the runs were not configured identically")

    baseline = {
        "base": {"identity": base_block, "system_model_generator": base_sm_generator},
        "candidate": {
            "identity": candidate_block, "system_model_generator": candidate_sm_generator},
    }
    return baseline, warnings


def _collect_tool_versions(
    callgraph_doc: dict | None, depmap_doc: dict | None,
    discovery_doc: dict | None, provenance_doc: dict | None,
    route_inventory_doc: dict | None = None,
) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}

    def add(tool: str, version: str) -> None:
        if tool:
            versions.setdefault(tool, set()).add(version or "")

    for row in (callgraph_doc or {}).get("repos", []):
        add(row.get("tool", ""), row.get("tool_version", ""))
    for row in (depmap_doc or {}).get("repos", []):
        add(row.get("tool", ""), row.get("tool_version", ""))
    # 57B-84 B2: route_inventory_doc is the CALLER's already-resolved doc
    # (``_resolve_route_docs``) — the standalone routes/route-inventory.json
    # artifact, or the pre-B2 embedded discovery field as a fallback for a
    # mixed-era comparison. This function only ever sees one resolved value.
    inventory = route_inventory_doc or {}
    add(inventory.get("tool", ""), inventory.get("tool_version", ""))
    for row in (provenance_doc or {}).get("tool_versions", []):
        add(row.get("tool", ""), row.get("version", ""))
    return versions


def _tool_drift(
    base_versions: dict[str, set[str]], candidate_versions: dict[str, set[str]],
) -> list[dict]:
    rows = []
    for tool in sorted(set(base_versions) | set(candidate_versions)):
        base_set = base_versions.get(tool, set())
        candidate_set = candidate_versions.get(tool, set())
        if base_set != candidate_set:
            rows.append({
                "tool": tool,
                "base_version": sorted(base_set),
                "candidate_version": sorted(candidate_set),
            })
    return rows


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _load_all(run: Path) -> dict[str, dict | None]:
    discovery = _load_json(run / "discovery-report.json")
    return {
        "capabilities": _load_json(run / "capabilities.json"),
        "evidence_catalog": _load_json(run / "evidence-catalog.json"),
        "system_model": _load_json(run / "system-model.json"),
        "callgraph": _load_json(run / "callgraph-coverage.json"),
        "depmap": _load_json(run / "imports" / "depmap-coverage.json"),
        "signals": _load_json(run / "signals" / "run-summary.json"),
        "discovery": discovery,
        # 57B-84 B2: route_inventory/ui_route_linkage moved from top-level
        # discovery-report.json fields to their own routes.emit.assemble
        # run-level docs — read from there now, same as callgraph/depmap.
        "route_inventory": _load_json(run / "routes" / "route-inventory.json"),
        "ui_route_linkage": _load_json(run / "routes" / "ui-route-linkage.json"),
        "provenance": _load_json(run / "run-provenance.json"),
        "identity": _load_json(run / "identity-map.json"),
        "provider_execution": _load_json(run / "provider-execution.json"),
    }


def _roots_for(run: Path, docs: dict[str, dict | None]) -> dict[str, str]:
    workspace_root = ""
    discovery = docs.get("discovery")
    if discovery is not None:
        workspace_root = str(discovery.get("workspace_root", "") or "")
    if not workspace_root and docs.get("identity") is not None:
        workspace_root = str(
            docs["identity"].get("project", {}).get("canonical_path", "") or "")
    analyzer_root = ""
    if docs.get("provenance") is not None:
        analyzer_root = str(docs["provenance"].get("analyzer", {}).get("root", "") or "")
    return {"$WORKSPACE": workspace_root, "$ANALYZER": analyzer_root, "$RUN": str(run)}


def compare(base_run: str | Path, candidate_run: str | Path) -> dict[str, Any]:
    """Compare two completed run directories; return the deterministic report.

    Pure read: nothing under either directory is modified. Raises
    ``ValueError`` for a nonexistent directory or an unreadable/malformed
    artifact (both mapped to CLI exit code 2 — genuine input errors, not
    semantic differences).
    """
    base = Path(base_run).expanduser().resolve()
    candidate = Path(candidate_run).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"base run directory does not exist: {base}")
    if not candidate.is_dir():
        raise ValueError(f"candidate run directory does not exist: {candidate}")

    base_docs = _load_all(base)
    candidate_docs = _load_all(candidate)
    base_roots = _roots_for(base, base_docs)
    candidate_roots = _roots_for(candidate, candidate_docs)

    baseline, warnings = _baseline(
        base_docs["provenance"], candidate_docs["provenance"],
        base_docs["identity"], candidate_docs["identity"],
        (base_docs["system_model"] or {}).get("generator")
        if base_docs["system_model"] is not None else None,
        (candidate_docs["system_model"] or {}).get("generator")
        if candidate_docs["system_model"] is not None else None,
    )
    # Effective route docs for THIS run: the standalone routes/*.json when
    # present (57B-84 B2+), else the field embedded in discovery-report.json
    # (pre-B2) — see _resolve_route_docs's own docstring for why a mixed-era
    # comparison needs this (57B-87's acceptance gate compares against the
    # pre-refactor mainline run, exactly this shape). Resolved once, up
    # front, RAW (tool identity intact) — _collect_tool_versions needs that;
    # the row-content comparison below strips it separately.
    base_route_inventory_raw, base_ui_route_linkage = _resolve_route_docs(
        base_docs["discovery"], base_docs["route_inventory"], base_docs["ui_route_linkage"])
    candidate_route_inventory_raw, candidate_ui_route_linkage = _resolve_route_docs(
        candidate_docs["discovery"], candidate_docs["route_inventory"],
        candidate_docs["ui_route_linkage"])

    tool_drift = _tool_drift(
        _collect_tool_versions(base_docs["callgraph"], base_docs["depmap"],
                               base_docs["discovery"], base_docs["provenance"],
                               base_route_inventory_raw),
        _collect_tool_versions(candidate_docs["callgraph"], candidate_docs["depmap"],
                               candidate_docs["discovery"], candidate_docs["provenance"],
                               candidate_route_inventory_raw),
    )

    sections: dict[str, dict[str, Any]] = {}
    sections["capability_records"] = _section(
        _capability_records(base_docs["capabilities"]),
        _capability_records(candidate_docs["capabilities"]))
    sections["evidence_catalog"] = _section(
        _evidence_catalog(base_docs["evidence_catalog"]),
        _evidence_catalog(candidate_docs["evidence_catalog"]))

    base_nodes = _system_model_nodes(base_docs["system_model"])
    candidate_nodes = _system_model_nodes(candidate_docs["system_model"])
    sections["system_model_nodes"] = _section(
        base_nodes[0] if base_nodes is not None else None,
        candidate_nodes[0] if candidate_nodes is not None else None)
    sections["system_model_edges"] = _section(
        _system_model_edges(base_docs["system_model"], base_nodes[1])
        if base_nodes is not None else None,
        _system_model_edges(candidate_docs["system_model"], candidate_nodes[1])
        if candidate_nodes is not None else None)
    sections["system_model_partitions"] = _section(
        _system_model_partitions(base_docs["system_model"]),
        _system_model_partitions(candidate_docs["system_model"]))
    sections["lane_coverage"] = _section(
        _lane_coverage(base_docs["callgraph"], base_docs["depmap"]),
        _lane_coverage(candidate_docs["callgraph"], candidate_docs["depmap"]))
    sections["signals"] = _section(
        _signals(base_docs["signals"]), _signals(candidate_docs["signals"]))

    base_discovery = (
        _noise_strip_discovery(base_docs["discovery"])
        if base_docs["discovery"] is not None else None)
    candidate_discovery = (
        _noise_strip_discovery(candidate_docs["discovery"])
        if candidate_docs["discovery"] is not None else None)
    # Row-content comparison strips tool identity (already surfaced above,
    # informationally, via tool_drift) so a version bump alone never counts
    # as a semantic route-fact difference.
    base_route_inventory = (
        _noise_strip_route_inventory(base_route_inventory_raw)
        if base_route_inventory_raw is not None else None)
    candidate_route_inventory = (
        _noise_strip_route_inventory(candidate_route_inventory_raw)
        if candidate_route_inventory_raw is not None else None)
    sections["discovery_facets"] = _section(
        _discovery_facets(base_discovery) if base_discovery is not None else None,
        _discovery_facets(candidate_discovery) if candidate_discovery is not None else None)
    sections["discovery_evidence"] = _section(
        _rows_to_set_map(_discovery_evidence_rows(
            base_discovery, base_route_inventory, base_ui_route_linkage))
        if base_discovery is not None else None,
        _rows_to_set_map(_discovery_evidence_rows(
            candidate_discovery, candidate_route_inventory, candidate_ui_route_linkage))
        if candidate_discovery is not None else None)
    sections["provider_execution"] = _section(
        _provider_execution(base_docs["provider_execution"]),
        _provider_execution(candidate_docs["provider_execution"]))

    prose = {
        "not_targeted": _prose_diff(
            _prose_lane(base_discovery, "not_targeted", base_roots)
            if base_discovery is not None else None,
            _prose_lane(candidate_discovery, "not_targeted", candidate_roots)
            if candidate_discovery is not None else None),
        "reduced_coverage_targets": _prose_diff(
            _prose_lane(base_discovery, "reduced_coverage_targets", base_roots)
            if base_discovery is not None else None,
            _prose_lane(candidate_discovery, "reduced_coverage_targets", candidate_roots)
            if candidate_discovery is not None else None),
    }
    # Informational, like tool_drift and the baseline header — NOT one of the
    # counted `prose` lanes, and deliberately not part of `sections` either:
    # provider-authored exception text is uncontrolled/environment-volatile
    # (temp paths, line numbers), so it is shown for a human reader but never
    # counted (see _provider_execution_reasons's docstring for the full why).
    provider_execution_reasons = _prose_diff(
        _provider_execution_reasons(base_docs["provider_execution"], base_roots),
        _provider_execution_reasons(candidate_docs["provider_execution"], candidate_roots))

    # `by_section` is the SOLE source of the total below (57B-112 §2): a prior
    # version added the prose lanes' contribution into `total` a second time,
    # independently of `by_section` -- which never carried a `prose/*` entry
    # at all -- so a human reconciling the total against the *printed*
    # per-section counts (which also never showed prose) came up exactly the
    # prose count short (98,475 vs 98,474 on a real mainline comparison,
    # traced to one `not_targeted` addition). Folding the prose lanes into
    # `by_section` itself (instead of adding them on the side) makes `total`
    # a pure `sum(by_section.values())` -- it cannot diverge from its own
    # addends again because there is no second, independently-accumulated
    # number left to diverge from.
    by_section = {
        name: len(section["added"]) + len(section["removed"]) + len(section["changed"])
        for name, section in sections.items()
    }
    by_section.update({
        f"prose/{name}": len(rows["added"]) + len(rows["removed"])
        for name, rows in prose.items()
    })
    total = sum(by_section.values())

    return {
        "schema_version": SCHEMA_VERSION,
        "baseline": baseline,
        "warnings": warnings,
        "tool_drift": tool_drift,
        "provider_execution_reasons": provider_execution_reasons,
        "sections": sections,
        "prose": prose,
        "summary": {"total_differences": total, "by_section": by_section},
    }


def has_semantic_differences(report: dict[str, Any]) -> bool:
    """True iff ``report`` carries any actual difference.

    ``baseline``/``warnings``/``tool_drift``/``provider_execution_reasons``
    are informational and never counted; a section's mere presence-mismatch
    (an artifact vanished or appeared) counts even when its keyed diff
    happens to be empty otherwise.
    """
    for section in report["sections"].values():
        if section["base_present"] != section["candidate_present"]:
            return True
        if section["added"] or section["removed"] or section["changed"]:
            return True
    for rows in report["prose"].values():
        if rows["base_present"] != rows["candidate_present"]:
            return True
        if rows["added"] or rows["removed"]:
            return True
    return False


# ---------------------------------------------------------------------------
# --semantic mode (57B-114): substance-level comparison, tolerant of the
# id/wording churn a migration is EXPECTED to cause. A section whose backing
# artifact is present on NEITHER side degrades to the literal string
# "not present in both runs" (a deterministic-only pair has no narrative
# reports/module map at all) -- this is an expected shape, not an error, and
# is never treated as a difference by ``has_semantic_mode_differences``.
# ---------------------------------------------------------------------------


_NARRATIVE_DOCS = ("overview.md", "technical-overview.md")
_NOT_PRESENT = "not present in both runs"


def _read_text(path: Path) -> str | None:
    """Read one narrative-report artifact, or ``None`` when it is absent.

    Mirrors ``_load_json``'s presence/error split: absence is a normal,
    expected shape (a deterministic-only run has no narrative reports);
    an unreadable *present* file is a genuine input error.
    """
    if not path.is_file():
        return None
    try:
        return path.read_text("utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


# --- findings: matched by evidence overlap, not finding_id -----------------


def _finding_evidence_spans(finding: dict) -> dict[tuple[str, str], tuple[int, int]]:
    """(repository_ref, relative_path) -> (min_line, max_line) merged across
    every source-ref-shaped evidence ref this finding cites. Metric/signal
    refs (``metric:...``, ``signals/...``) carry no repo+file identity, so
    they never contribute a span -- consistent with the task's own framing
    ("same repo + file path with overlapping line ranges")."""
    spans: dict[tuple[str, str], tuple[int, int]] = {}
    for item in finding.get("evidence", []) or []:
        if not isinstance(item, dict):
            continue
        for ref in item.get("refs", []) or []:
            parts = _source_parts(str(ref))
            if not parts:
                continue
            repository_ref, _revision, relative, line_text = parts
            line = int(line_text)
            key = (repository_ref, relative)
            low, high = spans.get(key, (line, line))
            spans[key] = (min(low, line), max(high, line))
    return spans


def _spans_overlap_count(base: dict[tuple[str, str], tuple[int, int]],
                         candidate: dict[tuple[str, str], tuple[int, int]]) -> int:
    """Count of (repo, file) keys shared by both findings whose line ranges
    overlap -- the matching SCORE two candidate findings are ranked by, not
    just a yes/no test, so the greedy matcher below prefers the strongest
    evidence correspondence when a finding could plausibly pair with more
    than one candidate on the other side."""
    count = 0
    for key, (base_low, base_high) in base.items():
        other = candidate.get(key)
        if other is not None and base_low <= other[1] and other[0] <= base_high:
            count += 1
    return count


_FINDING_SCALAR_FIELDS = ("priority", "confidence")


def _finding_deltas(base: dict, candidate: dict) -> dict[str, dict]:
    """Substance deltas between two MATCHED findings -- priority/confidence/
    affected-module changes, i.e. the same underlying finding re-judged."""
    deltas: dict[str, dict] = {}
    for field_name in _FINDING_SCALAR_FIELDS:
        if base.get(field_name) != candidate.get(field_name):
            deltas[field_name] = {"base": base.get(field_name),
                                  "candidate": candidate.get(field_name)}
    base_modules = sorted(set(base.get("affected_modules", []) or []))
    candidate_modules = sorted(set(candidate.get("affected_modules", []) or []))
    if base_modules != candidate_modules:
        deltas["affected_modules"] = {"base": base_modules, "candidate": candidate_modules}
    return deltas


def _match_findings(base_rows: list[dict], candidate_rows: list[dict]) -> dict[str, Any]:
    base_by_id = {str(row.get("finding_id", "")): row for row in base_rows
                 if isinstance(row, dict)}
    candidate_by_id = {str(row.get("finding_id", "")): row for row in candidate_rows
                      if isinstance(row, dict)}
    base_spans = {finding_id: _finding_evidence_spans(row)
                 for finding_id, row in base_by_id.items()}
    candidate_spans = {finding_id: _finding_evidence_spans(row)
                      for finding_id, row in candidate_by_id.items()}

    scored: list[tuple[int, str, str]] = []
    for base_id, base_row in base_by_id.items():
        for candidate_id, candidate_row in candidate_by_id.items():
            if base_row.get("lens") != candidate_row.get("lens"):
                continue
            overlap = _spans_overlap_count(base_spans[base_id], candidate_spans[candidate_id])
            if overlap:
                scored.append((overlap, base_id, candidate_id))
    # Greedy, strongest evidence-overlap first; ids break ties deterministically.
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))

    matched_base: set[str] = set()
    matched_candidate: set[str] = set()
    pairs = []
    for overlap, base_id, candidate_id in scored:
        if base_id in matched_base or candidate_id in matched_candidate:
            continue
        matched_base.add(base_id)
        matched_candidate.add(candidate_id)
        pairs.append({
            "base_finding_id": base_id, "candidate_finding_id": candidate_id,
            "evidence_overlap": overlap,
            "deltas": _finding_deltas(base_by_id[base_id], candidate_by_id[candidate_id]),
        })
    pairs.sort(key=lambda row: (row["base_finding_id"], row["candidate_finding_id"]))
    return {
        "matched": pairs,
        "unmatched_left": sorted(set(base_by_id) - matched_base),
        "unmatched_right": sorted(set(candidate_by_id) - matched_candidate),
    }


def _findings_section(base_run: Path, candidate_run: Path) -> dict | str:
    base_doc = _load_json(base_run / "findings.json")
    candidate_doc = _load_json(candidate_run / "findings.json")
    if base_doc is None and candidate_doc is None:
        return _NOT_PRESENT
    result = _match_findings(
        (base_doc or {}).get("findings", []) or [],
        (candidate_doc or {}).get("findings", []) or [])
    result["base_present"] = base_doc is not None
    result["candidate_present"] = candidate_doc is not None
    return result


# --- module map: ids, candidate-membership sets, classification diffs ------


def _module_map_projection(doc: dict) -> dict[str, dict]:
    membership: dict[str, set[str]] = {}
    for row in doc.get("candidate_dispositions", []) or []:
        for module_id in row.get("module_ids", []) or []:
            membership.setdefault(module_id, set()).add(row.get("candidate_id", ""))
    result: dict[str, dict] = {}
    for row in doc.get("modules", []) or []:
        module_id = row.get("module_id", "")
        result[module_id] = {
            "name": row.get("name"),
            "classification": row.get("classification"),
            "aliases": sorted(set(row.get("aliases", []) or [])),
            "candidate_members": sorted(membership.get(module_id, set())),
        }
    return result


def _module_map_section(base_run: Path, candidate_run: Path) -> dict | str:
    base_doc = _load_json(base_run / "module-map.json")
    candidate_doc = _load_json(candidate_run / "module-map.json")
    if base_doc is None and candidate_doc is None:
        return _NOT_PRESENT
    base_modules = _module_map_projection(base_doc) if base_doc is not None else {}
    candidate_modules = (
        _module_map_projection(candidate_doc) if candidate_doc is not None else {})
    added = sorted(set(candidate_modules) - set(base_modules))
    removed = sorted(set(base_modules) - set(candidate_modules))
    changed = [
        {"module_id": module_id, "base": base_modules[module_id],
         "candidate": candidate_modules[module_id]}
        for module_id in sorted(set(base_modules) & set(candidate_modules))
        if base_modules[module_id] != candidate_modules[module_id]
    ]
    return {
        "base_present": base_doc is not None, "candidate_present": candidate_doc is not None,
        "added": added, "removed": removed, "changed": changed,
    }


# --- citations: extracted from the narrative reports, grammar validated ----


# Broad recall on purpose: this only needs to FIND citation-shaped candidates
# (repo@revision:relative/path:line) embedded in free prose; each candidate
# is then classified valid/invalid by the SAME grammar findings.json's own
# refs are validated against (``_source_parts``), reused rather than
# re-derived as a second, looser regex would be. Trailing prose punctuation
# (a sentence-ending period/comma right after an unbacktick'd citation) is
# stripped before validation so it never masquerades as an invalid line number.
_CITATION_CANDIDATE = re.compile(r"[^\s`\[\]()]+@[^\s`\[\]()]+:[^\s`\[\]()]+")
_CITATION_TRAILING_PUNCT = ".,;:!?)]}"


def _citation_candidates(text: str) -> set[str]:
    return {match.rstrip(_CITATION_TRAILING_PUNCT)
           for match in _CITATION_CANDIDATE.findall(text)}


def _citations_for_text(text: str) -> tuple[list[str], int]:
    """(sorted grammar-valid citation set, count of grammar-invalid candidates)."""
    candidates = _citation_candidates(text)
    valid = sorted(token for token in candidates if _source_parts(token) is not None)
    return valid, len(candidates) - len(valid)


def _citations_section(base_run: Path, candidate_run: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in _NARRATIVE_DOCS:
        base_text = _read_text(base_run / name)
        candidate_text = _read_text(candidate_run / name)
        if base_text is None and candidate_text is None:
            result[name] = _NOT_PRESENT
            continue
        base_valid, base_invalid = _citations_for_text(base_text) if base_text else ([], 0)
        candidate_valid, candidate_invalid = (
            _citations_for_text(candidate_text) if candidate_text else ([], 0))
        result[name] = {
            "base_present": base_text is not None,
            "candidate_present": candidate_text is not None,
            "base_valid_count": len(base_valid), "candidate_valid_count": len(candidate_valid),
            "base_invalid_count": base_invalid, "candidate_invalid_count": candidate_invalid,
            "added": sorted(set(candidate_valid) - set(base_valid)),
            "removed": sorted(set(base_valid) - set(candidate_valid)),
        }
    return result


# --- disposition totals: per-class candidate-disposition counts ------------


def _disposition_totals(doc: dict) -> dict[str, int]:
    counts = {name: 0 for name in _DISPOSITIONS}
    for row in doc.get("candidate_dispositions", []) or []:
        disposition = row.get("disposition", "")
        counts[disposition] = counts.get(disposition, 0) + 1
    return counts


def _disposition_totals_section(base_run: Path, candidate_run: Path) -> dict | str:
    base_doc = _load_json(base_run / "module-map.json")
    candidate_doc = _load_json(candidate_run / "module-map.json")
    if base_doc is None and candidate_doc is None:
        return _NOT_PRESENT
    base_totals = _disposition_totals(base_doc) if base_doc is not None else {}
    candidate_totals = _disposition_totals(candidate_doc) if candidate_doc is not None else {}
    all_classes = sorted(set(base_totals) | set(candidate_totals))
    return {
        "base_present": base_doc is not None, "candidate_present": candidate_doc is not None,
        "base": base_totals, "candidate": candidate_totals,
        "equal": base_totals == candidate_totals,
        "differing_classes": [
            name for name in all_classes
            if base_totals.get(name, 0) != candidate_totals.get(name, 0)],
    }


# --- coverage: a semantic READING of compare()'s own byte-level sections ---


_COVERAGE_SECTION_NAMES = ("capability_records", "lane_coverage", "signals")


def _section_equal(section: dict) -> bool:
    return (section["base_present"] == section["candidate_present"]
           and not section["added"] and not section["removed"] and not section["changed"])


def _coverage_section(byte_report: dict) -> dict[str, dict]:
    """Lens/signal status-table equality, read off ``compare()``'s own
    ``capability_records``/``lane_coverage``/``signals`` sections rather than
    re-deriving the same check a second, potentially-diverging way."""
    sections = byte_report["sections"]
    return {
        name: {
            "equal": _section_equal(sections[name]),
            "difference_count": (
                len(sections[name]["added"]) + len(sections[name]["removed"])
                + len(sections[name]["changed"])),
        }
        for name in _COVERAGE_SECTION_NAMES
    }


# --- section completeness: presence + word counts + non-regression gate ---


_H2_HEADING = re.compile(r"(?m)^##[ \t]+(.+?)[ \t]*$")


def _sections_by_heading(text: str) -> dict[str, int]:
    """H2 heading text -> word count of its body (up to the next H2, or EOF).

    H2 is the section granularity both narrative templates share
    (``technical-overview.md``'s flat H2 sections; ``overview.md``'s 16
    numbered H2 sections) -- nested H3 content is counted as part of its
    parent H2, not split out further.
    """
    matches = list(_H2_HEADING.finditer(text))
    result: dict[str, int] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[match.group(1).strip()] = len(text[start:end].split())
    return result


def _section_completeness_for(base_text: str | None, candidate_text: str | None) -> dict | str:
    if base_text is None and candidate_text is None:
        return _NOT_PRESENT
    base_sections = _sections_by_heading(base_text) if base_text is not None else {}
    candidate_sections = (
        _sections_by_heading(candidate_text) if candidate_text is not None else {})
    rows = {}
    for name in sorted(set(base_sections) | set(candidate_sections)):
        base_words = base_sections.get(name)
        candidate_words = candidate_sections.get(name, 0)
        rows[name] = {
            "base_present": name in base_sections, "candidate_present": name in candidate_sections,
            "base_word_count": base_words or 0, "candidate_word_count": candidate_words,
            # A section absent from base has no floor to violate; one present
            # in base must never SHRINK in the candidate (the non-regression
            # gate this feeds: a migration must never simplify a report away).
            "equal_or_greater": True if base_words is None else candidate_words >= base_words,
        }
    return {
        "base_present": base_text is not None, "candidate_present": candidate_text is not None,
        "sections": rows,
    }


def _section_completeness_section(base_run: Path, candidate_run: Path) -> dict[str, Any]:
    return {
        name: _section_completeness_for(
            _read_text(base_run / name), _read_text(candidate_run / name))
        for name in _NARRATIVE_DOCS
    }


# --- orchestration -----------------------------------------------------


def compare_semantic(base_run: str | Path, candidate_run: str | Path) -> dict[str, Any]:
    """Compare two completed run directories at the substance level.

    Pure read, like ``compare()`` (which this calls first, both for its own
    directory-existence/malformed-artifact checks and to read its
    ``coverage``-feeding sections without a second implementation of the same
    check). Degrades gracefully section-by-section: an optional artifact
    (findings.json, module-map.json, the narrative reports) absent on BOTH
    sides never crashes the comparison, it marks that section
    ``"not present in both runs"`` -- the expected shape for a
    deterministic-only run pair.
    """
    byte_report = compare(base_run, candidate_run)
    base = Path(base_run).expanduser().resolve()
    candidate = Path(candidate_run).expanduser().resolve()
    return {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "findings": _findings_section(base, candidate),
        "module_map": _module_map_section(base, candidate),
        "citations": _citations_section(base, candidate),
        "disposition_totals": _disposition_totals_section(base, candidate),
        "coverage": _coverage_section(byte_report),
        "section_completeness": _section_completeness_section(base, candidate),
    }


def has_semantic_mode_differences(report: dict[str, Any]) -> bool:
    """True iff ``report`` (from ``compare_semantic``) carries any actual
    substance difference. A section reading ``"not present in both runs"``
    is an expected shape, never a difference."""
    findings = report["findings"]
    if findings != _NOT_PRESENT and (
            findings["unmatched_left"] or findings["unmatched_right"]
            or any(pair["deltas"] for pair in findings["matched"])):
        return True
    module_map = report["module_map"]
    if module_map != _NOT_PRESENT and (
            module_map["added"] or module_map["removed"] or module_map["changed"]
            or module_map["base_present"] != module_map["candidate_present"]):
        return True
    for doc in report["citations"].values():
        if doc != _NOT_PRESENT and (doc["added"] or doc["removed"]):
            return True
    disposition = report["disposition_totals"]
    if disposition != _NOT_PRESENT and not disposition["equal"]:
        return True
    if any(not row["equal"] for row in report["coverage"].values()):
        return True
    for doc in report["section_completeness"].values():
        if doc == _NOT_PRESENT:
            continue
        if any(not section["equal_or_greater"] for section in doc["sections"].values()):
            return True
    return False
