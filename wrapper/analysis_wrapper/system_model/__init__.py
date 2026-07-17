"""Structured machine-readable system model (57B-31).

A THIN, DETERMINISTIC assembler that normalizes artifacts other producers
already emit — discovery signals (TargetSpec + discovery-report) and the 57B-30
call graph — into one canonical, versioned graph (``system-model.json``). It does
NOT parse source, run language analysis, infer business meaning, or cluster
graph communities into modules; those remain the job of the upstream OSS
analyzers and of synthesis. This layer is normalization glue only:

  schema     — versioned Node/Edge contract + SystemModel container
  ids        — deterministic stable-ID scheme + citation parsing
  builder    — idempotent node/edge accumulation + reference resolution
  from_*     — one normalizer per input family (discovery, callgraph, imports)
  coverage   — per-producer coverage (status, caps, source universe, unresolved)
  assemble   — orchestrator: read a run dir -> write system-model.json

Edge TYPES are kept distinct (language call edges never merged with protocol/
persistence/deployment/import/inferred-module-boundary edges), every node and
edge carries a stable ID + provenance + status, and any producer cap surfaces in
coverage — a capped partition is `partial`, a missing analyzer `unavailable`,
never an empty graph reported as clean.
"""

# NOTE: the ``assemble`` FUNCTION is intentionally NOT re-exported here — a bare
# ``assemble`` name would shadow the ``assemble`` submodule as a package
# attribute (``import ...system_model.assemble as m`` would then resolve to the
# function). Import it from the submodule: ``from ...assemble import assemble``.
from .assemble import write_system_model
from .schema import SCHEMA_VERSION, Edge, Node, SystemModel

__all__ = [
    "write_system_model", "SystemModel", "Node", "Edge", "SCHEMA_VERSION",
]
