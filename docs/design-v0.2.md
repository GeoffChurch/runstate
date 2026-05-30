# runstate v0.2 — Design

**Status:** converged design; six convention decisions resolved + a three-agent review folded (rev 3). **Envelope freezable; `lifecycle.heartbeat` body held pending §12.6 (`consumed_seq` scoping); implementation-plan items in §12.**
**Date:** 2026-05-29.
**Supersedes:** v0.1 (`design-v0.1.md`). Full redesign.
**Decision trail:** the dialectic and rejected alternatives that produced this live in `design-v0.2-exploration.md`. This doc is the destination.

---

## 1. What this is

runstate is a protocol for **cooperative bidirectional control of a long-running scientific worker**, plus a reference Python implementation. It's a stack of thin layers: an **opinion-free transport substrate** (a per-run topic log) with **opt-in conventions** on top (cooperative-control, subscription, lifecycle, launcher). The value is the conventions; the substrate is generic enough to back with an off-the-shelf log (NATS JetStream, Kafka, Redis Streams).

## 2. Entities & layers

```
backend          — storage engine: SQLite (v0.2); NATS JetStream / Kafka / Redis / Postgres (later)
substrate        — a per-run TOPIC LOG over a backend. Opaque body; truly opinion-free.        [§4]
conventions      — opt-in protocols the substrate is blind to but carries:
  cooperative-control — content categories + the per-role subscription protocol                 [§5]
  subscription        — control.* bodies, the scheduling condition-algebra                        [§6]
  lifecycle           — lifecycle.* well-known events, worker-owned termination                   [§7]
  launcher            — launcher.* names, the liveness handle, the failure detector               [§8]
orchestration    — reference helpers (Launcher, Watcher, sweep)                                  [§9]
```

**Actors** (roles are *convention*, not substrate — the substrate is author-agnostic):
- **worker** — the workload side of a run. *Consumes* `control.*`; *produces* `lifecycle.*` and user `value`s. One OS process by default; a multi-process workload (DDP, multi-actor) is a convention (a body-level participant id), so "worker" = the workload side, not literally one process.
- **orchestrator** — *produces* `control.*`; *consumes* `value`s, `lifecycle.*`, `launcher.*`.
- **observer** — read-only; *consumes* the log, produces nothing. **Invisible to the worker.**
- **launcher** — spawns the worker, records its liveness handle; *produces* `launcher.*`.

**The schema stack** (there is no single "the schema"):
- **Envelope schema** — the substrate's wire contract (§4). Fixed envelope fields + an **opaque body**. Freezable on its own; constrains neither `topic` semantics nor body shapes.
- **Per-convention schemas** — subscription / lifecycle / launcher each strictly pin their own well-known bodies (`additionalProperties: false`), each frozen and versioned on its own timeline (§10).

## 3. Backend

The substrate's storage. v0.2 ships **SQLite only** (stdlib, embedded, zero-dependency; a single-file `messages` table with an autoincrement `seq` is already a retained, sequenced log). Multi-host backends are backlog (§14); **NATS JetStream is preferred** — subjects map to `topic.name`, stream sequence to `seq`, durable/ephemeral consumers to caller-owned cursors, `last_per_subject` to the `latest` projection.

## 4. Substrate: a per-run topic log

The substrate is a per-run, append-only **log of envelopes** addressed by an opaque **topic**. It has no notion of "worker," "direction," subscriptions, or any message shape. Its one near-zero opinion: messages carry a routing key, a correlation id, and an opaque body.

### Envelope

```python
@dataclass class Envelope:
    seq: int                  # substrate-assigned; per-topic monotonic (global iff the backend has one sequencer)
    topic: str                # PROTOCOL routing key — a CLOSED, protocol-owned vocabulary
    name: str | None          # APPLICATION identifier — OPEN, app-owned (e.g. "train.loss"); None for fixed protocol topics
    request_id: str | None    # correlation + visibility; None = unaddressed / broadcast
    body: dict                # opaque to the substrate — a convention interprets it
```

The split of `topic` (closed, protocol-owned) from `name` (open, app-owned) is load-bearing: it makes `topic` a **finite, inspectable vocabulary** and keeps application identifiers out of it, so there is **no reserved-vs-user collision** to resolve (a user metric named `lifecycle` is `{topic: value, name: "lifecycle"}`, distinct from `{topic: lifecycle.*}`) — no sigil, no reserved-root scheme needed.

