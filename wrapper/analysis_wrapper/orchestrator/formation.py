"""Deterministic module-map.json writer (57B-113 / 57B-116, M2).

``planner.py`` composes the single global module-formation task as
task_type ``formation-proposal`` -- schemas.py's own module docstring notes
that this task's output already MIRRORS module-map.json's shape (``modules``
+ either ``candidate_rules`` or ``candidate_dispositions`` + optional
``additional_candidates``; see ``synthesis.md`` step 4). This module's ONE
job is mechanical: take the run's single validated ``formation-proposal``
output and materialize it at module-map.json's canonical path, stamped with
module_map.py's own ``MAP_SCHEMA_VERSION`` (never invented here).

No judgment happens in this module -- the formation-proposal task already
decided modules/dispositions/rules; ``write()`` only copies that decision to
disk. The existing ``finalize-module-map`` command (unchanged) then runs
``module_map.expand_candidate_rules``/``module_map.validate`` against the
file this module writes -- the zero-omission/zero-overlap gate stays
exactly where it was.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .. import module_map
from ..executor import replace_artifact_text
from ..sanitize import sanitize_text
from .results import validated_outputs

# The only top-level fields module-map.json's own contract recognizes
# (module_map.py's validate()/expand_candidate_rules()); anything else an
# executor's formation-proposal output happened to include is dropped here
# rather than silently carried into the canonical artifact.
_MODULE_MAP_FIELDS = ("modules", "candidate_rules", "candidate_dispositions",
                     "additional_candidates")
MAX_UNRESOLVED_RATIO = 0.25


class FormationWriterError(ValueError):
    """The run's ledger does not hold exactly one validated
    ``formation-proposal`` task. Fail closed -- there is no reasonable
    partial module-map.json to write instead."""


def _formation_output(run: Path) -> Mapping[str, Any]:
    outputs = validated_outputs(run, task_type="formation-proposal")
    if not outputs:
        raise FormationWriterError(
            "no validated formation-proposal task found -- run plan-judgment "
            "and its executor to completion before write-module-map")
    if len(outputs) > 1:
        raise FormationWriterError(
            "expected exactly one validated formation-proposal task, found "
            f"{len(outputs)}: {', '.join(sorted(outputs))}")
    return next(iter(outputs.values()))


def module_map_document(proposal: Mapping[str, Any]) -> dict:
    """The validated formation-proposal output, restricted to module-map.json's
    own recognized fields, with module_map.py's OWN ``MAP_SCHEMA_VERSION``
    stamped last -- so it always wins even if a stray same-named field
    somehow made it into the executor's output (schemas.py's
    formation-proposal validator does not reject unknown top-level keys)."""
    document = {key: proposal[key] for key in _MODULE_MAP_FIELDS if key in proposal}
    document["schema_version"] = module_map.MAP_SCHEMA_VERSION
    return document


def write(run_dir: str | Path, *, out: str | Path | None = None) -> Path:
    """Write module-map.json (or ``out``, when given, for inspection/testing)
    from the run's single validated formation-proposal task. Returns the
    path written."""
    run = Path(run_dir).expanduser().resolve()
    proposal = _formation_output(run)
    document = module_map_document(proposal)
    out_path = Path(out).expanduser().resolve() if out else run / "module-map.json"
    replace_artifact_text(out_path, sanitize_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n"))
    return out_path


def unresolved_rows(run_dir: str | Path) -> list[dict]:
    """The exact post-expansion candidates that need a targeted second pass."""
    run = Path(run_dir).expanduser().resolve()
    candidates, document = module_map.validate(run)
    by_id = {row["candidate_id"]: row for row in candidates.get("candidates", [])}
    return [{**by_id[row["candidate_id"]], "prior_reason": row.get("reason", "")}
            for row in document.get("candidate_dispositions", [])
            if row.get("disposition") == "unresolved" and row.get("candidate_id") in by_id]


def apply_boundary_resolution(run_dir: str | Path) -> bool:
    """Merge the one targeted resolution generation into module-map.json.

    The formation task may leave a reasoned remainder, but it cannot complete
    on that remainder alone: every unresolved candidate is presented to this
    independent pass exactly once.  The merge is mechanical and validates the
    map again immediately; no candidate/module ownership is guessed here.
    """
    run = Path(run_dir).expanduser().resolve()
    outputs = validated_outputs(run, task_type="boundary-resolution")
    if not outputs:
        return False
    if len(outputs) != 1:
        raise FormationWriterError("expected exactly one validated boundary-resolution task")
    output = next(iter(outputs.values()))
    before = unresolved_rows(run)
    expected_ids = {row["candidate_id"] for row in before}
    rows = output.get("dispositions") if isinstance(output, dict) else None
    if not isinstance(rows, list):
        raise FormationWriterError("boundary-resolution has no dispositions list")
    by_id = {row.get("candidate_id"): row for row in rows if isinstance(row, dict)}
    if set(by_id) != expected_ids or len(by_id) != len(rows):
        raise FormationWriterError("boundary-resolution must disposition every unresolved candidate exactly once")
    path = run / "module-map.json"
    document = json.loads(path.read_text("utf-8"))
    modules = list(document.get("modules", []))
    existing = {row.get("module_id") for row in modules if isinstance(row, dict)}
    for row in output.get("modules", []) if isinstance(output, dict) else []:
        module_id = row.get("module_id") if isinstance(row, dict) else None
        if module_id in existing:
            raise FormationWriterError(f"boundary-resolution redefines existing module {module_id!r}")
        modules.append(row)
        existing.add(module_id)
    disposition_rows = document.get("candidate_dispositions")
    if not isinstance(disposition_rows, list):
        raise FormationWriterError("module-map has no expanded candidate_dispositions")
    document["modules"] = modules
    document["candidate_dispositions"] = [
        dict(by_id[row["candidate_id"]]) if row.get("candidate_id") in by_id else row
        for row in disposition_rows]
    replace_artifact_text(path, sanitize_text(json.dumps(document, indent=2, sort_keys=True) + "\n"))
    module_map.validate(run)
    return True


def write_quality(run_dir: str | Path, *, refined: bool) -> Path:
    """Persist a deterministic formation-quality gate for final audit."""
    run = Path(run_dir).expanduser().resolve()
    candidates, document = module_map.validate(run)
    total = len(document.get("candidate_dispositions", []))
    unresolved = sum(1 for row in document.get("candidate_dispositions", [])
                     if row.get("disposition") == "unresolved")
    ratio = unresolved / total if total else 0.0
    payload = {
        "candidate_count": total,
        "unresolved_count": unresolved,
        "unresolved_ratio": round(ratio, 6),
        "refined": refined,
        "max_unresolved_ratio": MAX_UNRESOLVED_RATIO,
        "authoritative": refined or unresolved == 0,
        "status": "passed" if ratio <= MAX_UNRESOLVED_RATIO and (refined or unresolved == 0) else "partial",
    }
    path = run / "tasks" / "module-formation-quality.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_artifact_text(path, sanitize_text(json.dumps(payload, indent=2, sort_keys=True) + "\n"))
    return path
