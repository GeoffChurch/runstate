# API reference — the public surface

Every name exported from the top-level `runstate` package (`runstate.__all__`),
grouped as [`runstate/__init__.py`](../runstate/__init__.py) groups them. Each
summary lifts the load-bearing phrases from the name's own docstring; the code
and the docstrings are authoritative where this page lags.

*Derived, not authoritative: on any disagreement the schemas +
`design-v0.2.md` win. A drift-guard test (`tests/test_public_api.py`) asserts
every name in `runstate.__all__` appears on this page.*

Reading order for newcomers is [`guide.md`](guide.md); the model is
[`overview.md`](overview.md).

---

## Substrate

The opinion-free topic-log transport.

### `open_channel`

```python
open_channel(run_id: str, *, root=None, backend="sqlite", json_default=None) -> Channel
```

Locate and open a run's channel. `root` is the directory (sqlite) or namespace
(memory) holding runs; `run_id` selects one. Repeated calls on the same
`(root, run_id)` share the run's log, so an orchestrator and a worker name the
run the same way. `json_default` is a sender-side `json.dumps` hook for coercing
exotic value payloads. Backends: `"memory"`, `"sqlite"` (default), `"postgres"`
(the `[postgres]` extra). Raises `ValueError` on a bad backend/root and
`ImportError` (with an install hint) if `backend="postgres"` without psycopg.

### `attach`

```python
attach(run_id=None, *, root=None, backend=None, json_default=None) -> Channel
```

Worker-side: open the channel for the run this process was launched into. A
launcher sets the environment; `attach()` reads it (explicit arguments
override). See [environment variables](#environment-variables) below. Raises
`KeyError` if `run_id` is omitted and `RUNSTATE_RUN_ID` is unset.

### `Channel`

```python
send(body, *, topic, name=None, request_id=None, expected_seq=None) -> int | None
read(after=0, *, topics=None, name=None, request_ids=None, limit=None) -> list[Envelope]
latest(topic, name=None) -> Envelope | None
last_seq() -> int
close() -> None
```

A handle on one run's append-only topic log (the ABC every backend implements).
`send` appends and returns the new `seq`; with `expected_seq` it is a
**compare-and-append** (append only if the log head is still `expected_seq`;
returns `None` if it lost — the primitive behind the single-spawn / episode
self-claim). `read` takes a caller-owned cursor (`after`); the substrate keeps no
per-reader state. `latest(topic, name)` reads the current value of a
register-like topic. `last_seq()` is the CAS's read half (the log's last `seq`,
`0` = empty), O(1) on every backend.

### `Body`

```python
Body = dict[str, Any]
```

The opaque envelope payload type — an arbitrary JSON-serializable dict the
substrate never parses.

### `Envelope`

```python
Envelope(seq: int, topic: str, name: str | None, request_id: str | None, body: Body)
```

One record in a channel's log. `topic` is the closed, protocol-owned routing
key; `name` is the open, application-owned identifier (e.g. a metric name);
`request_id` correlates a response to its request and scopes visibility; `body`
is opaque to the substrate.

## Worker

### `Worker`

```python
Worker(channel: Channel, *, now=time.time)
# drivers
steps(total=None, *, start=0) -> Iterator[int]
serve() -> Iterator[int]
# reporting
set(name, value) -> None
emit(name, value) -> None
# lifecycle
stopped(*, completed=False, error=None, final_step=None) -> None
retire() -> bool
tick(step: int | None) -> bool
# levels (properties)
claimed: bool          # won the episode claim (vs lost)
stop_pending: bool     # the tick() stop decision, as a side-effect-free poll
pinned: bool           # someone holds a live subscription on my output
```

