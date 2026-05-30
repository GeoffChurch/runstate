# runstate v0.1 — Design

**Status:** approved scope, ready for implementation plan
**Date:** 2026-05-27
**Supersedes:** see "Revision history" at the end of this file.

## Goal

`runstate` provides **two-way cooperative control** between an orchestrator and a long-running scientific worker, plus the small vocabulary of typed messages needed to make that control useful in practice.

Two architectural commitments:

1. **A pattern-neutral substrate** — durable bidirectional IPC scoped to per-run identity, plus process spawn/observe. The substrate transports JSON-serializable dicts; it has no opinion about what they mean.

2. **An opinionated minimum vocabulary** — typed commands (orchestrator → worker) for cooperative preempt; typed events (worker → orchestrator) for progress reporting, self-described exits, and command acknowledgment. Ships as opt-in `runstate.control` and `runstate.events` modules.

The substrate is the load-bearing abstraction; the vocabulary is the load-bearing opinion. Together they distinguish runstate from generic file-based IPC.

## Positioning

Tracking tools (TensorBoard, wandb, MLflow, neptune) are **one-way data-plane file sinks**: the training script writes events; a frontend reads them; the channel is asynchronous and unidirectional.

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

Workers report progress in two directions independently: to the tracker for visualization (one-way data plane), and to the runstate Channel for cooperative control (two-way control plane). Complementary, not competitive.

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
  control.py            # opt-in: typed orchestrator → worker commands (with Ack tracking)
  events.py             # opt-in: typed worker → orchestrator events (incl. Ack)
  orchestrate.py        # Orchestrator + RunResult
tests/
  conftest.py
  fixtures/
    toy_worker.py
  test_channel.py       # parametrized over backends
  test_launcher.py      # parametrized over backends
  test_control.py
  test_events.py
  test_orchestrate.py   # integration
```

Modules depend only on direct neighbors:
- `control` and `events` import the `Channel` Protocol from `channel`
- `orchestrate` composes Channel + Launcher and **explicitly imports `events.Stopped` and `events.Ack`** for inference and protocol tracking
- Workers import `runstate.attach()` plus optionally `control` and `events`

The orchestrate → events dependency is documented as intentional: the orchestrator's job includes tracking what the worker reported about its own lifecycle and acknowledging commands. This is one specific, narrow coupling; the events module does NOT depend on orchestrate.

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
    beyond serializability — the typed vocabulary in runstate.control /
    runstate.events is OPTIONAL; users may send arbitrary dicts.
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
- No history: a Channel does not promise to return already-consumed messages. v0.1 implementations MAY discard consumed messages or keep them. Users who want message history should use a tracker (wandb/MLflow) for that data, or read `RunResult.messages` after the run completes.

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
    """Observable handle to a launched process."""

    identity: int | str                    # backend-defined; typed concretely in impls

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

**`LocalLauncher`** — wraps `subprocess.Popen`. Returns `LocalProcessHandle` with `identity: int` (OS PID). ~30 LOC.

**`ThreadLauncher`** — for testing. Runs the "worker" in-process in a thread; useful for fast Protocol conformance tests without subprocess overhead. `identity: int` is a thread ID. Doesn't support real cancellation (Python threads can't be killed cooperatively), so `terminate()` raises an internal flag the worker thread is expected to poll; `kill()` is a hard `TimeoutError`. Documented as test-only.

Other launchers (`SubmititLauncher`, `RayLauncher`, etc.) live in optional packages, not v0.1.

## Control module (`control.py`) — opt-in

Typed orchestrator → worker commands. The library's load-bearing opinion: these are the cooperative-control primitives that distinguish runstate from generic IPC.

### Types

```python
import uuid
from dataclasses import dataclass, field, asdict
from typing import Literal

def _gen_id() -> str:
    """Generate a short unique command_id."""
    return uuid.uuid4().hex[:12]

@dataclass(frozen=True)
class StopNow:
    """Ask the worker to stop ASAP at its next safe point."""
    command_id: str = field(default_factory=_gen_id)
    type: Literal["StopNow"] = "StopNow"

