# Integrate your training loop in 15 minutes

A task-oriented guide for someone who has a training loop (or a simulation, or
any long-running job) and wants an orchestrator to **watch it and steer it while
it runs** — subscribe to its metrics, stop it early, resume it from a
checkpoint, reuse a finished run's results.

This is the how-to. For *why* the system is shaped this way, read
[`overview.md`](overview.md) (the guided tour) and [`design-v0.2.md`](design-v0.2.md)
(the rationale). For the exact public surface, [`api.md`](api.md).

*Derived, not authoritative: on any disagreement the schemas +
`design-v0.2.md` win. Every code block here is either lifted from a runnable
`examples/` program (named inline) or executed during authoring.*

---

## 1. Install

```bash
pip install runstate            # Python >= 3.11, stdlib-only core
pip install "runstate[postgres]"  # + the cross-host Postgres backend
```

The library is pure-Python and has no required dependencies. The examples below
run against the default SQLite backend (a single file per run) or an in-process
Memory backend (tests / single-process orchestration).

## 2. Wrap your loop (the worker side)

Your training script becomes a **worker**. Three lines wrap an existing loop:

- `runstate.attach()` opens this run's channel (it reads the `RUNSTATE_*` env
  vars a launcher set — see §3);
- `with runstate.Worker(channel) as w:` drains control requests, services
  subscriptions, and emits the lifecycle beacons for you;
- inside the loop, `w.steps(total=N)` yields each step and, after your body,
  reports progress and checks for a commanded stop.

This is `examples/minimal/worker.py` verbatim:

```python
import math

import runstate


def main():
    channel = runstate.attach()  # reads RUNSTATE_* set by the launcher
    with runstate.Worker(channel) as w:
        for step in w.steps(total=50):
            loss = max(0.01, 5.0 * (0.97**step) + math.sin(step * 0.2) * 0.1)
            w.set("loss", loss)  # report the current value
        w.stopped(completed=True)  # finished the budget -> claim completion


if __name__ == "__main__":
    main()
```

### `set` vs `emit` — who owns the cadence

Two ways to report a value; they differ in **who decides how often a point hits
the log**:

- **`w.set(name, value)`** — update the worker's *current value* for `name`. A
  point lands on the log only when a subscription fires (observer-chosen
  cadence). No subscribers ⟹ nothing logged. Use this for a live dashboard
  metric an orchestrator samples on its own schedule.
- **`w.emit(name, value)`** — log a point *now*, unconditionally (worker-chosen
  cadence). This is the series `ensure`/`history` read (the log-as-cache plane).
  Use it for the training curve you always want recorded.

Rule of thumb: `emit` the curve you'd checkpoint against; `set` the gauges an
observer may or may not watch.

### Completion is opt-in; the default is resumable

`w.stopped(completed=True)` is the worker's claim that the run is *intrinsically,
permanently* done. If you just fall off the `with` block without that claim, the
worker still emits a clean `lifecycle.stopped`, but its `outcome` reads
**`preempted`** — a clean, *resumable* stop. A resumable or chunked worker (§5)
deliberately never claims `completed`.

## 3. Orchestrate: spawn, subscribe, watch

Nothing spawns your worker for you — runstate ships **no `Orchestrator` class**.
You write a small script that composes a launcher, a subscription, and a
watcher. The reference `LocalLauncher` spawns the worker as a subprocess and
injects the `RUNSTATE_*` env that `attach()` reads.

The skeleton (the full runnable version is `examples/minimal/driver.py`):

```python
import runstate

with runstate.LocalLauncher(root="/tmp/runs") as launcher:
    ch = launcher.open_channel("run-1")
    # Subscribe BEFORE launch so the worker picks it up on its first tick:
    ch.send(
        {"every": {"step": 1}},  # the schedule (condition-algebra, below)
        topic=runstate.Topic.CONTROL_SUBSCRIBE,
        name="loss",
        request_id="me",
    )
    handle = launcher.launch("run-1", ["python", "worker.py"])

    watcher = runstate.Watcher()
    watcher.add(handle)
    result = watcher.wait("run-1", on_event=lambda rid, e: print(e.topic, e.body))
    print(result.outcome)  # an Outcome: completed / preempted / errored / killed / presumed_dead
```

