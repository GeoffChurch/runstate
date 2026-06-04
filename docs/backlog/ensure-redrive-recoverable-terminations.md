# ensure: re-drive recoverable terminations

**Status:** forward-looking idea, not designed. Surfaced by the mycooc dogfood
(Phase-4 dispatch adoption).

## The idea

`ensure` currently raises on any `_FAILURES` terminal outcome
(`errored`/`killed`/etc.). But a run that was killed or timed-out mid-chunk and
made progress before stopping is structurally resumable: its checkpoint is valid,
its channel log is intact, and re-driving it is exactly what `ensure` would do if
it had been a `preempted` outcome instead.

Letting `ensure` re-drive recoverable terminations — and raising only on genuine
no-progress (the run stopped with zero new progress since the last extend) — would
subsume a consumer's custom resume policy inside the memoizer, shrinking the
seam.

## Evidence from the mycooc dogfood

mycooc's Phase-4 `_SubprocessProducer` had to implement this internally:

- **`_run_one_chunk`** (inside `extend`) wraps each subprocess dispatch with
  `on_killed=RESUME` / `max_resume_attempts` logic so an OS-timeout mid-chunk
  re-drives the chunk to its step bound rather than failing the variant.
- **`_SyncHandle`** synthesizes a `lifecycle.stopped` with `completed=False` (no
  `error`) on a hard crash — a well-formed `preempted`-shaped terminal — so
  `ensure` can re-drive rather than raise. The synthesis is required because a
  hard-crashed subprocess emits no terminal at all; without it `ensure` would
  hang waiting for a terminal that never comes.

These are the dual of the missing feature: the consumer had to (a) implement its
own resume loop inside `extend`, and (b) manufacture a resumable-looking terminal
on hard crash, to work around `ensure` not knowing the difference between
"recoverable stop" and "fatal error."

## What "recoverable" means (the needed design)

A termination is recoverable when both hold:
1. **Progress was made** — the run's latest heartbeat step advanced past its
   starting point for this extend call.
2. **Checkpoint is valid** — the run's state is consistent (not mid-write when it
   died). For a subprocess producer this is heuristic (if it emitted a terminal it
   had time to flush; if it was hard-killed it may not have).

The tricky part is (2): distinguishing "killed cleanly after checkpointing" from
"killed mid-write, checkpoint corrupt." The former is re-drivable; the latter
should fail. A subprocess producer that always checkpoints before emitting its
terminal satisfies (2) by construction — making the terminal the validity signal.

## Validation approach

This is **not** bit-exact-testable: timing-dependent (did the kill arrive before
or after the checkpoint flush?). The test strategy needs a different axis:

- Property: re-driving a killed-but-checkpointed run reaches the same final state
  as an unkilled run at the same step budget (i.e. the outcome is idempotent under
  resilient-redriving).
- A mock producer that intentionally dies mid-chunk (after N steps) and resumes
  from checkpoint can test this deterministically, sidestepping the real kill
  timing.
