"""Normalize discovery evidence into nodes + non-call edges.

CANONICAL COMPLETENESS RULE (57B-31): routes come from the detailed
``route_liveness`` rows and tables from the uncapped ``table_evidence`` map —
NEVER from the capped ``module_signals.routes`` / ``module_signals.tables``
human-synthesis summaries (those are excluded from the canonical graph; the cap
they carry is disclosed in coverage instead).

Emits, per repo: one ``repository`` node (with stack/provenance/access-model
attributes), ``route`` nodes + ``route-linkage`` edges, ``data-store`` nodes +
``data`` edges (with the access-type ladder preserved), ``external-boundary``
nodes + ``boundary`` edges, and ``deployable-unit`` nodes. Containment
(repo -> file/route/data-store/unit) is added through the shared builder.
"""

from __future__ import annotations

from ..targetspec import TargetSpec
from . import ids
from .builder import ModelBuilder

REPO = "discovery"
LIVENESS = "discovery/liveness"
TABLES = "discovery/tables"
INTEG = "discovery/integrations"
CANDIDATES = "discovery/candidates"
DEPLOY = "discovery/deploy"
ACCESS = "discovery/access"


def load(builder: ModelBuilder, spec: TargetSpec, report: dict) -> dict:
    """Populate ``builder`` from ``targets.json`` (spec) + ``discovery-report``.

    Returns per-partition presence flags used by coverage (e.g. whether the
    detailed route artifact existed)."""
    heads = {r.repo_id: (r.git.head or "") for r in spec.repos}
    blocks = {b["repo_id"]: b for b in report.get("repos", [])}
    for target in spec.repos:
        block = blocks.get(target.repo_id, {})
        _repository(builder, target, block)
        _tables(builder, target.repo_id, heads, block.get("table_evidence", {}))
        _integrations(builder, target.repo_id, heads,
                      block.get("integration_evidence", {}))
        _deploy(builder, target.repo_id, heads, block.get("deployable_units", {}))
    _candidates(builder, spec)
    routes_present = _routes(builder, heads, report.get("route_liveness"))
    return {"routes_present": routes_present,
            "route_summary_capped": _summary_route_cap(blocks)}


# --------------------------------------------------------------------------- #
# repositories
# --------------------------------------------------------------------------- #

def _repository(builder: ModelBuilder, target, block: dict) -> str:
    prov = block.get("provenance", {})
    stacks = block.get("stacks", {})
    access = block.get("access_model", {})
    head = target.git.head or ""
    attrs = {
        "name": target.repo_id.rsplit("-", 1)[0],
        "stacks": list(target.stacks),
        "frameworks": stacks.get("frameworks", []),
        "analysis_roots": list(target.analysis_roots),
        "is_git": prov.get("is_git", target.git.is_git),
        "head": head,
        "branch": prov.get("branch", target.git.branch),
        "remote_redacted": prov.get("remote_redacted", ""),
        "commit_count": prov.get("commit_count", target.git.commit_count),
        "oldest_commit_date": prov.get("oldest_commit_date",
                                       target.git.oldest_commit_date),
        "package_manager": target.pm.name,
        "access_model": _access_summary(access),
    }
    repo_id = builder.add_node(
        "repository", [target.repo_id], label=target.repo_id, status="observed",
        repo_id=target.repo_id, producer=REPO,
        evidence=[f"{target.repo_id}@{head or 'nogit'}"] if head else [],
        attrs=attrs)
    # Policy artifacts (casbin model/policy) are located as files — surface them.
    for artifact in access.get("policy_artifacts", []):
        builder.note_file(target.repo_id, artifact.get("path", ""), producer=ACCESS)
    return repo_id


def _access_summary(access: dict) -> dict:
    if not access.get("available"):
        return {"available": False}
    return {
        "available": True,
        "role_catalog_names": access.get("role_catalog_names", []),
        "authz_checks": access.get("authz_checks", {}).get("count", 0),
        "middleware": access.get("middleware", {}).get("count", 0),
        "route_guards": access.get("route_guards", {}).get("count", 0),
        "contextual_identity": access.get("contextual_identity", {}).get("count", 0),
        "policy_artifacts": len(access.get("policy_artifacts", [])),
    }


# --------------------------------------------------------------------------- #
# routes (detailed route_liveness — the canonical source)
# --------------------------------------------------------------------------- #

def _routes(builder: ModelBuilder, heads: dict, liveness: dict | None) -> bool:
    if not liveness:
        return False
    frontend = liveness.get("frontend", "")
    for row in liveness.get("rows", []):
        repo_id = row.get("repo_id", "")
        method, path = row.get("method", ""), row.get("path", "")
        route_ev = row.get("route_evidence", "")
        status = row.get("status", "")
        head = heads.get(repo_id, "")
        citation = ids.make_citation(repo_id, head, route_ev)
        route_id = builder.add_node(
            "route", [repo_id, method, path, route_ev],
            label=f"{method} {path}", status="observed", repo_id=repo_id,
            producer=LIVENESS, evidence=[citation],
            attrs={"method": method, "path": path, "liveness": status})
        builder.note_file(repo_id, ids.split_position(route_ev)[0],
                          producer=LIVENESS, evidence=citation)
        _route_callers(builder, row, frontend, repo_id, heads, route_id)
    return True


def _route_callers(builder, row, frontend, repo_id, heads, route_id) -> None:
    status = row.get("status", "")
    # ui-called callers live in the frontend repo; internal-called ones in the
    # route's own repo. no-direct-path-match / match-ambiguous carry no caller
    # (preserved as unresolved counts in coverage, not as fabricated edges).
    caller_repo = frontend if status == "ui-called" else repo_id
    for caller_ev in row.get("caller_evidence", []):
        head = heads.get(caller_repo, "")
        citation = ids.make_citation(caller_repo, head, caller_ev)
        caller_file = builder.note_file(caller_repo, ids.split_position(caller_ev)[0],
                                        producer=LIVENESS, evidence=citation)
        builder.add_edge("route-linkage", caller_file, route_id, status="observed",
                         producer=LIVENESS, evidence=[citation],
                         attrs={"link": status})


