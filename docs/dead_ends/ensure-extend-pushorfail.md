# Dead end: `extend` as a push-or-fail operator (`ensure` as fixpoint iteration)

**Status:** REFUTED 2026-06-25 by a three-angle red-team (code/mechanism / design-basis /
operational-soundness), each verified against the code (and against the mycooc consumer).
The *insight* survives; the *prescription* — invert the producer seam so `extend` blocks,
retries, and raises — was unsafe, under-simplifying, and broke the validating consumer. The
live work moves to the corrected minimal-predicate path (see *Where it goes instead*).
Recorded so it isn't re-attempted. The full original proposal is in git history (was
`../backlog/ensure-extend-pushorfail.md`).

## What it proposed

Strengthen the producer seam's `extend(until)` into a **push-or-fail** operator: drive the
run toward `until`, retry recoverable deaths *internally* (relaunch + resume), and **raise**
(carrying a reason) only when it cannot advance (no-progress / fatal / veto). Reduce `ensure`
to a trivial iterate-to-goal loop (`while not satisfied(until): extend(until)`). Move *all*
liveness below `extend` into the producer; a shared helper carries the generic
retry-until-progress; the producer supplies a one-line recoverability judgment; the worker
keeps atomic-save + an `error` bit. Headline thesis: **`extend` encapsulates liveness** —
above it, pure progress→goal iteration; below it, all liveness (the reactive-stream face made
precise; `ensure` as Kleene fixpoint iteration of a monotone inflationary operator).

## Why it died

