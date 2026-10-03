# Falsification prototype: runstate's mycooc run shape over DBOS Transact (SQLite)

Date: 2026-10-02. Everything below was measured by running code. The code is in `scratchpad/dbos-prototype/`:
`rs_dbos.py` is the layer, `sim.py` the simulated training (user code), `worker.py` the CLI, `tests/b*.py` the
behaviour scripts, `out_*.txt` the final run of each, and `run_all.sh` reruns them. The repo was not modified.

**Hypothesis under test** (an inference from the prior-art survey): *"with current state kept in DBOS's mutable rows
instead of derived from an append-only log, runstate shrinks to a few hundred lines of conventions over DBOS:
workflow id = run identity, steps = training chunks (checkpoint + safe point), durable `recv` = cooperative stop,
streams = per-step values, fencing and single-spawn for free; the layer adds liveness, a verdict vocabulary and
`ensure`."*

## 1. Version and size

- **DBOS Transact `dbos==3.2.0`** from PyPI, in its own venv (`dbos-prototype/venv`, Python 3.12.9). It pulled
  SQLAlchemy 2.1.3. The SQLite system database is at schema migration **123**. No Postgres, no server, no
  Conductor.
- **Layer: `rs_dbos.py` has 251 lines in total and 197 non-blank, non-comment lines** (12 of those are docstring
  lines). The tests, `sim.py` (57 lines, user code) and `worker.py` (22 lines, CLI) are excluded. By concern:

| section | lines | what it is |
|---|---|---|
| imports, config, identity, segment listing | 35 | content-hash `rid`; DBOS ids `"<rid>.<k>"` |
| liveness | 30 | beacon table, beacon thread, staleness rule |
| stop plumbing | 10 | raw SQL over `notifications`: peek, and consume on behalf of a finished workflow |
| workflow skeleton + steps | 34 | deterministic chunk loop; `_drain` of stops at each safe point |
| worker entry `run()` + `_launch` | 38 | segment selection, conditional takeover, stop forwarding |
| third party | 29 | `stop`, `values`, `verdict`, `is_alive` (DBOSClient only) |
| consumer `ensure` | 21 | read first, spawn a worker on a miss, no-progress guard |

The layer's design, chosen after reading DBOS's source:

- **one DBOS workflow per segment.** A *segment* is a DBOS workflow `"<rid>.<k>"`; the run is the union of its
  segments.
- **the training loop runs inside DBOS steps.** Each step is a chunk: it trains from the on-disk checkpoint to
  `end`, writing one stream value per training step before checkpointing.
- **stop is a DBOS message.** It is read by `recv(timeout=0)` between chunks.
- **liveness is a beacon table in the same SQLite file.**
- **every executor id is unique.**
- **the application version is pinned.**
- **the serializer is portable JSON, set globally.**

## 2. Results

