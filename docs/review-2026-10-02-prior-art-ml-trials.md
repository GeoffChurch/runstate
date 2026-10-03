# Prior art: ML trial orchestration and experiment state

Family surveyed for `GeoffChurch/runstate`'s claim in `docs/positioning.md`:

> What does not exist elsewhere is the *identity*: attempt 4 resumes from step 400, is the same run as
> attempts 1–3, and the whole history is one re-readable artifact.

The claim is tested against the bet "no service; the log is a file beside the run", and against the two
consumers' real use. mycooc spawns its own processes, does content-addressed reuse, extends finished runs
and sends `control.stop`. translation gates on `ensure`.

Surveyed 2026-10-02. **Versions read:**

| system | version | how it was read |
|---|---|---|
| Optuna | **v5.0.0** (2026-09-07) | source read at the tag. Some files were first read in the local 4.3.0 install, then re-checked at v5.0.0. |
| Ray | **2.59.0** (2026-10-02) | source at the tag, plus docs.ray.io "latest" |
| Determined | **v0.38.1** (2025-03-19) | source and docs at the tag. It is the last release; the last push to the repo was 2025-03-20. |
| Katib | master | — |
| Syne Tune | main (v0.16.0, 2026-08-03) | — |
| submitit | 1.5.4 | local install |
| Hydra | main (v1.3.7) | — |
| MLflow / W&B | — | current docs. W&B's docs now redirect to docs.coreweave.com. |

Source links are in the tables, and §6 collects them. Anything not confirmed is marked `[unverified]`.
Interpretation is marked *interpretation*.

---

## 0. Bottom line

**The identity claim, as worded, is falsified.** The falsifying system is **Determined**:

- A trial is one durable id across many attempts. `restart_id` (also called `trial_run_id`) counts the
  attempts.
- `StartTrial(resume=True)` returns the latest checkpoint and `steps_completed`, so attempt N resumes from
  step S.
- Metrics are keyed `(trial_id, trial_run_id, total_batches)`. When an attempt restarts, rows from earlier
  attempts at or beyond its restart step are archived, not deleted. The trial therefore has one history,
  where the latest attempt wins at each step and every record is retained.
- Since August 2023 the trial id can be **caller-chosen** (`external_trial_id`, in "detached mode") for
  processes Determined did not spawn.

Weaker witnesses:

- **Ray Tune**: one `trial_id`, plus a `result.json` that is appended across restores, within one
  experiment and one driver.
- **W&B**: resume by caller-chosen run id, plus rewind-to-step.

**What survives** is not the identity but the **conjunction**. No surveyed system offers all of:

- (a) that identity,
- (b) **no service**, with a per-run file as the interop artifact,
- (c) a **durable cooperative stop** delivered to workers the system did not spawn,
- (d) **produce-on-miss** (`ensure`) keyed by a content-addressed id.

Determined has (a), and (c) only for workers it spawns, and needs a master service plus Postgres. Optuna
has (b), and only a shadow of (a). Nobody in the family has (d).

**Closest system:** Determined on semantics, Optuna's `JournalStorage` on deployment shape. Neither
subsumes runstate for these two consumers, for the reasons in §§1–3.

---

## 1. Optuna (v5.0.0)

**Summary.** Optuna is a hyperparameter-search library.

- **Units.** The unit of durability is the **study**. A **trial** is a single-attempt row with a state
  machine: `WAITING → RUNNING → {COMPLETE, PRUNED, FAIL}`.
- **Finished is final.** The storage contract says "Trials in finished states are not allowed to be
  modified" (`BaseStorage` docstring).
- **Intermediate values.** `trial.report(value, step)` stores one float per `(trial, step)` and feeds the
  pruner, which the worker polls with `trial.should_prune()`.
- **Liveness and retry.** Liveness is an RDB-only heartbeat. A stale trial is moved to `FAIL`, and
  `RetryHeartbeatStaleTrialCallback` (formerly `RetryFailedTrialCallback`) enqueues a **new** trial that
  links back through `system_attrs["failed_trial"/"retry_history"]`.
- **Storage.** Storage is pluggable:
  - `RDBStorage`: SQLite, Postgres or MySQL;
  - `JournalStorage` over `JournalFileBackend` (a JSONL op log, one file per storage) or Redis;
  - a gRPC proxy.
