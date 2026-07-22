"""Function/method call-graph extraction (57B-30 production lane).

Two OSS-backed lanes turn a TargetSpec into function/method call edges:

- ``go_lane``  — golang.org/x/tools/cmd/callgraph (VTA), a pure CLI adapter.
- ``js_lane``  — the pinned TypeScript compiler driven by a thin analyzer-owned
  node extractor.

``contract`` is the single source of truth for the edge + coverage shape;
``sources`` is the single source of truth for the production-source boundary;
``emit`` selects a lane per repository and writes
``callgraph/<artifact_key>.jsonl`` plus a
``callgraph-coverage.json``. Import/package edges (dependency-cruiser, ``go
list``) stay SEPARATE — this lane never relabels them, and cross-repo protocol
joins are boundary edges elsewhere (57B-31), never call edges here.
"""
