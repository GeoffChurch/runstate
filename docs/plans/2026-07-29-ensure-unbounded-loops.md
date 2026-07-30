# `ensure`: make the caller's seam total — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ensure` publishes a `sleep` callback, but in some failure shapes the drive loop re-enters without ever passing through it — so a caller cannot interrupt the loop at all, however well written. Make every outer pass reach the seam, and correct one error message that names the wrong cause. **No new parameter, no new exception type, no Protocol change.**

**Architecture:** Two changes inside `runstate/memoizer.py`'s `ensure`. Nothing else moves. The bound itself is deliberately **not** in this plan — see *The decision this plan does not make*.

**Tech stack:** Python 3.11+, stdlib; pytest over memory + sqlite + (skipped) postgres.

## Global Constraints

- Public API only — no `sqlite3`, no `?mode=ro`, no `_`-prefixed functions from outside their module.
- **Gates:** `uv sync --extra test` first, then `uv run pytest -q`; `uv run black --check runstate/ tests/`; `uv run mypy --strict runstate`. **The formatter is `black`, not `ruff`** (`[tool.black]` in `pyproject.toml`; CI runs `black --check runstate/ tests/`). `ruff` has no config here and reports pre-existing drift on master; do not "fix" it.
- Without `uv sync --extra test`, `uv run pytest` falls through to a PATH `pytest` whose interpreter carries an editable `runstate` — it will silently test the installed tree instead of yours.
- No back-compat shim. Migrate and delete the old path.
- Any asserted cost is measured, not reasoned from the shape of the call.

---

## The evidence

The headline shape is a **recordless handle**: `extend` returns a handle that is already dead and wrote nothing. It is deterministic and backend-independent.

| shape | `until` | master | with Task 1 |
|---|---|---|---|
| **recordless handle** | `{step}` | **96,931 extends, 0 sleeps** (still looping) | 297 extends, 296 sleeps |
| **R5**, `+1` step per relaunch (`specs/control-target.md`) | `{step}` | 3,033 extends, 0 sleeps | bounded, sleeps every pass |
| own spawn dies pre-attach, resumed log | `any[step,time]` | returns `[0,1,2]`, no error | unchanged (see Task 2) |
| foreign-host claim | `{step}` | hangs, but **already reaches the seam** | unchanged |

**Do not use the thread-spawn variant as the headline.** Its 0-sleep behaviour is a GIL-scheduling artifact: on ThreadLauncher + memory it yields 0 sleeps, but the same shape yields **34 sleeps on sqlite** and **42 under `LocalLauncher`**. A test pinned to it is green on master 100% of the time under sqlite — a permanent false green the moment it is backend-parametrized, which is this repo's habit.

## What the one line is, and what it is not

It is **not** the root cause. The same spawn on a *cold* log raises `RunFailedError` immediately; on a *resumed* log it loops. The difference: the dead spawn never claimed, so its `launcher.terminated` correlates to no episode and is dropped, and `_episode_stopped` returns the *previous* episode's `stopped`. `ensure` cannot see that its own spawn died.

That blind spot is the real defect, and it is the same family as **runstate#30** (a launcher-plane exit code standing in for the worker's verdict). Both are recorded; neither is fixed here.

What this plan fixes is narrower and worth fixing on its own: **the seam the library publishes must be total.** Today a caller who writes a perfect guard still cannot bound these shapes, because control never reaches their code.

---

## Task 1: Every outer pass must reach the `sleep` seam

