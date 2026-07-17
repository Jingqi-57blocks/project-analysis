"""Dependency-map stage — produce per-repo import maps under ``<run>/imports/``.

Mirrors the call-graph stage's shape (:mod:`analysis_wrapper.callgraph`): a lane
per repo writes ONE machine-readable dependency map into the run dir, which the
system-model assembler later normalizes into ``dependency`` edges (kept strictly
separate from ``call`` edges). JS/TS repos use the analyzer-owned
dependency-cruiser lane; Go repos use ``go list -deps -json``. No new parser or
graph engine — this stage is thin glue over the existing tools.
"""
