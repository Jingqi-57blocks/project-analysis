"""Call-graph contract: citations, edge round-trip, coverage accounting."""

from pathlib import Path

import pytest

from analysis_wrapper.callgraph import contract
from analysis_wrapper.callgraph.contract import (CallEdge, CallSiteCounts,
                                                 CoverageReport, RepoCoverage)


def _edge(**over):
    base = dict(lang="go", resolution="observed", kind="static-call",
                caller_symbol="svc.Run", caller_citation="app@abc:svc/run.go:10",
                callee_symbol="db.Query", callee_citation="app@abc:db/db.go:5",
                callsite_citation="app@abc:svc/run.go:12:5")
    base.update(over)
    return CallEdge(**base)


def test_citation_with_and_without_column():
    assert contract.citation("app", "abc", "svc/run.go", 10) == "app@abc:svc/run.go:10"
    assert contract.citation("app", "abc", "svc/run.go", 10, 5) == "app@abc:svc/run.go:10:5"


def test_citation_non_git_uses_stable_sentinel():
    assert contract.citation("app", "", "a.go", 1) == "app@nogit:a.go:1"


def test_citation_from_position_relativizes(tmp_path):
    root = tmp_path.resolve()
    pos = f"{root}/svc/run.go:12:5"
    assert contract.citation_from_position(pos, "app", "abc", root) == "app@abc:svc/run.go:12:5"


def test_edge_round_trips_through_json():
    edge = _edge()
    restored = CallEdge.from_dict(__import__("json").loads(edge.to_json_line()))
    assert restored == edge


def test_edge_rejects_bad_enum_values():
    with pytest.raises(ValueError):
        _edge(resolution="guessed")
    with pytest.raises(ValueError):
        _edge(kind="telepathy")
    with pytest.raises(ValueError):
        _edge(lang="cobol")


def test_edge_requires_core_fields():
    with pytest.raises(ValueError):
        _edge(caller_symbol="")


def test_sort_key_is_stable_and_orders_by_callsite():
    a = _edge(callsite_citation="app@abc:svc/run.go:12:5")
    b = _edge(callsite_citation="app@abc:svc/run.go:99:1")
    assert sorted([b, a], key=lambda e: e.sort_key()) == [a, b]


def test_sort_key_is_a_total_order_over_caller_symbol():
    # Synthetic package-init edges share a positionless caller_citation and
    # differ only in caller_symbol; sort_key MUST still distinguish them so the
    # written jsonl order is not left to hash-seed-dependent set iteration.
    a = _edge(caller_symbol="p/a.init", caller_citation="app@abc::0")
    b = _edge(caller_symbol="p/b.init", caller_citation="app@abc::0")
    assert a.sort_key() != b.sort_key()
    assert sorted([b, a], key=lambda e: e.sort_key()) == [a, b]


def test_call_site_counts_total():
    counts = CallSiteCounts(resolved=3, ambiguous=1, external=4, unresolved=2)
    assert counts.total == 10
    assert counts.to_dict()["total"] == 10


def test_coverage_status_complete_partial():
    assert contract.coverage_status({".go": 10}, {".go": 10}, 0) == "complete"
    assert contract.coverage_status({".go": 10}, {".go": 7}, 0) == "partial"
    assert contract.coverage_status({".go": 10}, {".go": 10}, 2) == "partial"
    # An eligible extension present but never analyzed -> partial (never dropped).
    assert contract.coverage_status({".ts": 5}, {}, 0) == "partial"


def test_repo_coverage_rejects_bad_status():
    with pytest.raises(ValueError):
        RepoCoverage(repository_ref="r", lang="go", status="mysterious")


def test_coverage_report_json_is_deterministic_and_sorted():
    cov_a = RepoCoverage(repository_ref="b", lang="go", status="complete")
    cov_b = RepoCoverage(repository_ref="a", lang="ts", status="partial")
    report = CoverageReport(scan_date="2026-07-17", repos=[cov_a, cov_b])
    first = report.to_json()
    assert report.to_json() == first                 # stable bytes
    ids = [r["repository_ref"] for r in __import__("json").loads(first)["repos"]]
    assert ids == ["a", "b"]                          # sorted
