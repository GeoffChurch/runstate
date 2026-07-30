# ensure: re-drive recoverable terminations

**Status:** **RESOLVED 2026-06-27 — shipped as G1 (take-the-latest) + a caller recipe**, *not*
as an in-`ensure` feature. `ensure` is unchanged (it re-drives `preempted`, raises on a death);
the only library change was [G1](value-plane-divergence-resolution.md). The journey: two
reframes were refuted (`../dead_ends/ensure-extend-pushorfail.md`,
`../dead_ends/failure-detector.md`), a recoverability-flip + producer `recoverable()` hook was
explored and **implemented**, then reverted after a "why not just fail fast?" challenge showed
it added in-library process-management (a give-up budget + thrash-visibility) for a ~10-line
gain. Kept here as the record of how the need is met.

## The need

A run killed or timed-out mid-chunk that *made progress* is structurally resumable — its
checkpoint is valid and its log intact — yet `ensure` raises on it. Consumers (mycooc's
`_run_one_chunk` / `_SyncHandle`) hand-roll a resume loop to work around that.

## The resolution: `ensure` fails fast on a death; the caller resumes by re-calling

`ensure` already does exactly the right thing — it auto-continues across `preempted` stops
(routine, no decision) and **raises on a death** (killed/errored — exceptional, a real "retry?"
decision). The caller catches the death, applies *its own* retry policy, and **re-calls
`ensure`**, which resumes from the checkpoint: read-first sees progress-but-not-done →
relaunch + resume. [G1](value-plane-divergence-resolution.md) makes the resumed overlap
re-emission safe (take-the-latest), so the re-call cannot poison reuse.

```python
from runstate import ensure, peek_terminal, Outcome, RunFailedError, NoProgressError

for _ in range(budget):                       # the caller owns the budget...
    try:
        series = ensure(producer, name, until=until)   # auto-continues preemptions; raises on a death
        break
    except (RunFailedError, NoProgressError):  # both derive from Exception, NOT RuntimeError
        r = peek_terminal(producer.channel)
        # the worker that wrote Stopped(error=...) self-diagnosed fatal -> don't retry;
        # a non-self-diagnosed death (killed / recordless exit) with progress -> resumable.
        if not (r and r.error is None and r.outcome in (Outcome.KILLED, Outcome.ERRORED)):
            raise
        # ...re-calling ensure resumes from the checkpoint
else:
    raise RuntimeError("exhausted retry budget")
```

(Verified by `tests/test_memoizer.py::test_ensure_killed_resumes_on_caller_re_call_take_the_latest`:
the first call fails fast on a kill — asserted as `RunFailedError`, which is what makes the catch
above correct — and the re-call resumes, take-the-latest absorbing a divergent overlap. The
`NoProgressError` arm of that `except` is **not** covered by this test.)

## Why this shape

- **Preemption vs death.** Preemption is routine and policy-free (you always want to continue),
  already bounded (the no-progress guard + the goal) and already visible (it writes a record) —
  so `ensure` bundles it. A death is exceptional and policy-laden (retry? how many times? give
  up?) — so `ensure` surfaces it and the caller decides. Bundle the routine; surface the
  exception.
- **The retry budget and the per-attempt visibility belong to the caller** — it has the budget
  and the eyes. An in-`ensure` auto-redrive would have needed a *generic* give-up bound (which
  can't tell a thrashing run from a legitimately long one — a naive `max_attempts` kills the
  100-preemption run) and *special* machinery to surface a silent crash-loop. The caller's loop
  gets both for free. (These were the "G2"/"G3" complications a `recoverable()` hook forced;
  fail-fast dissolves them.)
- **Scope.** Auto-retrying deaths with a give-up policy is process management — which the core
  explicitly leaves to the caller (ray / submitit / hydra). Fail-fast keeps `ensure` in the
  transport-and-reuse lane.
- **The self-diagnosed-fatal discriminator (`error is not None`) lives in the caller's retry
  predicate**, where it belongs: only the worker knows its death was fatal (it wrote
  `Stopped(error=…)`); everything else is the caller's judgment.

## G1 is the one library change (shipped)

[value-plane-divergence-resolution](value-plane-divergence-resolution.md): `history` resolves a
divergent re-emission by take-the-latest (delete the sticky raise). It is the keystone that makes
the caller's re-call safe, **and** it fixes a latent bug — the already-shipped `preempted`-redrive
could sticky-poison reuse on a non-reproducible worker. Committed.

## Honest scope vs the mycooc dogfood

This hands the consumer back a ~10-line retry loop (catch + re-call), rather than subsuming it via
a hook. But the bound and the visibility then live where they belong (the consumer's loop, which
has the budget), and an earlier review found the *bulk* of mycooc's `_run_one_chunk` is subprocess
process-management (timeout / SIGKILL escalation / SAVING-wait) that was never entering the core
anyway. Net: ~10 lines back, in exchange for no `recoverable()` hook, no in-library give-up bound,
no thrash-visibility machinery, and a cleaner scope boundary.

## Deferred (unchanged — no consumer pulls yet)

A public `extend_once` + channel-bound `satisfied` + the formalized "primitive basis" + the
own-loop recipe (for exotic *stop* policies). `history` stays exported; the rest stays internal
until a consumer needs to compose its own drive loop.
