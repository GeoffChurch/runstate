# runstate v0.1 — Design

**Status:** approved scope, ready for implementation plan
**Date:** 2026-05-27
**Supersedes:** design-v0.1-original.md (pre-review cut) and design-v0.1-rev2.md (intermediate with Preempter/Phase still in scope)

## Goal

`runstate` is **the substrate for cooperative orchestration of long-running scientific workers**. It provides:

- **Durable bidirectional IPC** between an orchestrator and a worker, scoped to per-run identity, surviving crashes on either side.
- **Process spawning and observation** as a swappable Protocol.
- **An orchestrator that composes the two** without imposing a message vocabulary or coordination pattern.

The library makes zero commitments about *what* the orchestrator and worker say to each other. It transports JSON-serializable dicts and observes process lifecycle. The dialogue protocol — preempt semantics, phase reporting, progress events, anything else — is **entirely user-defined** in v0.1.

## What v0.1 is not

The library does **not** ship in v0.1:

- Any standard message types (no `StopNow`, no `StopAtStep`, no `Phase`, no `Progress`, no `Completed`)
- Any concept of "step" or other workload-specific progress counters
- Any cooperative-preempt helper classes (no `Preempter`, no `SafePoint`)
- Any storage of run metadata beyond what's needed for the active run
- Any content-addressable identity or reuse logic

These are documented as **recipes** in the README — example code users copy and adapt — not as library API. The architectural discipline is that **the substrate has no taxonomic commitments**; anything that would require a vocabulary lives in user code or in opt-in modules added in later releases.

## Positioning

```
   sweep generator (Hydra / Optuna / custom)        ← config sweeps
            │
            ▼
      runstate orchestrator                          ← runstate v0.1
   ┌────────┴────────┐
   │                 │
 Channel          Launcher
 (IPC)            (process spawn)                    ← runstate v0.1
   │                 │
   ▼                 ▼
 worker script ──→ tracker (wandb / mlflow)          ← existing tools
   │                 │
   │                 ▼
   │            web UI, plots
   ▼
 your training loop
```

Other tools at the orchestration layer (Hydra+submitit, sacred, dvc, MLflow Projects) launch processes and forget. None offer durable bidirectional control during a run. runstate fills that gap and remains agnostic about what users put through the channel.

## Architecture

Four modules. Three Protocols. One concrete orchestrator.

```
runstate/
  __init__.py
  channel/
    __init__.py         # Channel Protocol + open_channel factory
    file.py             # FileChannel
    sqlite.py           # SqliteChannel
  launcher.py           # Launcher + ProcessHandle Protocols + LocalLauncher
  orchestrate.py        # Orchestrator + RunResult + CompletionReason
tests/
  conftest.py
  fixtures/
    toy_worker.py       # used by integration test
  test_channel.py       # parametrized over backends
  test_launcher.py
  test_orchestrate.py
```

Modules don't import each other except: `orchestrate.py` composes Channel and Launcher.

## Channel (`channel/__init__.py`)

Per-run, bidirectional, durable message transport.

### Protocol

