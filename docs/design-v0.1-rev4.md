# runstate v0.1 — Design

**Status:** approved scope, ready for implementation plan
**Date:** 2026-05-27
**Supersedes:**
- `design-v0.1-original.md` (initial cut with Store + Hasher + reuse-by-hash + Preempter + Phase + typed events)
- `design-v0.1-rev2.md` (Store/Hasher cut; kept Preempter + Phase)
- `design-v0.1-rev3-overcut.md` (also cut Preempter + all typed messages; recovered after second review)

## Goal

`runstate` provides **two-way cooperative control** between an orchestrator and a long-running scientific worker, plus the small vocabulary of typed messages needed to make that control useful in practice.

Two architectural commitments:

1. **A pattern-neutral substrate** — durable bidirectional IPC scoped to per-run identity, plus process spawn/observe. The substrate transports JSON-serializable dicts; it has no opinion about what they mean.

2. **An opinionated minimum vocabulary** — typed commands (orchestrator → worker) for cooperative preempt and runtime reconfiguration; typed events (worker → orchestrator) for progress reporting and self-described exits. Ships as opt-in `runstate.commands` and `runstate.report` modules, analogous to how `tensorboard` and `wandb` ship importable utilities for writing the standard event shapes.

The substrate is the load-bearing abstraction; the vocabulary is the load-bearing opinion. Together they distinguish runstate from generic file-based IPC.

## Positioning

Tracking tools (TensorBoard, wandb, MLflow, neptune) are **one-way data-plane file sinks**: the training script writes events; a frontend reads them; the channel is asynchronous and unidirectional. wandb's sweep agents introduce a sliver of control at run boundaries (pre-launch param selection) but no in-loop bidirectionality.

runstate is the **two-way control-plane** the data-plane tools don't provide:

```
   sweep generator (Hydra / Optuna / custom)
            │ configs
            ▼
      runstate orchestrator                          ← runstate v0.1 (substrate + helpers)
   ┌────────┴────────┐
   │                 │
 Channel          Launcher
 (durable IPC)    (process spawn)
   │                 │
   ▼                 ▼
 worker script ──→ tracker (wandb / TB)              ← one-way data plane
   │                 │
   │                 ▼
   │            web UI, plots
   ▼
 your training loop
```

Workers report progress in two directions independently: to the tracker for visualization (one-way data plane), and to the runstate Channel for cooperative control (two-way control plane). The two are complementary, not competitive.

## Architecture

Six modules. Three Protocols. Two opt-in helper modules with opinionated minimum vocabulary.

```
runstate/
  __init__.py
  channel/
    __init__.py         # Channel Protocol + open_channel factory
    file.py             # FileChannel
    sqlite.py           # SqliteChannel
  launcher.py           # Launcher + ProcessHandle Protocols + LocalLauncher + ThreadLauncher
  orchestrate.py        # Orchestrator + RunResult + CompletionReason
  commands.py           # opt-in: typed orchestrator → worker commands
  report.py             # opt-in: typed worker → orchestrator events
tests/
  conftest.py
  fixtures/
    toy_worker.py
  test_channel.py       # parametrized over backends
  test_launcher.py      # parametrized over backends
  test_commands.py
  test_report.py
  test_orchestrate.py   # integration
```

Modules depend only on direct neighbors. `commands` and `report` import `channel` (the Protocol). `orchestrate` composes Channel + Launcher + recognizes `report.Stopped` for inference. Workers import `runstate.attach()` plus optionally `commands` and `report`.

## Channel (`channel/__init__.py`)

Per-run, bidirectional, durable message transport. Pattern-neutral — supports async-polling and synchronous-yield equally via `recv(timeout=...)` semantics.

### Protocol

