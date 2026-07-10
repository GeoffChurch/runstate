# Launcher-record identity: a late-landing `terminated` can forge the live run's verdict

**Status:** open design thread, surfaced by the 2026-07-10 red-team (stage 3a of
the holistic review). Reproduced end-to-end on shipped machinery, twice
(`LocalLauncher` and `ThreadLauncher` flavors): `peek_terminal` — hence
`Watcher.poll`, `ensure`, `sweep` — reads `completed` (or `killed`) **for a run
that is alive and beaconing**.
**Severity:** wrong-verdict. `ensure` returns a truncated series *silently* on
the forged `completed`; a forged `killed` raises as a spurious failure.

## The seam

`launcher.launched` / `launcher.terminated` carry no episode identity, and
`peek_terminal`'s launcher tier pairs LATEST-terminated against LATEST-launched
(`_terminal_unless_followed`). The episode-boundary guard — *a terminal record
is void if a newer episode opener follows it* — assumes records land in episode
order. But a reap is a reader-side observation that can land **arbitrarily
late** relative to the episode it describes:

- **LocalLauncher (verified):** episode 1's child claims, stops cleanly, exits;
  a relaunch opens episode 2 (`launched` + `started` on the log, worker
  beaconing); THEN episode 1's reap writes its `terminated` — *after* ep2's
  opener, so no opener "follows" it and the verdict stands. Entirely shipped
  machinery: the reap discipline correctly keeps a *claimed* child's record
  (provenance); it just lands late. Killing ep1 instead forges `killed`.
- **ThreadLauncher (verified):** no reap discipline at all — a claim-race
  loser's runner writes `Terminated(exited, 0)` unconditionally; under
  concurrent dispatchers `ensure` returns a truncated series with no error.
  (Interim: the single-dispatcher caution in `../specs/memoizer.md`.)
- **Sibling:** lazy-launch's acknowledged "unclean loser" residue (pre-claim
  crash, rc ≠ 0, reap keeps the corpse) forges a spurious *failure* through the
  same late-landing shape.

## Candidate directions

1. **Envelope-`request_id` correlation (least invasive; no schema change).**
   `request_id` is the envelope's correlation field — the bodies stay frozen. A
   launcher stamps its `launched` and its `terminated` with one correlation id;
   `peek_terminal`'s launcher tier pairs a `terminated` to its OWN `launched`
   (by id) and voids the pair when a newer opener follows *the `launched`*, not
   the `terminated`. Late reaps then scope to the episode they describe. Cost:
   the launcher tier's fold pairs by id instead of two `latest` calls; id-less
   records (old logs, minimal launchers) fall back to the current rule.
2. **Reap-time scoping:** before writing `terminated`, the reaper checks
   whether a newer claim follows its own `launched`; if so, suppress (extending
   the foreign-claim-scoped skip beyond the never-claimed case) or write a
   scoped record. Cost: loses the manner-of-death provenance for the old
   episode — exactly what the current discipline deliberately keeps for
   claimed children.
3. **Body identity field** (`terminated.launched_seq` or similar): a launcher
   convention version bump (`additionalProperties: false`). Most explicit,
   heaviest.

Direction 1 looks canonical — correlation is precisely `request_id`'s stated
job, and the lift-rule already put it on the envelope for this reason. Weigh at
the basis-audit / simplification stages with the fold cost and the id-less
fallback in view.

## Related

- The `ensure` no-progress guard is now **claim-aware** (`live_episode` check;
  shipped with this thread's discovery) — that closes the *spurious-raise* half
  of the claim-window collision. This entry owns the *forged-verdict* half.
- `ensure`'s failure-outcome branch could get the same live-episode
  conditioning as an interim (a live episode alongside a failure record means
  the failure is stale or foreign); deliberately not applied pending this
  design.
- The residual in all variants: a **false-live** handle (pid reuse; unresolvable
  cross-host handles read as live) degrades the loud raise into `ensure`'s
  conservative wait — the same class as the documented cross-host claim-gate
  blindness (`index.md` "Cross-host liveness for the claim gate").
