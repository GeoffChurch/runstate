# `ensure` respects worker-declared completion; `RunResult.outcome`: `stopped` → `preempted`

**Status:** converged 2026-06-03 (design dialogue); ready for review → implementation.
**Origin:** surfaced by the mycooc dogfood (the runstate-adoption migration, Phase 4 —
orchestration via the memoizer). The validating use case did its job: it exposed a
real gap in a shipped primitive.

## The gap

`ensure(producer, name, *, up_to)` (`runstate/memoizer.py`) drives a producer until
`_progress(channel) >= up_to - 1`, re-driving (relaunching/resuming) on **every** clean
stop short of that, and raising on `_FAILURES` (`errored`/`killed`/`presumed_dead`). It
has **no way to express "the producer finished before `up_to`"** — a worker that
*converges* (early-stops) and will not produce step `up_to-1` no matter how often it is
re-driven. Against such a worker, `ensure` either spins (re-driving a producer that keeps
making a little progress past convergence — wrong *and* it changes the worker's output) or,
if a resume makes literally zero progress, raises the no-progress `RuntimeError` (also
wrong — a converged run is *done*, not failed).

This is the sequence-worker analogue of early stopping: the trajectory's useful length is
**the worker's** to decide, not the caller's `up_to`. `ensure` currently assumes the
caller's bound is always reachable.

## The distinction (the fix is already latent in the design)

A clean `lifecycle.stopped` is one of exactly two kinds, and the difference is a property
of **the stop**, not of the worker:

- **completed** — the producer is *done*; a relaunch yields nothing more.
- **preempted** — an external / non-intrinsic pause (a budget, a time limit, a chunk
  boundary, a cooperative `control.stop`); a relaunch *resumes* and yields more trajectory.

`RunResult.outcome` (the closed, normalized projection `peek_terminal` already computes)
**already encodes this**: `completed=True` in the stopped body → `outcome="completed"`; any
unmarked clean stop → `outcome="preempted"` (the resumable bucket, renamed from `"stopped"`
in this same change). `ensure` simply was not consulting the distinction that already
existed — it treated the two identically (re-drive). The fix is to consult it.

## Change A — `ensure` is satisfied by worker-declared completion

`ensure` is satisfied (returns the `history` read) when **either**:
1. `_progress(channel) >= up_to - 1` (the caller's bound is reached), **or**
2. the latest terminal has `outcome == "completed"` (the producer declared itself done —
   the early-completion / convergence case).

It re-drives only `preempted` terminals (resumable), and still raises on `_FAILURES`. When
(2) fires short of `up_to`, `ensure` returns the *available* (shorter) trajectory — the
honest answer: the producer will not yield more. Concretely the outer loop gains, before
re-driving, a check like:

```python
result = peek_terminal(channel)
if result is not None and result.outcome == "completed":
    return history(channel, name, dense)   # producer is done, even short of up_to
```

**Worker contract (load-bearing):** the worker claims `completed=True` only when it is
intrinsically done, and leaves the bit unset (default `completed=False`) otherwise — the
consumer-side projection then reads `preempted`. The worker emits no `reason` string; the
`completed` bit *is* the self-classification. `ensure` does **not** take a caller-side
"is it done?" predicate (that would move the judgment off the authoritative log and into a
guess). A worker that lies (claims `completed` on a mere pause) under-produces; one that
never claims `completed` and cannot reach `up_to` is the existing spin/no-progress case —
unchanged.

## Change B — rename the outcome value `"stopped"` → `"preempted"`

`RunResult.outcome` becomes `completed | preempted | errored | killed | presumed_dead`.

Why: `"stopped"` is the generic past tense that truthfully describes *every* terminal
(errored runs stopped; completed runs stopped), so as a *specific* enum value it is the
weakest of the five and bleeds into `"completed"`. `"preempted"` names the resumable
semantics precisely and stays cleanly orthogonal to `"killed"` (preempted = a *clean*,
checkpointed, resumable pause; killed = a *hard* termination with no clean-stop guarantee).

This bucket is purely a **consumer-side projection** of the worker's `completed=False`
(unmarked) stop: `peek_terminal`'s `launcher.terminated` tier only ever yields
`killed`/`completed`(exit 0)/`errored`(exit≠0) — never this bucket. That is correct: only
the *worker* can know "I paused, resumable" vs "I finished"; a launcher sees only an exit
code. The worker claims `completed=True` or stays unmarked; `preempted` is what the
consumer reads when the claim is absent.

`commanded` stops (a `control.stop` fired at a safe point) and *self-preemption* (a worker
that pauses on its own budget with no `control.stop`) both leave `completed` unset and
therefore both project to `preempted`. Commandedness is recoverable from the `control.stop`
on the log. We keep **one** `preempted` outcome — the worker emits no `reason` string.

The *topic* `lifecycle.stopped` is **unchanged** — it remains the event topic for any
cooperative stop (completed or preempted). Only the consumer-side `RunResult.outcome`
*value* is renamed.

## It generalizes

The unifying invariant: **`ensure` re-drives iff (request unmet) AND (last terminal is
`preempted`); it is satisfied iff request-met OR `completed`.** `"completed"` reads as "the
producer has produced everything it will, this episode," which is right across the worker
taxonomy:

- **Self-advancing sequence** (the shipped case): never self-completes unless it converges →
  drive to the bound; `completed` = converged.
- **Function / random-access** producer (the `run_id`-recipe / `ensure(I)` index-algebra
  future, `../backlog/memoizer-index-algebra.md`): `completed` = "all requested keys
  computed." The same bit; `ensure(I)` inherits it with no change.

So the `completed`/`preempted` bit is the load-bearing generalization, not a sequence-only
patch.

## Orthonormal-basis check

- **Independence (necessity):** not a new primitive — it *consults* the existing canonical
  projection (`RunResult.outcome`) that `ensure` was ignoring. This removes a gap; it adds no
  redundancy. The rename adds no value, only renames one.
- **Spanning (sufficiency):** supplies the missing basis vector — "the producer is done
  before the caller's bound" — without over-reaching: `ensure` reads the worker's own
  declaration, baking no workload opinion (which stops are "done" is the worker's call, via
  the `completed` bit).
- **Canonical form:** `outcome` is *the* canonical projection of the liveness tiers (per the
  repo rubric); using it is canonical, and `preempted` is the least-arbitrary name for the
  resumable bucket.
- **Orthogonality:** `completed` (intrinsic done) ⟂ `preempted` (extrinsic resumable pause)
  ⟂ the failure tier; each carries exactly one concern.
- **Serendipity:** one log-sourced notion of "done," now shared by `ensure`,
  `peek_terminal`, the `Watcher`, and consumer reuse-by-`peek_terminal` (e.g. mycooc Phase 3)
  — they already read the same record; `ensure` just joins them.

## Scope / ripple

Python + docs + tests; **no wire-schema change for A+B** — `outcome` is the consumer verdict
(`RunResult`), not a convention body, and at the time of A+B `Stopped.reason` was a free string
that A (early-completion) and B (the rename) read but did not reshape. The `Stopped` body's *own*
reshaping — dropping the free `reason` for a `completed` bit (and the wire bump that entails) — is a
**separate, later** change with its own spec: **`completed-opt-in.md` (B′)**. Its file inventory
lives there; don't duplicate it here.

A+B's ripple:
- `runstate/liveness.py` — `peek_terminal` assignment `"stopped"`→`"preempted"`; the
  `RunResult.outcome` enum comment + example.
- `runstate/memoizer.py` — `ensure`'s outer loop: the early-completion check (Change A).
- `runstate/sweep.py` — the comment referencing the `"stopped"` outcome.
- Tests — `test_liveness.py` / `test_sweep.py` / `test_inproc_integration.py` assertions on the
  renamed outcome; the `ensure` early-completion tests.
- `docs/design-v0.2.md` — the `RunResult.outcome` enum; the §7 / `Stopped` prose.
- `protocol/lifecycle-v0.2.schema.json` — **no change under A+B** (B′ is what later reshapes the
  `Stopped` body).

## Non-goals

- **Do not** split `commanded` into its own outcome — commandedness is recoverable from the
  `control.stop` on the log; the clean-stop bucket stays `preempted`.
- **Do not** rename the `lifecycle.stopped` *topic*.
- **Consumer-side, mycooc Phase 4 (separate repo):** for a worker to *benefit*, it must
  claim `completed=True` for true completion (patience/convergence) and leave the bit unset
  otherwise (chunk-preempt / `max_steps` / time budget — `max_steps` is *reaching the
  caller's bound*, i.e. resumable, **not** `completed`).
