# Integrate your training loop in 15 minutes

A task-oriented guide for someone who has a training loop (or a simulation, or
any long-running job) and wants an orchestrator to **watch it and steer it while
it runs** — subscribe to its metrics, stop it early, resume it from a
checkpoint, reuse a finished run's results.

This is the how-to, for **log format 0.4.0**. For *why* the system is shaped this
way, read [`overview.md`](overview.md) (the guided tour) and
[`specs/commits-and-lineage.md`](specs/commits-and-lineage.md) (the authority on
0.4.0; "spec §N" below is that spec's). For the exact public surface,
[`api.md`](api.md). Writing a non-Python implementation? The wire-level guide is
[`implementers-guide.md`](implementers-guide.md).

*Derived, not authoritative: on any disagreement the schemas and
`specs/commits-and-lineage.md` win.*

**The code blocks run.** A block fenced ` ```python ` is a complete program, and
`tests/test_guide.py` runs every one of them, each on its own, against the
current `runstate/`. A block fenced ` ```python fragment ` is a piece of a larger
program, most often a runnable one under `examples/` (named inline, and run by
`tests/test_examples.py`); it is shown, not run. Worker-side blocks call
`runstate.current_channel()`, which reads the `RUNSTATE_*` variables a launcher
sets (section 3); to run one by hand, set `RUNSTATE_RUN_ID` and
`RUNSTATE_CHANNEL_ROOT` (an existing directory) first.

Coming from format 0.3.0? Read [the migration note](#coming-from-format-030)
at the end first.

---

## 1. Install

```bash
pip install runstate            # Python >= 3.11, stdlib-only core
pip install "runstate[postgres]"  # + the cross-host Postgres backend
```

> **Not on PyPI yet.** The release machinery is prepared but the first release
> is owner-gated (`backlog/release-and-stability-contract.md`); until it lands,
> install from a clone: `pip install -e .`

The library is pure-Python and has no required dependencies. The examples below
run against the default SQLite backend (a single file per run) or an in-process
Memory backend (tests / single-process orchestration).

## 2. Wrap your loop (the worker side)

Your training script becomes a **worker**. The wrap is three pieces:

- `runstate.current_channel()` opens this run's channel (from the `RUNSTATE_*`
  env vars a launcher set — see section 3);
- `with runstate.Worker(channel, resume_from=..., sources=..., save=...) as w:`
  **claims** the run, drains control requests, answers subscriptions, and
  writes the `lifecycle.*` records for you;
- inside, `w.steps(until)` yields each iteration's index and, after your body,
  **commits** the iteration and checks for a commanded stop.

```python
import math

import runstate

channel = runstate.current_channel()  # reads the RUNSTATE_* a launcher set
with runstate.Worker(
    channel,
    resume_from=None,  # a fresh start: no node of this run to continue
    sources=["lr"],  # sampled on demand; every other name is an eager stream
    save=runstate.NoSave(),  # no checkpoints (section 4 adds them)
) as w:
    for step in w.steps(runstate.Target(runstate.Commits(), 50)):
        loss = max(0.01, 5.0 * 0.97**step + math.sin(step * 0.2) * 0.1)
        w.emit("loss", loss)  # an element of `loss`, committed by this iteration
        w.set("lr", 0.1 * 0.99**step)  # the current value, logged only if asked
    if not w.stop_pending:  # ran the whole budget, rather than being stopped
        w.stopped(completed=True, error=None)

head = runstate.LatestClaimHead()
assert len(runstate.series(channel, "loss", head=head)) == 50
assert runstate.peek_terminal(channel).outcomes == {runstate.Outcome.COMPLETED}
```

`examples/minimal/worker.py` is the same wrap, launched as a subprocess.

### The Worker's three choices

All three are **required and keyword-only**, so no reader re-derives them:

- **`resume_from`** — where the claim starts. `None` is a fresh start (or state
  from outside this run); a checkpoint object resumes from the node it was
  saved at (section 4); `runstate.LatestClaimHead()` continues the previous
  claim's head, for a worker that holds no state (a woken service, or a loss
  that is a pure function of the index). `AtNode(x)` branches from a node you
  choose.
- **`sources`** — the names this worker exposes to be **sampled on demand**
  (`[]` declares none). Every other name it reports is an **eager stream**.
- **`save`** — the checkpoint save policy: `runstate.NoSave()`, or
  `runstate.Every(k, fn)` (section 4).

### `emit` vs `set` — eager streams vs sources

Two ways to report a value; they differ in **who decides how often a point hits
the log**:

- **`w.emit(name, value)`** — an **eager stream**: a `value` record now, which
  the iteration's commit then names. Every emit is an element of the stream, in
  order. This is what `ensure`, `series`, `prefix_len` and `history` read. Use it
  for the curve you always want recorded.
- **`w.set(name, value)`** — a **source**: update the worker's register for a
  name declared in `sources`. Nothing is logged until a subscription fires
  (section 5), and then one sample is written and committed by that node. No
  subscribers means nothing logged. Use it for a gauge an observer may or may
  not watch.

`emit` raises on a declared source and `set` on an undeclared name, so a typo
cannot quietly change a name's kind. (A value sent through a separate channel
handle joins the next node with `w.include(seq)`.)

**Emit before you commit.** A value belongs to the node that commits it. One
emitted after an iteration's commit lands in the *next* node, and with a
checkpoint saved at the node in between, a crash loses it.

### The iteration: `commit()`, `heartbeat()`, `steps(until)`

- **`w.commit()`** ends an iteration: it drains control, evaluates
  subscriptions at the node being written, writes the fired sources' samples and
  one `lifecycle.commit` naming the iteration's values, runs the save policy, and
  returns the **stop decision** (`True` once a stop has held). One commit per
  iteration, so **`Commits()` counts iterations**.
- **`w.heartbeat()`** is liveness for inside a slow iteration: it drains
  control, writes a `lifecycle.heartbeat`, and returns the stop decision. It
  commits nothing and evaluates no subscription.
- **`w.steps(until)`** is the driver: it calls `commit()` after each body and
  yields the iteration's index, `count(Commits(), position)` before its commit.
  `until` is a `Target` (`Target(Commits(), n)` or `Target("loss", n)`: the
  first n of an eager stream) or `None` for no bound. It ends at the target or
  on a stop. There is no `start=`: the log holds the position, so a resumed
  driver continues run-absolute.
