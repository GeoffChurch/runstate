# Prior-art survey: data/asset orchestrators and content-addressed build caches

Family surveyed for `GeoffChurch/runstate`'s positioning claim. This family is the one nearest
runstate's memoisation and demand half: `ensure`, content-addressed run ids, and derived runs.
Compiled 2026-10-02. Follows `prior-art-rubric.md`.

**Versions checked (latest release on 2026-10-02):**

| system | version | released |
|---|---|---|
| Dagster | 1.13.25 | 2026-10-01 |
| Flyte | 1.16.9 | 2026-09-24 |
| flytekit | 1.16.29 | 2026-10-02 |
| flyte-sdk (Flyte 2) | 2.10.7 | 2026-10-02 |
| Metaflow | 2.19.39 | 2026-09-02 |
| metaflow-checkpoint | 0.2.13 | 2026-08-31 |
| Prefect | 3.8.7 | 2026-09-26 |
| DVC | 3.67.1 | — |
| DVCLive | 3.49.1 | — |
| Airflow | 3.3.2 | — |
| Nix manual | 2.34.9 | — |
| Bazel Remote Execution API (REAPI) | v2.12.0 | — |

**Depth.** Dagster, Flyte and Metaflow are surveyed in depth. Prefect, Nix, Bazel, DVC and Airflow
are brief. Prefect turned out closer than expected on the control plane, so it gets a little more
room than the brief was given.

**Source keys.** `[D3]`, `[F6]` and the like are keys into §10.

**Ratings are relative to runstate's mechanism and to what the two consumers use:**
- **covered**: the system provides it.
- **partial**: some of it, or a bounded form.
- **absent**: not provided.
- **conflicts**: the system's model works against it.

**Flags:**
- `[unverified]`: could not be confirmed from a primary source.
- `[inference]`: my reasoning from sources, not a documented statement.

---

## 0. Bottom line

1. **The bounded form of runstate's identity claim exists in all three in-depth systems.** The claim
   is "attempt 4 resumes from step 400, is the same run as attempts 1–3, and the history is one
   artifact." Each system matches it within one submission:
   - **Flyte**: retries of one task execution, plus intra-task checkpoints. "Each retry attempt sees
     the checkpoint saved by the attempt before it" `[F6]`.
   - **Metaflow**: `@retry` plus `@checkpoint`. Its `load_policy='eager'` even crosses runs `[M3]`.
   - **Dagster**: run monitoring resumes a crashed run *under the same run id*, and the crash is
     recorded in the same event log `[D1][D2]`.

   So "does not exist elsewhere" is **false as worded**. What survives is the **unbounded** form, and
   §8.3 states it precisely.
2. **Every system in the family treats the unit of work as atomic.** A materialisation, a cached task
   output, a derivation or an action result either exists or it doesn't. None can serve "loss of
   config C up to step N" from a partly done run, and none can extend a *completed* run under the
   same identity. They emulate extension in one way only: **chunking** the work into separately
   cached units (§8.2).
3. **Closest system overall: Dagster**, for these two consumers.
   - For **translation** (batch shards, an off-channel content-addressed store, consumers that gate
     on completion), Dagster is close to a drop-in.
   - For **mycooc** it covers reuse, derived runs and observation, but conflicts on cooperative
     stop, on step-level extension, and on the bet of not running a service.
   - **Flyte** is closest on two mechanisms: single-spawn by a heartbeat lease, and resume across
     attempts.
   - **Metaflow** is the only one whose default mode needs no service.
4. **No system in this family covers these runstate concerns:**
   - a **durable, safe-point cooperative stop** that survives worker downtime and is discharged per
     episode;
   - a **step-indexed value series as a first-class live read**;
   - **suffix-only production by extending the same identity**;
   - **cold third-party liveness from files alone**;
   - **subscriptions**;
   - a **claim that time never arbitrates**.

   The last is a double-edged difference. Flyte's lease expiry is exactly what would have prevented
   mycooc's cross-host wedge (`docs/backlog/cross-host-claim-gate.md` §8).
5. **A null result worth recording: DVC built step-indexed, resumable "checkpoint" experiments and
   removed them in 3.0** (release 2023-06-13, `#9271`). The maintainers' reasons map almost exactly
   onto two of runstate's own rules (§7):
   - the step-target ambiguity, which runstate handles by excluding the target from the run id;
   - reproducing a resumed run, which runstate handles with episodes.

---

## 1. Dagster

**Summary.** Dagster's identity is the **asset**, optionally split into **partitions**.
- A **run** executes ops or assets and writes an append-only, timestamped **event log**. The default
  SQLite storage keeps *one SQLite file per run* under `$DAGSTER_HOME/history/runs/` `[D13]`.
- A **materialisation** records an asset or partition as produced, with a code version, a data
  version and provenance `[D6]`.
- The **dagster-daemon** does the automatic work:
  - it evaluates *declarative automation*, a condition algebra over asset status, to launch what is
    missing or stale `[D9]`;
  - it runs sensors and the run queue;
  - it does *run monitoring*, which probes run-worker health through the run launcher and can resume
    a crashed run with a new worker `[D1][D2]`.
- External processes report back through **Dagster Pipes**, a small JSON protocol with a published
  JSON Schema and TypeScript, Rust and Java implementations. Pipes is one-way after launch `[D11]`.
