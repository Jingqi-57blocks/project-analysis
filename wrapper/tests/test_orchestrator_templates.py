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

# 57B-116: source_reads flags a paired selection-fetch task (planner.py's
# two-phase select/finalize flow) -- see each flagged lens file's own
# frontmatter comment for the justification.
EXPECTED_SOURCE_READS = {
    "structure-inventory": True,
    "complexity": False,
    "dead-code": False,
    "duplication": False,
    "dependencies-cycles": True,
    "hotspots-change-friction": False,
    "safety-net": True,
    "dependency-risk": False,
    "open-lens": True,
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


def test_source_reads_classification_matches_the_documented_design():
    loaded = tpl.load_lens_templates(REAL_SKILL_ROOT)
    assert {lens_id: t.source_reads for lens_id, t in loaded.items()} == EXPECTED_SOURCE_READS
    for lens_id, template in loaded.items():
        assert isinstance(template.source_reads, bool), lens_id


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


def test_output_contract_preamble_states_the_exact_coverage_row_shape():
    preamble = tpl.LENS_OUTPUT_CONTRACT_PREAMBLE
    assert '"signal"' in preamble and '"note"' in preamble
    assert '"complete" | "partial" | "failed" | "skipped"' in preamble
    assert "No other keys" in preamble
    assert "never a prose sentence" in preamble


def test_output_contract_preamble_gives_all_three_citation_grammars_with_examples():
    preamble = tpl.LENS_OUTPUT_CONTRACT_PREAMBLE
    assert "repo@revision:path:line" in preamble
    assert "signals/<view-file>:line" in preamble
    assert "metric:<metric_ref>" in preamble
    # one concrete example per grammar, each independently grammar-valid.
    from analysis_wrapper.orchestrator.schemas import citation_grammar_kind
    examples = {
        "source": "api@4f1c9a2b3d5e6f708192a3b4c5d6e7f809192a3b:internal/handler.go:42",
        "signal": "signals/lizard-api.view.txt:13",
        "metric": "metric:code.analyzed-scope.total",
    }
    for kind, example in examples.items():
        assert example in preamble
        assert citation_grammar_kind(example) == kind


def test_output_contract_preamble_forbids_candidate_ids_and_input_names_as_refs():
    preamble = tpl.LENS_OUTPUT_CONTRACT_PREAMBLE
    assert "NEVER a candidate_id" in preamble
    assert "NEVER an input" in preamble
    assert "module-candidates.json" in preamble  # a real input NAME, cited as a bad example


# --------------------------------------------------------------------------- #
# 57B-116: source_reads select/finalize instruction assembly
# --------------------------------------------------------------------------- #

def test_render_instructions_appends_source_verified_addendum_only_when_asked():
    loaded = tpl.load_lens_templates(REAL_SKILL_ROOT)
    shared = tpl.load_shared_body(REAL_SKILL_ROOT)
    plain = tpl.render_instructions(loaded["safety-net"], shared)
    verified = tpl.render_instructions(loaded["safety-net"], shared, source_verified=True)
    assert tpl.SOURCE_VERIFIED_ADDENDUM not in plain
    assert tpl.SOURCE_VERIFIED_ADDENDUM in verified
    assert verified.startswith(plain.rstrip("\n"))  # addendum is appended, not inserted
    assert "fetched-evidence.json" in tpl.SOURCE_VERIFIED_ADDENDUM


def test_render_selection_instructions_uses_the_selection_preamble_not_the_lens_one():
    loaded = tpl.load_lens_templates(REAL_SKILL_ROOT)
    shared = tpl.load_shared_body(REAL_SKILL_ROOT)
    instructions = tpl.render_selection_instructions(loaded["open-lens"], shared)
    assert instructions.index(tpl.SELECTION_FETCH_PREAMBLE) == 0
    assert tpl.LENS_OUTPUT_CONTRACT_PREAMBLE not in instructions
    assert "# Lens: open-lens" in instructions  # still carries the lens's own body
    assert "quoted_text" in instructions and 'EMPTY ("")' in instructions
    assert "up to 12" in instructions.lower() or "UP TO 12" in instructions


def test_frontmatter_parses_bare_true_false_as_python_bool(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    root = _write_synthetic_lens_dir(tmp_path / "a", lens_frontmatter=(
        "---\nshard: repo\nsignals: []\nsource_reads: true\n---\n"))
    assert tpl.load_lens_templates(root)["sample-lens"].source_reads is True

    root2 = _write_synthetic_lens_dir(tmp_path / "b", lens_frontmatter=(
        "---\nshard: repo\nsignals: []\nsource_reads: FALSE\n---\n"))
    assert tpl.load_lens_templates(root2)["sample-lens"].source_reads is False


def test_source_reads_defaults_false_when_absent_from_frontmatter(tmp_path):
    root = _write_synthetic_lens_dir(
        tmp_path, lens_frontmatter="---\nshard: repo\nsignals: []\n---\n")
    assert tpl.load_lens_templates(root)["sample-lens"].source_reads is False


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
