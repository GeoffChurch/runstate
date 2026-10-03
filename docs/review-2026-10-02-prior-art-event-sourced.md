# Prior-art survey: event-sourced entities and virtual actors

Family surveyed for the `GeoffChurch/runstate` prior-art question (rubric: `prior-art-rubric.md`).
Sources fetched 2026-10-02. In depth: Akka / Apache Pekko Persistence, Microsoft Orleans, Restate.
Briefly: Dapr Actors and Dapr Workflow, KurrentDB (formerly EventStoreDB), and Kafka where it adds
to what `docs/positioning.md` already says.

Ratings: **covered**, **partial**, **absent**, **conflicts** (the system's model works against the
concern). `[unverified]` marks anything not confirmed on a primary page.

---

## 0. Where the family and runstate split

Three structural facts explain most of the per-concern ratings. Each one holds across the family.

**F1. The family keeps commands out of the journal. runstate puts them in.** An Akka or Orleans
entity's journal holds only events that the entity decided to write. Commands, including "stop",
arrive through an ephemeral mailbox or call. runstate's per-run log is shared by several writers on
purpose. `control.*` from operators, `launcher.*` from launchers and the worker's own `lifecycle.*`
and `value` records all have one total order. The stop-discharge fold pairs a stop with the next
`lifecycle.stopped` **by seq** (`specs/stop-discharge.md`), so it depends on that shared order.

Restate is the exception that proves the point. It does log commands durably (inbox, signals,
cancellation). The handler never appends to that log directly, though. A server-side partition
leader sequences everything.

**F2. Every member that fences writers does it with an acquisition step that is not a journal
write.** `specs/write-authority.md` already argues this, and the family confirms it with
mechanisms that runstate's argument did not list.

- **Restate.** The server dispatches each attempt with a new epoch, and the processor "rejects any
  events from superseded epochs (late messages from older attempts)" (Restate architecture
  reference). Acquisition happens inside the server, not on the handler's write path.
- **Kafka.** `InitProducerId` "bumps up the epoch of the PID, so that the any previous zombie
  instance of the producer is fenced off" (KIP-98).
- **Akka/Pekko** uses a third shape that write-authority.md does not list. Acquisition is a **read**
  (recovery replays to the highest sequence number). Every subsequent write is an implicit CAS,
  because each persist writes `seq = last+1` under `PRIMARY KEY(persistence_id, sequence_number)`.
  Once a newer incarnation has written, every later write by a stale incarnation collides. The
  default reaction is that the actor "will stop if an exception is thrown from the journal".
  - runstate cannot take this shape without giving up F1. If the worker CAS'd every append, each
    operator `control.stop` or launcher record would make the worker's next append fail. The
    worker would then have to re-read the log and decide whether it had been displaced or merely
    interleaved. That is revision 3's detect-at-write, with the same "one step body too late"
    artifact problem.
  - *Analysis, not measured.* The event-sourcing answer to issue #32 is "move the inbox off the
    journal". That costs the single total order the discharge fold relies on.

**F3. The entity is code that the runtime hosts.** Akka and Orleans actors live in a JVM or .NET
process. Restate handlers are pushed to an HTTP endpoint: the partition leader "opens a
bidirectional stream to the target service endpoint" (architecture reference).

A mycooc run is different: a Python process on a preemptible GPU node, started by mycooc's own
`Popen`, which resumes from **its own** checkpoint. In every system here, that process is either:
- **outside the entity**, so the actor is a proxy and liveness has to be rebuilt by hand; or
- **inside a handler**, which needs Restate's push model, raised timeouts, and replay-deterministic
  code around the training loop.

None of the three fences the **artifact plane**: the checkpoint directory a zombie keeps writing.
Restate fences a stale attempt's *journal entries*. It does not stop the stale attempt's `ctx.run`
body, which is where checkpoint writes happen. That is write-authority.md's revision-2 refutation
#2, true of the whole family.

---

## 1. Akka / Apache Pekko Persistence (with Cluster Sharding)

**Versions:** Pekko 1.7.0 and Akka core 2.10.23 docs. Akka is BUSL-1.1; Pekko is Apache-2.0.

**Summary.**
- An `EventSourcedBehavior` has a `persistenceId` and an append-only journal of the events it
  persists. On start it recovers by replaying snapshot plus events. "The highest sequence number
  will always be recovered so you can keep persisting new events without corrupting your event log".
  Commands that arrive during recovery are stashed.
- Single writer is a stated precondition, not a property of the journal. "For a particular
  `persistenceId` only one persistent actor instance should be active at one time. If multiple
  instances were to persist events at the same time, the events would be interleaved and might not
  be interpreted correctly on replay."
- Cluster Sharding supplies it ("ensures that there is only one active entity for each id"), and
  the Split Brain Resolver backs that up.
