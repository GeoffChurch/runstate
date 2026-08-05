# A record names the claim it answers

**Status:** harm measured and reproduced; **soundness unsettled** — it must answer a prior
refutation (below). The surviving half of
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

## Prior art — attribution was proposed and refuted, and this must answer it

`value-plane-divergence-resolution.md` records: *"An earlier draft proposed **attribution + an
'authoritative attempt' default**; a code-grounded red-team refuted it — it added machinery to
resolve **a case that cannot arise**."* And in its rejected-alternatives list: *"Attribution + a
fork-surface (tag each value with its attempt) … Defer until one does (YAGNI). **Take-the-latest
needs no attribution.**"*

That is this proposal, refuted. **The reason it may not reach this case** — and the burden is on
this entry to show it, not to assume it:

> on atomic-CAS backends (memory, single-host sqlite) the worker birth-CAS **muzzles the
> double-spawn loser**, so no value-emitting double-live occurs; it can only occur on NFS.

The refutation reasons about a **double-spawn loser** — a worker that *lost* the birth CAS, whose
`_lost` is True and which therefore writes nothing. Correct about that case. The harm measured above
is a **displaced** worker: it *won* its claim legitimately and was displaced afterwards, so `_lost`
stays False and it keeps writing. That is #32, filed after this refutation was written.

So the two are about different routes to the same symptom — but "different route" is exactly the kind
of technicality that rescues a bad idea, and it should be attacked as such rather than accepted.

## What it costs, and what it does not fix

- **A schema bump.** `lifecycle-v0.4` → `v0.5` and `value-v0.2` → `v0.3`; both are
  `additionalProperties: false`, so the field cannot be added silently.
- **It reverses the soft half of a non-goal.** `../specs/run-episodes.md` declines *"explicit
  episode-ids"* — but the recorded reason is a **consumer census**, not a principle: *"a future
  refinement **if** provenance/correlation ever needs them; **no scoped consumer does**."* That
  census is now stale in one direction (a harm exists) and unmeasured in the other. The *other* half
  of that non-goal — a "done/sealed" marker — is principled and stays declined.
- **Old logs cannot be fixed, only new ones.** Recorded objection: stamping a claim epoch onto
  existing records *"can only be derived positionally — i.e. from exactly the inference the field
  exists to replace — so migrated values are a guess precisely in the case the field is for."* A
  **correctness** objection, not a cost one: whatever the fix is worth, it is worth it only
  forward.
- **It does not reach the artifact plane.** Per `../specs/write-authority.md`, this makes the log
  stop lying; the checkpoint a displaced worker wrote is still on disk, and that is where the real
  damage lands.

## What is owed before building

1. **Answer the prior refutation** (above). This is the soundness question and it comes first: is
   "displaced worker ≠ double-spawn loser" a real distinction, or a technicality rescuing an idea a
   code-grounded red-team already killed? No count settles this.
2. **Measure the cost, not the incidence.** An incident census is the wrong instrument here
   (`../../CLAUDE.md`: *a census bounds applicability and cost — never soundness*), and it is biased
   low in exact proportion to the consumer machinery already written to avoid the problem. The
   informative measure is **how many lines of consumer code exist solely to route around this gap,
   and which gap each routes around** — `reclaim_experiment.py` (361), `correlation_refusal`,
   `repair_malformed_stopped.py`, `_SyncHandle`, the claim-guard suite. That is a direct read on what
   the gap costs, immune to the mitigation bias, and it points the opposite way from an incident
   count.
3. **Decide it against the eliminator, not beside it.** Both are answers to "a third party or a
   displaced worker says something it should not." Two mechanisms for one problem is what
   `protocol-algebra.md` L2's minimality rule exists to prevent.
4. **Which planes.** The value plane is where the harm was measured. The verdict plane gets fixed by
   the same stamp and is arguably more important (it is what truncates `ensure`). The beacon plane
   would too. Scope deliberately rather than stamping everything.
