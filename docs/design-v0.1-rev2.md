# runstate v0.1 — Design

**Status:** approved scope, ready for implementation plan
**Date:** 2026-05-27
**Supersedes:** design-v0.1-original.md (replaced after spec review re-cut scope)

## Goal

`runstate` provides **cooperative bidirectional control between an orchestrator and long-running scientific workers**. The library owns the protocol layer that's missing from the existing ML tooling stack: deferred preempt, phase reporting, and safe-point discipline.

Concretely, runstate lets the orchestrator say to a running worker:
- "Stop now" (request graceful shutdown)
- "Stop at step N" (deferred preempt — fire when the worker crosses the threshold)
- arbitrary user-defined commands (the Channel transports JSON-serializable dicts)

And lets the worker say to the orchestrator:
- "I've reached phase X" (PhaseChange)
- "Step K complete, here's some progress info" (Progress)
- arbitrary user-defined messages

The protocol survives orchestrator crashes (messages are durable on disk), and respects cooperative-preemption discipline (the worker chooses when to check; the orchestrator never SIGKILLs mid-checkpoint).

## Positioning

```
   sweep generator (Hydra / Optuna)        ← config sweeps; existing tools
            │
            ▼
      runstate orchestrator                 ← runstate v0.1
   ┌────────┴────────┐
   │                 │
 Channel          Launcher
 (IPC)            (process spawn)           ← runstate v0.1
   │                 │
   ▼                 ▼
 worker script ──→ tracker (wandb)          ← existing tools (passive metric logging)
   │                 │
   │                 ▼
   │            web UI, plots
   ▼
 your training loop
```

Other tools at the orchestration layer (Hydra+submitit, sacred, dvc, MLflow Projects) launch processes and forget. None of them offer durable bidirectional control during a run. **That's the gap runstate fills.**

The worker reports metrics to the tracker (data-plane) AND to the runstate Channel (control-plane). The two are independent and complementary.

## Architecture

Five modules. Three Protocols (Channel, Launcher, ProcessHandle). Two concrete helper classes (Preempter, SafePoint). One concrete orchestrator.

```
runstate/
  __init__.py
  types.py              # Phase, CompletionReason, Command types
  channel/
    __init__.py         # Channel Protocol
    file.py             # FileChannel + FileChannelFactory
    sqlite.py           # SqliteChannel + SqliteChannelFactory
  preempt.py            # Preempter + SafePoint concrete helpers
  launcher.py           # Launcher + ProcessHandle Protocols + LocalLauncher
  orchestrate.py        # Orchestrator concrete class
tests/
  conftest.py
  test_channel.py       # parametrized over backends
  test_preempt.py
  test_launcher.py
  test_orchestrate.py
```

Modules don't import each other except: `channel/*` shares the Protocol; `preempt.py` imports `types.py` and the Channel Protocol; `orchestrate.py` composes all four.

## Shared types (`types.py`)

```python
from enum import StrEnum
from dataclasses import dataclass
from typing import Literal

# Phases the orchestrator understands natively.
# Workers may send other strings; orchestrator records them as-is.
class Phase(StrEnum):
    PENDING = "pending"
    LOADING = "loading"
    TRAINING = "training"
    SAVING = "saving"
    DONE = "done"
    FAILED = "failed"

# Why a run stopped. Distinguishes natural completion from external preempt.
class CompletionReason(StrEnum):
    NATURAL = "natural"             # worker decided it was done
    PREEMPTED = "preempted"         # orchestrator told it to stop
    FAILED = "failed"               # crashed / errored
    TIMEOUT = "timeout"             # external (launcher) killed it

# Standard command types from orchestrator to worker.
# Users may send other dicts via Channel directly; these are the recognized standard set.
@dataclass(frozen=True)
class StopNow:
    type: Literal["StopNow"] = "StopNow"

@dataclass(frozen=True)
class StopAtStep:
    at: int
    type: Literal["StopAtStep"] = "StopAtStep"

Command = StopNow | StopAtStep   # the standard recognized set

# Standard message types from worker to orchestrator.
@dataclass(frozen=True)
class PhaseChange:
    phase: str                       # use Phase enum values or arbitrary string
    type: Literal["PhaseChange"] = "PhaseChange"

@dataclass(frozen=True)
class Progress:
    step: int
    payload: dict                    # user-defined (loss, metrics, etc.)
    type: Literal["Progress"] = "Progress"

@dataclass(frozen=True)
class Completed:
    reason: CompletionReason
    final_step: int | None = None
    type: Literal["Completed"] = "Completed"

Event = PhaseChange | Progress | Completed   # the standard recognized set
```