| | behaviour | result | evidence (from `out_*.txt`) | what DBOS provided / what the layer had to build |
|---|---|---|---|---|
| B1 | single-spawn | **PASS** under the layer's config. **Two failures off it** | Pre-migrated DB: 5/5 concurrent fresh starts and 5/5 concurrent takeovers of a dead run had exactly one trainer (checkpoint-writer pids). **Cold DB: 5/5 rounds, the second process crashed in `DBOS.launch()`** with `table workflow_status already exists` (first-launch migration race). **DBOS default executor id (`local`): 5/5 rounds, launching a worker for an *unrelated* run displaced the live run's execution** ("no longer owned by this execution"), which the queue then re-dispatched. | DBOS: the primary key on `workflow_uuid` (a second start returns a handle and does not execute), and a conditional re-enqueue (`reenqueue_for_recovery … WHERE executor_id IN (dead)`) that only one taker can win. Layer: unique executor ids, which turns DBOS's own crash recovery off; a liveness gate; takeover through the private `DBOS._recover_pending_workflows([dead_executor])`. The public `resume_workflow` is unconditional and would race. Not solved: first creation of the DB file (the tests pre-migrate). |
| B2 | displaced writer | **PARTIAL** | All 7 rounds and 3 takeover routes (layer recovery after a stale beacon; DBOS same-executor startup recovery; operator `DBOSClient.resume_workflow`): **A's database writes after SIGCONT were all rejected** (`owner_xid` fencing). **A's checkpoint-file write landed in 7/7.** When B had already finished, the **final `ckpt.json` regressed from step 30 to step 6**, and the next `ensure(config, 40)` **recomputed steps 6..29**. A *learns* only through a WARNING log line. Its `run()` then **returns B's result as if it were its own** (`RESULT {"final_step": 30}`). | DBOS: fencing of step outputs, stream appends and status writes. The conflict is a `BaseException`, so user code does not see it and the execution "parks" until the new owner's outcome arrives. Layer: nothing. The artifact plane, the checkpoint that *is* the run's state in this shape, is not covered by fencing, and the layer cannot cover it without fencing the user's file writes. |
| B3 | crash and resume | **PASS** | 5/5 rounds resumed exactly at the checkpoint step, and `values()` was contiguous 0..39. The raw stream held a **duplicate step in 1/5 rounds** (kill inside the value→checkpoint window, step 13), which only the layer's take-latest read hides. **Record of two attempts:** DBOS keeps `recovery_attempts=2` and *overwrites* `executor_id` with the new owner's. The interrupted chunk leaves no step row, and the re-executed chunk's row carries the second attempt's start time only. The layer's beacon rows `(wid, pid)` and the `pid` inside each value are the only per-attempt trace. | DBOS: replay of recorded chunks, then the in-flight chunk re-executes from the worker's own checkpoint. Layer: the liveness-gated takeover (above), take-latest dedupe, per-value `pid`. |
| B3b | *(extra)* resume after a code change | **FAIL** | A ran under `application_version` v1 and was killed. B under v2 **hung for 15 s, trained nothing**, and the row stayed `PENDING` v1. DBOS recovers and dequeues only same-version work. The default version is an md5 of the workflow sources **plus the DBOS library version**, so a `pip install -U dbos` strands every PENDING run. | Layer: pins the version (`"v1"`). That trades stranding for DBOS replaying recorded steps against edited code. |
| B4 | stop while down | **PARTIAL** (2 of 5 variants pass, both with layer-built SQL) | **crash** (a dead PENDING segment): the next worker honoured the stop **only after re-training steps 14..19.** Replay re-takes the recorded "no stop" decision, so the first new safe point comes after the interrupted chunk. **crash + layer peek: PASS** (0 steps). **clean** (finished segment, stop sent to it): PASS **only because the layer forwards** the unconsumed message (raw SQL, `consumed_by_function_id=-1`) into the next segment. DBOS alone would leave it unconsumed on a finished workflow forever. **never started:** `stop()` is impossible, because a message needs an existing destination (foreign key). **DBOS cancel** as the stop: status is `CANCELLED` at once, and the next start resumed and trained 26 steps to the end, so **the stop was lost**. In every passing variant, verdict=preempted and the later start was not re-stopped. | DBOS: durable messages with a `consumed` flag, delivered to a recovered PENDING workflow. Layer: drain-all at each safe point; cross-segment forwarding; in-step peek. The last two read and write DBOS's `notifications` table directly, because no API reads or consumes another workflow's messages. |
| B5 | extend after completion | **PASS**, via a new identity | Ran to 10 (`<rid>.0` SUCCESS). **Natively:** starting `<rid>.0` again with `until=20` **silently returned `{"final_step": 10}`** and the stored inputs stayed `until=10`. `resume_workflow` on SUCCESS was a no-op. `fork_workflow` made a new uuid id **with the original inputs** and returned 10. **Layer:** `ensure(config, 20)` started segment `<rid>.1`, trained only 10..19 from checkpoint 10, and returned 0..19. Stream rows sit in `<rid>.0` (0..9) and `<rid>.1` (10..19). | Identity: run = `rid` (layer); DBOS id = `rid.1`, **a new workflow id per extension**. Layer: segments, stitching across ids, next-segment start. |
| B6 | ensure reads first | **PASS** | `ensure(config, 15)` with 0..19 present: **0 launches, no row changes, no checkpoint writes, 7 ms.** | DBOS: stream reads through `DBOSClient` (no launch, so no queue pickup). Layer: `ensure`, and progress = contiguous steps (assumes a dense emitter). |
| B7 | cold liveness | **PASS, entirely layer-built** | A separate observer process given only the file: **DBOS status `PENDING` for both runs at every observation, and DBOS never refreshed `updated_at` while running** (both aged 1.44 s, then 6.95 s and 6.96 s). The layer reported the killed run not-alive at **kill + 3.55 s** (stale threshold 3 s, beat every 0.5 s). The live run was alive at all 8 observations. | DBOS: nothing. A long step is invisible until it completes; the step-row count was the only thing that moved. Layer: beacon table + staleness rule + a claim grace window from the row's `updated_at`. |
| B8 | third-party read without DBOS | **PARTIAL** | With the layer's global portable-JSON serializer, `/usr/bin/python3` (where `import dbos` fails) read all 12 values, status, output and beacon with `sqlite3`+`json`. **With DBOS defaults, every value column (workflow output, step outputs, stream values, events) is base64 `py_pickle`.** A per-workflow `serialization_type=PORTABLE` is not enough: in the smoke test, step outputs and `recv` results stayed `py_pickle`. Stream rows have **no time column**, so the layer embeds `t`. The "System Tables" doc page documents the **Postgres** schema (`dbos.` prefix) and promises stability only for its SQL *functions*, not the tables. DBOS's own launch log says SQLite is *"for development and testing. PostgreSQL is recommended for production use."* | DBOS: a documented, migration-versioned schema, readable when JSON is configured. Layer: the global serializer choice and embedded timestamps. |
| B9 | stop inside a long step | **PARTIAL** | One DBOS step = 50 training steps × 0.1 s; stop sent about 1 s in. **(a) native `recv`: 40 more steps, 4.09 s**, landing only at the step boundary. **(b) layer peek: 1 step, 0.07 s.** **(c) one DBOS step per training step: 1 step, 0.06 s, at a cost of 38 `operation_outputs` rows for 11 training steps** (about 3.5 rows per step). **(d) `cancel_workflow`: status `CANCELLED` before the worker stopped**; the step aborted at its next fenced stream write (0 more steps), and the **worker process exited with `DBOSAwaitedWorkflowCancelledError`**. **(e) `preemptible=True` async step:** aborted in 0.05 s by the fenced write, or in 0.82 s by `asyncio.CancelledError` with no writes. Either way it fires at an arbitrary `await`, and the step is not recorded. | DBOS: messages read between steps only; cancel and preemption are pre-emptive and record a verdict. Layer: the in-step peek (raw SQL) to get a cooperative safe point inside a chunk. |

