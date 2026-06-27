# ensure: re-drive recoverable terminations

**Status:** forward-looking, **converged + red-teamed for implementation** (2026-06-26).
Surfaced by the mycooc dogfood. Two reframes were refuted on the way here
(`../dead_ends/ensure-extend-pushorfail.md`, `../dead_ends/failure-detector.md`). After a
four-angle code-grounded review the design shrank to a small, staged core; the API-surface
package it had accreted is **deferred** (see *Deferred*). The whole feature is: *delete the
divergence raise (G1), flip one branch (`KILLED`→re-drive for opted-in producers), add a retry
bound, add a thrash record.*

## The idea

`ensure` raises on any `_FAILURES` outcome (`errored`/`killed`/`presumed_dead`). But a run
killed or timed-out mid-chunk that *made progress* is structurally resumable — re-driving it is
what `ensure` already does for a `preempted` outcome. Letting `ensure` re-drive the recoverable
ones (raising only on genuine no-progress / a spent budget) removes a resume loop every consumer
hand-rolls. mycooc had to: `_run_one_chunk` runs an `on_killed=RESUME` loop, and `_SyncHandle`
synthesizes a `preempted`-shaped terminal on a hard crash so `ensure` re-drives instead of
hanging.

## Safe-to-redrive is about the *checkpoint*, not the *outcome* — and the shipped preempt path is already exposed

An earlier draft framed the preempt/kill split as a *protective asymmetry* (preempt
checkpoints-on-stop → no overlap → safe; kill leaves a gap → unsafe). The red-team showed that
is **false as a property of the outcome.** "Checkpoint == frontier" is a *worker discipline*,
not a consequence of stopping cleanly:

- The reference `Worker` has **no checkpoint primitive** — `steps(start=k)` only *accepts* a
  resume point; saving is the user's job. The default producer injects only the target, and
  `steps` defaults `start=0`, so a naive worker re-driven by the default machinery **restarts
  from 0 and re-emits `0..frontier`** every time.
- Even a disciplined worker has a **race**: `lifecycle.stopped(preempted)` is written by
  `Worker.__exit__` *before* the user's post-`with` checkpoint write. A kill in that window
  yields a clean `preempted` terminal + a stale checkpoint — a `killed` scenario wearing a
  `preempted` mask.

So overlap-re-emission is a function of **(checkpoint gap × non-reproducibility)** — both
worker/producer properties, *not* of `preempted` vs `killed`. The consequence: the
**already-shipped `preempted`-redrive is a latent poison on `master`** for any non-reproducible
worker, and **G1 fixes it** — G1 is not merely a gate for a future feature, it repairs a live
bug. (mycooc's `_SyncHandle` routes a hard crash onto the `preempted` path explicitly, so it
already drives the dangerous case.)

## Recoverability: the library decides the two clear cases; the producer owns the kill judgment