@dataclass(frozen=True)
class StopAtStep:
    """Ask the worker to stop when its current step reaches `at`.

    The worker's check() polls drained messages and decides; deferred
    preempt is a worker-side decision based on current state.
    """
    at: int
    command_id: str = field(default_factory=_gen_id)
    type: Literal["StopAtStep"] = "StopAtStep"

Command = StopNow | StopAtStep
```

### Helpers

```python
def send(channel: Channel, command: Command) -> str:
    """Serialize a typed Command and send it. Returns the command_id."""
    channel.send(asdict(command))
    return command.command_id

def send_stop(channel: Channel) -> str:
    """Convenience: send StopNow. Returns the command_id."""
    return send(channel, StopNow())

def send_stop_at_step(channel: Channel, step: int) -> str:
    """Convenience: send StopAtStep(at=step). Returns the command_id."""
    return send(channel, StopAtStep(at=step))


def parse(msg: dict) -> Command | None:
    """Parse a raw dict into a typed Command, or None if not recognized."""
    match msg.get("type"):
        case "StopNow":
            return StopNow(command_id=msg.get("command_id", _gen_id()))
        case "StopAtStep":
            return StopAtStep(at=msg["at"], command_id=msg.get("command_id", _gen_id()))
        case _:
            return None


class Checker:
    """Worker-side helper that tracks deferred-preempt state for one Channel.

    Holds pending StopAtStep state so that calls to check() across
    multiple steps correctly evaluate the deferred condition.

    State lives on the Checker instance — NOT monkey-patched onto the
    Channel. This means two Checkers wrapping the same Channel have
    independent pending state; users who want shared state should share
    the Checker instance.
    """

    def __init__(self, channel: Channel): ...

    def check(self, *, current_step: int | None = None) -> Command | None:
        """Drain pending messages, return active Command if any.

        StopNow → returned immediately; any held StopAtStep is dropped
                  silently (subsumed; no Ack for the dropped one).
        StopAtStep(at=N) → returned only if current_step is not None and
                            >= N. Otherwise held internally and returned
                            on a future call when current_step crosses N.
                            If a NEW StopAtStep arrives while one is held,
                            the new one supersedes — the old one is
                            dropped silently (no Ack).
        Non-Command dicts → ignored by this helper; read via channel.recv()
                            directly if you want them.

        Ack semantics (the single rule): only the command whose effect
        the worker ACTS ON gets ack'd. Superseded or subsumed pending
        commands are dropped without Ack. This gives the orchestrator's
        unacknowledged_commands a clean semantics: "commands the worker
        never acted on."

        The Ack carries the original command's command_id (not a new one)
        so the orchestrator can match acks to its sent commands.
        """

def check(channel: Channel, *, current_step: int | None = None) -> Command | None:
    """Functional convenience: equivalent to Checker(channel).check(current_step=...)
    with the Checker cached per-Channel in a module-level WeakKeyDictionary.

    Two users calling check(ch, ...) at different points in the worker
    will share the same Checker instance for that channel (and thus share
    deferred state). This is the desired behavior 99% of the time. If you
    need isolation, construct your own Checker explicitly."""
```

`command_id`s are 48-bit random hex (`uuid.uuid4().hex[:12]`). Birthday collision probability is negligible below ~1M commands per run; documented as a known limit.

### Usage example

Orchestrator side:
```python
from runstate import control

def watcher(msg, channel):
    if msg.get("type") == "Progress" and msg["metrics"].get("loss", 0) > 1000:
        cmd_id = control.send_stop(channel)
        # cmd_id can be used to track ack
```

Worker side:
```python
from runstate import control

ch = runstate.attach()
for step in range(max_steps):
    state = train_step(state)

    cmd = control.check(ch, current_step=step)
    match cmd:
        case control.StopNow():
            return  # ack already sent by check()
        case control.StopAtStep(at=at):
            return  # ack already sent by check()
        case None:
            pass
