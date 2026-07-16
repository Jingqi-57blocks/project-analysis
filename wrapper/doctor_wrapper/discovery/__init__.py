"""Discovery producers (57B-11): inventory, stacks, PM identity, Tier-2
exclusions, provenance, integration candidates — everything that PRODUCES a
TargetSpec. The TargetSpec contract itself is owned by targetspec.py (57B-10);
the executor never re-derives targets.

Boundary: discovery is mechanical. It records evidence and emits candidates;
it never classifies activity, scores health, or interprets findings.
"""