The reference worker loop; a context manager (`with Worker(channel) as w:`) that
emits the dying breath on exit. **Two drivers** for the two continuation
policies: `steps(total)` runs to the launch contract's target (yields each step,
then `tick`s, stops on a commanded stop; `start=k` resumes run-absolute);
`serve()` runs while leased demand exists (ticks stepless, exits at zero demand
via `retire`). `set` updates the current-value register (observer-chosen
cadence); `emit` logs a point unconditionally (worker-chosen cadence — the
series `ensure`/`history` read) and **raises `ValueError` before the first tick
or on a stepless worker**. `stopped(completed=True)` is the opt-in completion
claim; the default projects to `preempted`. `retire()` is the careful death (the
dying breath CAS'd against the drained log).

## Launchers

Opt-in orchestration: spawn a worker and bracket it with
`launcher.launched` / `launcher.terminated`. `open_channel` is uniform;
`launch`'s target is launcher-specific (a callable vs a command).

### `Launcher`

```python
launch(run_id: str, target: object, **kwargs) -> LaunchHandle
open_channel(run_id: str) -> Channel
```

Protocol: spawn a worker into a run. `launch`'s target is launcher-specific by
nature — an in-process callable for `ThreadLauncher`, a subprocess command for
`LocalLauncher` — since how the worker receives its channel (passed directly vs
re-derived via `attach`) differs in kind.

### `LaunchHandle`

```python
run_id: str
channel: Channel
handle: str            # the portable liveness token (local://host/pid)
is_alive() -> bool
wait(timeout=None) -> int | None    # blocks until finished, and reaps
terminate() -> None                 # force-kill where the substrate allows
```

Protocol: the observable surface of a launched worker, common to every launcher.
`is_alive` answers liveness; `wait` blocks until the worker finishes (and reaps
it); `terminate` force-kills where the substrate allows (`ThreadLauncher`
cannot, and raises).

### `ThreadLauncher`

```python
ThreadLauncher(*, root=None, backend="memory")
launch(run_id, target: Callable, *, args=(), kwargs=None) -> LaunchHandle
```

In-process launcher (tests / single-process orchestration): runs `target` on a
thread. Defaults to the memory backend. The concrete handle carries a
`.exception`.

### `LocalLauncher`

```python
LocalLauncher(*, root, backend="sqlite")
launch(run_id, cmd: list[str] | str, *, env=None) -> LaunchHandle
reap() -> None
```

Spawn workers as local subprocesses (the full handle story). `launch` runs a
command with `RUNSTATE_*` injected; the child calls `runstate.attach()` to
re-derive the same run's channel — so the backend must be cross-process durable
(sqlite, the default). As a context manager, best-effort reaps finished children
on exit (it does not block on or kill stragglers — that stays the caller's choice
via `wait` / `terminate`).

## Observables (the stateless observer plane) and orchestration

Pure folds `log → view` (stateless), plus the stateful `Watcher` and the
memoizer.

### `Watcher`

```python
Watcher(*, now=time.time, sleep=time.sleep, poll_interval=0.05,
        heartbeat_timeout=None, episode_grace=5.0)
add(handle: LaunchHandle) -> None
observe(run_id: str, channel: Channel) -> None
poll(run_id) -> RunStatus
wait(run_id, *, on_event=None, timeout=None) -> RunResult
wait_all(*, on_event=None, timeout=None) -> dict[str, RunStatus]
iter_events(timeout=None) -> Iterator[tuple[str, Envelope]]
broadcast(name, schedule: Condition, *, request_id=None) -> str
```

The stateful failure detector. `add`/`observe` track a run; `poll` returns a
`RunStatus` (`Running | RunResult`); `wait` blocks to a terminal verdict;
`wait_all` covers a set; `broadcast` fans one subscription across all tracked
runs under a shared `request_id` — the **cross-run barrier**. `poll` raises
`KeyError` for an untracked run. The Watcher adds the inference liveness tiers
(handle-probe, heartbeat-staleness) that need state (arrival times).

### `await_consumed`

```python
await_consumed(channel, seq, *, request_id=None, timeout=None,
               poll_interval=0.05, now=time.time, sleep=time.sleep) -> Nak | RunResult | None
```

Block until the control request at `seq` is answered or drained. **Answer-first**:
a `lifecycle.nak` bearing `request_id` that follows `seq` returns the `Nak`; a
terminal record following the request with no later episode returns the terminal
`RunResult` (refused-by-death); otherwise the heartbeat watermark
(`consumed_seq >= seq`) passing means accepted (returns `None`). Raises
`TimeoutError` if `timeout` elapses (not-yet-drained is not a refusal), and
`MalformedRecordError` on a nak body it cannot parse.

### `RunStatus`

```python
RunStatus = Running | RunResult
```

The sum type `Watcher.poll` / `wait_all` return: the run is either still in
flight (`Running`) or terminal (`RunResult`).

### `Running`

```python
Running(step: int | None = None, beacon_age: float | None = None)
```

The non-terminal arm of `RunStatus`: a run still in flight, with the Watcher's
live snapshot — `step` from the latest heartbeat, and `beacon_age` (seconds since
that heartbeat *arrived*, the gradient toward presumed-dead) which is
watcher-computed and not on the raw event stream.

### `RunResult`

```python
RunResult(outcome: Outcome, reason: str, run_id=None, error=None, final_step=None)
```

The terminal verdict: a *closed* `outcome` plus a verbatim `reason`. There is
deliberately **no `success` boolean** — whether a clean preemption "succeeded" is
a policy the consumer owns. `error` is the worker's self-diagnosed failure
diagnostic (or None); `final_step` the last step reached.

### `Outcome`

```python
Outcome  # StrEnum: COMPLETED, PREEMPTED, ERRORED, KILLED, PRESUMED_DEAD
Outcome.failures() -> frozenset  # {errored, killed, presumed_dead}
```

The CLOSED, normalized terminal verdict — the codomain of `RunResult.outcome`.
Each member IS its wire string (`Outcome.COMPLETED == "completed"`), so it
serializes byte-identically and compares equal to the bare strings on existing
logs — zero channel migration. The single authoritative home for the vocabulary.

### `MalformedRecordError`

```python
MalformedRecordError(seq: int, topic: str, detail: str)
```

A record on a verdict topic cannot be interpreted — the writer violated the
convention. Raised by the **verdict folds** (`peek_terminal`, `live_episode`,
`await_consumed`'s nak parse), which decide categorical answers from single
records and refuse to guess; the **measurement folds** (`progress`,
`value_series`, `live_demand`) skip junk instead. Callers wanting degradation
catch this.

### `peek_terminal`

```python
peek_terminal(channel) -> RunResult | None
```

Return a terminal `RunResult` if the run has left a terminal *record*, else None.
The record-based verdict (a clean `lifecycle.stopped`, or a reaped
`launcher.terminated`); the inference tier (heartbeat staleness ⟹
`presumed_dead`) is the stateful Watcher's job. **Episode-aware: a terminal
stands until a new episode claims.**

### `last_activity`

```python
last_activity(channel) -> float | None
```

When this run last did anything, by the newest dated record's own `t` — the
freshness clock a third-party observer reads. The newest `t` among
`latest(heartbeat)` / `latest(stopped)` / `latest(terminated)`; a handful of O(1)
reads, never a `max` over the whole log. None if no dated record exists yet.
Cross-clock by construction (`t` is the worker/reaper's wall-clock) — a
display-only estimate, never an ordering key.

### `latest_episode`

```python
latest_episode(channel) -> Envelope | None
```

The latest `lifecycle.started` envelope, or None if no worker ever attached.
*Latest* means latest — live, cleanly ended, or crashed alike. The envelope's
`seq` is the episode-window watermark; its body parses via `Started(**e.body)`.
Owns the episode-boundary *rule* (an episode is a read-side derivation, not a
record) in one place.

### `live_episode`

```python
live_episode(channel) -> str | None
```

Handle of the currently-live episode, or None: the latest episode with no
following `stopped` whose worker resolves alive (a started-then-crashed episode
resolves dead → not live).

### `live_demand`

```python
live_demand(channel) -> list[Envelope]
```

The live leased demand: every `control.subscribe` envelope with no **answer**
following it by seq (an answer is a `control.unsubscribe` or `lifecycle.nak`
bearing its `request_id`), and — for time-referencing schedules — no episode
boundary between it and the latest `lifecycle.started`. The one public home of
the positional answer fold + the time-lease rule. Value-blind.

### `progress`

```python
progress(channel) -> int | None
```

Max step the trajectory reached, from the DENSE axis (the latest
`lifecycle.heartbeat.step` and `lifecycle.stopped.final_step`, whichever is
greater); None if neither has a value yet. **The window fencepost**: a target
`until={"step": N}` is the half-open window `[0, N)`, reached iff
`progress + 1 >= N`; `progress is None` is window-step 0.

### `undischarged_stops`

```python
undischarged_stops(channel) -> list[Envelope]
```

The `control.stop` envelopes not yet discharged — pending from append until the
next `lifecycle.stopped` follows by seq (one `stopped` discharges every pending
stop at once). The positional stop rule's public observer home, mirroring
`live_demand`. Note **pending ≠ due** and **naked stops over-report**
(conservative: never under-reports).

### `value_series`

```python
value_series(channel) -> dict[str, dict[int, Any]]
```

`{name: {step: value}}` — the run's reported values as functions of step, in one
log pass. The substrate's register projection lifted pointwise: last-write-wins
by `seq` per `(name, step)` cell (under an episode rewind the rewritten steps
last-win, the orphaned branch drops). Pure and cache-free; `request_id` is a
dedup concern only, ignored.

### `handle_pid`

```python
handle_pid(handle: str) -> int | None
```

The pid of a `local://host/pid` handle; None for a non-`local` scheme or an
unparseable token. Deliberately host-blind — pure grammar, no liveness claim
(hostname scoping lives in `resolve` only).

### `sweep`

```python
sweep(variants: Iterable[Variant], launcher, *, on_event=None, resume=True,
      stop_on_failure=False, watcher=None) -> list[RunResult]
```

Launch each variant into its own run and watch it to a terminal result,
sequentially. Returns one `RunResult` per run actually reached (a
`stop_on_failure` halt yields a shorter list).

### `Variant`

```python
Variant(run_id: str, target: object, launch_kwargs: dict = {})
```

One run's specification: a `run_id`, the launcher's `target` (a callable for
`ThreadLauncher`, a command for `LocalLauncher`), and any launcher-specific
kwargs forwarded to `launch`.

### `history`

```python
history(channel, name: str, schedule: Condition) -> list[Body]
```

Replay `schedule` (the Subscription algebra) over the logged `value` points for
`name`; return the bodies it fires on, in step order. Collapses by step (a
resumed episode re-emits the checkpoint overlap), taking the latest by `seq` —
the as-resumed branch. Time conditions are run-relative to the epoch (earliest
`lifecycle.started`); a time-referencing schedule with no epoch raises. A
nonconforming record is skipped; a conforming point with `step` null raises.

### `ensure`

```python
ensure(producer, name: str, *, until: Condition, poll_interval=0.01,
       sleep=time.sleep, clock=time.time) -> list[Body]
```

Return `name`'s series for the window `until`, producing the missing suffix on a
miss. Window-closed (or worker-declared `completed`) → a pure log read; else
`producer.extend(until)` and wait, **re-driving `preempted` and raising on a
failure outcome or no progress**. The worker contract: a resumable/chunked
producer stops `preempted` per chunk; a per-chunk `completed` claim ends the
drive early with the truncated series. Raises `TypeError`/`ValueError` on a bad
`until`, `RunFailedError` on a failure verdict, `NoProgressError` on a stalled
own-spawn.

### `launch_producer`

```python
launch_producer(launcher, variant: Variant, *, target_key="up_to") -> Producer
```

A producer backed by `launcher` relaunching `variant`, injecting the target into
the worker kwargs under `target_key`. For a **callable-worker** launcher (e.g.
`ThreadLauncher`) whose worker receives its config as `kwargs`. A subprocess,
ray, or service worker plumbs the target differently and gets its **own**
producer implementing `.channel` / `.run_id` / `.extend` (the seam).

### `foreign_episode`

```python
foreign_episode(channel) -> Producer  # a foreign-episode handle
```

The one public copy of the producer gate's foreign-episode handle. Compose a
producer's `extend` as `relaunch_if_needed(...) or foreign_episode(channel)`.

### `relaunch_if_needed`

```python
relaunch_if_needed(launcher, run_id, target, **launch_kwargs) -> LaunchHandle | None
```

Launch `target` into `run_id` only if no episode is currently live — a
launcher-agnostic, best-effort single-spawn guard over `live_episode` + `launch`.
Returns the new handle, or None if a live episode already exists. Correctness
rests on the worker's self-claim; this only avoids the wasted spawn in the common
already-live case.

### `ensure_served`

```python
ensure_served(launcher, run_id, target, **launch_kwargs) -> LaunchHandle | None
```

Wake a service iff there is live leased demand and no live episode —
`relaunch_if_needed`'s leased-demand sibling (two demand durabilities, two
deciders). Caller-invoked: subscribe, then `ensure_served` — the demander's
presence is the keepalive *and* the waker. The standing-daemon form is this in a
loop with a **mandatory `launcher.reap()`** per cycle. Never `Watcher.add()` the
returned handle (it may lose the claim race); `observe()` the run.

### `RunFailedError`

```python
RunFailedError(run_id: str, result: RunResult)
```

`ensure`'s producer run died with a failure verdict (`errored` / `killed` /
`presumed_dead`). `result` is the `RunResult` observed **at raise time** — the
retry decision's input, handed over rather than racily re-read.

### `NoProgressError`

```python
NoProgressError(run_id: str, *, progress: int | None, until: Condition)
```

`ensure`'s OWN spawn died without advancing the step frontier, and no live
episode owns the run — relaunching would spin, so refuse. A foreign episode's
no-progress death re-drives instead (the guard is own-spawn-scoped), and a live
foreign claim skips the raise (claim-aware).

## Convention vocabulary

### `Topic`

```python
Topic  # StrEnum: the closed, protocol-owned routing keys
# VALUE, LIFECYCLE_STARTED, LIFECYCLE_HEARTBEAT, LIFECYCLE_STOPPED, LIFECYCLE_NAK,
# LAUNCHER_LAUNCHED, LAUNCHER_TERMINATED, CONTROL_STOP, CONTROL_SUBSCRIBE, CONTROL_UNSUBSCRIBE
```

The CLOSED, protocol-owned routing keys (`Envelope.topic`) — the complete
enumerable set, including the body-less `control.*` verbs. Each member IS its
wire string (`Topic.VALUE == "value"`); the `name` axis stays open/app-owned. A
body-bearing topic's `<Payload>.TOPIC` is a typed alias of the same member
(`Stopped.TOPIC is Topic.LIFECYCLE_STOPPED`).

### `Condition`

```python
Condition = dict[str, Any]
```

A term of the subscription **condition-algebra**: a threshold (`{"step": N}` /
`{"time_seconds": S}` / `{"count": C}`), or `{"any": [...]}` / `{"all": [...]}`,
fully recursive. Used as `from` / `every` / `until` in a subscription body and as
the `schedule` argument to `history` / `Watcher.broadcast` and `until` in
`ensure`.

## Convention bodies (typed)

Frozen dataclasses mirroring the schemas: serialize via `dataclasses.asdict`,
parse via `Cls(**body)`. Each carries a `TOPIC` ClassVar (the `Topic` member it
rides on). The [wire topics](#wire-topics) table below maps each to its schema.

### `Value`

```python
Value(value: Any, step: int | None, t: float | None)
```

A worker's current value for `name`, sampled per a subscription (or emitted
unconditionally). `name` (envelope) says which metric; `request_id` (envelope)
says which subscription it answers.

### `Started`

```python
Started(handle: str, t: float)
```

Pushed on attach; the worker self-reports its liveness `handle`. `t` = the
emitter's wall-clock (the run epoch). Required and non-null (`seq` orders, `t`
measures); renamed from v0.3's `attached_at`.

