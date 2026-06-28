# `ensure(await_complete=True)`: gate on the producer's completion, not the window

**Status:** forward-looking idea, surfaced by the translation dogfood (a
cross-pollination review of a runstate consumer). Designed-and-ready, **not
urgent** — see *Use-case evidence*.
**Basis:** builds directly on `../specs/preempted-vs-completed.md` (`ensure`
already returns on `outcome == completed`) and `../specs/ensure-until-condition.md`
(the *condition ⟂ enforcement ⟂ resume-policy* split, and the "**no new
`Bound`/`Target` type**" rule that sinks the contrast case). Sibling to
`ensure-redrive-recoverable-terminations.md` — both refine `ensure`'s
terminal-outcome handling, on orthogonal knobs (that one re-drives the *failure*
tier; this one re-targets the *return* predicate).

## The gap

`ensure`'s `until` does **double duty**: it is the **drive-target**
(`producer.extend(until)`, `memoizer.py:239`) *and* the **return-predicate**
(`_satisfied(channel, until)`, `memoizer.py:237`). Window-close as a
return-predicate is a **proxy** for "the producer is done" — and the proxy is
wrong exactly when the producer has work sequenced *after* the gating step:

- an **off-channel side effect** — the producer writes a bulk artifact to a
  store the channel can't see, *after* its last step. The step window closes,
  `ensure` returns, the consumer reads the artifact — and races the write.
- a **cross-step artifact** — an artifact accumulated *across* steps (so it
  cannot be written before the last step by construction), durable only at the
  producer's intrinsic end.

The consumer wants to gate on **"the producer is done,"** but the only
first-class affordance is a step (or time) window, which is a different thing.

## The distinction (already latent in the design)

`preempted-vs-completed.md` (Change A, shipped) already made
`outcome == "completed"` a **sufficient early-return** for `ensure`: it returns
when **window-closed OR the producer declared `completed`** (`memoizer.py:233-234,
258-259`). The verdict plane (`peek_terminal → Outcome.COMPLETED`) is already
computed and already consulted.

This proposal makes completion the **sole** return-trigger, under an opt-in flag:
demote window-close to **drive-target-only**, and return **iff** the producer
completes. It removes one disjunct from the return-predicate; it changes nothing
about how the producer is driven.

## The change — `await_complete`

`ensure` gains `await_complete: bool = False`. The drive loop's
return-predicate becomes:

```python
def reached(ch):
    # `until` still DRIVES the producer (extend); the RETURN predicate is
    # window-close OR worker-declared completion. await_complete keeps `until`
    # as the drive-target ONLY — completion is then the sole return trigger.
    result = peek_terminal(ch)
    if result is not None and result.outcome == Outcome.COMPLETED:
        return True
    if await_complete:
        if result is not None and result.outcome == Outcome.PREEMPTED:
            raise RuntimeError(                       # fail loud (see contract)
                f"run {producer.run_id!r} stopped without completing (preempted); "
                f"a producer that never claims completed would re-drive forever")
        return False
    return _satisfied(ch, until, clock=clock)
```

The three `_satisfied(...)` return-checks in `ensure` become `reached(...)`; the
existing separate COMPLETED early-returns fold into it; the own-spawn
no-progress guard stays gated on `not await_complete` (its window-progress
signal is meaningless when the target is completion). **~8 lines, no wire
change.** An `ensure_terminal(producer, name, *, drive)` one-liner wrapping
`ensure(..., until=drive, await_complete=True)` is optional sugar — left to taste
(cf. `completed-opt-in.md`'s "no `w.completed()` sugar" non-goal).

**Worker contract (load-bearing).** `await_complete` is for a producer whose
drive-target is its **whole job** and that claims `completed=True` at its
intrinsic end (a fixed-length or converging worker). Per `completed-opt-in.md`,
reaching an *exogenous* bound defaults to `preempted` — so a **resumable chunk**
driven under `await_complete` would (correctly) raise, never return: a chunk
isn't "done." `until` in this mode is purely the launch/enforcement parameter
(the default `_LaunchProducer` still needs `{"step": N}` to size the worker); the
*return* is the verdict. The `preempted → raise` is the repo's **fail-loud**
discipline (`completed-opt-in.md`): a forgotten `completed=True` surfaces, it
does not livelock (the analogue of `_reject_count`'s un-driven-axis rule).

**Non-driving degenerate.** A consumer that did *not* launch the run just blocks
on `Watcher.wait` / `peek_terminal` (already available). `await_complete` is the
**driving** case — folding the completion-gate into `ensure`'s
read-first/produce-on-miss memoization, which "launch, then `Watcher.wait`" loses.

## Why it closes the race (and the one invariant it still needs)

A completion gate is correct exactly when the producer **flushes its artifact
before emitting `stopped(completed=True)`**. That invariant is the *natural* one
— it holds for anything that writes in the normal course of the worker body,
before the `with Worker` block exits (`__exit__` emits the terminal) — whereas
"write before gating-step N" is fragile and, for a cross-step artifact,
**unsatisfiable**. The gate trades a fragile invariant for one that holds by
default, and uniquely covers the cross-step case that reordering cannot.

The only fix needing **no** producer-ordering invariant at all is putting the
artifact's *existence* on the channel — a logged pointer the consumer waits on
(`../specs/store.md`, the cell-pointer recipe). That is the categorically
complete end-state; it is heavier (emit + wait on a pointer) and is the deeper
alternative below.

## Alternative considered — the predicate branch (rejected)

Add `{"completed": true}` as a term in the subscription condition-algebra, so a
consumer writes `until={"completed": true}`. **Rejected**, because `ensure`'s
`until` *is* the wire `Condition` (`ensure-until-condition.md`: "**no new
`Bound`/`Target` type — reuse the Condition dict verbatim**"), so the term
propagates into `control.subscribe` and thence to the **worker**, which:

- **cannot evaluate it** — subscriptions are serviced only on the live loop
  (`worker._service → sub.tick`); a worker cannot fire a subscription on its own
  not-yet-existent completion (it is, by construction, still running). So
  `until={"completed":true}` never trips the expiry gate → the sub never expires
  → the worker is **pinned immortally** (`serve()` never retires). The only
  escape is to **nak** it as unsatisfiable — i.e. a schema term *every worker
  refuses* on the very topic it is supposed to be a first-class member of.
- **duplicates `Outcome.COMPLETED`** — the canonical projection of the liveness
  tiers (the four-copies dedup of commit `44cea4a`); a second spelling on the
  wrong plane.
- **composes incoherently** — `{all: [{step: N}, {completed: true}]}` waits
  forever when the run completes early (`step: N` never holds) and is dead weight
  when it completes late.

It is a **wire-version bump for a coordinate the worker can't service** — the
verdict plane wearing a subscription costume. The completion notion belongs in
the orchestration plane (where `ensure` already reads it), not the substrate
algebra. (If ever wanted as a standalone dead-end, this section is the diagnosis
to move.)

## Alternative considered — on-channel artifact pointer (deeper)

Have the producer `channel.send` a pointer/existence record *after* the
`store.put`, and have the consumer gate on that logged `value`/pointer
(`../specs/store.md`). This closes the race with **no** producer-ordering
invariant and **no** library change — but it asks every off-channel producer to
emit a pointer and every consumer to wait on it. Strictly more correct, strictly
more work; the right move if off-channel artifacts proliferate. `await_complete`
is the lighter fix for the common "the artifact is durable once the run is done"
case.

## Orthonormal-basis check

- **Independence:** not a new primitive — it *consults* the existing verdict
  projection (`Outcome.COMPLETED`) that `preempted-vs-completed` already wired
  in, and adds **one boolean that selects the return-predicate**. The new axis
  (return-predicate independent of drive-target) was previously *hard-wired*
  equal; this names and frees it. No redundancy.
- **Spanning:** supplies the missing "return when the producer is done, however
  far that is" vector. Out-of-scope generality (an arbitrary
  caller-supplied return predicate) is excluded — YAGNI, same call as the
  `from`/`every` deferral.
- **Canonical form:** completion is `Outcome.COMPLETED`, *the* canonical
  terminal projection; gating on it reuses the normal form rather than minting a
  parallel `completed` term (the rejected branch). A boolean is the
  least-surface selector for a binary choice.
- **Orthogonality:** refines `ensure-until-condition.md`'s split. That doc gave
  *condition* (`until`, `ensure`) ⟂ *enforcement* (`until`→worker bound,
  producer) ⟂ *resume-policy* (`completed`/`preempted`/`_FAILURES`, `ensure`).
  This observes that `until` silently carried **two** of those — it was *both*
  the enforcement target *and* the return-predicate — and cleanly separates the
  **return-predicate** (window vs completion) as its own knob, leaving `until`
  the enforcement target alone.
- **Serendipity:** the COMPLETED branch `ensure` already has becomes the primary
  path; `Watcher.wait` is its non-driving twin; the `completed`/`preempted` bit
  composes unchanged; no new vocabulary, no wire change.

## Scope / ripple — **no wire-schema change**

- `runstate/memoizer.py` — `ensure` gains `await_complete`; the `reached()`
  return-predicate (folding the existing COMPLETED early-returns); the
  `preempted → raise`; the no-progress guard gated on `not await_complete`.
  Optional `ensure_terminal` wrapper.
- `runstate/vocabulary/schedule.py`, `protocol/*.schema.json` — **no change**
  (the whole point versus the predicate branch).
- Tests — `tests/test_memoizer.py`: `await_complete` returns on completion, not
  window-close; a producer that flushes a (test) artifact *after* its last step
  and *before* `stopped(completed=True)` is observed only after the flush; a
  `preempted` terminal under `await_complete` raises; a `_FAILURES` terminal
  still raises.
- `docs/specs/memoizer.md` — the `await_complete` semantics; the
  return-predicate-vs-drive-target distinction; the worker contract pointer to
  `completed-opt-in.md`.
- **Downstream (translation, separate repo — out of scope here):** `ensure_run`
  passes `await_complete=True`; closes the `embed_hyp` race without the
  write-before-gating-step reorder, and pre-empts the latent `estimate_worker`
  case.

## Use-case evidence (honest priority)

One consumer repo (translation), two instances, **one already mitigated**:

- `embed_hyp` wrote its off-channel embedding *after* its step loop; the
  consumer's `ensure_run(until={"step": n})` read it ~50 % of the time before
  the `store.put` landed. **Already fixed producer-side** by reordering
  (write-before-gating-step, commit `265ad0d`) — `await_complete` would have
  fixed it consumer-side without the reorder.
- `estimate_worker`'s `per_sentence` is accumulated *across* steps (no reorder
  possible) and is **latent** today — its only readers are offline report/plot
  scripts, never a live gate. It is the case `await_complete` uniquely fixes,
  *when* a live consumer needs it.

So: well-motivated, but below the "≥3 validated use cases" bar the repo applies
to speculative surface (cf. the Reconfigure non-goal). Promote when a live
consumer depends on a post-terminal artifact (the `estimate_worker` trigger).
*(The earlier "fold in opportunistically when `ensure`'s terminal handling is
reopened for ensure-redrive" trigger has **lapsed**: ensure-redrive shipped
2026-06-27 as G1 + a caller recipe with `ensure` left **unchanged** — there is no
reopening to ride, so this awaits the `estimate_worker` consumer pull alone.)*

## Non-goals

- **Not the predicate branch** — no `completed` term in the condition-algebra
  (above).
- **Not a general return-predicate parameter** — `await_complete` is the one
  in-scope selector; an arbitrary caller predicate is YAGNI and moves the
  done-judgment off the authoritative log (the same reasoning
  `preempted-vs-completed.md` used to refuse a caller-side "is it done?").
- **Not absorbing the artifact store into the channel** — the on-channel
  alternative is a *pointer*, never the blob (keeping bulk bytes off the step
  channel is deliberate).
- **Not changing the `completed` contract** — it stays the worker's opt-in claim
  (`completed-opt-in.md`); `await_complete` *relies* on that default, and its
  `preempted → raise` is what makes a forgotten claim loud.
