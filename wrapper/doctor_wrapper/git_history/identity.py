"""Conservative author identity handling for history signals."""

from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict

from ..gitinfo import git_command, safe_git_env

_BOT = re.compile(
    r"\[bot\]|dependabot|renovate|greenkeeper|snyk[-_ ]?bot|github[-_ ]?actions|"
    r"semantic-release|mergify|whitesource|imgbot|circleci|travis[-_ ]?ci|"
    r"gitlab[-_ ]?runner|teamcity|buildkite|jenkins|noreply@github\.com|actions@github\.com",
    re.I,
)
_NOREPLY = re.compile(r"^\d+\+(.+@users\.noreply\.github\.com)$", re.I)


def is_bot(name: str, email: str) -> bool:
    return bool(_BOT.search(f"{name} {email}"))


def normalize_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    match = _NOREPLY.match(email)
    return match.group(1) if match else email


def apply_mailmap(repo: str, identities: list[tuple[str, str]]) -> dict[tuple[str, str], tuple[str, str]]:
    """Use Git's own .mailmap parser; failure safely falls back to exact identities."""
    unique = sorted(set(identities))
    if not unique:
        return {}
    payload = "".join(f"{name} <{email}>\n" for name, email in unique)
    try:
        proc = subprocess.run(
            git_command(repo, "check-mailmap", "--stdin"), input=payload,
            capture_output=True, text=True, timeout=30,
            env=safe_git_env(),
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        proc = None
    if not proc or proc.returncode != 0 or len(proc.stdout.splitlines()) != len(unique):
        return {x: x for x in unique}
    mapped: dict[tuple[str, str], tuple[str, str]] = {}
    pattern = re.compile(r"^(.*) <([^>]*)>$")
    for original, line in zip(unique, proc.stdout.splitlines()):
        match = pattern.match(line.strip())
        mapped[original] = (match.group(1), match.group(2)) if match else original
    return mapped


class IdentityResolver:
    """Merge only strong evidence: .mailmap and exact normalized email.

    Same-name/different-email observations are returned as uncertain candidates;
    they are never silently merged.
    """

    def __init__(self, repo: str, observations: list[tuple[str, str]]):
        self._mapped = apply_mailmap(repo, observations)
        frequency = Counter(self._mapped.get(x, x) for x in observations)
        by_email: dict[object, list[tuple[str, str]]] = defaultdict(list)
        for identity in frequency:
            email = normalize_email(identity[1])
            # Only a non-empty email is STRONG evidence. An identity without an
            # email stays its own group — a shared display name must never merge
            # observations silently (module contract).
            by_email[email if email else ("no-email", identity)].append(identity)
        self._labels: dict[tuple[str, str], str] = {}
        for members in by_email.values():
            label = sorted(members, key=lambda x: (-frequency[x], normalize_name(x[0]), normalize_email(x[1])))[0]
            display = label[0].strip() or label[1].strip() or "(unknown)"
            for member in members:
                self._labels[member] = display
        names: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for identity in frequency:
            names[normalize_name(identity[0])].add(identity)
        self.uncertain_name_matches = sorted(
            name for name, members in names.items()
            if name and len({normalize_email(e) or ("no-email", n, e) for n, e in members}) > 1
        )

    def resolve(self, name: str, email: str) -> str:
        mapped = self._mapped.get((name, email), (name, email))
        return self._labels.get(mapped, mapped[0].strip() or mapped[1].strip() or "(unknown)")