- **`w.serve()`** is the driver for demand (section 6): it commits once per
  iteration while some subscription is live, and retires at zero.
- **`w.stop_pending`** reads the stop decision without side effects, for a
  callback whose host loop cannot act on a return value.

A loop you own calls `commit()` itself, as a Lightning or HF Trainer callback
would:

```python
import time

import runstate

channel = runstate.current_channel()
with runstate.Worker(
    channel, resume_from=None, sources=[], save=runstate.NoSave()
) as w:
    for epoch in range(3):
        for batch in range(4):  # a slow iteration: stay witnessed inside it
            time.sleep(0.01)
            if w.heartbeat():  # liveness only; True once a stop has held
                break
        w.emit("val_loss", 1.0 / (epoch + 1))  # emit BEFORE the commit
        if w.commit():  # one node per iteration; returns the stop decision
            break

assert len(runstate.series(channel, "val_loss", head=runstate.LatestClaimHead())) == 3
```

### `rewind` and `discard`

- **`w.rewind(target)`** moves the claim's position back to an earlier node (a
  checkpoint object, or `None` for the root) without ending the claim: the
  worker, its leases and its liveness survive. Pending values are dropped (commit
  first to keep them), and the next index `steps()` yields moves. The abandoned
  branch stays on the log, readable at its own node. Section 4 shows one.
- **`w.discard()`** declares the open iteration **torn**: its pending values
  never commit, every later `commit()`/`heartbeat()` returns `True`, and the exit
  is torn (`values: null`). Call it from the worker's own flow, never inside a
  signal handler: a SIGTERM handler sets a flag, and the loop discards at its
  next safe point.

```python
import runstate

channel = runstate.current_channel()
preempt = {"flag": False}  # what a SIGTERM handler would set (never act in one)

with runstate.Worker(
    channel, resume_from=None, sources=[], save=runstate.NoSave()
) as w:
    for step in w.steps(None):  # no bound: run until stopped
        w.emit("loss", 1.0 / (step + 1))
        if step == 3:
            preempt["flag"] = True  # the signal arrives mid-iteration
        if preempt["flag"]:
            w.discard()  # this iteration's loss never commits
            break
# leaving the block writes a preempted exit; after discard() it is torn

assert runstate.peek_terminal(channel).outcomes == {runstate.Outcome.PREEMPTED}
assert len(runstate.series(channel, "loss", head=runstate.LatestClaimHead())) == 3
```

### The exit, and why completion is opt-in

The exit (`lifecycle.stopped`) is the claim's last node. It comes in three
kinds, and what can follow it depends on the kind:

| exit | how you get it | can a later claim continue it? |
|---|---|---|
| **closed** | `w.stopped(completed=True, error=None)` | no: the lineage is complete, so `ensure` past it is `Unreachable` |
| **extendable** | leaving the `with` block normally, or `stopped(completed=False, ...)`, or `serve()` retiring | yes: its pending values commit, and the next claim resumes from it |
| **torn** | an exception in the block (errored), or any exit after `discard()` | not from it: the next claim continues its `parent` |

`completed=True` is the worker's claim that the run is *intrinsically,
permanently* done, and it **closes the lineage**: nothing can ever extend it. So
it is opt-in. Leaving the block without it writes a clean exit whose outcome is
**`preempted`** — resumable. A chunked worker, which reaches the drive's target
and stops so a later drive can extend it, never claims `completed` (pitfall 2).

## 3. Orchestrate: spawn, subscribe, watch

Nothing spawns your worker for you — runstate ships **no `Orchestrator` class**.
You write a small script that composes a launcher, a subscription and a watcher.

A launcher is typed by its **target record**, `Launcher[T]`:

- **`ThreadLauncher`** runs a `ThreadTarget(fn, args=..., kwargs=...)` on a
  thread, as `fn(channel, *args, **kwargs)`, on the memory backend by default
  (tests, single-process orchestration);
- **`LocalLauncher(root=...)`** runs a `Command(argv, env=...)` as a
  subprocess, injecting the `RUNSTATE_*` env that `current_channel()` reads
  (`env={}` is "the parent's environment", said explicitly).

Each launch writes `supervisor.launched` and, at its reap,
`supervisor.terminated`, and a death report when the worker left no exit
(section 6). In-process, end to end:

```python
import math

import runstate


def train(channel: runstate.Channel, *, iterations: int) -> None:
    with runstate.Worker(
        channel, resume_from=None, sources=["loss"], save=runstate.NoSave()
    ) as w:
        for step in w.steps(runstate.Target(runstate.Commits(), iterations)):
            w.set("loss", 5.0 * math.exp(-0.1 * step))
        if not w.stop_pending:
            w.stopped(completed=True, error=None)


launcher = runstate.ThreadLauncher()  # in-process, on the memory backend
ch = launcher.create_channel("run-1")
# Subscribe BEFORE the launch: the log holds the request until a worker drains it.
ch.send(
    {"every": {"commits": 10}},  # sample `loss` at every 10th commit
    topic=runstate.Topic.CONTROL_SUBSCRIBE,
    name="loss",  # the source it targets
    request_id="dash",  # the name its answers bear
)
target = runstate.ThreadTarget(train, args=(), kwargs={"iterations": 50})
handle = launcher.launch("run-1", target)

watcher = runstate.Watcher(heartbeat_timeout=30.0)  # required: it changes a verdict
watcher.add(handle)
result = watcher.wait("run-1")
assert result.outcomes == {runstate.Outcome.COMPLETED}
assert result.tier is runstate.Tier.EXIT  # the verdict rests on the worker's word

answers = runstate.answers(ch, "dash", head=runstate.LatestClaimHead())
assert len(answers) == 5  # fired at commits 1, 11, 21, 31 and 41
```

With a subprocess worker the shape is the same; this is the skeleton of
`examples/minimal/driver.py`, which also streams the answers as they arrive:

```python fragment
with runstate.LocalLauncher(root=root) as launcher:
    ch = launcher.create_channel(run_id)
    ch.send(
        {"every": {"commits": 1}},
        topic=runstate.Topic.CONTROL_SUBSCRIBE,
        name="loss",
        request_id="driver",
    )
    cmd = runstate.Command([sys.executable, str(worker)], env={})
    handle = launcher.launch(run_id, cmd)

    watcher = runstate.Watcher(heartbeat_timeout=30.0, poll_interval=0.02)
    watcher.add(handle)
    heads, answers = runstate.HeadFold(), runstate.Answers("driver")

    def on_event(rid: str, e: runstate.Envelope) -> None:
        if e.topic in (
            runstate.Topic.LIFECYCLE_COMMIT,
            runstate.Topic.LIFECYCLE_STOPPED,
        ):  # answers land with nodes: poll the folds only then
            for a in answers.poll(ch, heads.poll(ch)):  # the whole list: diff it
                ...

    result = watcher.wait(run_id, on_event=on_event)
```

Spawn however you like — the launcher is opt-in. To run on SLURM (or AWS Batch,
or locally) via submitit, `examples/submitit/` is a bring-your-own-launcher
recipe that talks the same protocol; the eventual first-class adapter is
sketched in [`backlog/submitit-launcher.md`](backlog/submitit-launcher.md).

### The Watcher and the verdict

`Watcher(heartbeat_timeout=...)` is the stateful failure detector.
`heartbeat_timeout` is **required**: how long a run may go unwitnessed (no new
`started`, `commit`, `heartbeat` or `rewind` of its claim) before the Watcher
infers `presumed_dead`, or `None` for no staleness tier. Size it above your
slowest iteration, or heartbeat inside it. `add(handle)` tracks a launch you
hold, `observe(run_id, channel)` a run you don't; `poll` returns a `RunStatus`,
`Running(head, commits, beacon_age)` or the terminal `RunResult`; `wait` blocks
to the verdict, streaming records to `on_event`; `wait_all` covers a set.

A `RunResult` is the verdict, by **viewpoint**:

- **`outcomes`** — a never-empty `frozenset` of the **closed** `Outcome` enum:
  `completed`, `preempted`, `errored`, `killed`, `lost` (a death report that saw
  the claim end and nothing of how) and `presumed_dead` (the Watcher's
  inference). One element under the worker's own exit; under death reports, the
  set of their outcomes. There is **no `success` boolean**: whether a clean
  `preempted` "succeeded" is a policy you own. Test the set, as `ensure` does
  with `result.outcomes & runstate.Outcome.failures()`.