**Design choices:**
- `Phase` is a closed enum with a typed `str` widening: the orchestrator's data model uses `str` for `phase` so worker-defined phases are storable; `Phase.TRAINING.value` is the canonical string for the common ones. No `Phase | str` contradiction.
- Commands and Events are typed dataclasses for the standard cases; users send arbitrary dicts through Channel for non-standard messages. The library only special-cases the standard set; everything else passes through as opaque `dict`.
- No `RunId` NewType. v0.1 uses plain `str` for run identifiers. v0.2 may reconsider if reuse-by-hash returns.

## Channel (`channel/__init__.py`)

Bidirectional durable per-run message transport between an orchestrator and a worker.

### Protocol

```python
from typing import Protocol, Literal, Iterator

class Channel(Protocol):
    """A directional view of a per-run message channel.

    Worker-role channels send to the orchestrator and recv from it.
    Orchestrator-role channels do the opposite. Two Channel instances
    pointed at the same run_id with opposite roles form a bidirectional
    transport.
    """

    role: Literal["worker", "orchestrator"]
    run_id: str

    def send(self, message: dict) -> None:
        """Send a message. Durable: survives process crash.
        Uses atomic write semantics. Returns when the message is committed."""

    def recv(self, timeout: float | None = None) -> dict | None:
        """Receive the next unread message in this direction.

        timeout=0 → non-blocking, returns None if no message.
        timeout=N → poll up to N seconds; backend-defined latency floor.
        timeout=None → block indefinitely.
        """

    def close(self) -> None:
        """Release any resources (file handles, DB connections)."""


def open_channel(
    run_id: str,
    *,
    role: Literal["worker", "orchestrator"],
    root: str,
    backend: Literal["file", "sqlite"] = "file",
) -> Channel:
    """Factory function. Returns a Channel pointed at the given run_id.

    Replaces the original spec's ChannelFactory class — a function is
    sufficient for the v0.1 needs.
    """
```

### Semantics

- Messages are JSON-serializable dicts. The Channel doesn't impose schema beyond serializability.
- `send` blocks until the message is durably committed (atomic write or SQL transaction).
- `recv` is direction-aware: a worker-role channel never returns its own sent messages.
- A worker that crashes and restarts can re-attach to the same `run_id` and receive any messages the orchestrator sent in the meantime.
- An orchestrator that crashes can re-attach and receive any messages the worker sent in the meantime. The full state is durable on disk.
- The Channel does NOT provide a message-history API. v0.1 does not promise the ability to iterate past messages; backends may discard consumed messages. (v0.2 may add an EventLog Protocol for this.)

### Default implementations

**`FileChannel`** (`channel/file.py`) — zero deps.
- Run dir layout: `<root>/<run_id>/messages/{to_worker, to_orchestrator}/<seq>.json`
- Send: atomic write of `<seq>.json` (write tempfile + rename within the same directory)
- Recv: list dir → filter unconsumed seq numbers → read smallest → atomic-delete after read
- Polling: scan the receive directory; sleep 50ms between polls when timeout > 0

**`SqliteChannel`** (`channel/sqlite.py`) — stdlib `sqlite3`.
- One DB per run: `<root>/<run_id>/channel.db`
- Schema: `messages(id INTEGER PRIMARY KEY, direction TEXT, payload TEXT, created_at REAL, consumed_at REAL)`
- WAL mode for concurrent reads with one writer per direction
- Send: INSERT, commit
- Recv: SELECT WHERE direction = ? AND consumed_at IS NULL ORDER BY id LIMIT 1; UPDATE consumed_at = now() on success; poll on empty result

