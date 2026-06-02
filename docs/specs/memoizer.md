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
surface (`peek_terminal` / `sweep` / `open_channel`); classes are reserved for
state-holders (`Watcher`'s cursors, `Worker`'s subscriptions). Promote to a
class only if/when the memoizer grows per-run state (a tailing cursor, a held
`Watcher`) — triangulated by that need, as the `Launcher` Protocol waited for
its second implementer.

- **`history(channel, name, schedule) -> list[dict]`** — the kernel. Depends on
  *only a channel* (worker-invisible; works on a dead run). Ticks a
  `Subscription` (`schedule.py`) over the logged `value` points for `name`,
  returning the bodies it fires on, in step order. The read-side dual of the
  worker's live `_service`.
- **`ensure(producer, name, *, up_to) -> list[dict]`** — read-first /
  produce-on-miss. Depends on a **producer** (Decision 5).

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
- `ensure(up_to=N)` relaunches-to-extend **iff progress < N**, then returns the
  series. Dense emitter → the two coincide. Sparse emitter → still correct:
  extend only if the trajectory truly hasn't reached N; return the sparse series.
  `ensure` guarantees "the trajectory reached N," **not** "N points exist" (you
  can't memoize a value the worker never computed). Reading progress off the
  `value` max-step instead would falsely relaunch a sparse run that finished past
  N but last *emitted* earlier.

### Decision 3 — `ensure` is read-first / produce-on-miss, waiting via the log
1. **Hit:** progress ≥ N → return
   `history(name, {"every":{"step":1},"until":{"step":N}})`. No worker touched.
2. **Miss:** `producer.extend(up_to=N)` (Decision 5) — trigger production toward
   N. The worker resumes from its `run_id`-keyed checkpoint via
   `steps(start=k, total=N)` and emits `k+1…N` (run-absolute) into the same log.
3. **Wait until reached or our episode ends:** track the launched episode's
   `LaunchHandle` (`extend` returns it) — `handle.is_alive()` is the exact,
   race-free signal that *our* episode finished (a log-seq heuristic trips over
   a prior episode's trailing `stopped`/`terminated` records). On a no-op extend
   (a foreign episode was already live) wait for that episode to go terminal
   (`peek_terminal`). The outcome is read from `peek_terminal`.
4. **Re-drive if short:** a clean stop *below* N → loop to step 2 (relaunch
   resumes from the higher checkpoint → converges). A **failure** outcome
   (`errored`/`killed`/`presumed_dead`) → stop and surface it (no relaunch storm).
5. Return the series.

### Decision 4 — `history` collapses by step but SURFACES divergent re-emission
A resume from a checkpoint behind the last *logged* step re-emits the overlap.
Under a reproducible worker the re-emitted values are identical (a no-op); under
a non-reproducible / non-target-independent one they differ — which is the exact
corruption the reuse precondition forbids. So `history` collapses to one body
per step but **raises on a same-step / differing-value collision** (the
reuse-soundness alarm) rather than silently taking last-wins. The clean
source-side fix is to checkpoint at the emission cadence (no overlap to
collapse); the divergence check is the safety net that turns a silent wrong-reuse
into a loud diagnostic. (Review B's sharpest catch.)

### Decision 5 — the producer seam (defer the named Protocol)
`ensure`'s second dependency is a **producer**: a structural, duck-typed handle
to one run that can be extended —
- `producer.channel` — the run's channel (to read progress + content);
- `producer.run_id` — for diagnostics (and an optional out-of-band `Watcher.observe`);
- `producer.extend(up_to)` — trigger production toward step `up_to` (idempotent;
  launch / relaunch-resume as needed; non-blocking — `ensure` owns the wait).
  **Returns** the launched episode's `LaunchHandle`, or `None` if it no-op'd (an
  episode was already live) — `ensure` tracks that handle's liveness and uses
  truthiness to tell whether it actually drove new work (the seam contract:
  `extend` returns truthy iff it triggered production).

Ship one factory, **`launch_producer(launcher, variant, *, target_key="up_to")`**,
for the common callable-worker case: its `extend(N)` injects the target into the
launch spec (`{**variant.launch_kwargs, target_key: N}`) and calls
`relaunch_if_needed` (Decision 6); its `channel` is
`launcher.open_channel(variant.run_id)`. **How the target reaches the worker is
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

### Decision 6 — idempotent relaunch is a free helper, not a launcher method
**`relaunch_if_needed(launcher, run_id, target, **launch_kwargs)`** —
launcher-agnostic: read `live_episode(channel)`; if a live episode exists, no-op
(don't double-spawn); else `launcher.launch(run_id, target, **launch_kwargs)`
(splatting the launcher-specific spec, exactly as `sweep` calls `launch`). It
knows nothing about `up_to` — the *producer* builds the target-N spec (Decision
5). Correctness rests on the **worker self-claim** (a check-to-spawn race just
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
is **single-run extend-to-N** over one log. Whole-run-no-extend (a dense emitter,
`up_to` = the run's natural length) is the degenerate overlap; the two share the
read-first idea but differ on granularity (set vs run) and the reuse predicate
(terminal-record vs progress ≥ N). No merge; a future Cartesian / extend-aware
sweep could compose `ensure` per cell.

## Deliverables
- **`runstate/memoizer.py`** — `history`, `ensure`, the producer seam
  (duck-typed) + `launch_producer`.
- **`runstate/launcher.py`** — `relaunch_if_needed` free helper (no Protocol
  change).
- **convention revision** — `value.t` → absolute wall-clock (worker +
  `payloads.py` docstring + design §10/§11 + §11 supersede note).
- **`runstate/__init__.py`** — export `history`, `ensure`, `launch_producer`,
  `relaunch_if_needed`.
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
  fired subset; **divergent re-emission of a step → raises**; benign *identical*
  re-emission → collapses silently; empty/short series; a `time_seconds` schedule
  replayed run-relative across two episodes (after the `value.t` fix). Both
  backends.
- `ensure`: full hit (progress ≥ N, no launch); cold miss (extend → reach N);
  partial hit + extend (0..k logged → resume → one 0..N series, run-absolute);
  clean-stop-below-N → re-drive; failure outcome → surfaced, no relaunch storm.
- `relaunch_if_needed`: live episode → no spawn; not-live → launch; concurrent →
  exactly one live episode (leans on the self-claim).
- `value.t` absolute: monotone wall-clock under an injected clock; the
  run-relative projection in `history` subtracts the run epoch.
- integration: the `examples/reuse` scenario via `ensure` + an
  extend-across-episodes.