- Dagster has no step-indexed resumable unit. A materialisation is atomic, and resuming inside an op
  is the user's own checkpoint code.

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** | Three candidates; none is the whole thing (details below the table). | [D1][D2][D4] |
| 2 | Single-spawn | **partial** | No store-level claim per asset or partition. Automation skips an `in_progress` partition. A queue tag limit serialises runs per tag value. Both rest on one daemon (details below). | [D9][D10] |
| 3 | Liveness / failure detection | **partial** | The daemon probes infrastructure status through the launcher; there is no worker heartbeat. The local launcher has no probe (details below). | [D1][D2][D3] |
| 4 | Terminal verdict | **covered** (different shape) | `DagsterRunStatus`: QUEUED, NOT_STARTED, MANAGED, STARTING, STARTED, SUCCESS, FAILURE, CANCELING, CANCELED. Partition status: materialised, failed, missing or in progress. There is no "preempted, resumable" verdict: a crash is either resumed or becomes FAILURE. SUCCESS is final, so extending means a new run. | [D4][D8] |
| 5 | Cooperative stop | **conflicts** | Terminate becomes an interrupt raised wherever the code happens to be, not a request read at a safe point. The request belongs to *that run*: it cannot be sent while the run is down, and it does not reach a later run. | [D12] |
| 6 | Per-step values | **partial** | Ops can log `AssetObservation` or `AssetMaterialization` with metadata mid-run, and Pipes can send `report_custom_message`. These land in the run's event log live, timestamped and queryable. There is no step key and no read for "the series up to step N". Making steps into partitions does not scale (below). | [D7][D8][D15] |
| 7 | Memoisation / produce-on-miss | **partial** | Partition status is a native "what exists" read. `AutomationCondition.on_missing()`, `missing()` and missing-partition backfills give produce-on-miss at partition grain. Versions flag staleness, but Dagster "does not automatically skip" (below). A rid used as a dynamic partition key gives content-addressed reuse. A partly done partition counts as missing or failed. | [D6][D9] |
| 8 | Demand / subscriptions | **partial** (a different kind) | Demand is on the launch side: a condition algebra over asset status, plus sensors (operators below). Nothing lets a reader ask a *running* worker to report something on a schedule. There are no leases. | [D9] |
| 9 | Derived runs and reuse graphs | **covered**, stronger than runstate | An asset graph with partition mappings. Each materialisation records the data versions of its inputs, staleness propagates downstream, and `eager()` re-materialises when an upstream changes. Identity is (asset, partition) plus recorded provenance, not a hash of the read set. | [D6][D9] |
| 10 | Retention / GC | **partial** | Runs and event logs are kept until deleted through the CLI or API. Dagster does not manage the I/O-manager storage. `[unverified]`: whether `dagster.yaml` `retention` covers anything beyond tick history. | [D13] |
| 11 | Time | **covered** | Every event-log entry is timestamped, and run start and end are recorded, so a cold reader can date records. | [D13] |
| 12 | Authority / provenance | **recorded**, same as runstate | In open-source Dagster, anyone with storage access can write events. Dagster+ adds role-based access control at the API. Provenance of *data* (input versions) is recorded per materialisation. | [D6] |
| 13 | Deployment | **heavy** | Services and storage (below). An in-process `materialize()` needs none of them. | [D1][D13] |
| 14 | Constraints on worker code | **conflicts** for mycooc | Work is a Python op or asset in a code location, or an external process that *Dagster launches* via Pipes. A self-spawned worker can only report "runless" events through `report_runless_asset_event`. SLURM goes through the community `dagster-slurm` package (Pipes, one job per asset). | [D11][D14][D16] |
| 15 | Interop | **partial** | Pipes has a JSON Schema and three non-Python implementations, but it is one-way and step-scoped (below). Event-log records use Dagster's internal serialisation. A GraphQL schema covers reading, launching and terminating. | [D11] |

**Detail for the rows above.**

- **Row 1, the three identity candidates:**
  - **Run resume.** The daemon "can launch a new run worker which resumes execution of the existing
    run". The `run_id` stays the same, and the crash shows in the same event log.
    - The attempt number is *derived by counting* the `"Launching a new run worker to resume run"`
      engine events in the run's own log (`count_resume_run_attempts`).
    - Only the K8s and Docker launchers support it.
    - Granularity is the op. Checkpointing inside an op is user code.
  - **Run groups.** Retries and re-executions get *new* run ids, linked by
    `parent_run_id`/`root_run_id`.
  - **Asset partition.** (asset_key, partition_key) persists across runs with its materialisation
    history. It is the nearest analogue of a runstate run, with Dagster runs playing the part of
    episodes.
- **Row 2, single-spawn mechanisms:**
  - Declarative automation "avoids launching duplicate runs by checking whether a run targeting a
    specific partition is already in-progress".
  - `QueuedRunCoordinator.tag_concurrency_limits` with `applyLimitPerUniqueValue` and `limit: 1`
    serialises runs per tag value when they are dequeued.
  - Both depend on a single daemon. Neither stops a launch that bypasses the queue `[inference]`.
- **Row 3, liveness:**
  - The daemon polls `run_launcher.check_run_worker_health` every `poll_interval_seconds` (120). It
    checks infrastructure status: the K8s Job, the Docker container or the ECS task.
  - It also enforces `start_timeout`, `cancel_timeout` and `max_runtime_seconds`.
  - The base launcher sets `supports_check_run_worker_health = False`, and `DefaultRunLauncher`
    does not override it. So a crashed *local* run worker stays `STARTED` until `max_runtime`, if
    that is set (from source).
  - Third parties read a *stored* status, written by the daemon or the worker.
- **Row 5, the stop path.** Terminate sets CANCELING, then the launcher terminates the worker. In the
  worker, SIGTERM is mapped to SIGINT and raised in op code as `DagsterExecutionInterruptedError`.
- **Row 6, steps as partitions.**
  - The guidance is at most 100k partitions per asset.
  - `MultiPartitionsDefinition` allows at most two dimensions (config × step).
  - A single run over a range records its partitions only at the end, and "a failure requires
    retrying all partitions together".
- **Row 7, versions.** `code_version` and `DataVersion` (a hash of the code version and the input
  data versions) mark an asset "Unsynced". Dagster's memoisation is that "the last-computed asset
  value is always cached".
- **Row 8, the condition algebra.**
  - Operands: `missing`, `in_progress`, `execution_failed`, `newly_updated`,
    `code_version_changed`, `cron_tick_passed`.
  - Operators: `& | ~`, `since`, `newly_true`, `any_deps_match`, `all_deps_match`,
    `any_downstream_conditions`.
- **Row 13, deployment.**
  - `dagster-webserver`.
  - `dagster-daemon`, required for automation, sensors, the run queue, run monitoring and run
    retries.
  - Code-location servers.
  - Instance storage: by default SQLite `runs.db` plus *a SQLite file per run*; Postgres or MySQL
    for more than one host.
- **Row 15, the Pipes schema.**
  - `PipesMessage` has `additionalProperties: false`, a version field, and the methods `opened`,
    `closed`, `log`, `report_asset_materialization`, `report_asset_check` and
    `report_custom_message`.
  - `PipesContextData` carries `run_id`, `retry_number`, the partition key or range, and the code
    version by asset.
  - It has TypeScript, Rust and Java implementations. Messages flow one way after launch, and they
    are scoped to one step.

**Strongest case that Dagster, plus a thin layer, subsumes runstate for these consumers.**

- **translation** is Dagster's home ground:
  - Each batch NLP shard is a partition of an asset.
  - The off-channel content-addressed artifact store becomes an I/O manager.
  - "Consumers gate on `ensure` reaching completion" becomes downstream assets under
    `on_missing()` or `eager()`, waiting for upstream partitions.
  - Progress goes out as Pipes logs or custom messages, and runstate's launchers become run
    launchers.
- **mycooc** could be modelled as follows:
  - A `trained_model` asset with a `DynamicPartitionsDefinition` keyed by the content-addressed rid,
    which already excludes the step target.
  - `code_version` carries the git fingerprint.
  - `on_missing()` launches.
  - Resume comes from the user's existing checkpoint, plus run retries. Run monitoring adds resume
    under the same id on K8s or Docker.
  - Loss is logged as `AssetObservation`s.
  - The cockpit becomes the Dagster UI plus a GraphQL client.
  - Derived analyses become downstream assets with *automatic* provenance and staleness, which is
    stronger than runstate's derived-run recipe.
  - The per-partition materialisation history plays the part of runstate's episode history.
- **The thin layer** would hold:
  - the rid → partition mapping;
  - a step-series reader over observations;
  - a cooperative stop flag the worker polls at safe points, for example a mutable run tag or an
    observation keyed by partition `[inference: not a documented pattern]`.

