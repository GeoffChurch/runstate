# runstate — System Overview

A guided tour of the whole system: what it does, how you talk to it, and why each
layer and component exists. This is the doc to read first.

- **Rationale & rejected alternatives:** `design-v0.2.md` (the converged design).
- **Log format 0.4.0, values committed to a lineage of nodes:**
  `specs/commits-and-lineage.md`.
- **Wire-authoritative source of truth:** the JSON Schema stack in `protocol/`.
- **Forward roadmap:** `backlog/synergy-map.md`.

*Maintained doc — keep it in sync as the layers evolve. Derived, never the
tiebreaker: on any disagreement, the schemas, `design-v0.2.md` and
`specs/commits-and-lineage.md` win.*

---

## What runstate is

runstate is a **protocol for cooperative, bidirectional control of a long-running
scientific worker** — a training run, a simulation, a sweep job — plus a reference
Python implementation. The worker reports what it's doing, and an orchestrator
steers it *while it runs*: "sample the loss every 100 iterations," "stop after
5000 iterations," "are you still alive?"

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
and a channel can host many worker **claims** (a `started…stopped`, then a later
`started…` continuing the same log) — which is how launch-on-demand,
relaunch-to-extend, and reconnect all fall out for free.

Within the log, a worker's values form a **lineage**: each iteration ends in a
**commit**, a node that names the node it extends and the values it commits, so
a run's history is a chain of nodes from a **head** back to the root. A claim
that resumes from an older node, or rewinds to one, starts a branch; every read
follows one lineage, so a displaced worker or a recomputed iteration can never
splice a series (`specs/commits-and-lineage.md` §2).

## How you interface with it

Three entry points, one per role. (The canonical code lives in
[`guide.md`](guide.md), whose blocks a test runs, and in `examples/`, which a
test runs too — so neither can drift.)

- **Worker side** — wrap your loop. `current_channel()` finds the run's channel
  (from the `RUNSTATE_*` env a launcher set); `with Worker(channel,
  resume_from=..., sources=..., save=...) as w:` claims the run, drains control,
  answers subscriptions and writes the lifecycle records while you iterate
  `w.steps(until)`, which commits each iteration. Values are reported by kind:
  `w.emit(...)` writes an element of an **eager stream** now, committed by the
  iteration (the series `ensure` and the lineage reads count), and `w.set(...)`
  updates a declared **source**, sampled only at the nodes where a subscription
  fires.
- **Orchestrator side** — spawn, subscribe, watch: a `Launcher` opens the run's
  channel and spawns the worker; a `control.subscribe` asks for a source's
  samples; `Watcher.wait(...)` streams events and folds liveness into the
  terminal `RunResult` verdict (a closed set of `outcomes`, and the `tier` it
  rests on).
- **Observer side** — attach to the existing channel and tail it read-only; the
  worker never knows (`attach_channel(run_id, root=...)` — which raises
  `RunNotFound` rather than fabricating a run, never mutates it — +
  `read(after=cursor)`, or the lineage reads by head).

A participant that wants none of the helpers talks the protocol directly:
`send`/`read`/`latest` plus the conventions below.

## The layers

runstate is a stack of thin layers, each justified on its own. Bottom-up:

### Layer 0 — Backend (storage)

The storage engine behind the substrate. v0.2 shipped two — **SQLite** (durable,
stdlib, zero-dependency — a single-file `log` table whose autoincrement `seq` is
already a sequenced, retained log) and **Memory** (a shared in-process list, for
in-proc orchestration and tests) — and the cross-host **Postgres** backend has since
shipped (`specs/channel-postgres.md`). All pass one conformance suite; other
off-the-shelf-log backends (NATS JetStream, Kafka) remain future work.

### Layer 1 — The substrate: a per-run topic log

The opinion-free transport. It knows nothing about "worker," "command," or any
message shape. It carries **envelopes**:

```python
Envelope = { seq, topic, name?, request_id?, body }
```

