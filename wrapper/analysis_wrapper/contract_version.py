"""The one artifact-contract version, imported everywhere (57B-113 / 57B-118, M4).

Every producer of a canonical run artifact previously defined its OWN
``SCHEMA_VERSION = "2.0.0"`` literal, independently. That is exactly the
pattern this workstream kept finding drift in elsewhere (citation grammars,
comparator row keys, dedup id universes) — a value that MUST agree everywhere
but is spelled out in N places, one edit away from disagreeing. One producer
forgetting to bump its own literal on a contract change would silently write
a mismatched artifact that `overview_audit.py`'s own
``artifact-contract-versions`` check exists to catch — after the fact, per
run, instead of never happening.

``CONTRACT_VERSION`` is the SOLE source. M4 is a deliberate, one-time,
explicitly-announced break (no backward compatibility; a run under the old
contract is re-run, never migrated) — see 57B-118. Every module that used to
carry its own ``SCHEMA_VERSION`` literal now imports this one instead; nothing
about their own artifact SHAPE changed as part of this bump.
"""

CONTRACT_VERSION = "3.0.0"