**Strongest case that it doesn't.**

- **The unit is the materialisation, and it is atomic.**
  - "Loss of C up to step N" from a run that reached step 400 of 1000 has no read.
  - A single run over a range credits nothing on failure.
  - Steps-as-partitions runs into the 100k-partition guidance and the two-dimension limit.
  - Extending after SUCCESS means a new run that overwrites the materialisation. The history
    survives only as events.
- **Stop conflicts.** It is an interrupt rather than a safe-point request. It cannot be posted while
  the run is down, and it never reaches the next attempt.
- **Resume under the same run id needs the K8s or Docker launchers.** mycooc is SLURM with
  SQLite on NFS. Through `dagster-slurm`, a crash becomes FAILURE plus a retry with a new run id.
  How `dagster-slurm` detects a dead SLURM job is `[unverified]`.
- **There must be a service.** That means the daemon, the webserver and, for more than one host,
  Postgres. A shared SQLite `DAGSTER_HOME` on NFS across hosts is not a configuration I found
  documented `[unverified]`.
- **Dagster must launch the process** for Pipes to work. mycooc spawns its own processes, which
  leaves only runless events.
- **Liveness for a third party is whatever the daemon last wrote.** With the default local launcher
  that is nothing.

**What adopting it would cost.**
- Rewrite mycooc's runner as assets, or as Pipes jobs launched by Dagster.
- Operate the webserver, the daemon and Postgres.
- Rebuild the `ensure`-to-step-N semantics over observations, or give them up.
- Give up the cooperative stop, or build it as a layer.
- Migrate about 2k existing run logs, which the user's no-legacy rule requires. The way to do it is
  to synthesise events through `report_runless_asset_event`.

For translation the cost is mostly the operations burden.

---

## 2. Flyte (Flyte 1 = flytekit/flyteidl; Flyte 2 = flyte-sdk)

**Summary.** Flyte runs typed **tasks** and **workflows** as **executions** on Kubernetes. Flyte 2
adds a local mode, a devbox and K8s `[F12][F14]`.

- **Task caching** keys on the inputs and a version `[F1][F2]`:
  - Flyte 1: project, domain, `cache_version`, task signature and inputs.
  - Flyte 2: the task name, an interface hash, the inputs minus ignored ones, and a version that can
    be an automatic hash of the function body.
- **`cache_serialize` / `serialize=True`** makes identical concurrent executions wait for one holder
  of a DataCatalog *reservation*. The holder keeps the reservation alive by heartbeat, and it
  expires if the holder stops `[F3][F4]`.
- **Intra-task checkpoints** let a retry attempt resume from the previous attempt's saved state. They
  are scoped to the attempts of one action, "not shared across different runs, even with identical
  inputs" `[F6]`.
- **Recover** re-executes a failed execution, copying the successful node outputs `[F11]`.
- **Flyte 2 traces** checkpoint helper-function results for replay after a crash `[F7][F13]`.
- **Interop**: flyteidl is a real protobuf IDL.

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** (bounded) | The execution id is (project, domain, name). The *name may be chosen by the caller*: on a duplicate, `FlyteRemote.execute(execution_name=…)` logs "Execution with Execution ID … already exists. Assuming this is the same execution, returning!" Inside an execution, a task's attempts share the checkpoint ("Each retry attempt sees the checkpoint saved by the attempt before it"). Checkpoints do not survive into a new execution. Recover creates a *new* execution. | [F5][F6][F10][F11] |
| 2 | Single-spawn | **covered** (a lease) for cached tasks | A DataCatalog reservation: "tasks are required to periodically extend the reservation", and "if the task currently holding the reservation fails to extend it before it times out, another task may acquire the reservation". `max-reservation-heartbeat` is 10s and `heartbeat-grace-period-multiplier` is 3. FlyteAdmin's unique execution names are a second arbiter. **Time arbitrates the claim**, which runstate deliberately refuses. | [F3][F4][F10] |
| 3 | Liveness / failure detection | **partial** | Propeller reconciles Kubernetes pod status, and reservation heartbeats cover cached tasks. Third parties read execution and node phases from the Admin API. Workers expose no heartbeat of their own; the detector is internal. | [F3][F14] |
| 4 | Terminal verdict | **covered** | Execution and node phases `[unverified exact list]`. User errors raised as `FlyteRecoverableException` are retried, so Flyte separates *recoverable* from *failed*. Flyte 2 changes the resources it requests on retry by failure mode, for example after an OOM or a preemption. A succeeded execution cannot be extended. | [F5][F12] |
| 5 | Cooperative stop | **absent** | Abort raises `asyncio.CancelledError` or `ActionAbortedError` in the task (Flyte 2), or deletes the pod (Flyte 1). The docs do not say whether an abort is recorded durably or can be sent to a task that is not running. Flyte 1 signals and gate nodes (`wait_for_input`, approve/reject) are durable inputs, but only *between* nodes, never inside a running task. | [F8][F10] |
| 6 | Per-step values | **absent / partial** | Decks (Flyte 1) and Reports (Flyte 2) are HTML. `flyte.report.flush()` streams a report to the UI while the task runs. Neither is a step-keyed, queryable series. | [F9] |
| 7 | Memoisation / produce-on-miss | **covered for atomic tasks** | Content-keyed cache (key components below). Flyte 1 does not invalidate on a code change alone `[inference from the key components]`. Nothing is cached for a partial run, so there is no suffix production. | [F1][F2] |
| 8 | Demand / subscriptions | **absent** | Schedules and launch plans only. There are no reader-to-worker subscriptions. | — |
| 9 | Derived runs | **covered** | A typed DAG, in which cached upstream outputs are the inputs of downstream tasks. | [F2] |
| 10 | Retention / GC | **partial** | Propeller's `max-cache-age` makes old cache entries miss. Object-store lifecycle is left to the operator. | [F4] |
| 11 | Time | **covered** | The Admin API records phase timestamps `[unverified detail]`. | — |
| 12 | Authority | **enforced at the API** | Admin authentication (OIDC) applies when it is configured. Cache entries are written by the platform. | `[unverified]` |
| 13 | Deployment | **heavy** | Flyte 1 and Flyte 2 OSS need a "Kubernetes cluster, a PostgreSQL database, and an object store". Flyte 2 local mode: "No cluster, no backend", with a local SQLite cache. Flyte 1 `pyflyte run` locally gets no retries ("retries are not supported"). | [F5][F12][F14][F15] |
| 14 | Constraints on worker code | **conflicts** for mycooc | Work runs as Flyte tasks in containers, or as raw-container tasks in any language. **Flyte launches the process.** Flyte 1 reaches SLURM through an SSH Slurm agent. Flyte 2 traces work "only for asynchronous functions" today. | [F7][F16] |
| 15 | Interop | **covered** (orchestration plane) | flyteidl protobuf covers tasks, literals and the Admin gRPC API. There is no spec for in-task telemetry or control. | `[unverified: not re-fetched]` |

**Detail for row 7.**
- **Flyte 1:** project, domain, `cache_version`, signature and inputs. Offloaded data is hashed with
  `HashMethod`.
