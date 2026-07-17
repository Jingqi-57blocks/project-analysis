"""Integration CANDIDATE generation (57B-11 S6, plan §17.7).

Mechanical producers over committed sources. Signal kinds:
`dependency`, `import`, `client_init`, `outbound_endpoint`, `config`, `env`,
`oauth_provider`, `ci_resource`. One candidate per VALUE with all signal kinds
merged (`dependency+import+client_init`); a candidate whose ONLY signal is a
dependency/lockfile declaration is labeled `dependency-only`.

Hard rules (plan §17.7): NO integration name lists — every pattern is
structural (syntax, file conventions, URL shapes). Disposition happens
downstream in the map stage; this module records evidence, never classifies
activity. Env variables contribute NAMES only, never values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..targetspec import IntegrationCandidate

_SKIP_DIRS = {"node_modules", "vendor", ".git", "dist", "build", "coverage"}
_SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go"}
_CONFIG_EXT = {".json", ".yml", ".yaml", ".toml", ".ini", ".conf"}
_MAX_FILES = 4000
_MAX_EVIDENCE = 3
_MAX_BYTES = 262_144

_JS_IMPORT = re.compile(
    r"(?:require\(\s*['\"]|from\s+['\"]|import\s*\(\s*['\"]|^import\s+['\"])"
    r"([^'\"./][^'\"]*)['\"]", re.M)
_GO_IMPORT = re.compile(r'^\s*(?:[\w.]+\s+)?"([\w-]+\.[\w.-]+/[^"]+)"', re.M)
# DISCLOSURE (57B-26): a declarative ast-grep equivalent exists at
# `wrapper/rules/client-init.yml` (rule-ready, fixtured + tested), but this
# per-file, near-an-import cross-reference is NOT yet wired to it — client_init
# candidate detection here still uses this transparent regex. Migrating this
# cross-reference to the rule is a follow-up; until then the rule and this regex
# coexist and the rule is not consumed by a producer.
_CLIENT_INIT = re.compile(
    r"new\s+[\w.]*Client\b|\.createClient\s*\(|\bNew\w*Client\s*\(|"
    r"createTransport\s*\(|NewSession\s*\(|\.connect\s*\(")
_URL = re.compile(r"https?://([\w.-]+\.[A-Za-z]{2,})(?::\d+)?(/[\w./#?&=%-]*)?")
_OAUTH_HINT = re.compile(r"oauth|/authorize\b|openid-configuration|\.well-known", re.I)
_ENV_JS = re.compile(r"process\.env\.([A-Z][A-Z0-9_]{2,})")
_ENV_VITE = re.compile(r"import\.meta\.env\.([A-Z][A-Z0-9_]{2,})")
_ENV_GO = re.compile(r'os\.Getenv\(\s*"([A-Z][A-Z0-9_]{2,})"\s*\)')
_ENV_FILE_LINE = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*=(.*)$")
_CI_RESOURCE = re.compile(r"^\s*-?\s*(?:image|uses|pipe)\s*:\s*['\"]?([\w./@:-]+)", re.M)
_CI_FILES = ("bitbucket-pipelines.yml", ".gitlab-ci.yml", "Jenkinsfile",
             "azure-pipelines.yml")
# Structural NOISE filter (loopback/doc/schema hosts) — hygiene, not an
# integration list; disclosed in the report notes.
_NOISE_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "example.com", "www.example.com",
    "www.w3.org", "json.schemastore.org", "schemas.microsoft.com",
    "registry.npmjs.org", "registry.yarnpkg.com",
}


@dataclass
class CandidateReport:
    candidates: list[IntegrationCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # caps/filters disclosed


def _package_key(spec: str, *, go: bool) -> str:
    if go:
        return "/".join(spec.split("/")[:3])
    if spec.startswith("@"):
        return "/".join(spec.split("/")[:2])
    return spec.split("/")[0]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:64]


class _Collector:
    """Merges per-value signals; emits sorted IntegrationCandidates."""

    def __init__(self, repo_id: str):
        self.repo_id = repo_id
        self._kinds: dict[str, set[str]] = {}
        self._evidence: dict[str, list[str]] = {}

    def add(self, value: str, kind: str, where: str) -> None:
        value = value.strip()
        if not value:
            return
        self._kinds.setdefault(value, set()).add(kind)
        rows = self._evidence.setdefault(value, [])
        entry = f"{where} ({kind})"
        if len(rows) < _MAX_EVIDENCE and entry not in rows:
            rows.append(entry)

    def report(self, notes: list[str]) -> CandidateReport:
        out = CandidateReport(notes=notes)
        for value in sorted(self._kinds):
            kinds = self._kinds[value]
            label = "dependency-only" if kinds == {"dependency"} \
                else "+".join(sorted(kinds))
            out.candidates.append(IntegrationCandidate(
                candidate_id=f"{self.repo_id}:{_slug(value)}",
                repo_id=self.repo_id,
                signal_kind=label,
                value=value,
                evidence=self._evidence[value],
            ))
        return out


def _iter_files(root: Path, tier2: set[str], notes: list[str]):
    count = 0
    stack = [root]
    while stack:
        base = stack.pop()
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if entry.is_dir():
                hidden_skip = (entry.parent == root and name.startswith(".")
                               and name != ".github")
                if name not in _SKIP_DIRS and name not in tier2 and not hidden_skip:
                    stack.append(entry)
                continue
            count += 1
            if count > _MAX_FILES:
                notes.append(
                    f"file cap hit: only first {_MAX_FILES} files scanned (disclosed)")
                return
            yield entry


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return ""
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _tracked_env_files(root: Path) -> set[str]:
    """Repo-relative paths of git-TRACKED .env* files (committed config)."""
    import subprocess

    from .. import gitinfo
    try:
        proc = subprocess.run(
            gitinfo.git_command(root, "ls-files", ".env*", "**/.env*"),
            env=gitinfo.safe_git_env(), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _scan_env_file(collect: _Collector, rel: str, text: str, *,
                   tracked: bool) -> None:
    """Env files yield variable NAMES always (policy: names only). Endpoint
    HOSTS are additionally derived from values ONLY for git-TRACKED env files
    (committed config, e.g. .env.production) — hosts, never full values."""
    for i, line in enumerate(text.splitlines(), 1):
        m = _ENV_FILE_LINE.match(line.strip())
        if not m:
            continue
        collect.add(m.group(1), "env", f"{rel}:{i}")
        if tracked:
            for url_match in _URL.finditer(m.group(2)):
                host = url_match.group(1).lower()
                if host not in _NOISE_HOSTS and not host.endswith(".invalid"):
                    collect.add(host, "config", f"{rel}:{i}")


def _scan_source(collect: _Collector, rel: str, text: str, *, go: bool) -> None:
    imports: list[str] = []
    for m in (_GO_IMPORT if go else _JS_IMPORT).finditer(text):
        key = _package_key(m.group(1), go=go)
        imports.append(key)
        collect.add(key, "import", f"{rel}:{_line_of(text, m.start())}")
    lines = text.splitlines()
    for m in _CLIENT_INIT.finditer(text):
        line_no = _line_of(text, m.start())
        line = lines[line_no - 1].lower() if line_no <= len(lines) else ""
        for key in imports:
            segments = [s for s in re.split(r"[/@.-]", key) if len(s) >= 3]
            if any(s.lower() in line for s in segments):
                collect.add(key, "client_init", f"{rel}:{line_no}")
    for pattern in (_ENV_JS, _ENV_VITE, _ENV_GO):
        for m in pattern.finditer(text):
            collect.add(m.group(1), "env", f"{rel}:{_line_of(text, m.start())}")


def _scan_urls(collect: _Collector, rel: str, text: str, *, in_config: bool) -> None:
    for m in _URL.finditer(text):
        host = m.group(1).lower()
        if host in _NOISE_HOSTS or host.endswith(".invalid"):
            continue
        where = f"{rel}:{_line_of(text, m.start())}"
        collect.add(host, "config" if in_config else "outbound_endpoint", where)
        if _OAUTH_HINT.search(m.group(0)):
            collect.add(host, "oauth_provider", where)


def generate(repo_path: str | Path, repo_id: str,
             dependencies: dict[str, str] | None = None,
             go_requires: list[str] | None = None,
             tier2_exclusions: list[str] | None = None) -> CandidateReport:
    """Produce integration candidates for one repo.

    `dependencies` (package.json sections merged) and `go_requires` (direct
    go.mod requires) let declaration-vs-usage be distinguished mechanically.
    """
    root = Path(repo_path).expanduser().resolve()
    collect = _Collector(repo_id)
    notes = ["noise filter: loopback/doc/schema/registry hosts suppressed"]

    for name in (dependencies or {}):
        collect.add(_package_key(name, go=False), "dependency", "package.json")
    for module in (go_requires or []):
        collect.add(_package_key(module, go=True), "dependency", "go.mod")

    tracked_env = _tracked_env_files(root)
    for path in _iter_files(root, set(tier2_exclusions or []), notes):
        rel = path.relative_to(root).as_posix()

        if path.name.startswith(".env"):
            _scan_env_file(collect, rel, _read(path), tracked=rel in tracked_env)
            continue

        is_ci = path.name in _CI_FILES or "/.github/workflows/" in f"/{rel}"
        suffix = path.suffix
        if suffix not in _SOURCE_EXT and suffix not in _CONFIG_EXT and not is_ci:
            continue
        text = _read(path)
        if not text:
            continue

        if is_ci:
            for m in _CI_RESOURCE.finditer(text):
                collect.add(m.group(1), "ci_resource",
                            f"{rel}:{_line_of(text, m.start())}")
        if suffix in _SOURCE_EXT:
            _scan_source(collect, rel, text, go=suffix == ".go")
        _scan_urls(collect, rel, text, in_config=suffix in _CONFIG_EXT or is_ci)

    return collect.report(notes)