```

## Events module (`events.py`) — opt-in

Typed worker → orchestrator events. Parallel structure to `control.py`.

### Types

```python
@dataclass(frozen=True)
class Progress:
    """Periodic report of training state.

    Send as often as you want orchestrator visibility — every step,
    every N steps, every wall-clock interval. step is optional for
    workers without a step concept (e.g., trial-based).
    """
    metrics: dict[str, float]
    step: int | None = None
    type: Literal["Progress"] = "Progress"

@dataclass(frozen=True)
class Stopped:
    """Worker's self-described exit notification.

    Sent immediately before the worker exits (natural completion,
    self-detected divergence, preempt acknowledgment, etc.). reason is
    a free-form string; common values are "natural", "preempted",
    "diverged", "nan_detected", "patience_triggered", "oom".
    """
    reason: str
    metadata: dict | None = None
    type: Literal["Stopped"] = "Stopped"

@dataclass(frozen=True)
class Ack:
    """Worker acknowledgment that a command was received and processed.

    Sent automatically by control.check() when it returns a command.
    The orchestrator tracks acks via command_id to know which commands
    actually reached the worker.
    """
    of: str            # the type field of the acknowledged command (e.g., "StopNow")
    command_id: str    # the command_id of the acknowledged command
    type: Literal["Ack"] = "Ack"

Event = Progress | Stopped | Ack
```

### Helpers

```python
def send(channel: Channel, event: Event) -> None:
    """Serialize a typed Event and send it."""
    channel.send(asdict(event))

def progress(channel: Channel, *, metrics: dict[str, float], step: int | None = None) -> None:
    """Convenience: send a Progress event."""
    send(channel, Progress(metrics=metrics, step=step))

def stopped(channel: Channel, *, reason: str, metadata: dict | None = None) -> None:
    """Convenience: send a Stopped event. Call immediately before exiting."""
    send(channel, Stopped(reason=reason, metadata=metadata))

def parse(msg: dict) -> Event | None:
    """Parse a raw dict into a typed Event, or None if not recognized."""
    match msg.get("type"):
        case "Progress":
            return Progress(
                metrics=msg["metrics"],
                step=msg.get("step"),
            )
        case "Stopped":
            return Stopped(
                reason=msg["reason"],
                metadata=msg.get("metadata"),
            )
        case "Ack":
            return Ack(of=msg["of"], command_id=msg["command_id"])
        case _:
            return None
```

### Usage example

```python
from runstate import events, control

ch = runstate.attach()
state = init_state()

for step in range(max_steps):
    state = train_step(state)
    events.progress(ch, step=step, metrics={"loss": state.loss, "lr": state.lr})

    if math.isnan(state.loss):
        events.stopped(ch, reason="nan_detected", metadata={"step": step})
        return

    cmd = control.check(ch, current_step=step)
    if cmd is not None:
        events.stopped(ch, reason="preempted", metadata={"step": step})
        return

events.stopped(ch, reason="natural", metadata={"final_step": step})
```

## Orchestrate (`orchestrate.py`)

Concrete orchestrator that composes Channel + Launcher for a single run. Tracks the worker's Stopped event and command acknowledgments.

### Types

```python
from dataclasses import dataclass, field
from runstate.events import Stopped   # documented intentional coupling

@dataclass
class RunResult:
    """Outcome of a worker run. Primitive signals; users compose what they need."""
    run_id: str
    exit_code: int
    duration_seconds: float
    preempted: bool                            # did orchestrator send any Stop command?
    killed: bool                               # did orchestrator call kill() (timeout)?
    stopped_event: Stopped | None              # worker's self-report, if it sent one
    commands_sent: list[Command]               # typed commands the orchestrator sent
    commands_acknowledged: list[str]           # command_ids that arrived as Ack events

    # Channel handle retained so users can replay messages on demand. The
    # orchestrator closes its own role on the channel after run() returns,
    # but the durable storage persists until the user (or a cleanup pass)
    # removes the run directory.
    _channel: Channel = field(repr=False, compare=False)

    def replay_messages(self) -> Iterator[dict]:
        """Iterate all worker-sent messages received during the run.

        Reads from the Channel backend's durable storage. Backends that
        don't preserve consumed messages (i.e., FileChannel as currently
        specced — deletes after read) will raise NotImplementedError
        unless they're configured to retain. SqliteChannel preserves the
        full history via the messages table.

        Users who want full message history should either: (a) use
        SqliteChannel backend, or (b) collect messages in their
        on_message callback themselves.
        """

    @property
    def unacknowledged_commands(self) -> list[Command]:
        """Commands the orchestrator sent but never saw acknowledged.
        Empty list means full delivery confirmation."""
        return [c for c in self.commands_sent if c.command_id not in self.commands_acknowledged]