### `Heartbeat`

```python
Heartbeat(step: int | None, consumed_seq: int, t: float)
```

Tick-driven liveness beacon: progress (`step`, null for a stepless service) + the
consumption watermark (`consumed_seq`) + freshness (`t`). This is the record
liveness reads, so `t` dates the newest beacon for a third-party observer.

### `Stopped`

```python
Stopped(completed: bool, error: str | None, final_step: int | None, t: float)
```

The cooperative dying breath; its existence on the log = a clean, *resumable*
halt. `completed=True` is the opt-in completion claim; otherwise it projects to
`preempted`. `error` is the failure diagnostic; a completed stop carries no error
(enforced).

### `Nak`

```python
Nak(reason: str, message: str)
```

A refused control request, correlated by `request_id`. `reason ∈ {malformed,
unsatisfiable, unsupported}`; dropped, never fatal to the worker.

### `Launched`

```python
Launched(handle: str, t: float, status: str = "running")
```

Spawn-intent + the worker's liveness handle. `t` = the spawner's wall-clock at
launch (dates a never-beaconed startup crash too).

### `Terminated`

```python
Terminated(reason: str, exit_code: int | None, signal: int | None, t: float)
```

The manner of death: `exited(exit_code)` XOR `killed(signal)`. `t` = the reaper's
wall-clock at the reaped death. Only a `wait()`-ing parent can produce it.