```python
from typing import Protocol, Literal

class Channel(Protocol):
    """A directional view of a per-run message channel.

    A Channel instance has a fixed direction determined by its role.
    Two Channel instances pointed at the same run_id with opposite roles
    form a bidirectional transport.

    Messages are JSON-serializable dicts. The Channel imposes no schema
    beyond serializability.
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
- Order: messages are delivered in send order (per direction).
- No history: a Channel does not promise to return already-consumed messages. v0.1 implementations MAY discard consumed messages or keep them for backend reasons, but neither behavior is part of the contract.

### Backends

**`FileChannel`** (`channel/file.py`) — zero deps.

- Layout: `<root>/<run_id>/{to_worker, to_orchestrator}/<seq>.json`
- Send: write atomic via tempfile + rename within the same directory; sequence number assigned monotonically
- Recv: scan receive directory for unconsumed sequences; read smallest; delete after successful read
- Polling: sleep 50ms between scans when timeout > 0
- File locking: `fcntl.flock` on the per-direction directory during send (sequence assignment) and recv (claim before delete). Unix only; Windows support deferred.

**`SqliteChannel`** (`channel/sqlite.py`) — stdlib `sqlite3`.

- One DB per run: `<root>/<run_id>/channel.db`
- Schema: `messages(id INTEGER PRIMARY KEY, direction TEXT, payload TEXT, created_at REAL, consumed_at REAL)` with index on `(direction, consumed_at)`
- WAL mode for concurrent reads alongside writes
- Send: INSERT + COMMIT
- Recv: SELECT WHERE direction = ? AND consumed_at IS NULL ORDER BY id LIMIT 1; UPDATE on success; poll on empty result

Both back the same Channel Protocol. Conformance tests run against both.

## Launcher (`launcher.py`)

Process spawning + observation. Pluggable backends.

### Protocols

```python
class ProcessHandle(Protocol):
    """Observable handle to a launched process. Backend-defined identity."""

    identity: object                    # backend-defined; type-narrowed in concrete impls

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

### Default implementation

**`LocalLauncher`** — wraps `subprocess.Popen`. Returns `LocalProcessHandle` with `identity: int` (OS PID). ~30 LOC including the handle wrapper.

Other launchers (`SubmititLauncher`, `RayLauncher`, etc.) live in optional packages, not v0.1.

## Orchestrate (`orchestrate.py`)

Concrete orchestrator that composes Channel + Launcher for a single run.

### Types

```python
from enum import StrEnum
from dataclasses import dataclass

class CompletionReason(StrEnum):
    """Why a run stopped. Inferred from process exit + orchestrator command history."""
    NATURAL = "natural"       # exit code 0, no termination requested
    FAILED = "failed"         # exit code != 0, no termination requested
    PREEMPTED = "preempted"   # orchestrator called terminate() during the run
    KILLED = "killed"         # orchestrator called kill() (or hit timeout)


@dataclass
class RunResult:
    run_id: str
    exit_code: int
    completion_reason: CompletionReason
    duration_seconds: float
    messages_received: int     # count of messages received from worker
    messages_sent: int          # count of messages sent to worker
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
        run_id: str | None = None,                # generate UUID if None
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        on_message: Callable[[dict, Channel], None] | None = None,
        timeout: float | None = None,
        timeout_grace_seconds: float = 30.0,
    ) -> RunResult:
        """Launch the worker, observe its Channel, block until exit.

        - Generates run_id (UUID) if not provided.
        - Creates <channel_root>/<run_id>/ as the run directory.
        - Opens an orchestrator-role Channel for the run.
        - Augments env with RUNSTATE_RUN_ID, RUNSTATE_CHANNEL_ROOT,
          RUNSTATE_CHANNEL_BACKEND so the worker can attach().
        - Launches the worker via the configured Launcher.
        - In a loop: receives messages from the worker, invokes on_message
          (passing the message and the orchestrator-role Channel so the
          callback can send commands back); polls the process for exit.
        - On process exit: drains remaining messages; builds RunResult.

        on_message is the single user hook. The callback receives:
          - msg: the dict received from the worker
          - channel: the orchestrator-role Channel, so the callback can
            call channel.send(reply_dict) to send commands back

        timeout: if set, the orchestrator calls handle.terminate() when
        the deadline expires, then waits timeout_grace_seconds for the
        worker to exit cooperatively, then calls handle.kill() if it
        hasn't exited. This preserves the cooperative discipline: the
        worker has a chance to checkpoint before being force-killed.

        Returns RunResult with completion_reason inferred from
        (exit_code, whether the orchestrator called terminate/kill,
        whether the timeout fired).
        """
```

### Worker-side API

The library exposes `runstate.attach()` for workers:

```python
def attach(
    run_id: str | None = None,
    *,
    root: str | None = None,
    backend: Literal["file", "sqlite"] | None = None,
) -> Channel:
    """Attach to the worker-role Channel for this run.

    Reads RUNSTATE_RUN_ID, RUNSTATE_CHANNEL_ROOT, RUNSTATE_CHANNEL_BACKEND
    from env if any argument is None. Raises RuntimeError if the env vars
    are missing and no explicit args were given.

    Pass explicit arguments to attach standalone (e.g., for debugging
    outside an orchestrator).
    """
```

### A minimal usage example

**Worker** (`train.py`):
```python
import runstate

ch = runstate.attach()
ch.send({"event": "started"})

for step in range(max_steps):
    state = train_one_step(state)
    ch.send({"event": "progress", "step": step, "loss": state.loss})

    # Cooperative safe point: drain incoming messages
    while True:
        msg = ch.recv(timeout=0)
        if msg is None:
            break
        if msg.get("command") == "stop":
            ch.send({"event": "stopping", "step": step})
            save_checkpoint(state)
            return

ch.send({"event": "done"})
```

**Orchestrator** (`run.py`):
```python
import runstate

def watch(msg, channel):
    if msg.get("event") == "progress":
        print(f"step {msg['step']} loss {msg['loss']:.4f}")
        if msg.get("loss", 0) > 1000:
            channel.send({"command": "stop"})

orch = runstate.Orchestrator(
    launcher=runstate.LocalLauncher(),
    channel_root="/tmp/runstate",
    backend="file",
)
result = orch.run(cmd=["python", "train.py"], on_message=watch)
print(f"finished: {result.completion_reason}, exit_code={result.exit_code}")
```

The user defines the entire message vocabulary (`event`, `command`, etc.). The library transports the dicts and observes the process.

## Testing

### Conformance tests for Channel

Parametrized over backends; every Channel Protocol method has at least one test:

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
    assert ch_w.recv(timeout=0) is None
