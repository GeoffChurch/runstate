# Prior-art survey: durable-execution engines vs runstate

Family surveyed: **Temporal** (with its ancestor **Cadence**), **DBOS Transact** (Python, both system-database
backends), **Azure Durable Functions / Durable Task Framework (DTFx)** in depth; **Inngest**, **Hatchet** and
**Restate's workflow service type** briefly.

Method: official docs and source, fetched 2026-10-02. Source clones used for claims the docs do not make:
`dbos-inc/dbos-transact-py` at `03fb5c9` (2026-10-01), `microsoft/durabletask-python` at `866cc78`
(2026-10-01), `microsoft/durabletask-mssql` (HEAD, 2026-10-02), and `temporalio/temporal` and
`temporalio/api` on `main`/`master` as of 2026-10-02. Source keys such as [T2] point to the list at the end.
Anything not confirmed in a primary source is marked `[unverified]`. My own judgements are marked
*assessment*.

Ratings: **covered / partial / absent / conflicts**. "Conflicts" means the system's model contradicts what
runstate needs, not merely that it lacks it.

---

## 0. The family-level question: is a GPU run a workflow or an activity?

All three in-depth systems split code into two kinds:

- **orchestration code** (Temporal *workflow*, DTFx *orchestrator*, DBOS *workflow function*), which is
  re-executed by replay and **must be deterministic**: [T6], [A2], [D2];
- **side-effecting code** (Temporal *activity*, DTFx *activity*, DBOS *step*), which is unconstrained, runs
  at least once, and has its result recorded.

A training loop does I/O, reads the clock and uses randomness, so it **cannot be orchestration code** in any
of the three. The mapping that works is:

> **run identity = workflow / instance ID**; **the GPU job = one activity or step** (or a chain of them);
> **the worker's own checkpoint is the resume point.**

What that mapping costs in each system:

| | Temporal activity | DBOS step | DTFx activity |
|---|---|---|---|
| Liveness signal during a multi-hour job | **Heartbeat** with a server-enforced Heartbeat Timeout [T2] | **None.** A step's row is written only when it completes, and the library has no heartbeat (no occurrence of "heartbeat" anywhere in `dbos/`) [D13] | **None in the API**: `ActivityContext` exposes only `orchestration_id` and `task_id` [A9]. Crash detection comes from backend lock expiry (MSSQL `taskEventLockTimeout`, default 2 min, renewed by `_RenewTaskLocks`) [A10] |
| Resume point handed to the next attempt | **Heartbeat details**: "the next Activity Task can access and continue with that payload" [T2] | Only completed steps are replayed. Inside a step, the worker resumes from its own checkpoint | Nothing. The activity re-runs with its original input |
| Cancellation reaching the running job | **On the heartbeat response** (`cancel_requested`). "Activities that don't Heartbeat can't receive a Cancellation" [T2] | At the **next step boundary**. A running synchronous step is not interrupted [D4], [D6] | **Never.** "Activity functions and sub-orchestrations run to completion" after terminate [A4] |
| Timeouts | Start-To-Close, Schedule-To-Close and Heartbeat, all optional [T2] | Workflow timeout, plus a per-step timeout for async steps only [D2], [D6] | The host's `functionTimeout`: 5 to 10 min on the legacy Consumption plan, unbounded on Flex, Premium and Dedicated [A7] |
| History cost | Small. Heartbeats are not history events, and retried attempts are **not** written either: "the ActivityTaskStarted Event will not show up … until the Activity Execution has completed or failed (having exhausted all retries)" [T14] | One row per completed step | Each scheduling, completion and failure appends history rows [A3] |
| Per-training-step metrics | **Do not fit in history.** It terminates past 51,200 events or 50 MB, and past 10,000 signals [T4], [T5] | **Streams**: append-only, offset-ordered, readable by anyone [D3] | Custom status, which holds the latest value only and is orchestrator-set [A4] |

Mapping the run to a *workflow* and running the training loop inside it is not available in any of the three:
the determinism rule forbids it. A "chunked" mapping is available: one activity or step per N training steps
inside a deterministic loop. It buys step-boundary cancellation and per-chunk records in DBOS and DTFx. It
costs one history event group per chunk in Temporal, which must Continue-As-New past the history limit and
so splits the run across Run IDs [T12].

---

## 1. Temporal (Cadence lineage)

**Summary.** Temporal is a server-centric durable-execution engine: a Frontend, History, Matching and Worker
service set over Cassandra, MySQL or Postgres, or Temporal Cloud. SQLite is supported "only for development
and testing" [T10], [T11].

- **Lineage.** It is a 2019 fork of Uber's Cadence by Cadence's creators. One of them earlier built DTFx at
  Microsoft, and both worked on AWS SWF, so the whole in-depth family shares one design lineage [T17]
  `[secondary source]`.
- **Identity and history.** A **Workflow ID** names a durable identity. "There can be at most one Workflow
  Execution with a given ID running at any point in time" [T1]. Each execution has an event-sourced history.
