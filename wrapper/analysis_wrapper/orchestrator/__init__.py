"""Orchestrator task contracts, engine, and protocol (57B-113, M0 + M1).

This subpackage is purely ADDITIVE: it defines the shapes, the DAG runner,
and the executor protocol a later milestone will wire into the existing
overview pipeline to hand out and verify bounded units of work (lens
findings, module-formation proposals, section drafts, repair edits, ...).
Nothing here is wired into the existing overview pipeline yet (M2's job),
and nothing here changes the behavior of any existing module.

  - ``contracts``     — TaskPacket / TaskResult / LedgerRecord: the envelopes
                        (M0).
  - ``schemas``       — per-task-type OUTPUT shape checks, no I/O (M0).
  - ``validators``    — evidence/prose checks (citations, numeric
                        provenance, forbidden vocabulary, relocation
                        invariant, reading budget, edit-op application) (M0).
  - ``rule_gate``     — the rule-to-gate coverage table: every bolded hard
                        rule in synthesis.md mapped to either a mechanical
                        gate or an honest "this stays a judgment call" tag
                        (M0).
  - ``engine``        — the DAG runner + append-only JSONL ledger; the
                        executor protocol's ``next-task``/``submit-task``
                        verbs are thin CLI wrappers over this (M1).
  - ``composer``       — builds TaskPackets from (template, instructions,
                        inputs), redacting every input and sharding
                        deterministically when a packet would exceed its
                        context budget (M1).
  - ``executor_api``   — the bundled headless executor (network calls;
                        invoked explicitly via ``run-executor``, never
                        implicitly) (M1).
  - ``conformance``    — one fixture + golden output per task type, used to
                        conformance-test an executor/model end to end (M1).
"""

from __future__ import annotations