# --------------------------------------------------------------------------- #
# tables / data-stores (uncapped table_evidence — the canonical source)
# --------------------------------------------------------------------------- #

def _tables(builder: ModelBuilder, repo_id: str, heads: dict, te: dict) -> None:
    if not te.get("available"):
        return
    head = heads.get(repo_id, "")
    for name, buckets in te.get("tables", {}).items():
        first = _first_citation(repo_id, head, buckets)
        table_id = builder.add_node(
            "data-store", [repo_id, name], label=name, status="observed",
            repo_id=repo_id, producer=TABLES,
            evidence=[first] if first else [],
            attrs={"table": name, "access_types": sorted(buckets.keys())})
        for access_type, sites in sorted(buckets.items()):
            for site in sites:
                citation = ids.make_citation(repo_id, head, site)
                file_id = builder.note_file(repo_id, ids.split_position(site)[0],
                                            producer=TABLES, evidence=citation)
                builder.add_edge("data", file_id, table_id, status="observed",
                                 producer=TABLES, evidence=[citation],
                                 attrs={"access": access_type},
                                 discriminator=access_type)


def _first_citation(repo_id: str, head: str, buckets: dict) -> str:
    """A single representative citation for the data-store node, chosen
    order-INDEPENDENTLY (min over sorted access + sorted sites) so upstream
    ast-grep match ordering cannot change the emitted model."""
    for _access, sites in sorted(buckets.items()):
        if sites:
            return ids.make_citation(repo_id, head, min(sites))
    return ""


# --------------------------------------------------------------------------- #
# external boundaries
# --------------------------------------------------------------------------- #

def _integrations(builder: ModelBuilder, repo_id: str, heads: dict, ie: dict) -> None:
    if not ie.get("available"):
        return
    head = heads.get(repo_id, "")
    for frag in ie.get("host_fragments", []):
        _boundary(builder, repo_id, head, "host", frag.get("value", ""),
                  frag.get("evidence", []), INTEG,
                  {"kind": "host-fragment", "value": frag.get("value", "")})
    for pkg in ie.get("integration_packages", []):
        _boundary(builder, repo_id, head, "package", pkg.get("package", ""),
                  pkg.get("evidence", []), INTEG,
                  {"kind": "integration-package", "package": pkg.get("package", ""),
                   "http_calls": pkg.get("http_calls", 0)})


def _boundary(builder, repo_id, head, node_key, value, evidence, producer, attrs):
    if not value:
        return
    ext_id = builder.add_node(
        "external-boundary", [node_key, value], label=value, status="observed",
        producer=producer, attrs=attrs)
    for ev in evidence:
        citation = ids.make_citation(repo_id, head, ev)
        file_id = builder.note_file(repo_id, ids.split_position(ev)[0],
                                    producer=producer, evidence=citation)
        builder.add_edge("boundary", file_id, ext_id, status="observed",
                         producer=producer, evidence=[citation],
                         attrs={"kind": attrs["kind"]})


def _candidates(builder: ModelBuilder, spec: TargetSpec) -> None:
    """Integration candidates from the TargetSpec (dependency/client_init/oauth/…).

    A candidate is a repo-scoped boundary signal; several signal kinds for the
    same target value merge into one external node so it is not triple-counted."""
    for cand in spec.integration_candidates:
        value = cand.value
        ext_id = builder.add_node(
            "external-boundary", ["candidate", value], label=value,
            status="observed", producer=CANDIDATES,
            attrs={"kind": "integration-candidate"})
        repo_node = ids.stable_id("repository", cand.repo_id)
        builder.add_edge("boundary", repo_node, ext_id, status="observed",
                         producer=CANDIDATES, evidence=list(cand.evidence),
                         attrs={"kind": "integration-candidate",
                                "signal_kind": cand.signal_kind},
                         discriminator=cand.signal_kind)


# --------------------------------------------------------------------------- #
# deployable units
# --------------------------------------------------------------------------- #

def _deploy(builder: ModelBuilder, repo_id: str, heads: dict, du: dict) -> None:
    head = heads.get(repo_id, "")
    repo_node = ids.stable_id("repository", repo_id)
    for unit in du.get("units", []):
        kind, name = unit.get("kind", ""), unit.get("name", "")
        evidence = unit.get("evidence", "")
        citation = ids.make_citation(repo_id, head, evidence) if evidence else ""
        attrs = {"unit_kind": kind, "name": name}
        for extra in ("built_here", "image"):
            if extra in unit:
                attrs[extra] = unit[extra]
        unit_id = builder.add_node(
            "deployable-unit", [repo_id, kind, name], label=f"{kind}:{name}",
            status="observed", repo_id=repo_id, producer=DEPLOY,
            evidence=[citation] if citation else [], attrs=attrs)
        builder.add_edge("containment", repo_node, unit_id, status="observed",
                         producer=DEPLOY)
        if evidence:
            builder.note_file(repo_id, ids.split_position(evidence)[0],
                              producer=DEPLOY, evidence=citation)


def _summary_route_cap(blocks: dict) -> bool:
    """Did the capped ``module_signals.routes`` summary hit its cap in any repo?
    Recorded in coverage as the reason the summary is unfit as a route source."""
    for block in blocks.values():
        for note in block.get("module_signals", {}).get("notes", []):
            if "route cap hit" in note:
                return True
    return False
