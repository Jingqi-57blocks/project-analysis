"""Single-run capability-provider execution loop (57B-78).

This module owns exactly ONE thing: for the CURRENT run, select the bundled
capability providers whose linked profiles were detected on a repository
target, execute each selected (provider, repository) pair exactly once
through the existing safe executor, and record what happened.  Nothing here
is a cross-run planner, a reuse/replay decision, a content-addressed store,
or a receipt-lineage system — those are explicitly out of scope for this
issue, and if a later issue ever wants them it starts fresh rather than
growing them out of this loop.

Selection is driven entirely by the ``profile_ids``/facet data a target
already carries (see :mod:`analysis_wrapper.targetspec`); this module never
branches on, or names, a concrete technology, ecosystem, or tool — a test
scans this file's source for exactly that.

A provider that raises is recorded, not propagated: one broken provider must
not hide every other provider's or repository's result for the run.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from ..evidence import catalog
from ..executor import SignalResult, replace_artifact_text
from ..identity import IdentityMap
from ..sanitize import sanitize_text
from ..targetspec import RepoTarget, TargetSpec
from .bundled import bundled_registry
from .contracts import CapabilityResult, RunContext, ToolAccess, run_provider
from .registry import ProfileRegistry
from .tool_access import ExecutorToolAccess

SCHEMA_VERSION = "1.0.0"
FILENAME = "provider-execution.json"

_REASON_LIMIT = 200


@dataclasses.dataclass(frozen=True)
class RecordingToolAccess:
    """A :class:`~.contracts.ToolAccess` wrapper that logs each call it sees.

    Providers still only ever see the narrow ``ToolAccess`` surface — no
    tool definition, argv, or executor internals reaches them through this
    wrapper any more than through the one it wraps.  It exists purely so one
    provider execution's tool calls can be disclosed in that execution's own
    record row, without changing what the provider receives back.
    """

    inner: ToolAccess
    log: list[dict[str, str]] = dataclasses.field(default_factory=list)

    def execute(
        self,
        tool_id: str,
        target: RepoTarget,
        *,
        signal_id: str = "",
    ) -> SignalResult:
        result = self.inner.execute(tool_id, target, signal_id=signal_id)
        status = getattr(result, "status", None)
        status_text = status.value if hasattr(status, "value") else str(status)
        self.log.append({
            "tool_id": tool_id, "signal_id": signal_id, "status": status_text,
        })
        return result


def run_providers(
    registry: ProfileRegistry,
    context: RunContext,
) -> tuple[list[CapabilityResult], list[dict[str, Any]]]:
    """Execute every applicable (provider, repository) pair exactly once.

    Repositories are visited in ``repo_id`` order (mirroring the executor
    sweep's own convention) and, within each, providers in the registry's
    already-sorted ``provider_id`` order — so which pairs run, and in what
    order, never depends on detection or bundled-registration order.

    A provider "applies" to a target iff at least one of the target's
    detected facets names a profile the provider is linked to. When several
    facets match the same provider, it still runs exactly once; every
    matching facet's profile ID is disclosed (sorted) in that row's
    ``matched_profiles`` instead of the duplicates being silently collapsed
    into an unexplained single run.

    A provider that declares itself ``universal`` (an OPTIONAL plain
    attribute read permissively via ``getattr`` — see
    :class:`~.contracts.CapabilityProvider`'s own docstring; never a required
    Protocol member, so no existing provider is forced to carry it) applies to
    EVERY target regardless of ``matched_profiles`` — including empty, which
    is disclosed as-is rather than being treated as "did not apply". This
    row-shape addition (an extra ``"universal"`` key, present ONLY on a
    universal provider's own rows) is the sole disclosure difference; a
    non-universal provider's row is byte-identical to before this existed.

    Returns the successful results in execution order, and the record rows
    sorted by ``(provider_id, repository_ref)`` — a different, and more
    reader-stable, order than execution itself.

    ``context.identities`` is this loop's ONLY source of identity (57B-81
    PR2 review cleanup): a caller that wants a different IdentityMap builds a
    new ``RunContext`` rather than passing a second, potentially-diverging
    one alongside it.
    """
    if context.identities is None:
        raise ValueError(
            "run_providers requires RunContext.identities to be resolved — "
            "build the context with an IdentityMap before running providers"
        )
    identities = context.identities
    results: list[CapabilityResult] = []
    rows: list[dict[str, Any]] = []
    targets = sorted(context.targets.repos, key=lambda repo: repo.repo_id)
    for target in targets:
        repository_ref = identities.reference_for(target.repo_id)
        for provider in registry.providers:
            matched = sorted({
                facet.profile_id for facet in target.facets
                if facet.profile_id in provider.profile_ids
            })
            universal = bool(getattr(provider, "universal", False))
            if not matched and not universal:
                continue
            recorder = RecordingToolAccess(inner=context.tool_access)
            run_context = dataclasses.replace(context, tool_access=recorder)
            row: dict[str, Any] = {
                "provider_id": provider.provider_id,
                "capability_id": provider.capability_id,
                "repository_ref": repository_ref,
                "matched_profiles": matched,
            }
            if universal:
                row["universal"] = True
            try:
                result = run_provider(provider, run_context, target)
            except Exception as exc:  # fail-soft: never sink the whole run
                # Provider-authored text, NOT identity-scrubbed: a provider
                # must never embed an internal repo_id in its own message.
                reason = f"{type(exc).__name__}: {exc}"[:_REASON_LIMIT]
                row.update(outcome="failed", reason=reason,
                          coverage=None, tools=list(recorder.log))
                rows.append(row)
                continue
            results.append(result)
            row.update(
                outcome="completed",
                reason="",
                coverage={
                    "applicability": result.coverage.applicability,
                    "status": result.coverage.status,
                    "reason_code": result.coverage.reason_code,
                },
                tools=list(recorder.log),
            )
            rows.append(row)
    rows.sort(key=lambda row: (row["provider_id"], row["repository_ref"]))
    return results, rows


def write_execution_record(
    run_dir: str | Path,
    *,
    rows: list[dict[str, Any]],
    network_authorized: bool,
    scan_date: str,
) -> Path:
    """Write the deterministic run-level provider-execution record.

    Byte-identical across repeated calls with the same inputs — including an
    empty selection, which still gets a stable, honest, present artifact
    rather than a missing one.
    """
    run = Path(run_dir).expanduser().resolve()
    out = run / FILENAME
    document = {
        "schema_version": SCHEMA_VERSION,
        "scan_date": scan_date,
        "network_authorized": bool(network_authorized),
        "executions": rows,
    }
    replace_artifact_text(
        out, sanitize_text(json.dumps(document, indent=2, sort_keys=True) + "\n"))
    return out


def run_provider_stage(
    run_dir: str | Path,
    spec: TargetSpec,
    identities: IdentityMap,
    *,
    scan_date: str,
    network_authorized: bool,
    provenance: dict[str, Any],
) -> dict[str, int]:
    """The one-call driver: select, execute, and record for this run only.

    Builds the same executor-backed tool access every other bundled path
    uses, runs every applicable (provider, repository) pair against the
    bundled registry, and writes both the run-level execution record and the
    evidence-catalog projection over whatever results came back.
    """
    run = Path(run_dir).expanduser().resolve()
    access = ExecutorToolAccess(
        spec, identities, run, scan_date, network_authorized=network_authorized)
    context = RunContext(
        targets=spec, output_dir=run, scan_date=scan_date,
        network_authorized=network_authorized, provenance=provenance,
        tool_access=access, identities=identities,
    )
    results, rows = run_providers(bundled_registry(), context)
    write_execution_record(
        run, rows=rows, network_authorized=network_authorized, scan_date=scan_date)
    catalog.write(run, results, identities)
    failed = sum(1 for row in rows if row["outcome"] == "failed")
    return {"executions": len(rows), "failed": failed}
