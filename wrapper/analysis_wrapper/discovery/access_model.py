"""Access-model signal view (57B-27 item 12) — LOCATE and COUNT, never interpret.

Emits WHERE authorization-shaped code lives so a downstream lens (and cross-repo
comparison) has evidence: role catalogs (types/enums), inline authorization
checks, middleware attachment, frontend route guards, policy-engine artifacts
located as data (casbin model/policy files), and structurally-obvious
contextual-identity comparisons (owner/leader/approver equality). It records
locations + counts only — it makes no claim about what any check enforces, and
anything not structurally obvious is left out (unresolved), never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import astgrep

_RULE = "access-control.yml"
_SKIP_DIRS = {"node_modules", "vendor", ".git", "dist", "build", "coverage"}
_SAMPLE_CAP = 8
_MAX_BYTES = 262_144
_POLICY_MAX_FILES = 4000

_ROLE_NAME = re.compile(r"(?i)\b(?:type|enum)\s+(\w+)")
# casbin model files declare these sections; policy files are p/g CSV rows.
_CASBIN_MODEL = re.compile(r"\[(request_definition|policy_definition|matchers)\]")
_CASBIN_POLICY = re.compile(r"^\s*[pg]\s*,", re.M)


@dataclass
class AccessModel:
    available: bool
    role_catalog: list[dict] = field(default_factory=list)
    authz_checks: dict = field(default_factory=dict)
    middleware: dict = field(default_factory=dict)
    route_guards: dict = field(default_factory=dict)
    contextual_identity: dict = field(default_factory=dict)
    policy_artifacts: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # ast-grep version/path/drift for this scan()-derived signal (57B-37).
    astgrep: dict = field(default_factory=astgrep.unavailable_provenance)

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "role_catalog": self.role_catalog,
            "role_catalog_names": sorted({r["name"] for r in self.role_catalog}),
            "authz_checks": self.authz_checks,
            "middleware": self.middleware,
            "route_guards": self.route_guards,
            "contextual_identity": self.contextual_identity,
            "policy_artifacts": self.policy_artifacts,
            "notes": self.notes,
            **self.astgrep,
        }


def _bucket(matches, rule_ids: set[str]) -> dict:
    hits = [m for m in matches if m.rule_id in rule_ids]
    return {"count": len(hits),
            "sample": [f"{m.file}:{m.line}" for m in hits[:_SAMPLE_CAP]]}


def _match_key(match) -> tuple:
    """Stable ordering before any capped sample is taken.

    ast-grep does not promise result order.  Sorting on repository-relative
    source coordinates and structural identity keeps discovery-report.json
    byte-stable without interpreting the matched business text.
    """
    return (match.file, match.line, match.rule_id, match.text,
            tuple(sorted(match.vars.items())))


def _find_policy_files(root: Path, tier2: set[str]) -> tuple[list[dict], bool]:
    found: list[dict] = []
    stack = [root]
    count = 0
    capped = False
    while stack and not capped:
        base = stack.pop()
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                rel0 = entry.relative_to(root).parts[0] if entry != root else ""
                if entry.name not in _SKIP_DIRS and rel0 not in tier2 \
                        and not entry.name.startswith("."):
                    stack.append(entry)
                continue
            if entry.suffix not in {".conf", ".csv", ".ini", ".model"}:
                continue
            count += 1
            if count > _POLICY_MAX_FILES:
                capped = True   # stop the WHOLE walk, not just this directory
                break
            try:
                if entry.stat().st_size > _MAX_BYTES:
                    continue
                text = entry.read_text("utf-8", errors="replace")
            except OSError:
                continue
            rel = entry.relative_to(root).as_posix()
            if _CASBIN_MODEL.search(text):
                found.append({"path": rel, "kind": "casbin-model"})
            elif _CASBIN_POLICY.search(text):
                found.append({"path": rel, "kind": "casbin-policy"})
    return sorted(found, key=lambda x: x["path"]), capped


def generate(repo_path: str | Path, repo_id: str, *,
             tier2_exclusions: list[str] | None = None) -> AccessModel:
    tier2 = set(tier2_exclusions or [])
    provenance = astgrep.probe().provenance()
    if not astgrep.available():
        return AccessModel(
            available=False,
            notes=["ast-grep unavailable: access-model view SKIPPED (fail-closed)"],
            astgrep=provenance)
    root = Path(repo_path).expanduser().resolve()
    matches = sorted(
        (m for m in astgrep.scan(repo_path, [astgrep.RULES_DIR / _RULE])
         if not (PurePosixPath(m.file).parts
                 and PurePosixPath(m.file).parts[0] in tier2)),
        key=_match_key,
    )

    role_catalog: list[dict] = []
    seen_roles: set[tuple[str, str]] = set()
    for match in matches:
        if match.rule_id in ("role-type-go", "role-enum-ts"):
            name = _ROLE_NAME.search(match.text)
            if name:
                key = (name.group(1), match.file)
                if key not in seen_roles and len(role_catalog) < 60:
                    seen_roles.add(key)
                    kind = "go-type" if match.rule_id == "role-type-go" else "ts-enum"
                    role_catalog.append({"name": name.group(1), "kind": kind,
                                         "evidence": f"{match.file}:{match.line}"})

    policy_artifacts, policy_capped = _find_policy_files(root, tier2)
    notes = [
        "LOCATE + COUNT only — never an interpretation of what a check enforces.",
        "authz/identity detection uses generic name/shape heuristics (no project "
        "role catalog); non-obvious cases are omitted, not guessed.",
        "policy artifacts (casbin model/policy) are located as DATA, not parsed "
        "for semantics.",
    ]
    if policy_capped:
        notes.append(f"COVERAGE CAP: policy-file scan stopped after "
                     f"{_POLICY_MAX_FILES} config files — beyond-cap files NOT "
                     "scanned (incomplete).")
    return AccessModel(
        available=True,
        role_catalog=role_catalog,
        authz_checks=_bucket(matches, {"authz-check-js", "authz-check-ts",
                                       "authz-check-tsx", "authz-check-go"}),
        middleware=_bucket(matches, {"middleware-attach-js", "middleware-attach-ts",
                                     "middleware-attach-go"}),
        route_guards=_bucket(matches, {"route-guard-tsx"}),
        contextual_identity=_bucket(matches, {"identity-check-go", "identity-check-ts"}),
        policy_artifacts=policy_artifacts,
        notes=notes, astgrep=provenance)