```python
from typing import Protocol, Literal

class Channel(Protocol):
    """A directional view of a per-run message channel.

    A Channel instance has a fixed direction determined by its role.
    Two Channel instances pointed at the same run_id with opposite roles
    form a bidirectional transport.

    Messages are JSON-serializable dicts. The Channel imposes no schema
    beyond serializability — the typed vocabulary in runstate.commands /
    runstate.report is OPTIONAL; users may send arbitrary dicts.
    """

    role: Literal["worker", "orchestrator"]
    run_id: str

    def send(self, message: dict) -> None:
        """Send a message. Durable: survives process crash.
        Uses atomic write semantics. Returns when the message is committed."""

    def recv(self, timeout: float | None = None) -> dict | None:
        """Receive the next unread message in this direction.

        timeout=0     → non-blocking, returns None if no message
        timeout=N>0   → poll up to N seconds; backend-defined latency floor
        timeout=None  → block indefinitely until a message arrives
        """

    def close(self) -> None:
        """Release resources (file handles, DB connections)."""


def open_channel(
    run_id: str,
    *,
    role: Literal["worker", "orchestrator"],
    root: str,
    backend: Literal["file", "sqlite"] = "file",
) -> Channel:
    """Open a Channel by run_id. Function factory; no class needed."""
```

### Semantics

- Messages are dicts; the Channel guarantees JSON-serializability and atomic durable write.
- `send` blocks until the message is committed to durable storage.
- `recv` is direction-aware: a worker-role Channel never returns its own sent messages.
- Crash recovery: a process can re-attach to the same `run_id` after a crash and see messages sent by the other party in the meantime.
- Order: messages delivered in send order (per direction).
- No history: a Channel does not promise to return already-consumed messages. v0.1 implementations MAY discard consumed messages or keep them. Users who want message history should use a tracker (wandb/MLflow) for that data, not runstate.

### Backends

**`FileChannel`** (`channel/file.py`) — zero deps.

- Layout: `<root>/<run_id>/{to_worker, to_orchestrator}/<seq>.json`
- Send: write atomic via tempfile + rename within the same directory; sequence number assigned monotonically (via `fcntl.flock` on the per-direction directory)
- Recv: scan receive directory for unconsumed sequences; read smallest; delete after successful read
- Polling: sleep 50ms between scans when timeout > 0
- Platform: Unix only (uses stdlib `fcntl`). Windows support deferred.

**`SqliteChannel`** (`channel/sqlite.py`) — stdlib `sqlite3`.

- One DB per run: `<root>/<run_id>/channel.db`
- Schema: `messages(id INTEGER PRIMARY KEY, direction TEXT, payload TEXT, created_at REAL, consumed_at REAL)` with index on `(direction, consumed_at)`
- WAL mode for concurrent reads alongside writes
- Send: INSERT + COMMIT
- Recv: SELECT WHERE direction = ? AND consumed_at IS NULL ORDER BY id LIMIT 1; UPDATE consumed_at on success; poll on empty result

Both back the same Channel Protocol. Conformance tests run against both.

## Launcher (`launcher.py`)

Process spawning + observation. Two backends in v0.1 — the Protocol earns its keep via dual conformance testing.

### Protocols

```python
class ProcessHandle(Protocol):
    """Observable handle to a launched process. Backend-defined identity."""

    identity: object                    # backend-defined; typed concretely in impls

    def poll(self) -> int | None:
        """Return exit code if done, else None. Non-blocking."""

    def terminate(self) -> None:
        """Request graceful termination (SIGTERM-equivalent)."""

    def kill(self) -> None:
        """Force termination (SIGKILL-equivalent)."""

    def wait(self, timeout: float | None = None) -> int:
        """Block until done. Return exit code. Raises TimeoutError on timeout."""


class Launcher(Protocol):
    """Spawns processes and returns observable handles."""

    def launch(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ProcessHandle: ...
```

### Default implementations

**`LocalLauncher`** — wraps `subprocess.Popen`. Returns `LocalProcessHandle` with `identity: int` (OS PID). ~30 LOC including the handle wrapper.

