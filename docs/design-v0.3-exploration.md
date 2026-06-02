# runstate v0.3 — exploration (forward design)

**Status:** exploration / thinking document (2026-05-31). **Nothing here is
committed.** The v0.2 protocol (the topic-log substrate + the four conventions +
the orchestration helpers) is unchanged and remains authoritative
(`design-v0.2.md`). This captures a design thread about the **data plane**
(metric series, caching, lazy production) and a **typing realization** about
launchers vs workers. Genre: a decision trail, like `design-v0.2-exploration.md`
— not a spec.

## 0. The question that started it

"Take on the Hasher." That re-scoped — the Hasher is a `run_id()` *recipe*, not a
component (see `backlog/index.md`, "Layer 4") — and a deeper question surfaced:
what should **reuse** *feel* like? The intuition: ask for "100 losses" from an
experiment and get them, oblivious to whether 0, 35, or all 100 were already
computed. Pulling that thread produced the picture below. The punchline up front:
**almost none of it needs protocol changes** — it's a data-plane *pattern* over
the existing substrate, plus one small `value` schema tightening.

## 1. The layer map (recap; v0.2, shipped)

```
L0 Backend        SQLite file / memory list. Append, read-by-position. Knows nothing.
L1 Substrate      The Channel: a per-run append-only LOG of envelopes
                  {seq, topic, name?, request_id?, body}. Routes on the envelope;
                  never reads `body`. The opinion-free layer.
                  iface: send / read / latest ; open_channel(run_id, root, backend)
L2 Conventions    Meanings for some topics (substrate is blind to them), pinned by schemas:
                  control.* (subscribe/unsubscribe/stop) · subscription (the schedule
                  algebra) · lifecycle.* (started/heartbeat/stopped/nak) · launcher.*
                  (launched/terminated) · value {value, step?}
L3 Orchestration  Optional helpers that SPEAK the conventions: Launcher, Watcher, sweep.
                  [proposed] run_id() recipe, history/memoizer helper.
L4 Relational     [deferred] Store: cross-run index (enumeration, Run×Experiment membership).
```

Roles are conventions, not entities: **worker** (drains `control.*`, produces
`value`/`lifecycle.*`), **orchestrator** (writes `control.*`), **observer**
(reads only; invisible to the worker), **launcher** (spawns, brackets with
`launcher.*`). Three clocks: `seq` (log order), `step` (worker progress), wall-time.
The **heartbeat** is an always-on tick beacon `{step, consumed_seq}` doing triple
duty (liveness, progress, the "request landed" watermark).

## 2. Two worker archetypes & emission policy

- **autonomous / push** (training): intrinsic work, lives by its loop, produces
  regardless of who's watching.
- **on-demand / service**: no intrinsic work, produces only while subscribed;
  ref-counts subscriptions and dies at zero (lifelines).

Orthogonal emission policy: **eager** (the worker `send`s its metrics every step,
broadcast, independent of subscriptions — fits the training worker) vs
**subscription-gated** (today's reference loop: a `value` is emitted only when a
schedule fires). Eager logging makes the log a *complete* trace; gated logging
makes it sparse.

## 3. The log is the data plane — and the cache

The substrate is fully retained (no GC, §12.9) and every `value` is a retained
envelope `{value, step}`. So **the metric series is already persisted by the
substrate, in the channel.** Consequences:

- "Re-serving the first 100 losses" is a **log read** — worker-uninvolved, works
  even if the run is over and the worker is dead. It is not worker "replay."
- Under eager logging, the worker needn't separately store the *emitted* metrics
  (the log holds them). What it uniquely must persist is its **checkpoint**, to
  *extend*.
- **Position in the name-stream is run-absolute for free**: across episodes on the
  same `run_id` (same log), episode 2's first loss is the 336th — no resume-counter
  bookkeeping.

## 4. `value.step`: always present, implicit-or-explicit

Decided preference: schema fields are **present-but-nullable**, not omittable. So
`value.step` becomes `required`, `["integer","null"]` (matching `heartbeat`).
Semantics split by plane:

- **Control plane**: the worker *must* know its step — step-keyed schedules
  (`from/every/until`) are evaluated by the worker against its own step, and the
  heartbeat reports it as progress. Not automatable.
- **Data plane**: tagging emitted values *can* be implicit. `value.step = null` ⟹
  "no explicit step; number me by ordinal position" (automatic, run-absolute, also
  covers the genuinely stepless worker). `value.step = int` ⟹ "this is my semantic
  step" (self-describing, alignable across metrics and writers).

Lean: `null` is the default; a dense single-series worker gets run-absolute
indexing for free. **Explicit `int` is needed for**: sparse/irregular emission,
cross-metric alignment ("loss when accuracy was measured"), and multi-writer
(DDP — interleaved emitters that position alone would conflate).