---

## Wire topics

The reserved topics, their body shape, producer/consumer, and the schema file
that pins each (`protocol/`). The substrate routes on `topic`/`name`/`request_id`
and never parses `body`.

| topic | body | produced by | consumed by | schema |
|---|---|---|---|---|
| `control.subscribe` | `Condition` (`{from?, every?, until?}`) | orchestrator | worker | `subscription-v0.2` |
| `control.unsubscribe` | `{}` | orchestrator (or worker on expiry) | worker | `subscription-v0.2` |
| `control.stop` | `{from?}` | orchestrator | worker | `subscription-v0.2` |
| `lifecycle.started` | `Started {handle, t}` | worker | observers | `lifecycle-v0.4` |
| `lifecycle.heartbeat` | `Heartbeat {step?, consumed_seq, t}` | worker | observers | `lifecycle-v0.4` |
| `lifecycle.stopped` | `Stopped {completed, error, final_step, t}` | worker | observers | `lifecycle-v0.4` |
| `lifecycle.nak` | `Nak {reason, message}` | worker | the requester (by `request_id`) | `lifecycle-v0.4` |
| `launcher.launched` | `Launched {handle, t, status}` | launcher | observers | `launcher-v0.4` |
| `launcher.terminated` | `Terminated {reason, exit_code?, signal?, t}` | launcher | observers | `launcher-v0.4` |
| `value` | `Value {value, step?, t?}` | worker | observers | `value-v0.2` |