**`ThreadLauncher`** — for testing. Runs the "worker" in-process in a thread; useful for fast Protocol conformance tests without subprocess overhead. `identity: int` is a thread ID. Doesn't support real cancellation (Python threads can't be killed cooperatively), so `terminate()` raises an internal flag the worker thread is expected to poll; `kill()` is a hard `TimeoutError` from the user's perspective. Documented as test-only.

The two backends together justify the Protocol abstraction: `ThreadLauncher` exercises the same `Launcher` contract as `LocalLauncher` without spawning processes, making the conformance tests fast.

Other launchers (`SubmititLauncher`, `RayLauncher`, etc.) live in optional packages, not v0.1.

## Commands module (`commands.py`) — opt-in

Typed orchestrator → worker commands. The library's load-bearing opinion: these are the cooperative-control primitives that distinguish runstate from generic IPC.

### Types

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass(frozen=True)
class StopNow:
    """Ask the worker to stop ASAP at its next safe point."""
    type: Literal["StopNow"] = "StopNow"

@dataclass(frozen=True)
class StopAtStep:
    """Ask the worker to stop when its current step reaches `at`.

    The worker's check() polls drained messages and decides; deferred
    preempt is a worker-side decision based on current state.
    """
    at: int
    type: Literal["StopAtStep"] = "StopAtStep"

@dataclass(frozen=True)
class Reconfigure:
    """Update worker hyperparameters or runtime state mid-flight.

    Payload semantics are user-defined — the worker interprets `params`.
    Common uses: LR decay, batch size adjust, temperature changes,
    curriculum stage advancement.
    """
    params: dict
    type: Literal["Reconfigure"] = "Reconfigure"

Command = StopNow | StopAtStep | Reconfigure  # the standard recognized set
```

### Orchestrator-side helpers

```python
def send_stop(channel: Channel) -> None:
    """Send StopNow over the channel."""
    channel.send({"type": "StopNow"})

def send_stop_at_step(channel: Channel, step: int) -> None:
    """Send StopAtStep(at=step) over the channel."""
    channel.send({"type": "StopAtStep", "at": step})

def send_reconfigure(channel: Channel, params: dict) -> None:
    """Send Reconfigure(params=params) over the channel."""
    channel.send({"type": "Reconfigure", "params": params})
```

### Worker-side helper

```python
def check(channel: Channel, *, current_step: int | None = None) -> Command | None:
    """Drain pending messages and return any active Command.

    StopNow → returned immediately
    StopAtStep(at=N) → returned only if current_step >= N (deferred preempt
                       is evaluated worker-side); held for future calls
                       otherwise
    Reconfigure → returned (worker decides what to do)
    Non-Command dicts → ignored by this helper; read via channel.recv()
                        directly if you want them

    If multiple StopAtStep arrive, the most recent one's `at` is held.
    StopNow always wins over any pending StopAtStep.
    """
```

The `check()` function maintains internal state (the most recent pending `StopAtStep`) so deferred preempts work across calls. Implementation detail: it uses a module-level dict keyed on `(channel.role, channel.run_id)` so multiple checks per run share state.

### Usage

Sending commands from the orchestrator:
```python
from runstate import commands

def watcher(msg: dict, send_to_worker: Callable[[dict], None]):
    if msg.get("type") == "Progress" and msg["metrics"].get("loss", 0) > 1000:
        # Loss diverging — stop the run
        send_to_worker({"type": "StopNow"})
```

Or via the helper (more typed):
```python
def watcher(msg: dict, channel: Channel):
    if msg.get("type") == "Progress" and msg["metrics"].get("loss", 0) > 1000:
        commands.send_stop(channel)
```

Worker side:
```python
from runstate import commands

ch = runstate.attach()

for step in range(max_steps):
    state = train_step(state)
    # ... reporting via runstate.report (see next section)

    cmd = commands.check(ch, current_step=step)
    match cmd:
        case commands.StopNow():
            # Exit gracefully
            return
        case commands.StopAtStep(at=at):
            # Deferred preempt fired; checkpoint and exit
            return
        case commands.Reconfigure(params=params):
            apply_hyperparams(state, params)
        case None:
            pass
