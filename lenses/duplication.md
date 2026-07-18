# Lens: duplication (group A)

**Question:** what is copy-pasted, does it drift, and which copies actually
matter?

**Signals:** jscpd per-repo views, jscpd-cross per-language-family views
(cross-repo clones), lizard (corroborating identical complexity signatures).

Look for, with evidence:
- **Cross-repo clones** (jscpd-cross) — duplicated business logic across
  services is the expensive kind: a fix applied to one copy silently misses
  the others. Cite both sides of the clone pair.
- **Within-repo clone clusters** — many clones concentrated in one area
  usually mark a missing abstraction; a few scattered clones usually don't.
- **Drift risk ranking** — a clone in rarely-touched config beats a clone in
  a hot path (corroborate with the churn view; cite it when used).
- **Test-code duplication** is usually acceptable — do not report it unless
  the duplicated block encodes business rules.

Rules:
- Clone claims cite the jscpd view rows (file pairs + line ranges) and, when
  arguing impact, the source lines of at least one copy.
- **Attribute each clone to the module of its FULL path, never a shared
  basename.** Same-named files (a `service.go` or `index.js` living in several
  different packages/directories) are DIFFERENT modules — read the whole path of
  each cited file and name the module it actually sits in. A clone pair whose two
  copies fall in different modules is a cross-module finding: name both, and
  never fold it onto one module because the filenames match.
- jscpd is same-language only (Phase-0 verdict): silence about cross-language
  duplication is a coverage limit — state it, never imply "no duplication
  across languages".
- Percentages are per analyzed scope after exclusions; quote them with that
  qualifier.