Both back the same Channel Protocol; choice is a one-line factory swap.

## Preempt (`preempt.py`)

Concrete helper classes that provide typed, ergonomic access to cooperative-preemption patterns on top of Channel. **These are not Protocols** — users wanting custom command vocabularies send raw dicts through Channel directly.

### Orchestrator-side helper

```python
class Preempter:
    """Orchestrator-side convenience for sending preempt commands.

    Wraps a Channel and provides typed methods for the standard Command vocabulary.
    Sending a non-standard command? Use channel.send(dict) directly.
    """

    def __init__(self, channel: Channel):
        assert channel.role == "orchestrator"
        self.channel = channel

    def request_stop(self) -> None:
        """Ask the worker to stop ASAP (at its next safe point)."""
        self.channel.send({"type": "StopNow"})

    def request_stop_at_step(self, step: int) -> None:
        """Ask the worker to stop when it crosses `step`. The worker fires
        at the first safe point where its current step >= the requested step."""
        self.channel.send({"type": "StopAtStep", "at": step})
```

### Worker-side helper

```python
class SafePoint:
    """Worker-side convenience for checking preempt commands at safe points.

    Wraps a Channel and provides a single check() method that returns the
    pending Command, if any. Workers call this between steps; the return
    value tells them whether to stop.
    """

    def __init__(self, channel: Channel):
        assert channel.role == "worker"
        self.channel = channel
        self._pending: Command | None = None

    def check(self, step: int) -> Command | None:
        """Drain pending commands; return one if a preempt condition is met.

        Returns:
          StopNow() — caller should checkpoint and exit immediately
          StopAtStep(at=N) — caller should checkpoint and exit (the at field
                             is for informational use; the SafePoint already
                             checked whether step >= N)
          None — no preempt requested; continue training

        Non-Command dict messages (user-defined commands) are NOT returned by
        this helper. Read those via channel.recv() directly.
        """
        # Drain all queued messages, holding onto the latest preempt command
        while True:
            msg = self.channel.recv(timeout=0)
            if msg is None:
                break
            if msg.get("type") == "StopNow":
                return StopNow()
            if msg.get("type") == "StopAtStep":
                self._pending = StopAtStep(at=msg["at"])
            # Other messages are ignored by this helper

        if self._pending and step >= self._pending.at:
            return self._pending
        return None
```

### Usage pattern (worker)

```python
import runstate

ch = runstate.attach()                  # see Worker-side API section
sp = runstate.SafePoint(ch)

ch.send({"type": "PhaseChange", "phase": "training"})

for step in range(max_steps):
    do_one_step()
    ch.send({"type": "Progress", "step": step, "payload": {"loss": loss}})

    cmd = sp.check(step=step)
    if cmd is not None:
        ch.send({"type": "PhaseChange", "phase": "saving"})
        save_checkpoint()
        ch.send({"type": "Completed", "reason": "preempted", "final_step": step})
        return

ch.send({"type": "Completed", "reason": "natural", "final_step": step})
```

The SafePoint discipline is **cooperative**: the worker chooses when to call `check()`. If the worker doesn't call it, the orchestrator can still send a SIGTERM via the Launcher, but that bypasses the cooperative protocol (no graceful checkpoint).

## Launcher (`launcher.py`)

Process spawning + observation. Pluggable backends.

### Protocols

