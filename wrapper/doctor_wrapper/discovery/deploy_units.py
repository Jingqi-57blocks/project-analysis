"""Deployable-unit signal view (57B-27 / item 13).

Locates STATIC deploy artifacts (Dockerfiles, docker-compose / compose files,
Go `package main` entrypoints, CI deploy files) and emits per-repo deployable-unit
candidates. This is a LOCATE-and-PARSE-as-data producer, never an analyzer: it
never claims deploy-config discovery is complete, never asserts a unit is actually
deployed. A repo with artifacts is `inferred`; a repo with none is `unknown`.

`source directory != deployable unit`: a tree with no build/run marker is
library/support, not an independently deployable unit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP = {"node_modules", "vendor", ".git", "coverage"}   # build/dist kept: markers live there
_COMPOSE_RE = re.compile(r"(?i)^(docker-)?compose[.\w-]*\.ya?ml$")
_GO_MAIN_PKG = re.compile(r"^\s*package\s+main\b", re.M)
_GO_MAIN_FUNC = re.compile(r"\bfunc\s+main\s*\(")
_CI_FILES = {"bitbucket-pipelines.yml", ".gitlab-ci.yml", "Jenkinsfile",
             "azure-pipelines.yml"}
_CI_DEPLOY = re.compile(r"(?i)\b(deploy|deployment|rollout|rancher|kubectl|helm|"
                        r"docker\s+push|release)\b")
_MAX_FILES = 6000
_MAX_BYTES = 262_144


@dataclass
class DeployUnits:
    status: str                                    # "inferred" | "unknown"
    units: list[dict] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "units": self.units,
            "artifacts": self.artifacts,
            "notes": self.notes,
        }


def _compose_services(text: str) -> dict[str, dict]:
    """Light indent-based extraction of a compose `services:` block (PyYAML is not
    a dependency). Handles the common 2-space layout; disclosed as best-effort."""
    services: dict[str, dict] = {}
    in_services = False
    svc_indent: int | None = None
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_services = bool(re.match(r"^services\s*:", stripped))
            svc_indent = None
            current = None
            continue
        if not in_services:
            continue
        if svc_indent is None:
            svc_indent = indent
        if indent == svc_indent:
            name = re.match(r"^([\w.-]+)\s*:", stripped)
            current = name.group(1) if name else None
            if current:
                services[current] = {"build": False, "image": None}
        elif current and indent > svc_indent:
            if re.match(r"^build\s*:", stripped):
                services[current]["build"] = True
            elif re.match(r"^image\s*:", stripped):
                services[current]["image"] = stripped.split(":", 1)[1].strip().strip("'\"")
    return services


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return ""
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return ""


def generate(repo_path: str | Path,
             tier2_exclusions: list[str] | None = None) -> DeployUnits:
    root = Path(repo_path).expanduser().resolve()
    tier2 = set(tier2_exclusions or [])
    units: list[dict] = []
    artifacts: list[str] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, name: str, evidence: str, **extra) -> None:
        key = (kind, name)
        if key not in seen:
            seen.add(key)
            units.append({"kind": kind, "name": name, "evidence": evidence, **extra})

    count = 0
    capped = False
    stack = [root]
    while stack and not capped:
        base = stack.pop()
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP and entry.name not in tier2 \
                        and not entry.name.startswith("."):
                    stack.append(entry)
                elif entry.name == ".github":       # CI deploy workflows live here
                    stack.append(entry)
                continue
            count += 1
            if count > _MAX_FILES:
                capped = True   # stop the WHOLE walk, not just this directory
                break
            rel = entry.relative_to(root).as_posix()
            name = entry.name
            parent = entry.parent
            ctx = "." if parent == root or parent.name in {"build", "deploy", "docker"} \
                else parent.relative_to(root).as_posix()
            if name == "Dockerfile" or name.startswith("Dockerfile."):
                artifacts.append(rel)
                add("container-image", ctx, rel)
            elif _COMPOSE_RE.match(name):
                artifacts.append(rel)
                for svc, info in _compose_services(_read(entry)).items():
                    add("compose-service", svc, rel,
                        built_here=info["build"], image=info["image"])
            elif name == "main.go":
                text = _read(entry)
                if _GO_MAIN_PKG.search(text) and _GO_MAIN_FUNC.search(text):
                    add("go-main-binary",
                        str(parent.relative_to(root).as_posix()) or ".", rel)
            elif name in _CI_FILES or "/.github/workflows/" in f"/{rel}":
                text = _read(entry)
                if _CI_DEPLOY.search(text):
                    artifacts.append(rel)
                    add("ci-deploy-step", rel, rel)

    status = "inferred" if units or artifacts else "unknown"
    notes = [
        "LOCATE-only: static deploy artifacts parsed as data (compose services via "
        "a light indent scan, not full YAML) — never a claim a unit is actually "
        "deployed, never that discovery is complete.",
        "status 'unknown' = no deploy artifact found in this repo (not 'no units').",
        "a source tree with no build/run marker is library/support, not a "
        "deployable unit.",
    ]
    if capped:
        notes.append(f"COVERAGE CAP: stopped after {_MAX_FILES} files — deploy "
                     "artifacts beyond the cap were NOT scanned (incomplete).")
    return DeployUnits(status=status,
                       units=sorted(units, key=lambda u: (u["kind"], u["name"])),
                       artifacts=sorted(set(artifacts)), notes=notes)