### The subscription schedule (the condition-algebra)

A subscription body is a small algebra over the worker's coordinates
`(step, time_seconds, count)`. Each of `from` / `every` / `until` is a
`Condition` — a threshold `{"step": N}` / `{"time_seconds": S}` / `{"count": C}`,
or `{"any": [...]}` (first to cross) / `{"all": [...]}` (last to cross):

| body | meaning |
|---|---|
| `{}` | once, now |
| `{"from": {"step": 100}}` | once, at step 100 |
| `{"every": {"step": 1}}` | every step, forever |
| `{"every": {"step": 10}, "until": {"step": 5000}}` | every 10th step to 5000 |

A one-shot omits `every`. `until` may carry a `count` (expire after N fires);
`count` is refused anywhere else. `control.stop` uses the same algebra with only
a `from` (default: stop now).

### The terminal verdict

`Watcher.wait` blocks until the run is terminal and returns a `RunResult`: a
**closed** `outcome` (an `Outcome` StrEnum) plus a verbatim `reason`. There is no
`success` boolean — whether a clean `preempted` "succeeded" is a policy you own,
not something the worker bakes in. The five outcomes:

- `completed` — the worker claimed intrinsic completion;
- `preempted` — a clean, resumable stop (the default, or a commanded stop);
- `errored` — the worker self-diagnosed a fatal error;
- `killed` — a reaped non-zero/ signal death;
- `presumed_dead` — the heartbeat went stale (the Watcher's inference tier).

### Steer it: stop early on divergence

Inside `Watcher.wait`'s `on_event` callback, send a cooperative `control.stop`
when you've seen enough. The worker stops cleanly at its next tick
(`outcome → preempted`) — this is `examples/minimal/driver.py`'s divergence
preempt, and `examples/redrive/` shows the killed-then-resume caller pattern.

Spawn however you like — the launcher is opt-in. To run on SLURM (or AWS Batch,
or locally) via submitit, `examples/submitit/` is a bring-your-own-launcher
recipe that talks the same protocol; the eventual first-class adapter is
sketched in [`backlog/submitit-launcher.md`](backlog/submitit-launcher.md).

## 4. Inspect and steer from the terminal

The `runstate` console script (installed with the package) reads a run's SQLite
log directly — no daemon, no server:

```bash
runstate status /tmp/runs                 # snapshot table: run_id, verdict, progress, age
runstate stop /tmp/runs run-1             # send a cooperative control.stop
runstate stop /tmp/runs run-1 --wait 30   # ... and block up to 30s for it to be answered
```

`status` discovers both the flat (`<root>/<rid>.db`) and content-addressed
sharded (`<root>/runs/<xx>/<rid>/<rid>.db`) layouts; its `age` column is the
freshness clock (time since the run last did anything). `stop` warns if the run
is down — the stop is then *armed* for the next episode (§6) and honored exactly
once when a worker next attaches. It is SQLite-only and deliberately not a live
viewer.

## 5. Resume and extend

A `run_id` names a **durable log**, not a process. A worker episode can end and a
later episode can attach to the *same* log and resume. Three pieces make resume
work:

### Checkpoint the frontier, resume with `steps(start=)`

Pass `start=k` to resume at step `k`; steps then emit as `k, k+1, …`
(run-absolute) and `lifecycle.stopped` records the correct `final_step`. The
checkpoint records **the frontier — the work actually done — never the target**.
This is the load-bearing half of `examples/reuse/`'s resumable cell:

```python
ckpt = Path(ckpt_dir) / f"{run_id}.json"
start = json.loads(ckpt.read_text())["next"] if ckpt.exists() else 0
with runstate.Worker(channel) as w:
    for step in w.steps(start=start, total=up_to):
        w.set("loss", 5.0 * math.exp(-lr * step))
        ckpt.write_text(json.dumps({"next": step + 1}))  # this step is done
```

Why the frontier and not the target: a cooperative `control.stop` can cut the
loop short. A checkpoint written *after* the loop as `{"next": up_to}` would
claim work that never happened — the next episode resumes past the gap, does
nothing, and a consumer raises `NoProgressError`.

### Reuse by content-addressed `run_id`

Derive the `run_id` from the inputs, so re-asking for the same computation is
free and asking for *more* resumes the same series (the `run_id` recipe,
`specs/run-id-recipe.md`):

```python
def run_id(inputs: dict) -> str:
    canon = json.dumps(inputs, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]
```

Crucially, the `run_id` **excludes the step target** — the target is the axis you
extend along, so `lr=0.3` maps to one log whether you ask for 8 steps or 20.

### `ensure` — read-first, produce-on-miss

`ensure(producer, name, until={"step": N})` serves the logged prefix when the run
already reached `N` (a cache hit, no worker), else relaunches-to-extend and
waits. From `examples/reuse/`:

```python
runstate.ensure(producer, "loss", until={"step": 8})    # cold: produce 0..7
runstate.ensure(producer, "loss", until={"step": 8})    # warm: served from the log
runstate.ensure(producer, "loss", until={"step": 20})   # extend: resume 8..19, one series
```

`ensure` auto-continues a clean `preempted` stop, but **fails fast on a death**
(`RunFailedError`) — the retry decision is yours (`examples/redrive/` is the
caller pattern). A `producer` is any object exposing `.channel` / `.run_id` /
`.extend(until)`; `launch_producer(launcher, variant)` builds one for a
callable-worker launcher, and `examples/redrive/` shows the subprocess seam.

## 6. The episode rule (one run_id, many episodes)

Because a log outlives any single worker, two rules keep consumers correct:

1. **Episode-scoped state is read from the *latest* `lifecycle.started`, never
   the first.** The live handle/pid, the current status, "which episode is this"
   — all come from the latest episode. `runstate.latest_episode` /
   `live_episode` own that rule; don't re-derive it.
2. **A terminal verdict stands only until a new episode claims.**
   `peek_terminal` is episode-aware: a resumed run is not "still done" just
   because the previous episode ended cleanly.

Launch-on-demand, relaunch-to-extend, and reconnect all fall out of this. A
service worker (`examples/monitor/`) takes it furthest: it runs while leased
demand exists (`w.serve()`), retires when demand drains, and re-wakes on fresh
demand — many episodes on one run.

## 7. Five pitfalls (with one-line fixes)

1. **`w.emit(...)` before the loop's first step raises `ValueError`.** A
   `step=None` point would permanently poison `history()` for that name (the log
   is append-only). *Fix:* emit *inside* the `steps()` loop (where a step
   exists); for genuinely stepless points use `channel.send` directly. (This
   also fires on a stepless `serve()` worker — services use `set`, not `emit`.)

2. **A per-chunk `completed` claim truncates `ensure`.** `ensure` reads
   `completed=True` as done-done and returns the truncated series. *Fix:* a
   resumable/chunked worker stops `preempted` (leave the claim unset) on every
   chunk; claim `completed` only at intrinsic, permanent completion.

3. **A time-referencing subscription (`{"time_seconds": S}`) is
   episode-scoped.** Its countdown can't honestly outlive the worker that was
   counting, so the next episode's `started` voids it. *Fix:* spell any bound
   meant to *outlive workers* in steps (`{"until": {"step": N}}`), not seconds.

4. **A cross-run broadcast barrier must be step-keyed.**
   `Watcher.broadcast(name, schedule)` fans one subscription across tracked runs;
   a time-keyed barrier on a run that *resumes* is boundary-voided (pitfall 3).
   *Fix:* key broadcast barriers on `step`, not time.

5. **Checkpoint what you *did*, not what you were *asked* to do.** Writing the
   target (`{"next": up_to}`) after the loop claims work a commanded stop may
   have skipped. *Fix:* write the frontier (`{"next": step + 1}`) *inside* the
   loop, after each step's work is durable (§5).

## Where to go next

- **Runnable examples:** `examples/minimal/` (spawn + subscribe + watch + stop),
  `examples/reuse/` (reuse + extend via `ensure`), `examples/redrive/`
  (killed-then-resume), `examples/monitor/` (the on-demand service worker),
  `examples/submitit/` (a SLURM/local launcher recipe).
- **The public surface:** [`api.md`](api.md).
- **The full model:** [`overview.md`](overview.md), then
  [`design-v0.2.md`](design-v0.2.md).
