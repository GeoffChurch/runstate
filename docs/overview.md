# runstate — System Overview

A guided tour of the whole system: what it does, how you talk to it, and why each
layer and component exists. This is the doc to read first.

- **Rationale & rejected alternatives:** `design-v0.2.md` (the converged design).
- **Wire-authoritative source of truth:** the JSON Schema stack in `protocol/`.
- **Forward roadmap:** `backlog/synergy-map.md`.

*Maintained doc — keep it in sync as the layers evolve. Derived, never the
tiebreaker: on any disagreement, the schemas + `design-v0.2.md` win.*

---

## What runstate is

runstate is a **protocol for cooperative, bidirectional control of a long-running
scientific worker** — a training run, a simulation, a sweep job — plus a reference
Python implementation. The worker reports what it's doing, and an orchestrator
steers it *while it runs*: "subscribe me to the loss every 100 steps," "stop at
step 5000," "are you still alive?"

The contrast that defines it: a **tracker** (wandb, TensorBoard, MLflow) is a
one-way pipe — the worker pushes metrics out, you look at them later. runstate is
the **control plane** — a two-way channel where the orchestrator's requests flow
*in* and the worker's reports flow *out*, over one shared, durable record. (Run
both: runstate to steer, a tracker to plot.)

The whole system grows from a single idea: **one per-run log that everyone reads
and writes — an opinion-free substrate — with all meaning supplied by opt-in
conventions layered on top.**

## The mental model

One run = one **channel** = one append-only **log** of envelopes. Three kinds of
participant share it:

- a **worker** — the workload; consumes commands, produces reports;
- an **orchestrator** — produces commands, consumes reports;
- any number of **observers** — read-only, *invisible to the worker*.

Nobody talks point-to-point; everyone appends to, and reads from, the same log.
The log is durable and **worker-liveness-agnostic**: you can address a run and
queue commands for it *before any worker exists* — they wait in the log until a
worker attaches and drains them. A `run_id` maps deterministically to one channel,
and a channel can host multiple worker **episodes** (a `started…stopped`, then a
later `started…` resuming the same log) — which is how launch-on-demand,
relaunch-to-extend, and reconnect all fall out for free.

## How you interface with it

Three entry points, one per role. (The two canonical code snippets live in the
README's Quickstart, and `examples/minimal/` is the runnable, tested version —
code is kept in those two places only, so it can't drift.)

- **Worker side** — wrap your loop. `attach()` finds the run's channel (from
  the `RUNSTATE_*` env a launcher set); `with Worker(attach()) as w:` drains
  control, services subscriptions, and beacons lifecycle while you iterate
  `w.steps(...)` and `w.set(...)` the values you're willing to report.
- **Orchestrator side** — spawn, subscribe, watch: a `Launcher` opens the run's
  channel and spawns the worker; a `control.subscribe` requests a value series;
  `Watcher.wait(...)` streams events and folds liveness into the terminal
  `RunResult` verdict (the closed `outcome` enum).
- **Observer side** — open the channel and tail it read-only; the worker never
  knows (`open_channel(run_id, root=...)` + `read(after=cursor)`).

A participant that wants none of the helpers talks the protocol directly:
`send`/`read`/`latest` plus the conventions below.

## The layers

runstate is a stack of thin layers, each justified on its own. Bottom-up:

### Layer 0 — Backend (storage)

The storage engine behind the substrate. v0.2 ships two: **SQLite** (durable,
stdlib, zero-dependency — a single-file `log` table whose autoincrement `seq` is
already a sequenced, retained log) and **Memory** (a shared in-process list, for
in-proc orchestration and tests). Both pass one conformance suite; multi-host
backends (NATS JetStream, Kafka, Postgres) are future work.

### Layer 1 — The substrate: a per-run topic log

The opinion-free transport. It knows nothing about "worker," "command," or any
message shape. It carries **envelopes**:

```python
Envelope = { seq, topic, name?, request_id?, body }
```

- **`seq`** — the substrate-assigned position; the log's total order.
- **`topic`** — a *closed, protocol-owned* routing key (`control.stop`,
  `lifecycle.heartbeat`, `value`, …): a finite, inspectable vocabulary.
- **`name`** — an *open, app-owned* identifier (e.g. your metric `"loss"`).
- **`request_id`** — correlates a response to its request and scopes who sees it.
- **`body`** — an opaque dict; the substrate never parses it.