- **Flyte 2:** inputs minus `ignored_inputs`, the task name, an interface hash and a version. The
  version comes from the `"auto"` function-body hash or from `"override"`.

**Strongest case that Flyte subsumes runstate.**

- **Content-addressed reuse is built in.** Flyte 2 even hashes the function body automatically.
- **Single-spawn with takeover.** `cache_serialize` is a heartbeat lease that another worker can take
  over. This **directly solves the failure class that cost mycooc about 20 GPU-hours**: a crashed
  foreign claim that reads live forever, which needed `reclaim_experiment.py`. Runstate's claim has
  no expiry; Flyte's does.
- **Resume after preemption.** Intra-task checkpoints plus retries give "attempt 4 resumes from step
  400" within one execution, with every attempt shown on one node.
- **Idempotent submission.** A caller-chosen `execution_name` (= rid) makes resubmission idempotent.
- **A resumable-versus-failed split.** `FlyteRecoverableException` separates the two verdicts.
- **Extension by chunking.** Extending a run could be a **chain of cached chunk tasks**,
  `train(cfg, start, end, prev_ckpt)`. Raising N from 100 to 500 then hits the cache for the
  `[0,100)` chunks and runs only the rest.
- **A real IDL** in flyteidl.
- **translation** fits naturally: batch tasks with cached blob outputs, gated downstream.

**Strongest case that it doesn't.**

- **A checkpoint dies with its execution.** That is documented for Flyte 2.
  - Extension after success therefore needs chunking.
  - Each chunk is a new pod or action, with process start-up and model load each time.
  - Work inside a chunk is invisible: no series and no progress beyond an HTML report.
- **No cooperative stop.**
- **Nothing beside the run.** A cold reader needs the Admin API.
- **The lease means time arbitrates the claim.** A worker that is alive but stalled and misses its
  heartbeats loses the reservation. A second worker then starts on the same checkpoint, which is the
  corruption runstate's single-spawn exists to prevent `[inference]`. Runstate's doctrine
  (`specs/observer-clock.md`: "Time never arbitrates a claim") rejects that trade explicitly. Flyte
  accepts it.
- **It needs Kubernetes, Postgres and an object store** for anything shared, and Flyte must launch
  the process.

**What adopting it would cost.**
- Containerise the work.
- Run a K8s control plane beside SLURM, which needs the Slurm agent.
- Restructure mycooc's training into chained cached chunks.
- Lose per-step `ensure`.
- Lose the cooperative stop.
- Migrate the existing history, which Flyte has no import path for `[inference]`.

---

## 3. Metaflow

**Summary.** A **flow** has **runs**, a run has **steps**, and a step has **tasks**.
- A task's artifacts are stored in a content-addressed datastore `[M9]`, and metadata goes to a local
  directory or to the metadata service.
- `resume` makes a **new run** that clones the successful steps of the origin run and restarts at a
  step boundary `[M1]`.
- `@retry` re-attempts a task.
- The **`@checkpoint`** decorator lives in the separate `metaflow-checkpoint` extension. It saves a
  directory while the task runs and reloads it `[M2]`:
  - on retry by default (`fresh`);
  - **across runs** with `load_policy='eager'`, keyed by flow, step and foreach index within the
    user's namespace `[M3]`.
- **Local mode is files only.** Run ids are microsecond timestamps, "without coordination or
  reliance on POSIX locks" `[M8]`.
- **Service mode** adds run and task heartbeats `[M7]`.

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** | The pathspec is flow/run/step/task. Run ids are minted by Metaflow, never by the caller. Task attempts come from `@retry`. `@checkpoint` resumes in two ways (below). `resume` makes a new run, linked by origin run id, with no single record spanning resumes. | [M1][M3][M8] |
| 2 | Single-spawn | **absent** | Local run ids are timestamps, so "we can be reasonably certain that it is unique and this makes it possible to do without coordination". Nothing stops two concurrent `eager` runs from loading the same checkpoint `[inference]`. | [M8] |
| 3 | Liveness / failure detection | **partial** (service only) | Service mode: a heartbeat sidecar posts to `…/runs/{run}/heartbeat` and `…/tasks/{task}/heartbeat` every 10 s, and the service and UI derive status from cutoffs. **Local mode has no heartbeat at all.** `Run.finished` is the end task's `_task_ok`, so a crashed or still-running run both read `finished == False` (from source). | [M6][M7][M12] |
| 4 | Terminal verdict | **partial** | `finished`, `successful` and `exception` booleans, "always about the latest task to have completed (in case of retries)". There is no preempted, killed or resumable verdict. A completed run cannot be extended; an `eager` checkpoint gives continuity in a new run. | [M6] |
| 5 | Cooperative stop | **absent** | The Runner API has `ExecutingRun.kill()`. There is no request channel into a running task. | [M5] |
| 6 | Per-step values | **partial** | Realtime cards: `current.card.refresh()`, with a 3–5 s poll in the UI or `card server`. But "the `card view` CLI command and the `get_cards` API see only delayed snapshots". Artifacts persist only when the task ends. `current.checkpoint.list(task=…)` can list the checkpoints of a task that is still running. | [M3][M4] |
| 7 | Memoisation / produce-on-miss | **absent** natively | Nothing skips a step by content. The datastore deduplicates artifacts by sha1, but does not reuse computation. The docs' `@memoize` is an *example* of a user-written decorator that reuses the "latest successful run". `resume` reuses the origin run's successful steps. | [M1][M9][M10] |
| 8 | Demand / subscriptions | **absent** | Only event triggers in production orchestrators (`@trigger`, `@trigger_on_finish`) `[unverified detail]`. | — |
| 9 | Derived runs | **partial** | The Client API reads any run's artifacts. There is no content-addressed identity and no staleness. | [M6] |
| 10 | Retention / GC | **absent** | `[unverified]` | — |
| 11 | Time | **covered** | `created_at` and `finished_at` on metadata and artifacts. | [M6] |
| 12 | Authority | **recorded** | User and namespace tags. `eager` honours the namespace. | [M3] |
| 13 | Deployment | **light locally, heavy shared** | Local mode is `.metaflow/` files, with no service. Shared or production use needs the metadata service (backed by Postgres), S3 and compute (Batch, K8s or `@slurm`), plus an orchestrator for scheduling. | [M7][M8][M11] |
| 14 | Constraints on worker code | **conflicts** for mycooc | Work runs as `FlowSpec` steps in Python. Metaflow launches the task. `@checkpoint` is an extension package. | [M2] |
| 15 | Interop | **absent** | No published format. The metadata service REST API is open source but not specified as a protocol. | — |

**Detail for row 1.**
- **`fresh`** reloads within the task's retries.
- **`eager`** "allows you to interrupt a run and resume it later in another run, preserving progress
  made within a task". It is keyed by flow name, step name and foreach index, within the user's
  namespace.

**Strongest case that Metaflow subsumes runstate.**

- **It is the only system here that shares runstate's bet of no service.** Local mode is plain files
  that any Client API reader can open.
