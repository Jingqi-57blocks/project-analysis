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