```python
class ProcessHandle(Protocol):
    """Observable handle to a launched process."""

    identity: object                    # backend-defined identity; not necessarily an OS PID

    def poll(self) -> int | None:
        """Return exit code if process is done, else None. Non-blocking."""

    def terminate(self) -> None:
        """Request graceful termination (e.g., SIGTERM)."""

    def kill(self) -> None:
        """Force termination (e.g., SIGKILL)."""

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

### Default implementation

**`LocalLauncher`** — wraps `subprocess.Popen`. ~30 LOC including the `LocalProcessHandle` wrapper.

`LocalProcessHandle.identity` is the OS PID (an `int`). Users wanting the PID specifically can do `handle.identity` and the type is documented as `int` in `LocalProcessHandle`.

Other launchers (`SubmititLauncher`, `RayLauncher`, etc.) are out of v0.1 and live in optional packages. The Protocol is designed to accommodate them.

## Orchestrate (`orchestrate.py`)

Concrete v0.1 orchestrator. Wires Launcher + Channel together for one run.

### API

```python
@dataclass
class RunResult:
    run_id: str
    exit_code: int
    final_phase: str | None              # last reported phase, or None
    completion_reason: CompletionReason | None
    duration_seconds: float


class Orchestrator:
    """Dispatches one worker run at a time. Observes its Channel and process."""

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
        run_id: str | None = None,        # generate UUID if not given
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        run_dir: str | None = None,       # subdir under channel_root; default = run_id
        on_progress: Callable[[Progress, Preempter], None] | None = None,
        on_phase_change: Callable[[PhaseChange, Preempter], None] | None = None,
        on_message: Callable[[dict, Preempter], None] | None = None,
        timeout: float | None = None,
        timeout_grace_seconds: float = 30.0,
    ) -> RunResult:
        """Launch the worker, observe its Channel, block until exit.

        Injects RUNSTATE_RUN_ID, RUNSTATE_CHANNEL_ROOT, RUNSTATE_CHANNEL_BACKEND
        into the worker's env so it can attach().

        Callbacks receive a Preempter as their second argument — call
        preempter.request_stop() or preempter.request_stop_at_step(N) from a
        callback to send commands to the running worker. The Preempter is
        backed by the same Channel the Orchestrator is observing on.

        on_message fires for any dict message that isn't a recognized
        PhaseChange/Progress/Completed (i.e., user-defined messages).

        On timeout: send Preempter.request_stop(), wait `timeout_grace_seconds`
        for the worker to exit cooperatively, then kill() if still running.
        This preserves the cooperative-preempt discipline even on hard
        timeouts."""
```

### Behavior

1. Generate run_id (UUID) if not provided.
2. Create run directory under `channel_root`.
3. Open an orchestrator-role Channel for the run.
4. Augment env with `RUNSTATE_RUN_ID`, `RUNSTATE_CHANNEL_ROOT`, `RUNSTATE_CHANNEL_BACKEND`.
5. Launch the worker via `launcher.launch()`.
6. Loop: poll Channel for messages; invoke user callbacks; check process exit.
7. On process exit: read any remaining Channel messages; build RunResult.

A Preempter is **injected into callbacks** by the Orchestrator. This is the supported way to send commands from the same thread that's observing the run:

```python
def watch(progress: Progress, preempter: Preempter):
    if progress.payload.get("loss", 0) > 1000:
        preempter.request_stop()

orchestrator.run(cmd=[...], on_progress=watch)
```

Sending commands from a *different* thread (e.g., a UI thread reacting to user input) is supported but requires the user to construct their own Channel pointing at the same run_id, open in orchestrator role:

```python
# In the UI thread
ch = runstate.open_channel(run_id, role="orchestrator",
                           root=channel_root, backend=backend)