- **`tier`** — the viewpoint the verdict rests on: `exit` (the worker's own
  word), `died` (death reports), `terminated` (an unclaimed run's launch), or
  the Watcher's `probed_dead`, `heartbeat_stale` and `session_ended`.
- **`node`** (the exit node, under `exit`), **`claim_seq`**, **`reports`** (the
  death reports' `DeathReport(seq, outcome, evidence, t)`, under `died`),
  **`error`**, **`malformed`** and **`run_id`**.

"Is the work done?" is `runstate.worker_completed(result)` — the head is
closed — never `COMPLETED in result.outcomes`, which an unclaimed `sbatch`
wrapper's exit 0 also reads.

### Steer it: stop, withdraw, and "did it land?"

A **`control.stop`** asks the worker to stop cleanly at its next node or
heartbeat (`outcome → preempted`). Its body is an optional `from` (section 5's
algebra; absent, it holds as soon as it is drained), and its `request_id` is
**required and never reused**: mint one per stop (`uuid.uuid4().hex`). The
exit names every stop it honoured.

A **`control.withdraw`** (body `{t}`) spends the request its `request_id`
names, a stop or a subscription, by name. It needs no worker. A withdrawal that
arrives after the worker's stop decision has already held is too late: the exit
honours the stop.

**`await_consumed(ch, seq)`** answers "did my request land?" for the
`control.subscribe` or `control.stop` at `seq`: `None` once a claim has taken it
(a watermark reaches it, or a node or the exit names it), a `Nak` if the worker
refused it (`malformed`, `unsatisfiable` or `unsupported`), or the terminal
`RunResult` if the run ended without taking it. It raises at once where `None`
would be a false "accepted", such as a reused id.

```python
import time
import uuid

import runstate


def train(channel: runstate.Channel) -> None:
    with runstate.Worker(
        channel, resume_from=None, sources=[], save=runstate.NoSave()
    ) as w:
        for step in w.steps(runstate.Target(runstate.Commits(), 20)):
            w.emit("loss", 1.0 / (step + 1))
        if not w.stop_pending:
            w.stopped(completed=True, error=None)


def run(launcher: runstate.ThreadLauncher, rid: str) -> runstate.RunResult:
    handle = launcher.launch(rid, runstate.ThreadTarget(train, args=(), kwargs={}))
    watcher = runstate.Watcher(heartbeat_timeout=30.0)
    watcher.add(handle)
    return watcher.wait(rid)


launcher = runstate.ThreadLauncher()
STOP, WITHDRAW = runstate.Topic.CONTROL_STOP, runstate.Topic.CONTROL_WITHDRAW

# A stop that holds once the lineage has 5 commits.
ch = launcher.create_channel("stopped-early")
stop = ch.send({"from": {"commits": 5}}, topic=STOP, request_id=uuid.uuid4().hex)
result = run(launcher, "stopped-early")
assert result.outcomes == {runstate.Outcome.PREEMPTED}  # clean, and resumable
head = runstate.LatestClaimHead()
assert runstate.prefix_len(ch, runstate.Commits(), head=head) == 5
assert runstate.await_consumed(ch, stop) is None  # landed: the exit honoured it

# A stop its asker withdrew before any worker read it: spent, never honoured.
ch = launcher.create_channel("withdrawn")
rid = uuid.uuid4().hex
ch.send({"from": {"commits": 5}}, topic=STOP, request_id=rid)
ch.send({"t": time.time()}, topic=WITHDRAW, request_id=rid)
assert run(launcher, "withdrawn").outcomes == {runstate.Outcome.COMPLETED}
```

`examples/minimal/driver.py` sends its stop from inside `Watcher.wait`'s
`on_event` callback when the loss diverges; the Watcher's `pending_stops(run_id)`
lists the stops no exit, withdrawal or refusal has spent yet.

## 4. Checkpoints: the recipe

A `run_id` names a **durable log**, not a process. A claim can end and a later
claim on the *same* log can continue from a node of it. The checkpoint recipe
(spec §6) makes "the state I loaded is the state that node committed" true by
construction — no checkpoint can name work that never committed:

- **Save through the Worker.** `save=runstate.Every(k, fn)` calls `fn(node)`
  right after every k-th commit lands, and after every extendable exit, the one
  instant the process's state is exactly what `node` commits. You never pick the
  moment; the policy decides only *whether*.
- **A checkpoint names its node.** Any object with an integer `.node` is a
  `runstate.NodeRef`, which `resume_from` and `rewind` take. Pass the object
  itself, never `AtNode(ckpt.node)`, so the state and the node come from one
  place.
- **Choose before claiming:** `runstate.OnHeadLineage().choose(ch, candidates,
  head=...)` picks the candidate nearest the head on the head's lineage (the
  suggested preset; `MostProgress(stream)` is the other).
- **Claim, load, then assert** `loaded["node"] == w.resume_node`, which
  catches a file that changed after you chose it.
- **Publish atomically** (temp file, then `os.replace`): `write_text` truncates
  before it fills, so a worker killed mid-write leaves a checkpoint the next
  claim cannot parse.

This is `examples/reuse/`'s resumable cell, cut to its load-bearing half:

```python
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import runstate


@dataclass(frozen=True)
class Checkpoint:
    node: int  # the node it was saved at: this makes it a runstate.NodeRef
    path: Path

    def load(self) -> dict:
        return json.loads(self.path.read_text())


def write(path: Path, payload: dict) -> None:
    """Publish atomically: a reader sees the whole file or none of it."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def train(ch: runstate.Channel, store: Path, until: runstate.Target) -> None:
    # 1. choose, BEFORE claiming, among the checkpoints the store holds
    saved = [Checkpoint(int(p.stem), p) for p in store.glob("*.json")]
    head = runstate.LatestClaimHead()
    chosen = runstate.OnHeadLineage().choose(ch, saved, head=head)
    state = {"loss": 5.0}

    def save(node: int) -> None:  # the Worker calls it right after `node` lands
        write(store / f"{node}.json", {"node": node, "loss": state["loss"]})

    # 2. claim from it
    with runstate.Worker(
        ch, resume_from=chosen, sources=[], save=runstate.Every(2, save)
    ) as w:
        if chosen is not None:
            loaded = chosen.load()  # 3. load AFTER claiming,
            assert loaded["node"] == w.resume_node  # 4. and check the node
            state["loss"] = loaded["loss"]
        for _ in w.steps(until):
            w.emit("loss", state["loss"])  # this node's value
            state["loss"] *= 0.9
    # no completed claim: reaching a chunk's target is a pause, and the
    # preempted exit is extendable (Every saves at it too)


store = Path(tempfile.mkdtemp())
with runstate.create_channel("ckpt-demo", root=tempfile.mkdtemp()) as ch:
    train(ch, store, runstate.Target("loss", 5))  # claim 1: losses 0..4
    train(ch, store, runstate.Target("loss", 12))  # claim 2: resumes, 5..11
    losses = runstate.series(ch, "loss", head=runstate.LatestClaimHead())
    assert len(losses) == 12  # one lineage, nothing to collapse
    assert abs(losses[11].value - 5.0 * 0.9**11) < 1e-9  # the state carried over
```

A crashed claim's commits past its last checkpoint stay on the log, on a branch
the next lineage does not include: `examples/redrive/` shows exactly that.

**Roll back inside a claim** with `w.rewind(ckpt)`, and assert `w.resume_node`
again; the abandoned branch stays readable at its own node:

```python
import tempfile
from dataclasses import dataclass

import runstate


@dataclass(frozen=True)
class Snapshot:  # an in-memory checkpoint: a NodeRef, and the state at it
    node: int
    loss: float


snapshots: list[Snapshot] = []
state = {"loss": 1.0, "spiked": False}


def save(node: int) -> None:  # the Worker calls it right after `node` lands
    snapshots.append(Snapshot(node, state["loss"]))


with runstate.create_channel("rewind-demo", root=tempfile.mkdtemp()) as ch:
    with runstate.Worker(
        ch, resume_from=None, sources=[], save=runstate.Every(1, save)
    ) as w:
        for step in w.steps(runstate.Target("loss", 8)):
            spike = step == 5 and not state["spiked"]
            state["loss"] *= 100.0 if spike else 0.5
            state["spiked"] |= spike
            w.emit("loss", state["loss"])
            if state["loss"] > 1.0:  # diverged: roll back past the last good node
                back = snapshots[-2]
                w.rewind(back)  # the pending loss is dropped; the next index moves
                assert w.resume_node == back.node  # the load assertion, at a rewind
                state["loss"] = back.loss

    losses = runstate.series(ch, "loss", head=runstate.LatestClaimHead())
    assert len(losses) == 8 and max(e.value for e in losses) < 1.0
    abandoned = runstate.series(ch, "loss", head=runstate.AtNode(snapshots[4].node))
    assert len(abandoned) == 5 and abandoned[-1] not in losses
```

## 5. Subscriptions

A **subscription** (`control.subscribe`) is a standing request: "sample source
`name` on *this* schedule." Its envelope `name` is the **source** it targets (a
name the worker declared in `sources`), and its `request_id` names it and its
answers. Each sample a firing takes is committed by the node that fired it, so
its answers are a lineage read like any other: `answers(ch, request_id,
head=...)`, or the held `Answers` fold. Eager streams are not subscribed to;
they are read (section 7).

### The schedule (the condition-algebra)

Each of `from` / `every` / `until` is a `Condition`: an atom, or
`{"any": [...]}` (first to hold) / `{"all": [...]}` (last to hold). The atoms
are **counts along the lineage**, plus the claim's clock:

| atom | holds once |
|---|---|
| `{"commits": n}` | the lineage has n commits (iterations) |
| `{"stream": "loss", "n": n}` | the lineage has n elements of the eager stream `loss` |
| `{"time_seconds": s}` | s seconds have passed since this claim drained the request |
| `{"count": n}` | the subscription has fired n times (`until` only) |

| body | meaning |
|---|---|
| `{}` | once, at the next node |
| `{"from": {"stream": "loss", "n": 100}}` | once, when the lineage holds 100 losses |
| `{"every": {"commits": 1}}` | at every commit, for good |
| `{"every": {"commits": 10}, "until": {"commits": 5000}}` | every 10th commit, to 5000 |
| `{"every": {"commits": 1}, "until": {"count": 3}}` | three samples |
| `{"every": {"time_seconds": 0.5}, "until": {"time_seconds": 60}}` | twice a second, for this claim's next minute |

