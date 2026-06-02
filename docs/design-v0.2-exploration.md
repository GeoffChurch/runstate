# runstate v0.2 — Design (log substrate + pull-first convention suite)

**Status:** redesigned through rev 11 (this session); **not yet freeze-ready** (see open questions); scope re-affirmation pending
**Date:** 2026-05-28 (last revised 2026-05-29, rev 11)
**Supersedes:** v0.1 (`docs/design-v0.1.md`). This is a full protocol redesign, not an additive change. v0.1's typed messages (`Progress`, `Stopped`, `StopNow`, `StopAtStep`) are removed and replaced by the unified vocabulary described here.

## Goal

The protocol unifies what v0.1 split between *lifecycle* (Heartbeat/Stopped) and *data* (proposed for v0.3) into a single **pull-first** vocabulary. Subscribers Request named values; workers respond with Values. Lifecycle events are well-known names within the same protocol, not a separate message family.

Two operational principles fall out of this:

1. **Lifecycle as named values.** Started/Stopped events are Values with well-known names, pushed by the worker without requiring an explicit Request. They are the irreducibly push events; everything else is pull or opt-in push. This is the load-bearing principle — it is what unifies the vocabulary.

2. **The worker owns its own termination.** A worker is a process; it stops when its own logic decides to, then emits `lifecycle.stopped` and exits. That is the universal exit path — it covers intrinsic completion (the loop ends), data-dependent stopping (`loss < 0.01`), and commanded stops alike. The orchestrator does not *remove* a worker; it *influences the worker's stop decision* via the well-known request `lifecycle.stop` (optionally with `when={"step": N}` for a future-step stop). Lifelines (below) are a narrow convention for one case only — the worker with no intrinsic work — not the general lifetime mechanism.

The system is **two layers**: an opinion-free **substrate** (a per-run log of envelopes with opaque bodies — Layer 1) and an opt-in **convention suite** (subscriptions + lifecycle — Layer 2). The "pull-first vocabulary" above *is* the convention suite; the substrate imposes almost nothing. Most of the design's content is the convention suite.

### Substrate vs convention suite (there is no third tier)

The hard line is between **two** layers, not three. An earlier draft posited a privileged "base protocol" of typed messages sitting between the substrate and the conventions; there is no such tier.

- **Substrate** (Layer 1; `protocol/messages-v0.2.schema.json` defines its envelope): a per-run durable **log of envelopes** — `{seq, direction, name?, request_id?, body}` — with an **opaque body it never parses**. It offers optional indexed addressing and ordered cursor reads; it imposes no message shapes, not even a `name`. **Lift-rule:** a field belongs in the envelope iff the substrate *indexes on it independently* — `name` (register / by-name reads), `request_id` (visibility), `direction` and `seq` (mechanical). Everything else is body. (A merged `name--request_id` key would fail this: register needs `WHERE name=…` ignoring request_id, visibility needs `WHERE request_id IN …` ignoring name — two independent index dimensions, not one composite.)
- **Convention suite** (Layer 2; all opt-in): the **subscription convention** (`Request`/`Value`/`Unsubscribe` body shapes, `when`/`until` semantics, the `request`/`unsubscribe` `kind` discriminator, the reference worker loop) **and** the **lifecycle convention** (the `lifecycle.*` well-known names — `started`, `stopped`, `stop`, `phase`, `heartbeat`, `nak`, `ack`) **and** lifelines. None of it is foundational: `when`/`until` is evaluated by the *worker* (the substrate is a passive log and never fires anything on a schedule), and Request-vs-Unsubscribe is a body distinction the substrate is blind to. Readers are **invisible** to the worker — a pure observer that only reads the log sends no Request and leaves no worker-side trace; the worker tracks only the Requests it services, keyed by `request_id` with no sender identity. The request_id-keyed subscription table and the lifeline reference-count are *worker-side convention state*, never substrate state.

The analogy is **TCP vs HTTP**: the substrate is TCP (addressed, ordered, payload-agnostic); the subscription + lifecycle suite is HTTP (a rich convention everyone *de facto* speaks, documented as the standard, but not part of the transport). **Conformance is thus two-tier:** *substrate-conformant* (implement the envelope log) vs *speaks-the-standard-suite* (also implement subscriptions + lifecycle). A Rust orchestrator that only wants to send a stop implements the envelope plus the single `lifecycle.stop` name — nothing else.

Stop is never a distinct wire message: `lifecycle.stop` is a well-known *Request name* in the subscription convention — an input to the worker's own stop decision, alongside its other triggers (loop bound, divergence check).

## Layers

```
Layer 1: Channel substrate              — per-run durable LOG of dicts, fan-out    [revised v0.2: queue → log]
Layer 2: Convention suite (opt-in)      — subscriptions + lifecycle (pull-first)    [this design]
Layer 3: Orchestration helpers          — Launcher + Watcher + sweep recipe        [this design]
Layer 4: Persistence + identity         — Store + Hasher                           [later]
Layer 5: Visualization protocols + UI   — long-term
```

**Layer 1 is no longer unchanged from v0.1.** Supporting multiple observers
forces the substrate from a queue to a log; see "Layer 1: the substrate is
a log" below. Layer 2 is rewritten. Layer 3 is new in v0.2.

## Layer 1: the substrate is a log (revised from v0.1)

v0.1's Channel is a **queue**: `recv` consumes a message — FileChannel
`unlink`s it, SqliteChannel marks `consumed_at` — so each message reaches
exactly one reader. A second observer sees nothing. Supporting multiple
observers from the start forces the contract from queue to **log**:

- **Non-destructive reads.** Reading never removes a message.
- **Caller-owned cursor.** Each reader owns its cursor — an `int` (`seq`
  position) it passes back on the next read (`read(after=cursor)`). The
  substrate stores *no* per-reader state and keeps *no* registry of who is
  reading; two observers on one run each see every message because each holds
  its own cursor. This replaces v0.1's channel-owned shared frontier
  (`consumed_at`), which is **dropped**. Caller-owned is the only multi-observer
  cursor scheme that needs no consumer registry — coherent with whole-run
  retention (the substrate never has to know its readers to decide what to
  keep). A reader that wants crash-resume persists its `int`; one that doesn't
  re-reads from `after=0`. Start position is just the initial cursor (`0` =
  earliest, current max = latest) — no separate start-mode flag.
- **Retention** until the run's channel is explicitly cleaned up (not merely
  while the worker process lives) — so a later `peek_terminal` / `sweep(resume)`
  can still read `lifecycle.stopped`. GC policy is an open question below.
- **Per-direction FIFO is the contract; global order is an optional capability.**
  Reads in one direction arrive in send order (always). A single sequencer —
  the db, a server, one shared lock — additionally yields a *global* `seq`
  across both directions; backends that have one (SQLite does) expose it, but
  the contract does not require it. `direction` is a filter, not a separate
  stream.

**Why one log, not two (initiality — but only where a sequencer exists).**
Where a single sequencer exists, the globally-ordered log is the *initial
object*: per-direction substreams are a canonical projection (filter by
`direction`); the merged total-ordered view is the identity; GC'd/aggregated
views are quotients. The canonical maps flow rich → poor — you can derive the
2-direction view from the 1 log, but not recover cross-direction order after
splitting (merging two independently-sequenced logs requires *inventing* an
interleaving). So on a single-sequencer backend, do **not** make "2 logs" the
primitive. **But initiality is a single-host luxury, not the foundation:**
global cross-direction order needs a single sequencer, which a leaderless
multi-host backend lacks. So the *portable* primitive — the actual contract —
is **per-direction FIFO**, and cross-direction total order is a capability that
single-sequencer backends add on top. Nothing in v0.2 requires it (completion
is single-direction; `latest(name)` is within one emitter; correlation is by
`request_id`); it buys cross-direction causal debugging and a single merged
replay timeline — both nice-to-have, neither load-bearing.