**Files:**
- Modify: `runstate/memoizer.py` (`ensure`'s inner poll loop, and the end of the outer `while` body)
- Test: `tests/test_memoizer.py`

**Interfaces:** No signature change. `sleep` keeps its type `Callable[[float], None]`.

- [ ] **Step 1: Write the failing test** — the recordless shape, deterministic on every backend.

```python
def test_ensure_outer_loop_always_reaches_the_sleep_seam():
    # A producer whose extend returns an ALREADY-DEAD handle that wrote nothing.
    # On master the inner poll loop breaks before its first sleep and the outer
    # loop re-enters at CPU speed, so the caller's seam is never reached and no
    # caller-side guard can bound this. Deterministic: no thread, no GIL race.
    class DeadHandle:
        def is_alive(self): return False
        def wait(self): return None

    launcher = runstate.ThreadLauncher()
    ch = launcher.create_channel("recordless")
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="o")
    ch.send({"handle": local_handle(), "t": time.time()}, topic="lifecycle.started")

    calls = {"extend": 0, "sleep": 0}

    class Recordless:
        run_id = "recordless"

        @property
        def channel(self):
            return ch

        def extend(self, until):
            calls["extend"] += 1
            if calls["extend"] > 500:
                raise AssertionError("unbounded: seam never reached")
            return DeadHandle()

    class Stop(Exception):
        pass

    def sleep(_):
        calls["sleep"] += 1
        raise Stop  # the caller's bound -- one pass proves the seam is reachable

    with pytest.raises(Stop):
        ensure(Recordless(), "loss", until={"step": 50}, sleep=sleep)
    assert calls["sleep"] == 1
```

- [ ] **Step 2: Run it.** `python -m pytest tests/test_memoizer.py -k always_reaches -q`
      Expect FAIL: `AssertionError: unbounded: seam never reached`, with `calls["sleep"] == 0`.
      Use `python -m pytest` from the repo root — `sys.path[0]` is then your tree.

- [ ] **Step 3: Implement.** Count the inner loop's polls, and yield only when it made none:

```python
        inner_polls = 0
        while not _satisfied(channel, until, clock=clock):
            if not handle.is_alive():
                handle.wait()
                break
            sleep(poll_interval)
            inner_polls += 1
        else:
            return history(channel, name, dense)
```

and at the end of the outer `while` body, after the no-progress guard:

```python
            raise NoProgressError(
                producer.run_id, progress=progress(channel), until=until
            )
        if inner_polls == 0:
            sleep(poll_interval)  # every outer pass reaches the seam at least once
    return history(channel, name, dense)
```

- [ ] **Step 4: Confirm the two rejected variants stay rejected.** Both were measured; put the numbers in the commit message.
  - **Unconditional** `sleep(poll_interval)`: bounds the shapes, but costs **+84–88%** on the chunked re-drive path at 100 chunks (1147 → 2115 ms memory; 1301 → 2450 ms sqlite). It near-doubles `ensure`'s primary path.
  - **Conditional on progress** (`if _progress(channel) <= before`): free on the healthy path, but **blind on R5** — every relaunch advances progress, so it never fires (measured 2,936 extends / 0 sleeps, indistinguishable from master).
  - The variant above is free on the healthy path **and** bounds R5, because a real chunk's worker is alive for at least one poll.
- [ ] **Step 5: Do not measure the healthy path with `test_ensure_redrives_within_one_call_to_reach_target`.** It performs 2 re-drives; the delta is ~20 ms and reads as noise, which would license shipping the unconditional variant without seeing the 88%. Use a ≥100-chunk producer.
- [ ] **Step 6: Gates.** Expect `769 passed, 213 skipped` (with `--extra test` installed and no PG DSN; without psycopg it is 768/201). `black --check` and `mypy --strict` clean.
- [ ] **Step 7: Commit.** `fix(memoizer): every outer pass must reach the caller's sleep seam`

## Task 2: Name a spawn that wrote nothing — scoped to its own episode

`NoProgressError(progress=2)` reports the *previous* episode's frontier when this episode's spawn died before writing. The message should distinguish "this spawn wrote nothing" from "this spawn ran and stalled".

**A plane-based watermark is not sufficient and must not be used.** Keying on "did any worker-plane record land" reports the wrong cause as soon as a spawn *claims* and then dies cleanly at zero progress, because its own `lifecycle.started` satisfies the watermark. Scope the evidence to **this episode** — records after `latest_episode(channel).seq`, attributed by launch id (`vocabulary/launch.py`, already stamped on both `launcher.*` records and re-emitted on the worker's `lifecycle.started`).

**Files:**
- Modify: `runstate/memoizer.py`, `docs/api.md`
- Test: `tests/test_memoizer.py`

**Interfaces:** `NoProgressError` keeps its type, so the documented catchers keep working. `docs/api.md` pins the constructor `NoProgressError(run_id, *, progress, until)` and `tests/test_public_api.py` enforces api.md coverage — **if the constructor gains a parameter, api.md changes in the same commit.**

- [ ] **Step 1: Write three failing tests**, not one. The third is the one a plane-based watermark fails:
  1. spawn dies pre-attach → message names "wrote no records";
  2. spawn attaches, then exits clean at zero progress → must **not** claim it wrote nothing;
  3. our spawn writes nothing while a third party lands a heartbeat at the same frontier → must not be credited to our spawn.
- [ ] **Step 2: Run them.** Expect all three to report `made no progress toward {...} (progress=2)`.
- [ ] **Step 3: Implement**, scoping the evidence window to the current episode.
- [ ] **Step 4: Verify** the cold-log path still raises `RunFailedError`
      (`test_ensure_surfaces_a_die_before_attach_without_hanging`), plus the full suite.
- [ ] **Step 5: Commit.** `fix(memoizer): name a spawn that wrote nothing, scoped to its own episode`

## Task 3: Say that `ensure` has no bound

`docs/api.md` documents `ensure` with no mention of the unbounded wait; `memoizer.py`'s "No hang timeout (unchanged)" is the only statement of it anywhere.

**Files:** Modify `docs/api.md` and `ensure`'s docstring.

- [ ] **Step 1: State the fact** — `ensure` can wait indefinitely on a live-looking claim, and `sleep` is a **test seam**, not the intended production bound. Point at the open decision below.
- [ ] **Step 2: Do not document a give-up recipe built on `sleep`.** Overloading it with policy means a test that injects a fake sleep also disables the production bound, and it would put a library invariant — which topics `ensure`'s own `extend` writes — into user code.
- [ ] **Step 3: Commit.** `docs: ensure can wait indefinitely, and sleep is not the bound`

## Task 4: Record what was rejected, and what is open

**Files:** Modify `docs/backlog/ensure-redrive-recoverable-terminations.md`.

- [ ] **Step 1: Record both rejected bounds**, with the reason that actually killed them:
  - **One knob cannot serve both loops.** The spawn storm needs a *count* bound (each iteration costs a subprocess); the hang needs a *duration* bound. A duration safe against the worst legitimate silence is orders of magnitude too large to contain a storm — validating a guard at 0.42 s while instructing callers to set hours is not a bound.
  - **`max_extends`** (reset-keyed): blind by construction to R5 with the reset; false-fires on the documented chunked-producer contract without it; and false-fires on a healthy stepless worker, because the step frontier is the wrong reset key.
  - **`stall_timeout`**: *admissible* — the constraint is one-sided, unlike the refuted staleness tier, and `ensure` writes nothing, so a raise cannot admit a second writer — but rejected on the arithmetic above.
  - **The general rule**, which is why this keeps recurring: **a threshold encodes workload knowledge the protocol does not have** — and once the log is remote, topology knowledge too. Same reason the staleness tier failed, reached from a second direction.
- [ ] **Step 2: Record the open measurement.** `_elapsed` returns `0.0` forever on an epochless log, so a time target where no spawn ever claims is unsatisfiable forever. Mirroring `history`'s raise is refuted — but record the correct blast radius: raising *unconditionally* breaks 16 tests, while a **faithful** gate (raise only when the schedule references time) breaks **3**. Implementation trap: `references_time` is subscription-shaped and walks `from`/`every`/`until`, so `references_time(until)` returns False for a bare `{"time_seconds": …}`; it must be called as `references_time({"until": until})`. Getting this wrong is a silent no-op that passes the suite.
- [ ] **Step 3: Commit.** `docs(backlog): the bounds that were rejected, and why`

---

## The decision this plan does not make

Whether `ensure` should take `timeout=`. Deliberately out of scope — but the evidence gathered here bears on it and should not be lost:

- **This repo's doctrine is that the library ships the mechanism and the caller supplies the number.** `await_consumed` is `ensure`'s structural twin — a free function that polls a channel until a condition — and it ships `timeout=None` *alongside* `poll_interval`, `now`, and `sleep`, raising `TimeoutError`. `Watcher.wait`, `wait_all`, and `iter_events` do the same, and `heartbeat_timeout` is an optional `None` knob whose own docstring says the threshold is per-workload. `ensure` is the only blocking observer entry point without one.
- **The vulnerable caller is the casual one.** A downstream consumer's sophisticated dispatch loop already handles this correctly by hand. Its *other* call site is a single unguarded line in an interactive tool whose producer waits on a foreign episode — exactly the hang shape, with no handler and no bound. A hand-written drive loop will never appear there; `timeout=` would be free.
- **A declarative bound survives distance; a caller-written loop does not.** `satisfied` is a pure function over an extracted coordinate vector, and the condition algebra is JSON. "Wake me when this holds, or after T" is a request a remote store can answer in one round trip — and `references_time` already reports, statically, whether a condition can be answered away from the observer. A caller-side loop forecloses that permanently, because the predicate is evaluated client-side by construction.
- **The alternative is already specified.** `docs/backlog/ensure-redrive-recoverable-terminations.md` defers a public `extend_once` + channel-bound `satisfied` + own-loop recipe "until a consumer needs to compose its own drive loop". That gate has now fired. It expresses the count and duration bounds separately — which the arithmetic above says is mandatory — and hands the caller `Watcher.wait(timeout=)`, whose stronger detector tiers are what actually answer the cross-host hang that `resolve()` abstains on.

## Method note for whoever implements this

Import shadowing produced a false green in several independent reviews of this area, including two of mine. The mechanism is **not** that the editable install beats `PYTHONPATH` — the editable finder *appends* to `sys.meta_path`, so `PYTHONPATH` and cwd win. The real cause is `sys.path[0]`: running `python /tmp/probe.py` puts **`/tmp`** on the path, not your copy, so the editable install answers the import. Run prototypes from the copy's own working directory via `python -` (stdin) or `python -m`, and **assert `runstate.__file__` inside the process before trusting any figure it prints.** Every number in this plan came from a run that asserted it.