- **`seq`** — the substrate-assigned position; the log's total order.
- **`topic`** — a *closed, protocol-owned* routing key (`control.stop`,
  `lifecycle.commit`, `value`, …): a finite, inspectable vocabulary.
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
last_seq() -> int
close()
```

- **Append + opaque body.** `send` appends and returns the new `seq`. The optional
  `expected_seq` makes it a **compare-and-append** (append only if the log head is
  still `expected_seq`; returns `None` if it lost) — the atomic primitive behind
  the single-spawn / claim CAS.
- **Caller-owned cursors.** A reader passes back the last `seq` it saw; the
  substrate keeps *no* per-reader state and *no* registry of who is reading. N
  observers each independently see every matching envelope, and the whole log is
  retained (the substrate needn't know its readers to decide what to keep).
  Crash-resume is just "persist your `seq`."
- **`latest(topic, name)`** is a first-class primitive (backends optimize it:
  indexed `ORDER BY seq DESC LIMIT 1`) — how you read "the current value" of a
  register-like topic.
- **`last_seq()`** is the CAS's read half, admitted on the **op-admission principle**:
  *the surface must be readable in every coordinate it requires callers to assert.*
  `expected_seq` makes every claimant assert the head, so the head must be readable —
  O(1) on every backend (`len(log)`; `MAX(seq)` on the key). Its consumers: CAS
  claimants (the worker's head-first attach) and an incremental reader's
  has-anything-new watermark. `count(topic)` / `first()` pass no such test — nobody is
  required to assert them — which is why the surface is **five** ops, not six.

**Why a log and not a queue.** A queue consumes-once — the first reader to pull a
message removes it for everyone else, which breaks multi-observer. A *retained log
with caller-owned cursors* gives fan-out for free, makes the log the source of
truth (any current state is a **fold** over it), and lets a late observer or a
crashed-and-restarted consumer re-derive everything by re-reading.

### Layer 2 — The conventions

Opt-in typed shapes the substrate carries blind. This is where runstate becomes a
*control* protocol. Four conventions plus the user `value`; bodies are pinned by
JSON schemas (`additionalProperties: false`, independently versioned:
`subscription`-`v0.4`, `lifecycle`-`v0.6`, `supervisor`-`v0.5`, `value`-`v0.3`
for log format 0.4.0), mirrored as frozen dataclasses in `runstate/vocabulary/`.

**(a) Cooperative-control — the one structural opinion.** The topic namespace is
*content-typed*, and each category has a fixed producer-role and consumer-set by
convention:

| topic | produced by | consumed by |
|---|---|---|
| `control.subscribe` / `control.stop` / `control.withdraw` | askers (orchestrators, observers) | worker |
| `lifecycle.*` (started / commit / heartbeat / rewind / stopped / bound / nak) | worker | observers |
| `supervisor.*` (launched / terminated / died) | launcher; `died` by any party with evidence of a death | observers |
| `value` (user metrics; `name` says which) | worker | observers |

The control category is written by askers only. The worker records what it did
with a request in its own records: a node's `fired` and `expired`, an exit's
`honored`, a `bound`, a `nak` (design §5).

"The worker is the consumer of control" is then an *emergent* fact (the worker is
whoever reads `control.>`), not a hardwired worker-vs-everyone axis. This is why
there is no `direction` flag: a worker-centric binary couldn't classify the
supervisor→observer flow, but content-typed topics classify everything cleanly.

**(b) Subscription — the pull/push vocabulary.** A **subscription** is a standing
request from an asker: "sample source `name` on *this* schedule." Its envelope
`name` is the **source** it targets (a name the worker declared on its claim);
each firing writes one sample of that source, which the node that fired it
commits, so the answers are a lineage read. A subscription with `name: null` is
**pure demand**: it targets nothing, never fires, and only keeps a `serve()`
worker alive, so its body takes only an `until`. The message *kind* is the topic
itself (`control.subscribe` vs `control.stop` vs `control.withdraw`), so there is
no separate `kind` field.

The schedule is a small **condition-algebra** over **counts along the lineage**
plus the claim's clock:

- a sub **fires at `from`** (default: the next node), **repeats every `every`**
  (absent ⟹ one-shot), **expires at `until`**;
- each of `from`/`every`/`until` is a `Condition`: an atom — `{commits: n}` (the
  lineage's iterations), `{stream: x, n}` (the lineage's elements of the eager
  stream x), `{time_seconds: s}`, or `{count: n}` (its own firings, `until` only)
  — or `{any: [...]}` (whichever holds first / OR), or `{all: [...]}` (whichever
  last / AND) — fully recursive;
- thresholds are `>=` on counts, so every condition is **monotone** along a
  lineage: once true, stays true; `every` is the delta since the last firing.

`{}` = once, at the next node · `{from: {stream: "loss", n: 100}}` = once, when
the lineage holds 100 losses · `{every: {commits: 10}, until: {commits: 5000}}` =
every 10th commit to 5000. Subscriptions are evaluated at each commit and at an
exit that did not discard its iteration, never at a heartbeat. `control.stop` is
the same algebra with only a `from` (default = stop now), evaluated at nodes and
heartbeats. The algebra has **no normal form** on purpose: conditions are
evaluated, never compared or hashed, so canonicalizing redundant encodings would
buy nothing.

**Lease or durable** — the atoms decide a subscription's class, and the class its
scope. A schedule with any `time_seconds` or `count` atom is a **lease**: the
worker that drains it binds it to its own claim (`lifecycle.bound`), its clock
and fire count are that claim's, and it is void once that claim has ended (its
countdown can't honestly survive the worker that was counting, so a dead client's
lease can never haunt a later claim). Every other schedule, whose atoms are all
lineage counts, is **durable**: it lives per lineage, outlives every claim, and is
re-armed on a lineage that has not expired it. So bounds meant to outlive workers
are spelled in commits or stream counts, not seconds.

Control facts live by one rule, **reference by name** (design §7): a record that
answers, ends or concerns another names it, by an id that is never reused, so the
log reads the same whatever order its records arrived in. Each request has one of
**three scopes**:

- **a stop**, by name, for the whole log (it is about the process): pending until
  an exit lists its `request_id` in `honored` — the worker names every stop
  pending when it stops — or a `control.withdraw` or `nak` names it. The decision
  is a latched *level*, so a missed `True` is recovered at the next safe point,
  and a stop honoured on one branch is not re-armed on another;
- **a lease**, per claim: spent by a withdraw, a `nak` or any node's `expired`
  naming it, and void once its claim has ended;
- **a durable subscription**, per lineage: spent by a withdraw or a `nak`, and
  expired by an `expired` on the head's lineage only.

Once any answer names an id the id is *spent*: a request reusing it is dead on
arrival, so a replacement takes a fresh id. A registration *expires* at the node
where no future fire is possible (`until` met, one-shot fired), which names it in
its `expired`. So `observables.live_demand(channel)` (and its held form,
`LiveDemand`) reads "who still wants something" straight off the log.

*Did my command land?* There is no per-request ack — and the read is
**answer-first**: a `nak` naming your request resolves it immediately
(`{reason, message}`, `reason ∈ {malformed, unsatisfiable, unsupported}`,
dropped — never fatal to the worker); otherwise the claim's **consumption
watermark** (`consumed_seq`, on its commits and heartbeats) passing your seq, or
a node or exit naming your request, means taken; and a terminal verdict arriving
instead means the run ended without taking it. `await_consumed()` is the blessed
read, returning exactly that answer space (`Nak` | `RunResult` | `None`), by the
`request_id` your request bears; it raises if your request bears none, reuses an
id an answer already spent, or re-sends a lease id void for every later claim.

**(c) Lifecycle — the worker's self-report** (`worker → observers`, reserved
`lifecycle.*`; `lifecycle`-`v0.6`):

| topic | body | what it is / why |
|---|---|---|
| `started` | `{handle, t, resume_from, sources}` | the **claim**: *who* computes (its liveness **handle**), *where* it starts (`resume_from`, an extendable node, or null), and the names it exposes as **sources**; its `seq` names the claim |
| `commit` | `{claim_seq, consumed_seq, parent, values, fired, expired, t}` | one per iteration: a **node** extending `parent`, committing `values`, naming the subscriptions that fired and expired there |
| `heartbeat` | `{claim_seq, consumed_seq, t}` | **liveness** inside a slow iteration, and the watermark; **not a node** — see below |
| `rewind` | `{claim_seq, resume_from, t}` | restates the claim's **position** at an earlier node, keeping the claim |
| `stopped` | `{completed, error, claim_seq, honored, parent, values, fired, expired, t}` | the **exit**: the claim's last node, by the dying-breath CAS, naming the stops it honoured — see below |
| `bound` | `{claim_seq}` | binds a lease (`request_id`) to the claim that registered it |
| `nak` | `{reason, message}` | a refused control request (named by `request_id`) |

*What is a commit?* The end of one iteration, written by `commit()` (which the
`steps()` and `serve()` drivers call for you). It does the work 0.3.0's
heartbeat did by accident: it marks an iteration complete, carries the
**watermark** (`consumed_seq`), and is where subscriptions fire. So
**`Commits()` counts iterations**, and every progress number is a count along the
lineage: `prefix_len(ch, Commits(), head=...)`, or a stream's elements.

*What is a heartbeat?* Liveness only. A worker inside a long iteration calls
`heartbeat()`, which drains control (so a stop lands mid-iteration) and is
*witnessed* by the Watcher; it commits nothing and evaluates no subscription. A
hung loop *stops* writing records, which is exactly what makes staleness a real
hang-detector.

*What is `stopped`?* The worker's own cooperative halt, and its claim's last
node. `completed=True` is the worker's opt-in claim of intrinsic, permanent
completion, and **closes the lineage**: nothing can extend it. The default
(`completed=False`, no error) projects to **preempted** and is **extendable**:
its pending values commit, and the next claim may resume from it. A non-null
`error`, or any exit after `discard()`, is **torn** (`values: null`): the next
claim continues its `parent`. A worker that *crashes* writes nothing — so
*absence of `stopped` ≠ alive*, which is why a claim can also end by a death
report (below), and why liveness needs the tiers.

**(d) Supervisor — the external report** (`supervisor → observers`, reserved
`supervisor.*`; `supervisor`-`v0.5`, renamed from 0.3.0's `launcher.*`).
Distinct from lifecycle: lifecycle is the worker's *own* report; supervisor is a
report *from outside* — the spawner's, or any party's with evidence of a death —
and it catches deaths the worker can't report itself.

| topic | body | what it is |
|---|---|---|
| `launched` | `{handle, status, t}` | spawn-intent + the liveness handle, named by the launch id; `t` is the launcher's wall-clock at spawn |
| `terminated` | `{reason: exited\|killed, exit_code?, signal?, t}` | the *manner* of a launch's process death; only a `wait()`-ing parent can produce it; `t` is when the reaper observed it |
| `died` | `{claim_seq, outcome, evidence, t}` | a **death report**: names the claim it ends, with its outcome (`preempted`, `errored`, `killed` or `lost`) and verbatim evidence (`reap: signal 9`, `slurm:TIMEOUT`) |

A claim has **ended** once its exit or a death report naming it is on the log —
the one predicate the claim gate, lease voiding, the verdict and the Watcher
share. A report is written on **evidence, never inference** (a reap, accounting,
a probe that finds the process gone; never staleness): the launchers write one
at a reap when the worker left no exit, `report_if_dead` records a probe's
finding, and `report_death` serves everyone else. A worker that reads a report
naming its own claim is *released* and writes nothing more.

*What is a handle?* A portable, scheme-tagged liveness token — `local://host/pid`
(and `slurm://…`, `k8s://…`, `ray://…` as backends land). Anyone **on the same host** can **resolve**
it (`os.kill(pid, 0)`, `squeue -j`) to a liveness *fact*, actor-independently — no
parent relationship, no PID file. The worker self-reports its handle on `started`;
the launcher also records it on `launched`.

