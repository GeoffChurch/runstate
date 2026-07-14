# runstate v0.2 — Design

**Status:** converged design, **implemented in v0.2** (rev 4 — implementation pass). Substrate + the four conventions + orchestration helpers + the JSON Schema stack are built and tested. The schemas are written and frozen; the two previously-held bodies are pinned — `lifecycle.heartbeat` = `{step, consumed_seq}` and `launcher.launched.status` = `running`. Remaining §12 items are consciously **deferred** (annotated there), not blocking.
**Date:** 2026-05-29.
**Supersedes:** v0.1 (`design-v0.1.md`). Full redesign.
**Decision trail:** the dialectic and rejected alternatives that produced this live in `design-v0.2-exploration.md`. This doc is the destination.

---

## 1. What this is

runstate is a protocol for **cooperative bidirectional control of a long-running scientific worker**, plus a reference Python implementation. It's a stack of thin layers: an **opinion-free transport substrate** (a per-run topic log) with **opt-in conventions** on top (cooperative-control, subscription, lifecycle, launcher). The value is the conventions; the substrate is generic enough to back with an off-the-shelf log (NATS JetStream, Kafka, Redis Streams).

## 2. Entities & layers

```
backend          — storage engine: SQLite + Postgres; NATS JetStream / Kafka / Redis (later)
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

The substrate's storage. Three backends ship: a durable **SQLite** one (stdlib, embedded, zero-dependency; a single-file `log` table with an autoincrement `seq` is already a retained, sequenced log), an in-process **Memory** one (a shared list, for in-proc orchestration and tests), and — post-v0.2 — the cross-host **Postgres** one (`specs/channel-postgres.md`: one shared `log` table keyed `(run_id, seq)`, the primary key as the CAS arbiter). All pass the same conformance suite. SQLite deployment: WAL is the default journal mode (with a 5 s `busy_timeout`); WAL needs shared memory a network filesystem cannot back, so an NFS-homed deployment exports `RUNSTATE_SQLITE_JOURNAL_MODE=DELETE` (the NFS-safe rollback journal). Further topic-log backends remain possible — e.g. NATS JetStream: subjects map to `topic.name`, stream sequence to `seq`, durable/ephemeral consumers to caller-owned cursors, `last_per_subject` to the `latest` projection.

## 4. Substrate: a per-run topic log

The substrate is a per-run, append-only **log of envelopes** addressed by an opaque **topic**. It has no notion of "worker," "direction," subscriptions, or any message shape. Its one near-zero opinion: messages carry a routing key, a correlation id, and an opaque body.

### Envelope

```python
@dataclass class Envelope:
    seq: int                  # substrate-assigned; the per-log total order — contiguous, 1-based (one sequencer per log)
    topic: str                # PROTOCOL routing key — a CLOSED, protocol-owned vocabulary
    name: str | None          # APPLICATION identifier — OPEN, app-owned (e.g. "train.loss"); None for fixed protocol topics
    request_id: str | None    # correlation + visibility; None = unaddressed / broadcast
    body: dict                # opaque to the substrate — a convention interprets it
```

The split of `topic` (closed, protocol-owned) from `name` (open, app-owned) is load-bearing: it makes `topic` a **finite, inspectable vocabulary** and keeps application identifiers out of it, so there is **no reserved-vs-user collision** to resolve (a user metric named `lifecycle` is `{topic: value, name: "lifecycle"}`, distinct from `{topic: lifecycle.*}`) — no sigil, no reserved-root scheme needed.

**Lift-rule** (what earns an envelope field): a field is in the envelope iff the substrate indexes/routes/filters on it — `topic` (routing, `latest`), `name` (the latest/by-metric dimension), `request_id` (correlation + visibility). `name` is its own field rather than a `topic` sub-level because it differs in *ownership/openness* (closed protocol vs open app) — which is exactly what merging them would have collided.

### Contract

- **Append + opaque body.** `send` appends and returns its `seq`. The substrate never parses `body`.
- **Value-snapshot at `send`; reads return copies.** The body is snapshotted when `send` returns — mutating the dict afterwards, or a returned `Envelope.body`, never alters the log. A body that isn't JSON-serializable fails **at `send`**, naming the problem — never later, on an innocent reader.
- **Compare-and-append (CAS).** `send(..., expected_seq=S)` appends **iff** the log's last `seq` is `S` (`0` = empty log) and returns the new `seq`; otherwise it returns `None` — the claim is *provably lost* (the log moved). The check-and-append is **one critical section across handles and processes**. A backend may raise only when the outcome is *indeterminate* (e.g. a competing writer wedged past the backend's wait bound), never to report a loss. Opinion-free (it reads a `seq`, never a body); it is the standard optimistic-concurrency primitive of log stores (NATS expected-last-seq, EventStore `expectedVersion`). The single-claim guards (run-episodes self-claim, §12.1) rest on it.
- **Every handle is thread-safe.** One Channel handle may be shared across threads (the ThreadLauncher topology — one instance held by both the worker and the Watcher): under mixed traffic through one handle — CAS claims interleaved with plain sends — every acknowledged send is durably in the log and at most one CAS wins; each backend serializes its own connection use internally.
- **Caller-owned cursors.** A reader owns its cursor (a `seq` position passed back on the next read). The substrate keeps **no per-reader state and no registry of who is reading** — N readers each see every matching envelope. (Whole-run retention follows: the substrate needn't know its readers to decide what to keep.) Crash-resume = persist the `seq`; otherwise re-read from `0`. Start position is just the initial cursor.
- **A per-log total order is the contract**: `seq` is one **contiguous, 1-based** sequence — exactly `1..N`, no gaps, across all topics — assigned by the log's single sequencer (SQLite's autoincrement; Postgres's `(run_id, seq)` key; the Memory list); the CAS's `0 = empty log` is its base case, and the conformance suite pins contiguity on every backend. The conventions depend on it — the pairing-by-`seq` rule (§7) compares positions *across* topics in every instance — so a backend offering only per-topic FIFO cannot host them.
- **Retention** until the run's channel is explicitly cleaned up. GC policy is open (§12).

### Surface

```python
send(body: dict, *, topic: str, name: str | None = None, request_id: str | None = None,
     expected_seq: int | None = None) -> int | None   # append; compare-and-append when expected_seq is given
read(after: int = 0, *, topics=None, name=None, request_ids=None, limit=None) -> list[Envelope]
    # topics: exact set or prefix/wildcard patterns (e.g. ["control.>"]) — opaque string matching
