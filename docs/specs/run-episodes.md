# Spec: run-episodes (scoped — episode primitive + autonomous-extend)

**Status:** ready to implement (2026-06-01). Distilled from
`docs/backlog/run-episodes.md` + `design-v0.3-exploration.md` §11, via the
brainstorm. **In scope:** the shared *episode primitive* + the *autonomous-extend*
policy (what the memoizer + mycooc need). **Deferred (out of scope):** the
service/lifeline policy (lazy-launch-on-subscribe + reap-at-zero-subs) — a
separate consumer (on-demand metric/inference servers), built later on the same
primitive.

## Model

A `run_id` is a **durable log hosting multiple worker *episodes*.** Episode 1
attaches → `started … stopped`; later, episode 2 attaches to the *same* `run_id`
(same channel/log) → `started … stopped`, resuming from the run-keyed state it
left behind. Rests on a shipped property: `open_channel(run_id, root)` is
deterministic + durable + liveness-agnostic.

It dissolves two deferred items: **completed-but-extendable** (a `stopped` ends *an
episode*, not the *run*; extending = a new episode appending to the same log — no
re-openable terminal) and **idempotent relaunch** (§12.1; "relaunch iff not
already live" *is* the single-spawn guard).

## Decisions (settled in brainstorm)

### 1. Episode boundaries: implicit
An episode is a `lifecycle.started … stopped` span paired by `seq` — **no new
schema field.** Episodes are a read-side derivation over the existing log; the
substrate/conventions are unchanged. (Explicit episode-ids = a future refinement
if provenance/correlation ever needs them; no scoped consumer does — the memoizer
reads by run-absolute `step`, not by episode.)

### 2. Episode-aware liveness
`peek_terminal` and the Watcher's record tiers become episode-aware:
- the latest `lifecycle.stopped` is terminal **iff no `lifecycle.started` follows
  it by `seq`**; same for `launcher.terminated` vs `launcher.launched`. A
  `started`/`launched` after it ⟹ an episode is live ⟹ `None`.
- `RunStatus`/`RunResult` **shape unchanged** (`Running | RunResult`); "terminal"
  now means *the latest episode ended (extendable)*, not "the run is sealed."
- **No "done forever" concept.** "Idle, may-relaunch" and "finished its last
  episode" are *identical on the log*, inherently (doneness is a future relaunch
  decision). Which one it is = the **caller's policy**. ("Never touched again" is a
  retention/GC question — §12.9, deferred.)
- **Addendum (2026-06-09) — the drain-side mirror.** Cross-episode *control*
  follows the same follow-by-`seq` fold in the opposite direction: every control
  fact is live until its counter-record (`unsubscribe` rescinds a subscribe; the
  next `lifecycle.stopped` *discharges* a `control.stop`), so a resumed episode
  re-derives standing subscriptions but never replays an answered stop. Specced
  and shipped as [stop-discharge](stop-discharge.md); folded into design §6/§7.

### 3. Single-spawn guard — the worker self-claims its episode
Two workers for one `run_id` would corrupt the series (both resume the same
checkpoint, interleave writes), so a relaunch must not start a second *live*
episode. The guard lives in the **worker's attach**, not the launcher — because a
launcher lacks the worker's liveness handle until *after* it spawns (a subprocess's
child pid is post-spawn), whereas the worker always has its own handle at the moment
it matters. Putting the guard in the launcher creates a chicken-and-egg (claim needs
the handle, handle needs the spawn) and a new orphan class (claimed-but-not-yet-
started, reapable only via the launcher's liveness — which breaks fire-and-forget).

- **CAS primitive:** `send(..., expected_seq=S)` appends iff the log's last `seq` is
  `S`, else rejects. Opinion-free (checks a `seq`, never the body); maps to NATS
  expected-last-seq / Kafka's idempotent producer. Memory: under its existing lock;
  SQLite: one guarded statement, atomic by construction. (Normative concurrency
  contract now in design §4: atomic across handles and processes; `None` =
  provably lost; raise = indeterminate fault.)
- **Worker self-claim:** on attach the worker **CAS-claims its `lifecycle.started`**
  (append iff no live episode). **Win →** proceed (load state, run). **Lose** (a live
  episode already claimed) **→ exit immediately**, before loading state or emitting
  anything. The optimistic loop is at the worker: read → check no-live → CAS-claim
  `started` with `expected_seq=last`; if rejected, re-read + re-check (and if a live
  episode is now present, lose).
- **Why the worker, not the launcher:** the claim happens where the handle is
  naturally available, so there is **no new orphan class** (a claim *is* a real
  `started`; the only failure is started-then-crashed, which the existing liveness
  tiers already cover); it's **symmetric** across Thread/Local launchers; and it's
  **launcher-agnostic** — a worker spawned by ray / submitit / hydra / bare
  `python` is guarded too. The guard is in the *protocol*, not our launcher.
- **Launcher pre-check (best-effort, optional):** before spawning, a launcher may
  read the log and skip the spawn if a live episode is already visible — an
  optimization that avoids a wasted spawn in the common already-live case.
  Correctness never depends on it; the worker's claim is the guarantee.
- **Wasted spawn** *(amended by `specs/lazy-launch.md`: this analysis priced the spawn and forgot the funeral — the loser's reaped `terminated{0}` could forge the run's verdict, and an explicit `stopped()` lacked the loser guard; both fixed there)*: in the rare check-to-claim race the loser spawns, checks, exits —
  doing no *work* (it claims before acting). Cheap in the common case (the pre-check
  spawns nothing); instant even in the race if the worker claims *before* heavy
  imports (a documented guard-prologue pattern).
- **Live-episode test / orphans:** a live episode is the latest `lifecycle.started`
  with no following `stopped` and the worker's handle resolving alive (or heartbeat
  fresh); a started-then-crashed episode is reaped by the existing tiers (`resolve()`
  on the worker handle / heartbeat staleness / `launcher.terminated`).
  `launcher.launched` remains the launcher's external *observation* (the second
  viewpoint), not the claim.