**(e) `value` — user metrics.** `{value, t}` (`value`-`v0.3`): your arbitrary
JSON-serializable datum and an absolute wall-clock `t`. `name` (envelope) says
which stream or source; `request_id` is null, since a node's `fired` names the
subscriptions a sample answers. A value belongs to the node whose `values` names
its seq, so it carries no step: an eager stream's element is indexed by its
place on the lineage. This is the one open-ended convention — the payload is
yours (a `json_default` hook coerces exotic types like numpy scalars on the way
out).

### Layer 3 — Orchestration helpers (opt-in)

Reference tooling built *on* the conventions. None of it is the protocol; a worker
or orchestrator can ignore it and compose `send`/`read`/`latest` directly.

- **`Worker`** — the reference loop. Constructing it **claims** the run, from the
  `resume_from` node it is given (a checkpoint, `LatestClaimHead()`, or `None`),
  declaring its `sources` and its checkpoint `save` policy, all three required.
  `commit()` ends an iteration: it drains control, evaluates subscriptions at
  the node being written, writes the samples and the `lifecycle.commit`, runs the
  save policy and returns the stop decision; `heartbeat()` is liveness only;
  `rewind(target)` restates the position; `discard()` tears the open iteration.
  **Two drivers** for the two protocol-visible continuation policies:
  `steps(until)` runs to a `Target` (the autonomous worker), `serve()` runs while
  demand is live (the service worker — stops at zero via the **careful death**,
  `retire()`, whose exit is compare-and-appended against the drained log so a
  racing subscribe is never orphaned; claims are CAS-claimed at both ends).
  `stop_pending` and `pinned` expose the two levels to hosts that own their
  loop; the `with` block writes the exit. Service-ness is **opt-in by verb** — an
  autonomous run's life never depends on its observers. The **checkpoint
  recipe** beside it (`Every`, `NoSave`, `OnHeadLineage`, `MostProgress`) saves
  only right after a node lands, so no checkpoint names work that never
  committed.
