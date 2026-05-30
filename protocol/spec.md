# runstate Protocol v0.1 — Specification

This document describes the runstate v0.1 protocol semantics. The wire format (message shapes) is defined in `messages-v0.1.schema.json` and is the authoritative source for what valid messages look like. This document covers everything else: the on-disk layout, the cooperative discipline, the direction conventions, and the rules that govern message flow.

Any implementation (Python, Rust, Go, etc.) that conforms to both the schema and this spec can interoperate.

## Scope

`runstate v0.1` defines a **per-run, bidirectional, durable message channel** between two parties: an **orchestrator** and a **worker**. The protocol is small, opinionated about cooperative-preempt discipline, and silent about everything else (process spawning, run metadata, sweep generation, visualization).

## Parties and roles

A `Channel` has two roles: `worker` and `orchestrator`.

- The **worker** is the long-running process performing the actual computation (e.g., a training loop).
- The **orchestrator** is any process that wishes to observe and direct the worker (e.g., a launching script, a CLI tool, a UI thread, a remote monitor).

A worker-role Channel sends to the orchestrator and receives from the orchestrator; an orchestrator-role Channel does the opposite. Two Channels with opposite roles on the same `run_id` form a bidirectional transport.

Multiple orchestrator-role Channels for the same `run_id` are permitted (e.g., a launcher script + a separate UI). Each independently sends commands and receives the worker's messages. The protocol does not coordinate among multiple orchestrators; users who need that coordinate at a higher layer.

## Run identity

A `run_id` is a string. Implementations choose how to generate them; common patterns are UUIDs, content hashes, or user-supplied names. The protocol imposes no semantic constraints beyond the string serving as a stable identifier for the run's Channel.

## Message direction

Two message classes:

- **Command** (orchestrator → worker): `StopNow`, `StopAtStep`. See schema.
- **Event** (worker → orchestrator): `Progress`, `Stopped`, `Ack`. See schema.

Implementations MAY transport additional, non-protocol message types (any JSON-serializable dict) on the same Channel. Such messages SHOULD use a `type` field that is not one of the reserved protocol type strings (`StopNow`, `StopAtStep`, `Progress`, `Stopped`, `Ack`). Conformant implementations MUST forward such messages unchanged.

## Durability and ordering guarantees

- **Atomic write**: `send` returns only after the message is committed to durable storage. A reader observing the message can assume it will not disappear.
- **Per-direction FIFO order**: messages sent in one direction arrive in send order. Cross-direction order is unspecified.
- **Crash recovery**: either party may crash and restart. After re-attaching to the same `run_id`, they observe all messages sent by the other party since the last consumed message.
- **No history guarantee**: implementations MAY discard consumed messages. The protocol does not promise that already-read messages remain accessible. Users who need history should either preserve it externally (a tracker, a log) or choose an implementation that explicitly preserves messages (e.g., the SQLite backend in the Python reference implementation retains all rows).

## On-disk layout

Implementations are free to use any on-disk layout, but the reference Python implementation uses:

```
<root>/
  <run_id>/
    # File backend:
    to_worker/<seq>.json          # messages from orchestrator to worker
    to_orchestrator/<seq>.json    # messages from worker to orchestrator

    # SQLite backend:
    channel.db                    # all messages in a single `messages` table
```

The choice of backend is opaque to the protocol; both produce identical wire-format messages.

## Cooperative-preempt discipline

The protocol's load-bearing opinion: **workers stop cooperatively at safe points; orchestrators do not SIGKILL mid-checkpoint.**

Concretely:

1. The worker checks for pending Commands at safe points it defines (typically between training steps).
2. When the worker receives `StopNow` or a fired `StopAtStep`, it MUST checkpoint its state if applicable, then exit. Implementations are free to define their own pre-exit cleanup (saving state, freeing GPU memory, etc.).
3. The worker SHOULD send `Stopped(reason=...)` before exiting, so the orchestrator knows the run terminated cooperatively rather than crashed.
4. The orchestrator SHOULD give the worker a grace period after sending a stop command before resorting to forced termination (SIGTERM/SIGKILL).