Replace `ensure`'s `result.outcome in _FAILURES → raise` branch with a recoverability decision
keyed on **`result.error is not None`** (the worker's self-report), *not* `outcome == ERRORED`
(which conflates two different deaths — see the table):

| terminal | `error` | verdict | why |
|---|---|---|---|
| `completed` | — | **done** (read-first short-circuits) | reached its goal |
| `errored`, `Stopped.error` set | non-None | **fatal — raise** | the worker self-diagnosed; only it knows |
| `preempted` (clean stop) | None | **re-drive** | already shipped; safe once G1 lands |
| `killed` (external signal) | None | **producer's call**, conservative default = raise | "is this kill resumable?" is workload knowledge |
| `errored` via nonzero exit, *no* `Stopped` (recordless) | **None** | **producer's call**, conservative default = raise | a crash with no self-diagnosis — same bucket as `killed` |

The library can canonically decide only two things from the existing taxonomy: **`error is not
None` ⟹ fatal** (the worker said so) and **`completed` ⟹ done**. Whether a *non-self-diagnosed*
death (`killed`, recordless exit) is worth re-driving is **not derivable** — three producers see
the identical `(killed, progress>0)` and have opposite correct answers (spot-reclaim = yes;
OOM = re-OOMs = no; non-reproducible = poison). So it is the **producer's** judgment, via an
**optional** `recoverable(result) -> bool` hook (consulted only for the non-self-diagnosed
bucket), with a **conservative default of `False` (= today's raise).** That keeps the feature
**purely additive** — a producer that doesn't opt in gets exactly today's behavior — and keeps
the workload opinion ("an external kill is usually transient") out of the library.

`PRESUMED_DEAD` is **inert here**: `ensure`'s only verdict source is `peek_terminal`, which
never returns it (it is the `Watcher`'s inference tier). Re-driving a *maybe-alive* run is the
concurrent-double-live route and belongs to the cross-host claim gate, not this item.

## Staging

1. **G1 — take-the-latest** ([value-plane-divergence-resolution](value-plane-divergence-resolution.md)).
   Delete the divergence raise; `history` collapses to the fold `value_series` already ships.
   *Removes code, and fixes the shipped `preempted`-redrive poison.* Independently shippable.
2. **The recoverability seam (additive, G1-free).** Key the raise on `error is not None`; add the
   optional `recoverable(result)` hook with a conservative `False` default. Changes **no**
   behavior until a producer opts in — so it can land any time.
3. **Post-G1: `KILLED`-redrive.** Producers opt in; bounded by **G2** (`max_attempts`); thrash
   made visible by **G3**. This is the actual user-facing feature, and it is one branch.

## The remaining gates

- **G2 — a retry bound (required, not a nicety).** The no-progress guard fires only on *zero*
  progress; a "slowly-failing-forward" worker (advances a little, dies, repeats, never reaches
  `until`) passes it forever and **hangs**. `ensure` needs a `max_attempts` (consecutive
  no-completion re-drives) — default small, e.g. mycooc's `RunPolicy.max_resume_attempts = 2`.
  Stall-on-zero (the existing guard) and the budget are two independent bounds; keep both. Richer
  give-up policies are out of scope — a consumer that needs one composes its own loop (see
  *Deferred*).
- **G3 — observable thrash.** A producer that spawns via raw `subprocess.Popen` (mycooc's
  `_run_one_chunk`) writes no `launcher.*` record, so a crash-loop SIGKILLed before first
  heartbeat leaves only `lifecycle.started` churn — invisible to `peek_terminal`/`Watcher`.
  Either restrict auto-relaunch to launcher-mediated producers, or have the redrive helper write
  a per-attempt terminal for recordless deaths (which also gives `peek_terminal` something to
  read — what `_SyncHandle` hand-rolls today).

## Deferred (YAGNI — no consumer pulls yet)

The review found these were public surface shipped ahead of any need; defer until a consumer
appears:

- **A public `extend_once` + a channel-bound `satisfied` + the formalized "primitive basis" +
  the own-loop recipe** (for exotic stop policies like "tolerate K=2 zeros, but only 3 times").
  No consumer has such a policy, and the recipe as sketched is **non-functional** anyway: a
  single-advance helper returning `{delta, ⊥}` cannot distinguish "early-completed" from
  "stalled" (the exact codomain defect that refuted the push-or-fail reframe), so it would need
  `peek_terminal` as a fourth primitive. `history` stays exported; `satisfied` and the
  single-advance step stay **internal** until something pulls them out.
- **The producer error-string override** (re-classify an `ERRORED` string as transient —
  CUDA-OOM vs NaN). mycooc classifies *outcomes*, not strings; add when a consumer classifies
  strings.

## Not doing

- **A `completed` guard on `relaunch_if_needed` / `ensure_served`.** An earlier draft proposed it
  to close a "hole." The hole is **never-bites** (unreachable via `ensure`, which guards
  `completed` first; a conformant worker resumes *forward* with no overlap — only a
  contract-violating worker reached by a direct call could diverge), and the guard is **wrong**
  for `ensure_served` — `specs/lazy-launch.md` documents re-waking a completed service run as
  accepted leased-demand behavior — and **redundant** for `relaunch_if_needed` (it would overload
  a liveness primitive with verdict policy that already lives in `ensure`). Direct
  `relaunch_if_needed` on a completed run is **caller-owned**.

## Evidence from the mycooc dogfood

mycooc's `_SubprocessProducer` hand-rolls the missing feature: `_run_one_chunk`'s
`on_killed=RESUME` / `max_resume_attempts` loop, and `_SyncHandle`'s synthesized
`preempted`-shaped terminal on a hard crash. Honest scope: killed-redrive + G2 + G3 let mycooc
delete the ~10-line RESUME-classification branch and the ~8-line terminal-synth — **not** the
bulk of `_run_one_chunk`, which is subprocess process-management (timeout / SIGKILL escalation /
SAVING-wait), explicitly out of the core's scope. (mycooc's `ensure_with_collision_retry` — the
claim-window race — is orthogonal and not absorbed here either.)

## Validation approach

**Not** bit-exact-testable (timing-dependent). Different axes:

- Property: re-driving a killed-but-checkpointed run reaches the same final state as an unkilled
  run at the same budget (idempotent under resilient re-driving). A mock producer that dies
  mid-chunk after N steps and resumes from checkpoint tests this deterministically.
- The G1 divergence test: a mock producer whose resume re-emits an overlapping step with a
  *different* value must yield take-the-latest, not a permanent `history` raise.
- The additivity test: with no producer opt-in, every existing `_FAILURES` test still raises
  (the seam changes nothing). The opt-in path is exercised separately.
