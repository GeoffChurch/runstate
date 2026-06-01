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

### 3. Single-spawn guard (idempotent relaunch)
Relaunch must spawn **iff no live episode**, atomically — double-spawn *corrupts*
(two workers resuming the same checkpoint interleave a garbage series), so the
guard must be correct, not best-effort.
- **CAS primitive:** `send(..., expected_seq=S)` appends iff the log's last `seq`
  is `S`, else rejects. Opinion-free (checks a `seq`, never the body); maps to NATS
  expected-last-seq / Kafka's idempotent producer. Memory: under its existing lock;
  SQLite: a transaction.
- **Idempotent `launch`:** optimistic loop — read; if a live episode exists, return
  its handle (no-op); else claim `launcher.launched` with `expected_seq=last`; if
  rejected (someone appended), re-read + re-check.
- **Orphans** (a `launched` with no `terminated` — a crash with no reaper):
  **reap-before-relaunch** — probe the dangling handle; if dead, write
  `launcher.terminated` (reuse the Watcher's probe/reap tier) so the "no live
  episode" check is accurate. Flow: **reap-dead → CAS-claim → spawn.**

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