Corrections to the survey's DBOS claims:

- **Cancel reaches inside a synchronous step.** It does so whenever that step writes to DBOS: a stream append from a
  step is fenced, so the step aborts at its next write instead of finishing. The survey said cancel lands at the
  next step boundary.
- **Shared-executor-id recovery steals live runs.** "Recovery requires a stable executor ID per host" is true, and on
  one host that same mechanism takes over runs that are still live (B1d).

## 3. Every semantic change and every piece of added machinery, and the runstate mechanism it re-creates

| # | what the layer had to do | semantic change? | re-creates |
|---|---|---|---|
| 1 | **Segments:** a new DBOS workflow id `<rid>.<k>` whenever the previous one is terminal. A finished workflow's inputs are frozen (a restart silently returns the old result), and replay re-takes recorded decisions, so a stopped workflow can never continue. | **Yes: workflow id ≠ run identity.** The run is a layer concept spanning ids. | **Episodes** (`run-episodes.md`): one identity, many attempts, extendable after completion. |
| 2 | `values()` stitches each segment's stream and keeps the latest value per step. Steps are at-least-once, so the raw stream carries duplicates (B3). | Reads are a fold, not a row lookup | `value_series`, the per-(name, step) take-the-latest register. |
| 3 | Beacon table `rs_beacon(wid, pid, host, t)` written every 0.5 s by a thread, plus a staleness rule, plus a grace window from `updated_at`. | Adds a table to DBOS's file | **Liveness tier 4** (heartbeat staleness), with the **observer clock** (`Heartbeat.t`, `observer-clock.md`) so that a cold reader can age it. |
| 4 | Unique executor ids, which disables DBOS's same-host recovery; takeover only when the beacon is stale, through the private `_recover_pending_workflows([dead_executor])`. | **Yes: DBOS's own recovery is turned off.** Its default steals live runs (B1c/d). | The **worker self-claim** plus a liveness-gated relaunch (`relaunch_if_needed`, `foreign_episode`). |
| 5 | Stop = a message; `_drain()` consumes **all** pending stops at each safe point and at halt. | Stop is a request, not DBOS's `CANCELLED` verdict | **Stop-discharge** ("any `stopped` answers every pending stop"). |
| 6 | Forwarding: unconsumed stops on a finished segment are marked consumed by raw SQL, and the next segment starts with `stop_first`. Not atomic: a crash between the two re-stops. | **Yes:** writes DBOS-internal state the API does not expose | **Stop-discharge across episodes** (a stop pending until the *next* `stopped`). |
| 7 | In-step stop peek: raw SQL on `notifications` from inside a step. | Reads DBOS internals | The Worker's **per-tick control drain**. |
| 8 | Verdict mapping: PENDING → running / presumed_dead by beacon; SUCCESS, CANCELLED and ENQUEUED → preempted; ERROR → errored. A `stopped` bit sits in the workflow output. | **Yes: DBOS says SUCCESS for a stop**, and `CANCELLED` is written before anything stopped | The closed **`RunResult.outcome`** projection. Commandedness is a stored bit here; runstate derives it from the `control.stop` record. |
| 9 | `t` and `pid` inside each stream value (stream rows have no time and no writer). | Payload convention | `Value.t` (observer clock) and provenance. |
| 10 | `read_stream(timeout_seconds=0.05)` to snapshot a non-terminal workflow's stream. A plain read blocks until the workflow ends. | Workaround | The non-blocking `read`. |
| 11 | `ensure` progress = contiguous values from 0 (a dense-emitter assumption). | Conflates progress with content | `ensure` Decision 2 keeps the dense heartbeat step apart from content. |
| 12 | Pinned `application_version`. | **Yes:** DBOS's code-change safety is traded for not stranding runs (B3b) | No runstate counterpart. runstate has no coupling to the code version. |
| 13 | Global portable-JSON serializer. | Config | runstate's JSON-only `protocol/` schemas. |
| 14 | *Not built, but needed:* serialising first creation of the DB file. Concurrent first launches crash (B1). | | `create_channel` open-or-create birth. |