- **`@checkpoint(load_policy='eager')` is mycooc's extend story, as documented:** "train a model for
  a few epochs, interrupt training, change code, and use the resume command to resume training from
  the latest checkpoint" `[M3]`.
- **Within one run**, `@retry` plus `@checkpoint` covers preemption.
- **SLURM** is covered by `@slurm`.
- **Live progress** comes from realtime cards.
- **Sweeps** come from `foreach` over configs.

**Strongest case that it doesn't.**

- **No content-addressed identity or reuse.** Run ids are timestamps. `eager` keys on the *foreach
  index*, not on a config hash, so reordering or extending a sweep can load the wrong config's
  checkpoint `[inference from the documented key]`.
- **No single-spawn.** It is deliberate: the run ids need no coordination.
- **In local mode, a cold reader cannot tell dead from running.** `Run.finished` stays False either
  way.
- **No stop, and no step-series read.**

The thin layer needed to fix these would be runstate itself: a run-id recipe, a claim, a liveness
signal, `ensure` and stop.

**What adopting it would cost.** Restructure into flows, and then rebuild most of runstate on top.

---

## 4. Prefect (brief, though closer on the control plane than expected)

**Summary.** Python flows and tasks report to a Prefect **server** (an API plus SQLite or Postgres)
or to Prefect Cloud.

- **Task caching** uses composable **cache policies** `[P1]`:
  - the default is inputs + code + flow run id;
  - the others are `INPUTS`, `TASK_SOURCE` and `FLOW_PARAMETERS`.
- **Transactions** give "at most one" execution per cache key, with `SERIALIZABLE` isolation and a
  lock manager (memory, file system or Redis) `[P2]`.
- **`prefect flow-run retry`** re-executes a failed or cancelled run "while maintaining the original
  flow run ID", with `run_count` incremented `[P4]`.
- **`send_input` / `receive_input`** is a **durable, ordered, per-flow-run inbox**. It needs no
  pause: "You don't need to pause or suspend the flow to send or receive input". Readers skip what
  they have already consumed with `exclude_keys` `[P3]`.
- **Heartbeats and automations** mark zombie runs Crashed `[P5]`.
- **Cancellation** goes to a worker, which signals the infrastructure, waits a 30 s grace period,
  then kills `[P6]`.

| # | concern | rating | mechanism |
|---|---|---|---|
| 1 | Identity across attempts | **partial** | One flow run id across retries (`run_count`). The id is a UUID minted by the server, not by the caller. Retry applies only to failed or cancelled runs, not completed ones `[P4]`. |
| 2 | Single-spawn | **partial** | `SERIALIZABLE` plus a lock manager, per cache key: the file-system lock works on one host, Redis across hosts. Without it, concurrent runs "may both execute" `[P2]`. |
| 3 | Liveness / failure detection | **partial** | Heartbeats (`PREFECT_RUNNER_HEARTBEAT_FREQUENCY`, at least 30), and an automation marks a run Crashed after 90 s of silence. The infrastructure id is the hostname plus PID, a container or a K8s job, a close cousin of runstate's `local://host/pid` `[P5][P6]`. |
| 4 | Terminal verdict | **covered** | Completed, Failed, Crashed, Cancelled, Paused and Suspended states. *Crashed* (infrastructure) is distinct from *Failed* (code). |
| 5 | Cooperative stop | **partial** | Cancellation is a signal followed by a kill. The **inbox** is the raw material for a durable, safe-point stop that would also survive into a retry under the same flow run id `[inference]`. |
| 6 | Per-step values | **partial** | Events, kept 7 days by default (`retention_period = timedelta(days=7)`, `[P7]`), and progress artifacts. Not a step-keyed series. |
| 7 | Memoisation | **covered for atomic tasks** | Cache policies and an expiry. Results go to `~/.prefect/storage/` by default. No suffix production. |
| 8 | Demand | **partial** | Automations and event triggers. No subscriptions. |
| 9 | Derived runs | **partial** | Within a flow's task graph only. |
| 10 | Retention | **partial** | Event retention exists. Result storage is unmanaged `[unverified]`. |
| 11 | Time | **covered** | |
| 12 | Authority | **recorded** in OSS `[unverified]`; role-based access control in Cloud. | |
| 13 | Deployment | **a server** | The server and its database. Workers are needed for deployments. |
| 14 | Constraints on worker code | **covered** (Python only) | A decorated `@flow` runs in whatever process calls it and reports to the server `[unverified: not re-fetched]`. A self-spawned worker, as in mycooc, can therefore participate; this is unlike Dagster and Flyte, which must launch the process. Deployments add workers. |
| 15 | Interop | **partial** | A REST API `[unverified as a stable spec]`. |

**Strongest case.** A thin layer over Prefect could provide most of runstate's control plane:
- the flow run id as the identity, kept across retries;
- the inbox carrying `stop`;
- heartbeats and Crashed for liveness;
- serialisable caching for reuse.

**Why it doesn't.**
- Identity is server-minted, so a content-addressed rid cannot be the run's identity. It can only be
  a cache key.
- A completed run cannot be retried or extended.
- Events expire after 7 days, which is lossy by default.
- A server is required.

---

## 5. Nix and Bazel's Remote Execution API (brief)

**Nix** (manual 2.34.9).

- **The unit is the derivation.** Outputs are either input-addressed or content-addressed
  `[N1]`:
  - "fixed" content addressing is stable;
  - "floating" content addressing (the `ca-derivations` feature) is experimental.
- **Concurrent builds of one path:** "the first Nix instance that gets there will perform the build,
  while the others block (or perform other derivations if available) until the build finishes"
  `[N2]`. This is a path lock, so it holds on one store and one host.
- **Builds are atomic and hermetic.** Nothing records partial or step-level progress.
- **GC is mark-and-sweep from GC roots.** That is the same structure as runstate's home-level GC
  recipe (`specs/store.md` Recipe 3), and Nix is the precedent.
- **Authority is *enforced*:**
  - the store is owned by the daemon;
  - there are trusted users;
  - binary caches are signed.

  That is stronger than anything else in the family.

**Ratings:**
- **covered:** memoisation of atomic work; derived runs; GC; authority.
- **absent:** identity across attempts; liveness; stop; per-step values; time (by design); suffix
  production.
- **single-spawn:** covered on one host.

**Bazel / REAPI** (v2.12.0).

- **Cache key.** An `Action` digest covers the command, the input root digest, a timeout and
  `do_not_cache`. It "will be cached in the action cache".
- **Timeout in the key, deliberately.** REAPI puts the **timeout in the key** so that "a lower
  timeout will result in a cache miss" rather than silently reusing a longer run's result `[B1]`.
  This is the *mirror image* of runstate's rule that the run id excludes the step target. Both are
  deliberate. REAPI's units are atomic, so it has nothing to extend.
- **Single-spawn by the server.** In-flight requests for the same `Action` may be merged
  (`do_not_cache` "…in-flight requests for the same `Action` may not be merged"). So the server
  arbitrates single-spawn when it chooses to.