The envelope itself is pinned by `envelope-v0.2`. Each convention schema is
`additionalProperties: false` and independently versioned; adding a field is a
deliberate version bump (never silent).

## Environment variables

`attach()` (and `LocalLauncher`, which sets them for the child) reads:

| variable | meaning | default |
|---|---|---|
| `RUNSTATE_RUN_ID` | the run to attach to | required (else `KeyError`) |
| `RUNSTATE_CHANNEL_ROOT` | the directory/namespace holding runs | `None` |
| `RUNSTATE_CHANNEL_BACKEND` | `memory` / `sqlite` / `postgres` | `sqlite` |
| `RUNSTATE_LAUNCH_ID` | the launch's correlation id (re-emitted on `started`) | `None` |
| `RUNSTATE_SQLITE_JOURNAL_MODE` | sqlite journal mode (`DELETE` on NFS) | `WAL` |

## The CLI

A minimal terminal tool ships as the `runstate` console script (`runstate/cli.py`,
stdlib `argparse`, **sqlite only**). It is a **tool, not API** — not in
`runstate.__all__`.

```bash
runstate status <root>                    # snapshot table: run_id, verdict, progress, age
runstate stop <root> <run_id> [--wait N]  # send a cooperative control.stop
```

`status` discovers both the flat (`<root>/<rid>.db`) and content-addressed
sharded (`<root>/runs/<xx>/<rid>/<rid>.db`) layouts, read-only. `stop` warns when
the run is down (the stop is then armed for the next episode); `--wait N` blocks
up to N seconds for the stop to be answered (accepted / nak / refused-by-death /
timeout). Deliberately not a daemon or live viewer.

## Exceptions

The public exception types and when they raise:

- **`MalformedRecordError`** — a verdict-topic record cannot be interpreted;
  raised by the verdict folds (`peek_terminal`, `live_episode`, `await_consumed`).
  The measurement folds skip junk instead.
- **`RunFailedError`** — `ensure`'s producer run died with a failure outcome
  (`errored` / `killed` / `presumed_dead`); carries the `RunResult` at raise time.
- **`NoProgressError`** — `ensure`'s own spawn died without advancing the step
  frontier and no live episode owns the run.

Builtin exceptions from the public functions: `open_channel` raises `ValueError`
(bad backend) / `ImportError` (postgres without psycopg); `attach` raises
`KeyError` (unset `RUNSTATE_RUN_ID`); `Watcher.poll` raises `KeyError` (untracked
run); `ensure` raises `TypeError` / `ValueError` (bad `until`); `Worker.emit`
raises `ValueError` (before the first tick / stepless).