Why `topic` and `name` are *separate fields* and not one dotted key: they differ
in **ownership**. `topic` is closed and protocol-owned; `name` is open and yours.
Splitting them means a user metric called `"lifecycle"`
(`{topic: value, name: "lifecycle"}`) can never collide with the reserved
`lifecycle.*` topics — no sigils, no reserved-prefix rules. The rule for what earns
an envelope field (the **lift-rule**): a field leaves the opaque body iff the
substrate *routes, indexes, or filters* on it. Everything else stays in `body`.

**Channel surface:**

```python
send(body, *, topic, name=None, request_id=None, expected_seq=None) -> int | None
read(after=0, *, topics=None, name=None, request_ids=None, limit=None) -> list[Envelope]
latest(topic, name=None) -> Envelope | None
close()
```

- **Append + opaque body.** `send` appends and returns the new `seq`. The optional
  `expected_seq` makes it a **compare-and-append** (append only if the log head is
  still `expected_seq`; returns `None` if it lost) — the atomic primitive behind
  the single-spawn / episode self-claim.
- **Caller-owned cursors.** A reader passes back the last `seq` it saw; the
  substrate keeps *no* per-reader state and *no* registry of who is reading. N
  observers each independently see every matching envelope, and the whole log is
  retained (the substrate needn't know its readers to decide what to keep).
  Crash-resume is just "persist your `seq`."
- **`latest(topic, name)`** is a first-class primitive (backends optimize it:
  indexed `ORDER BY seq DESC LIMIT 1`) — how you read "the current value" of a
  register-like topic.

**Why a log and not a queue.** A queue consumes-once — the first reader to pull a
message removes it for everyone else, which breaks multi-observer. A *retained log
with caller-owned cursors* gives fan-out for free, makes the log the source of
truth (any current state is a **fold** over it), and lets a late observer or a
crashed-and-restarted consumer re-derive everything by re-reading.

### Layer 2 — The conventions

Opt-in typed shapes the substrate carries blind. This is where runstate becomes a
*control* protocol. Four conventions plus the user `value`; bodies are pinned by
JSON schemas (`additionalProperties: false`, independently versioned), mirrored as
frozen dataclasses in `runstate/vocabulary/`.

**(a) Cooperative-control — the one structural opinion.** The topic namespace is
*content-typed*, and each category has a fixed producer-role and consumer-set by
convention:

| topic | produced by | consumed by |
|---|---|---|
| `control.subscribe` / `control.unsubscribe` / `control.stop` | orchestrator¹ | worker |
| `lifecycle.*` (started / heartbeat / stopped / nak) | worker | observers |
| `launcher.*` (launched / terminated) | launcher | observers |
| `value` (user metrics; `name` says which) | worker | observers |

¹ with one completion case: the worker itself appends `control.unsubscribe`
when a registration *expires* — the worker completing the
subscribe/unsubscribe pair, exactly as its `stopped` completes a
`control.stop` (design §5).

"The worker is the consumer of control" is then an *emergent* fact (the worker is
whoever reads `control.>`), not a hardwired worker-vs-everyone axis. This is why
there is no `direction` flag: a worker-centric binary couldn't classify the
launcher→observer flow, but content-typed topics classify everything cleanly.

**(b) Subscription — the pull/push vocabulary.** A **subscription** is a standing
request from the orchestrator: "send me `name` on *this* schedule." The worker
services it and emits correlated `value` envelopes. The message *kind* is the
topic itself (`control.subscribe` vs `control.stop`), so there is no separate
`kind` field.

The schedule is a small **condition-algebra** over the worker's coordinates
`(step, time_seconds, count)`:

- a sub **fires at `from`** (default: now), **repeats every `every`** (absent ⟹
  one-shot), **expires at `until`**;
- each of `from`/`every`/`until` is a `Condition`: a threshold `{step: N}` /
  `{time_seconds: S}` / `{count: C}`, or `{any: [...]}` (whichever crosses first /
  OR), or `{all: [...]}` (whichever last / AND) — fully recursive;
- thresholds are `>=`, so every condition is **monotone**: once true, stays true.

`{}` = once, now · `{from: {step: 100}}` = once at step 100 ·
`{every: {step: 1}, until: {step: 5000}}` = every step to 5000. `control.stop` is
the same algebra with only a `from` (default = stop now). (`count` is admitted
only in `until` — you can expire after N fires, not schedule *on* fire-count;
anywhere else it would be a circular gate, and the worker refuses it.) The
algebra has **no normal form** on purpose: conditions are evaluated, never compared
or hashed, so canonicalizing redundant encodings would buy nothing.

Control facts live by one **pairing-by-`seq` rule** (design §7): a standing fact
is live until its counter-record *follows* it on the log. A pending **stop** is
discharged by the next `stopped` (so the decision is a latched *level* — a missed
`True` is recovered at the next safe point — and a resumed episode never replays
an answered stop). A **subscribe** is live until an unsubscribe-or-nak bearing
its `request_id` follows it — the **answer fold** — and a registration *expires*
the moment no future fire is possible (`until` met, one-shot consumed), with the
worker writing the expiry record itself. One more pairing, recordless: a
**time-referencing** subscribe is a *lease on one living worker* — it is voided
by the next worker's `started` (its countdown can't honestly survive the worker
that was counting), so a dead client's lease can never haunt a run, and bounds
meant to outlive workers are spelled in steps (`until: {step: N}`), not seconds.
So `observables.live_demand(channel)` reads "who still wants something"
straight off the log.