preempter = Preempter(ch)
preempter.request_stop_at_step(200)
```

This works because Channel is durable on disk; multiple orchestrator-role openings of the same channel are safe (one sender, the running orchestrator drains).

### Completion-reason inference

If the worker sends a `Completed(reason=...)` message before exiting, the Orchestrator records that reason. If the worker exits without sending Completed, the Orchestrator infers:

- Process exit code 0 → `CompletionReason.NATURAL` (the worker just finished)
- Process exit code != 0 → `CompletionReason.FAILED`
- `terminate()` / `kill()` was called by the Orchestrator → `CompletionReason.PREEMPTED` (cooperative) or `CompletionReason.TIMEOUT` (hard) depending on which path took it

RunResult.final_phase is the last `PhaseChange.phase` value observed, or `None` if no PhaseChange was sent.

### What v0.1 orchestrator does NOT do

- **No fire-and-forget background worker.** `run()` blocks until exit.
- **No multi-run sweep loop.** Users write `for cfg in configs: orchestrator.run(...)`.
- **No reuse-by-hash.** That's v0.2 (requires Store + Hasher).
- **No status display / CLI.** v0.2+.
- **No timeout-resume semantics.** A `timeout` on `run()` just hard-stops via `kill()`.

## Worker-side API

The worker attaches to its Channel via:

```python
def attach(
    run_id: str | None = None,
    *,
    root: str | None = None,
    backend: Literal["file", "sqlite"] | None = None,
    role: Literal["worker"] = "worker",
) -> Channel:
    """Attach to the Channel for this run.

    If run_id/root/backend are None, reads from RUNSTATE_RUN_ID,
    RUNSTATE_CHANNEL_ROOT, RUNSTATE_CHANNEL_BACKEND env vars (set by
    Orchestrator). Raises RuntimeError if the env vars are missing.

    Pass explicit arguments to attach standalone (e.g., for debugging).
    """
```

Standalone debugging:
```python
ch = runstate.attach(run_id="test", root="/tmp/runstate-debug", backend="file")
```

Production:
```python
ch = runstate.attach()  # reads env vars set by Orchestrator
```

The user always knows when they're using env-var magic (no args) vs explicit (args). No `NullChannel` for "running without an orchestrator" in v0.1 — if a user wants to run a worker script standalone, they pass `--no-runstate` or similar in their own arg parsing and skip the `attach()` call.

## Testing

### Conformance tests

A single parametrized test suite runs against every Channel backend:

```python
@pytest.fixture(params=["file", "sqlite"])
def backend(request):
    return request.param

def test_send_recv_roundtrip(backend, tmp_path):
    ch_w = runstate.open_channel("r1", role="worker", root=str(tmp_path), backend=backend)
    ch_o = runstate.open_channel("r1", role="orchestrator", root=str(tmp_path), backend=backend)
    ch_w.send({"a": 1})
    assert ch_o.recv() == {"a": 1}
    ch_o.send({"b": 2})
    assert ch_w.recv() == {"b": 2}
    assert ch_w.recv(timeout=0) is None   # no more messages
