"""Base -> backend association for route liveness (57B-15).

A frontend base identifier (the ``${base}`` in a ``${base}/path`` call) is served
at runtime by exactly one backend. This module infers WHICH analyzed repo that
is, purely from evidence: the set of paths a base calls versus each backend's
registered routes. A base is credited to a backend only when its calls clearly
land there; overlap that fits several backends about equally (the parallel-
rewrite trap) stays UNRESOLVED, so a route is never marked ``ui-called`` on path
shape alone across backends.

No base or repo name is hardcoded — the association is computed from the
discovered calls and routes. Kept separate from the path-matching heuristics so
it can be exercised on domain-neutral inputs.
"""

from __future__ import annotations

# A base is credited to a backend only when its calls clearly land on THAT
# backend's routes: at least MIN_MATCHES of its distinct called paths match a
# route there, and that backend matches at least DOMINANCE times as many as the
# runner-up. DOMINANCE defends against the parallel-rewrite trap — a base whose
# paths fit several backends about equally stays unresolved. MIN_MATCHES is an
# evidence floor: a lone coincidental path match (e.g. a short route that is a
# prefix of one unrelated call) must not attribute a base to a backend, so a
# single match is treated as too thin — the base stays unresolved and its routes
# are `base-unresolved`, never falsely `ui-called`.
MIN_MATCHES = 2
DOMINANCE = 2


def resolve_base_backends(paths_by_base: dict, backend_routes: list,
                          matches) -> tuple[dict, list[str]]:
    """Associate each base with the backend its calls actually target.

    ``paths_by_base``: ``{base: set[tuple[str, ...]]}`` — normalized call paths
    per base. ``backend_routes``: ``[(repo_id, [route_segments, ...])]`` —
    concrete-bearing normalized routes per backend. ``matches(route_segs,
    call_segs) -> bool`` — the shared route/call prefix heuristic (injected so
    this module owns no path logic). Returns ``({base: repo_id or None}, notes)``
    — None where the evidence does not single out one backend.
    """
    resolved: dict[str, str | None] = {}
    resolved_pairs: list[str] = []
    unresolved: list[str] = []
    for base in sorted(paths_by_base):
        paths = paths_by_base[base]
        scores = []
        for repo_id, routes in backend_routes:
            matched = sum(1 for p in paths
                          if any(matches(r, list(p)) for r in routes))
            scores.append((matched, repo_id))
        scores.sort(key=lambda x: (-x[0], x[1]))  # deterministic ranking
        top_n, top_repo = scores[0] if scores else (0, None)
        second_n = scores[1][0] if len(scores) > 1 else 0
        if top_n >= MIN_MATCHES and top_n >= DOMINANCE * second_n:
            resolved[base] = top_repo
            resolved_pairs.append(f"{base}->{top_repo} ({top_n}/{len(paths)})")
        else:
            resolved[base] = None
            unresolved.append(base)
    notes = [
        "BASE RESOLUTION (57B-15): a frontend base identifier is credited to the "
        "backend whose registered routes its calls actually cover (>="
        f"{MIN_MATCHES} matched paths and >={DOMINANCE}x the runner-up); a route "
        "is `ui-called` ONLY when a caller whose resolved base maps to THAT "
        "backend hits it — never on path shape alone across backends."]
    if resolved_pairs:
        notes.append("resolved bases: " + ", ".join(resolved_pairs) + ".")
    if unresolved:
        notes.append(
            "UNRESOLVED bases (calls not attributable to a single analyzed "
            "backend — e.g. served by an unanalyzed provider, or matching several "
            "backends equally): " + ", ".join(sorted(unresolved)) +
            ". Routes matched only by these are `base-unresolved`, not credited.")
    return resolved, notes