```

No `CompletionReason` enum. No `success` property. Users compose what they need from primitive signals. Common derivations:

```python
# "successful natural completion"
ok = (result.exit_code == 0
      and not result.killed
      and (result.stopped_event is None
           or result.stopped_event.reason == "natural"))

# "user-requested stop"
stopped_by_orchestrator = result.preempted or (
    result.stopped_event and result.stopped_event.reason == "preempted")

# "self-aborted with custom reason"
self_aborted = (result.stopped_event
                and result.stopped_event.reason not in ("natural", "preempted"))

# "crashed without explanation"
crashed = (result.exit_code != 0
           and not result.killed
           and result.stopped_event is None)

# "force-killed by timeout"
killed = result.killed
```

Test fixtures demonstrate each derivation.

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
        on_message: Callable[[dict, Channel], None] | None = None,
        timeout: float | None = None,
        timeout_grace_seconds: float = 30.0,
    ) -> RunResult:
        """Launch the worker, observe its Channel, block until exit.

        on_message: invoked for each message received from the worker.
        Signature: on_message(msg: dict, channel: Channel) -> None.
        The channel is the orchestrator-role Channel; the callback can
        channel.send() commands or channel.recv() additional messages
        synchronously. Wrap with runstate.events.parse() and
        runstate.control.send() for typed handling:

            def watcher(msg, channel):
                event = events.parse(msg)
                match event:
                    case events.Progress(metrics=m, step=s):
                        if m.get("loss", 0) > 1000:
                            control.send_stop(channel)

        timeout: if set, the orchestrator sends StopNow when the deadline
        expires, waits timeout_grace_seconds for cooperative exit, then
        calls handle.kill() if still running. Preserves cooperative
        discipline even on hard timeouts.

        Lifecycle observation (the orchestrator's per-message bookkeeping):
        - Stopped events: extracted into RunResult.stopped_event (last one
          wins if multiple are sent)
        - Ack events: command_ids extracted into RunResult.commands_acknowledged
        - Commands sent via channel.send() inside on_message (or by the
          orchestrator's timeout handler) are tracked in RunResult.commands_sent
          as typed Command objects (parsed from outbound dicts)

        Message history is NOT retained on RunResult (would be unbounded).
        Users who want full history call result.replay_messages(), which
        reads from the Channel backend's durable storage (SqliteChannel
        preserves full history; FileChannel raises unless configured to
        retain). Users who want a bounded subset should collect from
        on_message themselves.

        Recognition of Stopped and Ack is the one intentional coupling
        between orchestrate and events; documented at the import site.
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
    """
```

### A complete minimal example

**Worker** (`train.py`):
```python
import runstate
from runstate import control, events

ch = runstate.attach()
state = init_state()

for step in range(max_steps):
    state = train_one_step(state)
    events.progress(ch, step=step, metrics={"loss": state.loss})

    cmd = control.check(ch, current_step=step)
    match cmd:
        case control.StopNow() | control.StopAtStep():
            save_checkpoint(state)
            events.stopped(ch, reason="preempted", metadata={"step": step})
            return
        case None:
            pass

events.stopped(ch, reason="natural", metadata={"final_step": step})
```

