"""Lens template loader tests (57B-113 / 57B-116, M2): frontmatter parsing on
the real nine lens files, digest sensitivity, and failure-closed behavior on
a malformed synthetic lens directory."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator import templates as tpl

REAL_SKILL_ROOT = Path(__file__).resolve().parents[2]

# The design decision this test suite locks in place: see each lens file's
# own frontmatter comment for the justification (structure-inventory,
# hotspots-change-friction, and safety-net were corrected from an initial
# tentative classification; duplication/dependencies-cycles/dependency-risk/
# open-lens keep their tentative "workspace" call; complexity/dead-code keep
# their tentative "repo" call).
EXPECTED_SHARD = {
    "structure-inventory": "workspace",
    "complexity": "repo",
    "dead-code": "repo",
    "duplication": "workspace",
    "dependencies-cycles": "workspace",
    "hotspots-change-friction": "repo",
    "safety-net": "repo",
    "dependency-risk": "workspace",
    "open-lens": "workspace",
}


def _write_synthetic_lens_dir(tmp_path: Path, *, lens_frontmatter: str) -> Path:
    lenses = tmp_path / "lenses"
    lenses.mkdir()
    (lenses / "_shared.md").write_text("# shared rules\nsuggested_direction\n", "utf-8")
    (lenses / "sample-lens.md").write_text(lens_frontmatter + "# Lens: sample-lens\nbody\n", "utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# the real nine lens files
# --------------------------------------------------------------------------- #

def test_all_nine_real_lens_files_parse_with_valid_frontmatter():
    loaded = tpl.load_lens_templates(REAL_SKILL_ROOT)
    assert set(loaded) == set(EXPECTED_SHARD) == set(tpl.discover_lens_ids(REAL_SKILL_ROOT))
    assert len(loaded) == 9
    for lens_id, template in loaded.items():
        assert template.shard in tpl.SHARD_KINDS
        assert isinstance(template.signals, tuple)
        assert all(isinstance(item, str) and item for item in template.signals)
        assert template.body_md.strip(), f"{lens_id}: empty body"
        assert len(template.version) == 64  # sha256 hex digest


def test_shard_classification_matches_the_documented_design():
    loaded = tpl.load_lens_templates(REAL_SKILL_ROOT)
    assert {lens_id: t.shard for lens_id, t in loaded.items()} == EXPECTED_SHARD


def test_open_lens_signals_is_deliberately_empty_meaning_every_tool():
    loaded = tpl.load_lens_templates(REAL_SKILL_ROOT)
    assert loaded["open-lens"].signals == ()
    assert tpl.matches_signal("anything-at-all", loaded["open-lens"].signals)


def test_frontmatter_never_disturbs_the_markdown_body():
    """The body after frontmatter must still start with the lens's own
    `# Lens: <id> (group <letter>)` heading -- 57B-116 is additive-only."""
    for lens_id in tpl.discover_lens_ids(REAL_SKILL_ROOT):
        text = (REAL_SKILL_ROOT / "lenses" / f"{lens_id}.md").read_text("utf-8")
        _, body = tpl._parse_frontmatter(text, f"{lens_id}.md")
        first_line = body.splitlines()[0]
        assert first_line.startswith(f"# Lens: {lens_id} "), (lens_id, first_line)


# --------------------------------------------------------------------------- #
# render_instructions
# --------------------------------------------------------------------------- #

def test_render_instructions_assembles_preamble_shared_and_lens_body_in_order():
    loaded = tpl.load_lens_templates(REAL_SKILL_ROOT)
    shared = tpl.load_shared_body(REAL_SKILL_ROOT)
    instructions = tpl.render_instructions(loaded["complexity"], shared)
    assert instructions.index(tpl.LENS_OUTPUT_CONTRACT_PREAMBLE) == 0
    assert instructions.index("You are one analysis lens") < instructions.index(
        "# Lens: complexity")
    assert "changeability_question" in instructions  # from the updated _shared.md


# --------------------------------------------------------------------------- #
# digest sensitivity
# --------------------------------------------------------------------------- #

def test_content_digest_is_order_and_join_sensitive():
    assert tpl.content_digest("ab", "c") != tpl.content_digest("a", "bc")
    assert tpl.content_digest("a", "b") != tpl.content_digest("b", "a")
    assert tpl.content_digest("x") == tpl.content_digest("x")


def test_editing_a_lens_file_changes_its_template_version(tmp_path):
    root = _write_synthetic_lens_dir(tmp_path, lens_frontmatter=(
        "---\nshard: repo\nsignals: [scc]\n---\n"))
    before = tpl.load_lens_templates(root)["sample-lens"].version

    text = (root / "lenses" / "sample-lens.md").read_text("utf-8")
    (root / "lenses" / "sample-lens.md").write_text(text + "\nan added line\n", "utf-8")
    after = tpl.load_lens_templates(root)["sample-lens"].version
    assert before != after


def test_editing_shared_md_changes_every_templates_version(tmp_path):
    root = _write_synthetic_lens_dir(tmp_path, lens_frontmatter=(
        "---\nshard: repo\nsignals: [scc]\n---\n"))
    before = tpl.load_lens_templates(root)["sample-lens"].version

    shared_path = root / "lenses" / "_shared.md"
    shared_path.write_text(shared_path.read_text("utf-8") + "\nnew shared rule\n", "utf-8")
    after = tpl.load_lens_templates(root)["sample-lens"].version
    assert before != after


# --------------------------------------------------------------------------- #
# fail-closed on a malformed synthetic lens
# --------------------------------------------------------------------------- #

def test_missing_frontmatter_raises_template_error(tmp_path):
    root = _write_synthetic_lens_dir(tmp_path, lens_frontmatter="")
    with pytest.raises(tpl.TemplateError, match="missing YAML frontmatter"):
        tpl.load_lens_templates(root)


def test_invalid_shard_value_raises_template_error(tmp_path):
    root = _write_synthetic_lens_dir(
        tmp_path, lens_frontmatter="---\nshard: everywhere\nsignals: []\n---\n")
    with pytest.raises(tpl.TemplateError, match="shard"):
        tpl.load_lens_templates(root)


def test_non_list_signals_value_raises_template_error(tmp_path):
    root = _write_synthetic_lens_dir(
        tmp_path, lens_frontmatter="---\nshard: repo\nsignals: scc\n---\n")
    with pytest.raises(tpl.TemplateError, match="signals"):
        tpl.load_lens_templates(root)


def test_malformed_frontmatter_line_raises_template_error(tmp_path):
    root = _write_synthetic_lens_dir(
        tmp_path, lens_frontmatter="---\nshard repo\nsignals: []\n---\n")
    with pytest.raises(tpl.TemplateError, match="malformed frontmatter line"):
        tpl.load_lens_templates(root)


def test_frontmatter_comment_lines_and_trailing_comments_are_ignored(tmp_path):
    root = _write_synthetic_lens_dir(tmp_path, lens_frontmatter=(
        "---\n# a leading comment\nshard: workspace  # trailing note\n"
        "# another comment\nsignals: [scc, lizard]\n---\n"))
    template = tpl.load_lens_templates(root)["sample-lens"]
    assert template.shard == "workspace"
    assert template.signals == ("scc", "lizard")