This is a discipline, not enforced by the protocol. Workers that never check for commands cannot be cooperatively preempted; orchestrators that immediately SIGKILL bypass the protocol entirely.

## Acknowledgment semantics

`Ack` is a worker-to-orchestrator event that confirms a command was received and acted on. The rules:

1. The worker auto-sends `Ack(of=<command_type>, command_id=<command_id>)` **only when it acts on a command** — i.e., when its check helper returns a non-None Command. The `command_id` MUST be the original command's `command_id`, not a new one.

2. **Superseded commands are dropped without Ack.** If a `StopAtStep(at=200, command_id=A)` is held pending (waiting for the worker's step to reach 200), and then a `StopAtStep(at=100, command_id=B)` arrives, the new one supersedes the old. When the worker eventually fires (at step 100), it acks B only. A is dropped silently; the orchestrator will see `A` in its sent-commands list but not in its received-acks list.

3. **Subsumed commands are dropped without Ack.** If a `StopAtStep` is held pending and then `StopNow` arrives, the worker acts on `StopNow` (which takes precedence) and acks it. The pending `StopAtStep` is dropped silently.

4. **Held-but-not-yet-fired commands are not acked.** Repeated check calls while a `StopAtStep` is held but its trigger condition hasn't been met do not emit Acks. The Ack happens at most once, when the command fires.

The rule in one sentence: **only the command whose effect the worker actually acted on gets ack'd.** Orchestrators that track sent commands vs received acks can identify which commands "never landed" — useful for watchdog logic on unresponsive workers.

## Safe points

The worker chooses when to check for commands. Common patterns:

- **Async polling**: at the end of each training step, the worker calls `check(timeout=0)` (non-blocking). Commands are processed at step granularity.
- **Synchronous yield**: at the end of each step, the worker calls `check(timeout=None)` (blocking). The worker waits for the orchestrator to send a command before continuing. Suitable for tight orchestrator coordination but adds an IPC roundtrip per step.

The protocol supports both via the underlying Channel's `recv(timeout=...)` semantics; implementations choose which to expose, and users choose which to use.

## Channel-instance state (implementation note)

The worker's check helper may need to maintain state across calls — specifically, a held `StopAtStep` whose `at` step has not yet been reached. This state is per-(channel, helper) and not part of the wire format. The reference Python implementation maintains this state on a `Checker` object bound to a Channel; other implementations are free to use whatever mechanism is appropriate.

The protocol does not require this state to be persisted across worker restarts: a worker that crashes and re-attaches sees fresh state. If the orchestrator wants its `StopAtStep` to survive a worker crash, it re-sends after detecting the crash. The orchestrator's tracking of "what commands I've sent" is its own responsibility, not the protocol's.

## Versioning

This document describes protocol version `0.1`. The schema `$id` is `https://runstate.dev/protocol/v0.1/messages.json`.

Breaking changes will produce a new schema with a new `$id` (e.g., `v0.2`, `v1.0`). Implementations declare which version they target. Coexistence of multiple protocol versions on the same Channel is not supported in v0.1; if you need multi-version support, run separate Channels per version.

## What's deliberately not in the protocol

- **Process spawning**: how the worker process gets started. The orchestrator and worker just need a shared `(run_id, root)` to find each other.
- **PIDs and OS signals**: separate concerns. Implementations may layer a PID-file convention on top, but the protocol itself doesn't address process identity.
- **Sweep generation**: parameter sweeps, hyperparameter search, etc. The protocol is per-run.
- **Run metadata**: experiment names, tags, configurations, fingerprints. Not in scope for v0.1.
- **Visualization, dashboards, UIs**: any process that can open a `runstate` Channel can act as a UI. The protocol provides the substrate, not the surface.

These omissions are intentional. The protocol's value is in being small and composable.