**Orchestrator** (`run.py`):
```python
import runstate
from runstate import control, events

def watcher(msg, channel):
    event = events.parse(msg)
    match event:
        case events.Progress(metrics=m, step=s):
            print(f"step {s} loss {m.get('loss'):.4f}")
            if m.get("loss", 0) > 1000:
                control.send_stop(channel)
        case events.Stopped(reason=r):
            print(f"worker stopped: {r}")
        case events.Ack(of=cmd_type, command_id=cid):
            print(f"ack of {cmd_type} ({cid})")
        case None:
            pass

orch = runstate.Orchestrator(
    launcher=runstate.LocalLauncher(),
    channel_root="/tmp/runstate",
    backend="file",
)
result = orch.run(cmd=["python", "train.py"], on_message=watcher)

# Compose the classification from primitive signals
ok = (result.exit_code == 0 and not result.killed
      and (result.stopped_event is None
           or result.stopped_event.reason == "natural"))
if ok:
    print("OK")
elif result.preempted:
    print(f"preempted; {len(result.unacknowledged_commands)} commands not ack'd")
elif result.killed:
    print("force-killed by timeout")
elif result.stopped_event:
    print(f"self-aborted: {result.stopped_event.reason}")
else:
    print(f"crashed: exit_code={result.exit_code}")
```

## Watchdog pattern (README recipe)

The Ack mechanism enables users to distinguish "worker didn't receive command" from "worker is still working on it." Suggested watchdog:

```python
def watcher_with_watchdog(msg, channel, *, state):
    state["last_msg_at"] = time.time()
    event = events.parse(msg)
    if isinstance(event, events.Ack):
        state["acked"].add(event.command_id)

# In a separate thread, watching the ProcessHandle the user retains:
def watchdog(handle, last_msg_state, command_ids_sent, acked):
    while handle.poll() is None:
        time.sleep(5)
        if time.time() - last_msg_state["last_msg_at"] > 60:
            # No messages for 60s — worker might be wedged
            unacked = [c for c in command_ids_sent if c not in acked]
            if unacked:
                # Sent commands but no acks — worker is unresponsive
                handle.kill()
```

v0.1 doesn't ship this as library code; it's a documented pattern users can adopt.

## Testing

### Channel conformance tests (parametrized over file + sqlite)

