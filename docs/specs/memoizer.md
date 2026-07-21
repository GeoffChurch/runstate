# Spec: memoizer (reuse-by-`run_id` with a schedule-shaped read)

**Status:** implemented 2026-06-02 (plan: `docs/plans/2026-06-02-memoizer.md`). Distilled from
`docs/design-v0.3-exploration.md` §5–9/§11 and the run-episodes spec's
"memoizer composition", refined by the orthonormal-basis reviews (Review A on
the shipped conventions, Review B on this design). Builds on shipped
run-episodes (CAS `send(expected_seq=)`, episode-aware
`peek_terminal`/`live_episode`, `Worker.steps(start=)`, the worker self-claim).

## What it is

The transparent "ask for N losses, get N" experience over a content-addressed
`run_id`: serve what the log already has (cache hit), and on a miss
relaunch-to-extend a worker to produce the gap (run-episodes supplies
resume-from-checkpoint), waiting until the trajectory reaches the target. It is
the **active/passive unification** of §11, realized over the
`vocabulary/schedule.py` evaluator:

- **passive** — replay a `Subscription` over the *logged* `value` points
  (worker-invisible; works on a dead run);
- **active** — the same, but produce the missing suffix on a miss.

Net new *capability* is small: autonomous-extend already works with shipped
primitives (the run-episodes integration test extends across episodes via plain
`launch` + the worker self-claim + `steps(start=)`). This thread is **the
schedule-replay kernel (`history`) + the read-first/produce-on-miss ergonomics
(`ensure`)** over that substrate — so it stays deliberately lean.

## Scope

**In:** the *autonomous-extend* memoizer + the passive schedule-read.
**Out (deferred):** the *service* memoizer — forwarding a live
`control.subscribe` to an already-running on-demand worker. That is the
service/lifeline half run-episodes also deferred; it lands later as a second
**producer** behind the same `ensure` (Decision 5).

## Model — compose through the log

Layers join **through the log, not by piping values** (§8). The (re)launched
worker writes `value`/`lifecycle.*`; the reader reads/tails the log; on a miss
the memoizer triggers production and reads the freshly-written values. The
channel is the durable join — which makes the cache survive across processes and
serve the next consumer. Order is forced: memoization **outside** the launcher
(cache in the durable log), never inside (ephemeral, per-run, useless for reuse).

## Surface — two free functions + a producer seam

Free functions, not a class: the two operations have **different dependency
sets**, and a class would force the passive reader (an observer with only a
channel) to hold a producer it doesn't have. This matches the function-first L3
surface (`peek_terminal` / `sweep` / `create_channel`); classes are reserved for
state-holders (`Watcher`'s cursors, `Worker`'s subscriptions). Promote to a
class only if/when the memoizer grows per-run state (a tailing cursor, a held
`Watcher`) — triangulated by that need, as the `Launcher` Protocol waited for
its second implementer.

- **`history(channel, name, schedule) -> list[dict]`** — the kernel. Depends on
  *only a channel* (worker-invisible; works on a dead run). Ticks a
  `Subscription` (`schedule.py`) over the logged `value` points for `name`,
  returning the bodies it fires on, in step order. The read-side dual of the
  worker's live `_service`.
- **`ensure(producer, name, *, until) -> list[dict]`** — read-first /
  produce-on-miss. `until` is a Condition from the subscription algebra
  (`{"step":N} | {"time_seconds":S} | any/all`); `up_to=N` is `until={"step":N}`
  (the half-open window `[0,N)`). Depends on a **producer** (Decision 5).
  See `docs/specs/ensure-until-condition.md` for the full rationale.

### Decision 1 — `name` is a per-query argument
Not pinned at construction: the *extend* is run-level (one trajectory to step
N), the *content* read is per-`name`. One run produces many metrics; a single
extend warms them all, and any `name`'s series is then readable. (Closes the
single-metric-pin smell from Review B.)

### Decision 2 — progress vs content: two signals, not one
- **Progress** ("did the trajectory reach step N?") is read from the **dense**
  axis: `lifecycle.heartbeat.step` while live, `lifecycle.stopped.final_step` (or
  the reaped record) when terminal. The worker beats every tick regardless of
  emission, so progress is always known densely.
