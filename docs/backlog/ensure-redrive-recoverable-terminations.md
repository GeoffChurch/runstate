# ensure: re-drive recoverable terminations

**Status:** forward-looking idea, partially designed. Surfaced by the mycooc dogfood
(Phase-4 dispatch adoption). The **push-or-fail `extend` reframe was REFUTED** 2026-06-25
(`../dead_ends/ensure-extend-pushorfail.md`) — the corrected shape (below) is a minimal
predicate, not a seam inversion, and it is **gated** on value-plane robustness.

## The idea

`ensure` currently raises on any `_FAILURES` terminal outcome (`errored`/`killed`/
`presumed_dead`). But a run killed or timed-out mid-chunk that *made progress* before
stopping is structurally resumable: its checkpoint is valid, its channel log is intact, and
re-driving it is exactly what `ensure` already does for a `preempted` outcome. Letting
`ensure` re-drive recoverable terminations — raising only on genuine no-progress — would
subsume a consumer's custom resume policy and shrink the seam.

## The reframe that decides the shape: the preempt/kill asymmetry is *protective*

The tempting move (and the refuted `extend` reframe) is to call "preempt re-drives but a
same-budget kill raises" an embarrassing leak and erase it. It is the **reverse**: the
asymmetry is load-bearing.

- A cooperative **`preempted`** checkpoints *on stop* → checkpoint == frontier → resume
  re-emits nothing → safe.
- A hard **`killed`** cannot checkpoint on the way out → checkpoint sits *behind* the
  last-emitted step → resume re-runs that gap and **re-emits** it. If the recompute is
  non-deterministic (different host/BLAS/unseeded dropout), `history()` raises `divergent
  re-emission … reuse would be unsound` **permanently and stickily** on the append-only log.

So *clean-final-checkpoint ⟺ no-overlap ⟺ safe-to-redrive*. Today's `killed`→raise is exactly
what prevents the poisoning write. **Re-driving killed runs is therefore not free** — it is
gated on the value plane no longer being poison-able (G1, below). This is the same rock that
killed the cross-host-liveness reframe (`../dead_ends/failure-detector.md`).

## The corrected design — a canonical recoverability function (no fat predicate, no default)

Keep `ensure` owning its loop, the no-progress guard, and the foreign-episode wait. Replace
only the `result.outcome in _FAILURES → raise` branch with a **library recoverability
function** over the basis we already have — `Outcome` + the worker's *self-reported* `error`
+ progress:

| outcome | self-diagnosed? | recoverable? | gated on |
|---|---|---|---|
| `ERRORED` (`Stopped.error` set) | yes — worker said it was fatal | **no** | — |
| `PREEMPTED` (cooperative stop) | n/a — clean | **yes**, now | (already shipped) |
| `KILLED` (external signal) | no | yes | **G1** (non-poisoning value plane) |
| `PRESUMED_DEAD` (liveness inference, no terminal) | no | yes | **the cross-host claim oracle** (re-driving a maybe-alive run is the concurrent double-live route) |

This is *canonical*, not a workload opinion: the worker is the only layer that knows its
death was fatal, and it signals that by writing `Stopped(error=…)` (`ERRORED`) versus dying
without self-diagnosis (`KILLED`/`PRESUMED_DEAD`). There is **no producer-predicate default**
to justify — recoverability is derived from the existing taxonomy. The *only* producer
escape hatch is an **optional override** for genuine error-string classification (a producer
that knows its `ERRORED` strings are transient — CUDA-OOM-resumable vs NaN-fatal); an
override has no default question (it is absent unless supplied).

## The gates (each closes a verified red-team finding)

- **G1 — value-plane robustness (the keystone; blocks `KILLED`-redrive).** A non-sticky /
  episode-scoped `history` that prefers the *resumed* episode's value on an overlapping step
  (the way `value_series` already last-write-wins) instead of raising forever. Without it,
  re-driving a killed run can irreversibly poison reuse-by-`run_id` — the one use case the
  whole effort validates. **This is the same prerequisite the cross-host claim gate needs**
  (index.md, "Cross-host liveness"); two reframes have now died on it.
- **G2 — an explicit `max_attempts` bound.** The no-progress guard catches a 0-progress
  crash-loop but *not* a "slowly-failing-forward" worker (a little progress each relaunch,
  never reaching `until`) — that spins to the budget. mycooc already chose a bound
  (`RunPolicy.max_resume_attempts = 2`); the generic path must not silently drop it.
- **G3 — observable thrash for raw-subprocess producers.** "Thrashing stays visible via
  `launcher.launched`/`terminated`" is false for the producer the `extend` seam exists for:
  mycooc's `_run_one_chunk` spawns via raw `subprocess.Popen` and writes *no* `launcher.*`
  record, so a crash-loop SIGKILLed before first heartbeat leaves only `lifecycle.started`
  churn (no terminal, no progress) — invisible to `peek_terminal`/`Watcher`. Either restrict
  auto-relaunch to launcher-mediated producers or have the helper write a per-attempt record.

## Evidence from the mycooc dogfood

mycooc's Phase-4 `_SubprocessProducer` had to implement this internally — the dual of the
missing feature:

- **`_run_one_chunk`** (inside `extend`) wraps each subprocess dispatch with
  `on_killed=RESUME` / `max_resume_attempts` so an OS-timeout mid-chunk re-drives to its step
  bound rather than failing the variant.
- **`_SyncHandle`** synthesizes a `lifecycle.stopped` with `completed=False` (no `error`) on
  a hard crash — a `preempted`-shaped terminal — so `ensure` re-drives rather than hangs
  waiting for a terminal that never comes.

These confirm the diagnosis *and* the gates: the consumer hand-rolled the resume loop (G2's
bound included), and manufactured a clean-looking terminal precisely because a hard crash
emits none (G3's observability gap).

## Validation approach

**Not** bit-exact-testable: timing-dependent (did the kill land before or after the
checkpoint flush?). A different axis:

- Property: re-driving a killed-but-checkpointed run reaches the same final state as an
  unkilled run at the same step budget (idempotent under resilient re-driving).
- A mock producer that intentionally dies mid-chunk (after N steps) and resumes from
  checkpoint tests this deterministically, sidestepping real kill timing.
- A **divergence** test (the G1 gate): a mock producer whose resume re-emits an overlapping
  step with a *different* value must not permanently poison `history` once G1 lands.
