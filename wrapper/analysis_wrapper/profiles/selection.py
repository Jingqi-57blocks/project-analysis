"""Execution-plane technology predicates.

Single home for the "is this target go/node/ts?" and "what family/languages
does it belong to?" questions that the execution plane (registry, CLI,
depcruise_lane) needs when deciding which tools apply. Before this module,
each of those call sites carried its own copy of the same is-go/is-node
detection, some layering a raw manifest-file probe (``go.mod``/
``package.json``/``tsconfig.json``) on top as a fallback for when discovery
produced no facets: ``registry.local_tools``/``registry.network_tools``/
``registry._language_args``, ``cli._family_groups``, and
``depcruise_lane._is_ts_target``. This module replaces all of those copies.

Technology names ("go", "node", "typescript", ...) are allowed here BY
DESIGN — this is the bundled-knowledge plane, same as ``profiles/bundled.py``
itself. What changed is the SOURCE of truth: matching is by facet PRESENCE
(``RepoTarget.has_profile``, which does not filter by ``state`` — a
"conflicting" or "unknown" facet still counts) with NO manifest-file
probing. Discovery's own fingerprints cover what the old manifest probes
reached for by hand: a ``go.mod`` produces both ``language.go`` and
``ecosystem.go-module``; a ``package.json`` produces ``ecosystem.node``
(manifest-default for ``language.javascript``, manifest-file for
``ecosystem.node``); a ``tsconfig.json``/``tsconfig.app.json`` produces
``language.typescript``.

This is NOT exact old-probe equivalence everywhere, though — ``language.go``
and ``language.javascript`` ALSO fingerprint on bare source extension
(``.go``; ``.js``/``.jsx``/``.mjs``/``.cjs``) with no manifest required.
``local_tools``'s old inclusion checks already read ``target.stacks``
(itself facet-derived) before ever falling back to a manifest probe, so it
already had this breadth and is unaffected. ``network_tools``'s old gates,
however, keyed directly off ``go.mod``/``package.json`` file existence with
no facet involved at all — so its new facet-driven gates here are STRICTLY
BROADER: a go-source repo with no committed ``go.mod`` (source-extension
facet only) now selects into osv-scanner, and a js-source repo with no
``package.json`` now selects into ``outdated``, where the old probes would
have skipped both outright. This is accepted by design, consistent with
``local_tools``'s pre-existing breadth: the affected tools do not silently
misbehave on such a repo — they run and fail (or degrade) individually,
disclosed in their own manifest, rather than being silently skipped. A
target discovery never faceted at all (no real target should reach here
that way) still simply selects nothing — there is no second detection path
underneath this one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..targetspec import RepoTarget

_NODE_PROFILES = ("language.javascript", "language.typescript", "ecosystem.node")


def is_go_target(target: "RepoTarget") -> bool:
    return target.has_profile("language.go")


def is_node_target(target: "RepoTarget") -> bool:
    # ecosystem.node reproduces the old raw package.json-probe reach: a
    # package.json-only repo with zero JS/TS sources still counts as node.
    # language.javascript/language.typescript reach further than that probe
    # did on their own (they also fingerprint on bare source extension, no
    # manifest needed) — accepted breadth, see the module docstring for why
    # that only matters at the network_tools call site.
    return any(target.has_profile(profile_id) for profile_id in _NODE_PROFILES)


def is_ts_target(target: "RepoTarget") -> bool:
    return target.has_profile("language.typescript")


def lizard_languages(target: "RepoTarget") -> list[str]:
    """Lizard ``-l`` language names for ``target``'s language facets.

    TypeScript implies TSX: a React repo whose discovery faceted only
    ``language.typescript`` must not silently lose .tsx complexity coverage
    (review P3-13) — the argv byte-shape feeds lizard manifests, so this
    reproduces the old alias-table output exactly for facet-carrying
    targets, sorted the same way.
    """
    langs: set[str] = set()
    if is_go_target(target):
        langs.add("go")
    if target.has_profile("language.javascript"):
        langs.add("javascript")
    if is_ts_target(target):
        langs.add("typescript")
        langs.add("tsx")
    return sorted(langs)


def family(target: "RepoTarget") -> str:
    """"node"/"go"/"other" language family for same-language cross-repo runs.

    node wins ties over go (matches the old elif order in
    ``cli._family_groups``): a polyglot go+js repo groups as node.
    """
    if is_node_target(target):
        return "node"
    if is_go_target(target):
        return "go"
    return "other"