latest(topic: str, name: str | None = None) -> Envelope | None        # most recent on a (topic, name)
last_seq() -> int                                                     # the log's last seq (0 = empty) — the CAS's read half
close()
```

The pattern grammar, in full: the **only** wildcard is a trailing `.>` — `"control.>"` matches every topic starting with `control.` — and everything before it is literal (metacharacters inert); no bare `">"`, no mid-string wildcards. `latest` takes exact topics only, no patterns.

`latest` is a substrate primitive (not just a `read` helper) on backend-optimization grounds (SQLite indexed `ORDER BY seq DESC LIMIT 1`; NATS `last_per_subject`). Its well-definedness requires a **single writer per (topic, name)** (the common case) or a single sequencer.

`last_seq` (added 2026-07-10) is admitted on the stronger ground — the **op-admission principle**: *the surface must be readable in every coordinate it requires callers to assert.* The CAS makes every claimant assert the head (`expected_seq`; `0` = empty is its base case) while nothing could read it below O(N); `last_seq` is that coordinate's read, O(1) on every backend (`len(log)`; `MAX(seq)` on the key), and every real log store exposes it (Kafka end offsets, NATS last-seq, EventStore head). Its two sanctioned consumer classes: CAS claimants (the Worker's head-first attach — folds from topic-filtered reads capped at the asserted head, same-read fusion preserved by assertion) and the incremental reader's has-anything-new watermark (a viewer polls `last_seq`, re-folds on change). Nothing else passes the principle: `count(topic)`, `first()`, `read_range` — no caller is required to assert those.

### Read projections

Stateful-communication shapes are **queries over the one log**: register/latest = `latest`; flag/terminal-fact = existence; queue = a single consumer persisting its cursor; bounded window = last-*k* by `seq`; tail = a cursor read. Per-(topic,name) compaction is a *semantic* choice (makes the register the retained object), not free GC — deferred, chosen with eyes open.

## 5. Cooperative-control convention (foundational)

The thinnest, most fundamental convention — the one structural opinion that makes runstate a *control* protocol. **It is not a binary in the namespace.** The namespace is **content-typed**, and each category has a fixed producer-role and consumer-set *by convention*:

| Category (reserved `topic`s) | Produced by | Consumed by |
|---|---|---|
| `control.subscribe` / `control.unsubscribe` / `control.stop` | orchestrator (and see below) | worker |
| `lifecycle.*` (started/stopped/heartbeat/nak) | worker | observers |
| `launcher.*` (launched/terminated) | launcher | observers |
| `value` (user metrics; `name` distinguishes them) | worker | observers |

One documented completion case (specs/service-worker.md): the worker itself appends `control.unsubscribe` when a registration expires (`until` met / one-shot consumed) — the **expiry counter-record**, the worker completing the subscribe/unsubscribe pair exactly as `lifecycle.stopped` completes `control.stop`. It is bookkeeping (the affine eliminator is the same whoever applies it), not a command; nothing routes on its author (§12.8's lift-rule).

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
{ "from"?: Condition, "every"?: Condition, "until"?: Condition }   # each slot OMITTABLE, never null — absence is semantic (fire-now / one-shot / never-expire); see the §7 notation note

# control.unsubscribe — request_id = the subscription to cancel; body = {}
# control.stop        — body = { "from"?: Condition }  (one-shot: at most a `from`; default = stop now; the request half of a request/outcome pair — §7)

# value (worker → observers) — name = which metric; request_id = the sub it answers (or None = broadcast)
{ "value": Any, "step"?: int, "t"?: float }  # value=app data; step=worker step-clock; t=absolute wall-clock secs (real-time axis)
```

### The scheduling condition-algebra

A subscription **fires at `from`** (default: the next safe point), **repeats every `every`** (if present — *absent ⟹ one-shot*), and **expires per `until`** (if present; else open for a recurring sub). `from`/`every`/`until` are each a **Condition** over the worker's coordinates `(step, wall-time, fire-count)`:

```
Coord        := {step: N} | {time_seconds: S}                 # from / every
UntilTerm    := Coord | {count: C}                            # until also admits count
Condition[T] := T | {any: [Condition[T], …≥1]} | {all: [Condition[T], …≥1]}   # fully recursive
#   from, every : Condition[Coord]      until : Condition[UntilTerm]
#   (the schema cannot express Condition[T] generically; it monomorphizes the two
#    instances as $defs `Condition` and `UntilCondition`)
#   (count grammatical only in `until` — structurally, via the per-slot term type)
```
- **`any` = whichever crosses first** (min / OR); **`all` = whichever crosses last** (max / AND).
- Clocks per slot: `from`/`until` are *absolute* (step value, seconds-since-registration, total fires); `every` is *deltas since the last fire*.
- **`until` gates *before* the fire**, at the same safe point — the boundary coordinate itself produces no fire (the window is half-open) — whereas a **count**-`until` expires *on* the fire that spends the budget (the count only moves with the fire).
- The algebra is freely associative; **no normal form** — equivalent encodings (`any[a, any[b,c]] ≡ any[a,b,c]`, dominated thresholds) are *behaviorally inert* (the boolean eval is associativity-invariant), and runstate never compares/dedups/hashes conditions, so canonicalization would buy nothing. (Optional soft `maxDepth` as a pure resource guard.)

Examples: `{}` = fire once now · `{from: {step: N}}` = fire once at step N · `{every: {any: [{step:10}, {time_seconds:60}]}}` = every 10 steps or 60 s, whichever first, forever · `{every: {step:1}, until: {all: [{step:5000}, {count:100}]}}` = every step until *both* step 5000 and 100 fires.

Helper sugar (Tenacity-style operators): `now()`, `at(step=N)`, `every(steps=K, seconds=S)`; `Step(10) | Time(60)` → `any`, `Step(5000) & Count(100)` → `all`.

### Correlation, visibility, acks

