"""system_model.coverage._imports — complete vs partial by repo-map presence."""

from analysis_wrapper.system_model import coverage
from analysis_wrapper.system_model.builder import ModelBuilder


def _builder_with_deps(*, unresolved: int = 0) -> ModelBuilder:
    b = ModelBuilder()
    src = b.note_file("r1", "pkg/a", producer="go-list", evidence="r1@h:pkg/a")
    dst = b.note_file("r1", "pkg/b", producer="go-list", evidence="r1@h:pkg/b")
    b.add_edge("dependency", src, dst, status="observed", producer="go-list")
    for i in range(unresolved):
        b.add_unresolved_edge("dependency", src, {"specifier": f"ext{i}"},
                              producer="go-list", discriminator=f"ext{i}")
    return b


def _summary(present, mapped, expected, unresolved=0):
    return {"present": present, "mapped_repos": mapped, "expected_repos": expected,
            "repos": mapped, "unresolved": unresolved, "stdlib_omitted": 4}


def test_complete_when_every_eligible_repo_mapped_and_no_unresolved():
    part = coverage._imports(
        _builder_with_deps(), _summary(True, ["r1"], ["r1"])).to_dict()
    assert part["status"] == "complete"
    assert part["counts"]["dependency_edges"] == 1
    assert part["counts"]["repos_with_maps"] == 1
    assert part["counts"]["repos_eligible"] == 1
    assert part["counts"]["stdlib_imports_omitted"] == 4


def test_partial_and_discloses_when_an_eligible_repo_has_no_map():
    part = coverage._imports(
        _builder_with_deps(), _summary(True, ["r1"], ["r1", "r2"])).to_dict()
    assert part["status"] == "partial"
    assert any("r2" in n for n in part["notes"])
    assert part["counts"]["repos_with_maps"] == 1
    assert part["counts"]["repos_eligible"] == 2


def test_partial_when_unresolved_external_specifiers_present():
    part = coverage._imports(
        _builder_with_deps(unresolved=3),
        _summary(True, ["r1"], ["r1"], unresolved=3)).to_dict()
    assert part["status"] == "partial"
    assert part["counts"]["unresolved_edges"] == 3
    assert part["unresolved"]["external_or_unresolvable_specifiers"] == 3


def test_absent_map_is_partial_and_lists_eligible_repos():
    part = coverage._imports(
        ModelBuilder(), _summary(False, [], ["r1", "r2"])).to_dict()
    assert part["status"] == "partial"
    assert part["counts"]["dependency_edges"] == 0
    assert any("r1" in n and "r2" in n for n in part["notes"])


def test_both_producers_are_named():
    part = coverage._imports(
        _builder_with_deps(), _summary(True, ["r1"], ["r1"])).to_dict()
    assert set(part["producers"]) == {"dependency-cruiser", "go-list"}