- **The activity contract is the closest semantic match to a long GPU run in this survey:**
  - a server-enforced heartbeat timeout;
  - heartbeat details passed to the next attempt as a checkpoint;
  - cancellation delivered on the heartbeat;
  - stale attempts fenced: a heartbeat or completion from an attempt the server has already timed out gets
    `NOT_FOUND` [T15].
- **Standalone Activities.** The new feature is "Temporal's job queue": a top-level activity with its own
  **Activity ID**, ID conflict and reuse policies, and heartbeat checkpointing [T7]. It is close to "one run =
  one durable job" with no workflow at all.
  - Release status is inconsistent between sources. The blog post of 2026-09-15 says "Public Preview"; the
    docs page, read 2026-10-02, says "Generally Available" for the core and Public Preview for
    pause, unpause, reset and update-options [T7], [T7b].
- **What it does not hold:** the run's per-step data. Its history is server-held, deleted after the namespace
  Retention Period [T11], and stored as **serialized protobuf blobs** in internal tables (`history_node.data`)
  [T9]. Nothing about it is "a file beside the run".

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** | Workflow ID; activity retries keep the Activity ID, and the attempt counter plus heartbeat details carry progress, so "attempt 4 resumes from step 400" holds. **But** intermediate attempts are deliberately left out of history: only the final `ActivityTaskStarted` with an attempt count, and `last_failure` in `PendingActivityInfo`. New Run IDs (retry, cron, reset, continue-as-new) get fresh histories. Closed histories are deleted at retention | [T1], [T2], [T3], [T12], [T14] |
| 2 | Single-spawn | **covered** (stronger than runstate) | The server enforces one open execution per Workflow ID per namespace. The conflict policy is Fail, UseExisting or TerminateExisting. Stale activity attempts are **fenced over time**: their heartbeats and completions get `NOT_FOUND`. The fencing covers writes to Temporal only, not side effects such as a zombie process writing checkpoints | [T1], [T15] |
| 3 | Liveness / failure detection | **covered** via the server; **conflicts** with no-service | The server enforces the Heartbeat and Start-To-Close timeouts. A later third party sees `last_heartbeat_time`, `heartbeat_details`, `attempt` and `last_worker_identity` through `DescribeWorkflowExecution`, which needs the server up. There is one verdict, the server's | [T2], [T3] |
| 4 | Terminal verdict | **partial** | Closed statuses are Completed, Failed, Canceled, Terminated, TimedOut and ContinuedAsNew. **There is no "resumable-interrupted" closed state**: a canceled run is closed, and resuming means starting a new run (new Run ID) under the same Workflow ID. A completed run is closed; to "extend" it, start a new run with reuse policy AllowDuplicate | [T1], [T13] |
| 5 | Cooperative stop | **covered** | Activity cancel is delivered on the heartbeat response, and the activity chooses its safe point. The request is durable and recorded in history. If the activity is between attempts it is canceled immediately. If the worker is dead, the heartbeat timeout fires and retries are blocked ("Attempt failed after cancellation was requested (retries blocked)"). Workflow-level cancel is also durable. A stop does not carry into a later new run, which is the intended design | [T2], [T13] |
| 6 | Per-step values | **absent** (out of scope; limits conflict) | Heartbeat details and search attributes hold the latest value only. Queries need a worker and leave no record. A series sent as signals or updates hits the 10k-signal / 51,200-event / 50 MB limits | [T3], [T4], [T5], [T8] |
| 7 | Memoisation / produce-on-miss | **partial** | Start with ID=rid and conflict policy UseExisting, which attaches to a live producer. Reuse policy RejectDuplicate or AllowDuplicateFailedOnly means "don't recompute a done run": fetch its result instead. **Only within retention**, and whole-run only: there is no "values up to step N" read | [T1], [T11] |
| 8 | Demand / subscriptions | **absent** | Pull-only queries; signals could carry a schedule, but nothing interprets one | [T8] |
| 9 | Derived runs / reuse graph | **partial** | Child workflows give a call tree. A content-addressed ID is user-chosen. There is no dependency index | [T1] |
| 10 | Retention / GC | **covered** (better than runstate) | Namespace Retention Period (minimum 1 day; CLI default 3 days; no maximum since 1.18), timer-driven deletion, `temporal workflow delete`, and experimental Archival to blob storage | [T11], [T16] |
| 11 | Time | **covered** via the server | Every history event carries a server-assigned time, and one clock arbitrates everything | [T3], [T4] |
| 12 | Write authority / provenance | **covered** for Temporal state | Task tokens fence stale attempts. `identity` and `last_worker_identity` are recorded. Namespace authorization is enforced | [T3], [T15] |
| 13 | Deployment shape | **conflicts** | A four-service server plus a database, or Temporal Cloud. SQLite is dev-only | [T10], [T11] |
| 14 | Constraints on worker code | **partial** | Workflow code must be deterministic and versioned. Activity code is free, but it must run inside an SDK worker polling a task queue, or report against the activity from outside: the `RecordActivityTaskHeartbeatById` RPC exists `[name confirmed; semantics unverified]`. It must call `heartbeat()` from the training loop to be cancellable | [T2], [T6], [T18] |
| 15 | Interop | **partial** | The gRPC/protobuf API is published (`temporalio/api`), with many SDKs. The storage format is internal protobuf blobs, with no record format a reader can use without the server | [T3], [T9] |