- **The observables** (`runstate.observables`) — the stateless observer plane:
  pure folds log → view. `peek_terminal` (the terminal verdict), `live_episode`
  (the claim gate), `latest_episode` (the newest claim), `live_demand` (the live
  subscriptions), `undischarged_stops` (the stops nothing has spent — the stop
  button's "did it land?"), and the **lineage reads**, each taking a required
  `head` (`LatestClaimHead()` or `AtNode(x)`): `series`, `prefix_len`, `prefix`,
  `last_element`, `aligned`, `answers` and `streams`. A polled path holds their
  incremental folds instead (`HeadFold`, `PrefixLen`, `LastElement`, `Answers`,
  `LiveDemand`, `PendingStops`), whose cost per poll is independent of the run's
  age. Observe statelessly here; watch statefully with the `Watcher` below.
- **`Launcher` / `LaunchHandle`** (Protocols) + **`ThreadLauncher`** (in-process;
  tests / single-process orchestration) and **`LocalLauncher`** (subprocess;
  injects `RUNSTATE_*` so the child's `current_channel()` meets the same log). A
  launcher is typed by its target record (`ThreadTarget`, `Command`). It does the
  irreducible job — spawn + emit handle — and returns, and its reap reports a
  claimant's death; *watching is a separable role*.
- **`Watcher`** — the stateful failure detector. `add(handle)` or `observe(run_id,
  channel)` tracks a run; `poll()` returns a `RunStatus` (`Running | RunResult`);
  `wait()` blocks to a terminal verdict; `wait_all()` covers a set; `broadcast(name,
  schedule)` fans one subscription across all tracked runs under a shared
  `request_id` — the **cross-run barrier**, and the reason there is no Experiment
  class.
- **`ensure`, the deciders** — `ensure(producer, target)` reads first and produces
  on a miss, returning `Reached` or `Unreachable`; `relaunch_if_needed` and
  `ensure_served` launch for durable and leased demand. (`sweep`, a sequential
  multi-run helper, was deleted on 2026-10-30: `dead_ends/sweep.md`.)

## Cross-cutting: liveness as a layered failure detector

"Is the run still alive?" has no single answer, so runstate stacks four tiers,
best to worst. None is substrate state — liveness is *emitted messages* +
*observer-side probing*, never a mutable TTL lease:

1. **The exit** — the claim's `lifecycle.stopped` record exists.
2. **A death report** — a `supervisor.died` naming the claim, from a reap, a probe,
   a session probe or accounting (for a run nothing claimed, its launch's
   `supervisor.terminated`).
3. **Probe the handle** — resolve it (`kill -0`) for the *fact* of death — same-host only (a foreign host's handle is *not locally resolvable* and falls to tier 4, never a false verdict) — even if
   the launcher is gone.
4. **Staleness** — no record of the claim (`started`, `commit`, `heartbeat`,
   `rewind`) witnessed within the Watcher's `heartbeat_timeout` ⟹ hung or
   crashed. The universal floor.

`peek_terminal(channel)` is the record-based verdict (tiers 1–2 — a pure, stateless
log read); the `Watcher` adds the inference tiers (3–4), which need state (arrival
times). All of it folds to one **`RunResult`**: a *closed* set of `outcomes` (from
`{completed, preempted, errored, killed, lost, presumed_dead}`) plus the closed `tier`
it rests on. There is deliberately **no `success` boolean** — whether a clean
preemption "succeeded" is a policy the *consumer* owns (e.g. `ensure` fails on any
outcome in `Outcome.failures()`), not something the producer should bake in. (Log
format 0.4.0, `specs/commits-and-lineage.md` §5: a read never writes, so the probe
tiers stay the Watcher's inferences until `report_if_dead` records one.)

Tier 4 carries an irreducible tension: a worker in a legitimately long single
iteration (a 20-minute epoch) writes nothing and *looks* dead. That's the
**dead-vs-busy** ambiguity — the threshold is a per-workload tuning (which is why
`heartbeat_timeout` is required), and a worker that can subdivide a long
iteration should call `heartbeat()` within it.

## Cross-cutting: the three clocks

Three independent notions of time, never conflated:

- **`seq`** — the substrate's transport order (the log's total order, assigned
  on append).
- **The lineage counts** — the worker's logical clock: `Commits()` (the
  iterations) and each eager stream's elements, counted along the lineage at a
  node. Never a body field (0.3.0's `step` is gone); what scheduling predicates
  evaluate against.
- **wall-clock** — real time (`value.t`; the required `t` each `lifecycle.*` /
  `supervisor.*` record carries since the observer clock; staleness).

All scheduling fires at the worker's nodes (and a stop's at its heartbeats too)
against the lineage counts and wall-clock, never `seq`. (`consumed_seq` on the
commit and the heartbeat is a read *position* in the inbound control order — not
a fourth clock.)

## What runstate is *not*

- **Not an orchestrator framework** — no `Orchestrator.run()`. Spawn however you
  like (subprocess, submitit, ray, Hydra); talk via the protocol.
- **Not a tracker** — use wandb / TensorBoard / MLflow for plotting; runstate is
  the control-plane counterpart.
- **Not a workflow engine** — no DAG, no retries, no scheduler. Compose those at
  your application layer.

## Where to go next

- [`guide.md`](guide.md) — the how-to, with runnable code.
- `design-v0.2.md` — the converged design with full rationale and rejected
  alternatives; `specs/commits-and-lineage.md` for log format 0.4.0.
- `protocol/` — the JSON Schema stack (the wire-authoritative source of truth).
- `examples/minimal/` — a runnable end-to-end example
  (`python examples/minimal/driver.py`).
- `backlog/synergy-map.md` — the forward roadmap. (Two of its layers since
  resolved by dissolution — the on-demand producer, `specs/derived-runs.md`,
  and the relational Store, `specs/store.md`; the visualization data plane
  remains the long-horizon item.)