- **Re-attaching.** `Execute` and `WaitExecution` stream a long-running `Operation` through the
  stages CACHE_CHECK, QUEUED, EXECUTING and COMPLETED. A second client can re-attach by operation
  name.
- **Interop is the family's best.** It is a protobuf specification with several independent server
  implementations `[unverified: implementations not enumerated here]`.
- **No** long-running resumable unit, no step-level values and no cooperative stop.

---

## 6. DVC and DVCLive (brief), with a null result

**DVC** (3.67.1).

- **Stages** are defined in `dvc.yaml` and locked in `dvc.lock` by the hashes of their commands,
  dependencies, parameters and outputs.
- **The run-cache** at `.dvc/cache/runs/<key[:2]>/<key>` restores a stage's outputs when "a stage
  runs under the same conditions". It can be shared with `dvc push --run-cache` `[V1][V2]`. The
  sharding is the same as runstate's `runs/<rid[:2]>/<rid>/` placement.
- **Reuse and resume exclude each other** (`dvc/stage/cache.py:_can_hash`). The run-cache refuses to
  cache any stage with `persist: true` outputs, and `persist` is DVC's mechanism for letting a stage
  resume from its own previous output `[V2]`. **In DVC you choose content-addressed reuse or
  resumption, not both.** That is precisely the combination mycooc's run-id recipe exists to
  provide: reuse a finished run, and extend it.
- **Deployment** is files and git; there is no service.

**DVCLive** (3.49.1).
- It writes a **per-step metric series to files**: `metrics.json`, plus `plots/metrics/*.tsv` with a
  step column. Anyone reading the file can see the series while the run is live.
- `Live(resume=True)` will "try to read the previous `step` from the `metrics_file`" and continue
  from it `[V5]`.
- It is the family's only service-free, step-keyed value series. It has no identity, claim, liveness
  or control.

**The null result.** **DVC 3.0 removed `checkpoints`**: "`checkpoints` has been removed (#9271)",
released 2023-06-13 `[V4]`. The feature was `checkpoint: true` outputs, with `dvc exp run` resuming
step-indexed training. The rationale is in issue #9221, opened 2023-03-21 and closed 2023-05-23
`[V3]`:
- "doesn't provide value while coming at an important cost of code/docs maintenance";
- "Epochs are being handled (arguably) incorrectly … the `epochs` param is now treated as `epochs +
  epochs_completed_at_checkpoint` which differs with the meaning when training without resuming";
- "After resuming from a checkpoint, the experiments can't be reproduced easily … not possible to
  reproduce the experiment at all if the checkpoints are deleted";
- "Resumed experiments are not differentiable after persisting";
- "ambiguous/unexpected behavior … how are downstream stages after `checkpoint: true` supposed to be
  executed?"

The replacement advice was: "People should handle interruption and resuming through the ML framework
and DVC already provides convenient tools to wrap it (params, `persist`, run-cache)". Yet `persist`
disables the run-cache, as shown above.

**What the null result says about runstate.** Two of the four failure reasons are exactly the
problems runstate's run-id recipe and episodes address:
- *the step target in identity* → "exclude the step-target; the trajectory must be
  target-independent", `specs/run-id-recipe.md`;
- *a resumed run's identity and history* → episodes on one log.

This is evidence both ways:
- **The need is real.** A mature tool shipped this exact feature.
- **The naive version failed** for the reasons runstate's recipe names. Runstate's answer is so far
  tested on two consumers by one author.

It is a strong candidate for the dead-end record or the wiki.

---

## 7. Airflow (brief; adds little)

- **Task-instance identity.** A task instance is (dag_id, run_id, task_id, map_index), with
  `try_number` counting attempts.
- **Task Execution API.** Airflow 3's Task Execution API (AIP-72) moves state transitions,
  heartbeats and XComs behind an API server. The Task SDK is "designed to support multiple language
  bindings" `[A2][A3]`.
- **Liveness.** A task instance is marked failed after `task_instance_heartbeat_timeout` `[A1]`.
  Liveness is heartbeat-timeout based and cross-host, but needs the scheduler and its database.
- **Absent:** a step-level unit, cooperative stop, and content-addressed reuse.
- **Its only addition here** is a second example, after Prefect, of heartbeat-timeout liveness
  behind a service API that is meant to be language-neutral.

---

## 8. Across the family

### 8.1 Which system comes closest

| | Dagster | Flyte | Metaflow | Prefect |
|---|---|---|---|---|
| covered | 4, 9, 11 (and 12, equal to runstate) | 2, 4, 7 (atomic), 9, 11, 15 | 11, 13 (local mode) (and 12, equal to runstate) | 4, 7 (atomic), 11, 14 (and 12, equal to runstate) |
| partial | 1, 2, 3, 6, 7, 8, 10, 15 | 1, 3, 6, 10, 12 | 1, 3, 4, 6, 9 | 1, 2, 3, 5, 6, 8, 9, 10, 15 |
| absent / conflicts | 5, 13, 14 | 5, 8, 13, 14 | 2, 5, 7, 8, 10, 14, 15 | 13 (needs a server) |

- **Dagster comes closest for the two consumers taken together.** It has a native "what exists /
  what is missing" read with produce-on-miss, durable per-partition history across runs, derived
  runs with automatic provenance, a timestamped event log, and an API a cockpit could use.
  translation is its home ground.
- **Flyte comes closest on two mechanisms** runstate treats as core:
  - single-spawn, with a lease that can be taken over;
  - resume across attempts.
- **Metaflow is the only one that shares the no-service bet.**
- **Prefect** has the best raw material for a control plane: a durable per-run inbox and one id
  across retries.

### 8.2 Concerns no system in this family covers

1. **A durable, safe-point cooperative stop** that survives worker downtime, carries into the next
   attempt, and is discharged by the next stop record (runstate C7, rubric 5).
   - Every system offers an interrupt or a kill: Dagster raises an interrupt, Flyte a
     `CancelledError` or pod deletion, Metaflow `kill()`, Prefect a signal then a kill.
   - Prefect's inbox is the only substrate one could build it on.
2. **A step-indexed value series as a first-class live read** ("loss up to step N").
   - In the orchestrators, values are per materialisation or task, or are HTML reports.
   - DVCLive has the series as files, but no identity or control.
3. **Producing only the missing suffix by extending the same identity, including after a completed
   run.**
   - In every system the unit is atomic, and success seals it.
   - The family's one workaround is **chunked prefix memoisation**: Dagster partitions per chunk;
     Flyte or Prefect cached chunk tasks chained through a checkpoint input; even a Nix derivation
     per chunk.
   - It works at chunk granularity. It costs a process or pod per chunk and leaves progress inside
     a chunk invisible. The identity becomes a chain of keys rather than one run.
   - That is a real alternative design for mycooc and should be priced, not dismissed.
4. **Cold third-party liveness without a service.**
   - Dagster's local launcher has no probe.
   - Metaflow's local mode has no heartbeat.
   - DVC has nothing.
   - Every system that does detect liveness does it in a daemon or server and *stores* the verdict.
   - Runstate's tiers, a handle probe plus staleness from dated records, are distinct here.
5. **Subscriptions and leased demand** (rubric 8). No system has them. No runstate consumer has used
   them either, so this is not a differentiator that matters to the consumers.
6. **A claim that time never arbitrates.**
   - The family uses leases (Flyte), locks (Prefect: file system or Redis; Nix: a path lock),
     merging by the server (REAPI) or queue serialisation by one daemon (Dagster).
   - Runstate's CAS-plus-recorded-claim with no expiry is unique here, and it **costs runstate the
     cross-host wedge** that Flyte's lease avoids.
   - So this is a design choice to defend, not a gap others failed to fill.

### 8.3 What the family does better than runstate

- **Derived runs with automatic provenance and staleness** (Dagster).
- **GC from roots** (Nix, the precedent for runstate's recipe).
- **Leases that can be taken over** (Flyte).
- **Retry budgets and failure-aware retries** (Flyte 2 changes its resource request after an OOM or
  a preemption).
- **A recoverable-versus-failed user error** (Flyte).
- **Enforced authority**: Nix signatures and role-based access control.
- **Interop that other implementers actually build to**: REAPI, flyteidl, Pipes' JSON Schema.
- **UIs and scheduling.**

### 8.4 Convergences worth noting

These are independent arrivals at runstate's choices, which is mild evidence for them.

| runstate choice | independent arrival |
|---|---|
| deriving an attempt count by folding the run's own log | Dagster counts resume attempts by folding its run's own event log |
| run placement `runs/<rid[:2]>/<rid>/` | DVC's run-cache uses the same `runs/<key[:2]>/<key>` sharding |
| the `local://host/pid` handle | Prefect identifies a process by "the machine hostname and the PID" |
| `additionalProperties: false` schemas | Pipes' JSON Schema pins `additionalProperties: false` and carries a protocol version |