**One primitive, many read projections.** The log is the *only* substrate
primitive; the familiar stateful-communication shapes are **queries over it,
not new stream types** — so the substrate needs no per-name "stream type"
taxonomy and nothing has to declare one. Concretely each is a filter on **envelope** fields (`name`, `request_id`, `seq`; see the envelope below), never a parse of the body:

- **register / latest value** (current phase; a latest-loss gauge):
  the most recent record for a name, by `seq`. (Assumes a name's emissions are
  `seq`-monotonic in `step`; for step-scoped values that can arrive out of step
  order, define the register over `step` instead.)
- **flag / terminal fact** ("is the run *cleanly* done?"): the *existence* of a
  `lifecycle.stopped` record — a single bit, no history needed. (A crashed
  worker writes none; absence ≠ alive — reconcile with the liveness sidecar.)
- **queue** (worker fan-in, single consumer): a consumer that *persists* its cursor, resuming after a crash instead of reprocessing (below).
- **bounded window** (last *k*): the last *k* records.
- **full history** (the loss curve; an event tail): a cursor read.

Each is a lossy view of the same initial object; the consumer chooses the
view by *how it reads*. Consequence for cursors: the lifecycle / "is it done"
consumers use stateless latest/exists reads and need **no cursor**; cursors
are required only by consumers that genuinely tail history. A per-name
*retention* policy (compacting a register's superseded records to reclaim
space) compacts away history and is therefore a *semantic* change, not free GC: it makes the register — not the full log — the retained object, so the initiality argument above holds **only under full retention**. Deferred, and chosen per-name with eyes open. This is also the niche where a future raw-file backend becomes
cheap (register = overwrite-a-file, flag = a `.stopped` file, queue =
a consume-dir — all fan-out without cursors or GC); only the unbounded history
tail is hard on files.

**Fan-in vs fan-out — same log, different cursors.** The two directions have
different access patterns, but neither needs a different storage model: both
are caller-owned cursor reads over the one log. orchestrator→worker (Requests)
is fan-in with a single consumer (the worker), which *persists* its cursor for
crash-resume; worker→orchestrator (Values) is fan-out, where each observer
keeps its own (usually ephemeral) cursor filtered by `request_id`. The
queue/log distinction collapses into "does this consumer persist its cursor?" —
there is no consume-and-delete step.

**Channel surface (Layer 1).** The whole substrate is:

```python
send(body: dict, *, name=None, request_id=None) -> int        # append; returns the assigned seq
read(after: int = 0, *, direction=None, name=None, request_ids=None,
     limit=None, timeout=None) -> list[Envelope]              # envelopes with seq > after, filtered
latest(name: str) -> Envelope | None                          # register/flag projection
close()
```

The entire mutable state is caller-held cursors; the store itself is
append-only. `direction` is set by the writer (it names the *audience*, not the author — the substrate is author-agnostic, so a launcher/daemon may also write the `to_orchestrator` stream). A thin Layer-2
iterator (used by the reference worker loop and `Watcher`) holds the cursor for
the common "just tail this run" case, so callers rarely thread it by hand.

**Liveness vs completion.** The log answers "did this run *cleanly* finish?"
authoritatively where the queue could not: `lifecycle.stopped` is retained
and readable by any observer, including one attaching late (`peek_terminal()`
is exactly this existence query, and silently assumed a log). What the log
cannot show is whether a *currently-running* worker is alive — a
crashed/SIGKILL'd worker writes no `stopped`. Liveness is then a **layered failure detector**, and *none of it is
substrate-owned* — the substrate stays a log; presence is expressed as messages
actors *emit*, never as substrate state. Best-to-worst precedence:

1. **Clean completion** = `exists(lifecycle.stopped)` — a log read under a
   convention name. Precise; the worker reports its own clean exit (incl. caught
   errors).
2. **Reaped death** = a `launcher.terminated{exit_code, signal, reason}` event,
   written by whoever `wait()`s the process (a stay-attached launcher or a
   daemon). Carries the *manner* of death — exit code / signal, killed-vs-crashed
   — which only the reaping parent can know and **no probe can recover.**
3. **Probe the handle** — the spawner records a portable, scheme-tagged
   **liveness handle** in `launcher.launched{handle,…}` (or the worker's
   `lifecycle.started{handle,…}`): `local://host/pid?start=T`, `slurm://jobid`,
   `k8s://ns/pod`, `ray://actor`. Any observer resolves it on demand (`kill -0`,
   `squeue -j`, …) to learn the *fact* of death, **actor-independently** — robust
   even if the launcher is long gone, and cross-host wherever the scheme's
   resolver is. (This obsoletes the old `.worker.pid` file — the handle lives in
   the log.)
