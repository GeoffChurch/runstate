# Spec: launcher-record identity — a launch names itself, so a death cannot forge a verdict

**Status:** SHIPPED 2026-07-14 (spec written 2026-07-11; the open §5 sub-question
was settled by the implementation spike it called for, and the spike changed two
things — see §8). Graduated from `../backlog/launcher-record-identity.md`.
**Severity (fixed):** wrong-verdict. `peek_terminal` — hence `Watcher.poll`,
`ensure`, `sweep` — read `completed` (or `killed`) **for a run that was alive and
beaconing**. `ensure` returned a truncated series *silently*; a forged `killed`
raised a spurious failure. Both forgeries were reproduced against the shipped
launchers, and both are now regression-pinned.

## 1. The problem

`launcher.launched` / `launcher.terminated` carried **no launch identity**, and
`peek_terminal`'s launcher tier paired the *latest* `terminated` against the
*latest* `launched`. That works only if records land in episode order — but a
**reap is a reader-side observation that can land arbitrarily late** relative to
the death it describes, and a claim-race loser's launch can be the newest one on
the log. Two reproduced forgeries:

- **LocalLauncher relaunch (`p2_reap_late.py`):** ep1 stops and lingers; ep2
  launches, claims, beacons; THEN ep1's reap writes `terminated` — after ep2's
  opener, so nothing "follows" it and ep1's stale verdict stood over the live
  ep2.
- **ThreadLauncher claim-loser (`p1_thread_flavor.py`):** two concurrent
  dispatchers both spawn; the loser loses the birth-CAS, its target returns
  cleanly, and the launcher wrote `Terminated(exited, 0)` — the loser's launch
  being the *latest*, the fold read its death as the run's.

## 2. Rationale — why `terminated` stays a record in `peek_terminal`

The tempting "maximal" fix — exile the reaped-death verdict to the Watcher,
leaving `peek_terminal` first-party (`stopped`) only — is **wrong**, and naming
why fixes the design. Two *orthogonal* axes were being conflated:

