"""system_model.ids — deterministic IDs + citation parsing."""

import pytest

from analysis_wrapper.system_model import ids


def test_stable_id_is_deterministic_and_order_sensitive():
    a = ids.stable_id("symbol", "repo", "Foo", "repo@sha:f.go:1")
    b = ids.stable_id("symbol", "repo", "Foo", "repo@sha:f.go:1")
    assert a == b
    assert a.startswith("sym:")
    # Different natural key -> different id; part order matters.
    assert ids.stable_id("symbol", "repo", "Foo", "x") != \
           ids.stable_id("symbol", "repo", "x", "Foo")


def test_stable_id_rejects_unknown_kind():
    with pytest.raises(ValueError):
        ids.stable_id("widget", "x")


def test_parse_citation_full_and_degraded():
    assert ids.parse_citation("repo-1@" + "a" * 40 + ":src/a.ts:12:3") == \
           ("repo-1", "a" * 40, "src/a.ts", 12, 3)
    assert ids.parse_citation("repo-1@sha:src/a.ts:12") == \
           ("repo-1", "sha", "src/a.ts", 12, None)
    # No position tail / no commit degrade rather than raise.
    assert ids.parse_citation("repo-1@sha:src/a.ts")[2] == "src/a.ts"
    assert ids.parse_citation("just-a-path")[2] == "just-a-path"


def test_make_citation_from_relative_position():
    assert ids.make_citation("repo-1", "sha", "src/a.ts:5") == "repo-1@sha:src/a.ts:5"
    assert ids.make_citation("repo-1", "sha", "src/a.ts:5:2") == "repo-1@sha:src/a.ts:5:2"
    assert ids.make_citation("repo-1", "sha", "Dockerfile") == "repo-1@sha:Dockerfile"
    # Non-git repo uses the nogit sentinel.
    assert ids.make_citation("repo-1", "", "f.go:1") == "repo-1@nogit:f.go:1"


def test_citation_file_roundtrip():
    assert ids.citation_file(ids.make_citation("r", "s", "a/b.go:3")) == "a/b.go"