## 5. The history helper = a memoizing launcher

The transparent "ask for N, get N" experience is a **Layer-3 helper** that is, in
general, a **memoizing launcher**:

- serve the requested range from the log (**cache hits**);
- on a **miss**, launch/resume a worker to produce the gap (which appends to the
  log, warming the cache for next time), then serve.

Eager logging is just cache **pre-warming**, not a correctness requirement:
- eager worker → warm cache → mostly reads, occasional extend;
- non-eager worker → cold cache → mostly launches-to-produce ("basically a worker
  wrapper"). Same interface, different hit-rate.

The helper interprets the request itself (the substrate is blind to the schedule
algebra — it sees only `topic`/`name`/`request_id`, never the `body`). A
purely-historical request never becomes a `control.subscribe` at all; the worker
is never notified.

Soundness caveat: reusing a cached prefix is valid only if the series is
**reproducible** (path-independent). A non-deterministic worker can still *produce*
N values, but they won't match a prior partial — nothing to reuse.

## 6. The cacher is (type-wise) a worker that wraps *computation*

A worker, from the channel's view, is "the thing that drains `control.*` and
produces `value`/`lifecycle`." A cacher does that, so it **is** a worker — one
that wraps user code C and a cache. Crucially it wraps **computation** and is the
**sole writer** (C hands values up; the cacher sends them) — true containment,
*because C is computation, not a channel-writer*. Two shapes of C:

- **iterator C** (training): a recurrence with per-step state, yields a value each
  step. It *steps* but needn't know its absolute index (the cacher/log assigns
  position). → a **series** memoizer ("step cacher").
- **function C** (`C(x) -> value`): genuinely stepless. The cacher memoizes by `x`,
  keyed by `run_id = h(x)`. → a **point** memoizer = the `run_id()` recipe's
  reuse-by-id.

## 7. Launcher vs worker = *viewpoint*, not entity

The realization: the launcher/worker split was never about separate processes.
It's about two **viewpoints**, both of which one process could emit:

- `lifecycle.*` = the workload's **self-report** (started, at step N, stopped
  because *X*).
- `launcher.*` = an **external** observer's report (launched; terminated with
  exit/signal).

Two things this split *encodes* (why it's fundamental, and why it must not be
merged even within one process):

1. **Causal independence.** `launcher.*` earns its keep only if its writer
   **outlives the workload** — a crash kills the self-report, so a *survivor* must
   say "it died." Forbidden topology: the workload emitting its *own*
   `launcher.terminated` (a real crash → silence; "absence ≠ dead" is the trap the
   external view closes). "One process writes both" is fine **iff** it's a
   supervisor writing about a *child*.
2. **Epistemic asymmetry.** The self knows *why* (a precise `reason`); an external
   observer knows only *that* it exited. A relay-wrapper around a black-box child
   can only *infer* lifecycle from exit codes — coarser — unless the child hands
   structured reasons up.

So "one process can be launcher + worker" is true (e.g. a wrapper that spawns a
child and writes on its behalf, ~today's `LocalLauncher` plus relaying the child's
output). It sits on a **containment spectrum**:

```
ThreadLauncher     in-process; target is contained computation (wrapper = sole writer)
relay-wrapper      subprocess, contained via relay (sole writer; child speaks a private protocol)  ← adapter for black-box code
LocalLauncher      subprocess; child is first-class (attaches, writes directly)
```

These trade coupling vs fidelity vs a relay hop, and coexist (additive). The
relay-wrapper's real value is as an **adapter** (onboard code that won't call
`attach()`).

## 8. Composition

User code composes as `memoizer(launcher(C))` — C innermost, launcher
(adapter/relay; fans a metric-dict into per-`name` value streams), memoizer
outermost; the consumer talks to the memoizer.

- **The layers compose *through the log*, not by piping values.** The launcher
  writes the log; the memoizer reads it; on a miss the memoizer triggers the
  launcher and reads the freshly-written values. The channel is the durable join —
  which is what makes the cache survive for the next request / a different consumer.
- **Order is forced.** `launcher(memoizer(C))` would put the cache *inside* the
  launched process → ephemeral, per-run, invisible to others → useless for
  cross-run reuse. Memoizer-outside keeps the cache in the durable log.
- The memoizer is parameterized by the run's **identity** (`run_id = h(config)` —
  the recipe), so it knows which log to read and what to launch on a miss.

## 9. What stays cooperative (the road not taken)

Everything is **cooperative and unenforced** — the orchestrator never removes a
worker; stopping is always the worker's decision; a worker can emit nonsense.
Making "request 100 → exactly 100" an *enforced* guarantee would require **owning
execution** (drive the worker as `f(state) -> (state', emissions)`, persist the
state ourselves, serve from our records). That is the function/state-machine
architecture runstate disavows ("transports messages, not processes"; no
`Orchestrator`; thin launchers). The memoizing-launcher is the *good* version: the
log is the memo table, so you get instant regurgitation (hit) + cooperative
extend (miss) **without** owning execution.