**Strongest case that Temporal (plus a thin layer) subsumes runstate for these consumers.**
- **The identity is the same.** Workflow ID = content-addressed rid, and the GPU job = one activity (or a
  Standalone Activity, whose Activity ID *is* the rid), with retry-forever. Then:
  - Preemption → heartbeat timeout → retry → the next attempt reads `heartbeat_details` (`{step,
    ckpt_path}`) and resumes. That is "attempt 4 resumes from step 400, same identity".
  - `control.stop` → activity cancel, delivered at the next heartbeat. It is durable, it blocks retries,
    and the server holds the verdict.
  - Single-spawn is enforced, not just recorded, and stale attempts are fenced. That is something runstate
    says it structurally cannot offer (`positioning.md`: "fencing tokens are not available").
  - Liveness, terminal verdict, retention and a viewer (Temporal UI/CLI) come with it. The cockpit's
    "attach to a run you didn't start" is `temporal workflow describe`.
- For **translation**, gating on completion is `handle.result()`.
- The thin layer: a small shim that runs mycooc's own spawned training process under an activity and
  heartbeats on its behalf, plus a metrics store for the values.

**Strongest case that it doesn't.**
- **The per-step value plane has no home.** mycooc's main value, reuse of finished runs (`ensure`: read the
  loss up to step N), reads a *series*, and Temporal neither holds one nor lets one fit in history.
- **The record leaves the run.** It lives in a server database as opaque protobuf blobs and is **deleted at
  retention** unless retention is set to forever. That is the opposite of the "file beside the run, readable
  in ten years with `sqlite3`" bet.
- **Attempts collapse.** Per-attempt history is deliberately not kept [T14], so the episode history that
  runstate treats as the core artifact does not exist.
- **Extending a completed run makes a new Run ID** with a fresh history. "Same identity" survives only as the
  Workflow ID string.
- **A service must be operated** (or paid for). The dev server's SQLite mode is unsupported for production.

**Adoption cost.**
- Operate a Temporal server and database, or pay for Cloud.
- Restructure mycooc's self-spawned workers as activities, or build a heartbeat-by-ID shim. Add `heartbeat()`
  calls to the training loop.
- Pick a second store for per-step values, which splits the run's record in two.
- Set namespace retention to effectively infinite if "don't recompute a done run" is to survive.
- *Assessment:* the rewrite touches every consumer's launch path, and it leaves `ensure`'s partial-series
  reads to be rebuilt elsewhere.

---

## 2. DBOS Transact (Python; SQLite or Postgres system database)

**Summary.** DBOS is an MIT-licensed **library**, not a server: "no separate orchestration server and no
infrastructure required besides Postgres" [D8]. SQLite is the default system database [D10].

- **What it checkpoints.** Workflow inputs, step outputs, messages, events and streams go into documented
  tables: `workflow_status`, `operation_outputs`, `streams`, `workflow_events`, `notifications` and others
  [D1].
- **External access.** `DBOSClient` is a separate process that "connects directly to the DBOS system
  database — there is no intermediary server". It can list workflows, list steps, read events and streams,
  cancel, resume, fork, rewind and delete [D5].
- **Other languages.** SDKs in Python, TypeScript, Go and Java (and .NET) share the schema. A
  `portable_json` serialization "can even be read and written from the database without any DBOS code at
  all" [D9].
- **Of the whole family, this has the deployment shape runstate bets on:** a file or a database beside the
  application, with readers needing only database access.