Subscriptions are evaluated at each `commit()` and at an exit that did not
discard its iteration, never at a heartbeat. `every` is a delta (it fires when
the count has grown by n since its last firing), the first firing is
immediate, and a source with no value yet does not fire. A `control.stop` takes
the same atoms in its `from`, but no `count`.

**The worker refuses** (a `nak` of reason `unsatisfiable`, which spends the id)
a subscription whose `name` it did not declare a source, a progress atom naming
a source (its count would depend on who asked), and an empty window (a `from`
that implies the `until`).

### Lease or durable

The atoms decide a subscription's **class**, and the class decides its scope:

- **Durable** — every atom is a lineage count (`commits`, `stream`). It lives
  **per lineage**: it outlives every claim, a relaunched worker serves it again,
  and a lineage that has not expired it (after a rewind, say) re-arms it.
- **Lease** — any `time_seconds` or `count` atom. It lives **per claim**: the
  worker that drains it binds it to its claim (`lifecycle.bound`), its clock and
  fire count are that claim's, and it is **void once that claim has ended**. A
  countdown cannot honestly outlive the worker that was counting, so a dead
  client's lease can never haunt a later claim. Renew a lease by re-sending its
  id while the claim holds it; after a crash, subscribe under a fresh id.

Bounds meant to outlive workers are spelled in commits or stream counts, not
seconds. A re-send of a live id may change its schedule, but not its target or
its class: that re-send is refused `malformed`.

### Pure demand

A subscription with `name=None` targets nothing: **pure demand**. It never
fires and takes no sample; it only keeps a `serve()` worker alive. Its body
takes only an `until` (`{"until": {"commits": 8}}`, durable;
`{"until": {"time_seconds": 30}}`, a lease), and with none it lasts until
withdrawn. `Watcher.broadcast(None, schedule)` posts one to every tracked run.

### The three request scopes

| request | scope | spent by | void |
|---|---|---|---|
| a stop | **by name**, for the whole log: it is about the process | the exit's `honored`, a `control.withdraw`, a `nak` | never |
| a lease | **per claim** | a withdraw, a `nak`, or any node's `expired` | once its claim has ended |
| a durable subscription | **per lineage** | a withdraw or a `nak`; an `expired` on the head's lineage only | never |

A stop honoured on one branch is not re-armed on another; a durable
subscription another branch expired is live again on one that has not.
`live_demand(ch)` (one-shot) and the held `LiveDemand` fold read "who still
wants something" by these rules; nothing is demanded of a closed head. The
wire-level detail is [`implementers-guide.md`](implementers-guide.md) §3.5–§3.7
and §3.11.

## 6. Services, deciders and death reports

### A service: `serve()`, `ensure_served` and pure demand

A **service worker** runs while someone wants it and retires when demand
drains (`examples/monitor/`). It drives with `serve()`, declares what it
reports as sources, and resumes from `LatestClaimHead()`, since it holds no
state. **`ensure_served(launcher, run_id, target, *, demand=)`** launches it
when there is live demand and no held claim; `demand` is the caller's
`LiveDemand` fold for the run, held across calls:

```python
import os

import runstate


def service(channel: runstate.Channel) -> None:
    # A woken service holds no state: it continues the previous claim's head.
    with runstate.Worker(
        channel,
        resume_from=runstate.LatestClaimHead(),
        sources=["load1"],
        save=runstate.NoSave(),
    ) as w:
        for _ in w.serve():  # one commit per iteration while demand is live
            w.set("load1", os.getloadavg()[0])


launcher = runstate.ThreadLauncher()
target = runstate.ThreadTarget(service, args=(), kwargs={})
ch = launcher.create_channel("monitor")
demand = runstate.LiveDemand()  # held across calls: each poll reads what is new
SUBSCRIBE = runstate.Topic.CONTROL_SUBSCRIBE

# A lease (it has a count atom): three samples, bound to the claim that serves it.
ch.send(
    {"every": {"commits": 1}, "until": {"count": 3}},
    topic=SUBSCRIBE,
    name="load1",
    request_id="dash-1",
)
handle = runstate.ensure_served(launcher, "monitor", target, demand=demand)
assert handle is not None  # live demand, no held claim: it launched
handle.wait()  # the service retired when its only lease expired
head = runstate.LatestClaimHead()
assert len(runstate.answers(ch, "dash-1", head=head)) == 3
assert demand.poll(ch) == []  # spent by the node whose `expired` names it

# Pure demand: no target, so no samples; it keeps a server alive to 8 commits.
ch.send({"until": {"commits": 8}}, topic=SUBSCRIBE, name=None, request_id="warm")
handle = runstate.ensure_served(launcher, "monitor", target, demand=demand)
assert handle is not None  # re-woken: a second claim on the same run
handle.wait()
assert runstate.prefix_len(ch, runstate.Commits(), head=head) == 8
assert len(ch.read(topics=[runstate.Topic.LIFECYCLE_STARTED])) == 2
```

A standing waker calls `ensure_served` in a loop with a `launcher.reap()` per
cycle, and `observe()`s the run rather than `add()`ing the returned handle,
which may lose the claim race. **`relaunch_if_needed(launcher, run_id,
target)`** is the durable-demand sibling: launch unless the newest claim is
held.

### Death reports: how a claim ends without its exit

A claim has **ended** once its exit or a **death report** naming it
(`supervisor.died`) is on the log. Nothing else ends it: no read probes a
process, so a same-host crash that nobody reports reads as *held*. Writing a
report is an act any party with **evidence** may take — a reap, an accounting
record, a probe that finds the process gone — and never an inference:
staleness and silence are not evidence, since a slow worker is alive.

- **The launchers report for you.** When the worker left no exit, a
  `LocalLauncher`'s reap writes `died` beside `terminated` (`reap: exit <n>`,
  `reap: signal <s>`), and a `ThreadLauncher` does when its thread finishes.
- **`report_if_dead(ch)`** records what a same-host probe or a backend's
  session probe finds (`lost`). The Worker's claim path, the deciders and
  `ensure` call it before they act on a held claim.
