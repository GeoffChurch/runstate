# runstate

A protocol + reference Python implementation for **cooperative bidirectional control** between an orchestrator and a long-running scientific worker.

> **New here?** To *understand* the system, read **[docs/overview.md](docs/overview.md)** — a guided tour: what it does, how you interface with it, and why each layer and component exists. To *integrate* your training loop, read **[docs/guide.md](docs/guide.md)** — a task-oriented 15-minute how-to (with **[docs/api.md](docs/api.md)** as the public-surface reference).

## What it is

`runstate` provides:

1. **A protocol** — a per-run, append-only **topic log** of envelopes `{seq, topic, name?, request_id?, body}`, with opt-in **conventions** layered on top (cooperative-control, subscription, lifecycle, supervisor, value). A worker's values are **committed** to the nodes of a **lineage**, so every read follows one chain of nodes from a chosen head. Wire format: the JSON Schema stack in `protocol/` (`envelope-v0.2.schema.json` + the per-convention schemas). Semantics: `docs/design-v0.2.md`, and `docs/specs/commits-and-lineage.md` for log format 0.4.0.

2. **A reference Python implementation** — the `runstate.channel` substrate (`MemoryChannel` + `SqliteChannel` + the cross-host `PostgresChannel`, the optional `[postgres]` extra), the reference `Worker` loop, and opt-in orchestration helpers (`ThreadLauncher` / `LocalLauncher`, `Watcher`, the deciders, `ensure`). (On NFS, export `RUNSTATE_SQLITE_JOURNAL_MODE=DELETE` — the default WAL journal needs shared memory a network filesystem can't back.)

The protocol is language-agnostic: any implementation that produces conforming messages can interoperate. The Python library is one such implementation. Writing another (Rust, Go, TS)? **[docs/implementers-guide.md](docs/implementers-guide.md)** is the language-neutral conformance reference.

## Quickstart

Install: `pip install -e .` (Python ≥ 3.11, stdlib-only core; add `.[postgres]` for the cross-host backend, `.[test]` for the suite).

**The code below runs.** As in the guide, a block fenced ` ```python ` is a complete program, and `tests/test_guide.py` runs each one against the current `runstate/`; a block fenced ` ```python fragment ` is a sketch, most often of a runnable example under `examples/` (named beside it, and run by `tests/test_examples.py`).

**Upgrading with existing logs.** A log's format is part of its address (`<root>/v<format>/<rid>.db`; schema `runstate_v<format>` on Postgres, with the dots as underscores), and opening a log of any other format than `runstate.LOG_FORMAT` raises instead of reading it: `LogFormatMismatch` for an older or newer format, `LogFormatMissing` for a log written before versioned addresses (`<root>/<rid>.db`). To upgrade, first stop every process that writes the logs and drain any queued jobs; then onboard legacy logs (written at or after `4729fcd`) exactly as the `LogFormatMissing` message instructs. On SQLite its loop moves each `<root>/<rid>.db` into `<root>/v0.2.0/` together with its `<rid>.db-wal`, `<rid>.db-shm` and `<rid>.db-journal` files, and leaves a **tombstone**, an empty read-only file, at the old address; on Postgres it moves the `log` table into schema `runstate_v0_2_0` and leaves an empty insert-refusing `log`. Either way a writer still on an older runstate fails loudly instead of starting the run over. Then run `runstate migrate <root>` (add `--backend postgres` for a DSN), which seals each old log and writes its copy in the current format, one retained step at a time. It first runs a detection pass that writes nothing, and refuses until you have stated what an old log leaves open (`--assume`, `--resolve`, `--accept`). Details: `docs/specs/log-formats.md` §6 and §8, and for the step to format 0.4.0, `docs/specs/commits-and-lineage.md` §7–§8.

The worker drives a normal loop; `runstate.Worker` claims the run, drains control, answers subscriptions, and writes the lifecycle records:

```python
# worker.py -- launched into a run: current_channel() reads the launcher's RUNSTATE_*
import runstate

with runstate.Worker(
    runstate.current_channel(),
    resume_from=None,  # a fresh start (a checkpoint object resumes from its node)
    sources=["lr"],  # sampled on demand; every other name is an eager stream
    save=runstate.NoSave(),  # no checkpoints (docs/guide.md section 4 adds them)
) as w:
    for step in w.steps(runstate.Target(runstate.Commits(), 1000)):
        w.emit("loss", 1.0 / (step + 1))  # your training step: always logged
        w.set("lr", 0.1 * 0.99**step)  # the current value, logged only when subscribed
    if not w.stop_pending:  # ran the whole budget, rather than being stopped
        w.stopped(completed=True, error=None)  # claim completion
```

`steps(target)` yields each iteration's index and **commits** the iteration after its body: one node per iteration, committing the values emitted in it. `emit` reports an element of an eager stream, which `series`, `ensure` and `history` read; `set` updates a declared source, which reaches the log only when a subscription fires. (Leaving the `with` block *without* the claim writes a clean, **extendable** exit whose outcome reads `preempted`, and a later claim can continue it. `completed=True` is the worker's opt-in claim of *intrinsic, permanent* completion, which closes the lineage; a resumable or chunked worker deliberately never claims it.)

