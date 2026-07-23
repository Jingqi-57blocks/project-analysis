"""57B-79: SourceRef/Fact and their alignment with findings.py's citation grammar."""

import pytest

from analysis_wrapper.evidence.facts import Fact, SourceRef, make_fact_id


def test_source_ref_round_trip_matches_findings_grammar():
    exact = f"api@{'a' * 40}:internal/service.go:2"
    ref = SourceRef.from_string(exact)
    assert ref.repository_ref == "api"
    assert ref.revision == "a" * 40
    assert ref.path == "internal/service.go"
    assert ref.line == 2
    assert ref.to_string() == exact


def test_source_ref_accepts_non_git_and_worktree_sentinels():
    for sentinel in ("NON-GIT", "WORKTREE"):
        ref = SourceRef(repository_ref="api", revision=sentinel, path="a.go", line=1)
        assert ref.to_string() == f"api@{sentinel}:a.go:1"


def test_source_ref_rejects_bad_revision_path_and_line():
    with pytest.raises(ValueError, match="revision"):
        SourceRef(repository_ref="api", revision="short", path="a.go", line=1)
    with pytest.raises(ValueError, match="revision"):
        SourceRef(repository_ref="api", revision="A" * 40, path="a.go", line=1)
    with pytest.raises(ValueError, match="path"):
        SourceRef(repository_ref="api", revision="a" * 40, path="../a.go", line=1)
    with pytest.raises(ValueError, match="path"):
        SourceRef(repository_ref="api", revision="a" * 40, path="/abs.go", line=1)
    with pytest.raises(ValueError, match="line"):
        SourceRef(repository_ref="api", revision="a" * 40, path="a.go", line=0)
    with pytest.raises(ValueError, match="repository_ref"):
        SourceRef(repository_ref="", revision="a" * 40, path="a.go", line=1)


def test_source_ref_from_string_rejects_malformed_refs():
    with pytest.raises(ValueError, match="invalid source ref"):
        SourceRef.from_string("not-a-valid-ref")
    with pytest.raises(ValueError, match="invalid source ref"):
        SourceRef.from_string(f"api@{'a' * 40}:internal/service.go:not-a-line")


def test_fact_validates_kind_and_json_safe_data():
    with pytest.raises(ValueError, match="JSON-safe"):
        Fact(fact_id="fact:0123456789abcdef", kind="observation", data={"bad": object()})
    with pytest.raises(ValueError):
        Fact(fact_id="fact:0123456789abcdef", kind="", data={})
    with pytest.raises(ValueError):
        Fact(fact_id="", kind="observation", data={})
    with pytest.raises(ValueError, match="source_refs"):
        Fact(fact_id="fact:0123456789abcdef", kind="observation", data={},
             source_refs=({"not": "a SourceRef"},))


def test_fact_to_dict_serializes_source_refs_as_sorted_strings():
    first = SourceRef(repository_ref="api", revision="a" * 40, path="b.go", line=3)
    second = SourceRef(repository_ref="api", revision="a" * 40, path="a.go", line=1)
    fact = Fact(fact_id="fact:abc", kind="route", data={"path": "/x"},
               source_refs=(first, second))
    as_dict = fact.to_dict()
    assert as_dict["data"] == {"path": "/x"}
    assert as_dict["source_refs"] == sorted([first.to_string(), second.to_string()])


def test_make_fact_id_is_deterministic_prefixed_and_key_sensitive():
    first = make_fact_id("cap", "repo-1", "route", ("GET", "/x"))
    second = make_fact_id("cap", "repo-1", "route", ("GET", "/x"))
    assert first == second
    assert first.startswith("fact:")
    different_key = make_fact_id("cap", "repo-1", "route", ("GET", "/y"))
    different_repo = make_fact_id("cap", "repo-2", "route", ("GET", "/x"))
    different_capability = make_fact_id("other-cap", "repo-1", "route", ("GET", "/x"))
    assert len({first, different_key, different_repo, different_capability}) == 4