- **`report_death(ch, claim_seq, *, outcome, evidence, force)`** is for
  everyone else, such as a reclaim tool reading SLURM accounting. It refuses
  (`ClaimAliveError`) when the claim's handle probes alive, unless `force=True`,
  and returns `None` when the claim already has a readable exit.

A worker that reads a report naming its own claim is **released**: it writes
nothing more, and every driver ends, so a mistaken report costs at most the
iteration in flight.

```python
import tempfile

import runstate

with runstate.create_channel("stranded", root=tempfile.mkdtemp()) as ch:
    # A worker claims, commits once, and then its process is lost (here: we just
    # stop calling it), so its claim is held and nothing will write its exit.
    w = runstate.Worker(ch, resume_from=None, sources=[], save=runstate.NoSave())
    w.emit("loss", 1.0)
    w.commit()
    assert runstate.peek_terminal(ch) is None  # held: no verdict yet

    # A party with evidence (say, the scheduler's accounting) ends the claim by
    # naming it. force=True: the evidence outranks a probe that finds the pid
    # alive, as this one would, since the "dead" worker is this process.
    claim = runstate.latest_episode(ch).seq
    killed = runstate.Outcome.KILLED
    runstate.report_death(
        ch, claim, outcome=killed, evidence="slurm:TIMEOUT", force=True
    )
    r = runstate.peek_terminal(ch)
    assert r.tier is runstate.Tier.DIED and r.outcomes == {killed}
    assert [d.evidence for d in r.reports] == ["slurm:TIMEOUT"]
    assert runstate.live_episode(ch) is None  # the claim gate is open again

    # Were the worker alive after all, its next drain reads the report and it
    # fences itself: it writes nothing more, and every driver ends.
    assert w.commit() is True and w.released
```

And the launcher's own report, for a worker that left no exit:

```python
import runstate


def lost(channel: runstate.Channel) -> None:
    w = runstate.Worker(channel, resume_from=None, sources=[], save=runstate.NoSave())
    w.commit()
    # returns without an exit: no `with`, so nothing writes lifecycle.stopped


launcher = runstate.ThreadLauncher()
handle = launcher.launch("lost", runstate.ThreadTarget(lost, args=(), kwargs={}))
watcher = runstate.Watcher(heartbeat_timeout=30.0)
watcher.add(handle)
r = watcher.wait("lost")  # the reap wrote supervisor.died beside terminated
assert r.tier is runstate.Tier.DIED and r.outcomes == {runstate.Outcome.LOST}
assert r.reports[0].evidence == "reap: returned"
```

## 7. Reads by head

A run's values form a **lineage**: the chain of nodes from a **head** back to
the root. A rewind, or a resume from an older checkpoint, starts a branch, and a
read that depends on a branch takes a **required, keyword-only `head`**, chosen
once at the edge:

- **`LatestClaimHead()`** — the newest claim's head. Its read is *settled* (its
  subject cannot grow) once the head is closed.
- **`AtNode(node)`** — that node: always settled, permanent, and the way to
  read an abandoned branch or the node `ensure` returned.
- A resolved **`Head(node, settled, w)`** (`strategy.resolve(ch)`) is itself a
  strategy: a polled caller resolves once per poll and passes it to every read.

The reads (each one-shot, reading nothing above the `last_seq()` it takes
first):

| read | gives |
|---|---|
| `series(ch, stream, *, head)` | an eager stream's elements, in lineage order |
| `prefix_len(ch, x, *, head)` | `count(x, head)`, for a stream or `Commits()`: a count, never a 0-based label |
| `prefix(ch, stream, n, *, head)` | `Complete(elements)`, `Pending(partial)` or `SettledShort(partial)` |
| `last_element(ch, stream, *, head)` | the last element in lineage order, or `None` |
| `aligned(ch, names, *, head, axis)` | one `AlignedRow(x, node, cells)` per node, x the axis's count |
| `answers(ch, request_id, *, head)` | a subscription's samples, one per firing |
| `streams(ch, *, head)` | `Streams(eager, sources)`: the names with a committed value |
| `history(ch, stream, schedule, *, head)` | a schedule replayed over the lineage's nodes |

An element is `Element(node, seq, value, t)`. `prefix` tells "not yet" from
"never": only `Complete` has `.elements`, so a short read cannot pass for a
complete one.

```python
import tempfile

import runstate
from runstate import Commits, LatestClaimHead

with runstate.create_channel("reads", root=tempfile.mkdtemp()) as ch:
    ch.send(  # a durable subscription to the `lr` source, every 4th commit
        {"every": {"commits": 4}},
        topic=runstate.Topic.CONTROL_SUBSCRIBE,
        name="lr",
        request_id="lr-every-4",
    )
    with runstate.Worker(
        ch, resume_from=None, sources=["lr"], save=runstate.NoSave()
    ) as w:
        for step in w.steps(runstate.Target("loss", 10)):
            w.emit("loss", 1.0 / (step + 1))
            if step % 3 == 2:
                w.emit("acc", step / 10)  # a sparser eager stream
            w.set("lr", 0.1 / (step + 1))
        w.stopped(completed=True, error=None)

    head = LatestClaimHead()  # chosen once, at the edge
    assert runstate.streams(ch, head=head) == runstate.Streams(
        eager=frozenset({"loss", "acc"}), sources=frozenset({"lr"})
    )
    assert runstate.prefix_len(ch, "loss", head=head) == 10
    assert runstate.prefix_len(ch, Commits(), head=head) == 10
    assert len(runstate.series(ch, "acc", head=head)) == 3
    assert runstate.last_element(ch, "loss", head=head).value == 0.1

    # prefix tells "not yet" from "never": this lineage is closed (completed)
    match runstate.prefix(ch, "loss", 20, head=head):
        case runstate.SettledShort(partial=partial):
            assert len(partial) == 10  # no more will ever come
        case other:
            raise AssertionError(other)

    # one row per node committing any of the names, x the axis's count there
    rows = runstate.aligned(ch, ["loss", "acc"], head=head, axis=Commits())
    assert [r.x for r in rows if r.cells["acc"]] == [3, 6, 9]

    # a source's samples are the answers to the request that fired them
    samples = runstate.answers(ch, "lr-every-4", head=head)
    assert [s.value for s in samples] == [0.1, 0.1 / 5, 0.1 / 9]  # commits 1, 5, 9

    h = head.resolve(ch)  # a Head is itself a strategy: one W for many reads
    assert h.settled and len(runstate.series(ch, "loss", head=h)) == 10
```

