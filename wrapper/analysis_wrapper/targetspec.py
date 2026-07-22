"""TargetSpec — the discovery→execution contract (plan §17.6).

DEFINED here (57B-10) with fixtures; PRODUCED by discovery (57B-11), which
implements the package-manager / stack / analysis-root / generated-file /
external-system-candidate producers. The executor consumes a TargetSpec and
never re-derives targets.

Everything is plain dataclasses + JSON — no schema system, no validation
framework (plan §2.6): `from_dict` raises ValueError with a precise message on
malformed input, and that is the entire "validation" story.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path


_SAFE_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SCHEMA_VERSION = "2.0.0"
_FACET_STATES = {"resolved", "conflicting", "unknown"}
_CONFIDENCE = {"high", "medium", "low"}
_REPO_FIELDS = {
    "repo_id", "path", "facets", "analysis_roots", "tier2_exclusions", "pm", "git",
}
_PM_FIELDS = {"name", "lockfile", "evidence"}
_GIT_FIELDS = {
    "head", "branch", "dirty_detail", "shallow", "commit_count",
    "oldest_commit_date",
}


def _relative_path(value: object, label: str, *, basename_only: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must stay relative to its repository")
    if basename_only and len(path.parts) != 1:
        raise ValueError(f"{label} must be a repository-root basename")
    return value


def _string_list(value: object, label: str, *, paths: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"{label} must be a string list")
    if paths:
        for i, item in enumerate(value):
            _relative_path(item, f"{label}[{i}]")
    return list(value)


def stable_repo_id(repo_path: str) -> str:
    """basename + short hash of the canonical absolute path.

    Two repos both named `api` in different directories must not collide
    (Phase 0 review requirement)."""
    canon = str(Path(repo_path).resolve())
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:8]
    return f"{Path(canon).name}-{digest}"


def path_contains(parent: str | Path, child: str | Path) -> bool:
    """Return whether ``child`` is the same path as, or is inside, ``parent``.

    Resolve first so containment is segment-aware (``app`` does not contain
    ``application``) and symlink aliases cannot create duplicate targets.
    """
    parent_path = Path(parent).expanduser().resolve()
    child_path = Path(child).expanduser().resolve()
    return child_path == parent_path or child_path.is_relative_to(parent_path)


def overlapping_repo_pairs(repos: list["RepoTarget"]) -> list[tuple[str, str]]:
    """Canonical repo-id pairs whose source trees overlap."""
    pairs: list[tuple[str, str]] = []
    ordered = sorted(repos, key=lambda repo: repo.repo_id)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if path_contains(left.path, right.path) or path_contains(right.path, left.path):
                pairs.append((left.repo_id, right.repo_id))
    return pairs


@dataclass
class PackageManager:
    name: str = "npm"            # npm | yarn | pnpm | go | none
    lockfile: str = ""           # authoritative lockfile basename ("" = none)
    evidence: str = ""           # how it was determined, conflicts disclosed


@dataclass
class GitProvenance:
    head: str = ""               # 40-char sha, or "" for non-git targets
    branch: str = ""
    dirty_detail: str = "no"     # "no" | "yes (N files: XY path; ...)"
    shallow: bool = False
    commit_count: int = 0
    oldest_commit_date: str = "" # ISO date of first commit ("" unknown)

    @property
    def is_git(self) -> bool:
        return bool(self.head)


@dataclass
class TechnologyFacet:
    """One evidence-backed technology observation for a repository."""

    profile_id: str
    kind: str
    scope_roots: list[str] = field(default_factory=list)  # ["."] = repo root
    evidence: list[str] = field(default_factory=list)
    confidence: str = "high"
    state: str = "resolved"


def _profile(profile_id: str):
    # Lazy import avoids making the profile contracts depend on TargetSpec.
    from .profiles.bundled import bundled_registry
    try:
        return bundled_registry().profile(profile_id)
    except KeyError as exc:
        raise ValueError(f"unknown bundled profile_id {profile_id!r}") from exc


@dataclass
class RepoTarget:
    repo_id: str
    path: str
    facets: list[TechnologyFacet] = field(default_factory=list)
    analysis_roots: list[str] = field(default_factory=list) # repo-relative; [] = root
    tier2_exclusions: list[str] = field(default_factory=list) # derived, disclosed
    pm: PackageManager = field(default_factory=PackageManager)
    git: GitProvenance = field(default_factory=GitProvenance)

    def root_paths(self) -> list[Path]:
        base = Path(self.path).expanduser().resolve()
        roots = [base / r for r in self.analysis_roots] if self.analysis_roots else [base]
        resolved = [path.resolve() for path in roots]
        if any(path != base and not path.is_relative_to(base) for path in resolved):
            raise ValueError(f"analysis root escapes repository {self.repo_id}")
        return resolved

    def facets_of_kind(self, kind: str) -> tuple[TechnologyFacet, ...]:
        return tuple(facet for facet in self.facets if facet.kind == kind)

    def has_profile(self, profile_id: str) -> bool:
        return any(facet.profile_id == profile_id for facet in self.facets)

    def profiles_for_capability(self, capability_id: str) -> tuple[str, ...]:
        return tuple(sorted(
            facet.profile_id for facet in self.facets
            if capability_id in _profile(facet.profile_id).capability_ids
        ))

    def technology_names(self, kind: str) -> list[str]:
        return sorted(
            _profile(facet.profile_id).display_name
            for facet in self.facets_of_kind(kind)
            if facet.state != "unknown"
        )

    @property
    def stacks(self) -> list[str]:
        """Temporary consumer view; the serialized contract owns only facets."""
        return self.technology_names("language")


@dataclass
class IntegrationCandidate:
    """Mechanical discovery fact; classification belongs to a lens, not discovery.

    57B-11 produces these from code structure (imports, client construction,
    endpoints, committed config/env names, OAuth providers, and CI resources).
    The values remain unlabeled here: a dependency name is evidence, not proof
    that an external service is active.
    """

    candidate_id: str
    repo_id: str
    signal_kind: str
    value: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class TargetSpec:
    repos: list[RepoTarget]
    integration_candidates: list[IntegrationCandidate] = field(default_factory=list)
    produced_by: str = ""        # discovery identity, e.g. "analysis-discovery/0.1.0"
    produced_at: str = ""        # ISO timestamp (recorded, never generated here)
    schema_version: str = SCHEMA_VERSION

    def repo(self, repo_id: str) -> RepoTarget:
        for r in self.repos:
            if r.repo_id == repo_id:
                return r
        raise KeyError(f"no repo with id {repo_id!r} in TargetSpec")

    # ---- JSON round-trip -----------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: dict) -> "TargetSpec":
        if not isinstance(data, dict) or "repos" not in data:
            raise ValueError("TargetSpec JSON must be an object with a 'repos' list")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"TargetSpec schema_version must be {SCHEMA_VERSION}; regenerate the run"
            )
        if not isinstance(data["repos"], list):
            raise ValueError("TargetSpec 'repos' must be a list")
        repos = []
        repo_ids: set[str] = set()
        for i, r in enumerate(data["repos"]):
            if not isinstance(r, dict):
                raise ValueError(f"repos[{i}] must be an object")
            for key in ("repo_id", "path"):
                if not r.get(key):
                    raise ValueError(f"repos[{i}]: missing required field {key!r}")
            if not isinstance(r["repo_id"], str) or not _SAFE_REPO_ID.fullmatch(r["repo_id"]):
                raise ValueError(
                    f"repos[{i}].repo_id must use only letters, digits, dot, underscore, "
                    "and hyphen"
                )
            if not isinstance(r["path"], str) or not Path(r["path"]).is_absolute():
                raise ValueError(f"repos[{i}].path must be an absolute path")
            if r["repo_id"] in repo_ids:
                raise ValueError(f"repos[{i}]: duplicate repo_id {r['repo_id']!r}")
            repo_ids.add(r["repo_id"])
            unknown_repo_fields = set(r) - _REPO_FIELDS
            if unknown_repo_fields:
                raise ValueError(
                    f"repos[{i}] contains unsupported fields: {sorted(unknown_repo_fields)}"
                )
            raw_facets = r.get("facets", [])
            if not isinstance(raw_facets, list):
                raise ValueError(f"repos[{i}].facets must be a list")
            facets: list[TechnologyFacet] = []
            facet_ids: set[str] = set()
            for j, raw in enumerate(raw_facets):
                label = f"repos[{i}].facets[{j}]"
                if not isinstance(raw, dict):
                    raise ValueError(f"{label} must be an object")
                expected = {
                    "profile_id", "kind", "scope_roots", "evidence",
                    "confidence", "state",
                }
                unknown = set(raw) - expected
                if unknown:
                    raise ValueError(f"{label} contains unsupported fields: {sorted(unknown)}")
                for key in ("profile_id", "kind"):
                    if not isinstance(raw.get(key), str) or not raw[key]:
                        raise ValueError(f"{label}.{key} must be a non-empty string")
                if raw["profile_id"] in facet_ids:
                    raise ValueError(f"{label}: duplicate profile_id {raw['profile_id']!r}")
                profile = _profile(raw["profile_id"])
                if raw["kind"] != profile.kind:
                    raise ValueError(
                        f"{label}.kind does not match bundled profile {raw['profile_id']!r}"
                    )
                scope_roots = _string_list(
                    raw.get("scope_roots", []), f"{label}.scope_roots", paths=True
                )
                evidence = _string_list(raw.get("evidence", []), f"{label}.evidence")
                if not evidence:
                    raise ValueError(f"{label}.evidence must not be empty")
                confidence = raw.get("confidence", "high")
                state = raw.get("state", "resolved")
                if confidence not in _CONFIDENCE:
                    raise ValueError(f"{label}.confidence is unsupported: {confidence!r}")
                if state not in _FACET_STATES:
                    raise ValueError(f"{label}.state is unsupported: {state!r}")
                facet_ids.add(raw["profile_id"])
                facets.append(TechnologyFacet(
                    profile_id=raw["profile_id"], kind=raw["kind"],
                    scope_roots=scope_roots, evidence=evidence,
                    confidence=confidence, state=state,
                ))
            roots = _string_list(
                r.get("analysis_roots", []), f"repos[{i}].analysis_roots", paths=True
            )
            exclusions = _string_list(
                r.get("tier2_exclusions", []),
                f"repos[{i}].tier2_exclusions",
                paths=True,
            )
            pm_data = r.get("pm", {})
            git_data = r.get("git", {})
            if not isinstance(pm_data, dict) or not isinstance(git_data, dict):
                raise ValueError(f"repos[{i}].pm and .git must be objects")
            unknown_pm = set(pm_data) - _PM_FIELDS
            unknown_git = set(git_data) - _GIT_FIELDS
            if unknown_pm or unknown_git:
                raise ValueError(
                    f"repos[{i}] contains unsupported provenance fields: "
                    f"pm={sorted(unknown_pm)}, git={sorted(unknown_git)}"
                )
            pm_name = pm_data.get("name", "npm")
            if pm_name not in {"npm", "yarn", "pnpm", "go", "none"}:
                raise ValueError(f"repos[{i}].pm.name is unsupported: {pm_name!r}")
            lockfile = pm_data.get("lockfile", "")
            if lockfile:
                _relative_path(lockfile, f"repos[{i}].pm.lockfile", basename_only=True)
            for key in ("evidence",):
                if key in pm_data and not isinstance(pm_data[key], str):
                    raise ValueError(f"repos[{i}].pm.{key} must be a string")
            head = git_data.get("head", "")
            if not isinstance(head, str) or (head and not re.fullmatch(r"[0-9a-fA-F]{40}", head)):
                raise ValueError(f"repos[{i}].git.head must be empty or a 40-character SHA")
            for key in ("branch", "dirty_detail", "oldest_commit_date"):
                if key in git_data and not isinstance(git_data[key], str):
                    raise ValueError(f"repos[{i}].git.{key} must be a string")
            if "shallow" in git_data and not isinstance(git_data["shallow"], bool):
                raise ValueError(f"repos[{i}].git.shallow must be boolean")
            if "commit_count" in git_data and (
                not isinstance(git_data["commit_count"], int)
                or isinstance(git_data["commit_count"], bool)
                or git_data["commit_count"] < 0
            ):
                raise ValueError(f"repos[{i}].git.commit_count must be a non-negative integer")
            repos.append(
                RepoTarget(
                    repo_id=r["repo_id"],
                    path=r["path"],
                    facets=facets,
                    analysis_roots=roots,
                    tier2_exclusions=exclusions,
                    pm=PackageManager(**pm_data),
                    git=GitProvenance(**git_data),
                )
            )
        raw_candidates = data.get("integration_candidates", [])
        if not isinstance(raw_candidates, list):
            raise ValueError("TargetSpec 'integration_candidates' must be a list")
        candidates: list[IntegrationCandidate] = []
        candidate_ids: set[str] = set()
        # Atomic kinds may be combined with "+" (a candidate observed several
        # ways: "dependency+import+client_init"); "dependency-only" is the
        # label for candidates whose sole signal is a dependency declaration
        # (57B-11 / plan §17.7).
        atomic_kinds = {
            "dependency", "import", "client_init", "outbound_endpoint",
            "config", "env", "oauth_provider", "ci_resource",
        }
        for i, c in enumerate(raw_candidates):
            if not isinstance(c, dict):
                raise ValueError(f"integration_candidates[{i}] must be an object")
            for key in ("candidate_id", "repo_id", "signal_kind", "value"):
                if not c.get(key):
                    raise ValueError(
                        f"integration_candidates[{i}]: missing required field {key!r}"
                    )
            if c["candidate_id"] in candidate_ids:
                raise ValueError(
                    f"integration_candidates[{i}]: duplicate candidate_id "
                    f"{c['candidate_id']!r}"
                )
            if c["repo_id"] not in repo_ids:
                raise ValueError(
                    f"integration_candidates[{i}]: unknown repo_id {c['repo_id']!r}"
                )
            kind = c["signal_kind"]
            if kind != "dependency-only" and (
                not kind or not set(kind.split("+")) <= atomic_kinds
            ):
                raise ValueError(
                    f"integration_candidates[{i}].signal_kind unsupported: {kind!r}"
                )
            evidence = c.get("evidence", [])
            if not isinstance(evidence, list) or not all(isinstance(x, str) for x in evidence):
                raise ValueError(f"integration_candidates[{i}].evidence must be a string list")
            candidate_ids.add(c["candidate_id"])
            candidates.append(IntegrationCandidate(**c))
        return cls(
            repos=repos,
            integration_candidates=candidates,
            produced_by=data.get("produced_by", ""),
            produced_at=data.get("produced_at", ""),
            schema_version=SCHEMA_VERSION,
        )

    @classmethod
    def from_json(cls, text: str) -> "TargetSpec":
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: str | Path) -> "TargetSpec":
        return cls.from_json(Path(path).read_text("utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), "utf-8")
