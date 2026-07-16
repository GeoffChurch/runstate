# runstate

A protocol + reference Python implementation for **cooperative bidirectional control** between an orchestrator and a long-running scientific worker.

> **New here?** To *understand* the system, read **[docs/overview.md](docs/overview.md)** — a guided tour: what it does, how you interface with it, and why each layer and component exists. To *integrate* your training loop, read **[docs/guide.md](docs/guide.md)** — a task-oriented 15-minute how-to (with **[docs/api.md](docs/api.md)** as the public-surface reference).

## What it is

`runstate` provides:

1. **A protocol** — a per-run, append-only **topic log** of envelopes `{seq, topic, name?, request_id?, body}`, with opt-in **conventions** layered on top (cooperative-control, subscription, lifecycle, launcher, value). Wire format: the JSON Schema stack in `protocol/` (`envelope-v0.2.schema.json` + the per-convention schemas). Semantics: `docs/design-v0.2.md`.

2. **A reference Python implementation** — the `runstate.channel` substrate (`MemoryChannel` + `SqliteChannel` + the cross-host `PostgresChannel`, the optional `[postgres]` extra), the reference `Worker` loop, and opt-in orchestration helpers (`ThreadLauncher` / `LocalLauncher`, `Watcher`, `sweep`). (On NFS, export `RUNSTATE_SQLITE_JOURNAL_MODE=DELETE` — the default WAL journal needs shared memory a network filesystem can't back.)

The protocol is language-agnostic: any implementation that produces conforming messages can interoperate. The Python library is one such implementation.

## Quickstart

Install: `pip install -e .` (Python ≥ 3.11, stdlib-only core; add `.[postgres]` for the cross-host backend, `.[test]` for the suite).

The worker drives a normal loop; `runstate.Worker` drains control, services subscriptions, and emits the lifecycle beacons:

```python
# worker.py — launched into a run; attach() reads RUNSTATE_* from the env
import runstate

with runstate.Worker(runstate.attach()) as w:
    for step in w.steps(total=1000):
        w.set("loss", train_one_step())   # reported to whoever subscribed
    w.stopped(completed=True)   # finished the whole budget -> claim completion
```

(Falling off the `with` block *without* the claim also emits a clean `lifecycle.stopped`, but as the resumable default — `outcome` reads `preempted`. `completed` is the worker's opt-in claim of *intrinsic, permanent* completion; a resumable or chunked worker deliberately never claims it.)

The orchestrator spawns it, subscribes, and watches it to a terminal result:

```python
import runstate

with runstate.LocalLauncher(root="/tmp/runs") as launcher, \
        launcher.open_channel("run-1") as ch:   # the channel is a context manager
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="me")
    handle = launcher.launch("run-1", ["python", "worker.py"])

    watcher = runstate.Watcher(); watcher.add(handle)
    result = watcher.wait("run-1", on_event=lambda rid, e: print(e.topic, e.body))
    print(result.outcome)   # "completed" | "preempted" | "errored" | "killed" | "presumed_dead"
```

A runnable version is in `examples/minimal/` (`python examples/minimal/driver.py`).

**Runs are episodic.** A `run_id` names a durable log, not a process: a worker *episode* (`started … stopped`) can end, and a later episode can attach to the **same** log and resume — which is how relaunch-to-extend, launch-on-demand, and reconnect all fall out. Two rules every consumer needs: episode-scoped state (the live handle/pid, current status) is read from the **latest** `lifecycle.started`, never the first — `runstate.latest_episode` / `live_episode` own that rule — and a terminal verdict stands only until a new episode claims (`peek_terminal` is episode-aware). The full model: [docs/overview.md](docs/overview.md).

## Recipes

Three common patterns; each has a runnable twin under `examples/`.

**Reuse + extend** — `ensure` serves the logged prefix on a cache hit, else relaunches-to-extend and waits. Content-addressed by `run_id`, so re-asking is free and asking for *more* steps resumes:

```python
runstate.ensure(producer, "loss", until={"step": 8})    # cold: produce 0..7
runstate.ensure(producer, "loss", until={"step": 8})    # warm: served from the log, no worker
runstate.ensure(producer, "loss", until={"step": 20})   # extend: resume 8..19, one series
```
→ `examples/reuse/`

**Divergence-preempt** — watch the value stream and send a cooperative `control.stop` when you've seen enough (divergence, a plateau, a budget); the worker stops cleanly at its next tick (`outcome` → `preempted`):

```python
def on_event(rid, e):
    if e.topic == "value" and e.body["value"] > THRESHOLD:
        ch.send({"from": {"step": 0}}, topic="control.stop", request_id="me")

watcher.wait("run-1", on_event=on_event)
```
→ `examples/minimal/`

**Killed-redrive (caller pattern)** — `ensure` auto-continues clean `preempted` stops but **fails fast on a death**; the retry decision is yours. `RunFailedError` hands you the verdict observed at raise time — decide if it's resumable (the worker didn't self-diagnose a fatal `error`) and re-call `ensure` to resume from the checkpoint; take-the-latest absorbs the re-emitted overlap:

```python
for _ in range(budget):                          # the retry budget lives here, with you
    try:
        series = runstate.ensure(producer, "loss", until={"step": N}); break
    except runstate.RunFailedError as e:
        if e.result.error is not None:           # worker self-diagnosed fatal -> don't retry
            raise
        # killed without a self-diagnosis -> the re-call resumes from the checkpoint
```
→ `examples/redrive/`

## CLI

A minimal terminal tool ships with the package (`runstate/cli.py`, stdlib `argparse`, sqlite only):

```bash
runstate status <root>                    # snapshot table: run_id, verdict, progress, freshness
runstate stop <root> <run_id> [--wait N]  # send a cooperative control.stop (armed for the next episode if the run is down)
```

`status` discovers both the flat (`<root>/<rid>.db`) and Recipe-1 sharded (`<root>/runs/<xx>/<rid>/<rid>.db`) layouts, and its freshness column is powered by the observer clock (`docs/specs/observer-clock.md`). It is deliberately **not** a daemon or a live viewer — those are a separate project (`docs/backlog/webapp-viewer.md`, `docs/backlog/visualization-story.md`).

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
 your training loop  ← uses runstate.attach + Worker
```

The substrate is the `Channel` (a durable per-run topic log). The protocol defines the conventions over it. The library ships the reference `Worker`, launchers, and `Watcher` for producing/consuming them.

## Status

**v0.2 is implemented** in `runstate/` — the topic-log substrate, the conventions, the orchestration helpers, and the JSON Schema stack with conformance tests. See `docs/design-v0.2.md` for the rationale and `docs/design-v0.2-exploration.md` for the decision trail. (The earlier v0.1 pull-first command/event model was superseded by this redesign.)

**v0.3 (in progress, on `master`):** the convention bodies are typed frozen dataclasses in `runstate/vocabulary/`; `value` events carry an absolute wall-clock `value.t`; a `run_id()` *recipe* documents reuse-by-content-hash (`docs/specs/run-id-recipe.md`); the **run-episodes** scoped primitive landed — a `run_id` hosts multiple resumable episodes (relaunch-to-extend), with the single-spawn guard implemented as a worker self-claim (`docs/specs/run-episodes.md`); and the **memoizer** that consumes it shipped — `history()` replays a schedule over the logged `value` points (passive, channel-only), and `ensure(producer, name, until={"step": N})` serves the logged prefix on a hit or relaunches-to-extend and waits on a miss, with the `launch_producer` seam + the `relaunch_if_needed` helper (`docs/specs/memoizer.md`). The **service worker** shipped next (`docs/specs/service-worker.md`): leased demand — `serve()`/`retire()`/`pinned`, the careful death (episodes CAS-claimed at both ends), expiry counter-records under the positional answer fold (`live_demand`); `examples/monitor/` is the on-demand dogfood. **Lazy-launch** shipped next (`docs/specs/lazy-launch.md`): `ensure_served`, the leased-demand waker — demand-first, no flap policy, loser corpses disciplined; `examples/monitor/` now runs the whole loop twice (demand → wake → serve → lapse → retire → re-demand → re-wake). Then the two dissolutions that closed the arc (`docs/specs/derived-runs.md`, `docs/specs/store.md`): the *function producer* needs no new surface (a derived run is an ordinary one-step run behind `ensure`; the named `Producer` Protocol stays deferred on evidence), and the relational-layer **Store** ships as recipes over the existing basis — the rid is the run's *address* (content-addressed placement; reuse-by-hash dissolves into `ensure` against the one home), membership is a cell pointer, provenance is the child's birth record — plus one helper, `foreign_episode` (the producer gate's foreign half). See `docs/backlog/index.md` "Start here". Full v0.3 trail: `docs/design-v0.3-exploration.md`.

**Shipped since (June–July 2026):** the cross-host **`PostgresChannel`** backend (`docs/specs/channel-postgres.md`) — one shared `log` table with `PRIMARY KEY (run_id, seq)` as the cross-host CAS arbiter (cross-host single-spawn + control fall out of it, claim model unchanged), a session advisory lock as a Watcher-consumed liveness capability, and the repo's first CI workflow running the suite against a real server. The 2026-07 holistic review then landed: `last_seq()` — the substrate's fifth op, the CAS's read half (worker attach on a 10⁶-envelope log: 3.4 s → 1.5 ms); `Worker.emit` — the unconditional broadcast-value verb beside the demand-sampled `set`; typed `RunFailedError` / `NoProgressError`; `undischarged_stops` — the stop fold's observer home; `lifecycle`-v0.3 (the dead `hostname` field dropped); and **launch identity** (`launcher`-v0.3, `docs/specs/launcher-record-identity.md`) — one correlation id per launch, on `launched` + `terminated` and re-emitted on the worker's `started`, anchoring the verdict to the *claimed* episode so a late reap or a claim-race loser's clean exit can no longer forge it. **Current thread:** the third-party observer (`docs/backlog/third-party-observer.md`) — what the log doesn't tell a party that didn't launch the run. Its first item, the **observer clock**, SHIPPED 2026-07-16 (`docs/specs/observer-clock.md`: the beacon is dated — `t` required on `heartbeat`/`stopped`/`launched`/`terminated` in `lifecycle`-/`launcher`-v0.4; the Watcher seeds its staleness clock from a cold log's own `t`, so a long-dead run finally reads dead to a late attacher, and `last_activity` is the freshness fold the new CLI reads; existing dbs migrate via the owner-run `scripts/migrate_observer_clock_v0_4.py`). Next up: item 2, the run's **target** on the log — the one missing basis vector — which awaits its own spec + adversarial pass.
