# `lifecycle.stopped` does not name the claim it eliminates

**Status:** the defect is real, measured, and **narrower than the first draft claimed**. That draft
proposed stamping *every* record — value plane included — and was refuted: the value-plane half sits
inside the declared consistency model. What survives is one record, two folds, and a hard sequencing
constraint. **Gated on `claim-eviction.md`** — not merely overlapping with it.

## The defect

`observables._episode_stopped` and `live_episode` pair a `lifecycle.stopped` to a claim
**positionally** — the terminal that follows the latest claim is taken to be *that claim's*
terminal. Nothing declares this rule, and `write-authority.md` revision 4 withdrew the invariant it
depends on (*"single-writer holds at the claiming instant only"*). The lifecycle tier was never
revisited.

Its sibling one tier up already does the right thing. `_launcher_terminal` is claim-anchored by
correlation id, and its docstring is the argument:

> the only death that can speak for the run is the death of *the launch that claim answered* — found
> by correlation id. **Position cannot do this job**: a reap is a reader-side observation that lands
> arbitrarily late … Both forgeries die by construction here.

`../specs/launcher-record-identity.md` made this move once, for the launcher tier. This is the same
defect, one tier down.

## Two harms, both measured

**1. A forged verdict truncates `ensure` silently.** A displaced worker's honest `stopped` lands
after the successor's claim, so `peek_terminal` reads it as the successor's terminal:

```
peek_terminal -> RunResult(outcome=COMPLETED, final_step=1)   # while the successor is still running
ensure(until={"step": 100}) -> 3 points, NO re-drive, NO error: [0.0, 0.1, 0.2]
```

**2. The claim plane cascades — the severe one, and the first draft missed it entirely.** From
**one** third-party forged `stopped`, the displaced worker's own honest dying breath releases the
*live successor's* claim, and it does not stop there:

```
A claimed: True
(one forged stopped)
B claimed: True                    <- legitimate successor
A.stopped()                        <- A's OWN honest dying breath
  -> live_episode = None           <- B's claim RELEASED
C claimed: True                    <- B and C now both live, from one forgery
```

This is the outcome `claim-eviction.md` and mycooc's own ruling both name as the worst available:
*"a false positive admits a second writer onto a live run, which is worse than the stranded claim it
would fix."* It is a bypass of the single-spawn guard, not a resolution policy.

**Reachability:** displacement is unreachable through the public API today — a second `Worker` loses
while the first lives, and only `Worker` writes `lifecycle.stopped` in the whole library. It requires
an out-of-library `stopped` (which mycooc's reclaim tool writes), NFS, or a false-dead handle probe.

## The fix

`lifecycle.stopped` carries `claim_seq`; `_episode_stopped` and `live_episode` attribute by aim
rather than by position. Prototyped: 4 files, +18/−11 lines. The verdict and claim planes both come
out correct, and `ensure` re-drives as it should.

**Required, not optional.** `lifecycle-v0.4` → **v0.5** with `claim_seq` non-null, so an unaimed
`stopped` is loudly malformed rather than silently inert. An `if stamped: … else: positional` branch
would be worse than useless: it makes an *unstamped* record strictly more powerful than a stamped one
(it would release any claim, forever) and makes forgery easier by omission.

**Migration has a precedent that shipped.** `launcher-record-identity.md` did exactly this backfill —
a one-time offline pass stamping synthetic ids positionally, *"applied ONCE, over a whole quiescent
log rather than live against a moving one, which is what made it wrong"*. The half that does **not**
transfer: launcher ambiguity is detectable, a forged `stopped` is not (*"byte-indistinguishable from
a real one"*), so there is no report-the-ambiguous pass. Already-broken logs stay broken.

## The sequencing constraint — this is the gate

**It cannot ship before the eviction thread.** Under the aim rule, a third party's unaimed
`lifecycle.stopped` no longer releases anything — and that is today's *only* claim-release
mechanism. `ensure` has no hang timeout. Measured against the prototype:

```
tests/test_memoizer.py -- HANGS (60s timeout, exit 143)
  first hang: test_ensure_redrives_when_extend_noops_onto_a_live_episode
Rest of suite: 48 failures across 3 files (baseline 810 passed / 0 failed)
```

The failure mode is a **silent infinite hang**, not a loud error. So this and a legal, aimed release
must land in the same window.

Neither subsumes the other: eviction fixes *the third party's over-assertion*; this fixes *the
displaced worker's own honest record being misattributed*. Eviction does not remove displacement — a
cross-host claim probes `None`, not `True`, so the veto does not fire — and the displaced worker's
`stopped` still forges the verdict afterwards.

**The L2-minimal statement covering both, which neither entry had:** *every record that eliminates a
claim must name the claim it eliminates* — `stopped` (first-party) and `evicted` (third-party) alike,
both carrying `claim_seq`. That is `cross-host-claim-gate.md` §8.2's *"stop trying to prove
authority; record aim instead"*, applied uniformly.

## What was dropped, and why

**The value-plane stamp — dropped.** A displaced worker winning a `(name, step)` cell is **inside the
declared model**. `value_series`'s own docstring: *"a caller can receive a series no single execution
produced. This is a **convergent merge** (last-write-wins per cell), not a consistent snapshot."*
And `write-authority.md`: *"resolved, not corrupt … a declared cost."* Asking for a different winner
is a request to change the consistency model, which is a much larger argument than a field.

The prior refutation of attribution also stands against it, and the first draft's defence answered
the wrong objection: the *"birth-CAS muzzles the double-spawn loser"* line sits under **"a raise on
double-live"**, while the **attribution** bullet's reason is *"a forensic affordance no consumer has
asked for. Defer until one does (YAGNI). Take-the-latest needs no attribution."*

**Post-terminal writes — fixed separately and cheaply.** A worker that had written its dying breath
could still `emit`, landing above the successor's claim. Two guard clauses (`emit`/`tick` latch on
`_stopped`, as `stopped()` already did), no field, no schema bump. Shipped on its own branch.

## Owed

- **Amend `value-plane-divergence-resolution.md`.** Its soundness premise — *"Episodes are sequential
  (single-writer-per-run), so the continuing branch owns the highest `seq` at every overlapped step"* —
  is falsified by displacement, and its out-of-contract residual list enumerates only
  `relaunch_if_needed`-on-completed and NFS, missing the forged-`stopped` route. The conclusion
  survives (the route needs an out-of-contract write); the enumeration does not. Documentation fix.
- **The cost measure, if one is wanted, is workarounds not incidents** (`../../CLAUDE.md`: *a census
  bounds applicability and cost — never soundness*).
