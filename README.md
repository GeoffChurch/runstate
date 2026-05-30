# runstate

A protocol + reference Python implementation for **cooperative bidirectional control** between an orchestrator and a long-running scientific worker.

## What it is

`runstate` provides:

1. **A protocol** — a per-run, append-only **topic log** of envelopes `{seq, topic, name?, request_id?, body}`, with opt-in **conventions** layered on top (cooperative-control, subscription, lifecycle, launcher). Wire format: the JSON Schema stack in `protocol/` (`envelope-v0.2.schema.json` + the per-convention schemas). Semantics: `docs/design-v0.2.md`.

2. **A reference Python implementation** — the `runstate.channel` substrate (`MemoryChannel` + `SqliteChannel`), the reference `Worker` loop, and opt-in orchestration helpers (`ThreadLauncher` / `LocalLauncher`, `Watcher`, `sweep`).

The protocol is language-agnostic: any implementation that produces conforming messages can interoperate. The Python library is one such implementation.

## Quickstart

The worker drives a normal loop; `runstate.Worker` drains control, services subscriptions, and emits the lifecycle beacons:

```python
# worker.py — launched into a run; attach() reads RUNSTATE_* from the env
import runstate

with runstate.Worker(runstate.attach()) as w:
    for step in w.steps(total=1000):
        w.set("loss", train_one_step())   # reported to whoever subscribed
```

The orchestrator spawns it, subscribes, and watches it to a terminal result:

```python
import runstate

with runstate.LocalLauncher(root="/tmp/runs") as launcher:
    ch = launcher.open_channel("run-1")
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="me")
    handle = launcher.launch("run-1", ["python", "worker.py"])

    watcher = runstate.Watcher(); watcher.add(handle)
    result = watcher.wait("run-1", on_event=lambda rid, e: print(e.topic, e.body))
    print(result.outcome)   # "completed" | "stopped" | "errored" | "killed" | "presumed_dead"
```

A runnable version is in `examples/minimal/` (`python examples/minimal/driver.py`).

## What it is not

- **Not an orchestrator framework.** No `Orchestrator.run()` class. There's an opt-in `Launcher` Protocol + thin reference launchers, but you can spawn worker processes however you want (`subprocess.Popen`, Hydra, submitit, ray) and use the protocol to talk to them.
- **Not a tracker.** Use wandb / TensorBoard / MLflow for one-way metric logging and visualization. `runstate` is the *control-plane* counterpart.
- **Not a workflow engine.** No DAG, no retry logic, no scheduler. Compose those at your application layer.

The control-plane / tracker split is a current division of labor, not a permanent one. Long-term, runstate could own a data-plane visualization protocol too (richer event types, viewer-side discovery) and become a one-stop shop. See `docs/backlog/`.

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

v0.2 is implemented in `runstate/` — the topic-log substrate, the conventions, the orchestration helpers, and the JSON Schema stack with conformance tests. See `docs/design-v0.2.md` for the design rationale and `docs/design-v0.2-exploration.md` for the full decision trail. (The earlier v0.1 pull-first command/event model was superseded by this redesign.)