## 10. Impact on the wire conventions

**Minimal — two `value`-body refinements, the rest untouched.**
- `value.step`: tightened to present-but-nullable (a deliberate `value` convention
  version bump; `additionalProperties:false`).
- `value.t`: **added** — a present-nullable field (worker-birth-relative seconds, the
  real-time axis); the one additive change. Enables time-based replay once a reader uses it.

> **Superseded 2026-06-02 (the memoizer thread):** `value.t` is now **absolute
> wall-clock**, not worker-birth-relative — the canonical, episode-independent
> raw fact. Run-relative time is a *reader* projection (`history` subtracts the
> run epoch = earliest `lifecycle.started`). Per-episode origins reset on
> relaunch and would corrupt cross-episode time-replay. See
> `docs/specs/memoizer.md` Decision 7.
- `launcher.*` / `lifecycle.*`: **reaffirmed and re-grounded** (two viewpoints, not
  an artifact of process count). Must not be merged.
- No new topics. The substrate is untouched. Eager logging, the history/memoizer
  helper, the relay-wrapper, run-absolute steps — all are Layer-3 / worker-policy,
  carried by the existing envelopes.

## 11. Open questions / decisions

- **Eager vs lazy emission is the *worker's* behavior, not a runstate default** — it's
  emergent (a worker either `send`s every step or only on demand) and invisible until
  runtime, so there is nothing for runstate to "set." The memoizer is therefore
  **agnostic**: read what's in the log, produce what isn't, *regardless of why it's
  missing*. Eager-vs-lazy moves only the cache hit-rate, never correctness. The real
  (smaller) choices: ship a one-line eager-log convenience? how to document the tradeoff?
- **History as a library helper vs a protocol mandate** (the "convention vs guarantee"
  fork) — lean: a **standard Layer-3 helper the consumer composes**, not a protocol
  mandate. In a cooperative substrate nothing is enforceable anyway (you can't force a
  worker to honor `control.stop`, let alone replay), so a "guarantee" would be fiction +
  would need a mandatory broker (against opinion-free/no-daemon). The helper puts the
  read-first/produce-on-miss smarts in the *consumer's* own code (guaranteed-by-
  construction for that consumer) and depends on the worker only for bare production —
  so there is no extra obligation for a worker to violate.
- **Implicit (`null`) vs explicit step default** — lean: `null` (ordinal), explicit
  for alignment/sparse/multi-writer.
- **Naming** — "step cacher" (series) / "memoizer" / "materializer" (spans the
  produce-on-miss half). Bikeshed.
- **The history/memoizer helper = the active/passive unification** (the CHR
  active/passive-constraint parallel, realized). One schedule-shaped helper with a
  `produce_on_miss` policy: **passive** = read-only over the log (structurally
  *invisible* to the worker), **active** = read + produce-on-miss (drives the
  worker). Both share the `Subscription` evaluator (`vocabulary/schedule.py`),
  driven *live* by the worker vs *replayed* over logged value points by the reader
  — so observers get the same high-level vocabulary as orchestrators without
  reading the log raw. Whole-run granularity works now (active-miss = `run_id` +
  launch, ≡ `examples/reuse/`); the **fine-grained** version is *blocked on
  run-episodes* — its move is *extending a prefix*, so without resume-from-
  checkpoint a memoizer degrades to whole-run recompute. *Open:* one helper + the
  policy flag (lean) vs two entry points (`history`/`stream`) over the evaluator.
- **`value.t` (worker-birth-relative timestamp) — shipped.** Present-nullable on the
  `value` body; the reference worker stamps `now()−birth` (deterministic under an
  injected clock, real in prod). The real-time axis — non-reproducible by nature, so
  **step stays the reproducible/logical axis**; `run_id` untouched (time is output
  metadata). On the `value` body (a worker concept), not the envelope. The
  *coordinate* is in place; the homogeneous time-based *replay* it enables lands with
  the memoizer (the reader replaying the `Subscription` over it).

> **Superseded 2026-06-02 (the memoizer thread):** `value.t` is now **absolute
> wall-clock**, not worker-birth-relative — the canonical, episode-independent
> raw fact. Run-relative time is a *reader* projection (`history` subtracts the
> run epoch = earliest `lifecycle.started`). Per-episode origins reset on
> relaunch and would corrupt cross-episode time-replay. See
> `docs/specs/memoizer.md` Decision 7.
- **Relation to neighbours:** *run-episodes* supplies resume/extend (and
  run-absolute steps); the *`run_id()` recipe* supplies addressing; the *Store*
  supplies cross-run enumeration/membership (the structure content-addressing
  discards). This thread is the data-plane glue between them.
