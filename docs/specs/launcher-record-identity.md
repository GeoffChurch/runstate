# Spec: launcher-record identity — a late `terminated` must not forge a live run's verdict

**Status:** converging (2026-07-11). Core design settled through three red-team
passes + a design dialectic; one fold-mechanics sub-question (§5) is flagged for
an implementation spike against the existing reproductions rather than more
prose. Graduated from `../backlog/launcher-record-identity.md`.
**Severity:** wrong-verdict. `peek_terminal` — hence `Watcher.poll`, `ensure`,
`sweep` — reads `completed` (or `killed`) **for a run that is alive and
beaconing**. `ensure` returns a truncated series *silently*; a forged `killed`
raises a spurious failure. **Exposed today:** translation's concurrent
`drive_block` shells over `ThreadLauncher` share content-addressed rids by
design; a forged truncation would be cached under the content hash as the real
result — permanent, silent corruption. (mycooc's serial runner is not exposed;
the interim `../specs/memoizer.md` caution covers it.)

## 1. The problem

`launcher.launched` / `launcher.terminated` carry **no episode identity**, and
`peek_terminal`'s launcher tier pairs the *latest* `terminated` against the
*latest* `launched` (`_terminal_unless_followed`). That works only if records
land in episode order — but a **reap is a reader-side observation that can land
arbitrarily late** relative to the death it describes. Two reproduced forgeries
(scripts in the stage-3a red-team; `p2_reap_late.py`, `p1_thread_flavor.py`):

- **LocalLauncher relaunch:** ep1 stops/dies, ep2 launches + `started` +
  beacons; THEN ep1's reap writes `terminated` — *after* ep2's opener, so no
  opener "follows" it and the stale verdict stands over the live ep2.