- The SQL journals are physically the same table as runstate's `PostgresChannel`.
  - pekko-persistence-jdbc's Postgres DDL is `event_journal(... persistence_id, sequence_number,
    writer, write_timestamp, event_ser_id, event_ser_manifest, event_payload ...,
    PRIMARY KEY(persistence_id, sequence_number))`.
  - R2DBC adds a DB-clock `db_timestamp`.
- Readers use Persistence Query. `eventsByPersistenceId` is "a query equivalent to replaying an
  event sourced actor … it is possible to keep it alive and watch for additional incoming events".
  It requires an ActorSystem.

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** | `persistenceId` outlives incarnations; recovery from journal + snapshot. Incarnations are not recorded as episodes. The per-event `writer` (writer UUID) column makes incarnation boundaries *derivable*, but only for incarnations that wrote something. An external process's own checkpoint is not modelled: the actor's state is the journal fold. | Pekko persistence 1.7.0 "Recovery"; jdbc DDL |
| 2 | Single-spawn | **covered** (for the actor) | Cluster Sharding: one active entity per id. SBR downs the minority side; `lease-majority` uses an external lease. **Detection:** the PK collision on `(persistence_id, sequence_number)`; persist failure stops the actor by default. **Repair at read:** replay filter on `writerUuid`, default `repair-by-discard-old`. **Residual:** "GC pauses … if pauses exceed stable-after × 2, brief dual-instance windows occur"; failover is ~45 s with defaults. | Akka 2.10.23 persistence (journal failures, replay filter); Pekko SBR docs; jdbc DDL |
| 3 | Liveness / failure detection | **partial** | Node-level cluster failure detection only. The entity is virtual: sending it a message *re-creates* it, so "is run X dead?" is not an entity-level question. Nothing models an external process. A later third party has no liveness record unless you design one. | Pekko cluster sharding 1.7.0 (passivation, remember entities) |
| 4 | Terminal verdict | **absent** | User-defined events plus `thenStop`. No outcome vocabulary. An entity can always accept further commands, so "extend" is whatever your command handler allows. | Pekko persistence 1.7.0 |
| 5 | Cooperative stop | **partial** | A command to the actor. Durable only if the actor persists it as an event, in which case it survives into the next incarnation because recovery replays it. Commands are stashed during recovery. Not built in. | Pekko persistence 1.7.0 (stashing) |
| 6 | Per-step values | **partial** (pattern fits well) | One event per step; any JVM reader with the serializers tails it live via `eventsByPersistenceId`. This is a push stream, better than polling. Values must route through the actor. | Pekko persistence-query 1.7.0 |
| 7 | Memoisation / produce-on-miss | **absent** | A user pattern: an entity keyed by a content hash answers `EnsureUpTo(N)` from state, or launches. Nothing built in. | — |
| 8 | Demand / subscriptions | **partial** | "Remember entities" restarts entities "after a rebalance or entity crash", which acts as durable demand. Idle passivation (default 2 min) is idle-stop. Readers tail the log but cannot ask a worker to report something. | Pekko cluster sharding 1.7.0 |
| 9 | Derived runs / reuse graphs | **absent** | User-level actor composition. | — |
| 10 | Retention / GC | **covered** | Snapshots plus event deletion on snapshot (`RetentionCriteria`) `[API names unverified on page]`. Journal tags. | Pekko persistence 1.7.0 TOC |
| 11 | Time | **covered** | `write_timestamp` (JDBC) or `db_timestamp` (R2DBC, DB clock) on every row. | jdbc and r2dbc DDL |
| 12 | Write authority / provenance / forgery | **partial** | `writer` UUID per event (recorded); PK plus sharding (enforced, for the actor). No authentication of who sent a command. | jdbc DDL; Akka replay filter |
| 13 | Deployment shape | **conflicts** | A JVM ActorSystem. Single-writer needs a Cluster plus SBR plus a journal DB (Postgres, Cassandra and others). Akka needs a commercial licence for production, $0 under $25M revenue but still a granted licence; Pekko is Apache-2.0. | akka.io/bsl-license-faq |
| 14 | Constraints on worker code | **conflicts** | JVM (Scala/Java). Actors must not block. A Python training loop cannot be the entity, so this is proxy-only. | — |
| 15 | Interop | **partial** | The SQL DDL is public. Payloads are `(ser_id, manifest, bytes)` from Akka serialization (Jackson JSON is possible), with no language-neutral record schema. | jdbc DDL |

**Strongest case that it, plus a thin layer, subsumes runstate.**
- The substrate is already the same: one row per event, a dense per-entity seq, the identical
  primary key, a writer column runstate lacks, and a DB timestamp.
- A `RunProxy` entity per content-addressed `run_id` would be the run's *opinionated* sequencer:
  - It decides the claim and issues an episode token.
  - It **rejects** reports that carry a stale token. That is log-plane fencing, which runstate
    calls structurally unavailable to itself (write-authority.md §"Why fencing tokens are not
    available"), because an actor *is* a substrate allowed to route on message type.
- Durable stop is an event, and recovery replays it.
- Observers tail the journal live.
- Remember-entities gives durable demand.
- Snapshots and retention are built in.

**Strongest case that it doesn't.**
- Both consumers are Python, and the actor would only be a proxy for the GPU process. Everything
  that is hard in runstate sits on the far side of that proxy:
  - process liveness across hosts;
  - the reclaim of a crashed foreign claim;
  - the "a zombie still writes checkpoints" plane.
- In practice you would rebuild runstate's heartbeat tier and claim rule as actor logic, then
  operate a JVM cluster to host them.
- A third-party reader needs an ActorSystem plus the serializers, or must decode
  `event_payload` blobs by hand. That is far from "anything that can read a file".
- **Adoption cost:** a JVM service, a cluster with SBR, a journal DB, a proxy protocol, and a
  Python client. Existing logs would have to be migrated into journal rows with Akka-serialized
  payloads. High.

---

## 2. Microsoft Orleans

**Versions:** v10.3.1 (2026-08-28), MIT. Docs dated 2025-03 to 2026-02.

**Summary.**
- Virtual actors ("grains"). "Grains have stable logical identities. They can activate … and
  deactivate many times …, but at most one activation of a grain exists at any point in time"
  (grain directory, 2026-01-22).
- That guarantee depends on the directory:
  - The default directory is "Eventually consistent … Allows occasional duplicate activations
    during cluster instability".
  - A strongly consistent in-cluster directory "prevents duplicate grain activations even during
    cluster instability". It is marked **preview** from Orleans 10.0 as of that page.
- Grain state persistence uses ETags. An ETag violation "*should* cause the write Task to be
  faulted with … `InconsistentStateException`".
- Event sourcing is `JournaledGrain` over log-consistency providers. The closest analogue to
  runstate's API:
  - **`RaiseConditionalEvent`** "double-check[s] if the local version matches the version in
    storage. If not … the conditional event is *not* appended".
  - "It's possible and sensible to use both conditional and unconditional events for the same
    grain". That is exactly runstate's `send(expected_seq=)` beside plain `send`.
- runstate's own `specs/service-worker.md` already names Orleans "the near-isomorph — eternal
  identity ÷ activations ≈ `run_id` ÷ episodes".

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** | Eternal grain identity, transient activations. Activations are not recorded as history. External-process checkpoints are not modelled. | grain directory (2026-01-22) |
| 2 | Single-spawn | **partial** | **Default:** the directory is eventually consistent and allows duplicates. **Opt-in:** a strong directory (10.0 preview) or a storage-backed directory. **Backstop:** the ETag `InconsistentStateException`. **CAS:** `RaiseConditionalEvent`. | grain directory; grain persistence (2026-02-06); replicated instances (2025-05-23) |
| 3 | Liveness / failure detection | **partial** | Silo membership only. A grain always "exists", because a call activates it. Nothing models an external process. | grain directory |
| 4 | Terminal verdict | **absent** | User-defined. | — |
| 5 | Cooperative stop | **partial** | `CancellationToken` is cooperative, but "Cancellation signals are only propagated while the call is active", so it is **not durable**. A durable stop has to be grain state you write. | cancellation tokens (2026-01-21) |
| 6 | Per-step values | **partial** | JournaledGrain events. `RetrieveConfirmedEvents` works only on `LogStorage`, which stores "the complete event sequence as a single object" and is "*not suitable for production use*". `StateStorage` keeps no events. `CustomStorage` is yours to write. | log-consistency providers (2025-03-29) |
| 7 | Memoisation | **absent** | User pattern. | — |
| 8 | Demand / subscriptions | **partial** | Reminders (durable; reactivate the grain) and timers (volatile). runstate's service-worker.md copied the "two durabilities" from here and rejected the idle-timeout collection. | runstate `specs/service-worker.md:33-41` |
| 9 | Derived runs | **absent** | — | — |
| 10 | Retention / GC | **partial** | `ClearStateAsync`; otherwise per provider. No log retention policy. | grain persistence |
| 11 | Time | **partial** `[unverified]` | Depends on the provider. | — |
| 12 | Write authority / provenance | **partial** | ETag enforcement on state writes. No per-event provenance. | grain persistence |
| 13 | Deployment shape | **conflicts** | A .NET silo cluster plus a membership provider plus storage providers. | — |
| 14 | Constraints on worker code | **conflicts** | .NET only. Single-threaded grain turns. Calls time out after 30 s by default (`MessagingOptions.ResponseTimeout`). | Orleans API docs |
| 15 | Interop | **absent** | No language-neutral wire or record format. Grain state is JSON by default since 7.0, but its layout is internal. | grain persistence |

**Strongest case that it subsumes runstate.**
- Conceptually it is the same object: eternal identity, many activations, a conditional plus
  unconditional append mix, and durable versus volatile demand.
- `CustomStorage` (`ApplyUpdatesToStorage(updates, expectedVersion)`) could be backed by a table
  identical to `PostgresChannel`. Orleans would then add single activation and turn-based
  concurrency on top of runstate's substrate.

**Strongest case that it doesn't.**
- .NET only, for Python consumers.
- Cancellation is non-durable.
- The default directory admits duplicates.
- The one provider that keeps the readable event list is "not suitable for production".
- The external GPU process is again behind a proxy.

**Adoption cost:** a .NET service plus a cluster. High, and higher than Akka in practice, because
nothing is gained over Akka for Python consumers.

---

## 3. Restate

**Versions:** server v1.7.13 (2026-10-01). Server licence BUSL-1.1 with an Additional Use Grant
that explicitly permits "Any type of production deployment … invoking services or workflows
written by the Licensee". SDKs for TS, Java/Kotlin, Python, Go, Rust and Ruby.

**Summary.**
- Durable execution plus keyed entities.
- A **Virtual Object** is a "Stateful entity with a unique key". Its K/V state is "retained
  indefinitely". "At most one handler with write access can run at a time per object key", which
  "Mimicks a queue per object key". Shared handlers "can only read state but run concurrently".
- A **Workflow**'s `run` handler "executes exactly once per workflow ID".
- Each invocation has a journal:
  - Every `ctx.run` result, state write, call, timer and promise is appended to the partition log
    (Bifrost) **before** it "happens".
  - On failure "the processor dispatches a new attempt and attaches the full journal so far.
    Attempts carry monotonically increasing epochs; the processor rejects any events from
    superseded epochs".
- The server is the failure detector:
  - inactivity timeout, default 1 min, then a request to suspend;
  - abort timeout, default 10 min, then forcible abort.
- Third parties read through SQL introspection (`sys_invocation`, `sys_journal`, `state`, …) on a
  running server.
- Durable control:
  - cancel, kill, pause, resume;
  - **signals**, which "deliver durable notifications to an ongoing invocation … Restate stores
    each resolution durably until the invocation receives it";
  - awakeables and workflow promises.

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** (closest in the family) | **Identity:** the Virtual Object key, with K/V state kept indefinitely. New invocations on the same key can continue the work ("extend"). **Attempts:** epoch-numbered and retried with the journal attached. **Not kept as history:** `retry_count` "is not a global attempt counter across invocation suspensions and leadership changes", and only `last_attempt_*`, `last_failure*` and `last_start_at` survive. **Journal retention** after completion defaults to 24 h. Restart-as-new and restart-from-prefix mint a **new** invocation id. | services; sql-introspection (`sys_invocation`); service configuration; managing invocations |
| 2 | Single-spawn | **covered** (for handler executions) | Exclusive handler per key (queue per key); attempt epochs fence stale attempts' journal events; partition leaders are fenced by epoch plus segment seal. It does **not** fence external side effects inside a stale attempt's `ctx.run`. | services; architecture reference |
| 3 | Liveness / failure detection | **covered** for handler streams; **partial** for an external process | The server watches the stream and the inactivity and abort timeouts. Status is `pending/scheduled/ready/running/paused/backing-off/suspended/completed`, with `modified_at` and `last_start_at`, all queryable by a third party. If the GPU job is *behind* the handler, its liveness is yours to model with durable timers. | introspection; sql-introspection; service configuration |
| 4 | Terminal verdict | **partial** | `completion_result` is success or failure, plus `completion_failure`, kill and cancel. Crash is not terminal: the invocation retries, then "pause" after max attempts. There is no "completed vs resumable-preempted" vocabulary. **Extending a completed run:** a Workflow **cannot** (run-once per id); a Virtual Object can (new invocation, same key). | sql-introspection; services; service configuration |
| 5 | Cooperative stop | **partial** | **Cancel:** recorded by the runtime and surfaced "at the next await point". It propagates down the call graph so compensation can run. **Gaps:** if cancelled "during run block execution, then a terminal error gets thrown here once execution finishes", so it is invisible inside a long `ctx.run`. It also needs the deployment to be reachable. Cancel ends the invocation as terminal-failed, not resumable. **Signals:** durable and repeatable, but addressed to an invocation id, not to the object. They survive retries of that invocation, not a new invocation. | managing invocations; external events (Python) |
| 6 | Per-step values | **partial** | **K/V state** is a last-write-wins register per key. **Journal entries** carry `appended_at`, so a per-step `ctx.run` result is visible while live. That history is subject to journal retention. There is no series primitive. Readers use shared handlers or SQL. | state; sql-introspection |
| 7 | Memoisation / produce-on-miss | **partial** | **Idempotency keys** return the committed result for a duplicate call (retention 24 h default). **Workflow-per-id:** run once, then `attach`. **K/V state** can serve as a durable cache. There is no "read up to step N, extend for the rest" primitive. | architecture reference; service configuration |
| 8 | Demand / subscriptions | **partial** | Signals, awakeables, durable timers and Kafka ingestion. No reader-specified reporting schedule or condition algebra. No leases. | external events |
| 9 | Derived runs | **partial** | Durable calls between objects; keys are user-chosen, so content-addressed derived ids are user-level. | services |
| 10 | Retention / GC | **covered** | Journal, idempotency and workflow retention; purge; purge journal; log trimming after snapshots. | service configuration; admin API index |
| 11 | Time | **covered** (via the server) | `created_at`, `running_at`, `completed_at`, `last_start_at`, and per-entry `appended_at` (journal v2). | sql-introspection |
| 12 | Write authority / provenance | **covered** for the journal (enforced epochs); **partial** otherwise | Epoch fencing; `invoked_by*` and `restarted_from` provenance fields. Request authentication `[not surveyed]`. | architecture; sql-introspection |
| 13 | Deployment shape | **conflicts** | `restate-server`: a single binary plus a persistent volume. Clusters add a Raft metadata store and S3 snapshots. Data lives in Bifrost segments plus RocksDB, not a file a third party can open. | self-hosted overview; architecture |
| 14 | Constraints on worker code | **conflicts** | **SDK:** required. **Push model:** the server calls your HTTP endpoint. **Determinism:** "Non-deterministic operations … must be wrapped" in `ctx.run`. **Timeouts:** must be raised above the longest gap between journal entries. **Cancellation:** not visible inside `ctx.run`. | durable steps; service configuration; architecture |
| 15 | Interop | **partial** | **Documented:** the SDK↔server protocol (prose spec, archived in `restatedev/service-protocol`, protobufs now in `restatedev/restate/service-protocol`), HTTP ingress, an OpenAPI admin API, and SQL. **Not portable:** the stored journal (`raw` binary; `entry_json` only for v2). | sys_journal schema; service-protocol repo |

**Strongest case that it, plus a thin layer, subsumes runstate.** Take mycooc and make the GPU
process **itself** the Restate deployment. Model the run as a Virtual Object keyed by the
content-addressed rid.

- **The training loop.** An exclusive handler `extend(target)` reads `final_step` from K/V. It
  then loops over checkpoint-sized chunks, `await ctx.run("chunk", train_from_own_checkpoint)`, and
  `ctx.set("final_step", k)`.
- **Crash or preemption.** The stream drops, so Restate re-dispatches with a higher epoch and
  attaches the journal. Replay skips finished chunks, and the worker resumes from its own
  checkpoint at the journaled step.
- **The claim and the cross-host wedge.** These become the server's problem. runstate's
  unresolvable crashed foreign claim that "reads live forever" (`backlog/cross-host-claim-gate.md`)
  and mycooc's `reclaim_experiment.py` exist because runstate has **no** central detector. Restate
  is one, and it fences the stale attempt's journal writes.
- **Reuse and gating.**
  - A finished run is reusable through K/V state kept indefinitely.
  - translation's "gate on completion" maps onto Workflow-per-content-id plus `attach`.
  - Step milestones can be workflow promises.
- **Observation.** Restate already ships a UI and SQL introspection, which is cockpit-like.
- Of everything surveyed, this comes closest to falsifying "does not exist elsewhere": identity
  outliving processes, attempts fenced and resumed, server-checked single-spawn, durable stop
  requests, and a Python SDK.

**Strongest case that it doesn't.**
- **It is a service, and the record is not durable.** The journal and attempt history are
  retained for 24 h after completion by default. Attempts survive only as "last attempt" fields.
  Storage is Bifrost and RocksDB, readable only through a running server. That inverts
  positioning.md's bet ("the log is a file beside the run … readable in ten years with `sqlite3`")
  and its "whole history is one re-readable artifact".
- **The push model does not fit how both consumers run.**
  - Restate must open a stream *to* the GPU process, which needs inbound reachability on compute
    nodes (environment-dependent, `[unverified]` for the owner's machines).
  - mycooc spawns its own processes and translation uses runstate's launchers. Restate dispatches
    to a registered deployment URL and does no host or GPU placement.
  - The proxy alternative (the handler submits the job and awaits a signal) puts liveness back on
    the far side, to be rebuilt with timers and heartbeats.
- **Cooperative stop does not reach a long step.** Cancel is invisible inside a long `ctx.run`.
  Shortening `ctx.run` to one step makes the journal grow per step, with replay cost on every
  retry. Cancel also yields a *terminal* failure, not runstate's resumable `preempted`.
- **Exclusive handlers serialise everything on the key.** While `extend` holds the key, "all other
  calls to this object will be queued", including an exclusive `report_value`. Per-step metrics
  need a second object or K/V writes from the handler itself.
- **Adoption cost: medium.**
  - Operate one server, single-node is acceptable.
  - Rewrite the worker integration as a handler with chunked `ctx.run`.
  - Raise timeouts.
  - Migrate the existing per-run logs. There is no journal import; K/V state can be written
    through the admin "modify service state" API. Per the no-legacy rule this is a real migration,
    not a shim.
  - Accept that the long-term record is no longer the run's own file.

---

## 4. Brief entries

### 4a. Dapr Actors and Dapr Workflow (v1.18; Apache-2.0)

**Summary.**
- **Actors.** Virtual actors over a sidecar, so the language is neutral: HTTP/gRPC, with a Python
  SDK. Access is turn-based ("no more than one thread can be active inside an actor object's
  code"). Placement routes "the same partition … for any given actor id". Failure-time
  single-activation guarantees are not stated on the pages read `[unverified]`. State outlives the
  actor in a state store. Reminders are durable ("fire whether an actor is active or inactive");
  timers are not.
- **Workflow.** Event-sourced ("an append-only log of history events") with deterministic
  orchestrators and at-least-once activities. Operations: raise-event, suspend/resume, terminate,
  purge.
- **Conflict.** "Only one workflow instance with a given ID can exist at any given time", and
  reusing an id after it terminates means "Previous execution history is overwritten". The
  recommendation is "Give every workflow execution an instance ID that has never been used
  before". That directly contradicts "one identity, many attempts, one history".

| # | concern | rating | note |
|---|---|---|---|
| 1 | Identity | **conflicts** (Workflow) / **partial** (Actors) | Workflow id reuse overwrites history. Actor id is durable; activations are not recorded. |
| 2 | Single-spawn | **partial** | Placement plus turn-based access; failure-time guarantee `[unverified]`. |
| 3 | Liveness | **partial** | Runtime-level only. |
| 4 | Verdict | **partial** | Workflow runtime status COMPLETED/FAILED/TERMINATED (a closed set). Extension needs a new id. |
| 5 | Stop | **partial** | `terminate` is not cooperative. raise-event is durable and can carry a cooperative stop that the orchestrator polls. |
| 6 | Values | **absent** | Custom status or state only. |
| 7 | Memo | **absent** | — |
| 8 | Demand | **partial** | Reminders. |
| 9 | Derived | **absent** | Child workflows exist, but nothing keys them by content identity. |
| 10 | Retention | **covered** | Purge. |
| 11 | Time | **partial** | History events carry timestamps `[unverified]`. |
| 12 | Authority | **partial** | — |
| 13 | Deployment | **conflicts** | Sidecar, placement service and state store. |
| 14 | Worker constraints | **conflicts** (Workflow determinism) / **partial** (Actors) | — |
| 15 | Interop | **partial** | HTTP/gRPC sidecar API; history encoding is internal. |

Sources: docs.dapr.io actors-features-concepts and workflow-features-concepts (v1.18).

### 4b. KurrentDB (formerly EventStoreDB; v26.2.0, 2026-09-30; Kurrent License v1, source-available)

**Summary.**
- One stream per entity.
- Appends take an expected stream state: `ANY` ("No concurrency check"), `NO_STREAM`, `EXISTS`, or
  an integer position. A mismatch raises `WrongCurrentVersionError`. This "will protect the stream
  from becoming inconsistent due to conflicting concurrent writers" (Python client v1.2).
- Stream metadata gives `$maxAge`, `$maxCount`, `$tb` (truncate-before) and per-stream `$acl`
  read/write ACLs (server docs v22.10).
- Catch-up subscriptions tail a stream live `[from general knowledge; not fetched]`.
- **This is runstate's substrate, almost exactly.** `design-v0.2.md` §4 already cites EventStore
  `expectedVersion` as the CAS's ancestor.

**The difference from runstate's CAS, specifically.**
- The primitive is the same.
- Event-sourcing *practice* applies it on **every** append of an aggregate, so the stream position
  is itself the fencing token. That is the Akka pattern of F2, and it is possible only because
  commands stay off the stream (F1).
- runstate applies it at **two** sites (claim and death) because its stream is multi-writer by
  design.

| # | concern | rating | note |
|---|---|---|---|
| 1 | Identity | **partial** | A stream per run; no episode conventions. |
| 2 | Single-spawn | **partial** | The CAS primitive exists; arbitration logic is yours (the same as runstate's). |
| 3 | Liveness | **absent** | — |
| 4–9 | Verdict, stop, values, memo, demand, derived | **absent** | Conventions are yours. |
| 6 | Values (readability) | **partial** | Live tail via subscription. |
| 10 | Retention | **covered** | `$maxAge`, `$maxCount`, `$tb`, scavenge. |
| 11 | Time | **covered** `[unverified]` | Per-event created timestamp. |
| 12 | Authority | **partial** | Per-stream ACLs are **enforced** write authority per user. runstate has none. |
| 13 | Deployment | **conflicts** | A server is required. |
| 14 | Worker constraints | **covered** | None beyond a client; there is an official Python client. |
| 15 | Interop | **partial** | Public gRPC API and many clients; the storage format is not an interop surface. |

**Verdict.** A `KurrentChannel` backend would be about `PostgresChannel`-sized and would change
nothing above the substrate. This supports positioning.md's claim that the substrate exists
elsewhere, and does nothing against its identity claim.

### 4c. Kafka: only what positioning.md missed

positioning.md's row says "No claim arbitration for an entity". Three corrections or additions:

1. **Zombie fencing per logical producer exists.**
   - A `transactional.id` per `run_id` gives exactly the "single writer over time" that runstate
     says it cannot offer. `InitProducerId` "bumps up the epoch … so that any previous zombie
     instance of the producer is fenced off"; stale producers get `ProducerFencedException`
     (KIP-98).
   - This is a **different kind of operation** from `produce`, which is the condition
     write-authority.md names. write-authority.md already cites it as the canonical example;
     positioning.md's table should not say "no claim arbitration" without that qualification.
   - **Limits:** it fences only transactional writes from that producer id, it says nothing about
     liveness or artifacts, and it needs a broker cluster.
2. **Compaction is a server-side last-write-wins register.** It retains "at least the last known
   value for each message key". Keyed by `(run, metric, step)`, a compacted topic *is*
   `value_series`'s take-the-latest read (Confluent "Log Compaction" design doc).
3. **Per-entity identity can be emulated** by key-within-partition. Reading one run means scanning
   its partition, which is why positioning.md's "topics and partitions, not this run" stands for
   reads.

---

## 5. Across the family

### Which system comes closest

**Restate** comes closest on coverage. Counting the 15 concerns:
- **covered:** 5 (single-spawn for handlers, retention, time, journal write authority, handler
  liveness);
- **partial:** 7;
- **conflicts:** 2 (deployment, worker constraints);
- **interop:** partial.

It has the only first-party Python story and the only server-side failure detector plus fencer.
It would dissolve runstate's hardest open problems: the cross-host wedge, reclaim, and fencing of
the log plane. It does so by being exactly what runstate decided not to be, a service.

**Orleans** is the closest *conceptual* isomorph, as runstate's own `service-worker.md` already
records. Eternal identity ÷ activations matches `run_id` ÷ episodes, and `RaiseConditionalEvent`
beside `RaiseEvent` matches `send(expected_seq=)` beside `send`. It is .NET-only.

**Akka/Pekko's SQL journal** is the closest *substrate*: the identical primary key, plus a writer
UUID and a DB timestamp that runstate's log lacks.

### Answers to the four key questions

1. **CAS versus single-writer enforcement.**
   - **Akka:** prevention by sharding and SBR, detection by the dense-seq primary key on every
     persist, repair at read by `writerUuid` "repair-by-discard-old". That is three layers.
   - **Orleans:** prevention by the directory (eventually consistent by default), detection by
     ETag or `RaiseConditionalEvent`.
   - **Restate:** server-assigned attempt epochs that reject superseded events.
   - **KurrentDB:** the bare CAS, applied per append by convention.
   - runstate's "single writer only at the claiming instant" is the consequence of F1, not of a
     missing feature. Every member that does better either keeps commands off the journal, so
     every write can be a CAS (Akka, KurrentDB practice), or puts a server-side sequencer in front
     of the writer (Restate, Kafka).
   - None of them protects the artifact plane. Restate fences a stale attempt's journal events but
     not its `ctx.run` body. write-authority.md's conclusion that fencing "cannot protect the
     plane where the harm is" holds across the family.
2. **Actor versus long external process.** In Akka, Orleans and Dapr Actors the actor would be a
   thin proxy. It would buy log-plane fencing, because an actor is a substrate allowed to route on
   message type. It would cost:
   - a JVM or .NET service;
   - rebuilding liveness inside the proxy;
   - leaving the external process's checkpoint as unfenced as today.

   Restate alone can host the loop itself. The cost is the push model, chunked `ctx.run` with
   deterministic glue, raised timeouts, and cancellation that is blind inside a chunk.
3. **Third-party read without the runtime.**
   - **Restate:** no. SQL on a running server; the journal is retained 24 h by default.
   - **Orleans:** only by decoding provider blobs.
   - **Akka:** SQL rows yes, payloads only with the serializers. The official route needs an
     ActorSystem.
   - **KurrentDB and Kafka:** a server, but any client language.
   - No member offers "a file beside the run, readable with `sqlite3`".
4. **Cooperative stop and per-step series.**
   - **Stop is natural in Akka**, as a persisted command-event replayed on recovery, but you write
     it.
   - **Stop is non-durable in Orleans** (`CancellationToken` only while the call is active).
   - **Stop is durable but blunt in Restate:** terminal, and invisible inside `ctx.run`. Signals
     are the better fit and are addressed to an invocation, not to the run.
   - **Per-step series are natural in Akka.** Events tailed live by `eventsByPersistenceId` are a
     better observer read than runstate's polling.
   - **Elsewhere the series is awkward:** a last-write-wins K/V register in Restate, and a
     non-production `LogStorage` in Orleans.

### runstate concerns no system in the family covers

- **Attempts as first-class, retained history of one identity.** "Attempt 4 is the same run as
  attempts 1–3, and the whole history is one re-readable artifact."
  - Virtual actors give the identity.
  - None keeps the attempts as readable episodes. Restate keeps only last-attempt fields and its
    `retry_count` is explicitly not global. Akka's `writer` column makes boundaries derivable only
    for incarnations that wrote.
  - **This is the part of positioning.md's claim that survives.**
- **An external, self-checkpointing process as the entity.** Every member assumes the runtime
  hosts the entity's code.
- **No service.** The record is a file that outlives the tooling.
- **Cold third-party liveness of an external process from dated records plus handle probes.** In
  virtual-actor systems "dead" is not an entity state, because messaging re-creates the entity.
- **A resumable-versus-completed verdict vocabulary that coexists with extending a completed run
  under the same identity.** Dapr Workflow has a closed status enum but overwrites history on id
  reuse; Restate Workflows run once per id.
- **`ensure`-style produce-on-miss up to step N, with extension.** Restate's idempotency and
  workflow-attach cover whole results within a retention window.
- **A reader-specified reporting schedule** (the subscription condition algebra). Note that no
  runstate consumer uses it either.
- **Content-addressed derived-run identity recipes.**
- **A language-neutral schema for the stored records** (runstate's JSON Schema stack). Restate and
  KurrentDB document their *APIs*; Akka documents its *DDL*; none documents its stored records.

### What the family does better than runstate (fairness)

- **Log-plane fencing.** Restate epochs, Akka's primary key plus actor stop, Kafka producer epochs.
- **A central failure detector.** Restate removes the cross-host wedge class entirely.
- **Push-based live tailing for observers.** Akka Persistence Query, KurrentDB subscriptions.
- **Built-in retention policies.** All of them.
- **Enforced per-stream access control.** KurrentDB `$acl`.
- **A shipped inspection UI and SQL.** Restate.
- **A writer identity on every record.** Akka `writer`, which runstate's #39 "author-blind
  discharge" lacks.

### Implications for positioning.md (for the owner to weigh)

- **Narrow "does not exist elsewhere".** A durable identity that outlives its processes is the
  *definition* of virtual actors: Orleans, "Grains have stable logical identities. They can
  activate … and deactivate many times". The table's Erlang and actors rows ("the unit is a
  process, not a run spanning processes") predate virtual actors with persistence and should
  concede that.
- **Say what is distinctive.** It is the *combination*: an external, self-checkpointing process,
  attempts recorded as re-readable episodes, and no service.
- **Add Restate and Orleans/Akka rows.** Include an honest "use Restate instead when…" case: when
  you can run a server, your workers can be HTTP endpoints, and you want enforced single-writer
  and central failure detection more than a durable file.
- **Qualify the Kafka row** with `transactional.id` fencing.

### Optional, for the redesign (`docs/backlog/if-built-today/`)

- **Akka Replicated Event Sourcing** deliberately relaxes the single-writer principle: "running
  multiple replicas of each entity", state "eventually consistent", and the event handler "must be
  able to handle concurrent events" via CRDTs. This is prior art for the no-central-store regime
  the redesign targets.
- **Orleans' replicated JournaledGrain** cites the Global Sequence Protocol (GSP) for the same
  regime.
- **Restate signals** ("can be resolved multiple times … stored durably until the invocation
  receives it") are a told, durable, per-recipient message: close to the redesign's
  withdrawal-by-revealing-a-secret lease shape, but without unforgeability.
- No member has quantified questions, told negative facts over regions, or demand derived by
  rules.

---

## Sources (fetched 2026-10-02)

**Akka / Pekko**
- Pekko Persistence (typed), 1.7.0: https://pekko.apache.org/docs/pekko/current/typed/persistence.html
- Akka core Persistence, 2.10.23 (journal failures, `thenRun` at-most-once, replay filter `repair-by-discard-old`, BUSL-1.1): https://doc.akka.io/libraries/akka-core/current/typed/persistence.html
- Pekko Split Brain Resolver: https://pekko.apache.org/docs/pekko/current/split-brain-resolver.html
- Pekko Cluster Sharding, 1.7.0: https://pekko.apache.org/docs/pekko/current/typed/cluster-sharding.html
- Pekko Persistence Query, 1.7.0: https://pekko.apache.org/docs/pekko/current/persistence-query.html
- Pekko Replicated Event Sourcing: https://pekko.apache.org/docs/pekko/current/typed/replicated-eventsourcing.html
- pekko-persistence-jdbc Postgres DDL: https://github.com/apache/pekko-persistence-jdbc/blob/main/core/src/main/resources/schema/postgres/postgres-create-schema.sql
- pekko-persistence-r2dbc Postgres DDL: https://github.com/apache/pekko-persistence-r2dbc/blob/main/ddl-scripts/create_tables_postgres.sql
- Akka BSL FAQ: https://akka.io/bsl-license-faq

**Orleans** (v10.3.1, MIT)
- Grain directory (2026-01-22): https://learn.microsoft.com/en-us/dotnet/orleans/host/grain-directory
- Grain persistence (2026-02-06): https://learn.microsoft.com/en-us/dotnet/orleans/grains/grain-persistence/
- Event sourcing overview (2025-05-23): https://learn.microsoft.com/en-us/dotnet/orleans/grains/event-sourcing/
- Log-consistency providers (2025-03-29): https://learn.microsoft.com/en-us/dotnet/orleans/grains/event-sourcing/log-consistency-providers
- Replicated grains (2025-05-23): https://learn.microsoft.com/en-us/dotnet/orleans/grains/event-sourcing/replicated-instances
- Cancellation tokens (2026-01-21): https://learn.microsoft.com/en-us/dotnet/orleans/grains/cancellation-tokens
- `MessagingOptions.ResponseTimeout`: https://learn.microsoft.com/en-us/dotnet/api/orleans.configuration.messagingoptions.responsetimeout

**Restate** (server v1.7.13)
- Services: https://docs.restate.dev/foundations/services
- Managing invocations: https://docs.restate.dev/services/invocation/managing-invocations
- Introspection: https://docs.restate.dev/services/introspection
- SQL schema: https://docs.restate.dev/references/sql-introspection
- Service configuration (retention, timeouts): https://docs.restate.dev/services/configuration
- Architecture (epoch fencing, push stream): https://docs.restate.dev/references/architecture
- Signals and external events (Python): https://docs.restate.dev/develop/python/external-events
- Durable steps: https://docs.restate.dev/develop/python/durable-steps
- Self-hosted overview: https://docs.restate.dev/server/overview
- LICENSE (BUSL-1.1 with Additional Use Grant): https://github.com/restatedev/restate/blob/main/LICENSE
- Service invocation protocol spec (archived): https://github.com/restatedev/service-protocol/blob/main/service-invocation-protocol.md

**Dapr** (v1.18)
- Actors features: https://docs.dapr.io/developing-applications/building-blocks/actors/actors-features-concepts/
- Workflow features: https://docs.dapr.io/developing-applications/building-blocks/workflow/workflow-features-concepts/

**KurrentDB**
- Appending events (Python client v1.2): https://docs.kurrent.io/clients/python/v1.2/appending-events
- Streams metadata (v22.10): https://docs.kurrent.io/server/v22.10/streams
- Licence: KLv1, per https://github.com/kurrent-io/KurrentDB/blob/master/LICENSE.md and the docs landing page

**Kafka**
- KIP-98 (transactional.id, producer epoch, zombie fencing): https://cwiki.apache.org/confluence/display/KAFKA/KIP-98+-+Exactly+Once+Delivery+and+Transactional+Messaging
- Log compaction: https://docs.confluent.io/kafka/design/log_compaction.html

**runstate** (local checkout, branch `spec/episode-aim`)
- `docs/positioning.md`
- `docs/specs/write-authority.md`
- `docs/specs/service-worker.md:33-41`
- `docs/design-v0.2.md` §4
- the concern map `vs-shipped-map.md` (C1–C21)