**Lift-rule** (what earns an envelope field): a field is in the envelope iff the substrate indexes/routes/filters on it — `topic` (routing, `latest`), `name` (the latest/by-metric dimension), `request_id` (correlation + visibility). `name` is its own field rather than a `topic` sub-level because it differs in *ownership/openness* (closed protocol vs open app) — which is exactly what merging them would have collided.

### Contract

- **Append + opaque body.** `send` appends and returns its `seq`. The substrate never parses `body`.
- **Caller-owned cursors.** A reader owns its cursor (a `seq` position passed back on the next read). The substrate keeps **no per-reader state and no registry of who is reading** — N readers each see every matching envelope. (Whole-run retention follows: the substrate needn't know its readers to decide what to keep.) Crash-resume = persist the `seq`; otherwise re-read from `0`. Start position is just the initial cursor.
- **Per-topic FIFO is the contract; global cross-topic order is an optional capability** (present where a single sequencer is — SQLite's autoincrement, a server).
- **Retention** until the run's channel is explicitly cleaned up. GC policy is open (§12).

### Surface

```python
send(body: dict, *, topic: str, name: str | None = None, request_id: str | None = None) -> int   # append; returns seq
read(after: int = 0, *, topics=None, name=None, request_ids=None, limit=None, timeout=None) -> list[Envelope]
    # topics: exact set or prefix/wildcard patterns (e.g. ["control.>"]) — opaque string matching
latest(topic: str, name: str | None = None) -> Envelope | None        # most recent on a (topic, name)
close()
```

`latest` is a substrate primitive (not just a `read` helper) on backend-optimization grounds (SQLite indexed `ORDER BY seq DESC LIMIT 1`; NATS `last_per_subject`). Its well-definedness requires a **single writer per (topic, name)** (the common case) or a single sequencer.

### Read projections

Stateful-communication shapes are **queries over the one log**: register/latest = `latest`; flag/terminal-fact = existence; queue = a single consumer persisting its cursor; bounded window = last-*k* by `seq`; tail = a cursor read. Per-(topic,name) compaction is a *semantic* choice (makes the register the retained object), not free GC — deferred, chosen with eyes open.

## 5. Cooperative-control convention (foundational)

The thinnest, most fundamental convention — the one structural opinion that makes runstate a *control* protocol. **It is not a binary in the namespace.** The namespace is **content-typed**, and each category has a fixed producer-role and consumer-set *by convention*:

| Category (reserved `topic`s) | Produced by | Consumed by |
|---|---|---|
| `control.subscribe` / `control.unsubscribe` / `control.stop` | orchestrator | worker |
| `lifecycle.*` (started/stopped/phase/heartbeat/nak) | worker | observers |
| `launcher.*` (launched/terminated) | launcher | observers |
| `value` (user metrics; `name` distinguishes them) | worker | observers |

Each role is then just a **subscription map** (the protocol):
- **worker** reads `control.>`; produces `lifecycle.*` and `value`s.
- **orchestrator** writes `control.*`; reads `lifecycle.>`, `launcher.>`, and the `value`s it wants.
- **observer** reads those; produces nothing.
- **launcher** writes `launcher.*`.

So **"the worker is the consumer of `control`" is an emergent fact of the protocol, not a reified worker-vs-everyone axis.** This is why `direction` is gone (the launcher→observer flow never involves the worker, so a worker-centric binary couldn't classify it; content categories do, cleanly). Finer routing *within* the value category is `name` + `request_id`, never the category. A multi-process workload tags a body-level participant id; the substrate and categories are unchanged. `run_id` = one channel = one bipartite worker/outside log; an *experiment* of N runs is N channels coordinated at a higher layer (§9), never one N-party channel.

## 6. Subscription convention

The pull/push vocabulary. Message kind is the `topic` itself — `control.subscribe` vs `control.unsubscribe` vs `control.stop` vs `value` are distinct topics, so **there is no separate `kind` discriminator** (the closed `topic` vocabulary is the discriminator).

```python
# control.subscribe — name = target value; request_id correlates; body = a schedule
{ "from"?: Condition, "every"?: Condition, "until"?: Condition }

# control.unsubscribe — request_id = the subscription to cancel; body = {}
# control.stop        — body = { "from"?: Condition }  (a one-shot; default = stop now)

# value (worker → observers) — name = which metric; request_id = the sub it answers (or None = broadcast)
{ "value": Any, "step"?: int }      # `value` payload is Any (app data); `step` = the worker's clock
```

### The scheduling condition-algebra

A subscription **fires at `from`** (default: the next safe point), **repeats every `every`** (if present — *absent ⟹ one-shot*), and **expires per `until`** (if present; else open for a recurring sub). `from`/`every`/`until` are each a **Condition** over the worker's coordinates `(step, wall-time, fire-count)`:

```
Coord        := {step: N} | {time_seconds: S}                 # from / every
UntilTerm    := Coord | {count: C}                            # until also admits count
Condition[T] := T | {any: [Condition[T], …≥1]} | {all: [Condition[T], …≥1]}   # fully recursive
#   from, every : Condition[Coord]      until : Condition[UntilTerm]
#   (count grammatical only in `until` — structurally, via the per-slot term type)
```
- **`any` = whichever crosses first** (min / OR); **`all` = whichever crosses last** (max / AND).
- Clocks per slot: `from`/`until` are *absolute* (step value, seconds-since-registration, total fires); `every` is *deltas since the last fire*.
- The algebra is freely associative; **no normal form** — equivalent encodings (`any[a, any[b,c]] ≡ any[a,b,c]`, dominated thresholds) are *behaviorally inert* (the boolean eval is associativity-invariant), and runstate never compares/dedups/hashes conditions, so canonicalization would buy nothing. (Optional soft `maxDepth` as a pure resource guard.)

Examples: `{}` = fire once now · `{from: {step: N}}` = fire once at step N · `{every: {any: [{step:10}, {time_seconds:60}]}}` = every 10 steps or 60 s, whichever first, forever · `{every: {step:1}, until: {all: [{step:5000}, {count:100}]}}` = every step until *both* step 5000 and 100 fires.

Helper sugar (Tenacity-style operators): `now()`, `at(step=N)`, `every(steps=K, seconds=S)`; `Step(10) | Time(60)` → `any`, `Step(5000) & Count(100)` → `all`.

### Correlation, visibility, acks

- `request_id` (envelope) correlates a `value` to its `control.subscribe` and scopes visibility: an observer reads `request_id ∈ {None} ∪ {its ids}`. The worker reads all `control.*` regardless. Visibility is **read-side filtering, not enforcement**, until a backend can enforce it.
- **No registration ack, no per-request receipt.** "Did my request land?" = the worker's **consumption watermark**: the `consumed_seq` it publishes on its heartbeat (§7) — its read position **in the inbound `control` order** (not a global `seq`; see §12) — so a request is registered once `consumed_seq ≥ its seq` *and* no `nak` arrived. The worker advances `consumed_seq` only **after** durably registering/naking, so it's a true registration watermark (not merely "read past"). **Failure** is `lifecycle.nak` (envelope `request_id` = the offending request; body `{status, message}`), covering **only *statically* unsatisfiable** requests — a one-shot trigger already passed, `until` already true, or a step-keyed condition sent to a **stepless** worker. (The *dynamic* "will never fire" — the worker *stops* before reaching the trigger — is signalled by `lifecycle.stopped`, not `nak`; a merely *slow* worker is a patience-cap concern, §9. Three never-fire causes, three handlers.)

**Reference worker loop** (each `tick(step=…)`):
1. `read` new `control.*` after the persisted cursor; register subscriptions / drop unsubscribed / `nak` the unsatisfiable; **then** advance + persist the cursor and publish `consumed_seq` (registration before watermark); note any `control.stop`.
2. Service due subscriptions (emit `value`s); reap subscriptions whose `until` is met. **Register before reap** (step 1 before step 2) so a keepalive *refresh* never transiently zeroes the lifeline count.
3. Evaluate the stop decision (§7); if stopping, emit `lifecycle.stopped` and exit.

## 7. Lifecycle convention

Well-known **outbound** events (`worker → observers`), reserved `lifecycle.*`.

| Topic | Semantics |
|---|---|
| `lifecycle.started` | Pushed on attach. Body `{handle, hostname?, attached_at?}` — the worker self-reports its **liveness handle** (§8) when no launcher recorded one. |
| `lifecycle.stopped` | The cooperative dying breath; body `{reason, error?, final_step?}`. **Existence = the run cleanly finished** (a retained log fact). A crashed worker emits nothing — absence ≠ alive (§8). |
| `lifecycle.phase` | Body `{phase}`. `latest` queries the current phase. |
| `lifecycle.heartbeat` | **Pushed beacon** (`request_id=None`), **tick-driven** (a hung loop stops it), periodic. Body `{step?, consumed_seq}` — serves **liveness** (staleness), **progress** (step advancing), and the **registration watermark** (§6; `consumed_seq` = the worker's read position in its inbound `control` order). `step` is null for a service worker with no step. No embedded timestamp (staleness uses the reader's arrival clock). |
| `lifecycle.nak` | Negative ack (§6); body `{status, message}`, envelope `request_id` = the offending request. |

**Worker-owned termination.** Stopping is always the worker's decision — intrinsic completion, data-dependent stops, and commanded stops (`control.stop`) all feed one stop check. The orchestrator never *removes* a worker. (`control.stop` "landed" = the watermark; its *effect* = `lifecycle.stopped`; there is no separate stop receipt.)

**Lifelines** (the no-intrinsic-work *service worker* only) **need no dedicated mechanism**: the service worker **ref-counts its active subscriptions and stops at zero**. The observer-vs-lifeline distinction *is* read-vs-subscribe — a passive observer *reads* (invisible, never pins); a client *subscribes* (counted, pins, because it's asking the service to produce). Keepalive/orphan: clients subscribe with a time-bounded `until={time_seconds: N}` and refresh; a crash ⟹ expiry ⟹ count drops ⟹ the service can die (reuses the scheduling algebra). Bootstrap: lazy-launch (the launching subscribe is the first lifeline) or eager + a startup grace window. A training worker ignores all of this — it lives by its loop.

## 8. Launcher convention + the liveness detector

Reserved `launcher.*`, written by the spawner/reaper — a Layer-3 / process-level concern, not substrate or lifecycle.

| Topic | Body |
|---|---|
| `launcher.launched` | `{handle, status}` — spawn-intent + the worker's liveness **handle** (also resolves the lazy-launch race, §9). |
| `launcher.terminated` | `{exit_code?, signal?, reason: "exited" | "killed"}` — the *manner* of death; only a `wait()`ing parent can produce it. |

**The handle** is a portable, scheme-tagged token: `local://host/pid?start=T`, `slurm://jobid`, `k8s://ns/pod`, `ray://actor`. Resolving it (`kill -0`, `squeue -j`) answers liveness **actor-independently** — robust even if the launcher is gone, cross-host where the scheme resolves. It obsoletes a `.worker.pid` file (the handle lives in the log). **Single source of truth:** the worker self-reports via `lifecycle.started`; `launcher.launched` carries the spawn-intent + the launcher's known handle.

**Liveness is a layered, opt-in failure detector** — none of it substrate-owned (presence is *emitted messages*, never substrate state; a mutable TTL'd *lease* is deliberately avoided). Best-to-worst:
1. **Clean completion** — `lifecycle.stopped` exists.
2. **Reaped death** — `launcher.terminated` (the manner; needs a reaper).
3. **Probe the handle** — resolve it for the *fact* of death (actor-independent).
4. **Heartbeat staleness** — `lifecycle.heartbeat` older than a threshold ⟹ crashed/hung. The universal floor. Because the beacon is tick-driven, staleness catches *hangs* (not just crashes) — but for the same reason a worker in a legitimately **long single step** (a 20-min epoch, a giant batch) stops beaconing and looks dead. So the threshold must exceed the worker's *max* inter-beacon gap, which the reader often can't know a priori, and progress-staleness doesn't help (the `step` is frozen *because it's in progress*). This is the irreducible **dead-vs-busy** ambiguity: the threshold is a per-workload tuning, and a worker that *can* sub-divide a long step should beacon within it.

Three reference configurations: **(a)** floor only · **(b)** + handle (observers self-probe) · **(c)** + handle + reaper (a daemon/stay-attached launcher probes/reaps once and writes the result).

**Spawn vs watch/reap split.** `launch()` does the irreducible job — spawn + emit handle — and returns (all a cluster scheduler permits; fire-and-forget). Watching/reaping is a *separable* role. `terminate()` resolves the handle and kills (`kill`/`scancel`/`kubectl`), not via a parent relationship.

**Defaults (opinion-free ≠ batteries-not-included):** the reference worker loop heartbeats; the reference `LocalLauncher` writes a `local://` handle and, as a context manager, reaps and emits `terminated`. Every tier is removable.

## 9. Orchestration helpers (Layer 3)

Reference tooling; assumes the conventions (a worker that opts out composes its own loop from `send`/`read`/`latest` + the liveness tiers).

```python
class Launcher(Protocol):                                    # target is launcher-specific (callable vs argv)
    def launch(self, run_id, target, **kwargs) -> LaunchHandle: ...
    def open_channel(self, run_id) -> Channel: ...           # first control.* lazily launches (§12)

class LaunchHandle(Protocol):                                # concrete per launcher (thread / subprocess)
    run_id: str; channel: Channel
    handle: str                                              # portable liveness/terminate token (§8)
    def is_alive(self) -> bool: ...
    def wait(self, timeout=None) -> int | None: ...          # block until done (+reap); None for a thread
    def terminate(self) -> None: ...                         # force-kill where the substrate allows

class Watcher:
    def add(self, handle: LaunchHandle) -> None: ...
    def observe(self, run_id: str, channel: Channel) -> None: ...   # handle-free: late-attach / observe-only (§12)
    def iter_events(self, timeout=None) -> Iterator[tuple[str, Envelope]]: ...   # the stream (deltas)
    def poll(self, run_id) -> RunStatus: ...                 # the fold (Running | RunResult), non-blocking
    def wait(self, run_id, *, on_event=None, timeout=None) -> RunResult: ...
    def wait_all(self, *, on_event=None, timeout=None) -> dict[str, RunStatus]: ...   # total over tracked runs
    def broadcast(self, name, schedule, *, request_id=None) -> str: ...   # shared request_id; the cross-run barrier

# A run's current status: still-running (a live snapshot) or a terminal verdict.
# The Running arm carries watcher-unique state (beacon_age = the gradient toward
# presumed-dead) not on the raw event stream, so poll is lossless rather than
# returning Optional[RunResult] (None-as-pending). peek_terminal stays Optional —
# the record plane is stateless and can't populate Running.
RunStatus = Running | RunResult
@dataclass class Running:
    step: int | None; beacon_age: float | None              # done == False

@dataclass class RunResult:                                  # done == True
    run_id: str | None
    outcome: str   # CLOSED: "completed" | "stopped" | "errored" | "killed" | "presumed_dead"
    reason: str    # verbatim per-tier label (the raw "why", finer than the bucket)
    error: str | None; final_step: int | None; elapsed: float
    # No `success`: a pure projection of `outcome` that would bake one contested
    # policy into the producer. Consumers apply their own (sweep fails on the
    # bottom three). `outcome` (normalized, cross-tier) and `reason` (raw, per-tier)
    # are orthogonal; a clean non-completion is outcome="stopped", reason="commanded".

# peek_terminal is the RECORD-based verdict (a terminal envelope exists); the
# Watcher adds the INFERENCE-based tiers (probe + heartbeat staleness → presumed_dead).
def peek_terminal(channel) -> RunResult | None:            # clean stop OR reaped launcher.terminated; else None
def sweep(variants, launcher, *, on_event=None, resume=True, stop_on_failure=False, watcher=None) -> list[RunResult]:
    # sequential; watches each until terminal (clean stop OR detected-dead → presumed_dead)
```

**Cross-run synchronization.** `Watcher.broadcast(subscribe(name="loss", from=at(step=100)))` fans one subscription across all active runs (one shared `request_id`; the `run_id` disambiguates responses). It's the primary cross-run mechanism — *no Experiment class*. It is a **pure synchronization**: it blocks until every *live* run reaches the point, so a slow-but-healthy run **legitimately delays it, unbounded by design** (that is what "synchronize" means). Each run resolves to exactly one of: **fires** (the value); **`nak`** (statically unsatisfiable — e.g. already past step 100); **`lifecycle.stopped`** (stopped before reaching it — excluded); **heartbeat-stale** (crashed/hung — excluded). A **bounded-latency** caller MUST additionally supply a **patience cap** — a wall-clock deadline after which the barrier returns *partial* results (still-running slow runs reported as pending). So the cap is *optional for a pure sync* but *required for a bounded wait*; "no separate timeout" holds only for the unbounded-sync reading. (Three distinct never-fire causes → three handlers: static-unsatisfiable → `nak`; stops-early → `stopped`; too-slow-for-my-patience → the cap.)

## 10. The schema stack (freeze story)

No single schema:
- **Envelope schema** — `{seq, topic, name?, request_id?, body}` with `body: object` (opaque). Close to freezable; constrains neither `topic` semantics nor body shapes.
- **Convention schemas** — each strictly pins its own well-known bodies (`additionalProperties: false`), independently versioned. Adding a field to a well-known body is a deliberate convention-version bump. **User `value` bodies** pin only the *wrapper* (`{value, step?}`); the `value` payload is `Any`. Validation is opt-in/layered: the substrate never validates; "opt-in convention" means "you needn't emit `lifecycle.*`, but if you do, conform."

This is the cut that dissolves "blocking for the schema": the convention decisions block specific *convention* schemas, downstream — not the envelope. **Freeze status:** the envelope is freezable now; the `lifecycle.heartbeat` body is *not* until `consumed_seq` is scoped, and `launcher.launched`'s `status` domain must be pinned first (both §12).

## 11. Three clocks

`seq` = the substrate's transport order (per topic; global where a sequencer exists). `step` = the worker's logical clock (a body field). wall-clock = real time. **All scheduling predicates evaluate in the worker's tick, against `step`/wall-clock, never `seq`**, and only at safe points. (`consumed_seq` in the heartbeat is the worker's read position in its **inbound `control` order** — not a global-`seq` position, not a fourth clock; §12 explains why it's scoped to inbound.) `latest` orders by `seq`, = "most recent emitted" only when a (topic,name)'s emissions are seq-monotonic in `step` (true for a single writer).

## 12. Open questions — implementation-plan items

The six convention decisions are settled (see revision history). These remain; **none change the wire *envelope*** (freeze it now), but a few touch convention *bodies* or the seq contract (flagged ▸) and gate freezing those bodies:

1. **Lazy-launch double-spawn race** — write `launcher.launched` spawn-intent inside the channel lock at/before exec. ▸ pins `launcher.launched`'s `status` domain (`intended | running | failed`?) — a body-shape question.
2. **`send_request` ↔ `Channel` seam** — `open_channel` returns a launch-wrapping channel.
3. **`Watcher.observe`** — the handle-free / late-attach / observe-only path (today's `add` is handle-only, excluding the pure-observer story the design centers on).
4. **`broadcast` returns the assigned `request_id`** (else cancel-the-lot is unreachable).
5. **Cursor-persistence mechanics** — where/how the worker persists its cursor, and the at-least-once / at-most-once boundary; it must advance `consumed_seq` only *after* durable registration (§6). At-least-once means a `value` or a `count`-`until` can over-fire on crash-replay — an observable semantic, so pin it.
6. **▸ `consumed_seq` scoping** — `seq` is per-topic (global only on a single-sequencer backend, §4); a single scalar watermark over the worker's several `control.*` topics is well-defined only if **inbound `control` is single-ordered**. Scope `consumed_seq` to that inbound order (cheap on every backend; needs no global cross-everything order). **Gates freezing the `lifecycle.heartbeat` body.**
7. **▸ Multi-orchestrator support** — v0.1 permitted several orchestrator-role channels. Under multiple `control.*` writers, `latest(control.*)` loses its single-writer precondition (§4) and is ill-defined for "was a stop requested?". Decide whether v0.2 keeps multi-orchestrator and what it does to `latest` on control topics. (Coupled to #8.)
8. **Author / provenance (deferred, not blocking)** — nothing currently routes on author, so by the lift-rule it's not an envelope field. **Stopgap:** encode author as a `request_id` prefix — `request_id` is an opaque *string*, so `"webui:<unique>"` rides through the visibility filter (set-membership over opaque ids) and attributes exactly the addressed `control` messages where multi-orchestrator attribution matters (unaddressed pushes are the worker's anyway). It earns a real envelope field only if provenance/authz becomes load-bearing — coupled to #7.
9. **Writer-serialization + GC/retention** — concurrent `seq` assignment across senders; the channel lock; the retention policy (completion / `peek_terminal` / resume guarantees hold only under full retention).

## 13. Rejected alternatives

(Diagnoses in `design-v0.2-exploration.md`.)

- **Queue substrate (consume-once).** Breaks multi-observer. → a log with caller-owned cursors.
- **`direction` as a substrate primitive (binary `to_worker`/`to_orchestrator`).** Smuggled a role ontology into a "role-free" substrate and couldn't classify the launcher→observer flow. → **content-typed topics + a per-role subscription protocol**; the worker/outside split is emergent, not a namespace axis.
- **A single monolithic message schema.** → the schema stack (§10).
- **A merged `topic`+`name` key (or `topic` carrying user identifiers).** Created a reserved-vs-user collision needing a sigil/root. → **split fields**: `topic` closed/protocol, `name` open/app; collision impossible by construction.
- **A `kind` body discriminator.** Redundant once topics are content-typed. → the `topic` *is* the discriminator.
- **Per-request registration ack (`lifecycle.ack`) and per-request stop receipts.** *Relocated, not eliminated* — "did my request land?" is answered by the heartbeat **`consumed_seq` watermark** + `nak`, amortizing all pending acks into one piggybacked monotonic scalar instead of N per-request messages. (Don't drop `consumed_seq` from a minimal heartbeat thinking "we don't do acks" — it *is* the ack.) Barrier backstop = heartbeat staleness + an optional patience cap (§9).
- **Heartbeat-as-lifeline / requested heartbeat.** Re-created the accidental-pin trap. → heartbeat is a **push beacon** (unsubscribable); lifelines = service-worker **subscription ref-count** (observer-vs-lifeline = read-vs-subscribe).
- **A substrate liveness *lease*** (mutable TTL'd presence). → liveness as emitted messages + observer-side probing.
- **A normal form for the condition algebra.** Canonicalization buys nothing here (we never compare/hash conditions). → accept inert algebraic redundancy; keep the algebra fully recursive.
- **A separate `stop_at` name; `now`/`step:N` as `when` primitives.** → `control.stop` + `from`; one-shot = omit `every`; `now`/`at` are helper sugar.
- **FileChannel / a file backend.** Fan-out scales badly. → SQLite-only; topic-log backends (NATS/…) later.

## 14. Scope: v0.2 vs later

**v0.2 ships:** the substrate (SQLite topic log) + the four conventions + the orchestration helpers, once the per-convention schemas freeze and §12 is settled in implementation.
**Layer 4 (later):** Store + Hasher (relational run/experiment metadata; content-addressable fingerprinting; reuse-by-hash).
**Long-term:** richer data-plane Progress + a viewer-discovery protocol — its *own* protocol in `protocol/`, distinct from this control protocol. Compose, don't conflate.

## Revision history

- 2026-05-29 (rev 3): **Folded a three-agent review** (calibrated red-team ×2 + an unprimed independent re-derivation that *converged* on the substrate, scheduling, liveness, and cross-run shape — validating the bones). Fixes: the cross-run barrier is a **pure synchronization** (a slow-but-healthy run legitimately delays it, unbounded; bounded-latency callers supply a **patience cap** returning partial results — corrected the "no timeout needed for correctness" overclaim); `nak`-on-unsatisfiable is **static-only** (dynamic never-fire → `stopped`, slow → patience cap; stepless workers `nak` step-conditions); `consumed_seq` scoped to the **inbound `control` order** (well-defined on per-topic-seq backends; gates freezing the heartbeat body) and published **after** durable registration; **register-before-reap** made a normative loop invariant; documented the tick-beacon vs legitimate-long-step tension; per-slot threshold types (`count` only in `until`, structurally); §13 ack relabelled **relocated, not eliminated**; new open items — multi-orchestrator vs `latest(control.*)`, and author/provenance (deferred, with a `request_id`-prefix stopgap). **Envelope freezable; heartbeat body held pending `consumed_seq` scoping.**
- 2026-05-29 (rev 2): **Folded the six resolved convention decisions.** #6 split-field envelope `{seq, topic, name?, request_id?, body}` with a closed content-typed `topic` vocabulary and an open app `name` (dissolves the reserved-vs-user collision); #3 dissolved (`topic` discriminates — no `kind`); #5 the recursive `from`/`every`/`until` condition-algebra over (step, time, count) with `any`/`all`, no normal form (one-shot = omit `every`); #1 no registration ack / stop receipt (heartbeat `consumed_seq` watermark + `nak`-on-unsatisfiable; barrier backstop = heartbeat staleness, no separate timeout); #2 heartbeat = tick-driven push beacon `{step?, consumed_seq}`, lifelines = service-worker subscription ref-count (no reserved name); #4 opaque envelope body / strict-pinned convention bodies / `Any` user payload. Replaced the in/out binary with content-typed categories + the per-role subscription protocol. Remaining open items are implementation-plan only (§12).
- 2026-05-29 (rev 1): Clean rewrite from the converged architecture (topic-log substrate + conventions). Supersedes `design-v0.2-exploration.md` (the full decision trail).