### 8.5 What this means for the positioning claim

**The claim as written is falsified in this family** by its bounded form:
- Flyte: task attempts that share checkpoints, recorded on one execution.
- Metaflow: `@retry` plus `@checkpoint`; `eager` across runs.
- Dagster: run resume under the same run id, in the same event log.

**What survives.** No system in this family combines all four of these:
1. **A caller-chosen, content-addressed identity** that is *also* the durable home of all attempts.
   - Flyte's named executions are caller-chosen but last one execution.
   - Dagster partitions are durable but their materialisations are atomic.
2. **An unbounded sequence of episodes**, including **extension after a completed episode** at step
   granularity.
3. **A history that is one file beside the run**, readable with no service.
   - Dagster's per-run SQLite file is the nearest match. But it holds one run, not the identity
     across runs, and its schema is internal.
4. **A control plane (stop) on the same record.**

A defensible rewording:

> "Bounded versions exist: Flyte resumes a task across retry attempts from its checkpoint, Metaflow
> `@checkpoint` and Dagster run-resume do the same. What we found in no orchestrator is one
> caller-addressed identity that hosts unboundedly many attempts, extends after completion, and keeps
> its whole history and control in one service-free file."

The other family reports should be checked before relying on this, especially the durable-execution
engines.

### 8.6 Optional, for the redesign (`docs/backlog/if-built-today/`)

- **Questions with quantifiers.** Dagster's `AutomationCondition` has quantifiers over dependencies:
  `any_deps_match` means "true for any upstream partition", and `all_deps_match` means "true for at
  least one partition of each upstream asset". It also has temporal operators (`since`,
  `newly_true`). It is a launch-side condition algebra, and the closest thing in the family to
  "questions with quantifiers".
- **Demand propagated by rules.** `on_missing` waits for all upstream partitions, so demand
  propagates through dependencies. Nix and Bazel realise missing dependencies on demand in the same
  way.
- **Told negative facts.** No system has "nothing more will come". Dagster's `missing` is
  closed-world absence, not a told fact.

---

## 9. Caveats

**Not verified from primary sources:**
- Flyte's exact phase lists.
- Prefect OSS authentication.
- Metaflow's `@trigger` details.
- How `dagster-slurm` detects a dead SLURM job.
- Whether a shared-NFS SQLite `DAGSTER_HOME` is supported.

**Taken from community or secondary sources:**
- The Metaflow heartbeat cutoff variables (`RUN_INACTIVE_CUTOFF_TIME`).
- That a single Dagster run over a range emits per-partition materialisations at the end. The
  official docs say only "Dagster will track that all the partitions have been filled" and "a
  failure requires retrying all partitions together".

**Not done:**
- Any hands-on test of Dagster partition keys as steps, or of Flyte chunk chains.

---

## 10. Sources

Documentation was fetched on 2026-10-02. Source files are from the default branch on 2026-10-02.

**Dagster** (docs 1.13.25)
- **[D1]** Run monitoring: https://dagster.io/docs/guides/deploy/execution/run-monitoring
- **[D2]** https://github.com/dagster-io/dagster/blob/master/python_modules/dagster/dagster/_daemon/monitoring/run_monitoring.py
  (`count_resume_run_attempts`, `monitor_started_run`)
- **[D3]** `python_modules/dagster/dagster/_core/launcher/base.py`: `supports_check_run_worker_health`
  and `supports_resume_run` default to False. `default_run_launcher.py` does not override them.
- **[D4]** `python_modules/dagster/dagster/_core/storage/dagster_run.py`: `DagsterRunStatus`,
  `parent_run_id`, `root_run_id`.
- **[D5]** Run retries: https://dagster.io/docs/guides/deploy/execution/run-retries
- **[D6]** Asset versioning and caching:
  https://dagster.io/docs/guides/build/assets/asset-versioning-and-caching
- **[D7]** Backfills:
  - https://dagster.io/docs/guides/build/partitions-and-backfills/backfilling-data
  - https://dagster.io/docs/examples/mini-examples/partition-backfill-strategies
- **[D8]** Partitioning assets:
  https://dagster.io/docs/guides/build/partitions-and-backfills/partitioning-assets
- **[D9]** Declarative automation:
  - https://dagster.io/docs/guides/automate/declarative-automation
  - https://dagster.io/docs/guides/automate/declarative-automation/customizing-automation-conditions/automation-condition-operands-and-operators
- **[D10]** Concurrency:
  - https://dagster.io/docs/guides/operate/managing-concurrency
  - `QueuedRunCoordinator` `tag_concurrency_limits` / `applyLimitPerUniqueValue`:
    https://docs.dagster.io/_modules/dagster/_core/run_coordinator/queued_run_coordinator
- **[D11]** Pipes:
  - https://dagster.io/docs/guides/build/external-pipelines/dagster-pipes-details-and-customization
  - JSON Schema (last change 2024-12-19):
    https://github.com/dagster-io/community-integrations/tree/main/libraries/pipes/jsonschema
  - TypeScript, Rust and Java implementations, 2025-05-12:
    https://dagster.io/blog/pipes-typescript-rust-java
- **[D12]** `python_modules/dagster/dagster/_utils/interrupts.py`: SIGTERM is mapped to SIGINT and
  raised as `DagsterExecutionInterruptedError`.