- **who wrote it** — first-party (the worker's own `stopped`) vs third-party
  (the reaper's `terminated`).
- **how it is known** — a durable **record** on the log vs live **inference**
  from process/clock state (handle probe, heartbeat staleness).

`launcher.terminated` is **third-party but a record** — durable, readable
post-hoc, third-hand. The `peek_terminal` / `Watcher` split is drawn on the
*second* axis, and it is drawn **correctly**. The Watcher's inference tiers
detect a process dying *now*; they **cannot reconstruct** that one died and was
reaped *then* — the manner of death lives only in the `terminated` record. A
bare-channel post-hoc reader (mycooc's cell inspection, `sweep`'s resume-skip,
the future viewer) has no handle and no clock; `peek_terminal`-over-the-record is
its only path, and it is a real, blessed use case.

Its only defects were that a third-party record could be **misattributed** and
**late**. Those are attribution and ordering problems, not layering problems —
and identity fixes exactly those two, nothing more.

## 3. The design

### (a) One correlation id per launch, on all three records

A launcher mints an id per launch and stamps it on the envelope's `request_id`
of **both** its records — `launched` and `terminated` — and the **worker
re-emits it on its `lifecycle.started`**. So a launch, the claim that answers
it, and the death that ends it all name the same thing.

`terminated` now asserts only *"**my launch** ended"* — first-party to the
launcher, about its own child — instead of the unknowable *"the run is dead."*
That de-conflation is the root-cause fix. It also buys **dead-log attribution**:
which episode a manner-of-death belongs to, recoverable no other way, forever.

The id reaches the worker **ambiently**, exactly as the run id does (`attach`):
`RUNSTATE_LAUNCH_ID` in the child's environment (cross-process; the portable,
interop half — another language's launcher sets the same variable), or a
ContextVar bound around the target (in-process; a thread launcher has no
environment to inject into, and its threads share one pid, hence one handle, so
nothing *else* could tell its launches apart). `vocabulary/launch.py` owns both.
The worker never interprets the id; it only re-emits it.

### (b) The verdict is anchored to the CLAIMED episode

A run's episode is its **claim** (`lifecycle.started`) — which is what
`live_episode` / `latest_episode` already anchor to. So the only death that can
speak for a run is *the death of the launch that claim answered*, found by id.
Both forgeries then die **by construction**, with no timing window: the stale
reap names the OLD launch, and the loser's death names a launch no episode ever
claimed.

Two silences are deliberate. A claim with **no** launch id (a hand-run worker —
nobody launched it) has no launcher record that speaks for it; the Watcher's
inference tiers still do. A claimed episode whose launch has not ended is,
simply, running. And if **nothing ever claimed**, the launcher's records stand
alone: that is the null-worker startup crash, whose `terminated` is its only
possible terminal.

The result is that **both tiers now obey one rule — a terminal stands until a new
episode claims** — where before, a mere *launch* voided a launcher terminal while
only a *claim* voided a `stopped`. The uniformity is a gain, not a coincidence:
the episode-boundary rule was always the claim.

### (c) An unidentified launcher record is malformed (launcher-**v0.3**)

The fold *depends* on the id, so the schema **pins** it: `launcher-v0.3` requires
a non-null `request_id` on both launcher records. (The spec's first draft claimed
"no schema change" — true about *legality*, wrong about *necessity*; see §8.) The
verdict plane raises `MalformedRecordError` on a death that names no launch,
rather than guess — guessing is precisely what forged verdicts. `lifecycle` is
**not** bumped: a `started`'s id is legitimately null for a hand-run worker, so
nothing there changes.

### (d) The reap discipline is DELETED

`LocalLauncher`'s reap was conditionally silent: it suppressed a claim-loser's
clean-exit `terminated` (foreign-claim-scoped, `launched`-seq-scoped for pid
reuse) precisely because an identity-less record would forge the winner's
verdict. With identity, **the writer stays honest and attribution becomes the
reader's job**: the loser's corpse lands on the log as what it is — that launch
ended, having never claimed — and the fold ignores it structurally. A whole
mechanism, its pid-reuse scoping, and its four tests are gone; startup-crash
visibility is now uniform rather than carved out. `reap()` itself stays
mandatory in a waker loop (zombies, `specs/lazy-launch.md`).

## 4. Where it lives

- `runstate/vocabulary/launch.py` — **new**: `LAUNCH_ID_ENV`, `new_launch_id`,
  `launch_scope`, `current_launch_id` (in-process binding beats an inherited
  environment: a ThreadLauncher *inside* a launched process stamps its own id).
- `runstate/launcher.py` — both launchers mint and stamp; `_ThreadHandle` /
  `_LocalHandle` expose `.launch_id`; `_reap` is unconditional; `_claimed_away`
  and `launched_seq` are gone.
- `runstate/worker.py` — the claim carries `request_id=current_launch_id()`.
- `runstate/observables.py` — `_launcher_terminal` (claim-anchored, id-paired),
  `_episode_stopped`, `_launch_id` (the strictness gate), and `_verdict_record`
  — the single record `peek_terminal` speaks for.
- `runstate/watcher.py` — `await_consumed`'s refused-by-death check uses
  `_verdict_record` instead of re-deriving "which terminal counts" from the
  latest terminal-topic envelope (an unrelated launch's death is not this
  request's refusal — the same forgery in miniature).
- `protocol/launcher-v0.3.schema.json` — the bump (v0.2 deleted).
- `scripts/migrate_launcher_v0_3.py` — committed, run to convergence, deleted.

## 5. Migration (no id-less path, ever)

Old logs carry null launcher `request_id`s, which the new fold **rejects
loudly**. A one-time offline pass stamps synthetic ids by positional pairing —
which is what the old fold assumed, but applied ONCE, over a whole *quiescent*
log rather than live against a moving one, which is what made it wrong. Per db:
each `launched` mints `mig-<seq>`; a `started` takes the newest unclaimed
launch's id (a `started` with no launch above it is hand-run and stays id-less);
a `terminated` takes the **oldest** un-terminated launch's id (FIFO — which is
what lands a *late* reap on the episode it actually belongs to). Quiescence-gated
(`live_episode`), idempotent, converge-over-passes — the `lifecycle-v0.3`
precedent. Concurrent launches in a historical log make the death pairing a
genuine guess (that ambiguity *is* the bug; no offline rule recovers what the
writer never recorded), so such dbs are reported by path.

## 6. Ripple

Design §6 (correlation vs visibility scoping); `specs/lazy-launch.md` (the reap
discipline section — now the honest-corpse rule); `specs/observables.md` (the
tier's fold); `specs/memoizer.md` (the concurrent-dispatch caution — **retired**:
`launch_producer` over `ThreadLauncher` is no longer single-dispatcher-only).

## 7. Regression pins

`tests/test_observables.py` — the late reap over a live episode; the late reap
attributed post-hoc on a cold log (both dead, ep1's reap landing last → ep2's
verdict); the claim-loser's clean exit; the hand-run worker's episode; a death
that names no launch (raises). `tests/test_thread_launcher.py` — the
**slow-winner** ordering (the winner's `started` lands after *both* launches, so
log position cannot attribute it — the case that killed every positional
formulation). `tests/test_service_worker.py` — the honest corpse and its
powerlessness (three), and the double-waker race end-to-end.
`tests/test_schema.py` — a launcher record must name its launch.

## 8. What the spike changed (and what that says)

The spec was written with an open sub-question (ThreadLauncher's shared-pid
attribution: id-to-the-worker vs source-suppression vs scope-it-away) and a
recommendation to settle it by implementation rather than more prose, "because
the fold formulation broke twice under pure reasoning." That was the right call
twice over:

1. **§5's lean (the id flows to the worker's `started`) won — and paid a
   dividend nobody predicted.** It did not merely *fix* ThreadLauncher; it made
   the verdict launcher-agnostic, which is what let the reap discipline be
   **deleted** rather than extended. The fix removed more machinery than it
   added.
2. **The "no schema change" claim was wrong**, and only writing the tests
   exposed it: a fixture holding an id-less launcher record kept passing while
   quietly producing *no verdict*. Legality (`request_id` was unconstrained) is
   not necessity (the fold now *depends* on it) — and a dependency the schema
   does not pin is exactly the unstated invariant this review exists to
   eliminate. Hence launcher-v0.3.

## Related

- The `ensure` no-progress guard is already **claim-aware** (`be5387b`) — that
  closed the *spurious-raise* half of the claim-window collision; this closed the
  *forged-verdict* half.
- Residual (all designs): a **false-live** handle (pid reuse; unresolvable
  cross-host handles read as live) is the documented cross-host claim-gate
  blindness (`../backlog/index.md`), out of scope here — and the reason the
  live-guard must never be `resolve()`-based (a probe voiding a *true* verdict is
  worse than the forgery).
