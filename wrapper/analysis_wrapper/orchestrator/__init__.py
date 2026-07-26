"""Orchestrator task contracts and validators (57B-113 / 57B-114, M0).

This subpackage is purely ADDITIVE: it defines the shapes and checks a later
orchestrator milestone will use to hand out and verify bounded units of work
(lens findings, module-formation proposals, section drafts, repair edits,
...). Nothing here is wired into the existing overview pipeline yet, and
nothing here changes the behavior of any existing module.

  - ``contracts``  — TaskPacket / TaskResult / LedgerRecord: the envelopes.
  - ``schemas``    — per-task-type OUTPUT shape checks (no I/O).
  - ``validators`` — evidence/prose checks (citations, numeric provenance,
                     forbidden vocabulary, relocation invariant, reading
                     budget, edit-op application).
  - ``rule_gate``  — the rule-to-gate coverage table: every bolded hard rule
                     in synthesis.md mapped to either a mechanical gate or an
                     honest "this stays a judgment call" tag.
"""

from __future__ import annotations