- **[D13]** Instance storage (`history/runs.db`, `history/runs/<run_id>.db`):
  https://docs.dagster.io/guides/deploy/dagster-yaml
- **[D14]** `python_modules/dagster/dagster/_core/instance/methods/asset_methods.py`:
  `report_runless_asset_event`.
- **[D15]** Op events: https://docs.dagster.io/guides/build/ops/op-events
- **[D16]** dagster-slurm: https://docs.dagster.io/integrations/libraries/slurm

**Flyte**
- **[F1]** Flyte 2 caching:
  https://www.union.ai/_r_/flyte/en/latest/user_guide/development_lifecycle/caching/ (serves the v2
  page)
- **[F2]** Flyte 1 caching:
  https://docs-flyte-legacy.union.ai/en/latest/user_guide/development_lifecycle/caching.html
- **[F3]** Flyte 1 cache serialising:
  https://docs-flyte-legacy.union.ai/en/latest/user_guide/development_lifecycle/cache_serializing.html
- **[F4]** DataCatalog configuration (`heartbeat-grace-period-multiplier` 3,
  `max-reservation-heartbeat` 10s) and propeller's `max-cache-age`:
  https://www.union.ai/docs/v1/flyte/deployment/configuration-reference/datacatalog-config/
- **[F5]** Flyte 1 intratask checkpoints:
  https://www.union.ai/docs/v1/flyte/user-guide/programming/intratask_checkpoints/
- **[F6]** Flyte 2 intra-task checkpoints:
  https://www.union.ai/docs/v2/flyte/user-guide/task-programming/intra-task-checkpoints/
- **[F7]** Flyte 2 traces: https://www.union.ai/docs/v2/flyte/user-guide/task-programming/traces/
- **[F8]** Flyte 2 abort: https://www.union.ai/docs/v2/flyte/user-guide/task-programming/abort-tasks/
- **[F9]** Flyte 2 reports: https://www.union.ai/docs/v2/flyte/user-guide/task-programming/reports/
- **[F10]** https://github.com/flyteorg/flytekit/blob/master/flytekit/remote/remote.py
  (`execute(execution_name=…)` treats AlreadyExists as "the same execution"; signals)
- **[F11]** Recover:
  https://docs-flyte-legacy.union.ai/en/v1.13.2/flytectl/gen/flytectl_create_execution.html
- **[F12]** Flyte 2 GA, 2026-08-04:
  https://web.union.ai/blog-post/flyte-2-is-generally-available-the-durable-open-source-ai-runtime
- **[F13]** 2026-02-11: https://web.union.ai/blog-post/building-crash-proof-ai-systems
- **[F14]** OSS deployment: https://www.union.ai/docs/v2/flyte/oss-deployment/overview/
- **[F15]** Running locally: https://www.union.ai/docs/v2/flyte/user-guide/run-modes/running-locally/
- **[F16]** Slurm agent:
  https://docs-flyte-legacy.union.ai/en/latest/flytesnacks/examples/slurm_agent/index.html

**Metaflow**
- **[M1]** `resume`: https://docs.metaflow.org/metaflow/debugging
- **[M2]** https://docs.metaflow.org/scaling/checkpoint/introduction
- **[M3]** https://docs.metaflow.org/scaling/checkpoint/selecting-checkpoints
- **[M4]** https://docs.metaflow.org/metaflow/visualizing-results/dynamic-cards
- **[M5]** https://docs.metaflow.org/metaflow/managing-flows/runner
- **[M6]** Client API:
  - https://docs.metaflow.org/metaflow/client
  - https://github.com/Netflix/metaflow/blob/master/metaflow/client/core.py (`Run.finished` → end
    task `_task_ok`)
- **[M7]** Heartbeats: `metaflow/metadata_provider/heartbeat.py` (10 s) and
  `metaflow/plugins/metadata_providers/service.py` (run and task heartbeat URLs).
- **[M8]** `metaflow/plugins/metadata_providers/local.py`: `new_run_id` is a timestamp, "without
  coordination or reliance on POSIX locks".
- **[M9]** `metaflow/datastore/content_addressed_store.py`
- **[M10]** The `@memoize` example:
  https://docs.metaflow.org/metaflow/composing-flows/advanced-custom-decorators
- **[M11]** https://pypi.org/project/metaflow-slurm
- **[M12]** Heartbeat cutoffs (community, secondary):
  https://community.outerbounds.com/t/22659848/question-around-the-metaflow-ui-showing-old-runs-as-still-ru

**Prefect** (3.8.7)
- **[P1]** https://docs.prefect.io/v3/concepts/caching
- **[P2]** https://docs.prefect.io/v3/advanced/transactions
- **[P3]** https://docs.prefect.io/v3/advanced/interactive
- **[P4]** https://docs.prefect.io/v3/how-to-guides/workflows/retry-flow-runs
- **[P5]** https://docs.prefect.io/v3/advanced/detect-zombie-flows
- **[P6]** https://docs.prefect.io/v3/advanced/cancel-workflows
- **[P7]** `src/prefect/settings/models/server/events.py` (`retention_period` defaults to 7 days)

**Nix and Bazel**
- **[N1]** Nix manual 2.34.9:
  https://nix.dev/manual/nix/stable/store/derivation/outputs/content-address
- **[N2]** Nix manual, "simple building and testing" (legacy text on concurrent builds and locks),
  preserved in the Lix tree:
  https://git.lix.systems/puck/lix/src/commit/9819bb20da130509ab62f303267331c2403b043c/doc/manual/expressions/simple-building-testing.xml
  `[older text; current wording not re-checked]`
- **[B1]** REAPI `remote_execution.proto` v2.12.0:
  https://github.com/bazelbuild/remote-apis/blob/main/build/bazel/remote/execution/v2/remote_execution.proto
  (`Action`, `timeout`, `do_not_cache`, `skip_cache_lookup`, `ExecutionStage`)

**DVC**
- **[V1]** https://doc.dvc.org/user-guide/pipelines/run-cache
- **[V2]** https://github.com/treeverse/dvc/blob/main/dvc/stage/cache.py: `_can_hash` excludes
  `persist` outputs; `runs/<key[:2]>/<key>`.
- **[V3]** https://github.com/treeverse/dvc/issues/9221, "Drop/Revisit usage of `checkpoints`",
  2023-03-21 → 2023-05-23.
- **[V4]** https://github.com/treeverse/dvc/releases/tag/3.0.0 (2023-06-13): "`checkpoints` has been
  removed (#9271)".
- **[V5]** DVCLive `Live`: https://doc.dvc.org/dvclive/live/

**Airflow** (3.3.2)
- **[A1]** https://airflow.apache.org/docs/apache-airflow/3.2.1/core-concepts/tasks.html
  (`task_instance_heartbeat_timeout`)
- **[A2]** https://airflow.apache.org/docs/task-sdk/stable/index.html
- **[A3]** https://cwiki.apache.org/confluence/display/AIRFLOW/Airflow+3+Workstreams (AIP-72)