```

Every Channel Protocol method gets at least one test. Every backend must pass every test.

### Cross-cutting tests

- **Crash-recovery**: write messages with one Channel instance, close, open a fresh instance, verify messages are recoverable.
- **Atomicity**: simulate a partial write (interrupted send), verify reads don't see the partial message.
- **Direction safety**: worker can't read its own sent messages.

### Preempt + SafePoint tests

- Preempter.request_stop_at_step(N) → SafePoint.check(step=N-1) returns None; check(step=N) returns StopAtStep.
- Preempter.request_stop() → SafePoint.check returns StopNow regardless of step.
- Multiple commands sent: SafePoint returns the latest StopAtStep (overwriting earlier ones); StopNow always wins.

### Launcher tests

- LocalLauncher spawns `python -c "exit(0)"`, ProcessHandle.wait() returns 0.
- LocalLauncher spawns `python -c "import time; time.sleep(10)"`, ProcessHandle.terminate() ends it within ~1s.
- ProcessHandle.poll() returns None for a running process, integer after exit.

### Orchestrator integration tests

A toy worker script (in `tests/fixtures/`) that:
1. Calls `runstate.attach()`
2. Sends PhaseChange events
3. Sends Progress events for N steps
4. Calls SafePoint.check() each step
5. Exits on preempt or natural completion

Integration tests dispatch this script via Orchestrator and verify:
- Phase changes are observed via the on_phase_change callback
- Progress events are observed via the on_progress callback
- Sending Preempter.request_stop_at_step(N) results in the worker exiting at step N
- RunResult fields are populated correctly

## Out of v0.1 scope

Deferred to later versions:

**v0.2 (next):**
- **Store Protocol + backends**: relational metadata for runs and experiments
- **Hasher Protocol + DefaultHasher**: content-addressable input fingerprinting
- **Reuse-by-hash**: orchestrator checks Store before launching; skips if matching run is DONE
- **Multi-run sweep loop helper**: `orchestrator.run_sweep(configs, ...)`
- **Fire-and-forget background worker**: orchestrator detaches; CLI `runstate status` reads state from disk

**v0.3+:**
- Resume budgets (`max_steps_per_run` semantics)
- Smoke gate (abort sweep if first probe fails)
- `--status` CLI / Rich table renderer
- Parallel dispatch
- xxhash optional dep

**Separate packages:**
- `runstate-hydra` (Hydra config + sweep adapter)
- `runstate-postgres` (Postgres backend for Channel + Store)
- `runstate-submitit` (SubmititLauncher)
- `runstate-ray` (RayLauncher)
- `runstate-mlflow` (MLflow exporter for Store)

**Never in this library:**
- Web UI / visualization (users export to wandb/MLflow for this)

## Dependencies

**Runtime (v0.1):**
- Python 3.11+
- stdlib only

**Optional v0.1:**
- `portalocker` for cross-platform file locking IF we need cross-platform support. **Decision: ship Unix-only in v0.1 using stdlib `fcntl`.** Windows support is v0.2+ via portalocker.

**Dev:**
- `pytest`

## Decisions made (no longer open)

The original spec had three "open questions" — answering them now:

1. **`recv` blocking semantics in FileChannel**: poll with 50ms sleep between scans. No inotify in v0.1. Document the 50ms latency floor.
2. **`Run.config` serialization**: N/A in v0.1 (no Store). When Store returns in v0.2, use `json.dumps(sort_keys=True, separators=(",", ":"))` for canonical form.
3. **File locking portability**: stdlib `fcntl` (Unix-only). Windows support deferred.

## Success criteria

v0.1 is shippable when:

1. All three Protocol modules implemented with their default backends. Two backends for Channel (file, sqlite). One backend for Launcher (local).
2. Conformance test suite passes for all Channel backends.
3. Preempter + SafePoint helpers work end-to-end against both backends.
4. Toy worker script integration test passes: orchestrator dispatches the worker, observes phase/progress messages, sends a deferred preempt, worker stops gracefully at the requested step, RunResult reflects the actual outcome.
5. README documents:
   - The elevator pitch (cooperative-IPC + preempt discipline; complementary to wandb/Hydra)
   - The worker-side API (attach + SafePoint + Channel.send)
   - The orchestrator-side API (Orchestrator.run + Preempter)
   - A complete minimal example
6. The library installs cleanly via `pip install -e ~/src/runstate` and imports as `runstate`.

## Repository layout

```
~/src/runstate/
  pyproject.toml
  README.md
  LICENSE                          # MIT
  .gitignore
  runstate/
    __init__.py                    # re-exports (the public surface):
                                   #   Channel, open_channel, attach
                                   #   Preempter, SafePoint
                                   #   Launcher, ProcessHandle, LocalLauncher
                                   #   Orchestrator, RunResult
                                   #   Phase, CompletionReason
                                   #   PhaseChange, Progress, Completed
                                   #   StopNow, StopAtStep, Command, Event
    types.py
    channel/
      __init__.py                  # Channel Protocol, open_channel factory
      file.py                      # FileChannel
      sqlite.py                    # SqliteChannel
    preempt.py                     # Preempter, SafePoint, Command types
    launcher.py                    # Launcher / ProcessHandle Protocols + LocalLauncher
    orchestrate.py                 # Orchestrator, RunResult
  tests/
    conftest.py
    fixtures/
      toy_worker.py                # used by orchestrator integration test
    test_channel.py                # parametrized over backends
    test_preempt.py
    test_launcher.py
    test_orchestrate.py
  docs/
    design-v0.1.md                 # this file
    design-v0.1-original.md        # pre-review-cut spec (for history)
  examples/
    minimal.py                     # documented walkthrough
```