### Held folds, for a polled path

A polled read must cost the same however old the run is, so the one-shot reads
above never run on a polled path. Hold a fold instead: each `poll` reads only
the records since the last, and equals its pure read after every record.
`HeadFold` is `LatestClaimHead`, held; `PrefixLen(x)`, `LastElement(stream)` and
`Answers(request_id)` take the polled head; `LiveDemand` and `PendingStops`
read by name and take none.

```python
import time

import runstate


def train(channel: runstate.Channel) -> None:
    with runstate.Worker(
        channel, resume_from=None, sources=[], save=runstate.NoSave()
    ) as w:
        for step in w.steps(runstate.Target("loss", 30)):
            w.emit("loss", 1.0 / (step + 1))
            time.sleep(0.005)


launcher = runstate.ThreadLauncher()
ch = launcher.create_channel("polled")
launcher.launch("polled", runstate.ThreadTarget(train, args=(), kwargs={}))

heads = runstate.HeadFold()
done = runstate.PrefixLen(runstate.Commits())
latest = runstate.LastElement("loss")
while True:
    head = heads.poll(ch)  # resolve the head once per poll ...
    n, last = done.poll(ch, head), latest.poll(ch, head)  # ... and read at it
    if last is not None:
        print(f"{n:>2} commits, loss {last.value:.3f}")
    if runstate.peek_terminal(ch) is not None:
        break
    time.sleep(0.02)
assert done.poll(ch, heads.poll(ch)) == 30
```

`Answers` returns the whole list along the head, and re-derives it when the
head moves off its last lineage, so a caller that streams answers diffs the
lists (as `examples/minimal/driver.py` does).

## 8. Reuse and extend: `ensure`

### Reuse by content-addressed `run_id`

Derive the `run_id` from the inputs, so asking again for the same computation
is free and asking for *more* extends the same lineage (the `run_id` recipe,
`specs/run-id-recipe.md`):

```python fragment
def run_id(inputs: dict) -> str:
    canon = json.dumps(inputs, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]
```

Crucially, the `run_id` **excludes the target** — the target is the axis you
extend along, so `lr=0.3` maps to one log whether you ask for 8 losses or 20.

### `ensure` — read first, produce on a miss

**`ensure(producer, target)`** returns **`Reached(node)`** when the newest
claim's lineage holds the `Target` (a cache hit, no worker), returns
**`Unreachable(closed_at)`** when the head is closed below it, and otherwise
calls `producer.extend(target)` and waits. Neither outcome carries elements:
read at `AtNode(node)`, which is permanent.

A **producer** is any object with `.channel`, `.run_id` and
`.extend(target)`, returning a liveness handle. The default,
**`launch_producer(launcher, run_id, target)`**, relaunches a `ThreadTarget`
with the drive's `Target` passed as the keyword argument `until`, which the
worker hands to `steps(until)`; while a claim is held it waits on that one.

```python
import hashlib
import json
import math

import runstate


def run_id(inputs: dict) -> str:  # the cache key: the inputs, never the target
    canon = json.dumps(inputs, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def train(channel: runstate.Channel, *, until: runstate.Target, lr: float) -> None:
    # This loss is a pure function of the index, so the worker holds no state
    # and continues the head's lineage (the checkpoint recipe is for state).
    with runstate.Worker(
        channel,
        resume_from=runstate.LatestClaimHead(),
        sources=[],
        save=runstate.NoSave(),
    ) as w:
        for step in w.steps(until):  # `until` is the drive's Target
            w.emit("loss", 5.0 * math.exp(-lr * step))
    # no completed claim: a preempted exit at the target, the next drive extends


launcher = runstate.ThreadLauncher()
inputs = {"lr": 0.3}
target = runstate.ThreadTarget(train, args=(), kwargs=inputs)
producer = runstate.launch_producer(launcher, run_id(inputs), target)


def claims() -> int:
    return len(producer.channel.read(topics=[runstate.Topic.LIFECYCLE_STARTED]))


def losses(n: int) -> list[runstate.Element]:
    r = runstate.ensure(producer, runstate.Target("loss", n))
    if not isinstance(r, runstate.Reached) or r.node is None:
        raise SystemExit(f"loss[:{n}] is out of reach: {r}")
    return runstate.series(producer.channel, "loss", head=runstate.AtNode(r.node))


assert len(losses(8)) == 8 and claims() == 1  # cold: one launch, 0..7
assert len(losses(8)) == 8 and claims() == 1  # warm: served from the log
assert len(losses(20)) == 20 and claims() == 2  # extend: one lineage, 8..19
```

`examples/reuse/` is the same with checkpoints (section 4). A `Command` is not
`Drivable` (a subprocess has no defined way to receive a `Target`), so a
subprocess producer is your own object of the same shape:
`examples/redrive/driver.py` passes the target as `Target.atom()` JSON in the
environment, and its worker reads it back with `Target.from_atom`.

**`ensure` auto-continues a clean `preempted` stop, but fails fast on a
death.** Its failure exits are per drive:

- **`RunFailedError(result)`** when the drive's claim ends with an outcome in
  `Outcome.failures()`. The retry decision is yours: `examples/redrive/` is the
  caller's loop, retrying a death the worker did not self-diagnose
  (`result.error is None`);
- **`NoProgressError`** when the drive's claim ends having written no commit;
- **`RecordlessExitError`** when the drive's process died with no claim naming
  its launch (a startup crash before the claim);
- a `Target` naming a source the drive's claim declares raises `ValueError`.

While the drive's claim is held, `ensure` waits, whatever its handle says, and
calls `report_if_dead` on each poll.

## 9. Claims and lineages: one run_id, many claims

Because a log outlives any single worker, a few rules keep consumers correct:

1. **Claim-scoped state is read from the *latest* claim, never the first.** The
   live handle, the current status, "which claim is this" — all come from the
   newest `lifecycle.started`. `latest_episode` and `live_episode` own that
   rule; don't re-derive it.
2. **A verdict stands only until a new claim.** `peek_terminal` reads the
   newest claim's, so a resumed run is not "still done" because its previous
   claim ended cleanly. A `Watcher` holding a launch's handle reads *that
   launch's* claim, so a relaunch never shows the previous claim's verdict.