### 4. Autonomous-extend contract
- **`run_id` excludes the step-target.** Hash the *trajectory-determining inputs
  minus `max_steps`*; the target is the *extend axis*, not identity (else
  `steps=100` and `steps=500` are different runs and nothing extends).
  **Precondition (workload-owned): the trajectory must be target-independent** —
  `loss[42]` is the same whether you asked for 100 or 500 steps. (mycooc already
  separates this: `_compute_config_hash` excludes the count; `_find_reusable_run(
  min_steps=N)` checks "reached ≥ N".)
- **Target passed at relaunch** — `N` is a *launch* parameter (the worker's loop
  bound), not a control message. `control.stop` stays for *early* stop.
- **Resume + run-absolute step = a worker convention** (not a runstate mechanism):
  the relaunched worker reads its `run_id`-keyed checkpoint, restores at step `k`,
  and continues `value.step` at `k, k+1, …`. runstate gives no directory.
- **`Worker.steps(start=k, total=N)`** affordance: run-absolute resume *with*
  correct lifecycle bookkeeping (`final_step` / commanded `stop_reason`), closing
  the `steps(total=N-k)` episode-relative footgun.
- **Memoizer composition** (the consumer — built in the *next* thread, not here):
  `ensure(run_id, config, up_to=N)` → read `loss[0:N]`; if latest logged step ≥
  `N−1`, reuse; else CAS-guarded-relaunch(target=`N`) → wait (episode-aware) → read.

## Deliverables
- **substrate:** `send(..., expected_seq=...)` CAS on the Channel surface +
  Memory + SQLite (both backends; conformance-tested).
- **liveness:** episode-aware `peek_terminal` (the `seq` follow-check).
- **watcher:** episode-aware record tiers (same follow-check); reap reused at launch.
- **launcher:** idempotent `launch` (CAS-claim loop + reap-dead-orphan); returns the
  existing handle when a live episode exists.
- **worker:** `steps(start=k, total=N)`.
- **docs:** the `run_id`-excludes-target + target-independence rule → into
  `docs/specs/run-id-recipe.md`; the worker-resume convention written up.

## Non-goals
- The service/lifeline policy (deferred — a separate consumer on the same primitive).
- The worker's checkpoint/resume *mechanism* (worker's job; runstate transports
  messages, not files/dirs).
- Explicit episode-ids; a "done/sealed" marker; retention/GC.

## Tests (TDD targets)
- **episode-aware `peek_terminal`:** `started…stopped…started` (live ep2) → `None`;
  `…stopped` with no following `started` → terminal. Same for
  `launched`/`terminated`. Parametrized over both backends.
- **CAS `send(expected_seq=)`:** appends at the matching last-`seq`, rejects on
  mismatch; both backends.
- **idempotent `launch`:** concurrent relaunch of one `run_id` → exactly one
  `launcher.launched` and one spawn; an already-live `run_id` → no-op + the existing
  handle.
- **reap-before-relaunch:** a dangling `launched` whose handle probes dead →
  `terminated` written → relaunch proceeds.
- **`steps(start=k, total=N)`:** emits `value.step` `k…N−1`; `final_step` and a
  commanded `stop_reason` are correct.
- **autonomous-extend (integration):** a resumable test worker runs `run_id` to
  `k`, checkpoints; relaunch with target `N` resumes (`steps(start=k)`) and extends;
  the log reads back as one `loss[0:N]` series (run-absolute step), and
  `peek_terminal` is episode-terminal only after ep2's `stopped`.

## ⚠ The caveat to surface loudly
Autonomous-extend is **correct only for target-independent trajectories.** A
`max_steps`-keyed schedule (e.g. cosine-decay-over-total) makes a different target
a *different run* — extending then reuses a wrong-trajectory prefix. Excluding the
target from `run_id` is necessary but **not sufficient**; the workload must *be*
target-independent. This is the one place extend can silently corrupt reuse — the
recipe doc must say so, and mycooc must confirm its schedule isn't `max_steps`-keyed.
