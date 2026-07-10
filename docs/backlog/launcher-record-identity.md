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

## Second red-team pass (2026-07-10) — amendments

1. **The live-guard must be log-ordered, never probe-based.** A `resolve()`-based
   "definitively live" check inverts the failure: pid reuse makes a DEAD run read
   live, VOIDING a *true* `killed` verdict — `--status` lies indefinitely and
   `ensure`'s foreign gate (`is_alive()` re-reads the same false-live) waits
   forever: a wedged sweep, operationally worse than the forgery. The sound rule
   is a **pure fold**: a `terminated` is void if any worker-authored record
   (`lifecycle.*`, `value`) FOLLOWS it by seq. No probe, lives in `peek_terminal`
   itself (dissolving the Watcher-vs-ensure home question), self-heals within one
   beacon; `EpisodeProbe` (connection-bound, genuinely definitive) stays
   admissible where present; bare `resolve()` never voids.
2. **Correlation alone does not close the live variants.** A ThreadLauncher
   claim-loser's pair is internally clean — its own `launched` is the newest
   opener, so id-pairing still reads it as terminal. The void rule (amendment 1)
   is load-bearing for all three reproduced live-forgery variants; correlation is
   what fixes **dead-log attribution** (post-hoc, nothing alive to write records —
   only correlation carries which episode a manner-of-death belongs to). Two
   halves, both required.
3. **No id-less dual path.** Old logs can be migrated by a one-time offline pass
   stamping synthetic correlation ids via today's positional pairing — replayed
   once, no worse than current behavior for old data — so the runtime keeps ONE
   fold (the no-compat doctrine holds).
4. **ThreadLauncher loser-suppression** (retires memoizer.md's single-dispatcher
   caution): handle identity can't work (sibling threads share `local://host/pid`);
   the portable rule is LocalLauncher's lifted — clean exit + `live_episode(ch)
   is not None` → suppress the `Terminated` write (a foreign claim explains the
   silence).
5. **Priority raised: translation is exposed today.** Its concurrent `drive_block`
   shells over ThreadLauncher share rids by design; a forged truncated result
   would be stored under a content-addressed rid as permanent, silent cache
   corruption. The interim mitigations protect mycooc (serial runner) but not this
   topology.

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