```

## Report module (`report.py`) — opt-in

Typed worker → orchestrator events. The "outbound" counterpart to `commands.py`.

### Types

```python
@dataclass(frozen=True)
class Progress:
    """Periodic report of training state to the orchestrator.

    Send as often as you want orchestrator visibility — every step,
    every N steps, every wall-clock interval. `metrics` is a user-defined
    dict of numeric values (loss, accuracy, GPU memory, etc.).
    """
    step: int
    metrics: dict
    type: Literal["Progress"] = "Progress"

@dataclass(frozen=True)
class Stopped:
    """Worker's self-described exit notification.

    Sent immediately before the worker exits (whether natural completion,
    self-detected divergence, preempt acknowledgment, etc.). The orchestrator
    uses this to set RunResult.completion_reason precisely.

    Common reasons: "natural", "preempted", "diverged", "nan_detected",
                    "patience_triggered", "oom", "user_interrupt"

    Users may use any reason string; the standard set is documented but
    not enforced.
    """
    reason: str
    metadata: dict | None = None
    type: Literal["Stopped"] = "Stopped"

Event = Progress | Stopped
```

### Helpers

```python
def progress(channel: Channel, *, step: int, metrics: dict) -> None:
    """Send a Progress event."""
    channel.send({"type": "Progress", "step": step, "metrics": metrics})

def stopped(channel: Channel, *, reason: str, metadata: dict | None = None) -> None:
    """Send a Stopped event. Call immediately before exiting."""
    channel.send({"type": "Stopped", "reason": reason, "metadata": metadata})
```

### Usage

```python
from runstate import report, commands

ch = runstate.attach()

for step in range(max_steps):
    state = train_step(state)
    report.progress(ch, step=step, metrics={"loss": state.loss, "lr": state.lr})

    if math.isnan(state.loss):
        report.stopped(ch, reason="nan_detected", metadata={"step": step})
        return

    cmd = commands.check(ch, current_step=step)
    if cmd is not None:
        report.stopped(ch, reason="preempted", metadata={"step": step, "command": cmd.__class__.__name__})
        return

report.stopped(ch, reason="natural", metadata={"step": step})
```

## Orchestrate (`orchestrate.py`)

Concrete orchestrator that composes Channel + Launcher for a single run, with awareness of `report.Stopped` for precise completion-reason tracking.

### Types

```python
from enum import StrEnum
from dataclasses import dataclass

class CompletionReason(StrEnum):
    """Why a run stopped. Coarse-grained classification; the precise
    worker-reported reason (e.g., 'nan_detected', 'patience_triggered')
    lives separately in RunResult.completion_detail."""
    NATURAL = "natural"               # worker completed successfully
    FAILED = "failed"                 # worker self-aborted or crashed
    PREEMPTED = "preempted"           # orchestrator asked it to stop
    KILLED = "killed"                 # orchestrator force-killed (e.g., timeout grace expired)


@dataclass
class RunResult:
    run_id: str
    exit_code: int
    completion_reason: CompletionReason
    completion_detail: str | None      # the raw `reason` string from
                                       # report.Stopped, if any; preserves
                                       # custom reasons like "nan_detected"
    duration_seconds: float
    messages_received: int
    messages_sent: int