*Did my command land?* There is no per-request ack — and the read is
**answer-first**: a `nak` following your request resolves it immediately
(`{reason, message}`, `reason ∈ {malformed, unsatisfiable, unsupported}`,
dropped — never fatal to the worker); otherwise the worker's **consumption
watermark** (`consumed_seq`, on its heartbeat) passing your seq means
registered-and-accepted; and a terminal `stopped` arriving instead means the run
died under your request. `await_consumed()` is the blessed read, returning
exactly that answer space (`Nak` | `RunResult` | `None`).

**(c) Lifecycle — the worker's self-report** (`worker → observers`, reserved
`lifecycle.*`):

| topic | body | what it is / why |
|---|---|---|
| `started` | `{handle, hostname?, attached_at?}` | pushed on attach; the worker self-reports its liveness **handle** |
| `heartbeat` | `{step?, consumed_seq}` | a tick-driven **beacon** — see below |
| `stopped` | `{completed, error, final_step}` | the cooperative **dying breath** — see below |
| `nak` | `{reason, message}` | a refused control request (correlated by `request_id`) |

*What is a heartbeat?* A periodic **beacon** the worker pushes from inside its loop
(not on request). It does triple duty: **liveness** (if it goes stale, the worker
is hung or dead), **progress** (its `step` is advancing), and the **ack watermark**
(`consumed_seq`). Crucially it is *tick-driven* — emitted from the loop — so a hung
loop *stops* beaconing, which is exactly what makes staleness a real hang-detector.
(`step` is null for a "stepless" service worker.)

*What is `stopped`?* The worker's own cooperative halt. **Its existence on the log
= a clean, resumable stop** (a retained fact, not a transient signal).
`completed=True` is the worker's opt-in claim of intrinsic, permanent completion;
the default (`completed=False`, no error) projects to **preempted** (stopped but
resumable); a non-null `error` projects to **errored**. A worker that *crashes*
emits nothing — so *absence of `stopped` ≠ alive*, which is why liveness needs the
tiers below.

**(d) Launcher — the process-level view** (`launcher → observers`, reserved
`launcher.*`). Distinct from lifecycle: lifecycle is the worker's *semantic*
self-report; launcher is the *spawner's* report, and it catches deaths the worker
can't report itself.

| topic | body | what it is |
|---|---|---|
| `launched` | `{handle, status}` | spawn-intent + the liveness handle |
| `terminated` | `{reason: exited\|killed, exit_code?, signal?}` | the *manner* of death; only a `wait()`-ing parent can produce it |

*What is a handle?* A portable, scheme-tagged liveness token — `local://host/pid`
(and `slurm://…`, `k8s://…`, `ray://…` as backends land). Anyone can **resolve**
it (`os.kill(pid, 0)`, `squeue -j`) to a liveness *fact*, actor-independently — no
parent relationship, no PID file. The worker self-reports its handle on `started`;
the launcher also records it on `launched`.

**(e) `value` — user metrics.** `{value, step?, t?}`: your arbitrary
JSON-serializable datum, stamped with the worker's `step` and an absolute
wall-clock `t`. `name` (envelope) says which metric; `request_id` says which
subscription it answers. This is the one open-ended convention — the payload is
yours (a `json_default` hook coerces exotic types like numpy scalars on the way
out).

### Layer 3 — Orchestration helpers (opt-in)

Reference tooling built *on* the conventions. None of it is the protocol; a worker
or orchestrator can ignore it and compose `send`/`read`/`latest` directly.

