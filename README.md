# runstate

A protocol + reference Python helpers for **cooperative bidirectional control** between an orchestrator and a long-running scientific worker.

## What it is

`runstate` provides:

1. **A protocol** (`protocol/messages-v0.1.schema.json` + `protocol/spec.md`) — wire format and semantics for orchestrator↔worker messages: cooperative preempt, progress reporting, command acknowledgment.

2. **A reference Python implementation** — `runstate.Channel` (durable bidirectional IPC) + opt-in `runstate.control` and `runstate.events` helpers that produce and consume protocol-conforming messages.

The protocol is language-agnostic: any implementation that produces conforming messages can interoperate. The Python library is one such implementation; v0.1 ships only this one.

## What it is not (in v0.1)

- Not an orchestrator. No `Orchestrator.run()` class, no launcher abstraction. Spawn worker processes however you want (`subprocess.Popen`, Hydra, submitit, ray, etc.) and use the protocol to talk to them.
- Not a tracker. Use wandb / TensorBoard / MLflow for one-way metric logging and visualization. `runstate` v0.1 is the *control-plane* counterpart.
- Not a workflow engine. No DAG, no retry logic, no scheduler. Compose those at your own application layer.

The control-plane / tracker split is a v0.1 division of labor, not a permanent one. Long-term, runstate could own a data-plane visualization protocol too (richer event types, viewer-side discovery) and become a one-stop shop. See `docs/backlog/`.

## Positioning

```
   sweep generator (Hydra / Optuna)
            │ configs
            ▼
       your orchestrator script  ← uses runstate protocol
   ┌────────┴────────┐
   │                 │
 Channel        Process spawn
 (runstate)     (subprocess / submitit / ray)
   │                 │
   ▼                 ▼
 worker script ──→ tracker (wandb / TB)
   │                 │
   │                 ▼
   │            web UI, plots
   ▼
 your training loop  ← uses runstate.attach + control + events
```

The substrate is `Channel` (durable per-run JSON message transport). The protocol defines the typed messages. The library ships typed helpers for producing/consuming them.

## Status

v0.1 in development. Protocol is described in `protocol/`; the Python library implements it in `runstate/`. See `docs/design-v0.1.md` for the full design rationale (with revision history).