1. **Re-driving *killed* runs reopens the sticky reuse-poisoning that killed
   `failure-detector.md` — by a sequential route this time, and the design's motivating
   "leak" is in fact the *protective invariant*.** A cooperative `preempted` checkpoints on
   stop → checkpoint == frontier → resume re-emits nothing. A hard `killed` cannot checkpoint
   on the way out → checkpoint sits *behind* the last-emitted step → resume re-runs that gap
   and **re-emits it**; if the recompute is non-deterministic (different host/BLAS/unseeded
   dropout), `history()` raises `divergent re-emission … reuse would be unsound`
   **permanently and stickily** on the append-only log (every future `ensure`/`history` for
   that `run_id` then raises forever, at any goal). Today's `killed`→raise is exactly what
   prevents that write. So the preempt/kill asymmetry the design calls an embarrassing leak
   is load-bearing: *clean-final-checkpoint ⟺ no-overlap ⟺ safe-to-redrive*. This is the same
   irreversible hazard as `failure-detector.md` §"Why it died" #1, reached by sequential
   resume instead of concurrent double-live. Atomic-save does not help (that guards *torn*
   files; this is *stale* checkpoint + *non-deterministic recompute*). **Crucially this binds
   the feature, not just the shape:** any design that re-drives `killed` writes the overlap.
   (Operational red-team repro'd it against the real `Worker`/`history`.)

2. **"`extend` encapsulates liveness" is false — liveness leaks back up through ≥3 shipped
   seams.** (a) The **foreign-episode handle**: own-vs-foreign-spawn *is* liveness, carried
   today by the handle *type*; dissolving the handle loses it and re-buries the
   recordless-winner hang that `specs/store.md` Recipe-2 was written to kill (a blocking
   `extend` gated behind a foreign winner cannot observe the winner's recordless death). (b)
   The **give-up reason string** the design surfaces *above* `extend`. (c) The
   **going-vs-wedged** check the orchestrator runs *concurrently* with `ensure` (mycooc's
   runner-as-worker drains `control.stop` during the drive). The headline justification is
   decorative, and where treated as load-bearing (the blocking contract) it reintroduces a
   documented deadlock.

3. **It does not net-simplify — relocation with a multiplier.** `ensure` sheds ~23 lines, but
   a new shared helper + a stronger `extend` contract on *every* producer + a new seam method
   appear. This is the exact test that sank `failure-detector.md` (§"Why it died" #6). The
   monotone/fixpoint vocabulary is true but orthogonal to cost — `ensure`'s current loop is
   *already* fixpoint iteration.

4. **"advance-or-raise" is the wrong codomain — a missing basis vector.** A two-valued
   {advanced, ⊥} cannot express "stopped cleanly on budget, resume later but *not now*" — the
   `preempted` outcome (the entire reason `ensure` re-drives) collapses into advanced-or-fail.
   The design's own two-axis consumer (progress *and* wall-clock budget) needs the third
   value. A *blocking* `extend` also destroys the cooperative-stop yield point the consumer
   depends on (it coarsens `control.stop` to whole-`extend` granularity).

5. **It breaks the validating consumer.** mycooc's orchestrator is built on `ensure`
   **raising** as a "quiescent yield, leave for a later invocation to resume" signal (it
   string-matches `"no progress"`); push-or-fail buries that retry inside `extend`. The
   migration scope (open-Q #8) named only the toy `_LaunchProducer` and missed the consumer
   entirely.

## The root error (one line)

**It tried to *erase* the preempt/kill asymmetry as a "leak," but the asymmetry is a
*protective invariant* (clean-final-checkpoint ⟺ no-overlap ⟺ safe-to-redrive) — so erasing
it writes the very divergent re-emission that irreversibly poisons reuse — and it relocated
liveness into a blocking `extend` it claimed "encapsulates" liveness, when liveness
demonstrably leaks back out through the handle, the reason, and the yield point.**

## What survives (genuine — keep)

- **The diagnosis is correct.** Routine recoverable terminations are real; the same scheduler
  that cleanly preempts (re-driven today) also SIGKILLs at the same budget (raises today), and
  mycooc hand-rolls the workaround (`_SyncHandle` synthesizes a `preempted`-shaped terminal;
  `_run_one_chunk` runs an `on_killed=RESUME` loop). That asymmetry is worth *narrowing* — but
  by making killed-redrive *safe*, not by erasing the distinction.
- **Recoverability is the producer's/worker's knowledge — and it is *canonically derivable*,
  not a workload opinion.** Over `Outcome` + the worker's *self-reported* `error` + progress:
  `ERRORED` (worker self-diagnosed fatal) → not recoverable; `PREEMPTED`/`KILLED`/
  `PRESUMED_DEAD` (no self-diagnosis) → recoverable iff progress. So the fix is a library
  *function*, with an *optional* producer override for genuine error-string classification
  (CUDA-OOM-transient vs NaN-fatal) — not a fat predicate with a compat default.
- **De-opinioning `ensure`'s `_FAILURES` outcome-taxonomy branch** is the real basis
  improvement (the `preempted`-vs-`killed` branch *is* the opinion leak).
- **The monotone-frontier framing** is sound for the own-spawn, single-producer, step-axis
  case — the bulk of real usage.

## Where it goes instead

The minimal-predicate path in `../backlog/ensure-redrive-recoverable-terminations.md`: keep
`ensure` owning its loop, the no-progress guard, and the foreign-episode wait; replace only
the `result.outcome in _FAILURES → raise` branch with the canonical recoverability function;
**gate each outcome-tier on its own safety prerequisite** —

- `PREEMPTED` → re-drive **now** (already done; no overlap),
- `KILLED` → re-drive **once the value plane is non-poisoning** (G1: a non-sticky /
  episode-scoped `history` that prefers the resumed episode's value on an overlapping step
  rather than raising forever),
- `PRESUMED_DEAD` → re-drive **once the cross-host claim oracle lands** (re-driving a
  maybe-alive run is the *concurrent* double-live route),
- `ERRORED` → never (worker self-diagnosed fatal);

bound by an explicit `max_attempts`; with thrash made observable for raw-subprocess producers.

**The keystone.** G1 — value-plane robustness against sticky divergent re-emission — is the
*same* prerequisite the cross-host claim gate needs (index.md, "Cross-host liveness"). **Two
consecutive elegant reframes have now died on this one rock.** That makes the value-plane
robustness the real next dig: it is the gate for an entire class of redrive/claim features,
not a failure-detector footnote.
