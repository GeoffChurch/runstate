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
**already encodes this**: `reason=="completed"` → `outcome="completed"`; any other clean
reason → the catch-all bucket (today named `"stopped"`). `ensure` simply was not consulting
it — it treated `completed` and the catch-all identically (re-drive). The fix is to consult
the distinction that already exists.

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

**Worker contract (load-bearing):** the worker must *self-classify* its stop — emit
`reason="completed"` only when it is intrinsically done, and a preemption reason otherwise.
This is a property of the log the worker already owns; `ensure` does **not** take a
caller-side "is it done?" predicate (that would move the judgment off the authoritative log
and into a guess). A worker that lies (emits `completed` on a mere pause) under-produces;
one that never emits `completed` and cannot reach `up_to` is the existing spin/no-progress
case — unchanged.

## Change B — rename the outcome value `"stopped"` → `"preempted"`

`RunResult.outcome` becomes `completed | preempted | errored | killed | presumed_dead`.

Why: `"stopped"` is the generic past tense that truthfully describes *every* terminal
(errored runs stopped; completed runs stopped), so as a *specific* enum value it is the
weakest of the five and bleeds into `"completed"`. `"preempted"` names the resumable
semantics precisely and stays cleanly orthogonal to `"killed"` (preempted = a *clean*,
checkpointed, resumable pause; killed = a *hard* termination with no clean-stop guarantee).

This bucket is purely **worker-self-reported**: `peek_terminal`'s `launcher.terminated` tier
only ever yields `killed`/`completed`(exit 0)/`errored`(exit≠0) — never this bucket. That is
correct: only the *worker* can know "I paused, resumable" vs "I finished"; a launcher sees
only an exit code. `"preempted"` is the worker's cooperative declaration of a resumable
pause.

`commanded` is **one `reason` that projects into `preempted`**, not the bucket's name. The
bucket also holds *consumer self-preemption* — a worker that pauses itself on its own budget
(mycooc's chunk / `max_steps` / time budget, configured at launch and polled internally,
with **no** `control.stop`). So `commanded` would be too narrow (it would mislabel
self-budget stops). We keep **one** `preempted` outcome; we do **not** add a `commanded`
outcome (that distinction lives in the verbatim `reason`, where it belongs).

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
  `reason`).
- **Canonical form:** `outcome` is *the* canonical projection of the liveness tiers (per the
  repo rubric); using it is canonical, and `preempted` is the least-arbitrary name for the
  resumable bucket.
- **Orthogonality:** `completed` (intrinsic done) ⟂ `preempted` (extrinsic resumable pause)
  ⟂ the failure tier; each carries exactly one concern.
- **Serendipity:** one log-sourced notion of "done," now shared by `ensure`,
  `peek_terminal`, the `Watcher`, and consumer reuse-by-`peek_terminal` (e.g. mycooc Phase 3)
  — they already read the same record; `ensure` just joins them.

## Scope / ripple

Python + docs + tests; **no wire-schema change** (`outcome` is the consumer verdict
`RunResult`, not a convention body; `Stopped.reason` is a free string, unlike the
closed-enum `Nak.reason`):

- `runstate/liveness.py` — `peek_terminal` `:90` assignment `"stopped"`→`"preempted"`; the
  `RunResult.outcome` enum comment `:28` and the `:24` example.
- `runstate/memoizer.py` — `ensure`'s outer loop: the early-completion check (Change A).
- `runstate/sweep.py:18` — comment referencing the `"stopped"` outcome.
- Tests — `test_liveness.py`, `test_sweep.py`, `test_inproc_integration.py` assertions on
  `outcome == "stopped"`; **add** `ensure` tests (synthetic channels: `completed` short of
  `up_to` ⟹ `ensure` returns without re-driving; `preempted` short ⟹ re-drives;
  `completed` at/over `up_to` ⟹ returns).
- `docs/design-v0.2.md` — the `RunResult.outcome` enum (`:222`, `:228`); and soften the
  §7 / `Stopped`-schema prose "its existence on the log = the run cleanly finished" to "a
  clean cooperative stop; *finished* (completed) vs *paused* (preempted) is carried by
  `reason` → the `outcome` projection."
- `docs/specs/memoizer.md` — the `ensure` semantics (the completion satisfaction condition).
- `protocol/lifecycle-v0.2.schema.json` — **no change** (the `Stopped.reason` free string
  already admits any worker label; only its prose description is clarified, optionally).

## Non-goals

- **Do not** split `commanded` into its own outcome — it is one `reason` under `preempted`.
- **Do not** rename the `lifecycle.stopped` *topic*.
- **Consumer-side, out of scope here (mycooc Phase 4):** for a worker to *benefit*, it must
  emit `reason="completed"` for true completion (mycooc: patience/convergence) and a
  preemption reason otherwise (mycooc: chunk-preempt / `max_steps` / time budget — note
  `max_steps` is *reaching the caller's bound*, i.e. `preempted`/resumable, **not**
  `completed`). That re-mapping is schema-legal (`Stopped.reason` is free) and lands in
  mycooc's emitter, not here.