- **`Worker`** — the reference loop, with **two drivers** for the two
  protocol-visible continuation policies: `steps(total)` runs to the launch
  contract's target (the autonomous worker), `serve()` runs while *leased
  demand* exists (the service worker — stops at zero subscriptions via the
  **careful death**, `retire()`, whose dying breath is compare-and-appended
  against the drained log so a racing subscribe is never orphaned; episodes
  are CAS-claimed at both ends). Each `tick()` drains control, services due
  subscriptions, beacons a heartbeat, and returns the stop level; `stop_pending`
  and `pinned` expose the two levels to hosts that own their loop; the `with`
  block emits the dying breath on exit. Service-ness is **opt-in by verb** —
  an autonomous run's life never depends on its observers.
- **The observables** (`runstate.observables`) — the stateless observer plane:
  pure folds log → view. `peek_terminal` (the terminal verdict), `live_episode`,
  `latest_episode` (the episode-boundary rule), `progress` (the step frontier),
  `value_series` (the per-(name, step) register projection), `live_demand`
  (unanswered subscribes). Observe statelessly here; watch statefully with the
  `Watcher` below.
- **`Launcher` / `LaunchHandle`** (Protocols) + **`ThreadLauncher`** (in-process;
  tests / single-process orchestration) and **`LocalLauncher`** (subprocess;
  injects `RUNSTATE_*` so the child's `attach()` meets the same log). A launcher
  does the irreducible job — spawn + emit handle — and returns; *watching/reaping
  is a separable role*.
- **`Watcher`** — the stateful failure detector. `add(handle)` or `observe(run_id,
  channel)` tracks a run; `poll()` returns a `RunStatus` (`Running | RunResult`);
  `wait()` blocks to a terminal verdict; `wait_all()` covers a set; `broadcast(name,
  schedule)` fans one subscription across all tracked runs under a shared
  `request_id` — the **cross-run barrier**, and the reason there is no Experiment
  class.
- **`sweep`** — sequential multi-run helper.

## Cross-cutting: liveness as a layered failure detector

"Is the run still alive?" has no single answer, so runstate stacks four tiers,
best to worst. None is substrate state — liveness is *emitted messages* +
*observer-side probing*, never a mutable TTL lease:

1. **Clean completion** — a `lifecycle.stopped` record exists.
2. **Reaped death** — a `launcher.terminated` record (the manner of death; needs a
   reaper).
3. **Probe the handle** — resolve it (`kill -0`) for the *fact* of death, even if
   the launcher is gone.
4. **Heartbeat staleness** — newest heartbeat older than a threshold ⟹ hung or
   crashed. The universal floor.

`peek_terminal(channel)` is the record-based verdict (tiers 1–2 — a pure, stateless
log read); the `Watcher` adds the inference tiers (3–4), which need state (arrival
times). All of it folds to one **`RunResult`**: a *closed* `outcome ∈ {completed,
preempted, errored, killed, presumed_dead}` plus a verbatim `reason`. There is
deliberately **no `success` boolean** — whether a clean preemption "succeeded" is a
policy the *consumer* owns (e.g. `sweep` fails on the bottom three), not something
the producer should bake in.

Tier 4 carries an irreducible tension: a worker in a legitimately long single step
(a 20-minute epoch) stops beaconing and *looks* dead. That's the **dead-vs-busy**
ambiguity — the threshold is a per-workload tuning, and a worker that can subdivide
a long step should beacon within it.

## Cross-cutting: the three clocks

Three independent notions of time, never conflated:

- **`seq`** — the substrate's transport order (per topic; global where a single
  sequencer exists).
- **`step`** — the worker's logical clock (a `body` field); what scheduling
  predicates evaluate against.
- **wall-clock** — real time (`value.t`, heartbeat staleness).

All scheduling fires in the worker's tick against `step` / wall-clock, never `seq`.
(`consumed_seq` in the heartbeat is a read *position* in the inbound control order
— not a fourth clock.)

## What runstate is *not*

- **Not an orchestrator framework** — no `Orchestrator.run()`. Spawn however you
  like (subprocess, submitit, ray, Hydra); talk via the protocol.
- **Not a tracker** — use wandb / TensorBoard / MLflow for plotting; runstate is
  the control-plane counterpart.
- **Not a workflow engine** — no DAG, no retries, no scheduler. Compose those at
  your application layer.

## Where to go next

- `design-v0.2.md` — the converged design with full rationale and rejected
  alternatives.
- `protocol/` — the JSON Schema stack (the wire-authoritative source of truth).
- `examples/minimal/` — a runnable end-to-end example
  (`python examples/minimal/driver.py`).
- `backlog/synergy-map.md` — the forward roadmap: the layers still to come (the
  on-demand producer, the relational **Store**, the visualization data plane).