Every Channel Protocol method covered:
- Round-trip send/recv
- Direction safety (worker can't read its own sent messages)
- Crash recovery (close + reopen, messages still readable)
- Atomicity (partial write doesn't surface as partial read)
- Ordering (per-direction send order preserved)
- Timeout semantics (`timeout=0` returns immediately; `timeout=N>0` blocks ≤N seconds; `timeout=None` blocks until message)

### Launcher conformance tests (parametrized over local + thread)

- `launch(["python", "-c", "exit(0)"])` → `handle.wait()` returns 0
- `launch(["python", "-c", "import time; time.sleep(10)"])` → `terminate()` ends it within ~1s
- `poll()` returns None for running process, integer after exit
- `kill()` force-terminates

### Control module tests

Basic semantics:
- `send_stop_at_step(channel, 200)` + worker's `Checker(ch).check(current_step=199)` → returns None; **no Ack sent** (command wasn't acted on)
- `send_stop_at_step(channel, 200)` + worker's `Checker(ch).check(current_step=200)` → returns `StopAtStep` AND an `Ack` with the **original** command_id appears in the orchestrator-direction
- `parse()` round-trip: `parse(asdict(StopNow()))` == `StopNow()` with the same command_id

Ack semantics for superseded/subsumed commands (the rule: only acted-on commands get ack'd):
- **Superseded**: send `StopAtStep(at=200, command_id=A)`, then send `StopAtStep(at=100, command_id=B)`. Worker `check(current_step=100)` returns `StopAtStep(at=100, command_id=B)` and emits Ack(command_id=B). **A is dropped without Ack** (it never fired). Orchestrator's `unacknowledged_commands` contains the StopAtStep(at=200).
- **Repeated check on held command**: send `StopAtStep(at=200, command_id=A)`. Worker `check(current_step=10)` returns None, no Ack. Worker `check(current_step=50)` returns None, no Ack. Worker `check(current_step=200)` returns the command, Ack(command_id=A) emitted exactly once.
- **Subsumed by StopNow**: send `StopAtStep(at=200, command_id=A)`. Before it fires, send `StopNow(command_id=B)`. Worker `check(current_step=10)` returns StopNow, emits Ack(command_id=B). **A is dropped without Ack**.

Checker isolation:
- Two `Checker(ch)` instances wrapping the same Channel have **independent** pending state. Sending a StopAtStep, one Checker drains it, the other Checker sees no commands.
- Functional `control.check(ch, ...)` uses the cached Checker — repeated calls from the same caller share state correctly.

### Events module tests

- `progress(channel, step=10, metrics={"loss": 1.5})` arrives as `{"type": "Progress", "step": 10, "metrics": {"loss": 1.5}}`
- `parse()` round-trip for each event type
- `Progress.step` optional: `progress(channel, metrics={"loss": 1.5})` parses back with step=None

### Orchestrator integration tests

A toy worker (`tests/fixtures/toy_worker.py`):
1. `runstate.attach()`
2. Sends progress messages each "step"
3. Polls commands; exits on StopNow/StopAtStep
4. Sends `Stopped` before exit

Integration tests verify:
- `on_message` callback receives every message
- Sending StopNow via the callback's channel.send causes cooperative exit
- `RunResult.preempted` is True when orchestrator sent any command
- `RunResult.stopped_event` captures the worker's last self-report
- `RunResult.commands_sent` contains typed `Command` objects (not raw dicts) for everything the orchestrator sent
- `RunResult.commands_acknowledged` lists ack'd command_ids
- `RunResult.unacknowledged_commands` is empty in the happy cooperative case; non-empty when commands didn't reach the worker (timeout-kill case)
- `RunResult.killed` is True after timeout-based kill
- The custom-derivation patterns in the spec produce the expected results across canonical scenarios:
  - Worker self-aborts: exit_code=0 + Stopped(reason="diverged") → composed `self_aborted` derivation is True; `ok` derivation is False
  - Worker crashes: exit_code != 0 + no Stopped → `crashed` derivation is True
  - Worker preempted: orchestrator sent StopNow + worker exits 0 + Stopped(reason="preempted") → `stopped_by_orchestrator` derivation is True
- `result.replay_messages()` returns all worker-sent messages when backend supports it (SqliteChannel); raises NotImplementedError on FileChannel

## Decisions made (closed)

- **`recv` blocking semantics in FileChannel**: poll with 50ms sleep. No `inotify`. Latency floor documented.
- **JSON serialization**: messages serialized with `json.dumps(sort_keys=True, separators=(",", ":"))` for byte-stable on-disk representation.
- **File locking**: stdlib `fcntl.flock` (Unix only). Windows support deferred.
- **Run identity**: plain `str` in v0.1. No `RunId` NewType.
- **Standard message vocabulary**: 2 commands (StopNow, StopAtStep), 3 events (Progress, Stopped, Ack). Ships in `runstate.control` and `runstate.events` as opt-in. Users CAN send raw dicts via Channel for non-standard messages.
- **No `CompletionReason` enum.** RunResult exposes primitive signals (`preempted`, `killed`, `stopped_event`, etc.); users compose classifications. Removes the substrate-vs-helpers boundary violation that would otherwise exist (orchestrate needing to enum-classify worker-supplied reason strings).
- **Ack is first-class.** Commands carry `command_id`; `control.check()` auto-sends Ack; orchestrator tracks acks in RunResult. Protocol hardening — not opinionated about workload.
- **Pause/Resume, Snapshot, Cleanup, Reconfigure**: out of v0.1. Pause/Resume can be modeled as Stop+restart externally. Snapshot is well-defined but workload-specific. Cleanup is too workload-specific to standardize. Reconfigure is speculative without concrete validated use cases; users can send raw `channel.send({"type": "Reconfigure", "params": ...})` if they want.
- **No OmegaConf/Hydra dep for typed serialization.** Hand-written `parse()` and `dataclasses.asdict()` handle dict↔dataclass conversion in stdlib. Users get typed pattern matching on both sides without seeing `"type"` strings in their code.

## Out of v0.1 scope

Deferred to later versions:

**v0.2 (next):**
- `Store` Protocol + backends: relational metadata for runs and experiments (many-to-many membership)
- `Hasher` Protocol + `DefaultHasher`: content-addressable input fingerprinting
- Reuse-by-hash in the orchestrator
- Multi-run sweep loop helper (`orchestrator.run_sweep`)
- Fire-and-forget background worker + CLI status display
- Pause/Resume + Snapshot + Reconfigure as opt-in additions to `runstate.control`
- Smarter timeout logic using Acks (wait for Ack before counting grace seconds)

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
3. **Control** module: `StopNow`, `StopAtStep` (with deferred semantics), `parse()`, `send()`, `check()` (with auto-ack) work end-to-end; tests cover multi-message drain scenarios.
4. **Events** module: `Progress`, `Stopped`, `Ack`, `parse()`, `send()` round-trip cleanly.
5. **Orchestrator** integration test passes: the toy worker dispatched, orchestrator observes every message, sends StopNow via the callback path, worker exits cooperatively with `Stopped(reason="preempted")`, RunResult precisely reflects the outcome including Acks.
6. **Ack tracking**: tests verify `commands_sent` / `commands_acknowledged` / `unacknowledged_commands` correctness, including the timeout-kill case where commands may be unacknowledged.
7. **README** documents:
   - The substrate (Channel + Launcher + Orchestrator)
   - The opinionated minimum vocabulary (`runstate.control`, `runstate.events`)
   - The complementary-to-wandb/Hydra positioning (control plane vs data plane)
   - A complete minimal example
   - Two recipe sections: async-polling pattern (the example) and synchronous-yield pattern (worker calls `recv(timeout=None)`)
   - The watchdog pattern using Acks
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
                                   #   Orchestrator, RunResult
    channel/
      __init__.py                  # Channel Protocol, open_channel factory
      file.py                      # FileChannel
      sqlite.py                    # SqliteChannel
    launcher.py                    # Launcher + ProcessHandle Protocols + 2 backends
    control.py                     # StopNow, StopAtStep, parse, send, send_stop, send_stop_at_step, check
    events.py                      # Progress, Stopped, Ack, parse, send, progress, stopped
    orchestrate.py                 # Orchestrator + RunResult
  tests/
    conftest.py
    fixtures/
      toy_worker.py
    test_channel.py
    test_launcher.py
    test_control.py
    test_events.py
    test_orchestrate.py
  docs/
    design-v0.1.md                 # this file
    design-v0.1-original.md        # history (initial spec)
    design-v0.1-rev2.md            # history (Store/Hasher cut)
    design-v0.1-rev3-overcut.md    # history (over-cut — no helpers)
    design-v0.1-rev4.md            # history (commands/report with Reconfigure + CompletionReason)
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

## Revision history

| Rev | Key shape | Reason for next cut |
|---|---|---|
| **Original** | Store + Hasher + reuse + Preempter + Phase + typed events | Speculative scope; reuse-by-hash deferable |
| **Rev 2** | Preempter + Phase + typed events (no Store/Hasher) | Phase + typed events over-opinionated |
| **Rev 3 (overcut)** | Pure substrate, no opinions | Subagent review: indistinguishable from subprocess + JSON queue |
| **Rev 4** | Commands (StopNow/StopAtStep/Reconfigure) + Report (Progress/Stopped) + CompletionReason | Subagent review on primitives: StopAtMark generalization, Reconfigure speculative, CompletionReason boundary violation |
| **Rev 5** | Control + Events + Ack + primitive signals (no CompletionReason, no Reconfigure) | Reviewer: `_control_pending` monkey-patch smell, Ack semantics under-specified, messages unbounded, commands_sent untyped, success hides self-aborts |
| **This rev (final)** | Same as Rev 5 + Checker class (no monkey-patching) + explicit Ack semantics + replay_messages() + typed commands_sent + dropped success property | — |

The arc converged on: minimal-opinionated cooperative-preempt vocabulary, primitive signals (no derived enums or properties), first-class Ack with precisely-specified semantics, no CompletionReason (the boundary violation it required is now gone), Checker class for stateful deferred-preempt without monkey-patching, on-demand message replay (no unbounded retention). ~900-1100 LOC core.