```

### Orchestrator API

```python
class Orchestrator:
    """Dispatches a worker run; observes its Channel; blocks until exit."""

    def __init__(
        self,
        launcher: Launcher,
        channel_root: str,
        backend: Literal["file", "sqlite"] = "file",
    ): ...

    def run(
        self,
        cmd: list[str],
        *,
        run_id: str | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        on_message: Callable[[dict, Callable[[dict], None]], None] | None = None,
        timeout: float | None = None,
        timeout_grace_seconds: float = 30.0,
    ) -> RunResult:
        """Launch the worker, observe its Channel, block until exit.

        on_message: invoked for each message received from the worker.
        Signature: on_message(msg: dict, send: Callable[[dict], None]) -> None.
        The `send` callable enqueues a message to the worker — use it for
        in-loop replies (cooperative control). Wrap with runstate.commands
        helpers if you want typed sending:

            def watcher(msg, send):
                if needs_stop(msg):
                    send({"type": "StopNow"})

        timeout: if set, the orchestrator sends StopNow when the deadline
        expires, waits timeout_grace_seconds for cooperative exit, then
        calls handle.kill() if still running. Preserves cooperative
        discipline even on hard timeouts.

        CompletionReason precedence (first match wins):
          1. If kill() was called (timeout grace expired) → KILLED
          2. Else if worker sent report.Stopped → map its `reason` to the enum:
               "natural"   → NATURAL
               "preempted" → PREEMPTED
               anything else → FAILED   (worker self-aborted with a custom reason)
             The raw `reason` string is always preserved in completion_detail.
          3. Else if orchestrator sent any StopNow/StopAtStep → PREEMPTED
          4. Else if exit_code == 0 → NATURAL
          5. Else (exit_code != 0) → FAILED

        Note: KILLED takes precedence over Stopped because if kill() fired,
        the Stopped message (if any) doesn't represent a graceful exit. The
        worker's intent was overridden by the timeout.
        """
```

### Worker-side API

```python
def attach(
    run_id: str | None = None,
    *,
    root: str | None = None,
    backend: Literal["file", "sqlite"] | None = None,
) -> Channel:
    """Attach to the worker-role Channel for this run.

    Reads RUNSTATE_RUN_ID, RUNSTATE_CHANNEL_ROOT, RUNSTATE_CHANNEL_BACKEND
    from env if any argument is None. Raises RuntimeError if env vars are
    missing AND no explicit args provided.

    Pass explicit arguments to attach standalone (debugging outside an
    orchestrator).
    """
```

### A complete minimal example

**Worker** (`train.py`):
```python
import runstate
from runstate import report, commands

ch = runstate.attach()
state = init_state()

for step in range(max_steps):
    state = train_one_step(state)
    report.progress(ch, step=step, metrics={"loss": state.loss})

    cmd = commands.check(ch, current_step=step)
    match cmd:
        case commands.StopNow() | commands.StopAtStep():
            save_checkpoint(state)
            report.stopped(ch, reason="preempted", metadata={"step": step})
            return
        case commands.Reconfigure(params=params):
            apply_hyperparams(state, params)
        case None:
            pass

report.stopped(ch, reason="natural", metadata={"final_step": step})
```

**Orchestrator** (`run.py`):
```python
import runstate
from runstate import commands

def watcher(msg, send):
    if msg.get("type") == "Progress":
        loss = msg["metrics"].get("loss", 0)
        print(f"step {msg['step']} loss {loss:.4f}")
        if loss > 1000:
            send({"type": "StopNow"})            # divergence preempt
        elif msg["step"] == 200 and loss > 10:   # patience-style
            send({"type": "Reconfigure", "params": {"lr": 1e-5}})

