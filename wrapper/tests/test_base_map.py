"""Base -> backend association: evidence-based, conservative, domain-neutral."""

from analysis_wrapper.discovery import base_map
from analysis_wrapper.discovery.liveness import _matches


def _resolve(paths_by_base, backend_routes):
    return base_map.resolve_base_backends(paths_by_base, backend_routes, _matches)[0]


def test_base_resolves_to_the_backend_its_calls_cover():
    # apiA's paths land on svc-a (3) far more than svc-b (1) -> svc-a.
    routes = [
        ("svc-a", [["a-only"], ["a-two"], ["shared"]]),
        ("svc-b", [["b-only"], ["b-two"], ["shared"]]),
    ]
    paths = {"apiA": {("a-only", "1"), ("a-two", "2"), ("shared", "9")}}
    assert _resolve(paths, routes) == {"apiA": "svc-a"}


def test_base_matching_two_backends_equally_is_unresolved():
    # No dominance (1 vs 1) -> conservative: attribute to neither.
    routes = [("svc-a", [["shared"]]), ("svc-b", [["shared"]])]
    paths = {"ambi": {("shared", "1"), ("shared", "2")}}
    assert _resolve(paths, routes) == {"ambi": None}


def test_single_coincidental_match_is_below_the_floor():
    # One matched path is too thin to attribute a base to a backend.
    routes = [("svc-a", [["client"]]), ("svc-b", [["other"]])]
    paths = {"lonely": {("client", "activation", "x", "y")}}
    assert _resolve(paths, routes) == {"lonely": None}


def test_dominance_requires_a_clear_margin():
    routes = [("svc-a", [["x"], ["y"], ["z"]]), ("svc-b", [["x"], ["y"]])]
    # svc-a: 3 matched, svc-b: 2 matched -> 3 < 2*2, not dominant -> unresolved.
    paths = {"base": {("x", "1"), ("y", "1"), ("z", "1")}}
    assert _resolve(paths, routes) == {"base": None}
    # Add a second z-shaped path only svc-a serves: 4 vs 2 -> dominant.
    paths = {"base": {("x", "1"), ("y", "1"), ("z", "1"), ("z", "2")}}
    assert _resolve(paths, routes) == {"base": "svc-a"}


def test_notes_disclose_resolved_and_unresolved_bases():
    routes = [("svc-a", [["a"], ["b"]]), ("svc-b", [["c"]])]
    paths = {"good": {("a", "1"), ("b", "1")}, "weak": {("c", "1")}}
    resolved, notes = base_map.resolve_base_backends(paths, routes, _matches)
    assert resolved == {"good": "svc-a", "weak": None}
    text = " ".join(notes)
    assert "good->svc-a" in text
    assert "UNRESOLVED" in text and "weak" in text


def test_deterministic_ranking_on_tie_counts():
    # Equal match counts must rank by repo_id so resolution is reproducible;
    # a genuine tie stays unresolved regardless of backend order.
    routes_ab = [("svc-a", [["shared"]]), ("svc-b", [["shared"]])]
    routes_ba = [("svc-b", [["shared"]]), ("svc-a", [["shared"]])]
    paths = {"t": {("shared", "1"), ("shared", "2")}}
    assert _resolve(paths, routes_ab) == _resolve(paths, routes_ba) == {"t": None}