4. **Heartbeat staleness** = `latest(lifecycle.heartbeat)` older than a timeout.
   The universal floor: no handle, no reaper, works cross-host, but probabilistic
   (can't distinguish crashed from hung).

These compose as **opt-in tiers** — three coherent reference configurations:
**(a) floor only** (clean-stop + heartbeat): universal, weakest. **(b) + handle,
observers self-probe**: on-demand fact-of-death — but every observer probes
independently, so it's wasteful/rate-limited for expensive schemes (`squeue`)
with many observers. **(c) + handle + reaper**: a daemon (or stay-attached
launcher) probes/reaps *once* and writes the result to the log — coalescing the
probe cost *and* adding manner-of-death for passive observers. Tiers (b)/(c) need
a watcher that outlives the worker; the orphan-of-last-resort (watcher also dies)
bottoms out at the heartbeat floor — perfect detection is impossible, so the
floor is irreducible.

This splits the conflated "Launcher" into **spawn** (make a process exist + emit
its handle — the irreducible Layer-3 job, and all a cluster scheduler lets you
do) and **watch/reap/report** (a *separable, optional* role: stay-attached
launcher, daemon, or nobody). `terminate()` likewise resolves the handle and
kills (`kill` / `scancel` / `kubectl delete`), not via a parent relationship a
fire-and-forget launcher no longer holds.

**Defaults matter — opinion-free must not mean batteries-not-included.** The
reference convention ships sane defaults so the out-of-box story is "no mystery
on a single host": the reference worker loop heartbeats; `LocalLauncher` writes
a `local://` handle and, used as a context manager (the common case), stays
attached to reap and emit `terminated`. Every tier is removable/overridable.

A **lease** (mutable, TTL'd presence the substrate maintains) is deliberately
*avoided*: it would be a second ontology inside Layer 1 — mutable presence plus
a notion of who's connected — exactly the reader-awareness the substrate keeps
out. All four tiers are emitted messages + observer-side probing, zero new
substrate concepts.

**Backend disposition.**

- **SqliteChannel — the single Channel backend for v0.2.** Stdlib (no external
  dep); its core (single `AUTOINCREMENT id` = `seq`, `direction` column,
  retained rows) *already is* the globally-sequenced log. v0.2 changes: add
  envelope columns (`name`, `request_id`) + indexes; **drop `consumed_at`**
  (reads are caller-owned cursors — `WHERE seq > :after [AND name/request_id …]
  ORDER BY seq`); add `latest(name)` (`… WHERE name = :name ORDER BY seq DESC
  LIMIT 1`).
- **FileChannel — removed in v0.2.** v0.1's file backend is a single-consumer
  queue (consume-by-`unlink`); making it fan-out would need a global seq
  counter, no-delete + per-reader cursor files, and a GC policy, and it scales
  badly regardless (one file per message → inode pressure, O(N) directory scans
  per poll). stdlib SQLite covers every case in one indexed file, so v0.2 ships
  **SQLite only**. A file backend returns to scope only if the per-name read
  taxonomy (register/flag/queue) makes it cheap again — backlog.
- **Multi-host backends (backlog).** Prefer **NATS JetStream** — the closest
  semantic match: subjects = `name` (with `lifecycle.>` wildcards), stream
  sequence = `seq`, durable/ephemeral consumers = caller-owned cursors,
  subject-filtered consumers = by-name routing, and `last_per_subject`
  retention = the `latest(name)` register projection, built in. Redis Streams
  and Postgres also fit the log + cursor shape but need bolt-ons (RediSearch /
  SQL) for the indexed `latest`/visibility projections.

## Layer 2: the convention suite (subscriptions + lifecycle)

Layer 2 is **opt-in convention** over the Layer-1 substrate. First the envelope it
plugs into (the substrate's contract, repeated here for reference), then the
subscription and lifecycle conventions.

### The envelope (Layer 1's contract)

Every message is an **envelope** wrapping an opaque **body**. The substrate
routes, indexes, and filters on the envelope; only the convention layer interprets
the body.

```python
@dataclass class Envelope:
    seq: int                  # substrate-assigned; monotonic per direction (global iff the backend has a single sequencer)
    direction: str            # audience: "to_worker" (worker consumes) | "to_orchestrator" (observers consume); any actor may write either — the substrate is author-agnostic
    name: str | None          # routing key: lifecycle.* or user-defined; None = unaddressed
    request_id: str | None    # correlation + visibility; None = spontaneous push / broadcast
    body: dict                # opaque to Layer 1 — one of the body shapes below
```

`seq` is stamped by the substrate; the writer sets `direction` (the *audience* —
any actor may write either, the substrate is author-agnostic) and
`name`/`request_id`; the body holds the type-specific params. The ergonomic
`Request`/`Value`/`Unsubscribe` dataclasses below are Layer-2 conveniences whose
`name`/`request_id` attributes map to envelope fields and whose remaining
attributes are the body — they pack/unpack to the envelope on send/recv.

### Typed messages (body shapes — the subscription convention)

These shapes are the **subscription convention**, not foundational — the substrate sees only the envelope + an opaque body. On the wire the to_worker body carries an explicit `kind` discriminator (`"request"` | `"unsubscribe"`) so a reader deserializes to the right shape — restoring v0.1's explicit `type`, but as a *convention* body field, not a wire axiom. Values need no `kind`: everything to_orchestrator is a Value, dispatched by `name`.

**Orchestrator → Worker:**

```python
@dataclass class Request:
    name: str                               # well-known or user-defined
    when: When                              # {"now": true} | {"step": N} | {"every": K}
    until: Until | None = None              # {"step": N} | {"time_seconds": N} | {"count": N} | None
    request_id: str

@dataclass class Unsubscribe:
    request_id: str
```

**Worker → Orchestrator:**

```python
@dataclass class Value:
    name: str
    value: Any                              # JSON-serializable
    step: int | None = None                 # None for non-step-scoped values
    request_id: str | None = None           # None = spontaneous push; else = response to Request
```

That is the entire vocabulary: one envelope + three body shapes. On the wire `name` and `request_id` live in the envelope (the substrate routes/indexes/filters on them); `value`/`when`/`until`/`step` live in the body (opaque to the substrate — note `step` is the worker's clock, distinct from the envelope's `seq`). Request/Value/Unsubscribe are therefore body shapes, not sibling top-level message types: a Value is recognized by direction (to_orchestrator) + `name`, and Request vs Unsubscribe by the body `kind` discriminator; the dataclasses above are the opt-in Layer-2 ergonomic surface. The JSON Schema in `protocol/messages-v0.2.schema.json` is authoritative for wire format; this section is authoritative for semantics.

Negative acknowledgments (worker rejecting an unknown Request name, or an Unsubscribe for a request_id it doesn't have active) are expressed as Values with a conventional name — see "Acknowledgments as Values" below. The convention suite does not define a separate Ack message type; it's pushed back through the same Value channel like everything else.

### Conventions

Well-known names with defined semantics:

| Name | Direction of typical use | Semantics |
|---|---|---|
| `lifecycle.started` | pushed by worker on attach | Always pushed; no subscription required to receive. Value is `{handle, hostname, attached_at}` or similar — `handle` (e.g. `local://host/pid?start=T`) is the worker self-reporting its own liveness handle when no Launcher recorded one. |
| `lifecycle.stopped` | pushed by worker on exit | The worker's last message *when it exits cooperatively*. A courtesy, not a guarantee — a crashed/SIGKILL'd worker emits nothing, so its absence is not proof the run is alive (reconcile with the liveness tiers — handle probe / heartbeat). Value is `{reason, error?, final_step?}`. |
| `lifecycle.phase` | requested or pushed | Push on phase transitions; or subscribe with `when={"now": true}` to query current phase. Value is the phase name (string). |
| `lifecycle.stop` | requested; optional `when={"step": N}` | Cooperative stop. Default (`when={"now": true}`) stops at the next safe point; `when={"step": N}` stops at the next safe point where step ≥ N. Re-expresses v0.1's `StopNow` and subsumes `StopAtStep` (no separate name — just `stop` + the existing `when`). **Ack:** when serviced, the worker returns a Unit `Value` keyed by the request's `request_id` (the receipt that *this* request landed); the separate `lifecycle.stopped` push is the dying breath, not the per-request ack. |
| `lifecycle.heartbeat` | pushed (beacon) or requested (lifeline) | A liveness ping; Value is `null` — the content is in the *timing*. **Primary role:** the worker pushes it periodically (`request_id=None`) as the **liveness-floor beacon**; observers read `latest(lifecycle.heartbeat)` + a timeout to detect crash/hang (see "Liveness vs completion"). **Secondary role:** for a **service worker** (no intrinsic work), an orchestrator's *subscription* to this name is the default *lifeline* — its presence is the reason-to-live, its `until` the lifetime bound. A worker with intrinsic work lives by its own loop; a heartbeat subscription on it is just an observer ping. See "Lifelines: the service-worker case only". |
| `lifecycle.nak` | pushed by worker | Negative acknowledgment for an unhandleable Request or Unsubscribe. Envelope `request_id` = the offending request's id (so it routes to that requester via the visibility filter); body is `{status, message}`. See "Request IDs and acknowledgments as Values" below. Optional — workers may silently ignore failures instead. |
| `launcher.launched` | written by the spawner | Records the worker's portable liveness **handle** at spawn — body `{handle, start_time?, launcher_handle?}` (e.g. `handle="local://host/pid?start=T"`). Authored by the Launcher (process-level), written to the `to_orchestrator` audience. A worker with no Launcher self-reports the handle via `lifecycle.started` instead. |
| `launcher.terminated` | written by the reaper | The *manner* of death from whoever `wait()`s the process — body `{exit_code?, signal?, reason: "exited" | "killed"}`. Optional (liveness tier c); only the reaping parent can produce it. |
| user-defined | requested or pushed | Application metrics, artifacts, custom signals. Free-form names within a namespace the application controls. |

`started` and `stopped` cannot be pull (started fires before any subscription can exist; stopped is the cooperative dying breath, pushed regardless of who's listening — when the worker exits cleanly enough to send it). Other lifecycle/launcher events are also predominantly *pushed* (the `heartbeat` beacon, `launcher.launched`/`terminated`); **pull-first is the default for user-metric subscriptions**, not a universal rule.

### Termination: the worker decides

Stopping is always the worker's own decision. The worker is a process; at a safe point it decides to stop, pushes `Value(name="lifecycle.stopped", value={reason: ...})`, and exits. This single path covers every reason a worker stops:

- **Intrinsic completion** — the training loop reaches its bound (`step == 5000`). The worker exits because its work is done; no protocol involved.
- **Data-dependent** — `loss < 0.01`, early-stopping patience, NaN detected. Arbitrary user code in the worker's stop check; no protocol involved.
- **Commanded** — the orchestrator asked it to stop (now, or at a future step). Below.

The orchestrator never *removes* a worker. It feeds an **input to the worker's stop decision** via one well-known Request name (`lifecycle.stop`, optionally scheduled with `when`), which sits alongside the worker's other stop triggers:

| Request | Worker behavior |
|---|---|
| `lifecycle.stop` | Stop at the next safe point. (v0.1's `StopNow`, re-expressed as a request name.) |
| `lifecycle.stop`, `when={"step": N}` | Record N; stop at the next safe point where step ≥ N. (v0.1's `StopAtStep`, now just `stop` + a `when` schedule — no separate name.) |

Both are ordinary Requests on the wire — no new message type. The worker's reference loop, each iteration (or `tick()` call):

1. Reads new input via `read(after=cursor, direction=to_worker)` and advances + persists its cursor; updates its observer-subscription table (keyed by `request_id`); notes any `lifecycle.stop` (immediate, or scheduled via `when={"step": N}`).
2. Services due Requests with matching Values; reaps observer subscriptions whose `until` has been met (worker-side, race-free).
3. Evaluates its stop decision: intrinsic bound reached? data-dependent condition? `lifecycle.stop` received (immediately, or its scheduled `when={"step": N}` crossed)? If any → push `lifecycle.stopped`, exit.

When the worker services a stop request it returns a Unit `Value` keyed by that request's `request_id` — the request-level receipt confirming *this* stop landed — exactly like any other serviced Request. `lifecycle.stopped` is then the separate dying breath (a `request_id=None` push), announcing the fact of termination to all observers regardless of cause. The two coincide in time for a commanded stop but carry different information: the receipt correlates to the originating request; the dying breath is the broadcast lifecycle fact. (This corrects an earlier draft that made `lifecycle.stopped` itself the per-request ack, which broke `request_id` correlation.) There is no separate `stop_at` name: a stop-at-step is simply `lifecycle.stop` carrying `when={"step": N}`, built from the `stop` primitive plus the existing `when` schedule. A stop that never fires (the worker exited first for another reason) is handled like any subscription whose `when` never arrives.

This is why there's no `StopAtStep` problem (an earlier draft modeled stopping as removing a "reason to live," which made *scheduled* removal impossible). Once stopping is the worker's decision, "stop at step N" is just one more trigger feeding that decision — exactly as cheap as it was in v0.1.

### Lifelines: the service-worker case only

One worker shape genuinely needs a lifetime mechanism beyond "I decide": the **service worker** — no intrinsic work, exists only to answer requests, should die when nobody's interested. Its stop decision is "stop when no one who matters is subscribed." That needs a distinction the general case doesn't:

- **Observer** — requests Values but does not affect lifetime. The default for every subscription.
- **Lifeline** — a subscription the service worker treats as a reason-to-live. Reference-counted: it stays up while ≥1 lifeline is active and stops when the last ends.

The distinction exists so that a mere observer (someone tailing `loss`) doesn't accidentally pin the worker alive — the failure of a naive "stop when subscription count hits zero" rule. Lifeline-ness is signaled **by reserved name, not a wire flag**: a subscription to `lifecycle.heartbeat` (or an application-designated name) is a lifeline; everything else observes. The envelope stays free of a `pin` field — lifeline-ness is pure convention.

Lifelines are always **externally issued** (the orchestrator subscribes); a worker never self-subscribes to keep itself alive — if it has work, its own loop is the reason it lives; if it doesn't, an outside lifeline is. The reference loop for a service worker adds one clause to step 3: *also* stop if the active-lifeline count is zero.

Note the scope: this is a narrow convention for one worker shape. A training run never touches it — it lives and dies by its own loop. Don't reach for lifelines unless you're actually building a request-driven service with no intrinsic work.

#### Bootstrap (service workers only)

A service worker launched **eagerly** starts with zero lifelines and would exit before the orchestrator's first subscription lands. Lazy launch dodges this — the launching Request *is* the first lifeline (see "Lazy launch via `open_channel()`"). Eager launch of a service worker needs a startup grace window before the zero-lifeline check arms. Document as a sharp edge. (Workers with intrinsic work have no bootstrap race — they have work to do from step 0.)

#### Always-on push

A worker that wants some metrics to appear regardless of subscribers sends `Value` with `request_id=None`. Allowed by the protocol. Note this shares the `request_id=None` encoding with the `lifecycle.*` pushes; an orchestrator distinguishes "lifecycle event I care about" from "unsolicited metric" by the `lifecycle.` name prefix. The prefix is doing real semantic work — the spec must call this out.

### Until conditions

A Request's `until` field defines when the subscription auto-expires — when the worker stops emitting Values for it. (It does not terminate the worker; termination is the worker's own decision, above. The one exception is a service-worker lifeline, where the lifeline's `until` running out removes a reason-to-live.) Supported forms in v0.2 — consistently object-tagged so the schema is a clean `oneOf` and future variants slot in without ambiguity:

- `until=None` (default): open-ended; persists until explicit Unsubscribe.
- `until={"step": N}`: expires when step ≥ N.
- `until={"time_seconds": N}`: expires at the first safe point ≥ N seconds after the worker registered the Request (wall-clock is evaluated only when the worker ticks, like `every`; "registered" = worker-receipt time, since there is no registration ack by default).
- `until={"count": N}`: expires after N Value responses have been sent.

The set above is **closed** for v0.2. Adding a variant is a protocol version bump (the schema's `additionalProperties: false` is load-bearing).

`when` follows the same object-tagged discipline: `{"now": true}` | `{"step": N}` | `{"every": K}`. Mixing bare strings/ints with object variants (the earlier `"now" | step_k | {"every": K}` sketch) forces an awkward `oneOf` across primitive and object types — avoid it.

Open semantics to pin down: does `when={"now": true}` respond once and expire, or persist? Treat it as `until={"count": 1}` unless an explicit `until` overrides — i.e. one-shot by default.

Deferred to backlog:
- **Value-predicate `until`** (e.g., `until={"value": {"gt": 0.5}}`) — opens a small DSL. Not needed for worker self-termination: a worker stops on a data-dependent condition with arbitrary code in its own stop check (see "Termination: the worker decides"), no `until` involved. Only revisit if an *orchestrator* needs to express "unsubscribe when the value crosses a threshold". Defer until then.
- **`count` race resolution** — whether `count` means Values *sent by the worker* or *received by the orchestrator*. v0.2 specifies worker-sent (race-free, worker-side). Revisit if users actually want "next N I receive".

### Request IDs and acknowledgments as Values

`request_id` is required on Request and Unsubscribe; orchestrator generates them (uuid4 is fine). Worker correlates Value responses to the originating Request via `request_id`.

Successful operations need no explicit acknowledgment — a Value reaching the orchestrator is the natural confirmation that the Request was registered and is firing. The only cases that need explicit feedback are *failures*: an unknown metric name, a malformed Request, an Unsubscribe targeting a nonexistent subscription.

These cases are handled by **convention, not by a separate protocol message**. A worker that wants to report a failure pushes a Value with a reserved name:

```python
Value(
    name="lifecycle.nak",                          # negative-ack convention
    value={"status": "unsupported", "message": "unknown metric: 'loss'"},
    request_id="r123",   # the offending request's id → routes to that requester via the envelope
)
```

Because the envelope `request_id` carries the offending request's id, a nak routes straight to the orchestrator that sent that request (via the visibility filter). Orchestrators that care about feedback subscribe to this name or filter for it in their event stream. Orchestrators that don't care can ignore it. The worker may choose not to emit nak Values at all (silent ignore) — the protocol doesn't mandate the behavior, only standardizes the *shape* if the worker chooses to participate.

The `runstate.send_nak(channel, request_id, status, message)` helper in `registry.py` is a one-liner for workers that want to opt in (it sets the envelope `request_id` and a `{status, message}` body). It's not part of the protocol surface; it's a convenience for the convention.

Rationale: every worker-to-orchestrator signal is a Value; the protocol does not split message types by purpose. Acknowledgments live in the same vocabulary as metrics and lifecycle, distinguished only by name.

## Layer 3: orchestration helpers

The **substrate** is lifecycle-free; **these Layer-3 helpers are not** — they assume the lifecycle *convention*. `Watcher.wait`/`RunResult` derive their outcome from `lifecycle.stopped`, `sweep`'s `resume` detects already-done variants via `peek_terminal` checking whether a `lifecycle.stopped` record *exists*, and `RunResult.elapsed` spans `started`→`stopped`. So the `lifecycle.*` convention is optional at the wire but *assumed* by everything in this layer. A worker that opts out of the convention also opts out of these helpers and composes its own observation loop directly from Request/Value/Unsubscribe + the liveness tiers (handle probe / heartbeat). State this where the helpers are documented so the convention's reach is visible.

### Launcher Protocol

```python
class Launcher(Protocol):
    def launch(
        self,
        run_id: str,
        target: Callable | list[str],
        *,
        args: tuple = (),
        kwargs: dict | None = None,
        env: dict[str, str] | None = None,
    ) -> LaunchHandle: ...

    def open_channel(self, run_id: str) -> Channel:
        """Return an orchestrator-role Channel bound to this Launcher.
        Sending the first Request on the channel will lazily launch the worker."""
```

`launch()` does the irreducible job: **spawn a process and emit its liveness
handle** (`launcher.launched{handle,…}`), then return. Watching/reaping is a
*separable, optional* role (see "Liveness vs completion") — the same launcher
may stay attached to reap, a daemon may, or nobody does. A cluster launcher
(`submitit`, Ray, k8s) is inherently fire-and-forget: it submits a job, the
scheduler owns the process, and the handle (`slurm://…`) is what makes liveness
resolvable afterward.

Two reference implementations:

- **`LocalLauncher`** (`runstate/launcher/subprocess.py`): spawns via `subprocess.Popen`; writes a `local://host/pid?start=T` handle. Worker receives env vars (`RUNSTATE_RUN_ID`, `RUNSTATE_CHANNEL_ROOT`) and uses `runstate.attach()`. Used as a context manager (the default), it stays attached to reap and emit `terminated`; otherwise it can fire-and-forget.
- **`ThreadLauncher`** (`runstate/launcher/thread.py`): runs the target as a thread in the same process. Primarily for testing; uses a context-local Channel so `attach()` works without env vars.

### LaunchHandle

```python
@dataclass class LaunchHandle:
    run_id: str
    channel: Channel                 # orchestrator-role
    handle: str                      # portable liveness/terminate token: local://… | slurm://… | …
    # backend-specific (None for fire-and-forget / cluster):
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None

    def is_alive(self) -> bool: ...        # resolve `handle` (kill -0 / squeue / …)
    def wait(self, timeout: float | None = None) -> int | None: ...  # only if THIS process reaps
    def terminate(self) -> None: ...       # resolve `handle` and kill (kill / scancel / kubectl delete)
```

`terminate()` resolves the handle and kills — the last resort, and the only path
once a fire-and-forget launcher has exited (no parent relationship to use). The
*cooperative* shutdown is via channel dynamics — send `lifecycle.stop`, wait for
`lifecycle.stopped`. `wait()` returns an exit status only if *this* process is
the reaping parent (a local, stay-attached launcher); a fire-and-forget or
cluster handle has none.

### Lazy launch via `open_channel()`

The symmetric mirror of worker-side `attach()`. Calling `launcher.open_channel(run_id)` returns a Channel bound to the launcher's launch capability. Sending the first Request checks for an existing worker (via the log handle), launches if absent, then sends.

Atomicity: `send_request()` acquires the channel's lock (SqliteChannel's transaction), does the liveness check + optional launch + message append inside the lock, releases.

Worker liveness check (handle-based, no `.worker.pid` file):
- Read the latest `launcher.launched` / `lifecycle.started` handle from the log; if absent → launch.
- If present, resolve it (`kill -0` for `local://`, `squeue -j` for `slurm://`, …); if dead/stale and there's no `lifecycle.stopped` → launch.
- Use *handle/process* liveness here, not heartbeat — a hung-but-alive worker must not be double-spawned. The double-spawn race (open question #8) is still about making check + spawn + append atomic.

### Watcher

```python
class Watcher:
    """Multiplex events from multiple active LaunchHandles."""
    def add(self, handle: LaunchHandle) -> None: ...
    def remove(self, run_id: str) -> None: ...

    def active(self) -> list[str]: ...
    def active_count(self) -> int: ...

    def iter_events(self, timeout: float | None = None) -> Iterator[tuple[str, Envelope]]:
        """Yield (run_id, envelope) as events arrive from any active handle,
        each handle read via its own caller-owned cursor.
        Returns when timeout expires (timeout=None blocks indefinitely)."""

    def wait(self, run_id: str, *, timeout: float | None = None) -> RunResult: ...
    def wait_any(self, *, timeout: float | None = None) -> RunResult: ...
    def wait_all(self, *, timeout: float | None = None) -> list[RunResult]: ...

    def broadcast(self, message: dict | Request | Unsubscribe) -> None:
        """Send the same message to all active handles. Useful for cross-run sync:
        broadcast a Request(name="loss", when={"step": 100}) and collect responses."""
```

The Watcher is single-threaded (driver thread); not safe for concurrent calls from multiple threads. Document this.

Polling implementation:
- Poll each SqliteChannel's messages table for new rows (`id > cursor`) per active run_id every 50ms.
- A future LISTEN-NOTIFY / push mode for tighter latency lives in the backlog.

### RunResult

```python
@dataclass class RunResult:
    run_id: str
    outcome: str                    # "completed" | "errored" | "killed" | "presumed_dead"
    success: bool                   # True iff outcome == "completed"
    reason: str                     # from lifecycle.stopped, launcher.terminated, or the detector tier
    error: str | None
    final_step: int | None
    elapsed: float                  # started → stopped/terminated [SUPERSEDED 2026-06-02: dropped, never populated — see conventions-hygiene F8]
```

The reachable outcomes depend on the **liveness tier** in play (above): under the
heartbeat-only floor, an unclean death surfaces as `presumed_dead` (a timeout
inference, no crisp exit code); `killed` and exit codes require a reaper tier.

### Free function: `peek_terminal()`

```python
def peek_terminal(run_id: str, root: Path) -> RunResult | None:
    """Read-only: returns the terminal RunResult if a `lifecycle.stopped`
    record *exists* on the channel (an existence query, not `latest` —
    completion is existence, not most-recent), else None. A single indexed
    lookup, not a scan; does not require a live handle. Used by sweep() to skip
    already-completed variants. (One backend in v0.2, so no `backend` arg.)"""
```

### sweep recipe

```python
def sweep(
    variants: Iterable[VariantSpec],
    launcher: Launcher,
    *,
    on_event: Callable[[str, Envelope], None] | None = None,
    timeout_per_variant: float | None = None,
    resume: bool = True,
    stop_on_failure: bool = False,
) -> list[RunResult]: ...
```

Sequential. Iterates variants, calls `launcher.launch()` for each, watches the channel until terminal — a clean `lifecycle.stopped`, or (per the liveness tier) a detected-dead / `timeout_per_variant` `presumed_dead` — captures the `RunResult`, moves on. `resume=True` skips variants whose channel already has a `lifecycle.stopped` event. `stop_on_failure=False` continues on per-variant errors, capturing them in the result list.

For parallel sweeps, BO loops, or other adaptive patterns, users write their own loop with Launcher + Watcher directly. `sweep()` is the simple-case recipe (~50 LOC); not the universal interface.

### Cross-run synchronization

`Watcher.broadcast(Request(name="loss", when={"step": 100}))` sends the same Request to all active handles. Each worker, when it reaches step 100, sends back a Value with that name and step. The orchestrator collects them via `Watcher.iter_events()` — but a count reaching `active_count()` is a *safe* barrier only if every active worker will actually answer. A worker already past step 100, or one that stops before reaching it, never responds; so a robust barrier needs the registration-ack (open question #6) to distinguish "registered, not yet fired" from "will never fire," plus a timeout. Naively waiting for the count to hit `active_count()` can hang.

This is the primary mechanism for cross-run synchronization. No separate Experiment class is needed in v0.2.

**`request_id` allocation on broadcast must be specified.** Two options: (a) the *same* `request_id` to every run — correlation is trivial (responses share the id; the `run_id` in the `(run_id, event)` tuple disambiguates which run answered) and `broadcast(Unsubscribe(id))` cleanly cancels the lot; (b) a *unique* `request_id` per run — finer-grained cancellation but a correlation table to maintain. v0.2 picks **(a)**: one `request_id` per broadcast, the `run_id` is the disambiguator. Document that a broadcast is therefore one logical subscription fanned across runs, cancelled as a unit.

**Multi-subscriber, same name.** Two orchestrators each `Request(name="loss", ...)` produce *two independent subscriptions* (distinct `request_id`s); the worker emits one Value per subscription per fire — no dedup. Wire traffic scales with subscriber count. State this so users don't expect fan-out coalescing. (Delivery to all subscribers is handled by the Layer-1 log — non-destructive reads + per-caller cursors mean every subscription sees every fire; see Layer 1.)

## Files and module layout

```
runstate/
  __init__.py             # re-exports, attach()
  channel/                # read API revised queue → log (caller-owned cursors), see Layer 1
    __init__.py
    sqlite.py             # the single backend in v0.2 (file.py removed)
  protocol.py             # NEW: Request, Unsubscribe, Value dataclasses + parse + send_*
                          # Replaces control.py and events.py from v0.1.
  registry.py             # NEW: worker-side @metric decorator + tick() + subscription tracker
  launcher/
    __init__.py           # NEW: Launcher Protocol, LaunchHandle, open_channel()
    subprocess.py         # NEW: LocalLauncher
    thread.py             # NEW: ThreadLauncher
  watcher.py              # NEW: Watcher class
  sweep.py                # NEW: sweep() recipe, VariantSpec, RunResult, peek_terminal()
```

v0.1's `control.py`, `events.py`, and `channel/file.py` are deleted. The SqliteChannel *storage* is preserved, but its read API moves from queue to log (caller-owned cursors; see Layer 1). `attach()` keeps its current signature but its companion `protocol.py` replaces the v0.1 typed messages.

## What gets rewritten vs preserved

**Preserved:**
- `runstate/channel/sqlite.py` storage *model* — retained + globally-sequenced is correct, but the table gains **envelope columns** (`name`, `request_id`) + indexes, and the read API moves queue → log with caller-owned cursors (see "Layer 1: the substrate is a log"). v0.2 dbs are not v0.1-compatible (new columns; `consumed_at` gone) — fresh runs only.
- `runstate/__init__.py:attach()` — unchanged contract.

**Removed:**
- `runstate/channel/file.py` (FileChannel) — see Layer 1. With one backend, `tests/conftest.py` no longer parametrizes over backends (SQLite only).

**Rewritten:**
- Protocol layer: `control.py` + `events.py` → `protocol.py` + `registry.py`.
- JSON Schema: `protocol/messages-v0.1.schema.json` → `protocol/messages-v0.2.schema.json` with new vocabulary.
- Prose spec: `protocol/spec.md` updated for the queue→log substrate, the substrate-vs-convention-suite split (no base-protocol tier), worker-owned termination (`lifecycle.stop` as a well-known request, with `when={"step": N}` for stop-at-step), the liveness tiers + handle, and the narrow service-worker lifeline convention.
- Example: `examples/minimal/{worker,driver}.py` rewritten to demonstrate the common case — a training worker pushing `started`/`heartbeat`/metrics/`stopped`, and a driver subscribing to a metric and sending `lifecycle.stop`. (A separate service-worker example can show the `lifecycle.heartbeat` lifeline.)
- Tests for protocol messages, schema conformance, subscription lifecycle, until expiration.

**New:**
- `launcher/`, `watcher.py`, `sweep.py`, `registry.py`.
- Tests for Launcher (both LocalLauncher and ThreadLauncher), Watcher, sweep, subscription registry, lazy-launch atomicity.

## Backlog (deferred, captured for later)

The following ideas surfaced during design but are deliberately not in v0.2:

- **`auto-launch-daemon`** — the long-running supervisor pattern. Lazy-launch covers the typical case; the daemon is the right tool when passive subscribers must wake runs, or when worker spawn must happen on a different machine than the subscriber. Deferred.
- **Subscription replay for late subscribers** — last-value cache as opt-in worker convenience. Protocol-level historical replay is the Store layer's job.
- **Value-predicate `until` conditions** — `until={"value": {"gt": 0.5}}`. Defer until concrete use case.
- **Subscription keepalive** — orchestrator periodically refreshes subscriptions; worker drops un-refreshed ones. Solves orchestrator-crash orphan-worker semantics. Add when a crashed orphan actually matters.
- **`Throttle` command** — subscriber asks worker to send a value less frequently than the worker's natural cadence. Add when a real cadence mismatch appears.
- **Store + Hasher (Layer 4)** — content-addressable run identity, persistent run/experiment metadata, reuse-by-hash. The natural next layer after v0.2.
- **Additional Channel backends (low priority)** — for multi-host orchestration where a shared filesystem is absent or fragile, **NATS JetStream** (preferred — subjects map to `name`, `last_per_subject` to the `latest` projection), then Redis Streams / Postgres; and, if it ever proves worthwhile, a **raw-file backend**. Obstacles for files: fan-out needs a single global sequence counter, non-destructive cursor reads (no consume-by-`unlink`), and a retention/GC policy, and it scales poorly for unbounded history (one file per message → inode pressure + O(N) directory scans per poll). A file backend is attractive only for register/flag/queue read patterns, not unbounded fan-out history. See also `docs/backlog/backends/`.
- **Visualization protocols (Layer 5)** — long-term.

These are captured in `docs/backlog/` as one-line entries or standalone files as they earn elaboration.

## Open implementation questions

Carried into the implementation-plan phase:

1. **`tick()` exposure is a design question, not just an impl detail.** The worker's per-iteration work (drain channel, update the subscription table incl. self-lifelines, evaluate `until`, reap, service due Requests, push Values, check lifeline count) is the thing users touch most. Whether it's a bare `runstate.tick(step=...)`, a context manager, or `for step in runstate.steps(...):` defines how the protocol *feels*. Settle during design, not after.
2. **`when={"every": K}` fires only at safe points — reuse v0.1's "safe points" language.** If the worker calls `tick()` every 100 steps, an `every=1` Request can only fire every 100 steps. This is the same worker-discipline matter as v0.1's command-checking cadence: the worker chooses safe points; subscriptions fire at safe points; sub-step granularity needs sub-step ticks. Provide a sub-step variant only if a real case demands it.
3. **Threading model for ThreadLauncher.** Worker callable runs in a thread; how does the worker's `tick()` interact with the orchestrator-thread reading the same Channel? Channel's locking should handle it, but verify.
4. **Cursor persistence mechanics (fan-in consumer).** The worker "persists its cursor" for crash-resume — but *where* (a `.cursor` sidecar? a row in the db?) and *atomic with what*? The cursor write vs. the side effects of processing the message it points past is the at-least-once / at-most-once boundary; pin it.
5. **Writer serialization without `consumed_at`.** Dropping `consumed_at` / `BEGIN IMMEDIATE` removes v0.1's consumer serialization. Specify concurrent `seq` assignment across multiple senders (orchestrators), and the "channel lock" the lazy-launch path (#8) relies on, under the new autocommit read path.

### Open questions that *are* protocol-level (not just impl)

6. **Registration ack** (still open, but the envelope tilts it toward (a)). Value-is-the-ack confuses "the worker registered my Request" with "the Request fired" — a `when={"step": 1000}` subscription is registered immediately but emits nothing for a long time. The envelope makes an explicit ack nearly free: the worker pushes a `lifecycle.ack` Value whose envelope `request_id` = the registered request's id (empty body), and the orchestrator's filtered `read`/`exists` on its own `request_id`s then distinguishes "registered" (ack present) from "not yet" (neither) from "rejected" (`lifecycle.nak`). Decide: (a) explicit `lifecycle.ack` on registration (now cheap, closes the gap — **lean (a)**), or (b) silent registration + drop NAK entirely. The current middle ground — implicit positive ack, optional NAK — is the worst of both for any orchestrator that must distinguish "not yet" from "rejected" (e.g. `broadcast` waiting on a barrier).
7. **Multi-orchestrator delivery — RESOLVED by the Layer-1 log.** v0.1's Channel was unicast-FIFO (whichever reader consumed a message, the others didn't see it), which would have broken lifecycle-as-Value + multi-subscriber + late-attach. The queue → log change (non-destructive reads, caller-owned cursors, retention) fixes this directly: `lifecycle.started`/`stopped` and pushed metrics are retained and every observer, including a late one, reads them from its own cursor. What remains is not delivery but (a) the **retention / GC policy** for the log, and (b) the **liveness-vs-completion split** (completion = retained log fact; crash-aliveness = the liveness tiers — handle probe / heartbeat) — both covered under "Layer 1: the substrate is a log". The `when={"now": true}` "query current state" idea is no longer load-bearing for completion (the log is authoritative); keep it only as a convenience for *live* state.
8. **Lazy-launch double-spawn race.** `send_request()` holds the channel lock for the liveness check + spawn + append, but `Popen` returning ≠ the worker has attached and `launcher.launched{handle}` is in the log. A second `send_request()` moments later can see no handle and spawn a duplicate. Resolve at the protocol/spec level: write `launcher.launched{handle}` as a spawn-intent record *inside the spawn lock, at/before exec*, so the next caller sees it; or fail-fast. This bit v0.1's equivalent and is not a mere impl detail.

Items 1–5 don't change the protocol design. Items 6–8 might — settle them before freezing the v0.2 schema.

## Revision history

- 2026-05-28: v0.2 design draft — full protocol redesign from v0.1, pull-first via Request/Unsubscribe/Value with subscription-as-pin discipline. Acknowledgments expressed as Values by convention rather than a separate message type. Supersedes v0.1's separate lifecycle/data split (which was never implemented anyway).
- 2026-05-29 (rev 11): **Consistency pass after rev 8–10.** Updated the `lifecycle.heartbeat` row to its primary pushed-beacon (liveness-floor) role alongside the secondary lifeline role; reconciled the "pull-first" framing (pull-first is the user-metric default; lifecycle/launcher events are pushed); hedged the cross-run-sync barrier (a worker past step N, or stopping first, never answers — a robust barrier needs the registration-ack #6 + a timeout, else it hangs); `sweep` now watches until *terminal* (clean stop, or detected-dead/`timeout` → `presumed_dead`), not only `lifecycle.stopped`; fixed stale wording ("base-vs-convention split" → substrate-vs-convention-suite; "protocol layer" → convention suite; "process liveness" → liveness tiers); the minimal example now demonstrates the common training-worker case, not a lifeline; bumped the header (status: scope re-affirmation pending; date: last revised 2026-05-29).
- 2026-05-29 (rev 10): **Liveness becomes a layered, opt-in failure detector with a portable handle; "Launcher" split into spawn vs watch/reap.** A spawner records a scheme-tagged liveness **handle** (`local://`, `slurm://`, `k8s://`, `ray://`) in `launcher.launched` (or the worker's `lifecycle.started`); this obsoletes the `.worker.pid` file and works cross-host where the scheme resolves. The detector has four precedence tiers — clean `lifecycle.stopped` → reaped `launcher.terminated{exit_code,signal}` (manner of death; only a `wait()`ing parent can produce it) → resolve-the-handle probe (actor-independent fact of death) → heartbeat-staleness floor — exposed as three reference configurations: (a) floor only, (b) +handle/observer-probe, (c) +handle+reaper (coalesces the probe and records manner once). `launch()` is now just spawn + emit handle (all a cluster scheduler allows; fire-and-forget); watch/reap is a separable optional role; `terminate()`/`is_alive()` resolve the handle (`kill`/`scancel`/`kubectl`). `direction` reframed as **audience, author-agnostic** (so the launcher/daemon may write the `to_orchestrator` stream). `RunResult` gains a `presumed_dead` outcome under the floor tier. Reference convention ships sane defaults (worker heartbeats; `LocalLauncher` writes a `local://` handle and reaps when used as a context manager) so it isn't batteries-not-included.
- 2026-05-29 (rev 9): **Liveness is convention + Launcher, never a substrate lease; NATS JetStream named as the preferred multi-host backend.** Reframed liveness-vs-completion: completion = `exists(lifecycle.stopped)` (log read); application-liveness = `latest(lifecycle.heartbeat)` + timeout (convention); process-liveness = the attach-written `.worker.pid` read by the Launcher (Layer 3). Dropped the "lease (substrate sidecar)" option — a mutable TTL'd presence would be a second ontology in Layer 1 and reintroduce reader-awareness; presence is instead expressed as emitted messages/artifacts, keeping the substrate a pure log. Launch-if-not-running keys off process-liveness (pid), not heartbeat. Backlog now names NATS JetStream as the preferred multi-host backend (subjects=`name`, `last_per_subject`=`latest`), Redis/Postgres as alternatives.
- 2026-05-29 (rev 8): **Collapsed the phantom "base protocol" tier into a two-layer model (B1–B5).** There is no privileged tier between substrate and conventions: Layer 1 is the substrate (an envelope log with an opaque body — TCP-like), and Layer 2 is the opt-in convention suite (subscriptions + lifecycle — HTTP-like); `when`/`until`, Request/Value/Unsubscribe, and the `kind` discriminator are all convention, since the substrate never evaluates `when`/`until` (the worker does) and is blind to the body. Conformance is two-tier (substrate-conformant vs speaks-the-standard-suite). Sharpened the lift-rule to "the substrate indexes on it *independently*" (so `name`/`request_id` stay separate envelope dimensions, not a merged key — B1). Weakened the ordering contract to per-direction FIFO with cross-direction global order as an optional backend capability (initiality is a single-host luxury, not the foundation — B2). Stated that observers are invisible to the worker and the subscription table / lifeline count are worker-side convention state (B3). Added an explicit `kind` (`request`/`unsubscribe`) discriminator to the to_worker body; Values carry none (B4).
- 2026-05-29 (rev 7): **Applied fixes from an independent two-reviewer pass.** Added `direction` to `read()` (the worker's own-direction drain); deleted the stale "multi-orchestrator delivery needs re-examination" note (now resolved by #7); fixed the `when`/`until` dataclass comments to the object-tagged forms; `peek_terminal` is an *existence* query (does a `lifecycle.stopped` record exist), not `latest`; unified the `Watcher`/`sweep` event type to `Envelope`; corrected the initiality/compaction inconsistency (register-compaction is a semantic change, not free GC — initiality holds only under full retention); hedged the `flag` ("cleanly done") and `time_seconds` (safe-point) claims; retention is "until explicit cleanup," not "life of the run"; noted the register `seq`/`step` monotonicity assumption; added open questions for cursor-persistence mechanics (#4) and writer-serialization (#5); noted v0.2 dbs are not v0.1-compatible.
- 2026-05-29 (rev 6): **Layer 2–3 coherence sweep** — reconciled the helpers with the envelope + cursor model. `nak` now carries the offending request's id in the **envelope `request_id`** (routing it to that requester) with a `{status, message}` body, instead of stuffing `request_id` in the body. `peek_terminal()` is a single `latest("lifecycle.stopped")` indexed lookup, not a scan, and drops its `backend` arg (one backend). `Watcher.iter_events` yields `(run_id, Envelope)` read via per-handle caller-owned cursors. The reference loop's "drain" step is now a `read(after=cursor, direction=to_worker)` + persist-cursor. Re-framed open question #4 (registration ack): the envelope makes an explicit `lifecycle.ack` nearly free, tilting the decision toward (a).
- 2026-05-29 (rev 5): **Caller-owned cursors; `consumed_at` dropped.** Each reader owns its cursor (an `int` `seq` position passed back via `read(after=…)`); the substrate keeps no per-reader state and no consumer registry — the only multi-observer cursor scheme needing no registry, coherent with whole-run retention. The worker is just a consumer that *persists* its cursor for crash-resume; observers keep ephemeral cursors filtered by `request_id`; the queue/log distinction collapses to "does the consumer persist its cursor?" with no consume-and-delete. Start position is just the initial cursor (`0` = earliest, current max = latest). A thin Layer-2 iterator hides cursor-threading for the common tail case. Channel surface: `send(body, *, name, request_id) -> seq`, `read(after, *, name, request_ids, limit, timeout)`, `latest(name)`, `close()`.
- 2026-05-28 (rev 4): **Adopted an envelope/body split for the wire + substrate.** `name` and `request_id` lift out of the message dict into the envelope `{seq, direction, name?, request_id?, body}`; the substrate routes/indexes/filters on the envelope and never parses the body. Principle: a field lifts to the envelope iff the substrate routes/indexes/filters on it — so `name` (routing key + register/flag projections), `request_id` (correlation + visibility filter), `direction`, and `seq` are envelope fields; `value`/`when`/`until`/`step` stay in the body. `step` stays in the body deliberately: it is the worker's clock, distinct from the substrate's per-message `seq`, and the substrate never compares steps. Request/Value/Unsubscribe become body shapes (discriminated by direction + populated envelope fields), not sibling top-level types; the typed dataclasses survive as opt-in Layer-2 conveniences. Visibility-by-`request_id` is read-side filtering, not enforcement, until a backend can enforce it.
- 2026-05-28 (rev 3): **Deleted `lifecycle.stop_at` as a reserved name** — a stop-at-step is `lifecycle.stop` + `when={"step": N}`, built from primitives, so the convention reserves one stop name, not two. **Removed FileChannel / the file backend** — v0.2 ships SQLite only; redis/postgres/raw-file backends are low-priority backlog (raw files viable only for register/flag/queue patterns, not unbounded fan-out history).
- 2026-05-28 (rev 2): **Layer 1 substrate revised from queue to log** to support multiple observers from the start. Reads become non-destructive with reader-owned cursors over a retained, globally-sequenced log (one log, projected to two directions — the globally-ordered log is the initial object; "2 logs" is a lossy projection of it, not the primitive). SqliteChannel's schema already is this log; only its read API changes. FileChannel stays single-observer (fan-out scales badly as one-file-per-message); SQLite becomes the canonical/default multi-observer backend. Liveness splits cleanly: completion = retained log fact, crash-aliveness = pid/lease sidecar. This resolves open question #5 (multi-orchestrator delivery). Also corrected the `lifecycle.stop` ack: a serviced stop returns a `request_id`-keyed Unit `Value` receipt, with `lifecycle.stopped` as the separate dying breath; `lifecycle.stop_at` collapses into `lifecycle.stop` + `when={"step": N}`.
- 2026-05-28 (rev): dropped **subscription-as-pin**. First draft made *all* subscriptions pin the worker (a crashed observer would have killed the run); a second draft fixed that with lifeline-leases + self-issued lifelines, but self-lifelines were notional ceremony and made a scheduled `StopAtStep` inexpressible. Final model: **the worker owns its own termination** (intrinsic / data-dependent / commanded), emits `lifecycle.stopped`, exits. Stop is influenced by two well-known *requests* — `lifecycle.stop` (= v0.1 StopNow) and `lifecycle.stop_at` (= v0.1 StopAtStep) — not message types, not lifeline arithmetic; this restores a clean StopAtStep. Lifelines survive only as a **narrow, externally-issued convention for the no-intrinsic-work service worker** (observer-vs-lifeline distinction so observers don't pin). Object-tagged `when`/`until` for a clean schema. Surfaced protocol-level open questions: registration ack, multi-orchestrator delivery, lazy-launch double-spawn.