orch = runstate.Orchestrator(
    launcher=runstate.LocalLauncher(),
    channel_root="/tmp/runstate",
    backend="file",
)
result = orch.run(cmd=["python", "train.py"], on_message=watcher)
print(f"finished: {result.completion_reason} ({result.completion_detail})")
```

## Testing

### Conformance tests for Channel (parametrized over file + sqlite)

Every Channel Protocol method covered:
- Round-trip send/recv
- Direction safety (worker can't read its own sent messages)
- Crash recovery (close + reopen, messages still readable)
- Atomicity (partial write doesn't surface as partial read)
- Ordering (per-direction send order preserved)
- Timeout semantics (`timeout=0` returns immediately; `timeout=N>0` blocks ≤N seconds; `timeout=None` blocks until message)

### Conformance tests for Launcher (parametrized over local + thread)

- `launch(["python", "-c", "exit(0)"])` → `handle.wait()` returns 0
- `launch(["python", "-c", "import time; time.sleep(10)"])` → `terminate()` ends it within ~1s
- `poll()` returns None for running process, integer after exit
- `kill()` force-terminates

### Commands module tests

- `send_stop_at_step(channel, 200)` + worker's `check(channel, current_step=199)` → returns None
- `send_stop_at_step(channel, 200)` + worker's `check(channel, current_step=200)` → returns StopAtStep
- Multiple StopAtStep in flight: the latest one's `at` wins
- StopNow short-circuits any pending StopAtStep
- Reconfigure with `params={"lr": 1e-5}` arrives intact at the worker
- Non-Command dicts are silently passed by `check()` (returns None, doesn't surface them)

### Report module tests

- `progress(channel, step=10, metrics={"loss": 1.5})` arrives at orchestrator as `{"type": "Progress", "step": 10, "metrics": {"loss": 1.5}}`
- `stopped(channel, reason="diverged", metadata={"step": 50})` same

### Orchestrator integration tests

A toy worker (`tests/fixtures/toy_worker.py`):
1. `runstate.attach()`
2. Sends progress messages each "step"
3. Polls commands; exits on StopNow/StopAtStep
4. Sends `Stopped` before exit

Integration tests dispatch the toy worker and verify:
- `on_message` callback receives every message
- Sending StopNow via the `send` callable causes cooperative exit
- `RunResult.completion_reason` is `PREEMPTED` when orchestrator sent a Stop
- `RunResult.completion_reason` is `NATURAL` when worker self-completes with `Stopped(reason="natural")`
- `RunResult.completion_detail` preserves custom Stopped reasons (e.g., `"nan_detected"`)
- `RunResult.completion_reason` is `FAILED` when worker exits non-zero without sending Stopped
- `RunResult.completion_reason` is `KILLED` after timeout-based kill
- Hard timeout path: orchestrator sends StopNow, waits `timeout_grace_seconds`, then kills

## Decisions made (closed)

- **`recv` blocking semantics in FileChannel**: poll with 50ms sleep. No `inotify`. Latency floor documented.
- **JSON serialization**: messages serialized with `json.dumps(sort_keys=True, separators=(",", ":"))` for byte-stable on-disk representation.
- **File locking**: stdlib `fcntl.flock` (Unix only). Windows support deferred.
- **Run identity**: plain `str` in v0.1. No `RunId` NewType.
- **Standard message vocabulary**: 3 commands (StopNow, StopAtStep, Reconfigure), 2 events (Progress, Stopped). Ships in `runstate.commands` and `runstate.report` as opt-in. Users CAN send raw dicts via Channel for non-standard messages; the helpers don't preclude that.
- **Pause/Resume, Snapshot, Cleanup**: out of v0.1. Pause/Resume can be modeled as Stop+restart externally. Snapshot is well-defined but workload-specific; users can define their own dict shape. Cleanup is too workload-specific to standardize.

## Out of v0.1 scope

Deferred:

**v0.2 (next):**
- `Store` Protocol + backends: relational metadata for runs and experiments (many-to-many membership)
- `Hasher` Protocol + `DefaultHasher`: content-addressable input fingerprinting
- Reuse-by-hash in the orchestrator
- Multi-run sweep loop helper (`orchestrator.run_sweep`)
- Fire-and-forget background worker + CLI status display
- Pause/Resume + Snapshot as opt-in additions to `runstate.commands`

**v0.3+:**
- Resume budgets, smoke gate, parallel dispatch
- `runstate-postgres` (Postgres backend for Channel + future Store)
- `runstate-submitit`, `runstate-ray` launcher packages
- `runstate-hydra` (config + sweep adapter)
- `runstate-mlflow` (Store-to-MLflow exporter when Store ships)

**Never in this library:**
- Web UI / visualization (use wandb/MLflow for this)

## Dependencies

**Runtime (v0.1):**
- Python 3.11+
- stdlib only

**Dev:**
- `pytest`

## Success criteria

v0.1 is shippable when:

1. **Channel** Protocol implemented with both backends (file + sqlite); conformance tests pass for both.
2. **Launcher** Protocol implemented with both backends (local + thread); conformance tests pass for both.
3. **Commands** module: `StopNow`, `StopAtStep` (with deferred semantics), `Reconfigure` work end-to-end; tests cover the `check()` drain logic including multi-message scenarios.
4. **Report** module: `Progress`, `Stopped` events round-trip cleanly.
5. **Orchestrator** integration test passes: the toy worker dispatched, orchestrator observes every message, sends StopNow via the callback path, worker exits cooperatively with `Stopped(reason="preempted")`, RunResult precisely reflects the outcome including custom reason in `completion_detail`.
6. **CompletionReason inference**: the test matrix covers all five branches of the inference logic (Stopped present, orchestrator preempted, exit 0, exit non-zero, kill-on-timeout).
7. **README** documents:
   - The substrate (Channel + Launcher + Orchestrator)
   - The opinionated minimum vocabulary (`runstate.commands`, `runstate.report`)
   - The complementary-to-wandb/Hydra positioning (control plane vs data plane)
   - A complete minimal example
   - Two recipe sections: async-polling pattern (the example) and synchronous-yield pattern (worker calls `recv(timeout=None)`)
8. The library installs cleanly via `pip install -e ~/src/runstate` and imports as `runstate`.

## Repository layout

```
~/src/runstate/
  pyproject.toml
  README.md
  LICENSE                          # MIT
  .gitignore
  runstate/
    __init__.py                    # re-exports the public surface:
                                   #   Channel, open_channel, attach
                                   #   Launcher, ProcessHandle
                                   #   LocalLauncher, ThreadLauncher
                                   #   Orchestrator, RunResult, CompletionReason
    channel/
      __init__.py                  # Channel Protocol, open_channel factory
      file.py                      # FileChannel
      sqlite.py                    # SqliteChannel
    launcher.py                    # Launcher + ProcessHandle Protocols + 2 backends
    commands.py                    # StopNow, StopAtStep, Reconfigure, check, send_*
    report.py                      # Progress, Stopped, progress, stopped
    orchestrate.py                 # Orchestrator + RunResult + CompletionReason
  tests/
    conftest.py
    fixtures/
      toy_worker.py
    test_channel.py
    test_launcher.py
    test_commands.py
    test_report.py
    test_orchestrate.py
  docs/
    design-v0.1.md                 # this file
    design-v0.1-original.md        # history
    design-v0.1-rev2.md            # history
    design-v0.1-rev3-overcut.md    # history (the over-cut version)
  examples/
    minimal/
      worker.py
      orchestrator.py
    divergence_preempt/            # the canonical use case
      worker.py
      orchestrator.py
    sync_yield/                    # synchronous RPC pattern
      worker.py
      orchestrator.py
```

## What changed from previous revisions — summary

| Rev | What it had | What was cut going to next rev |
|---|---|---|
| **Original** | Store + Hasher + reuse + Preempter + Phase + typed events | Store/Hasher/reuse (over-spec for v0.1) |
| **Rev 2** | Preempter + Phase + typed events (no Store/Hasher) | Phase + typed events (over-opinionated) |
| **Rev 3 (overcut)** | Pure substrate, no opinions | (none — over-correction) |
| **This rev (final)** | Substrate + minimum cooperative-preempt vocabulary | — |

The arc: started over-specced, swung to over-cut, settled on minimum-opinionated-vocabulary as the right balance. ~900-1100 LOC core. The substrate has no opinions (Channel transports dicts); the helpers have exactly the opinions needed to express the canonical use cases (divergence preempt, step budgets, runtime reconfiguration).
