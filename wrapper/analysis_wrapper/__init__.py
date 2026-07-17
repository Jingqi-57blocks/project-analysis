"""analysis_wrapper — the Project Analysis tool-execution wrapper (Linear 57B-10).

One component, separate concerns:
  status     — the authoritative signal-status contract and aggregation
  sanitize   — redaction + bounded views (the single redaction implementation)
  targetspec — the TargetSpec contract (defined here; produced by discovery, 57B-11)
  manifest   — revision-anchored per-signal manifests
  tooldefs   — data-driven tool definitions (plain data, not a plugin system)
  executor   — safe subprocess execution, classification, immutability check

Boundary rule (canonical, plan §2.7): this package invokes allowlisted tools,
applies safe arguments, redacts, bounds output, and records manifests. It never
interprets findings, validates documents, scores report quality, or decides
whether a report passes.
"""

__version__ = "0.1.0"