- `request_id` (envelope) correlates a `value` to its `control.subscribe` and scopes visibility: an observer reads `request_id ∈ {None} ∪ {its ids}`. The worker reads all `control.*` regardless. Visibility is **read-side filtering, not enforcement**, until a backend can enforce it.
- **No registration ack, no per-request receipt.** "Did my request land?" = the worker's **consumption watermark**: the `consumed_seq` it publishes on its heartbeat (§7) — its read position **in the inbound `control` order** (not a global `seq`; see §12) — so a request is registered once `consumed_seq ≥ its seq` *and* no `nak` arrived. The worker advances `consumed_seq` only **after** durably registering/naking, so it's a true registration watermark (not merely "read past"). **Failure** is `lifecycle.nak` (envelope `request_id` = the offending request; body `{reason, message}`, `reason ∈ {malformed, unsatisfiable, unsupported}`). `unsatisfiable` is the clean *never-fire* refusal — a *statically* zero-fire request: `until` already true, a step-keyed condition on a **stepless** worker, or an **empty window** (`from ⟹ until` — the gate opens no earlier than it closes). The empty-window case is decided for a *conjunctive* `from` by a single-point check (does `until` already hold at `from`'s minimal corner?), reusing `satisfied` — O(input), no normal form. A `from` containing an `any` has many corners (a potential exponential blow-up); rather than canonicalize, we punt and it degrades to a dynamic never-fire. (Catching it in general is entailment over monotone formulas, whose DNF is exponential for `all`-of-`any`s — deliberately out of scope.) `malformed` (body didn't conform — a sender bug) and `unsupported` (unknown `control` verb) refuse a bad request *without crashing the worker*: one bad message is naked and dropped, never fatal. (The *dynamic* "will never fire" — the worker *stops* before reaching the trigger — is signalled by `lifecycle.stopped`, not `nak`; a merely *slow* worker is a patience-cap concern, §9. The fourth never-fire signal, previously silent: a registration that *expires* (`until` met / one-shot consumed / recurrence no longer possible) is answered by the worker's **expiry counter-record** — a `control.unsubscribe` bearing the `request_id`, delivered to exactly the right observer by visibility scoping. And the fifth, *recordless by design*: a **time-referencing** registration is episode-scoped (`specs/time-lease-boundary.md`) — discharged by the next episode boundary, whose `lifecycle.started` is already on the log and *is* the answer. Five never-fire causes, five handlers — with the honest nuance that for the fifth, the watermark still passes the request (it was processed), so acceptance ≠ will-serve; a lease's lifecycle is read via `live_demand`, and a lease client's detection mechanism is its own renewal cadence.) "Did my request land?" is therefore **answer-first**: a `nak` *following* the request by `seq` resolves it regardless of the watermark; `await_consumed`'s codomain is the full answer space — `Nak` (refused) | terminal `RunResult` (the run died under the request, no later episode) | `None` (accepted at the watermark). A terminal that *precedes* the request leaves it waiting for the next episode; refused-by-death observer-side is also why the autonomous worker's plain `stopped()` needs no death-CAS of its own.

**Reference worker loop** (each `tick(step=…)`):
1. `read` new `control.*` after the persisted cursor; register subscriptions / drop unsubscribed / `nak` the unsatisfiable (and the malformed — including any count atom outside `until`, the accidental-pure-pin gate); **skip already-answered subscribes** (an unsubscribe or nak *following* the subscribe by `seq` — the positional answer fold, §7 — so a resumed episode neither resurrects an expired lease nor re-naks a refused request; the worker's own expiry records re-drain as silent no-ops) and **pop-then-skip boundary-voided time-leases** (a time-referencing subscribe with a prior episode's `started` after it — `specs/time-lease-boundary.md`; the skip still rescinds its same-id predecessor, because registrations are slots, not a set). Per subscribe these folds apply in a **fixed order** — answered-skip, then boundary-void, then the structural `malformed` gate, then `unsatisfiable`, then register — with observable consequences an implementation must reproduce: an answered-and-malformed subscribe is never re-naked on resume, and a boundary-voided malformed time-lease gets no nak. **Then** advance the cursor — published as `consumed_seq` on step 3's beacon (registration before watermark; cursor persistence is deferred, §12.5); add any `control.stop` to the **pending set** — skipping stops already discharged by a `lifecycle.stopped` later on the log (§7).
2. Service due subscriptions (emit `value`s); a registration **expires** the moment no future fire is possible (`until` met, one-shot consumed, recurrence impossible — registered ⟺ fire-possible, enforced), and expiry is **emit-then-delete**: the expiry counter-record lands on the log before memory changes. **Register before reap** (step 1 before step 2) so a keepalive *refresh* never transiently zeroes the lifeline count.
3. Beacon the heartbeat — **exactly one per tick, unconditional** (the dense per-tick axis `progress` relies on it), after steps 1–2: every answer (nak, registration effect) and every serviced record (values, expiry counter-records) lands on the log *before* the tick's heartbeat, which is what makes its `consumed_seq` a truthful watermark for same-tick records.
4. Evaluate the stop decision — the pending set's `any`-join, a monotone *level* (§7); if stopping, emit `lifecycle.stopped` and exit. (A *service* worker additionally exits at zero leased demand via the careful death — `retire()`, specs/service-worker.md — an explicit opt-in by verb, never a default.)

## 7. Lifecycle convention

Well-known **outbound** events (`worker → observers`), reserved `lifecycle.*`.

(Notation: in a **worker-authored body** (`lifecycle.*`, `value`), `field?` means *present but nullable* — the worker always sends the key, possibly `null` (e.g. heartbeat `step?` for a stepless worker) — whereas at the **envelope** level `name?`/`request_id?` are genuinely *omittable-or-null*. The schemas pin this: nullable-present body fields are `required` with a `["…","null"]` type. The one deliberate carve-out from present-nullable: the orchestrator-authored **schedule slots** (§6 `from`/`every`/`until`) are *omittable and non-nullable* — absence is itself the semantics (fire-now / one-shot / never-expire), not a missing measurement, and `{"from": null}` is schema-invalid.)

| Topic | Semantics |
|---|---|
| `lifecycle.started` | Pushed on attach. Body `{handle, attached_at?}` — the worker self-reports its **liveness handle** (§8) when no launcher recorded one. (A `hostname` field was removed in `lifecycle`-`v0.3`, 2026-07-10 — dead: never emitted non-null, and the handle owns location; existing logs migrated.) |
| `lifecycle.stopped` | The cooperative dying breath; body `{completed, error, final_step}`. **Existence = a clean, *resumable* halt** (a retained log fact); `completed=True` is the worker's opt-in claim of intrinsic, permanent completion — else `preempted`. A crashed worker emits nothing — absence ≠ alive (§8). |
| `lifecycle.heartbeat` | **Pushed beacon** (`request_id=None`), **tick-driven** (a hung loop stops it), periodic. Body `{step?, consumed_seq}` — serves **liveness** (staleness), **progress** (step advancing), and the **registration watermark** (§6; `consumed_seq` = the worker's read position in its inbound `control` order). `step` is null for a service worker with no step. No embedded timestamp (staleness uses the reader's arrival clock). |
| `lifecycle.nak` | Negative ack (§6); body `{reason, message}`, `reason ∈ {malformed, unsatisfiable, unsupported}` (syntactic / semantic / unknown-verb), envelope `request_id` = the offending request. |

**Worker-owned termination.** Stopping is always the worker's decision — intrinsic completion, data-dependent stops, and commanded stops (`control.stop`) all feed one stop check. The orchestrator never *removes* a worker. (`control.stop` "landed" = the watermark; its *effect* = `lifecycle.stopped`; there is no separate stop receipt.)

**The stop request/outcome pair & the unifying drain rule** (`specs/stop-discharge.md`). A `control.stop` is the *request half* of a request/outcome pair: **pending from its append until the next `lifecycle.stopped` that follows it by `seq`** — the *effect* record designated above — and any `stopped` **discharges every pending stop at once** (a broadcast answer, matching the `stopped` record's own broadcast nature). A discharged stop is history (provenance for "was this halt commanded?" — the commanding stops for a given `stopped` are exactly the pending set at its `seq`), never again input. Within an episode the pending stops are a **set of monotone predicates**; the stop decision is their `any`-join — a *level* that latches by inheritance, never a consumed-once pulse (a host that misses one `True` recovers it at the next safe point; `Worker.stop_pending` exposes the same predicate as a side-effect-free poll). This instantiates the **unifying drain rule**: *every control fact is live until its counter-record, and the worker folds the whole log applying counter-records.* Subscribe's counter-record is `unsubscribe` (an explicit rescind); stop's is the next `stopped` (a discharge — no `un-stop` verb: a stop is self-clearing, its receipt being the very thing it requests). Both verbs re-derive from `seq 0` across episodes: subscriptions persist because their rescissions are on the log; stops expire because their discharges are. Corollary: a stop sent while the run is *down* is answered by the next episode — immediately, and **exactly once** (the answering episode's own `stopped` discharges it). This is one instance of the design's **pairing-by-`seq` rule**, stated here once: *a standing fact is paired with its counter-record by log position — the counter must FOLLOW it by `seq`.* The instances, four: a `control.stop` ↔ the next `lifecycle.stopped` (the discharge; its public observer home is `observables.undischarged_stops`); a `control.subscribe` ↔ the next `control.unsubscribe`-or-`nak` bearing its `request_id` (the **answer fold** — specs/service-worker.md; its public home is `observables.live_demand`); a **time-referencing** subscribe ↔ additionally the next episode boundary (`specs/time-lease-boundary.md` — a time-lease is a contract with one living episode, voided recordlessly by a foreign `started`, re-anchoring at most once); episode terminality ↔ no `started`/`launched` following the terminal record (`specs/run-episodes.md`). `value` fires are deliberately *not* answers — the answer set stays schedule-independent and the fold body-light. (The formal version belongs to `docs/backlog/protocol-algebra.md`.)

**Lifelines** (the *service worker* — specced: `docs/specs/service-worker.md`). The kernel: the service worker **counts its live subscriptions exactly and stops at zero** — ref-count-exact, **no grace windows** (demand is enumerable log records, so the timeouts other systems are forced into don't apply; the log buffers demand across relaunch). The observer-vs-lifeline distinction *is* read-vs-subscribe — a passive observer *reads* (invisible, never pins); a client *subscribes* (counted, **pins**, because it's asking the service to produce) — now a theorem of the enforced invariant *registered ⟺ a future fire is possible*. Keepalive/orphan: clients subscribe with a time-bounded `until={time_seconds: N}` and refresh; a crash ⟹ expiry ⟹ the worker writes the expiry counter-record and the count drops (reuses the scheduling algebra; the lease-shaped hysteresis is *client-chosen*, never a server knob) — and when the *worker* dies before noticing the lapse, the lease does not resurrect: time-referencing registrations are episode-scoped, voided by the next boundary (`specs/time-lease-boundary.md`), so a dead client's lease costs at most one re-anchored episode, ever. The death is **careful**: the dying breath is compare-and-appended against the drained log (`retire()` — episodes are CAS-claimed at both ends), so a subscribe racing the death is never orphaned. Bootstrap is loop-order safety, not a grace window: the first tick drains the subscribe that justified the launch before any zero-demand check. The policy is **opt-in by verb** (`serve()` / `retire()`; one worker primitive, two demand durabilities — the launch contract is durable demand, subscriptions are leased): an autonomous worker ignores all of this *by construction* — no term couples its life to observation.

## 8. Launcher convention + the liveness detector

Reserved `launcher.*`, written by the spawner/reaper — a Layer-3 / process-level concern, not substrate or lifecycle.

| Topic | Body |
|---|---|
| `launcher.launched` | `{handle, status}` — spawn-intent + the worker's liveness **handle**. (It is the launcher's *observation*, never the claim — the lazy-launch race is arbitrated by the worker's birth-CAS; `specs/lazy-launch.md`.) |
| `launcher.terminated` | `{exit_code?, signal?, reason: "exited" | "killed"}` — the *manner* of death; only a `wait()`ing parent can produce it. |

**Launch identity (launcher-v0.3).** Both launcher records **must** carry the envelope's `request_id`: one id per launch, minted by the launcher, and **re-emitted by the worker on its `lifecycle.started`** (ambiently — `RUNSTATE_LAUNCH_ID`, beside the other `attach` variables; null iff nobody launched the worker). So a launch, the claim answering it, and the death ending it name the same thing. Without it, `terminated` asserts the unknowable *"the run is dead"* rather than *"my launch ended"*, and a late reap or a claim-race loser's death forges a live episode's verdict — reproduced, then fixed (`specs/launcher-record-identity.md`). Here `request_id` is **correlation only**: it never scopes visibility (that stays a value-plane concern), and the verdict fold pairs a death to the launch the *claimed* episode answered, never by log position.

**The handle** is a portable, scheme-tagged token: `local://host/pid`, `slurm://jobid`, `k8s://ns/pod`, `ray://actor`. (A pid-reuse disambiguator — `local://host/pid?start=T` — is deferred, and the reference parser does not yet accept it: `docs/backlog/conventions-hygiene.md` F9.) Resolving it (`kill -0`, `squeue -j`) answers liveness **actor-independently** — robust even if the launcher is gone, cross-host where the scheme resolves. It obsoletes a `.worker.pid` file (the handle lives in the log). **Single source of truth:** the worker self-reports via `lifecycle.started`; `launcher.launched` carries the spawn-intent + the launcher's known handle.

**Liveness is a layered, opt-in failure detector** — none of it substrate-owned (presence is *emitted messages*, never substrate state; a mutable TTL'd *lease* is deliberately avoided). Best-to-worst:
1. **Clean completion** — `lifecycle.stopped` exists.
2. **Reaped death** — `launcher.terminated` (the manner; needs a reaper).
3. **Probe the handle** — resolve it for the *fact* of death (actor-independent).
4. **Heartbeat staleness** — `lifecycle.heartbeat` older than a threshold ⟹ crashed/hung. The universal floor. Because the beacon is tick-driven, staleness catches *hangs* (not just crashes) — but for the same reason a worker in a legitimately **long single step** (a 20-min epoch, a giant batch) stops beaconing and looks dead. So the threshold must exceed the worker's *max* inter-beacon gap, which the reader often can't know a priori, and progress-staleness doesn't help (the `step` is frozen *because it's in progress*). This is the irreducible **dead-vs-busy** ambiguity: the threshold is a per-workload tuning, and a worker that *can* sub-divide a long step should beacon within it.

Three reference configurations: **(a)** floor only · **(b)** + handle (observers self-probe) · **(c)** + handle + reaper (a daemon/stay-attached launcher probes/reaps once and writes the result).

**Spawn vs watch/reap split.** `launch()` does the irreducible job — spawn + emit handle — and returns (all a cluster scheduler permits; fire-and-forget). Watching/reaping is a *separable* role. `terminate()` resolves the handle and kills (`kill`/`scancel`/`kubectl`), not via a parent relationship.

**Defaults (opinion-free ≠ batteries-not-included):** the reference worker loop heartbeats; the reference `LocalLauncher` writes a `local://` handle and reaps (`reap()`/`wait`/`poll`/`__exit__`), emitting `terminated` for whatever its own child did — *unconditionally*, since the launch id says whose death it is and the reader attributes it (`specs/launcher-record-identity.md`; this replaced a conditional "reap discipline" that suppressed a race loser's record). `resolve` is hostname-scoped: another host's `local://` handle is *not locally resolvable* (None → the staleness floor), never a false verdict from the wrong pid table. Every tier is removable.

## 9. Orchestration helpers (Layer 3)

Reference tooling; assumes the conventions (a worker that opts out composes its own loop from `send`/`read`/`latest` + the liveness tiers).

```python
class Launcher(Protocol):                                    # target is launcher-specific (callable vs argv)
    def launch(self, run_id, target, **kwargs) -> LaunchHandle: ...
    def open_channel(self, run_id) -> Channel: ...           # locate/open the run (lazy-launch = caller-invoked ensure_served, specs/lazy-launch.md)

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
    outcome: str   # CLOSED: "completed" | "preempted" | "errored" | "killed" | "presumed_dead"
    reason: str    # verbatim per-tier label (the raw "why", finer than the bucket)
    error: str | None; final_step: int | None
    # No `success`: a pure projection of `outcome` that would bake one contested
    # policy into the producer. Consumers apply their own (sweep fails on the
    # bottom three). `outcome` (normalized, cross-tier) and `reason` (raw, per-tier)
    # are orthogonal; for the lifecycle tier `reason == outcome` (the worker's verbatim
    # label is gone — commandedness is recoverable from the `control.stop` on the log).
    # The launcher tier still carries finer labels ("exited" | "killed"), and the
    # inference tiers (presumed_dead) a fixed vocabulary consumers may branch on:
    # "probed_dead" (the handle resolved dead, recordless), "heartbeat_stale",
    # "episode_lock_released" (the episode lock dropped past the birth grace).

# peek_terminal is the RECORD-based verdict (a terminal envelope exists); the
# Watcher adds the INFERENCE-based tiers (probe + heartbeat staleness → presumed_dead).
def peek_terminal(channel) -> RunResult | None:            # clean stop OR reaped launcher.terminated; else None
def sweep(variants, launcher, *, on_event=None, resume=True, stop_on_failure=False, watcher=None) -> list[RunResult]:
    # sequential; watches each until terminal (clean stop OR detected-dead → presumed_dead)
```

**The stateless observables** (`observables.py`; spec: `docs/specs/observables.md`). Pure, body-aware folds `log → derived view` — the questions an observer can ask without disturbing the run (the read side of §7's read-vs-subscribe line): `peek_terminal` (above), `live_episode`, `latest_episode` (the episode-boundary rule — the latest `lifecycle.started`, live or ended; its `seq` is the episode-window watermark for `read(after=…)`), `progress` (the step frontier: max of the latest heartbeat/stopped axes; `None` before any stepped record), and `value_series` (§4's register projection lifted pointwise to the (name, step) plane — last-write-wins by `seq`, so duplicate samples dedup and an episode rewind resolves to the as-resumed trajectory). Observe *statelessly* there; watch *statefully* here — the `Watcher` adds the one non-log-derivable input, arrival time. (The substrate *projects*, §4, body-opaque; the conventions *observe*, body-aware.)

**Cross-run synchronization.** `Watcher.broadcast("loss", {"from": {"step": 100}})` fans one subscription across all tracked runs (one shared `request_id`; the `run_id` disambiguates responses). It's the primary cross-run mechanism — *no Experiment class*. It is a **pure synchronization**: it blocks until every *live* run reaches the point, so a slow-but-healthy run **legitimately delays it, unbounded by design** (that is what "synchronize" means). Each run resolves to exactly one of: **fires** (the value); **`nak`** (statically unsatisfiable — e.g. an `until` already met at registration; an already-crossed `from` is NOT this case — by the `>=` semantics it *fires* at the next safe point); **`lifecycle.stopped`** (stopped before reaching it — excluded); **heartbeat-stale** (crashed/hung — excluded). A **bounded-latency** caller MUST additionally supply a **patience cap** — a wall-clock deadline after which the barrier returns *partial* results (still-running slow runs reported as pending). So the cap is *optional for a pure sync* but *required for a bounded wait*; "no separate timeout" holds only for the unbounded-sync reading. (The never-fire causes → handlers: static-unsatisfiable → `nak`; stops-early → `stopped`; too-slow-for-my-patience → the cap; and — new with `specs/time-lease-boundary.md` — a **time-keyed barrier subscription on a run that resumes is boundary-voided with no record**, so **broadcast schedules should be step-keyed**; a boundary-aware re-broadcasting Watcher is a backlog note.)

## 10. The schema stack (freeze story)

No single schema:
- **Envelope schema** — `{seq, topic, name?, request_id?, body}` with `body: object` (opaque). Close to freezable; constrains neither `topic` semantics nor body shapes.
- **Convention schemas** — each strictly pins its own well-known bodies (`additionalProperties: false`), independently versioned. Adding a field to a well-known body is a deliberate convention-version bump. **User `value` bodies** pin only the *wrapper* (`{value, step?, t?}`); the `value` payload is `Any` JSON-serializable value — a sender-side `json_default` hook (`attach`/`open_channel`) coerces exotic types (numpy scalars, tensors), and an unserializable value with no hook fails fast at emission (naming the metric), rather than silently dropping it. Validation is opt-in/layered: the substrate never validates; "opt-in convention" means "you needn't emit `lifecycle.*`, but if you do, conform."

This is the cut that dissolves "blocking for the schema": the convention decisions block specific *convention* schemas, downstream — not the envelope. **Freeze status (rev 4):** the stack is written and frozen in `protocol/` — `envelope-v0.2` plus `subscription` / `launcher` / `value`-`v0.2` and `lifecycle`-`v0.3` (the first exercised convention bump: `Started.hostname` removed 2026-07-10, logs migrated — per-convention versioning working as designed), each `additionalProperties: false`. The two previously-held bodies are pinned: `lifecycle.heartbeat` = `{step, consumed_seq}` (`consumed_seq` = the inbound-`control` read position, §11) and `launcher.launched.status` = `enum ["running"]` (room to widen via a convention-version bump). `tests/test_schema.py` validates that the messages the implementation emits conform.

## 11. Three clocks

`seq` = the substrate's transport order (the per-log total order, §4). `step` = the worker's logical clock (a body field). wall-clock = real time. **All scheduling predicates evaluate in the worker's tick, against `step`/wall-clock, never `seq`**, and only at safe points. (`consumed_seq` in the heartbeat is the worker's read position in its **inbound `control` order** — not a global-`seq` position, not a fourth clock; §12 explains why it's scoped to inbound.) `latest` orders by `seq`, = "most recent emitted" only when a (topic,name)'s emissions are seq-monotonic in `step` (true for a single writer).

## 12. Open questions — implementation-plan items

The six convention decisions are settled (see revision history). Status tags below are as of the rev-4 implementation pass. **None change the wire *envelope***; the items still open touch operational mechanics, not the frozen schemas. The *deferred* items below are mirrored as discoverable work in `docs/backlog/index.md`.

1. **Lazy-launch double-spawn race** — *[CLOSED 2026-06-11 by `specs/lazy-launch.md`]* `ensure_served` (the leased-demand decider beside `relaunch_if_needed`), the foreign-claim-scoped reap discipline (*deleted 2026-07-14 — superseded by launch identity, `specs/launcher-record-identity.md`*), hostname-scoped `resolve`, the `_lost` guard on explicit `stopped()`, and the mandatory per-cycle `reap()` in the activator recipe. Historical text follows. — *was: [deferred; its inputs now exist]* `launcher.launched.status` is pinned to `running`, but lazy-launch-on-first-`control` itself is **not** built: `launch()` is explicit (you spawn, then send control). The relaunch decider's demand fold is now log-derivable (`observables.live_demand`, specs/service-worker.md), the ghost-lease flap is bounded by construction (`specs/time-lease-boundary.md` — no waker flap policy needed; the one surviving constraint is no `ensure` over stepless services), and the worker-side claim (birth-CAS) and the careful death (death-CAS) bracket the race. The follow-on spec owns it.
2. **`send_request` ↔ `Channel` seam** — *[dissolved with #1]* the decider is caller-invoked (`ensure_served`), never channel-wrapping; `open_channel` stays a plain channel.
3. **`Watcher.observe`** — *[done]* `observe(run_id, channel)` is the handle-free / late-attach / observe-only path; `add(handle)` is the handle path that also enables the probe tier.
4. **`broadcast` returns the assigned `request_id`** — *[done]* `Watcher.broadcast(name, schedule)` returns the shared id; pass `request_id=` to reuse one (cancel-the-lot reachable).
5. **Cursor-persistence mechanics** — *[consumer-side decided out of scope (2026-06-01); worker-side deferred]*. **Consumer side:** the substrate owns durability + `seq`, not offset-tracking — caller-owned cursors (§3). The consumers we ship are *state-deriving* (`Watcher`, `peek_terminal`, `live_episode` fold the durable log to current state), so they re-derive on restart and need *no* cursor; an exactly-once *event-processing* consumer persists its own offset and resumes via `read(after=last_seq)` (~5 lines). We ship the primitive (durable log + `seq` + `read(after=)`), not the policy — same shape as the `run_id` recipe. (Holds because runstate is *fan-out*; competing-consumer / work-queue offset coordination is a different substrate, also out of scope.) **Worker side (still deferred, now bounded):** the worker's in-memory control cursor has no crash-replay persistence; the at-least-once / at-most-once boundary is now *bounded* by the expiry counter-records (specs/service-worker.md) plus the episode-boundary discharge (`specs/time-lease-boundary.md`) — a consumed one-shot or lapsed lease is never replayed once its record landed, and a time-lease that escaped its record is voided by the next boundary (re-anchor ≤ 1, globally — not per-episode); a `count`-`until`'s partial fire-history still resets on replay. Within a process, registration precedes the heartbeat, so `consumed_seq` advances only after registration. The cross-episode shape is **settled (2026-06-09)**: the unifying drain rule (§7; `specs/stop-discharge.md`) re-derives both verbs from `seq 0` and lets counter-records expire them — superseding the earlier state-vs-event framing. What this item retains is pure *efficiency*: resuming the drain from a persisted cursor instead of refolding, orthogonal to correctness. *(Scale evidence, 2026-07-10 — measured: the framing survives for the refold itself — the topic-filtered control re-drain is index-served, ~2 ms on a 10⁶-envelope log — but the dominant resume term was elsewhere: `Worker.__init__` computed its claim-read folds from an **unfiltered** `read()`, ~3.4 s + ~0.8 GB transient at 10⁶. Closed same day: `last_seq()` (§4) + the head-first capped attach — measured 3,400 ms → 1.5 ms on the same log.)*
6. **▸ `consumed_seq` scoping** — *[done for the shipped backends]* heartbeat = `{step, consumed_seq}` is implemented and frozen; `consumed_seq` is the worker's read position over `control.>`. On SQLite/Memory `seq` is globally ordered, so the scalar is well-defined. The per-subject-backend subtlety (a single watermark needs inbound-`control` single-ordering) re-surfaces only when a multi-subject backend (NATS) lands.
7. **▸ Multi-orchestrator support** — *[handled by the drain model]* the worker **drains** `control.>` (processes every command after its cursor), so it never relies on `latest(control.*)` for "was a stop requested?" — multiple orchestrators' commands all take effect. The remaining piece is *attribution* under multiple writers, which is #8. (`peek_terminal`'s `latest` calls are on worker/launcher-written topics, each single-writer.)
8. **Author / provenance** — *[deferred; stopgap available]* nothing routes on author, so by the lift-rule it's not an envelope field. `request_id` is an opaque string in the implementation, so the `"webui:<unique>"` prefix stopgap works today. A real field waits on provenance/authz becoming load-bearing.
9. **Writer-serialization + GC/retention** — *[partial; home-level GC recipe'd 2026-06-11]* writer-serialization is handled (`MemoryChannel` takes a shared lock for concurrent in-process writers; `SqliteChannel` relies on autoincrement + SQLite's own locking). Retention is **full, no GC** *within a log* — which is exactly the precondition `peek_terminal` / resume rely on. **Home-level** collection (delete/prune a whole run's directory) is now a consumer recipe — `specs/store.md` Recipe 3: pointer-rooted mark-and-sweep, gated on `live_episode`, selective-prune default — and never truncates a live log; in-log retention/compaction remains future work.

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
- **An engine-owned loop (inversion of control — the RunEngine/durable-execution shape).** At scale it converges on a log internally anyway (durable execution = persisted event history + deterministic replay), so the log is the foundation and IoC a programming-model veneer; the layering is one-directional — an opt-in driver can own a user's loop *on* the substrate, but the opinion-free log can never be recovered *from* an engine — and it is totalizing at adoption (workloads rewritten into the engine's vocabulary, against an ecosystem of user-owned training loops). → the cooperative worker: the user keeps the loop; `steps()`'s generator boundary is the deliberate midpoint (the library gets control exactly at safe points); rich interruption (pause/rewind/suspenders) stays an opt-in Layer-3 driver if its use cases ever land (§14, the Pause/Resume backlog item carries the prior art).

## 14. Scope: v0.2 vs later

**v0.2 ships (and now does):** the substrate (Memory + SQLite topic log) + the four conventions + the orchestration helpers (launchers, Watcher, sweep) + the frozen schema stack. (Shipped since: the cross-host **Postgres** backend, `specs/channel-postgres.md`.) The §12 items left open (cursor-persistence/crash-replay efficiency, multi-orchestrator attribution, in-log retention/compaction) are non-blocking operational refinements, not protocol gaps (lazy-launch closed 2026-06-11, `specs/lazy-launch.md`; home-level GC recipe'd same day, §12.9).
**Layer 4 — DISSOLVED (2026-06-11, `specs/store.md`):** the once-planned **Store** (relational run/experiment metadata; many-to-many membership; the cross-run reuse-by-hash query) ships as recipes over the existing basis, not a component: the rid is the run's *address* (content-addressed placement — reuse-by-hash dissolves into `ensure` against the one home), membership is the cell pointer + the consumer's tracked tabulated overview, provenance is a backward record on the derived run's own log, and any index is a derived, rebuildable, never-authoritative cache. (The once-planned "Hasher" had already re-scoped to a `run_id()` recipe: content-addressable identity is a substrate affordance via caller-chosen `run_id`.)
**Long-term:** richer data-plane Progress + a viewer-discovery protocol — its *own* protocol in a **separate project** on runstate (never this repo's `protocol/`), distinct from this control protocol. Compose, don't conflate.

## Revision history

- 2026-06-11 (rev 11): **Layer 4 dissolved (§12.9, §14; specs/store.md).**
  The Store ships as recipes over the existing basis — rid-as-address
  (content-addressed placement; reuse-by-hash = `ensure` against the one
  home, arbitrated by the shipped birth-CAS), membership = cell pointers +
  the consumer's tracked overview, provenance = the child's birth record,
  index = derived/rebuildable/never-authoritative — plus one library
  helper by the F7 doctrine (`foreign_episode`, the producer gate's
  foreign half; the `extend` seam contract revised to
  liveness-handle-always, and `ensure`'s no-progress guard own-spawn-
  scoped). Home-level GC = pointer-rooted mark-and-sweep (recipe).
- 2026-06-11 (rev 10): **Lazy-launch (§8/§9/§12.1–2; specs/lazy-launch.md).**
  `ensure_served` — the leased-demand decider beside `relaunch_if_needed`
  (two demand durabilities, two deciders): live demand ∧ no live episode ⟹
  launch; caller-invoked (the demander's presence is the keepalive AND the
  waker), with the standing activator as a composition whose per-cycle
  `reap()` is load-bearing (zombies read alive to `kill -0`). Wasted spawns
  stay accepted, their funerals disciplined: the foreign-claim-scoped reap
  rule (skip `terminated` iff exit 0 ∧ never-claimed-after-own-`launched` ∧
  a foreign claim follows) keeps loser corpses off the verdict plane while
  null workers and startup crashes keep theirs; explicit `stopped()` gains
  the loser guard; `resolve` becomes hostname-scoped (a pre-existing
  false-dead off-host that could double-claim). ThreadLauncher named
  degenerate for multi-waker use. Closes §12.1/§12.2.
- 2026-06-11 (rev 9): **Episode-scoped time-leases (§6/§7/§9/§12;
  specs/time-lease-boundary.md).** The one piece of standing state that did
  not re-derive from the log — a lease's elapsed countdown — is dissolved
  rather than persisted: a time-referencing subscribe is discharged by the
  next episode boundary (pairing-by-`seq`'s fourth instance; the boundary
  `started` is the counter-record, recordless by design), with pop-then-skip
  preserving slot semantics. Re-anchor ≤ 1 globally; the dead-client ghost
  costs ≤ 2 relaunches by construction, deleting the waker's entire flap
  policy (backoff/give-up/cadence knobs all rejected as wrong-layer). Five
  never-fire causes, five handlers; acceptance ≠ will-serve documented;
  broadcast barriers steered step-keyed. One shared predicate
  (`schedule.references_time` + the voided-check in `observables`) serves the
  worker and `live_demand`, whose body-untouched purity claim is amended
  (shape-peeking, value-blind). Stops deliberately keep their re-anchor
  (at-least-once is their spec'd posture; no flap is reachable).
- 2026-06-10 (rev 8): **The service worker (§5/§6/§7/§12; specs/service-worker.md).**
  One worker primitive, two demand durabilities (durable = the launch
  contract; leased = subscriptions); the lifeline kernel is ref-count-exact
  with no grace windows, opt-in **by verb** (`serve()`/`retire()`), never a
  default or a flag. New protocol behaviors: **expiry counter-records** (the
  worker completes the subscribe/unsubscribe pair — §5 gains the documented
  completion case; the fourth never-fire handler) under the **positional
  answer fold** (a subscribe is live until an unsubscribe-or-nak *follows* it
  by `seq`; public home `observables.live_demand`); the **careful death**
  (`retire()` — the dying breath is CAS'd against the drained log, episodes
  claimed at both ends); *registered ⟺ fire-possible* enforced (never-recur
  `every` expires; count atoms outside `until` nak `malformed`);
  `await_consumed` is answer-first with the full answer-space codomain
  (`Nak` | terminal `RunResult` | `None`). §7 names the **pairing-by-`seq`
  rule** once (stop/stopped; subscribe/answer; episode terminality). The
  refuted alternatives (lazy-as-primitive, reap-as-condition-data,
  property+recipe-only, a constructor flag) are recorded in the spec.
- 2026-06-10 (rev 7): **The stateless observables (§9)** — `liveness.py` grew
  into `observables.py` (`docs/specs/observables.md`): the stateless observer
  plane (pure body-aware folds; observe-statelessly vs watch-statefully as the
  Layer-3 boundary), adding `latest_episode` (the episode-boundary rule, named
  once), public `progress` (the memoizer's private fold, `None` for absence),
  `value_series` (the §4 register projection lifted to the (name, step) plane),
  and `vocabulary.handle_pid` (one parse site for the handle grammar, with
  `resolve` routed through it). Completes the mycooc audit's F5–F8
  read-projection batch; the consumer's hand-rolled helpers delete in one sweep.
- 2026-06-09 (rev 6): **Folded the stop request/outcome pair + the unifying drain rule into §6/§7** (from `specs/stop-discharge.md`, implemented this date). A `control.stop` is pending until the next `lifecycle.stopped` that follows it by `seq` — its designated *effect*, now also its *discharge* (broadcast: one `stopped` answers every pending stop); the in-episode decision is the pending set's monotone `any`-join (a level, not a consumed-once pulse; `Worker.stop_pending` is the side-effect-free poll). One drain rule covers both control verbs — *every control fact is live until its counter-record* (`unsubscribe` rescinds; `stopped` discharges) — closing the mycooc audit's F2 (lost edge) and F3 (clobber) and the cross-episode stop replay in one re-typing; §12.5's cross-episode question is settled (cursor persistence stays an efficiency item). Executable companions: the S1–S4, crash-edge, and pre-staged stop tests in `tests/test_worker.py`.
- 2026-06-09 (rev 5): **Folded the substrate CAS into §4.** `send(expected_seq=)` (shipped 2026-06-01 with `specs/run-episodes.md` but never folded back here) added to the Surface and given its normative Contract bullet — the concurrency trichotomy settled by the working-tree review: check-and-append is one critical section across handles *and* processes; `None` = provably lost; raise = indeterminate backend fault, never a loss. Executable companions: the conformance race / shared-handle / wedged-writer / moved-log tests in `tests/test_channel.py`.
- 2026-05-30 (rev 4): **Implementation pass.** Built the substrate (Memory + SQLite, conformance-tested over both), the four conventions, the reference `Worker`, and the orchestration helpers (`ThreadLauncher` / `LocalLauncher` + Protocols, `Watcher`, `sweep`), and **wrote + froze the JSON Schema stack**. Reconciliations folded back here: the two held bodies are pinned (heartbeat `{step, consumed_seq}`, `launcher.launched.status` = `running`); §12 annotated with status — #3 (`observe`), #4 (`broadcast` returns id), #6 (`consumed_seq` for the shipped backends) **done**, #7 **handled by the drain model**, #9 **partial** (writer-serialization yes, GC no); #1/#2 (lazy-launch + its channel seam), #5 (cursor-persistence / crash-replay), #8 (author field) **deferred**, non-blocking. Two design refinements surfaced during build and were adopted: `RunResult` dropped `success` for a closed `outcome` + verbatim `reason`; `Watcher.poll`/`wait_all` return `RunStatus = Running | RunResult` (the `Running` arm carries the watcher-unique `beacon_age`), making the fold lossless instead of `Optional[RunResult]`.
- 2026-05-29 (rev 3): **Folded a three-agent review** (calibrated red-team ×2 + an unprimed independent re-derivation that *converged* on the substrate, scheduling, liveness, and cross-run shape — validating the bones). Fixes: the cross-run barrier is a **pure synchronization** (a slow-but-healthy run legitimately delays it, unbounded; bounded-latency callers supply a **patience cap** returning partial results — corrected the "no timeout needed for correctness" overclaim); `nak`-on-unsatisfiable is **static-only** (dynamic never-fire → `stopped`, slow → patience cap; stepless workers `nak` step-conditions); `consumed_seq` scoped to the **inbound `control` order** (well-defined on per-topic-seq backends; gates freezing the heartbeat body) and published **after** durable registration; **register-before-reap** made a normative loop invariant; documented the tick-beacon vs legitimate-long-step tension; per-slot threshold types (`count` only in `until`, structurally); §13 ack relabelled **relocated, not eliminated**; new open items — multi-orchestrator vs `latest(control.*)`, and author/provenance (deferred, with a `request_id`-prefix stopgap). **Envelope freezable; heartbeat body held pending `consumed_seq` scoping.**
- 2026-05-29 (rev 2): **Folded the six resolved convention decisions.** #6 split-field envelope `{seq, topic, name?, request_id?, body}` with a closed content-typed `topic` vocabulary and an open app `name` (dissolves the reserved-vs-user collision); #3 dissolved (`topic` discriminates — no `kind`); #5 the recursive `from`/`every`/`until` condition-algebra over (step, time, count) with `any`/`all`, no normal form (one-shot = omit `every`); #1 no registration ack / stop receipt (heartbeat `consumed_seq` watermark + `nak`-on-unsatisfiable; barrier backstop = heartbeat staleness, no separate timeout); #2 heartbeat = tick-driven push beacon `{step?, consumed_seq}`, lifelines = service-worker subscription ref-count (no reserved name); #4 opaque envelope body / strict-pinned convention bodies / `Any` user payload. Replaced the in/out binary with content-typed categories + the per-role subscription protocol. Remaining open items are implementation-plan only (§12).
- 2026-05-29 (rev 1): Clean rewrite from the converged architecture (topic-log substrate + conventions). Supersedes `design-v0.2-exploration.md` (the full decision trail).