3. **A branch is never spliced.** A displaced worker, a rewind or a resume from
   an older checkpoint commits on its own branch; every read follows one
   lineage from its head, and the other branches stay on the log, readable at
   their own nodes.
4. **A claim ends by record, not by probe** (section 6). If you watch runs and
   want a probe's finding recorded, call `report_if_dead`.

Launch-on-demand, relaunch-to-extend and reconnect all fall out of this. A
service worker (`examples/monitor/`) takes it furthest: many claims on one run,
each woken by fresh demand.

## 10. Eight pitfalls (with one-line fixes)

1. **A value emitted after the commit lands in the next node.** With a
   checkpoint saved in between, a crash loses it. *Fix:* emit inside the
   iteration, before its `commit()` (or inside the `steps()` body, which commits
   after it).

2. **A per-chunk `completed` claim closes the lineage.** A chunked worker that
   claims `completed` at each chunk's target makes every later `ensure` past it
   `Unreachable`. *Fix:* a chunk leaves the block without the claim (a
   preempted, extendable exit); claim `completed` only at intrinsic, permanent
   completion.

   ```python
   import runstate


   def chunk(channel: runstate.Channel, *, until: runstate.Target) -> None:
       with runstate.Worker(
           channel,
           resume_from=runstate.LatestClaimHead(),
           sources=[],
           save=runstate.NoSave(),
       ) as w:
           for step in w.steps(until):
               w.emit("loss", 1.0 / (step + 1))
           w.stopped(completed=True, error=None)  # WRONG for a chunk


   launcher = runstate.ThreadLauncher()
   target = runstate.ThreadTarget(chunk, args=(), kwargs={})
   producer = runstate.launch_producer(launcher, "chunked", target)
   five, ten = runstate.Target("loss", 5), runstate.Target("loss", 10)
   assert isinstance(runstate.ensure(producer, five), runstate.Reached)
   assert isinstance(runstate.ensure(producer, ten), runstate.Unreachable)
   ```

3. **A time or count atom makes a subscription a lease, which dies with its
   claim.** After a crash it is void for every later claim, re-sends of its id
   included. *Fix:* spell a bound meant to outlive workers in lineage counts
   (`{"until": {"commits": N}}`); after a crash, resubscribe under a fresh id.

4. **A cross-run broadcast barrier must be lineage-keyed.**
   `Watcher.broadcast(name, schedule)` fans one subscription across tracked
   runs; a time-keyed barrier on a run that relaunches is void for the new
   claim (pitfall 3). *Fix:* key it on `commits` or a stream count.

5. **Checkpoint through the save policy, not by hand.** A checkpoint written
   before its node lands, or after the loop with the *target* in it, names work
   that never committed. *Fix:* `save=Every(k, fn)`, choose before claiming,
   load after, and assert `loaded node == w.resume_node` (section 4).

6. **A target or progress atom naming a source counts 0 forever.** *Fix:* aim
   `steps(until)`, `ensure` and a schedule's atoms at an eager stream or
   `Commits()`; the Worker raises `ValueError` on such a target, and refuses
   such an atom `unsatisfiable`.

7. **A subscription that can never fire pins a `serve()` worker.** One on a
   source that is never `set`, or a `{"stream": ...}` atom naming a stream that
   is never emitted (a misspelling), keeps demand live forever. *Fix:* bound it
   with a time `until`, which makes it a lease that dies with its claim.

8. **Progress is a count, not a label.** "Has it reached step `s`?" on 0.3.0's
   0-based step is `prefix_len(...) > s`, and "does it hold n?" is
   `prefix_len(...) >= n`. *Fix:* think in counts: empty is 0, and a target `n`
   is reached at a count of at least `n`.

## Coming from format 0.3.0

[`specs/commits-and-lineage.md`](specs/commits-and-lineage.md) §8 is the full
upgrade list, call site by call site, and its §7 is the migration step that
moves a 0.3.0 log forward under `runstate migrate`
([`specs/log-formats.md`](specs/log-formats.md) §8). In brief:

- **`tick(step)` is gone**: `commit()`, with no argument, ends an iteration, and
  `heartbeat()` is liveness only. `steps(total=, start=)` is `steps(until)`,
  with a `Target`; the log holds the position.
- **`Worker(channel)`** takes `resume_from=`, `sources=` and `save=`, all
  required. A name you `set` must be declared a source; a name `ensure` counts
  is `emit`ted.
- **The step atom `{"step": N}` is gone**: use `{"commits": N}` or
  `{"stream": name, "n": N}`. Values carry no `step`, and the
  `lifecycle.stopped` field `final_step` is gone (`RunResult.node` names the
  exit node).
- **`control.unsubscribe` is retired**: an asker withdraws with
  `control.withdraw`; an expiry is a node's `expired`. **`launcher.*`** is
  **`supervisor.*`**, which adds the death report `supervisor.died`.
- **`progress` and `value_series` are removed**: use `prefix_len`, and
  `streams` with `series` or `aligned`. Lineage reads take `head=`.
- **`RunResult.outcome` is `outcomes`** (a set) and `reason` is `tier`.
- **A launch takes its launcher's target record**: `ThreadTarget` or `Command`.
  `launch_producer(launcher, run_id, target)` replaces `launch_producer(launcher,
  variant)`. **`sweep` and `Variant` are deleted**
  ([`dead_ends/sweep.md`](dead_ends/sweep.md)).
- **`ensure(producer, name, until={"step": N})`** is
  `ensure(producer, Target(name, N))`, returning `Reached` or `Unreachable`.
- **A stranded claim is released by `report_death`**, never by a forged
  `stopped`.

## Where to go next

- **Runnable examples:** `examples/minimal/` (spawn + subscribe + watch + stop),
  `examples/reuse/` (reuse + extend via `ensure`, with checkpoints),
  `examples/redrive/` (killed-then-resume), `examples/monitor/` (the on-demand
  service worker), `examples/submitit/` (a SLURM/local launcher recipe).
- **The public surface:** [`api.md`](api.md).
- **The full model:** [`overview.md`](overview.md), then
  [`specs/commits-and-lineage.md`](specs/commits-and-lineage.md) and
  [`design-v0.2.md`](design-v0.2.md).
- **The wire format, for another language:**
  [`implementers-guide.md`](implementers-guide.md).