The orchestrator spawns it, subscribes, and watches it to a terminal result (the skeleton of `examples/minimal/driver.py`):

```python fragment
import sys

import runstate

with runstate.LocalLauncher(root="/tmp/runs") as launcher:
    ch = launcher.create_channel("run-1")  # birth the run's log
    # Subscribe BEFORE the launch: the log holds the request until a worker drains it.
    ch.send(
        {"every": {"commits": 10}},  # sample the `lr` source at every 10th commit
        topic=runstate.Topic.CONTROL_SUBSCRIBE,
        name="lr",
        request_id="me",  # the name its answers bear
    )
    cmd = runstate.Command([sys.executable, "worker.py"], env={})  # the parent's env
    handle = launcher.launch("run-1", cmd)

    watcher = runstate.Watcher(heartbeat_timeout=30.0)  # required: it changes a verdict
    watcher.add(handle)
    result = watcher.wait("run-1", on_event=lambda rid, e: print(e.topic, e.body))
    print(result.outcomes)  # a set of Outcome: completed, preempted, errored, ...

    head = runstate.LatestClaimHead()  # a read follows one lineage, from this head
    print(len(runstate.series(ch, "loss", head=head)))  # 1000 eager losses
    print(len(runstate.answers(ch, "me", head=head)))  # 100 lr samples, one per firing
```

A runnable version is in `examples/minimal/` (`python examples/minimal/driver.py`).

**Runs are claims on a durable log.** A `run_id` names a durable log, not a process: a worker's *claim* (`started … stopped`) can end, and a later claim can continue the **same** log from a node of it — which is how relaunch-to-extend, launch-on-demand, and reconnect all fall out. Three rules every consumer needs: claim-scoped state (the live handle, the current status) is read from the **latest** `lifecycle.started`, never the first — `runstate.latest_episode` / `live_episode` own that rule; a terminal verdict stands only until a new claim (`peek_terminal` reads the newest claim's); and a branch is never spliced — a rewind, a resume from an older checkpoint or a displaced worker commits on its own branch, and every lineage read takes a required `head=` (`LatestClaimHead()`, or `AtNode(node)` for a fixed one). A claim ends by **record**, its exit or a death report naming it, never by a probe's silence. The full model: [docs/overview.md](docs/overview.md).

## Recipes

Three common patterns; each has a runnable twin under `examples/`.

**Reuse + extend** — `ensure` answers from the log when the newest claim's lineage already holds the target (`Reached(node)`, no worker), else relaunches-to-extend and waits. Content-addressed by `run_id`, so re-asking is free and asking for *more* resumes the same lineage:

```python
import hashlib
import json
import math

import runstate


def train(channel: runstate.Channel, *, until: runstate.Target, lr: float) -> None:
    # A pure function of the index holds no state, so it continues the head's lineage.
    with runstate.Worker(
        channel,
        resume_from=runstate.LatestClaimHead(),
        sources=[],
        save=runstate.NoSave(),
    ) as w:
        for step in w.steps(until):  # `until` is the drive's Target
            w.emit("loss", 5.0 * math.exp(-lr * step))
    # no completed claim: an extendable exit at the target, which the next drive extends


inputs = {"lr": 0.3}  # the run_id is the inputs' hash, never the target's
rid = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
target = runstate.ThreadTarget(train, args=(), kwargs=inputs)
producer = runstate.launch_producer(runstate.ThreadLauncher(), rid, target)

runstate.ensure(producer, runstate.Target("loss", 8))  # cold: produce 0..7
runstate.ensure(producer, runstate.Target("loss", 8))  # warm: from the log, no worker
r = runstate.ensure(producer, runstate.Target("loss", 20))  # extend: resume 8..19
assert isinstance(r, runstate.Reached) and r.node is not None

losses = runstate.series(producer.channel, "loss", head=runstate.AtNode(r.node))
assert len(losses) == 20  # one lineage, nothing to collapse
claims = producer.channel.read(topics=[runstate.Topic.LIFECYCLE_STARTED])
assert len(claims) == 2  # the warm call launched nothing
```
→ `examples/reuse/` (the same, with checkpoints)

**Divergence-preempt** — watch the run and send a cooperative `control.stop` when you've seen enough (divergence, a plateau, a budget); the worker stops cleanly at its next commit or heartbeat, and the verdict's `outcomes` holds `preempted`. A stop's `request_id` is required and never reused:

```python
import time
import uuid

import runstate

THRESHOLD = 100.0


def train(channel: runstate.Channel) -> None:
    with runstate.Worker(
        channel, resume_from=None, sources=[], save=runstate.NoSave()
    ) as w:
        for step in w.steps(None):  # no bound: it runs until it is stopped
            w.emit("loss", 1.5**step)  # a loss that diverges
            time.sleep(0.005)


launcher = runstate.ThreadLauncher()
ch = launcher.create_channel("run-1")
handle = launcher.launch("run-1", runstate.ThreadTarget(train, args=(), kwargs={}))

heads, last = runstate.HeadFold(), runstate.LastElement("loss")  # held folds
stops: list[int] = []


def on_event(rid: str, e: runstate.Envelope) -> None:
    if e.topic != runstate.Topic.LIFECYCLE_COMMIT or stops:
        return  # a value lands with its node: read the fold only then
    loss = last.poll(ch, heads.poll(ch))
    if loss is not None and loss.value > THRESHOLD:
        # no `from`: the stop holds as soon as the worker drains it
        stop_id = uuid.uuid4().hex  # one per stop, never reused
        stop = ch.send({}, topic=runstate.Topic.CONTROL_STOP, request_id=stop_id)
        stops.append(stop)


watcher = runstate.Watcher(heartbeat_timeout=30.0)
watcher.add(handle)
result = watcher.wait("run-1", on_event=on_event)
assert result.outcomes == {runstate.Outcome.PREEMPTED}  # clean, and resumable
assert runstate.await_consumed(ch, stops[0]) is None  # the exit honoured the stop
```
→ `examples/minimal/`

**Killed-redrive (caller pattern)** — `ensure` auto-continues clean `preempted` stops but **fails fast on a death**; the retry decision is yours. `RunFailedError` hands you the verdict observed at raise time — decide if it's resumable (the worker didn't self-diagnose a fatal `error`) and re-call `ensure` to resume from the checkpoint. The crashed claim's commits past that checkpoint stay on the log, on a branch the new lineage does not include, so the series is one lineage with nothing to collapse:

```python fragment
for _ in range(budget):  # the retry budget lives here, with you
    try:
        reached = runstate.ensure(producer, runstate.Target("loss", n))
        break
    except runstate.RunFailedError as e:
        if e.result.error is not None:  # the worker self-diagnosed fatal: don't retry
            raise
        # a death it did not diagnose (killed, lost): the re-call resumes
```
→ `examples/redrive/`

The patterns compose: `examples/vqe/` is a full domain workload (a variational quantum eigensolver, qiskit + optional IonQ Cloud — `pip install runstate[examples]`) whose iteration is **long, expensive, and cancelable** — the worker calls `w.heartbeat()` *while it waits* on a queued job, which keeps it witnessed without writing a node, and a `control.stop` lands mid-queue and cancels the in-flight job; it emits the eager `energy` stream beside a demand-sampled `job_seconds` source, resumes by the checkpoint recipe, and is content-addressed so re-running is a cache hit, while the driver's plateau policy steers it. `examples/monitor/` is the on-demand service worker (leased demand: `serve()` while a subscription is live, retiring when its lease expires; `ensure_served` wakes it again on fresh demand, read through the `LiveDemand` fold).

