"""Durable skill-file invariants (Phase-1 exit criteria, greppable form).

Guards the prose that ships: zero target-project literals, the standing
disclaimer in every report template, valid skill frontmatter, and the lens
set staying consistent with its README grouping.
"""

import json
import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
PROSE_FILES = [SKILL_ROOT / "SKILL.md", SKILL_ROOT / "synthesis.md"] \
    + sorted((SKILL_ROOT / "templates").glob("*.md")) \
    + sorted((SKILL_ROOT / "lenses").glob("*.md"))

# Target-project literals that must never appear in shipped skill prose or the
# wrapper's declarative rules/fixtures (both are ship candidates).
FORBIDDEN = re.compile(r"wcp|57block|jira|bitbucket|rancher|worklog|beisen|italent", re.I)

# The wrapper's ast-grep rules + their fixtures ship with the tool and must stay
# evidence-free (domain-neutral widget/gadget naming only).
RULE_FILES = sorted(p for p in (SKILL_ROOT / "wrapper" / "rules").rglob("*")
                    if p.is_file())

DISCLAIMER_MARK = "repository evidence only"


def test_shipped_prose_has_zero_target_literals():
    offenders = []
    for path in PROSE_FILES:
        for i, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(SKILL_ROOT)}:{i}: {line.strip()[:80]}")
    assert not offenders, "target literals in shipped prose:\n" + "\n".join(offenders)


def test_wrapper_rules_and_fixtures_have_zero_target_literals():
    offenders = []
    for path in RULE_FILES:
        try:
            text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(SKILL_ROOT)}:{i}: {line.strip()[:80]}")
    assert not offenders, "target literals in wrapper rules/fixtures:\n" + "\n".join(offenders)


def test_every_report_template_carries_the_standing_disclaimer():
    for path in sorted((SKILL_ROOT / "templates").glob("*.md")):
        assert DISCLAIMER_MARK in path.read_text("utf-8"), \
            f"{path.name}: standing scope disclaimer missing"


def test_skill_frontmatter_names_the_command():
    text = (SKILL_ROOT / "SKILL.md").read_text("utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "SKILL.md must start with YAML frontmatter"
    fields = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    assert fields["name"].strip() == "project-analysis"
    assert len(fields["description"].strip()) > 40


def test_lens_set_matches_readme_grouping():
    lens_files = {p.stem for p in (SKILL_ROOT / "lenses").glob("*.md")} \
        - {"README", "_shared"}
    readme = (SKILL_ROOT / "lenses" / "README.md").read_text("utf-8")
    missing = sorted(name for name in lens_files if name not in readme)
    assert not missing, f"lenses not mapped to a group in README: {missing}"
    assert len(lens_files) == 9, f"expected 9 lenses, found {sorted(lens_files)}"


def test_lens_coverage_catalog_matches_installed_lenses_and_tools():
    lens_files = {p.stem for p in (SKILL_ROOT / "lenses").glob("*.md")} \
        - {"README", "_shared"}
    catalog = json.loads((SKILL_ROOT / "lenses" / "coverage-map.json").read_text())
    rows = catalog["lenses"]
    assert {row["lens_id"] for row in rows} == lens_files
    known_tools = {
        "scc", "lizard", "jscpd", "jscpd-cross", "dependency-cruiser",
        "staticcheck", "go-list", "git-history", "osv-scanner", "outdated",
    }
    mapped = {tool for row in rows for tool in row["tools"]}
    assert mapped <= known_tools


def test_every_lens_reminds_the_finding_shape_or_defers_to_shared():
    shared = (SKILL_ROOT / "lenses" / "_shared.md").read_text("utf-8")
    assert "suggested_direction" in shared and "confidence" in shared
    for path in sorted((SKILL_ROOT / "lenses").glob("*.md")):
        if path.stem in {"README", "_shared"}:
            continue
        text = path.read_text("utf-8")
        assert "evidence" in text.lower(), f"{path.name}: no evidence discipline"
        assert "group " in text.splitlines()[0].lower() or "(group" in text.splitlines()[0], \
            f"{path.name}: first line must state its group"