- **Where it differs.** The core record is a **mutable status row**, not an append-only log. Liveness is
  **absent** from the open-source library: cross-process recovery is either "same executor ID restarts", an
  admin-API call, or the paid **Conductor** [D7], [D11], [D12]. A cancel is written as a **verdict**
  (`status=CANCELLED`) and takes effect at the next step boundary.

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** | Workflow ID = identity. Recovery "resumes the workflow from the last completed step". A `recovery_attempts` counter is kept. **But** the status row is overwritten in place: no per-attempt record survives except the counter and `executor_id`. `rewind_workflow` *drops* step history from step k onward. Stream rows do survive a rewind | [D2], [D1], [D13] `_sys_db.py` L1520-1545 |
| 2 | Single-spawn | **covered** (stronger than runstate) | `workflow_uuid` is the primary key, and a start with an existing ID returns the existing handle. Recovery re-enqueues so that "the queue's atomic ENQUEUED->PENDING dequeue … admits exactly one runner". A per-execution **ownership token `owner_xid`** is checked under a row lock on every step write, so a stale executor's writes raise `DBOSWorkflowConflictIDError`. That is real fencing over time, in the same SQLite/Postgres deployment shape as runstate. SQLite is single-host only | [D2], [D13] `_recovery.py`; `_sys_db.py` L2683-2709 |
| 3 | Liveness / failure detection | **absent** (open library); partial with paid Conductor | There is no heartbeat. A `PENDING` row looks the same whether its executor is alive or dead, and an in-flight step is invisible until it completes. Recovery assumes a restarted executor's own `PENDING` work is dead (by executor ID), or is triggered through the admin API. Conductor marks executors DISCONNECTED and then DEAD after a grace period, over a WebSocket, out of band; that verdict is not written to the database | [D7], [D11], [D13] |
| 4 | Terminal verdict | **partial** | Statuses are `PENDING`, `ENQUEUED`, `DELAYED`, `SUCCESS`, `ERROR`, `CANCELLED` and `MAX_RECOVERY_ATTEMPTS_EXCEEDED`. **CANCELLED is resumable**: `resume_workflow` continues from the last completed step, which is a genuine "resumable-interrupted" state. **Extending a completed run is not native.** A new start with the same ID returns the old workflow, and **the new inputs are not compared, so a raised target is silently ignored**. The alternatives are `rewind_workflow` (destructive) or `fork_workflow` (a new ID, with the original inputs) | [D4], [D5], [D13] `_sys_db.py` L1060-1086; `_dbos.py` `fork_workflow` |
| 5 | Cooperative stop | **partial** | `cancel_workflow` immediately sets `status=CANCELLED`, clears `owner_xid` and dequeues. The workflow is "interrupt[ed] at the beginning of its next step". The in-flight step's output write is then refused by the ownership check, so the chunk is lost to the database (the worker's own on-disk checkpoint survives). It is durable while the worker is down, because recovery only picks up `PENDING` work, and it persists until someone calls `resume_workflow`. **It is written as a verdict, not a request**, and nothing inside a long step learns of it unless user code polls `get_workflow_status` | [D4], [D13] `_sys_db.py` L1188-1230 |
| 6 | Per-step values | **covered** (with caveats) | **Streams.** `write_stream` and `close_stream` are "immutable and append-only", exactly-once from the workflow and at-least-once from a step. `read_stream(workflow_id, key, offset=)` is available to any client, and the rows are plain SQL (`dbos.streams`). Events give latest-value key/value pairs plus `workflow_events_history`. Caveats: the default serializer is **`py_pickle`**, with `portable_json` opt-in; stream rows carry **no timestamp**; step retries can duplicate points | [D3], [D1], [D9], [D13] `_serialization.py` L95-111 |
| 7 | Memoisation / produce-on-miss | **partial** | The workflow ID is an idempotency key: starting with ID=rid either starts the run or returns the existing handle, and `get_result()` on a SUCCESS run returns the stored output. **That record is permanent**, with no retention unless deleted. A partial read ("loss to step N") is a stream read. **Extending is not supported** (row 4) | [D2], [D5] |
| 8 | Demand / subscriptions | **absent** | `send`/`recv` durable messages could carry a schedule. There is no condition algebra | [D3] |
| 9 | Derived runs / reuse graph | **partial** | Child workflows with `parent_workflow_id`, and `forked_from`/`was_forked_from` columns. There is no read-set index | [D1] |
| 10 | Retention / GC | **partial** | `delete_workflow(s)` in the library, and `retention_timestamp` columns. Retention *policies* are a Conductor (paid) feature | [D5], [D8], [D12] |
| 11 | Time | **partial** | Workflow rows carry `created_at`, `updated_at` and `completed_at`; step rows carry `started_at_epoch_ms` and `completed_at_epoch_ms`, taken from the database clock. **Stream and event rows have no time column**, so a cold reader cannot date a value point | [D1], [D13] |
| 12 | Write authority / provenance | **partial** | The `owner_xid` fencing is enforced. `authenticated_user`, `assumed_role` and `executor_id` are recorded. Database roles apply | [D1], [D13] |
| 13 | Deployment shape | **covered** (closest to runstate's bet) | A library plus a SQLite file (single host) or Postgres (multi-host). No execution server. Conductor is optional. "Because a SQLite database is just a file on disk, it can't be used in a distributed setting" | [D8], [D10] |
| 14 | Constraints on worker code | **partial** | The workflow function must be deterministic. The process must configure and launch DBOS, and the training job becomes one or more steps. Recovery requires a stable executor ID per host (`DBOS__VMID`) | [D2], [D7] |
| 15 | Interop | **partial** (best in the family) | The schema is documented on a "System Tables" page and shared by the multi-language SDKs; `portable_json` and SQL entry points such as `dbos.enqueue_workflow` exist. **Stability is not promised** on the page, and the Postgres migration list runs to migration 123 at `03fb5c9`. Values are pickled by default | [D1], [D9], [D13] `_migration.py` |

**Strongest case that DBOS (plus a thin layer) subsumes runstate for these consumers.**
- **It has the same bet:** a library, a SQLite file or Postgres, and readers that talk to the database
  directly with no service. So the cockpit's third-party attach is `DBOSClient.list_workflows` /
  `list_workflow_steps` / `read_stream`.
- **The mapping for mycooc is direct:**
  - one workflow per run (ID = rid), whose body loops over training chunks as steps, each "train from my
    checkpoint to step k+N";
  - per-step metrics written to a stream, with `close_stream` as "nothing more will come";
  - `control.stop` = `cancel_workflow`, which takes effect at the next chunk boundary;
  - resume after a stop or preemption = `resume_workflow` or recovery;
  - single-spawn = the primary key plus the `owner_xid` fencing.
- **For translation:** run a workflow per job, read progress with `read_stream`, and gate on completion with
  `get_result()`.
- **What it gives beyond runstate:** enforced fencing, a resumable CANCELLED status, durable messages, queues
  with concurrency limits, multi-language SDKs, and a migration framework.
- The thin layer: a mycooc-side wrapper that maps "extend to a new target" onto `fork_workflow` or onto an ID
  scheme, plus a timestamped heartbeat stream.

**Strongest case that it doesn't.**
- **No liveness in the open library.** A third party cannot tell a dead `PENDING` run from a live one. mycooc
  works across hosts and has a reclaim tool for crashed claims on other hosts; under DBOS that becomes "know
  the dead executor's ID and call the admin API", or pay for Conductor. The tiered detector runstate ships has
  no counterpart.
- **Stop is preemptive and written as a verdict.**
  - It lands only at a step boundary, and the in-flight chunk's step output is discarded.
  - The status row says CANCELLED *before* the worker has stopped. That is the
    `stop`-request-mistaken-for-`stopped`-report confusion `positioning.md` warns about, built into the
    schema.
- **Extending is a trap.** The silent-ignore of new inputs on a same-ID start reproduces exactly the
  "`ensure` that truncated and looked like a legitimate early stop" harm that `CLAUDE.md` names as invisible
  in logs.
- **Mutable rows, not a log.** Rewind deletes history and status is overwritten, so "the whole history is one
  re-readable artifact" does not hold.
- **The schema is not a published contract,** and values are pickled unless the user opts in.
- **SQLite cannot go cross-host;** mycooc's multi-host operation would need Postgres.

**Adoption cost.**
- Make each training entry point a DBOS app, and restructure the loop into deterministic chunks of steps.
- Choose Postgres for multi-host.
- Keep a separate liveness mechanism and the reclaim tool, or buy Conductor.
- Design run extension, and accept that it means a new ID or a destructive rewind.
- Switch values to `portable_json`.
- *Assessment:* this is the lowest-cost member of the family to adopt. The two concerns it leaves (liveness and
  extension) are the ones mycooc's own machinery already exists to handle.

---

## 3. Azure Durable Functions / Durable Task Framework (DTFx)

**Summary.** DTFx is Microsoft's event-sourced orchestration framework. Durable Functions is its Azure
Functions extension, and the newer portable "Durable Task SDKs" (.NET, Python, Java, JS) run against the
managed **Durable Task Scheduler** [A3], [A6].

- **Identity and history.** An **instance ID** (user-chosen, at most 100 characters, "unique within a task
  hub") names an orchestration. History is append-only: "the Durable Task Framework uses an append-only store
  to record the full series of actions" [A3].
- **Backends:**
  - the Durable Task Scheduler (Azure-managed and recommended; there is a local emulator);
  - Azure Storage tables and queues (the Azurite emulator works locally);
  - MSSQL (self-hostable, with `dt.vInstances` views for ad-hoc SQL);
  - Netherite (support ends 2028-03-31) [A6], [A10].
- **The fit for a long GPU job is poor:**
  - activities have **no heartbeat and no cancellation channel** [A9];
  - terminate "doesn't currently propagate. Activity functions … run to completion" [A4];
  - the documented singleton pattern carries an explicit race warning [A1].

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** | An instance ID with append-only history; ContinueAsNew restarts the history (eternal orchestrations). Activity retries are driven by the orchestrator's retry policy, so attempts appear in history (`[unverified]` row-level detail). Nothing carries a checkpoint into the next attempt | [A3], [A4] |
| 2 | Single-spawn | **partial** | The instance ID is unique per task hub. Partition leases (blob leases or SQL locks) give one orchestrator executor at a time. **The singleton start is check-then-start**, and the docs warn that "both calls might report success". Starting over an existing non-terminal instance may *terminate* it, depending on dedupe statuses `[secondary: release notes]`. Activities are at-least-once, with no fencing of a zombie activity | [A1], [A8], [A11] |
| 3 | Liveness / failure detection | **partial** (backend-internal) | There is no heartbeat API. A crash is detected by the work item's lock or visibility expiry: MSSQL `taskEventLockTimeout`, default 2 min, renewed during execution. That is visible in SQL (`dt.NewTasks`) but is not part of the status API. The status API reports only `Running` | [A9], [A10], [A4] |
| 4 | Terminal verdict | **partial** | Runtime statuses are Pending, Running, Completed, ContinuedAsNew, Failed, Terminated and Suspended. **Suspended is resumable.** `rewind` applies to Failed only; `restart` creates a fresh instance. A Completed instance can be overwritten by a new start with the same ID | [A4], [A1] |
| 5 | Cooperative stop | **partial** (orchestrator) / **absent** (activity) | Raise an external event. If the orchestrator isn't waiting, the event is buffered, but it is discarded if the instance doesn't exist. Delivery is at-least-once. Terminate and suspend are queued operations. **Nothing reaches a running activity** | [A5], [A4] |
| 6 | Per-step values | **partial** | `CustomStatus` holds the latest JSON value and is set by the orchestrator, not the activity. Durable entities could hold a series (*assessment*, not a documented pattern) | [A4] |
| 7 | Memoisation / produce-on-miss | **partial** | Reuse by ID is user code (query status, then start), with the race above. Nothing returns the existing result | [A1] |
| 8 | Demand / subscriptions | **absent** | External events only | [A5] |
| 9 | Derived runs | **partial** | Sub-orchestrations | [A3] |
| 10 | Retention / GC | **covered** | Purge-instance APIs; the Durable Task Scheduler has autopurge retention policies | [A4], [A8] |
| 11 | Time | **covered** via the backend | History rows carry timestamps; `CurrentUtcDateTime` is replay-safe | [A3], [A2] |
| 12 | Write authority / provenance | **partial** | Storage RBAC, and in MSSQL a stored-procedure-only runtime role with per-tenant isolation. No activity fencing | [A10] |
| 13 | Deployment shape | **conflicts** | A Functions host or DTFx app, plus a managed scheduler, a storage account or SQL Server. There is no supported embedded-file backend for production | [A6] |
| 14 | Constraints on worker code | **partial** | Orchestrators must be deterministic, and Python orchestrators must be generators. Activities are unconstrained but blind: no heartbeat, no cancellation, and bound by the host timeout | [A2], [A7], [A9] |
| 15 | Interop | **partial** | The portable SDKs share a gRPC protocol. The Azure Storage History table "format and content might change". MSSQL offers views and stored procedures | [A3], [A10] |

**Strongest case for.**
- An append-only, event-sourced history per instance (closer to runstate's log than DBOS's mutable rows).
- A **Suspended** (resumable) state.
- A self-hostable SQL Server backend whose rows a third party can query.
- Eternal orchestrations for runs that "never end".

**Strongest case against.**
- The long job, the activity, gets no heartbeat, no checkpoint hand-off and no cancellation, so cooperative
  stop, liveness and resume-from-checkpoint all fall back to user code.
- The singleton start is racy by the docs' own account.
- The product is Azure-centred, and the recommended backend is a managed service.
- *Assessment:* weakest fit of the three.

**Adoption cost.**
- A .NET/Azure-oriented runtime or the Durable Task Scheduler, or SQL Server.
- Rebuild heartbeat, stop and checkpoint hand-off on the side.
- Not recommended for these consumers.

---

## 4. Briefly: Inngest, Hatchet, Restate workflows

- **Inngest.** An event-driven step-function platform. A single self-hostable binary bundles the event API,
  queue, executor and state store, with SQLite by default and Postgres and Redis for production [I1].
  - Steps run "in a separate request to your app", with prior step results re-sent as memoized JSON [I3].
    *Assessment:* that suits short steps, not a multi-hour GPU loop; Inngest points to "Connect" for
    worker-style deployments.
  - Idempotency is a **24-hour** window on an event ID or a CEL key [I2], so there is no permanent
    content-addressed reuse.
  - Not close. No table.
- **Hatchet.** A Postgres-backed task and workflow engine. Self-hosting needs an API server, an engine (gRPC)
  and Postgres, with RabbitMQ optional; "Hatchet Lite" bundles them for development and low-throughput use
  [H2].
  - The execution timeout (default 60 s) can be extended additively from inside the task with
    `refreshTimeout`, which is effectively a lease-style heartbeat.
  - "A timed out task does not guarantee that the task will be stopped immediately"; cancellation is a flag
    the task checks [H1].
  - Semantically it is near Temporal's activity on liveness. It needs a service, and the run's record lives
    in Hatchet's schema. Not close enough to tabulate.
- **Restate workflows.** The `run` handler "executes exactly once per workflow ID" [R2], and resubmission
  "fail[s] with 'Previously accepted'". State is "queryable from other handlers or the Restate UI", and
  durable promises signal a running workflow [R1].
  - State is retained only for "the duration of the workflow retention (default one day)", configurable
    [R1].
  - A Restate server is required.
  - It is close to DBOS's workflow-ID-as-idempotency-key. Retention and the server conflict with reusing
    finished runs by ID and with the no-service bet. Its virtual-object side is surveyed separately.

---

## 5. Across the family

### Which system comes closest

**On deployment shape and data model, DBOS Transact.**
- It is the only member that shares runstate's bet: a library, a SQLite file or Postgres, and third-party
  readers needing only database access, with a documented schema.
- It covers single-spawn (more strongly, with fencing), per-step values (streams), whole-run memoisation by
  ID, durable step-granular cancel with a resumable CANCELLED state, and deployment.
- It leaves uncovered:
  - liveness (no heartbeat in the open library);
  - safe-point cooperative stop inside a long step, with stop recorded as a request rather than a verdict;
  - extending a completed run under the same identity (new inputs are silently ignored);
  - per-attempt (episode) history, since the status row is mutable and rewind deletes;
  - dating value points (no timestamp on stream rows);
  - demand and subscriptions;
  - a published stable record format (pickle by default; the schema churns).

**On run semantics, Temporal.**
- It is the closest to "attempt 4 resumes from step 400 and is the same run": the activity heartbeat
  timeout, heartbeat-details checkpoint, cancel-on-heartbeat and fenced attempts, and now Standalone
  Activities, with an Activity ID as the run's name and dedupe policies.
- It conflicts with the no-service bet, has nowhere for the per-step series, deletes history at retention,
  and deliberately collapses attempts.

### The claim under test

`positioning.md` says: *"What does not exist elsewhere is the identity: attempt 4 resumes from step 400, is the
same run as attempts 1–3, and the whole history is one re-readable artifact."*

- **The first two clauses are falsified.**
  - Temporal's Workflow/Activity ID plus retry-with-heartbeat-details is that identity, by design [T1], [T2].
  - DBOS's workflow ID plus "resume from the last completed step" is it at step granularity [D2].
  - DTFx's instance ID is it at orchestration granularity.
- **The third clause survives.** No member of this family keeps the *episode history* as a re-readable
  artifact:
  - Temporal omits retried attempts from history [T14] and deletes closed histories at retention [T11];
  - DBOS overwrites the status row and rewind deletes steps [D13];
  - DTFx's history format "might change" [A3];
  - and none puts the run's per-step value series in the same record as its lifecycle.
- *Assessment:* the defensible claim is narrower. It is not "durable identity" but **"durable identity whose
  full episode-by-episode history, lifecycle and per-step values form one append-only record, kept
  indefinitely and readable without a service."**
- `positioning.md`'s comparison table should gain a durable-execution row, naming Temporal and DBOS.

### runstate concerns no system in this family covers

1. **Episode history as first-class records under one identity**, retained indefinitely and readable without
   a server (C1/C3).
2. **Cold third-party liveness from the record alone, with no service.** Temporal has liveness only through
   its server, DBOS has none, and DTFx has it only as backend-internal lock expiry (C4).
3. **Extending a *completed* run under the same identity.** Everywhere, "completed" is closed: you start a new
   run, rewind (destructively) or restart (C6/C12).
4. **Stop as a durable *request*, distinct from the *report* that the run stopped.** Temporal comes closest:
   cancel-requested is a distinct state until the activity acknowledges. DBOS writes the verdict at once, and
   DTFx cannot reach the activity (C7).
5. **Reader-driven demand with a condition algebra** (`control.subscribe`). Every member is buildable from
   messages, but none has it, and no runstate consumer uses it either (C8).
6. **A reuse graph for derived runs** keyed by read-set identities. Child/parent links exist; read-set
   indexes do not (C13).
7. **A stable, published, language-neutral record format.** DBOS comes closest (a documented schema plus
   opt-in `portable_json`) but promises no stability (C17).

### What the family does better than runstate (and should inform the redesign)

- **Fencing over time.**
  - Temporal rejects stale attempts' heartbeats and completions with `NOT_FOUND` [T15].
  - DBOS checks an `owner_xid` token under a row lock on every step write [D13].
  - `positioning.md` says "fencing tokens are not available … structural, because acquiring the claim is
    itself an append". DBOS shows that the *same deployment shape* (SQLite or Postgres) can enforce fencing,
    by making ownership a mutable cell rather than an appended record.
  - *Assessment:* that is a design choice runstate made, not a constraint of a file-beside-the-run
    deployment.
- **A resumable-stopped verdict.** DBOS `CANCELLED` + `resume_workflow` and DTFx `Suspended` + resume.
- **Retention policy** (Temporal namespaces, the DTS autopurge), **server-enforced heartbeat timeouts**, and
  **viewers that already exist** (Temporal UI, DTS dashboard, Conductor).
- **Multi-language SDKs** sharing one schema or protocol (DBOS, DTFx, Temporal).

### Optional notes for `if-built-today/`

- **Told negative facts.** DBOS `close_stream(key)` is a told "nothing more will come" on a series, and
  `read_stream` terminates on it or on workflow end [D3]. Temporal's closed-execution events and Restate's
  rejected durable promise are told terminal facts.
- **Not seen in any member:** questions with explicit quantifiers, demand propagated by rules, or
  four-valued status with visible conflict. (I did not search hard, as instructed.)

---

## Sources (all fetched 2026-10-02 unless dated)

**Temporal / Cadence**
- [T1] https://docs.temporal.io/workflow-execution/workflowid-runid
- [T2] https://docs.temporal.io/encyclopedia/detecting-activity-failures
- [T3] https://github.com/temporalio/api/blob/master/temporal/api/workflow/v1/message.proto (`PendingActivityInfo`, `WorkflowExecutionInfo`)
- [T4] https://docs.temporal.io/workflow-execution/event
- [T5] https://docs.temporal.io/self-hosted-guide/defaults
- [T6] https://docs.temporal.io/workflow-definition
- [T7] https://docs.temporal.io/standalone-activity
- [T7b] https://temporal.io/blog/standalone-activities-durable-job-processing-now-in-public-preview (2026-09-15)
- [T8] https://docs.temporal.io/encyclopedia/workflow-message-passing
- [T9] https://github.com/temporalio/temporal/blob/main/schema/sqlite/v3/temporal/schema.sql (`history_node.data` blob)
- [T10] https://docs.temporal.io/temporal-service/persistence
- [T11] https://docs.temporal.io/temporal-service/temporal-server (Retention Period)
- [T12] https://docs.temporal.io/workflow-execution/continue-as-new
- [T13] https://dotnet.temporal.io/api/Temporalio.Api.Enums.V1.ActivityExecutionStatus.html
- [T14] https://docs.temporal.io/retry-policies (ActivityTaskStarted written only at close); corroborated by
  https://community.temporal.io/t/when-does-temporal-write-the-activitytaskstarted-event-into-workflow-history/6162 (2022-10-09)
- [T15] https://docs.temporal.io/troubleshooting/request-failures (`NOT_FOUND` after timeout)
- [T16] https://docs.temporal.io/temporal-service/archival ("experimental")
- [T17] https://preview.temporal.io/about; https://softwareengineeringdaily.com/2020/04/08/cadence-ubers-workflow-engine-with-maxim-fateev/ (lineage, via search summary) `[secondary]`
- [T18] https://nodejs.temporal.io/api/interfaces/proto.temporal.api.workflowservice.v1.IRecordActivityTaskHeartbeatByIdResponse (RPC name only)

**DBOS**
- [D1] https://docs.dbos.dev/explanations/system-tables
- [D2] https://docs.dbos.dev/python/tutorials/workflow-tutorial
- [D3] https://docs.dbos.dev/python/tutorials/workflow-communication
- [D4] https://docs.dbos.dev/python/tutorials/workflow-management
- [D5] https://docs.dbos.dev/python/reference/client
- [D6] https://docs.dbos.dev/python/tutorials/step-tutorial
- [D7] https://docs.dbos.dev/production/workflow-recovery and https://docs.dbos.dev/production/self-hosting/workflow-recovery (search excerpt; direct fetch 404)
- [D8] https://docs.dbos.dev/architecture
- [D9] https://docs.dbos.dev/explanations/portable-workflows
- [D10] https://docs.dbos.dev/python/tutorials/database-connection
- [D11] https://dbos.dev/blog/introducing-dbos-conductor (2025-03-27)
- [D12] https://www.dbos.dev/pricing
- [D13] https://github.com/dbos-inc/dbos-transact-py at `03fb5c9` (2026-10-01), MIT:
  - `dbos/_sys_db.py` (`_check_owner_txn` L2683-2709; `cancel_workflows` L1188-1230; `rewind_workflow`
    L1520-1545; `reenqueue_for_recovery` L4631-4665; same-ID start checks L1060-1086);
  - `dbos/_recovery.py`;
  - `dbos/_serialization.py` (`DefaultSerializer.name() == "py_pickle"`);
  - `dbos/_migration.py`.

**Azure Durable Functions / DTFx** (Microsoft Learn pages show `ms.date`)
- [A1] https://learn.microsoft.com/en-us/azure/durable-task/common/durable-task-singletons (2026-04-23)
- [A2] https://learn.microsoft.com/en-us/azure/durable-task/common/durable-task-code-constraints (2026-08-24)
- [A3] https://learn.microsoft.com/en-us/azure/durable-task/common/durable-task-orchestrations (2026-04-22)
- [A4] https://learn.microsoft.com/en-us/azure/durable-task/common/durable-task-instance-management (2026-04-22)
- [A5] https://learn.microsoft.com/en-us/azure/durable-task/common/durable-task-external-events (2026-05-04)
- [A6] https://learn.microsoft.com/en-us/azure/durable-task/common/durable-task-storage-providers (2026-04-22)
- [A7] https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale (2026-09-16)
- [A8] https://learn.microsoft.com/en-us/azure/durable-task/durable-functions/durable-functions-azure-storage-provider (2026-09-22)
- [A9] https://github.com/microsoft/durabletask-python at `866cc78` (2026-10-01): `durabletask/task.py`
  `ActivityContext` (L877-900) exposes only `orchestration_id`, `task_id`; there is no "heartbeat" in
  `durabletask/*.py`
- [A10] https://github.com/microsoft/durabletask-mssql: `docs/quickstart.md` (`taskEventLockTimeout`),
  `docs/architecture.md` (`dt.vInstances`), `src/.../Scripts/permissions.sql` (`_RenewTaskLocks`)
- [A11] https://newreleases.io/project/github/Azure/azure-functions-durable-extension/release/v1.16.0-worker-extension (dedupe statuses) `[secondary]`

**Others**
- [I1] https://www.inngest.com/docs/self-hosting
- [I2] https://www.inngest.com/docs/guides/handling-idempotency
- [I3] https://www.inngest.com/docs/learn/how-functions-are-executed
- [H1] https://docs.hatchet.run/home/timeouts
- [H2] https://docs.hatchet.run/self-hosting
- [R1] https://docs.restate.dev/tour/workflows
- [R2] https://docs.restate.dev/foundations/services