- **Viewer.** `optuna-dashboard` reads either an RDB URL or a journal file path. It also ships a
  browser-only build that reads a SQLite file through Wasm.

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** | The study is durable (`study_name`, `load_if_exists`). A trial is **not**: a retry is a new trial number in WAITING with `failed_trial`/`retry_history` lineage. Intermediate values are copied only with `inherit_intermediate_values=True`. Resuming from a checkpoint is user code: the canonical example walks `retry_history` to find the last `artifact_id` user attr. | [`storages/_callbacks.py`](https://github.com/optuna/optuna/blob/v5.0.0/optuna/storages/_callbacks.py); [pytorch_checkpoint.py](https://github.com/optuna/optuna-examples/blob/main/pytorch/pytorch_checkpoint.py) |
| 2 | Single-spawn | **covered** (WAITING→RUNNING only) | **RDB:** `SELECT … FOR UPDATE`; RUNNING is set only if the current state is WAITING, otherwise it returns False. **Journal:** a pre-check, then an unconditional append of a RUNNING record, then replay. The first RUNNING record in log order wins (`_apply_set_trial_state_values` ignores a later one), so ownership is decided **by the fold, not by CAS**. Stale-FAIL is arbitrated the same way, so exactly one retry trial is minted. | [`_rdb/storage.py`](https://github.com/optuna/optuna/blob/v5.0.0/optuna/storages/_rdb/storage.py) `set_trial_state_values`; [`journal/_storage.py`](https://github.com/optuna/optuna/blob/v5.0.0/optuna/storages/journal/_storage.py); `Study._pop_waiting_trial_id` |
| 3 | Liveness / failure detection | **partial** | A heartbeat thread writes **DB server time** (`sqlalchemy.func.now()`) every `heartbeat_interval`. `fail_stale_trials` fails RUNNING trials past `grace_period`, which defaults to 2× the interval. Three limits: it runs only at the start of each trial inside `study.optimize()` (or when called explicitly); "If you use ask and tell … it will not work"; and `BaseHeartbeat` is implemented **only by RDBStorage** (and its cache wrapper), so the journal has none. A third party has no read-only liveness API: `fail_stale_trials` mutates, and the heartbeat table is reachable only by SQL. | [`_heartbeat.py`](https://github.com/optuna/optuna/blob/v5.0.0/optuna/storages/_heartbeat.py); RDBStorage docstring; [FAQ](https://optuna.readthedocs.io/en/stable/faq.html) |
| 4 | Terminal verdict; extend a completed run | **partial / conflicts** | Five states. FAIL conflates a crash, a preemption detected by heartbeat, and an objective exception. COMPLETE is immutable (`UpdateFinishedTrialError`), so **a finished trial cannot be extended**; you mint a new trial and copy values. | `BaseStorage`; `TrialState` |
| 5 | Cooperative stop | **partial** | Pruning is cooperative: the worker polls `should_prune()` at its own safe point and raises `TrialPruned`. But the decision belongs to the in-process `BasePruner` acting on intermediate values. There is **no operator→trial request record**. `Study.stop()` "only works when it is called inside an objective function or callback" of the calling process. A thin layer is possible: a custom pruner that reads a study user attr written by an operator, which would be durable in storage. It is not provided. | [`study.py`](https://github.com/optuna/optuna/blob/v5.0.0/optuna/study/study.py) `stop`; [`trial/_trial.py`](https://github.com/optuna/optuna/blob/v5.0.0/optuna/trial/_trial.py) `should_prune` |
| 6 | Per-step values, readable live | **partial** | One float per `(trial, step)`, **single-objective only**. A repeated step is ignored with a warning (first write wins). Any process with storage access reads it live (`study.get_trials()`, dashboard). There are no named multi-metric series, and `user_attrs` has no step axis. Across retries the series is split over trial numbers unless copied. | `Trial.report`, `BaseStorage.set_trial_intermediate_value` |
| 7 | Memoisation / produce-on-miss | **absent** | `enqueue_trial(skip_if_exists=True)` dedups on params ("might produce duplicated trials if called simultaneously"). There is no "values up to step N, extend if short", and no content-addressed trial id: trial numbers are sequential, though a *study* name could be a hash. | `Study.enqueue_trial` |
| 8 | Demand / subscriptions | **absent** | The `BaseStorage` API has no reader-to-worker request op. Samplers and pruners read whatever the worker reports. | `BaseStorage` (enumerated) |
| 9 | Derived runs | **absent** | No cross-trial derivation record in the storage model. `copy_study` copies whole studies. | `BaseStorage` |
| 10 | Retention / GC | **absent** | `BaseStorage` has no retention op beyond `delete_study`. Journal snapshots exist only for backends implementing `BaseJournalSnapshot` (Redis). | `journal/_base.py` |
| 11 | Time | **partial** | `datetime_start`/`datetime_complete` per trial, normalized to UTC in v5.0 (#6776). Intermediate values are **undated**. Journal records carry no time except state transitions. The RDB heartbeat uses the server clock, which is a good cross-host choice. | v5.0.0 release notes; journal `_storage.py` |
| 12 | Write authority, provenance, forgery | **partial** (stronger than runstate in one respect) | The storage **enforces** "finished is immutable". A zombie whose trial was stale-failed gets `UpdateFinishedTrialError` on its next write, which works as a de facto fence. Journal records carry `worker_id` (uuid + thread id). There is no authentication. | `check_trial_is_updatable`; journal `_write_log` |
| 13 | Deployment shape | **library, no service needed** | SQLite file, or a single JSONL journal file with symlink or `O_EXCL` lockfiles "for NFSv2/v3 or later". Postgres, MySQL, Redis or gRPC are optional. | [journal tutorial](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/011_journal_storage.html); `journal/_file.py` |
| 14 | Constraints on worker code | **partial** | Python, or Rustuna. Parameters arrive via `trial.suggest_*`, though they can be fixed with `enqueue_trial`/`ask(fixed_distributions)`. Heartbeat needs `optimize()`. Ask-and-tell decouples the loop, but constructing `Trial(study, trial_id)` in another process is "not recommended". | `Trial` docstring; RDBStorage docstring |
| 15 | Interop | **partial** | The RDB schema is SQLAlchemy models with versioned migrations. The journal op codes (`CREATE_TRIAL`, `SET_TRIAL_INTERMEDIATE_VALUE`, …) are defined in code, with no published schema and no version field. **A second implementation exists:** Rustuna (Rust, released by PFN with Optuna v5.0 on 2026-09-07). Optuna v5 replays Rustuna journals by skipping Rustuna's `DISCARD_TRIALS` op (#6790), and Rustuna has a `test_schema_compatibility.py`. | [PR #6790](https://github.com/optuna/optuna/pull/6790); [rustuna](https://github.com/optuna/rustuna); [PFN release](https://www.preferred.jp/en/news/pr20260907) |

### Optuna's key questions answered

**Do intermediate values follow a resumed trial?** No. A trial is not resumed; it is re-run as a new trial.

- **Under heartbeat retry:** the original trial goes to FAIL (immutable). A new WAITING trial carries
  `retry_history`, and only optionally a **copy** of the old values.
- **What a third party sees:** N trials and N partial series. To recover runstate's "one history" it must
  re-join them by `retry_history`.
- **An undocumented route:** the storage contract would allow RUNNING→WAITING (only finished trials are
  frozen). Re-popping the same `trial_id` would then keep one identity. But no public API does it, and
  `tell` rejects non-RUNNING trials.

**Operator stop?** No first-class one. The nearest thin layer is a custom `BasePruner` that also reads an
operator flag from `study.user_attrs`. That is durable and cooperative, but every worker must call
`should_prune()`, and the flag carries no discharge semantics.

**Produce-on-miss, and "the step budget rose"?** Neither exists. Extending means minting a new trial,
because COMPLETE is frozen.

**Usable without the search loop?** Partly. Ask-and-tell plus `enqueue_trial` with fixed params lets a
foreign runner use Optuna as a store. The cost is losing the heartbeat, and you inherit a param-suggestion
API you don't need.

**JournalStorage vs runstate's no-service bet.** This is the closest *architectural* sibling in the family.

| | runstate (SQLite backend) | Optuna JournalStorage (file backend) |
|---|---|---|
| Unit per file | **one run** | **one storage** (all studies and trials, interleaved) |
| Record | an observation or request envelope `{seq, topic, name, request_id, body}` | a state mutation op (`SET_TRIAL_STATE_VALUES`, …) |
| Ordering | SQLite autoincrement seq | file append order under a lockfile |
| Claim | **CAS** (`expected_seq`), so the loser's record never enters the log | **append-then-fold**: the loser's RUNNING record is in the log and ignored on replay. v5 adds a pre-check to fix a gRPC false-ownership bug ([#6084](https://github.com/optuna/optuna/issues/6084), cited in the code). |
| Episodes / attempts | many episodes per run | none: one attempt per trial; retries are new trials |
| Liveness | heartbeat records in the log, dated, so a cold reader can age them | **none in the journal** |
| Readers | anything that reads SQLite | anything that parses JSONL; reads take no lock and skip a torn trailing line |
| NFS | documented caveat: on NFS "the birth-claim CAS can admit two winners" (`docs/implementers-guide.md` §5.1) | **designed for NFS**: an atomic symlink (NFSv2+) or `O_EXCL` create (NFSv3+) lockfile around each append, with `fsync`. The lock is force-broken after a 30 s `grace_period` with no mtime change; *interpretation*: that admits two writers if a lock holder stalls more than 30 s. |
| Observer creates the file? | no: `attach_channel` raises `RunNotFound` (`specs/channel-locators.md`) | **yes**: `JournalFileBackend.__init__` does `open(path, "ab")`. This is the observer-mutates bug runstate fixed with the locator split. |
| Second-language implementation | protocol + JSON Schemas published; none built | **Rustuna exists** (2026-09) and is cross-tested |

*Interpretation:* Optuna independently made runstate's bet, a log file beside the work that any reader can
replay. It shows the bet is viable at scale (PFN shipped a second implementation). But it aimed the bet at
a *study of single-attempt trials*. It has none of runstate's per-run apparatus: episodes, a liveness
record, control.

### Strongest case that Optuna (+ a thin layer) subsumes runstate

- **Coverage.** JournalStorage already gives the no-service file, NFS-safe-ish arbitration, a
  cross-language format, a mature viewer, and a pruning hook that is a cooperative safe point.
- **The layer would add only:**
  - a pruner that honours an operator flag (stop);
  - a heartbeat written as journal user-attr ops (liveness);
  - "resume" as "re-enqueue with the same params plus `inherit_intermediate_values`, keyed by a
    `user_attrs['rid']`" (identity);
  - a tiny `ensure` that looks up trials by that rid and enqueues if short (memoisation).
- **Gains for mycooc:** search samplers it doesn't have today, and an artifact store (`optuna.artifacts`)
  for translation's off-channel blobs.

### Strongest case that it doesn't

**The data model fights three of mycooc's needs:**

- Extend is forbidden: COMPLETE is immutable.
- Identity is split per attempt: retries are new trials.
- A series is one unnamed float per step: single-objective only, with no named metrics.

**The layer would rebuild most of runstate on top of a store whose invariants oppose it:**

- an episode join over `retry_history`;
- a per-rid series merge;
- an extend-by-clone;
- liveness in user attrs, since the journal has none;
- stop semantics with no discharge.

**Smaller costs:**

- A per-storage (not per-run) file means every reader replays every study's ops.
- The pruner is the worker's decision, not a recorded request.

**Adoption cost:** a schema migration of mycooc's per-run logs into trials, and a permanent impedance layer.
Net: it is a store to *borrow ideas from*, chiefly the NFS lockfile and the Rustuna-style
second-implementation test. It is not one to adopt.

---

## 2. Ray Tune + Ray Train (2.59.0)

**Summary.** Ray Tune is a driver-centric trial orchestrator.

- **The driver.** A single `TuneController` process schedules trials as Ray actors. Schedulers (ASHA, PBT,
  HyperBand) return `CONTINUE | PAUSE | STOP` on each reported result, and Stoppers can stop trials.
- **One trial across attempts.** A trial keeps its `trial_id` and directory across:
  - in-run failures (`FailureConfig(max_failures)`);
  - scheduler PAUSE→resume (a checkpoint, then a relaunch);
  - driver restarts (`Tuner.restore(path)`, which converts RUNNING→PENDING and resumes from the latest
    checkpoint).
- **Per-trial results.** Results go to a per-trial `result.json` (JSONL), opened in **append mode** and
  restored from remote storage on restart. So one file accumulates every attempt, and each line carries
  `iterations_since_restore`, `time_since_restore`, `pid`, `hostname` and `timestamp`.
- **Experiment state** is a *periodic snapshot* (JSON with cloudpickled fields).
- **Ray Train V2** (default in 2.59: `is_v2_enabled()` defaults to True) identifies a run by the
  **caller-chosen** `RunConfig(storage_path, name)`. It auto-resumes from the latest checkpoint, warns "If
  name is reused unintentionally, Ray Train will fetch the previous run state", and **no longer saves
  free-floating metrics**.

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **partial** (strong, but scoped to one experiment) | The same `trial_id`, directory and append-mode `result.json` across failure retries, PAUSE and `Tuner.restore`. The id is minted by Tune and is not caller-chosen, except at Train V2's run level via `name`. | [`trial.py`](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/experiment/trial.py) `__setstate__`; [`logger/json.py`](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/logger/json.py); [Tune FT](https://docs.ray.io/en/latest/tune/tutorials/tune-fault-tolerance.html); [Train FT](https://docs.ray.io/en/latest/train/user-guides/fault-tolerance.html) |
| 2 | Single-spawn | **partial** | Arbitrated by being the only driver. I found no cross-driver arbitration documented for `Tuner.restore` on a shared path `[unverified that none exists]`. | `Tuner.restore` docstring |
| 3 | Liveness | **partial** | Live: Ray actor failure detection, and Train V2 controller health checks (`HEALTH_CHECK_INTERVAL_S`). Cold third party: only the snapshot (period "auto", at least 10 s) and result timestamps. A dead driver leaves trials marked RUNNING on disk. | [`experiment_state.py`](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/execution/experiment_state.py); `train/v2/_internal/constants.py` |
| 4 | Terminal verdict; extend | **partial / conflicts** | PENDING / RUNNING / PAUSED / TERMINATED / ERROR. Docs: "Finished trials … will not be resumed", and "Tuner.restore is not meant for resuming a terminated experiment". `resume_errored` and `restart_errored` distinguish resuming from a checkpoint and restarting from scratch. | `Tuner.restore` docstring |
| 5 | Cooperative stop | **partial** | Stop and pause decisions come from in-driver Stoppers and schedulers, evaluated per result (that is, at the worker's report boundary). PAUSE checkpoints first. **The external client API (`TuneClient.stop_trial`) was removed in 2023-11** ([#41469](https://github.com/ray-project/ray/pull/41469)). A thin layer, a Stopper that polls a file, is possible but is not durable beyond the driver. | [`trial_scheduler.py`](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/schedulers/trial_scheduler.py); `tune_controller.py` |
| 6 | Per-step values | **covered** (Tune) / **absent** (Train V2 without Tune) | JSONL `result.json` per trial, flushed per result, readable live by anything. Lines are dated, and attempts are separable by `iterations_since_restore`/`pid`. Train V2: "Free-floating metrics are no longer automatically saved." | [`result.py`](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/result.py) `AUTO_RESULT_KEYS`; [Train V2 migration](https://github.com/ray-project/ray/issues/49454) |
| 7 | Memoisation / produce-on-miss | **absent** | No API to read a finished trial's series and extend it. | Tune and Train API |
| 8 | Demand / subscriptions | **absent** | Scheduler-driven. | — |
| 9 | Derived runs | **partial** | PBT "exploit" clones one trial's checkpoint into another. The lineage is logged by PBT `[file format unverified]`. | Ray PBT docs |
| 10 | Retention / GC | **partial** | `CheckpointConfig(num_to_keep)`. | Ray docs |
| 11 | Time | **covered** (results) | `timestamp`, `date`, `time_total_s` on every result, from the worker's wall clock. | `AUTO_RESULT_KEYS` |
| 12 | Write authority | **absent** | `Tuner.restore` warns: "Never restore from a path that other parties can write to" (pickle). | `Tuner.restore` docstring |
| 13 | Deployment | **runtime required** | A Ray runtime (a local cluster from `ray.init`, or a Ray cluster with GCS and raylets) plus a `storage_path` (local, NFS or S3). | Ray docs |
| 14 | Constraints on worker code | **conflicts** | Trials run as Ray actors launched by the Tune driver. Train V2 launches your loop through its controller. You adopt Ray's process model. | Train V2 migration guide |
| 15 | Interop | **partial** | `result.json` is plain JSONL. Experiment and trial state uses cloudpickle (non-JSON fields hex-pickled). | `trial.py` `_nonjson_fields` |

**Strongest case for subsumption.** Within one Tune experiment the identity is real:

- the same `trial_id`, a checkpoint resume, and one appended `result.json` per trial;
- a dated JSONL file that any reader can tail;
- PAUSE/resume as a first-class scheduler verb.

Train V2's `name`-keyed auto-resume is literally a caller-chosen, content-addressable run identity.

**Strongest case against.**

- **Wrong owner.** It owns the processes. mycooc spawns its own; translation uses runstate's launchers.
  Tune's identity lives in a driver's experiment directory: a TERMINATED trial cannot be extended, and no
  outside party can request a stop since #41469.
- **Fragile state.** State is a pickled snapshot, not a log.
- **Lost metrics.** Train V2 alone drops free-floating metrics.

**Adoption cost:** rewrite both consumers' launch paths onto Ray actors and operate a Ray runtime. That is
not a thin layer.

---

## 3. Determined (v0.38.1; dormant since March 2025)

**Summary.** Determined is a training platform: a Go master plus PostgreSQL (≥13), and agents, Kubernetes
or Slurm for managed tasks.

**Managed mode:**

- A trial restarts from its latest checkpoint up to `max_restarts`, with a `restarts` counter.
- `det experiment pause` delivers a **cooperative preemption** via
  `core_context.preempt.should_preempt()`. It is a long-poll, and the worker must **acknowledge** before
  exiting for the master to restart it later.
- `det e continue` (0.26.1, 2023-10) resumes a terminal single-searcher experiment **in place, "whether
  it previously succeeded or failed"**. It sets the trial to PAUSED, applies config overrides, and
  resumes from the checkpoint. That is: **extend a completed run**.

**Detached mode** ("detached mode v1 / core api v2", [#7060](https://github.com/determined-ai/determined/pull/7060), 2023-08-17) lets *any* process report into Determined:

- **Identity.** `core_v2.init(Config(external_experiment_id=…, external_trial_id=…))` → `PutTrial`
  (get-or-create by the caller's id) → `StartTrial(resume=True)`. That increments `restart_id`, sets
  RUNNING, and returns `LatestCheckpoint` and `StepsCompleted`.
- **Liveness.** A client heartbeat thread PATCHes the trial every 60 s. The master's `MarkLostTrials`
  marks unmanaged RUNNING trials whose `last_activity` is more than 5 minutes old as ERROR.
- **Metrics** are inserted with `trial_run_id`. On a restart, `rollbackMetrics` sets `archived = true` on
  earlier-run rows at or beyond the new step. Those rows are retained, but hidden from the default view.
- **Stop:** "At present, detached mode does not support preemption" (`DummyPreemptContext`).

| # | concern | rating | mechanism | source |
|---|---|---|---|---|
| 1 | Durable identity across attempts | **covered** | One trial id; `restart_id`/`trial_run_id` per attempt; `StartTrial` returns the latest checkpoint and `steps_completed`; one metric history with per-step rollback (archived, not deleted). Detached ids are caller-chosen. | [`trials/api_trials.go`](https://github.com/determined-ai/determined/blob/v0.38.1/master/internal/trials/api_trials.go) `StartTrial`; [`api_experiment.go`](https://github.com/determined-ai/determined/blob/v0.38.1/master/internal/api_experiment.go) `PutTrial`; [`db/postgres_trial_metrics.go`](https://github.com/determined-ai/determined/blob/v0.38.1/master/internal/db/postgres_trial_metrics.go) `rollbackMetrics`; [detached checkpoints tutorial](https://github.com/determined-ai/determined/blob/v0.38.1/docs/tutorials/detached-mode/save-load-checkpoints.rst) |
| 2 | Single-spawn | **covered** (managed) / **absent** (detached resume) | Managed: the master schedules allocations. Detached: `StartTrial` locks `FOR UPDATE` and checks `run_id == 0` only when `resume=false`. With `resume=true`, two concurrent processes both succeed. | `StartTrial` |
| 3 | Liveness | **covered** (server-side) | Managed: the master owns allocations. Detached: a 60 s heartbeat (`_HeartbeatReporter`) and `MarkLostTrials` at 5 minutes. A third party reads `state` and `last_activity` over REST. | [`core/_heartbeat.py`](https://github.com/determined-ai/determined/blob/v0.38.1/harness/determined/core/_heartbeat.py); [`trials/postgres_trials.go`](https://github.com/determined-ai/determined/blob/v0.38.1/master/internal/trials/postgres_trials.go) |
| 4 | Terminal verdict; extend | **covered** | ACTIVE / PAUSED / COMPLETED / CANCELED / ERROR. `ContinueExperiment` requires a terminal state, flips the trial to PAUSED, and resumes with the overridden config. Caveat: it zeroes `restarts` ("we somewhat lose information"). Detached `StartTrial(resume)` reopens any state. | `api_experiment.go` `ContinueExperiment`; [release notes](https://github.com/determined-ai/determined/blob/v0.38.1/docs/release-notes.rst) 0.26.1 |
| 5 | Cooperative stop | **covered** (managed) / **absent** (detached) | Pause, priority preemption, or the searcher's early stop → `should_preempt()` (long-poll, auto-ack) → checkpoint and exit. PAUSED is durable, and reactivation resumes. Detached: `DummyPreemptContext`. | [`core/_preempt.py`](https://github.com/determined-ai/determined/blob/v0.38.1/harness/determined/core/_preempt.py); [`core_v2/_core_context_v2.py`](https://github.com/determined-ai/determined/blob/v0.38.1/harness/determined/experimental/core_v2/_core_context_v2.py) |
| 6 | Per-step values | **covered** | `report_training_metrics`/`report_validation_metrics(steps_completed, {named metrics})` into Postgres, readable live via REST, CLI and WebUI. | [detached metrics tutorial](https://github.com/determined-ai/determined/blob/v0.38.1/docs/tutorials/detached-mode/simple-metrics-reporting.rst) |
| 7 | Memoisation / produce-on-miss | **partial** (read half only) | get-or-create by external id, plus latest checkpoint, plus metrics read, plus `continue` gives "read what exists" and "extend". There is no "launch a producer for what is missing". | as above |
| 8 | Demand / subscriptions | **absent** (removed) | `core.searcher.operations` was "train until length N", the nearest analogue to `ensure(until)`. It was deprecated, and the 0.38 searcher-context removal ([#10131](https://github.com/determined-ai/determined/pull/10131)) took it out: "Training code no longer requires `core.searcher.operations`". ASHA now uses `max_time` + `time_metric` plus preemption. *Interpretation:* this parallels runstate's measured "no consumer ever sent a subscription" — demand collapsed into "run to target, stop early". | release notes 0.38.0 |
| 9 | Derived runs | **partial** | Forked experiments and continued trials link to their parents (WebUI); warm start from a checkpoint. | release notes |
| 10 | Retention / GC | **covered** | Checkpoint GC policies and log-retention policies (enforced by the service). | release notes 0.38 |
| 11 | Time | **covered** | Master-clock `end_time`, `last_activity`, start and end times. | `postgres_trial_metrics.go` |
| 12 | Write authority | **covered** (service-enforced) | Authentication, access tokens, RBAC (EE), and `CanEditExperimentsMetadata` checks on every write. | `StartTrial` / `PatchTrial` |
| 13 | Deployment | **service** | A master plus PostgreSQL. Managed mode needs container runtimes (Docker / K8s / Slurm-EE). **Last release v0.38.1 (2025-03-19); no pushes since 2025-03-20** `[maintenance status otherwise unverified]`. | GitHub API |
| 14 | Constraints on worker code | managed: **conflicts** / detached: **minimal** | Managed tasks are containerised and launched by Determined. Detached needs only the Python client and network access to the master. | detached-mode docs |
| 15 | Interop | **partial** | A REST/gRPC API defined in protobuf with Swagger. Clients are generatable; there is no file format. | `proto/` |

**Strongest case for subsumption.** Determined's detached mode is close to a shipped version of
runstate's identity layer *for code Determined did not launch*:

- a caller-chosen (thus content-hashable) trial id;
- an attempt counter, and resume-from-latest-checkpoint by step;
- one named per-step history with latest-attempt-wins and full retention;
- a heartbeat with server-side lost-marking;
- in-place extension (`continue`);
- auth, GC and a WebUI.

A thin layer would add only three things:

- a claim (a CAS on a trial metadata field, or the advisory lock runstate already has);
- a stop flag polled by the worker;
- `ensure` over `PutTrial` + metrics + spawn.

For mycooc this removes the reclaim tool's job on the server side: `MarkLostTrials` fails a stale claim
everywhere.

**Strongest case against.**

- **Stop.** The one control mycooc measurably uses (`control.stop`, 37 real stops) is **unsupported in
  detached mode**. It works only for Determined-launched, containerised trials.
- **Single-spawn.** It is absent on the detached resume path.
- **Service.** It is the opposite of the bet: every metric write needs network access to a master, and
  offline buffering is `[unverified]`. A cold reader needs the API, not a file.
- **Dormancy.** No release for 18 months.

**Adoption cost:** operate a master and Postgres indefinitely for a dormant project, and write the claim,
stop and `ensure` layers anyway. *Interpretation:* the semantics are the strongest available prior art.
The vehicle is a liability.

---

## 4. Brief entries

### Kubeflow Katib

Kubernetes CRDs (Experiment, Suggestion, Trial).

- **Metrics.** Collected by a sidecar that parses stdout (default regex `name=value`) or files, or pushed
  with `report_metrics()` to a DB manager.
- **Early stop is not cooperative.** When the rules are met, the file metrics-collector writes an
  "early-stopped" mark file and **`Terminate()`s the training process**
  ([`file-metricscollector/main.go`](https://github.com/kubeflow/katib/blob/master/cmd/metricscollector/v1beta1/file-metricscollector/main.go)).
- **`resumePolicy` resumes the Suggestion (the search), not trials.**
- **"Done" can be reopened.** A Succeeded experiment can be extended by raising `maxTrialCount`
  ([resume docs](https://www.kubeflow.org/docs/components/katib/user-guides/resume-experiment/)), so
  "done" is revisable at the search level.
- **No trial identity across attempts** beyond Kubernetes Job retries.
- **Requires Kubernetes**, which positioning.md already rejects.

Adds nothing positioning missed, except the stdout-line metric contract (see Syne Tune).

### Syne Tune (v0.16.0, active)

The `LocalBackend` spawns **arbitrary scripts** as subprocesses.

- **Worker contract.** The worker reports by printing `[tune-metric]: {json}` to stdout, appended to
  `<trial>/std.out` ([`report.py`](https://github.com/syne-tune/syne-tune/blob/main/syne_tune/report.py)).
  This is a language-agnostic line protocol, and the per-trial file accumulates every attempt.
- **Pause and resume.** Promotion-based schedulers pause (by **killing the process tree**: `_kill_process`)
  and resume the *same* `trial_id` with the same `st_checkpoint_dir`.
- **Derived runs.** `start_trial(checkpoint_trial_id=…)` copies another trial's checkpoint.
- **Driver recovery.** The tuner pickles itself (`tuner.dill`) every 10 s for resumption.

**Better than runstate:** a trivially implementable worker contract (print a line).

**Worse:** stop is SIGKILL; there is no liveness record; the driver owns spawning; no extend and no
memoisation. Source:
[`local_backend.py`](https://github.com/syne-tune/syne-tune/blob/main/syne_tune/backend/local_backend.py),
[FAQ](https://syne-tune.readthedocs.io/en/latest/faq.html).

### submitit (1.5.4)

- **Requeue.** On the Slurm preemption or timeout signal (USR2), `checkpoint_and_try_requeue` calls the
  callable's `checkpoint()`, pickles the returned `DelayedSubmission`, and runs `scontrol requeue`. The
  **same Slurm job id** restarts with that pickled state (`submitit/core/job_environment.py`,
  `submitit/helpers.py:Checkpointable`). That is identity across attempts at the job level.
- **Absent:** per-step values, a stop request beyond `scancel`, and a verdict beyond Slurm/sacct state
  plus the pickled result.
- **Fit.** A launcher, complementary to runstate's `Launcher` protocol rather than an alternative. The
  Hydra submitit launcher wraps it the same way.

### Hydra (v1.3.7)

- **Model.** The `Launcher.launch(job_overrides, initial_job_idx) -> Sequence[JobReturn]` interface, with
  `JobStatus ∈ {UNKNOWN, COMPLETED, FAILED}`
  ([`plugins/launcher.py`](https://github.com/facebookresearch/hydra/blob/main/hydra/plugins/launcher.py),
  [`core/utils.py`](https://github.com/facebookresearch/hydra/blob/main/hydra/core/utils.py)). The model
  is "run these jobs, return results". No persistent job state is part of the interface.
- **Resume.** Resuming a sweep is delegated to the sweeper's own store (e.g. the Optuna sweeper's
  storage) or to submitit requeue.
- **Fact from the consumer repos (read-only grep):** both mycooc (`main.py`, `run_identity.py`) and
  translation (`conf/`) import Hydra for **config composition**. Neither imports Optuna, Ray, Determined,
  submitit, W&B or MLflow.
- **Fit.** Hydra sits *above* runstate (config → rid), not beside it.

### MLflow / W&B: what positioning.md missed

**MLflow.**

- The default backend is now a **local SQLite file** (`sqlite:///mlflow.db`), usable with no server
  ([backend-store docs](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/)). The
  claim that "all chose the service model" is outdated for MLflow.
- `start_run(run_id=…)` resumes logging into an existing run.
- Run ids are minted by the store: `MlflowClient.create_run(experiment_id, start_time, tags, run_name)`
  takes no id ([`tracking/client.py`](https://github.com/mlflow/mlflow/blob/master/mlflow/tracking/client.py)).
  Content addressing therefore needs a tag search.
- There is still no stop control.

**W&B** (docs now at docs.coreweave.com) has more than "effectively no control plane":

- **Caller-chosen run id resume:** `wandb.init(id=…, resume="must"|"allow")` "resumes from last step"
  ([resuming](https://docs.coreweave.com/guides/runs/resuming)).
- **Rewind:** `resume_from="<id>?_step=N"` keeps the run id and **truncates history to N**, archiving the
  original (preview; SDK ≥ 0.17.1). **Fork:** `fork_from` ([rewind](https://docs.coreweave.com/guides/runs/rewind)).
- **A UI stop.** The client polls every 15 s, and "'Stopping' a run means killing its script"
  ([`run_stopping.py`](https://github.com/wandb/wandb/blob/main/wandb/sdk/lib/run_stopping.py)). It is a
  control, but non-cooperative and live-only. The run state becomes `Killed`.
- **`mark_preempting()`**, so a sweep agent requeues the run.
- **A local file the server only consumes.** Every run writes a local `.wandb` **transaction log**: a
  LevelDB-format log of protobuf records created with `O_EXCL`
  ([`core/internal/transactionlog/writer.go`](https://github.com/wandb/wandb/blob/main/core/internal/transactionlog/writer.go)).
  **`wandb leet`**, a terminal UI, reads it live
  ([`wandb/cli/leet.py`](https://github.com/wandb/wandb/blob/main/wandb/cli/leet.py)). This is a
  file-beside-the-run plus a TUI viewer, though the format is an internal proto, not a published
  contract. LEET ships in the same binary as the producer.

---

## 5. Across the family

### Coverage matrix

Key: C = covered, P = partial, A = absent, X = conflicts.

| concern | Optuna | Ray Tune/Train | Determined | Katib | Syne Tune | submitit | W&B | runstate |
|---|---|---|---|---|---|---|---|---|
| 1 identity across attempts | P (lineage of trials) | P (per experiment) | **C** | A | P | P (job id) | P (resume, rewind) | C |
| 2 single-spawn | C (single-attempt) | P (one driver) | C managed / A detached | C (k8s) | P (driver) | C (Slurm) | A | C (CAS) |
| 3 liveness, cold third party | P (RDB, via SQL) | P | **C** (server) | P | A | P (sacct) | P (server) | C (dated records) |
| 4 verdict + extend completed | X (frozen) | X | **C** (`continue`) | P (search level) | A | A | P (resume or rewind) | C |
| 5 durable cooperative stop | P (pruner hook) | P (in-driver) | C managed / A detached | A (SIGTERM) | A (SIGKILL) | A | A (kill) | C |
| 6 per-step values, live | P (one float) | C | **C** | C | C (stdout) | A | C | C |
| 7 produce-on-miss by content id | A | A | P (read half) | A | A | A | A | C |
| 8 reader-driven demand | A | A | A (removed) | A | A | A | A | C (unused) |
| 9 derived runs | A | P (PBT) | P | A | P | A | P (fork) | recipes |
| 10 retention / GC | A | P | **C** | P | A | A | C (service) | in-log + recipe |
| 11 dating by a cold party | P | C | C | C | P | P | C | C |
| 12 enforced write authority | P (finished = fence) | A | **C** (auth) | C (k8s RBAC) | A | A | C | recorded only |
| 13 no service | **C** (journal file) | X (Ray runtime) | X (master + PG) | X (k8s) | C | C | P (local log) | C |
| 14 foreign runner | P (ask/tell) | X | C detached | X | X | C | C | C |
| 15 implementable wire format | P (de facto, two impls) | P (JSONL results) | P (proto API) | P (stdout regex) | **C**-ish (one line) | A (pickle) | A (internal proto) | C (JSON Schema) |

### Which system comes closest

**Determined detached mode** on what the run *is*. It covers concerns 1, 3, 4 (with extend), 6, 10, 11 and
12. It misses:

- 2: single-spawn on resume;
- 5: stop for foreign workers;
- 7: produce-on-miss;
- 13: it is a service;
- and it is dormant.

**Optuna JournalStorage** on how the record is *kept*. It covers 13, partially 15, and 2 for one attempt.
Its model fails 1, 4 and 5.

### Concerns no system in the family covers

1. **Produce-on-miss keyed by content-addressed identity (C7).** "Loss of config C up to step N" means read
   the existing values, then launch or extend a producer for the rest. Nobody has it. The nearest:
   - Determined's removed searcher operations (demand to a length);
   - Optuna's `skip_if_exists` (param dedup, racy).
2. **A durable cooperative stop delivered to a worker the system did not spawn (C5),** with the request
   surviving the worker being down and discharged by the next stop report. Determined has exactly this
   protocol (should_preempt plus ack, PAUSED durable), but only for workers it launched.
3. **Single-spawn for a long-lived, many-attempt identity with no service (C2 × C13).** Optuna arbitrates
   claims in a file, but only for single-attempt trials. Determined has multi-attempt identity but no
   claim on detached resume.
4. **Reader-driven demand / subscriptions (C8).** Absent everywhere. Determined *removed* its nearest
   form.
5. **A published, versioned per-run schema as the interop contract (C15).** Optuna's journal is a de facto
   format with two implementations but no schema or version field. W&B's local log is an internal proto.

### What these systems do better (worth borrowing or noting)

- **NFS claims (Optuna).** An `O_EXCL`/symlink lockfile around each append, plus fsync, plus readers that
  skip a torn trailing line. This is a shipped answer to runstate's documented NFS gap: "the birth-claim
  CAS can admit two winners" (`docs/implementers-guide.md`). Caveat (*interpretation*): the 30 s
  forced-release lease reintroduces a stall assumption.
- **A finished trial as a write fence (Optuna).** It is enforced, so zombie writes fail loudly. It
  conflicts with runstate's extend, so the lesson is about *where* fencing is possible, not a
  recommendation.
- **Metric rollback with archival (Determined).** It is the same projection runstate's `value_series`
  uses (take-the-latest per step, full retention), arrived at independently. It corroborates the design.
- **The preemption ack (Determined).** "The task must acknowledge the preemption signal … for the task to
  be restarted". It is a server-side analogue of runstate's stop discharge.
- **A second implementation (Optuna).** Rustuna, cross-tested on the same journal files, is the kind of
  conformance evidence runstate's "other-language implementations welcome" has not produced.
- **The stdout line contract (Syne Tune, Katib).** The cheapest possible worker contract in any language.
- **Server-clock heartbeats (Optuna RDB, Determined).** The heartbeat is dated by one clock.

### Implications for `docs/positioning.md`

Factual, with sources above:

- "What does not exist elsewhere is the identity" should be retracted or narrowed. **Determined**
  (2023-08 onward, detached mode with `external_trial_id`, `restart_id`, latest-checkpoint resume and a
  rollback metric history) is a counterexample. Ray Tune and W&B are partial ones.
- The defensible claim is the **conjunction**: per-run identity *as a file-format protocol* (no service),
  plus a durable cooperative control plane for workers you did not spawn, plus `ensure`.
- The MLflow/W&B row is outdated in three places:
  - MLflow defaults to a local SQLite file;
  - W&B writes a local transaction log that a local TUI reads live;
  - W&B has a (kill-style) stop and resume or rewind by caller-chosen run id.
- The table should gain rows for **Determined** (closest semantics, service), **Optuna JournalStorage**
  (closest bet, different unit) and **Ray Tune** (identity inside a driver).

### Optional: redesign-relevant (`docs/backlog/if-built-today/`)

Noted in passing, not searched for:

- **Told negative facts:**
  - Ray `Searcher.FINISHED` ("no more suggestions/configurations will be provided");
  - Katib/Determined experiment completion;
  - Katib's *revisable* done (raise `maxTrialCount`).
- **Demand derived by rules:** ASHA/HyperBand promotion (a trial's continuation is demanded by a rule over
  other trials' values). Determined's searcher operations were the explicit wire form, now removed.
- **Questions with quantifiers:** none seen.

---

## 6. Sources

**Optuna**

- source at v5.0.0: [storages/](https://github.com/optuna/optuna/tree/v5.0.0/optuna/storages) (`_base.py`, `_heartbeat.py`, `_callbacks.py`, `_rdb/storage.py`, `journal/_storage.py`, `journal/_file.py`)
- [study/study.py](https://github.com/optuna/optuna/blob/v5.0.0/optuna/study/study.py)
- [trial/_trial.py](https://github.com/optuna/optuna/blob/v5.0.0/optuna/trial/_trial.py)
- [v5.0.0 release](https://github.com/optuna/optuna/releases/tag/v5.0.0)
- [PR #6790](https://github.com/optuna/optuna/pull/6790)
- [FAQ](https://optuna.readthedocs.io/en/stable/faq.html)
- [Journal tutorial](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/011_journal_storage.html)
- [storages reference](https://optuna.readthedocs.io/en/latest/reference/storages.html)
- [pytorch_checkpoint.py](https://github.com/optuna/optuna-examples/blob/main/pytorch/pytorch_checkpoint.py)
- [optuna-dashboard](https://github.com/optuna/optuna-dashboard) (README; `optuna_dashboard/_storage_url.py`)
- [Rustuna](https://github.com/optuna/rustuna)
- [PFN announcement](https://www.preferred.jp/en/news/pr20260907)

**Ray** (2.59.0)

- source: [tune/experiment/trial.py](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/experiment/trial.py), [tune/logger/json.py](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/logger/json.py), [tune/tuner.py](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/tuner.py), [tune/result.py](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/result.py), [tune/schedulers/trial_scheduler.py](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/schedulers/trial_scheduler.py), [tune/execution/experiment_state.py](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/execution/experiment_state.py), [tune/search/searcher.py](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/tune/search/searcher.py), [train/v2/_internal/constants.py](https://github.com/ray-project/ray/blob/ray-2.59.0/python/ray/train/v2/_internal/constants.py)
- [Tune fault tolerance](https://docs.ray.io/en/latest/tune/tutorials/tune-fault-tolerance.html)
- [Train fault tolerance](https://docs.ray.io/en/latest/train/user-guides/fault-tolerance.html)
- [Train V2 migration #49454](https://github.com/ray-project/ray/issues/49454)
- [legacy client removal #41469](https://github.com/ray-project/ray/pull/41469)

**Determined** (v0.38.1)

- master: [trials/api_trials.go](https://github.com/determined-ai/determined/blob/v0.38.1/master/internal/trials/api_trials.go), [trials/postgres_trials.go](https://github.com/determined-ai/determined/blob/v0.38.1/master/internal/trials/postgres_trials.go), [api_experiment.go](https://github.com/determined-ai/determined/blob/v0.38.1/master/internal/api_experiment.go), [db/postgres_trial_metrics.go](https://github.com/determined-ai/determined/blob/v0.38.1/master/internal/db/postgres_trial_metrics.go)
- harness: [core/_preempt.py](https://github.com/determined-ai/determined/blob/v0.38.1/harness/determined/core/_preempt.py), [core/_heartbeat.py](https://github.com/determined-ai/determined/blob/v0.38.1/harness/determined/core/_heartbeat.py), [experimental/core_v2/](https://github.com/determined-ai/determined/tree/v0.38.1/harness/determined/experimental/core_v2)
- docs: [detached-mode tutorials](https://github.com/determined-ai/determined/tree/v0.38.1/docs/tutorials/detached-mode), [release notes](https://github.com/determined-ai/determined/blob/v0.38.1/docs/release-notes.rst), [Core API guide](https://github.com/determined-ai/determined/blob/v0.38.1/docs/model-dev-guide/api-guides/apis-howto/api-core-ug-basic.rst)

**Katib**

- [early stopping](https://www.kubeflow.org/docs/components/katib/user-guides/early-stopping/)
- [resume](https://www.kubeflow.org/docs/components/katib/user-guides/resume-experiment/)
- [metrics collector](https://www.kubeflow.org/docs/components/katib/user-guides/metrics-collector/)
- [file-metricscollector/main.go](https://github.com/kubeflow/katib/blob/master/cmd/metricscollector/v1beta1/file-metricscollector/main.go)

**Syne Tune**

- [report.py](https://github.com/syne-tune/syne-tune/blob/main/syne_tune/report.py)
- [backend/local_backend.py](https://github.com/syne-tune/syne-tune/blob/main/syne_tune/backend/local_backend.py)
- [backend/trial_backend.py](https://github.com/syne-tune/syne-tune/blob/main/syne_tune/backend/trial_backend.py)
- [FAQ](https://syne-tune.readthedocs.io/en/latest/faq.html)

**submitit** (local 1.5.4): `submitit/helpers.py`, `submitit/core/job_environment.py`

**Hydra**

- [plugins/launcher.py](https://github.com/facebookresearch/hydra/blob/main/hydra/plugins/launcher.py)
- [hydra_submitit_launcher](https://github.com/facebookresearch/hydra/blob/main/plugins/hydra_submitit_launcher/hydra_plugins/hydra_submitit_launcher/submitit_launcher.py)

**MLflow**: [backend store](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/)

**W&B**

- docs: [resuming](https://docs.coreweave.com/guides/runs/resuming), [rewind](https://docs.coreweave.com/guides/runs/rewind), [stop runs](https://docs.coreweave.com/models/runs/stop-runs.md)
- source: [sdk/lib/run_stopping.py](https://github.com/wandb/wandb/blob/main/wandb/sdk/lib/run_stopping.py), [core/internal/transactionlog/writer.go](https://github.com/wandb/wandb/blob/main/core/internal/transactionlog/writer.go), [cli/leet.py](https://github.com/wandb/wandb/blob/main/wandb/cli/leet.py)

**runstate (local)**: `docs/positioning.md`, `docs/implementers-guide.md` §5.1 (NFS caveat), `docs/backlog/index.md`, `CLAUDE.md`
