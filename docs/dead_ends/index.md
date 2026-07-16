# Dead ends

Refuted ideas with diagnosis — investigated seriously enough to learn
something, so the *reason* they didn't work is preserved and the ground isn't
re-tread. Parallel structure to `../backlog/` (conventions in `../README.md`:
when a dead end is created, its backlog entry is dropped and a line lands
here).

- [failure-detector](failure-detector.md) — observe-then-claim
  (heartbeat-staleness as the authoritative claim detector). REFUTED
  2026-06-24: claiming on the *weakest* detector admits a double-live window
  that sticky-poisons reuse; the claim gate needs *definitive* evidence (a
  record, a sound same-host probe, or a connection-oriented backend's lock) —
  the heartbeat ◊P stays the floor for *observation*, never the claim.
- [ensure-extend-pushorfail](ensure-extend-pushorfail.md) — `extend` as a
  blocking push-or-fail operator (`ensure` reduced to fixpoint iteration).
  REFUTED 2026-06-25: re-driving `killed` runs writes the divergent overlap
  the preempt/kill asymmetry exists to prevent (the asymmetry is a
  *protective invariant*, not a leak); "extend encapsulates liveness" is
  false through three shipped seams; and the two-valued advance-or-raise
  codomain cannot express `preempted`.