## What it is not

- **Not an orchestrator framework.** No `Orchestrator.run()` class. There's an opt-in `Launcher` Protocol + thin reference launchers, but you can spawn worker processes however you want (`subprocess.Popen`, Hydra, submitit, ray) and use the protocol to talk to them.
- **Not a tracker.** Use wandb / TensorBoard / MLflow for one-way metric logging and visualization. `runstate` is the *control-plane* counterpart.
- **Not a workflow engine.** No DAG, no retry logic, no scheduler. Compose those at your application layer.

The control-plane / tracker split is a current division of labor, not a permanent one. Long-term, a **separate viz project built on runstate** could own the data-plane protocols (richer event types, viewer-side discovery, artifacts) and make the pair a one-stop shop — runstate itself stays the minimal control protocol the data plane rides on; those protocols never land in this repo's `protocol/`. See `docs/backlog/visualization-story.md`.

## Positioning

```
   sweep generator (Hydra / Optuna)
            │ configs
            ▼
       your orchestrator script  ← uses runstate protocol + helpers
   ┌────────┴────────┐
   │                 │
 topic log      process spawn
 (Channel)      (runstate launchers, or subprocess / submitit / ray)
   │                 │
   ▼                 ▼
 worker script ──→ tracker (wandb / TB)
   │                 │
   │                 ▼
   │            web UI, plots
   ▼
 your training loop  ← uses runstate.current_channel + Worker
```

The substrate is the `Channel` (a durable per-run topic log). The protocol defines the conventions over it. The library ships the reference `Worker`, launchers, and `Watcher` for producing/consuming them.

## Status

**Current: layer 2, commits and lineage — log format 0.4.0** (`docs/specs/commits-and-lineage.md`, executed by `docs/plans/2026-10-28-layer2-live.md`). `runstate.LOG_FORMAT` is 0.4.0, the 0.3.0 → 0.4.0 step is registered, and the package is 0.4.0.dev0. What it gives:

- **One lineage of a run's values, nodes and commits.** Each iteration's `commit()` writes a node that names the values it commits; every read (`series`, `prefix`, `aligned`, `answers`, `ensure`) follows one lineage from a head chosen at the edge, so a rewind, a resume from an older checkpoint or a displaced worker branches instead of splicing. Progress is a count along the lineage (`Commits()`, or an eager stream's length), the subscription algebra counts the same way (`{commits: n}`, `{stream: name, n: n}`), and the checkpoint recipe makes a resumed state the state its node committed (`Every(k, fn)`, `OnHeadLineage`).
- **Death reports from evidence.** A claim ends by its exit or by a `supervisor.died` naming it — written by a launcher's reap, by `report_if_dead`, or by any party with evidence through `report_death` — never by an inference from silence; a worker that reads a report naming its claim is released. Verdicts are by viewpoint: `RunResult.outcomes` (a set of the closed `Outcome` enum) and `tier`.
- **The session probe on Postgres.** Every `PostgresChannel` connection holds a fresh advisory-lock key, and each row carries its writer's; a claim whose session has ended is evidence, which `report_if_dead` records as a death report (`docs/specs/channel-postgres.md`). The CAS stays the only claim arbiter.
- **The format migration under `runstate migrate`:** the retained 0.3.0 → 0.4.0 step (`commits-and-lineage.md` §7), with its detection pass and the consumer's stated choices, beside the 0.2.0 → 0.3.0 one. Both are registered, so `runstate migrate` moves a 0.2.0 or a 0.3.0 log to 0.4.0.

**Earlier history, briefly.** v0.2 replaced the original pull-first command/event model with the two-layer design: the topic-log substrate plus opt-in conventions (`docs/design-v0.2.md`; the decision trail in `docs/design-v0.2-exploration.md`). v0.3 added run episodes, the memoizer (`ensure`), the service worker and lazy-launch, and dissolved the relational Store into recipes (`docs/design-v0.3-exploration.md`). Since then: the cross-host `PostgresChannel`, the 2026-07 holistic review, launch identity, the observer clock, versioned log formats with `runstate migrate`, and reference by name (log format 0.3.0). The specs are under `docs/specs/`; what is next starts at `docs/backlog/index.md` ("Start here").