```

Additional behaviors covered:
- Direction safety (worker can't read its own sent messages)
- Crash recovery (close + reopen, messages still readable)
- Atomicity (partial write doesn't surface as a partial read)
- Ordering (messages delivered in send order per direction)
- Timeout semantics (`timeout=0` returns immediately; `timeout=N>0` blocks ≤N seconds; `timeout=None` blocks until message)

### Launcher tests

- `LocalLauncher.launch(["python", "-c", "exit(0)"])` → `handle.wait()` returns 0
- `LocalLauncher.launch(["python", "-c", "import time; time.sleep(10)"])` → `handle.terminate()` ends it within ~1s
- `handle.poll()` returns None for running process, integer after exit
- `handle.kill()` after terminate hasn't worked

### Orchestrator integration tests

A toy worker script (`tests/fixtures/toy_worker.py`):
1. `runstate.attach()`
2. Sends progress messages for N steps
3. Drains messages each step; exits if it receives `{"command": "stop"}`

Integration tests dispatch the toy worker and verify:
- `on_message` callback receives every message the worker sends
- Sending `{"command": "stop"}` from the callback causes the worker to exit
- `RunResult.completion_reason` is `NATURAL` when the worker exits 0 without orchestrator intervention
- `RunResult.completion_reason` is `PREEMPTED` when the orchestrator calls `terminate()` mid-run
- `RunResult.completion_reason` is `FAILED` when the worker exits non-zero
- `RunResult.completion_reason` is `KILLED` after timeout-based kill
- `messages_sent` and `messages_received` counts are accurate

## Decisions made (closed)

- **`recv` blocking semantics** in `FileChannel`: poll with 50ms sleep. No `inotify`. Latency floor documented.
- **JSON serialization**: messages serialized with `json.dumps(sort_keys=True, separators=(",", ":"))` for byte-stable on-disk representation. Aids debugging.
- **File locking**: stdlib `fcntl.flock` (Unix only). Windows support deferred to v0.2+ via `portalocker` (optional dep then).
- **Run identity**: plain `str` in v0.1. No `RunId` NewType. v0.2 may revisit if content-addressable identity returns.
- **Message vocabulary**: zero standard message types in v0.1 core. README documents recipes for cooperative-preempt patterns (both async-polling and synchronous-yield) as code-to-copy.

## Out of v0.1 scope

Deferred to later versions:

**v0.2 (next):**
- `runstate.cooperative` opt-in module: `Preempter`, `AsyncSafePoint`, `SyncSafePoint` helpers + standard `StopNow`/`StopAtStep` dict-shape conventions
- `Store` Protocol + backends (file + sqlite): relational metadata for runs and experiments
- `Hasher` Protocol + `DefaultHasher`: content-addressable input fingerprinting
- Reuse-by-hash in the orchestrator
- Multi-run sweep loop helper
- Fire-and-forget background worker + CLI status display

**v0.3+:**
- Resume budgets, smoke gate, parallel dispatch, Postgres backend
- Optional packages: `runstate-hydra`, `runstate-submitit`, `runstate-mlflow`, etc.

**Never in this library:**
- Web UI / visualization (users use wandb/MLflow for this)

## Dependencies

**Runtime (v0.1):**
- Python 3.11+
- stdlib only

**Dev:**
- `pytest`

## Success criteria

v0.1 is shippable when:

1. Channel Protocol implemented with both backends (file, sqlite); conformance tests pass for both.
2. Launcher Protocol implemented with `LocalLauncher`; basic process-lifecycle tests pass.
3. Orchestrator integration test passes: orchestrator dispatches the toy worker, callbacks receive every message, sending a command via the callback causes the worker to exit cooperatively, RunResult reflects the actual outcome including correct CompletionReason.
4. README documents:
   - The substrate (Channel + Launcher + Orchestrator) — what runstate IS
   - At least two recipes (async-polling cooperative-preempt; synchronous-yield RPC) — what runstate ENABLES
   - The complementary-to-wandb/Hydra positioning
   - A complete minimal example from worker through orchestrator
5. The library installs cleanly via `pip install -e ~/src/runstate` and imports as `runstate`.

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
                                   #   Launcher, ProcessHandle, LocalLauncher
                                   #   Orchestrator, RunResult, CompletionReason
    channel/
      __init__.py                  # Channel Protocol, open_channel factory
      file.py                      # FileChannel
      sqlite.py                    # SqliteChannel
    launcher.py                    # Launcher + ProcessHandle Protocols + LocalLauncher
    orchestrate.py                 # Orchestrator + RunResult + CompletionReason
  tests/
    conftest.py
    fixtures/
      toy_worker.py
    test_channel.py
    test_launcher.py
    test_orchestrate.py
  docs/
    design-v0.1.md                 # this file
    design-v0.1-original.md        # pre-review-cut spec (history)
    design-v0.1-rev2.md            # intermediate cut (history)
  examples/
    async_preempt/                 # recipe: async-polling cooperative-preempt
      worker.py
      orchestrator.py
    sync_yield/                    # recipe: synchronous-yield RPC
      worker.py
      orchestrator.py
    minimal/                       # recipe: minimal "hello" example
      worker.py
      orchestrator.py
```

## What changed from previous revisions

The original spec had: 5 Protocols, content-addressable reuse via Hasher, Store with experiment manifests, Preempter with deferred preempt semantics, Phase enum, typed Progress/PhaseChange/Completed messages.

**Rev 2** dropped Store, Hasher, reuse (deferred to v0.2). Kept Preempter, SafePoint, Phase, the typed message vocabulary.

**This rev (final)** drops the typed message vocabulary entirely. The library transports dicts; users define the semantics. Preempter, SafePoint, and standard messages become recipes in `examples/` and helpers in v0.2's `runstate.cooperative` module.

The result is a v0.1 that's minimal, opinion-free, and validates the core abstractions (Channel + Launcher + Orchestrator) before committing to any particular dialogue pattern. ~600-800 LOC of core library plus tests.
