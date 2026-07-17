"""Assembled-URL / integration-package evidence (57B-22 item 5, ast-grep).

A NEW discovery producer that catches integrations the scheme-anchored URL regex
misses: bare host-fragment string constants (e.g. `host = "openapi.vendor.cn"`
where the scheme lives in a sibling constant) and distinctively-named packages
that make outbound HTTP calls. Structural evidence only — every finding is a
CANDIDATE with citations, never a claim that a service is active, and cross-file
constant propagation is deliberately NOT attempted (such cases stay unresolved).

D1 boundary: import edges come from dependency-cruiser / go list, never here.
Table/column naming evidence comes from the DB extractor (item 6).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import astgrep
from .candidates import _NOISE_HOSTS

_HOST_RULE = "integration-host.yml"
_HTTP_RULE = "http-call-site.yml"

# A host fragment is a lowercase domain; the ast-grep rule is permissive so the
# hostname shape is enforced HERE, dropping file-name and identifier look-alikes.
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*\.)+[a-z]{2,}$")
_FILE_EXT = {
    "json", "yaml", "yml", "xlsx", "xls", "csv", "go", "js", "ts", "tsx", "jsx",
    "mjs", "cjs", "html", "css", "scss", "less", "md", "txt", "png", "svg",
    "jpg", "jpeg", "gif", "pdf", "xml", "sql", "sh", "lock", "map", "toml",
    "ini", "conf", "proto", "mod", "sum", "env", "tmpl", "gohtml", "webp", "ico",
}
_NON_PUBLIC_SUFFIX = (".invalid", ".local", ".localhost", ".test", ".example")

# Directory names too generic to be an integration's own package.
_GENERIC_DIRS = {
    "handlers", "handler", "tasks", "task", "internal", "api", "http", "https",
    "client", "clients", "service", "services", "server", "servers", "pkg",
    "cmd", "model", "models", "common", "config", "configs", "db", "database",
    "middleware", "middlewares", "router", "routers", "routes", "route", "util",
    "utils", "helper", "helpers", "lib", "libs", "src", "app", "apps", "core",
    "controller", "controllers", "repository", "repositories", "store", "stores",
    "gateway", "integration", "integrations", "external", "adapter", "adapters",
    "provider", "providers", "transport", "rpc", "grpc", "proto", "test", "tests",
    "mock", "mocks", "dto", "types", "type", "constant", "constants", "errors",
    "error", "logger", "logging", "domain", "usecase", "usecases", "entity",
    "entities", "schema", "schemas", "worker", "workers", "job", "jobs", "web",
}


@dataclass
class IntegrationEvidence:
    available: bool
    host_fragments: list[dict] = field(default_factory=list)
    integration_packages: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # ast-grep version/path/drift for this scan()-derived signal (57B-37).
    astgrep: dict = field(default_factory=astgrep.unavailable_provenance)

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "host_fragments": self.host_fragments,
            "integration_packages": self.integration_packages,
            "notes": self.notes,
            **self.astgrep,
        }


def _is_host_fragment(value: str) -> bool:
    value = value.strip()
    if not _HOST_RE.match(value):
        return False
    if value in _NOISE_HOSTS or value.endswith(_NON_PUBLIC_SUFFIX):
        return False
    return value.rsplit(".", 1)[1] not in _FILE_EXT


def _excluded(rel: str, tier2: set[str]) -> bool:
    parts = PurePosixPath(rel).parts
    return bool(parts) and parts[0] in tier2


def generate(repo_path: str | Path, repo_id: str, *,
             tier2_exclusions: list[str] | None = None) -> IntegrationEvidence:
    tier2 = set(tier2_exclusions or [])
    provenance = astgrep.probe().provenance()
    if not astgrep.available():
        return IntegrationEvidence(
            available=False,
            notes=["ast-grep unavailable: assembled-URL / integration-package "
                   "evidence SKIPPED (fail-closed — install ast-grep to enable)"],
            astgrep=provenance)

    # Sort matches into a stable order BEFORE the 5-site evidence cap applies, so
    # a truncated sample is deterministic (ast-grep scan order is not stable).
    _key = lambda m: (m.file, m.line, m.rule_id, m.text)
    rules_dir = astgrep.RULES_DIR
    host_matches = sorted(astgrep.scan(repo_path, [rules_dir / _HOST_RULE]), key=_key)
    call_matches = sorted(astgrep.scan(repo_path, [rules_dir / _HTTP_RULE]), key=_key)

    # 1) host-fragment constants
    evidence_truncated = False               # a dropped >5th site is disclosed below
    host_evidence: dict[str, list[str]] = defaultdict(list)
    for match in host_matches:
        if _excluded(match.file, tier2):
            continue
        value = match.text.strip().strip("\"'`")
        if not _is_host_fragment(value):
            continue
        where = f"{match.file}:{match.line}"
        if where in host_evidence[value]:
            continue
        if len(host_evidence[value]) < 5:
            host_evidence[value].append(where)
        else:
            evidence_truncated = True
    host_fragments = [{"value": value, "evidence": host_evidence[value]}
                      for value in sorted(host_evidence)]

    # 2) integration packages: distinctively-named dirs with HTTP call sites,
    # merged across dirs that share a leaf name (handlers/beisen + tasks/beisen).
    by_leaf: dict[str, dict] = {}
    for match in call_matches:
        if _excluded(match.file, tier2):
            continue
        parent = PurePosixPath(match.file).parent
        leaf = parent.name
        if not leaf or leaf.lower() in _GENERIC_DIRS:
            continue
        entry = by_leaf.setdefault(leaf, {"package": leaf, "dirs": set(),
                                          "http_calls": 0, "evidence": []})
        entry["dirs"].add(str(parent))
        entry["http_calls"] += 1
        if len(entry["evidence"]) < 5:
            entry["evidence"].append(f"{match.file}:{match.line}")
        else:
            evidence_truncated = True
    integration_packages = [
        {"package": e["package"], "dirs": sorted(e["dirs"]),
         "http_calls": e["http_calls"], "evidence": e["evidence"]}
        for e in sorted(by_leaf.values(), key=lambda x: (-x["http_calls"], x["package"]))
    ]

    notes = [
        "host fragments: bare-hostname string literals (scheme-less assembled "
        "URLs); file-name and dotted-identifier look-alikes filtered; cross-file "
        "constant propagation NOT attempted (unresolved).",
        "integration packages: distinctively-named directories with outbound HTTP "
        "call sites (generic infra dir names excluded).",
        "candidates only — evidence of code that CAN talk to a service, never "
        "proof one is active; table/column naming evidence is in the DB extractor.",
    ]
    if evidence_truncated:
        notes.append(
            "COVERAGE CAP: per-host / per-package evidence capped at 5 sites — "
            "further call sites for at least one integration were NOT recorded "
            "(the distinct host/package set is complete; per-site evidence is sampled).")
    return IntegrationEvidence(True, host_fragments, integration_packages, notes,
                               astgrep=provenance)