- **ThreadLauncher claim-loser:** two concurrent dispatchers both spawn; the
  loser loses the birth-CAS, its target returns cleanly, and the launcher writes
  `Terminated(exited, 0)` unconditionally — the loser's launch is the *latest*
  launched, so the fold reads its death as the run's.

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
*second* axis (record vs inference), and it is drawn **correctly**:
`peek_terminal` reads durable records; the Watcher adds live inference. The
Watcher's inference tiers detect a process dying *now*; they **cannot
reconstruct** that one died and was reaped *then* — the manner of death lives
only in the `terminated` record. A bare-channel post-hoc reader (mycooc's cell
inspection, `sweep`'s resume-skip, the future viewer) has no handle and no
clock; `peek_terminal`-over-the-record is its only path, and it is a real,
blessed use case. So `terminated` belongs in `peek_terminal`.

Its only defects are that a third-party record can be **misattributed** (which
episode's death is it?) and **late** (landing after a relaunch). Those are
attribution and ordering problems — not a layering problem — and the fix
addresses exactly those two, nothing more.

## 3. Design — the converged core

### (a) Correlation via envelope `request_id` — no schema change

`request_id` is the envelope's correlation field (its stated job; the §4
lift-rule put it there for this). The launcher schema pins only `topic` + `body`
(verified: `request_id` is unconstrained, envelope-level, nullable) — so a
launcher stamping a fresh per-launch id on **both** its `launched` and its
`terminated` is **no schema change and no convention bump**. A `terminated` then
asserts only *"my launch ended"* (first-party to the launcher, tied to its own
`launched`) rather than the unknowable *"the run is dead."* This de-conflation
is the root-cause fix; it also carries **dead-log attribution** post-hoc (which
episode a manner-of-death belongs to — recoverable no other way).

### (b) Anchor the launcher verdict to the CLAIMED episode

The key correction from working the mechanics: the fold must anchor to the
latest **claimed** episode, **not** the latest `launched`. An episode is defined
by its *claim* (the worker's `started`), not its launch-*intent* — which is
exactly what `latest_episode` / `live_episode` already anchor to. The
claim-loser never claimed (no `started`), so anchoring to the claimed episode
**ignores the loser by construction**, with no timing window. The launcher-tier
verdict is: *the correlated `terminated` of the latest claimed episode's launch*
(or `None` → running if that launch has no `terminated`); a launch that crashed
before any claim (the null-worker startup crash the reap discipline deliberately
records) keeps its `terminated` as the verdict when no episode claimed at all.

### (c) Migration — no id-less dual path

Old logs carry `null` launcher `request_id`s. Per the no-compat doctrine, a
one-time offline pass stamps synthetic ids (positional launched↔terminated
pairing — trivial for the single-episode majority), same quiescence-gated,
idempotent shape as the `lifecycle-v0.3` hostname migration. The fold then
carries **only** the id path — never an `if id is None: fall back to positional`
accommodation.

## 4. What changed from the earlier A+B+C′ plan

Working the fold across all cases simplified it:

- **`ThreadLauncher` loser-suppression (old "B") — likely UNNEEDED.** The
  claimed-anchoring fold (3b) ignores the unclaimed loser's `terminated`
  structurally, so the launcher needs no source-side suppression — pending §5.
- **Generalized supersession (old "C′") — demoted to optional.** With
  claimed-anchoring, the launcher tier needs no "void if a liveness assertion
  follows" rule (anchoring to the claim already does it, window-free). C′ remains
  a *stopped-tier* robustness nicety (a foreign `heartbeat` after a `stopped`);
  fold it in only if the spike shows it earns its ~2 lines. It is **not**
  load-bearing, correcting the earlier framing.

## 5. The open sub-question (resolve by spike, not prose)

Anchoring to the claimed episode requires **attributing a `started` to the launch
that spawned it** — and that is clean for `LocalLauncher` (distinct child pids:
`started.handle == launched.handle`) but **genuinely hard for `ThreadLauncher`**,
where all in-process threads share `local://host/PID`, so neither handle nor log
*position* reliably attributes a `started` to its launch (a slow winner's
`started` can land *after* the loser's `launched`, breaking positional
attribution — verified by construction). Three options, to be adjudicated by
implementing each against the two reproduction scripts + the conformance suite:

1. **Correlation id flows to the worker's `started`.** The launcher passes its
   id to the worker (env / kwarg); the worker stamps `started.request_id` with
   it. Clean, uniform attribution across both launchers — at the cost of
   coupling the worker's self-report to the launcher's id (a mild breach of
   "the handle is worker-authored, launcher-independent").
2. **`ThreadLauncher` source-suppression (old B, revived only if 1 loses).** The
   loser suppresses its own `terminated`. Needs a claim-loss signal from the
   `Worker` (`_lost`) back to the launcher wrapper — the launcher currently wraps
   an opaque `target`, so this adds a coupling too.
3. **Scope `ThreadLauncher` to single-dispatch (status quo + the memoizer
   caution).** Cheapest, but leaves translation's exposure open — so only
   acceptable if 1 and 2 both prove worse.

Lean: **option 1** (uniform, launcher-agnostic fold; the "coupling" is just the
worker learning its own launch's correlation id, which is arguably *more*
honest than today's handle-only self-report) — but this is exactly the call a
spike settles fastest, because the reproductions immediately show which passes.

## 6. Scope / ripple

- `runstate/launcher.py` — both launchers stamp a per-launch `request_id` on
  `launched` + `terminated`; `LocalHandle`/`ThreadHandle` hold it; (option 1) the
  id reaches the worker.
- `runstate/observables.py` — the launcher tier of `peek_terminal` re-spelled to
  claimed-episode anchoring + id pairing; `_terminal_unless_followed` generalized
  or split.
- `runstate/worker.py` — (option 1 only) `started` carries the launch id.
- a migration script — committed → run to convergence on the consumer roots →
  deleted (the `lifecycle-v0.3` precedent).
- design §6 — one line: launcher correlation ids are **correlation-only**;
  visibility scoping stays a value-plane concern (the F7 axis note).
- **No schema change, no convention bump.**

## 7. Test cases (the reproductions ARE the adjudication)

Regression pins, each currently forging:

1. LocalLauncher relaunch, ep2 live → `peek_terminal` is None (running), not
   ep1's stale `completed`/`killed` (`p2_reap_late.py`).
2. LocalLauncher, both episodes dead, ep1's reap lands last → ep2's verdict, not
   ep1's late one (out-of-order post-hoc attribution).
3. ThreadLauncher claim-loser, winner live → running, not the loser's
   `completed` (`p1_thread_flavor.py`), **including** the slow-winner ordering
   (winner's `started` after the loser's `launched`).
4. Null-worker startup crash (launched, terminated, never started) → `errored`
   preserved (the reap discipline's deliberate record).
5. Single-episode clean + killed runs → unchanged verdicts (no regression).
6. Migration: an id-less historical log → stamped, reads the same correct
   verdict; idempotent re-run is a no-op.

## Related

- The `ensure` no-progress guard is already **claim-aware** (shipped `be5387b`)
  — that closed the *spurious-raise* half of the claim-window collision; this
  spec owns the *forged-verdict* half.
- Residual (all designs): a **false-live** handle (pid reuse; unresolvable
  cross-host handles read as live) is the documented cross-host claim-gate
  blindness (`../backlog/index.md`), out of scope here — and the reason the
  live-guard must never be `resolve()`-based (a probe voiding a *true* verdict is
  worse than the forgery).
