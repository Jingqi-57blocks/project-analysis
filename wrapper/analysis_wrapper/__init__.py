"""analysis_wrapper — the Project Analysis tool-execution wrapper (Linear 57B-10).

One component, separate concerns:
  status     — the authoritative signal-status contract and aggregation
  sanitize   — redaction + bounded views (the single redaction implementation)
  targetspec — the TargetSpec contract (defined here; produced by discovery, 57B-11)
  manifest   — revision-anchored per-signal manifests
  tooldefs   — data-driven tool definitions (plain data, not a plugin system)
  executor   — safe subprocess execution, classification, immutability check
  profiles   — explicit bundled profile/provider contracts (not dynamic plugins)
  run_provenance — simple per-run version/options/tool record (not a cache key)
  identity   — per-run internal ID to human-readable name/reference mapping

Boundary rule: this package invokes allowlisted tools, applies safe arguments,
redacts, bounds output, records manifests, and validates STRUCTURED stage
contracts. It never interprets business prose, scores diagnostic quality, or
decides whether a diagnosis is correct.
"""

__version__ = "0.4.0"
