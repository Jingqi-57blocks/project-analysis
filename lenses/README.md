# Lenses

Nine analysis lenses, executed as **three grouped agents** (grouping fixed by
the Phase-0 toolchain data — group A's tools are local and fast; B is the
history lane; C includes the network lane). Every agent reads `_shared.md`
first, then its lens files, then the bounded signal views.

| group | lenses | signals consumed |
|---|---|---|
| A — static structure | structure-inventory, complexity, dependencies-cycles, duplication, dead-code | scc, lizard, jscpd, jscpd-cross, dependency-cruiser, go-list, staticcheck |
| B — history & safety | hotspots-change-friction, safety-net | git-history (+ source tree for observed test evidence) |
| C — risk & open | dependency-risk, open-lens | osv-scanner, outdated (network lane; often skipped), all views |

Inputs per agent: `signals/*.view.txt` + manifests + `run-summary.json` +
`discovery-report.json` + `module_candidates.md`. Output per agent: findings
in the shared shape + a per-signal coverage statement. Nothing else.