- **Content** ("what values exist for `name`") is the **value series**, at
  whatever density the worker emitted.
- `ensure(until={"step":N})` relaunches-to-extend **iff the window is not closed**
  (for a step condition: progress < N), then returns the series. Dense emitter →
  the two coincide. Sparse emitter → still correct: extend only if the trajectory
  truly hasn't reached the condition; return the sparse series. `ensure` guarantees
  "the trajectory closed the window," **not** "N points exist" (you can't memoize
  a value the worker never computed). Reading progress off the `value` max-step
  instead would falsely relaunch a sparse run that finished past N but last *emitted*
  earlier.

### Decision 3 — `ensure` is read-first / produce-on-miss, waiting via the log
1. **Hit:** window closed (`_satisfied(until)` — `satisfied()` over the condition-algebra,
   with `step = _progress+1` for the half-open `[0,N)` convention and time from the
   **consumer's own poll-clock**, injectable; see `docs/specs/ensure-until-condition.md`) **or**
   the latest terminal has `outcome == "completed"` (worker self-declared done) → return
   `history(name, {"every":{"step":1},"until":until})`. No worker touched.
   When (2) fires short of the target, `ensure` returns the available (shorter) trajectory —
   the honest answer: the producer declared it will not yield more. See
   `docs/specs/preempted-vs-completed.md` for the worker contract.
2. **Miss:** `producer.extend(until)` (Decision 5) — trigger production toward the
   condition. The worker resumes from its `run_id`-keyed checkpoint via
   `steps(start=k, total=N)` (self-bound, translating the condition to its own
   stop-bound) and emits (run-absolute) into the same log.
3. **Wait until reached or the episode ends:** track the handle `extend`
   returns — `handle.is_alive()` is the exact, race-free signal that the
   episode driving the work finished (a log-seq heuristic trips over a prior
   episode's trailing `stopped`/`terminated` records). When a foreign episode
   was already live, the handle is `foreign_episode(channel)` — its
   `is_alive()` re-reads `live_episode` every poll, so a recordless winner
   death breaks the wait instead of stranding it (`specs/store.md` Recipe 2;
   the old wait-for-a-terminal-*record* branch hung exactly there). The
   outcome is read from `peek_terminal`.
4. **Re-drive if preempted:** a `preempted` stop with the window still open → loop to
   step 2 (relaunch resumes from the higher checkpoint → converges). A `completed` stop
   short of the target → return the available trajectory (producer is done). A **failure**
   outcome (`errored`/`killed`/`presumed_dead`) → stop and surface it (no relaunch storm).
   The **no-progress guard** is axis-aware: raises only when step stalled AND
   `satisfied(until, step=progress+1, time_seconds=∞)` is False — a pure time
   condition never trips it; a step-stalled step condition always does.
5. Return the series.

### Decision 4 — `history` collapses by step, take-the-latest
A resume from a checkpoint behind the last *logged* step re-emits the overlap.
Under a reproducible worker the re-emitted values are identical (a no-op); under
a non-reproducible one they differ. `history` collapses to one body per step,
**taking the latest record by `seq`** — the as-resumed / continuing branch (the
same fold `value_series` uses for display).

An earlier version *raised* on a same-step / differing-value collision (a
"reuse-soundness alarm"). A code-grounded red-team retired it
(`../backlog/value-plane-divergence-resolution.md`): the raise was **sticky** on
the append-only log — it poisoned reuse for that run *forever*, including the
already-shipped `preempted`-redrive path — and take-the-latest is **sound for
every divergence `ensure` can produce** (`ensure` never re-drives a `completed`
run, so the only case where latest ≠ authoritative — a finished run re-run
divergently — is unreachable through `ensure`). The clean source-side fix
remains to checkpoint at the emission cadence (no overlap to collapse).

Crucially, **re-emission is never forced**: a worker is free to resume at
`last-logged + 1` and skip the overlap entirely (the clean path), in which case
the collapse does nothing. It is a *defensive free-rider* on whatever
overlap a lagging-checkpoint worker happens to produce — never a mandated
re-emission (which would be wasted recompute). So `history` polices nothing it
isn't handed; it imposes no cost on a worker that skips.

### Decision 5 — the producer seam (defer the named Protocol)
`ensure`'s second dependency is a **producer**: a structural, duck-typed handle
to one run that can be extended —
- `producer.channel` — the run's channel (to read progress + content);
- `producer.run_id` — for diagnostics (and an optional out-of-band `Watcher.observe`);
- `producer.extend(until)` — trigger production toward the condition `until` (idempotent;
  launch / relaunch-resume as needed; non-blocking — `ensure` owns the wait).
  **Returns a liveness handle for the work that will satisfy the demand**: the
  launched episode's `LaunchHandle`, or `foreign_episode(channel)` when an
  episode is already live (the Recipe-2 gate, `specs/store.md`). `None` is not
  in the contract — `ensure` raises `TypeError` on it, because a handle-less
  wait can only watch for terminal *records* and a recordless winner death
  then strands it forever. `ensure` distinguishes the two handle kinds for
  exactly one rule: the no-progress guard is **own-spawn-scoped** (a foreign
  episode ending without progress is re-driven, never raised on — we never
  launched, so there is no evidence a relaunch would spin).

Ship one factory, **`launch_producer(launcher, variant, *, target_key="up_to")`**,
for the common callable-worker case: its `extend({"step":N})` extracts the scalar
`N` and injects it into the launch spec under `target_key`; any other condition shape
(`time_seconds`, `any`/`all`) raises — bring your own producer for those. Its
gate is `relaunch_if_needed(...) or foreign_episode(channel)` (Decision 6 +
the store spec's Recipe 2); its `channel` is
`launcher.create_channel(variant.run_id)` (the producer births/extends the run).
**How the target reaches the worker is
launcher/workload-specific** — a kwarg for an in-process `ThreadLauncher`
callable, an env var / CLI arg for a `LocalLauncher` subprocess — so it lives in
the *producer* (the seam), which is exactly the extension point a subprocess or
ray/service user overrides with their own tiny producer. (Consistent with
"runstate transports messages, not processes": plumbing a loop-bound into a
process is the user's launch concern.) The **named `Producer` Protocol is
deferred** — the structural seam keeps downstream extensibility (a user can
supply a ray / service producer) without freezing a versioned Protocol; the
named Protocol lands with the deferred service worker, the second implementer
that triangulates its shape. This *is* the "inner worker-shaped thing" the
memoizer wraps; the wrapping is expressed by passing the producer to `ensure`,
not by a class holding it.

*(Correction, 2026-06-11 — `specs/derived-runs.md`: the predicted second
implementer arrived (mycooc's subprocess producer; a third with the analysis
producer) and the shape did NOT change — three implementers share the same
3-attribute seam. The named Protocol stays deferred on evidence: it would add
a name and no constraint. This corrects, rather than silently moves, the
"lands with the second implementer" promise above.)*

*(Second correction, same day — `specs/store.md`: the seam's ATTRIBUTE shape
held again, but the `extend` RETURN contract was revised — a liveness handle
always (own spawn or `foreign_episode(channel)`), never `None`, and the
no-progress guard became own-spawn-scoped. The old `None`-on-no-op spelling
hid a real hang (the recordless winner death) behind the record-only wait;
this paragraph's earlier truthy-iff-drove clause is gone with it.)*

*(Concurrency caution, 2026-07-10 — **RETIRED 2026-07-14**, as promised, by
`./launcher-record-identity.md` shipping. It read: under CONCURRENT dispatchers a
claim-race loser's `ThreadLauncher` runner writes `Terminated(exited, 0)`
unconditionally, which `peek_terminal` read as the live run's `completed` —
`ensure` then returned a truncated series with no error; so `launch_producer`
over `ThreadLauncher` was single-dispatcher-only. Launcher records now name their
launch (launcher-v0.3) and the verdict is anchored to the claimed episode, so the
loser's death speaks for nobody: **concurrent dispatch over `ThreadLauncher` is
supported**, and pinned by the slow-winner test.)*

### Decision 6 — idempotent relaunch is a free helper, not a launcher method
**`relaunch_if_needed(launcher, run_id, target, **launch_kwargs)`** —
launcher-agnostic: read `live_episode(channel)`; if a live episode exists, no-op
(don't double-spawn); else `launcher.launch(run_id, target, **launch_kwargs)`
(splatting the launcher-specific spec, exactly as `sweep` calls `launch`). It
knows nothing about `until` — the *producer* translates the condition to a target
spec (Decision 5). Correctness rests on the **worker self-claim** (a check-to-spawn race just
wastes a spawn that exits before acting); this helper is the optimization (no-op
when already live). It is **not** a `Launcher` Protocol method — that would force
every downstream launcher (submitit / ray / k8s) to implement a second verb for
what is pure composition over `launch` + a log read, and the launcher stays
target-opaque. A dangling dead orphan needs no explicit reap: the
worker self-claim ignores it (its handle resolves dead) and episode-aware
`peek_terminal` is unaffected; `Watcher.poll` reaps it for cleanliness if the run
is tracked. (Honors the "let the check happen before launch" instinct without
the Protocol burden; supersedes the run-episodes "idempotent `launch`"
deliverable, which never shipped.)

### Decision 7 — `value.t` → absolute wall-clock; the reader projects run-relative
The memoizer is `value.t`'s first real consumer (§10/§11). Its shipped semantics
— "seconds since worker birth" — are **per-episode** (reset on relaunch), so
cross-episode time-replay is wrong. Revise the **convention semantics** to
**absolute wall-clock** — the canonical, opinion-free raw fact (matching the
heartbeat embedding no timestamp and letting the reader use its own clock, §7;
and "views are projections of the log"). **Run-relative time is a reader
projection:** `history`'s time-based replay subtracts the run epoch (earliest
`lifecycle.started`) internally, so a "loss within the first 600 s" query works
without the user touching absolute time or piercing the abstraction. Semantic
revision only (worker impl + §10/§11 prose + `payloads.py` docstring + a
*supersede* note on the §11 "shipped" entry); the JSON schema is `number|null`
already → no `additionalProperties` bump. Assumes episodes share a comparable
wall-clock (true same-host; cross-host clock skew is a caveat).

## Relationship to `sweep`
`sweep(resume=True)` is **set-level** run-or-skip over independent runs and never
*extends* (it skips any run with a terminal record, else launches whole). `ensure`
is **single-run extend-to-condition** over one log. Whole-run-no-extend (a dense
emitter, `until` matching the run's natural length) is the degenerate overlap; the
two share the read-first idea but differ on granularity (set vs run) and the reuse
predicate (terminal-record vs window-closed). No merge; a future Cartesian /
extend-aware sweep could compose `ensure` per cell.

## Design note: the memoizer is thin; the worker owns the structure

A recurring confusion, headed off here. The memoizer holds **no** knowledge of
the sequence/recurrence structure. Its irreducible job is: read the log to see
what's present, compute the missing indices, ask the worker to produce them,
wait, read back. **The worker owns all structure-exploitation** — resume, the
recurrence, checkpointing — which is why runstate "transports messages, not
processes." The only thing the worker *can't* own is *what's already cached* (it
doesn't read the log — it produces; the memoizer reads). Clean division:
**memoizer owns cache + miss-detection; worker owns production + structure.**

- **The trap:** "a value-cache reuses values, not work" holds only for a
  *pure/stateless* worker. A real sequence worker is *stateful-resumable* (keeps
  / checkpoints `state[k]`), so it reuses its **own** work — producing step 100
  after 99 resumes from kept state (O(1)), not O(100). So we must not re-derive
  the structure on the memoizer side; the worker already exploits it.
- **`ensure(until={"step":N})` is sugar.** The general request is "ensure the log
  holds the indices `I`." For a *sequence* worker, `I` is a contiguous prefix that
  compresses to one condition `{"step":N}`, and the worker self-advances — hence
  `until={"step":N}`. A time condition `{"time_seconds":S}` drives the worker for a
  wall-clock budget (the worker self-bounds and emits `preempted`; `ensure` re-drives
  until `_elapsed >= S` on the consumer poll-clock). For a *function* worker (inference
  / on-demand eval), `I` is an arbitrary, externally-supplied key-set the worker can't
  self-enumerate, so the sugar doesn't apply — but the same `ensure(I)` does. The
  sequence-vs-function (and autonomous-vs-service) distinction lives **entirely in
  the worker's production strategy** (advance-a-loop vs evaluate-a-key;
  launch-with-target vs subscribe-and-serve) — *invisible to the memoizer*. There
  is **one** thin memoizer, not two; the deferred function/service case is the
  same memoizer pointed at a different-strategy worker, not parallel machinery.
  (The `from`/`every` emission filter is the next layer of the index algebra:
  `docs/backlog/memoizer-index-algebra.md`.)

## Deliverables
- **`runstate/memoizer.py`** — `history`, `ensure`, the producer seam
  (duck-typed) + `launch_producer` (+ `foreign_episode`, added 2026-06-11 by
  `specs/store.md`).
- **`runstate/launcher.py`** — `relaunch_if_needed` free helper (no Protocol
  change).
- **convention revision** — `value.t` → absolute wall-clock (worker +
  `payloads.py` docstring + design §10/§11 + §11 supersede note).
- **`runstate/__init__.py`** — export `history`, `ensure`, `launch_producer`,
  `relaunch_if_needed` (+ `foreign_episode`).
- **example** — `examples/reuse/` re-expressed via `ensure` (whole-run hit) + an
  extend-across-episodes demo.
- **docs** — this spec; cross-ref `run-id-recipe.md`.

## Non-goals
- The service/lifeline memoizer (live-subscription forwarding) — deferred.
- The worker's checkpoint/resume *mechanism* (worker's job; runstate transports
  messages, not files/dirs).
- A named `Producer` Protocol (Decision 5) and a `Memoizer` class (both deferred
  until a second producer / per-run state exists).
- Cross-metric alignment ("loss at accuracy's steps") — `history` is
  single-`name` by design; a user joins two reads.
- Time-based replay beyond what absolute `value.t` + the run-epoch projection
  gives.

## Caveats (documented, not enforced)
- **Reproducible + target-independent trajectory** — the loud caveat in
  `run-id-recipe.md`; reuse of a prefix is valid only if `loss[42]` is the same
  regardless of the target. Decision 4's divergence-raise is the alarm when it is
  violated.
- **Checkpoint cadence** — checkpoint at the emission cadence to avoid a
  re-emitted overlap; `history`'s collapse + divergence-check is the safety net.
- **Dense-series assumption for the hit path** — Decision 2; sparse emission is
  handled via the progress signal, returning the sparse series.
- **Clock comparability** — the run-relative projection (Decision 7) assumes
  episodes share a comparable wall-clock.

## Tests (TDD targets)
- `history`: `every`/`from`/`until` replay over a fixed logged series → correct
  fired subset; **divergent re-emission of a step → take-the-latest (highest-`seq`
  wins)**; benign *identical* re-emission → collapses; empty/short series; a `time_seconds` schedule
  replayed run-relative across two episodes (after the `value.t` fix). All
  backends.
- `ensure`: full hit (window closed, no launch); cold miss (extend → window closes);
  partial hit + extend (0..k logged → resume → one continuous series, run-absolute);
  `preempted`-stop below target → re-drive; `completed`-short-of-target → return
  available trajectory without re-driving (read-first and post-drive paths); failure
  outcome → surfaced, no relaunch storm; time milestone satisfies via poll-clock
  (not `value.t`); axis-aware no-progress guard; count condition rejected at entry.
- `relaunch_if_needed`: live episode → no spawn; not-live → launch; concurrent →
  exactly one live episode (leans on the self-claim).
- `value.t` absolute: monotone wall-clock under an injected clock; the
  run-relative projection in `history` subtracts the run epoch.
- integration: the `examples/reuse` scenario via `ensure` + an
  extend-across-episodes.
