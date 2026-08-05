# A record names the claim it answers

**Status:** harm measured, **consumer census owed**. The surviving half of
`../dead_ends/per-episode-loglets.md`. Not to be built alongside the designated-eliminator thread —
they overlap and should be decided together.

## The harm, reproduced

A displaced worker's record wins a cell it should not, because every read-side attribution in the
value and verdict planes is **positional**, and position misattributes a late write.

Episode 1's worker A is displaced by episode 2's worker B. B writes `(loss, 41) = 0.73`. A, still
running and unaware, writes `(loss, 41) = 0.50` **later in wall clock**. Measured on memory *and*
sqlite:

```
value_series -> {40: 0.1, 41: 0.5}     history -> [0.1, 0.5]     # A, the DEAD episode, wins
```

It is not cosmetic. The same displacement forges the run's verdict —
`peek_terminal -> RunResult(outcome=COMPLETED, final_step=1)` while B is still running — and then:

```
ensure(until={"step": 100}) on a run whose live episode reached step 2
  -> 3 points returned with NO re-drive and NO error: [0.0, 0.1, 0.2]
```

Silent truncation through the reuse path, which is the case the whole memoizer exists to serve.

## Why the obvious read-side fix fails

"Resolve by *episode* rather than by global `seq`, purely read-side" was proposed and **measured
broken**: read-side, the only way to attribute a record to an episode is *positionally*, and A's late
write sits at a `seq` above B's claim. Position says it belongs to episode 2. Still broken.

That is the whole point — the information needed is not on the log.

## The fix

**Stamp the record with the writer's own claim `seq`**, which the worker already holds
(`Worker._started_seq`), and resolve read-side by `(stamped episode, seq)`. Measured: the cell
resolves to B's `0.73`. Zero substrate change, zero reordering, no new primitive.

The repo already ships this pattern one tier up. `observables.py`'s launcher-death correlation, whose
docstring is the argument:

> the only death that can speak for the run is the death of *the launch that claim answered* — found
> by correlation id. **Position cannot do this job**: a reap is a reader-side observation that lands
> arbitrarily late … Both forgeries die by construction here.

`../specs/launcher-record-identity.md` made the move once, for the launcher tier. This extends it to
the lifecycle and value tiers. `cross-host-claim-gate.md` §8.2 is already heading the same way:
*"Stop trying to prove authority; record aim instead."*

## What it costs, and what it does not fix

- **A schema bump.** `lifecycle-v0.4` → `v0.5` and `value-v0.2` → `v0.3`; both are
  `additionalProperties: false`, so the field cannot be added silently.
- **It reverses the soft half of a non-goal.** `../specs/run-episodes.md` declines *"explicit
  episode-ids"* — but the recorded reason is a **consumer census**, not a principle: *"a future
  refinement **if** provenance/correlation ever needs them; **no scoped consumer does**."* That
  census is now stale in one direction (a harm exists) and unmeasured in the other. The *other* half
  of that non-goal — a "done/sealed" marker — is principled and stays declined.
- **It does not reach the artifact plane.** Per `../specs/write-authority.md`, this makes the log
  stop lying; the checkpoint a displaced worker wrote is still on disk, and that is where the real
  damage lands.

## What is owed before building

1. **The census.** `CLAUDE.md`'s *"count before designing for a consumer"* applies. A harm has been
   measured; a *frequency* has not. `claim-eviction.md` was deferred on exactly this bar — a
   corpus scan over 1,933 logs — and this proposal has had one hand-run repro. Run the same scan:
   how many real logs contain a `(name, step)` cell won by a record from a superseded episode?
2. **Decide it against the eliminator, not beside it.** Both are answers to "a third party or a
   displaced worker says something it should not." Two mechanisms for one problem is what
   `protocol-algebra.md` L2's minimality rule exists to prevent.
3. **Which planes.** The value plane is where the harm was measured. The verdict plane gets fixed by
   the same stamp and is arguably more important (it is what truncates `ensure`). The beacon plane
   would too. Scope deliberately rather than stamping everything.