## 4. Verdict

**The hypothesis fails as stated, and a weaker form holds with named gaps.**

- **The weaker form holds.** About 200 lines over DBOS do provide the mycooc behaviours: single-spawn, crash
  resume, extend-after-completion, read-first `ensure`, cold liveness, and a JSON file readable with `sqlite3`.
- **Four behaviours remain only partial even with the layer:**
  - the artifact plane under displacement (B2);
  - stop after a crash without the SQL peek (B4);
  - stop before the first start (B4);
  - schema stability (B8).
- **One extra behaviour fails:** resume after a code or DBOS-version change, unless the version is pinned (B3b).
- **The stated mapping is wrong.** It says DBOS supplies identity, stop, single-spawn and fencing, leaving the layer
  only liveness, a verdict vocabulary and `ensure`. Three of the five "for free" items needed layer machinery, and
  that machinery is runstate's own mechanisms rebuilt.

The three most important reasons:

1. **A DBOS workflow id cannot be the run identity.**
   - A finished workflow is closed: restarting the id with a raised target **silently returns the old result**
     (verified), `resume` on SUCCESS is a no-op, and `fork` keeps the old inputs.
   - Replay is deterministic, so a workflow that recorded "stop received" stops again on every resume.
   - Both extend-after-completion and continue-after-stop therefore need a **new workflow id per episode**.
   - The layer then has to stitch values across ids and forward pending stops across ids: that is **episodes and
     stop-discharge rebuilt**, and the forwarding step reaches past DBOS's API into its tables.
2. **DBOS has no liveness, and its recovery is keyed on executor identity, not on death.**
   - A live run and a `kill -9`ed one are both `PENDING`, with `updated_at` frozen.
   - With the default executor id, **starting any worker on the host displaces every live run's execution** (5/5).
   - Single-spawn is "free" only after the layer **turns DBOS recovery off** (unique executor ids) and rebuilds a
     heartbeat, a staleness detector and a conditional takeover. That is runstate's liveness tiers and self-claim.
3. **Fencing covers the database, not the run's state.**
   - In mycooc's shape the checkpoint file *is* the run.
   - A displaced writer's checkpoint write landed in **7/7 rounds**, permanently regressed a finished run's
     checkpoint from **30 to 6** (24 steps recomputed), and the displaced process was never told: it returned the
     new owner's result as its own.
   - So DBOS's enforced fencing, its real advantage over runstate, does not protect the plane that matters here. On
     that plane the outcome is the same as runstate's, which declines fencing, but with less record of what
     happened.

Also against the deployment shape:

- DBOS itself labels SQLite "for development and testing".
- First-time DB creation races, and the loser crashes.
- mycooc's multi-host operation would need Postgres, which was not tested, per the brief.

Scale caveat: the timings come from a simulation (0.05–0.1 s per step, 3 s staleness threshold), not from GPU runs.
