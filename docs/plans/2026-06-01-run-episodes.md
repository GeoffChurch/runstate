# Run-Episodes Implementation Plan (scoped)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a `run_id` host multiple resumable *episodes*, so a run can be relaunched-to-extend without double-spawning — the primitive the fine-grained memoizer needs.

**Architecture:** Episodes are *implicit* (a `lifecycle.started…stopped` span by `seq`); no schema change. Adds: a CAS `send(expected_seq=)` substrate primitive; episode-aware `peek_terminal`; `Worker.steps(start=)`; and the **worker self-claims its episode** (CAS `lifecycle.started`; a loser exits before acting) — so the single-spawn guard lives in the protocol, not the launcher. Autonomous-extend (relaunch with a higher target; the worker resumes from a `run_id`-keyed checkpoint and continues the run-absolute step) is a convention riding on those.

**Tech stack:** Python 3.11+, stdlib only (`sqlite3`, `os`, `socket`, `dataclasses`); pytest. Tests run over both channel backends via the `ch`/`open_channel` fixtures in `tests/conftest.py`.

**Spec:** `docs/specs/run-episodes.md`. **Out of scope:** the service/lifeline policy; the memoizer itself; the worker's checkpoint mechanism.

---

## File structure

- `runstate/channel/memory.py` — `MemoryChannel.send`: add `expected_seq` (CAS under the existing lock).
- `runstate/channel/sqlite.py` — `SqliteChannel.send`: add `expected_seq` (CAS in a transaction).
- `runstate/liveness.py` — `peek_terminal`: episode-aware (`stopped`/`terminated` terminal iff no `started`/`launched` follows by `seq`); plus `live_episode(channel)` (latest `started` with no following `stopped`, worker resolves alive — the claim's live-check).
- `runstate/worker.py` — `steps(start=0, total=None)`; and **self-claim the episode** at attach (CAS `lifecycle.started`; `self._lost` short-circuits `steps`/`__exit__` on a loss).
- `runstate/vocabulary/handle.py` — `resolve(handle: str) -> bool | None` (probe a `local://host/pid` token via `os.kill(pid, 0)`; `None` if not locally resolvable).
- `runstate/launcher.py` — **unchanged this pass.** The guard is the worker's self-claim, so the launcher just spawns. A best-effort launch pre-check (skip the spawn if a live episode is visible) and `LocalLauncher` idempotent relaunch are **deferred** (spec §3 / non-goals); `ThreadLauncher` losers are cheap threads, so the memoizer doesn't need the pre-check.
- Tests: `tests/test_channel.py`, `tests/test_liveness.py`, `tests/test_worker.py`, `tests/test_handle.py` (new), `tests/test_run_episodes.py` (new, integration).

The Watcher needs **no** change: its terminal tiers call `peek_terminal`, so they inherit episode-awareness.

---

## Task 1: CAS `send(expected_seq=)` on both backends

**Files:**
- Modify: `runstate/channel/memory.py` (`MemoryChannel.send`)
- Modify: `runstate/channel/sqlite.py` (`SqliteChannel.send`)
- Test: `tests/test_channel.py`

- [ ] **Step 1: Write the failing test** (parametrized over both backends via the `ch` fixture)

```python
def test_send_expected_seq_appends_on_match_rejects_on_mismatch(ch):
    s1 = ch.send({"value": 1, "step": 0, "t": 0.0}, topic="value", name="loss")
    # CAS with the correct last seq -> appends, returns the new seq
    s2 = ch.send({"value": 2, "step": 1, "t": 0.0}, topic="value", name="loss",
                 expected_seq=s1)
    assert s2 == s1 + 1
    # CAS with a stale last seq -> rejected (no append), returns None
    rejected = ch.send({"value": 3, "step": 2, "t": 0.0}, topic="value", name="loss",
                       expected_seq=s1)
    assert rejected is None
    assert [e.body["value"] for e in ch.read(topics=["value"])] == [1, 2]
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_channel.py::test_send_expected_seq_appends_on_match_rejects_on_mismatch -v`
Expected: FAIL — `send()` got an unexpected keyword argument `expected_seq`.

- [ ] **Step 3: Implement on MemoryChannel** (read `runstate/channel/memory.py` for `send`'s exact body; add the guard inside the existing lock, *before* assigning the seq/appending)

```python
def send(self, body, *, topic, name=None, request_id=None, expected_seq=None):
    with self._lock:
        if expected_seq is not None:
            last = self._log[-1].seq if self._log else 0
            if last != expected_seq:
                return None
        seq = (self._log[-1].seq if self._log else 0) + 1
        # ... existing append (Envelope(seq, topic, name, request_id, body)) ...
        return seq
```

- [ ] **Step 4: Implement on SqliteChannel** (read `runstate/channel/sqlite.py`; wrap the check + insert in one transaction so SQLite's write lock serializes concurrent writers)

```python
def send(self, body, *, topic, name=None, request_id=None, expected_seq=None):
    with self._conn:  # BEGIN/COMMIT — serializes writers on the file
        if expected_seq is not None:
            row = self._conn.execute("SELECT MAX(seq) FROM log").fetchone()
            last = row[0] or 0
            if last != expected_seq:
                return None
        # ... existing INSERT ... ; return cursor.lastrowid
```

- [ ] **Step 5: Run the full channel suite, verify green**

Run: `python -m pytest tests/test_channel.py -q`
Expected: PASS (existing tests unaffected — `expected_seq` defaults to `None`).

- [ ] **Step 6: Commit**

```bash
git add runstate/channel/memory.py runstate/channel/sqlite.py tests/test_channel.py
git commit -m "substrate: CAS send(expected_seq=) — conditional append (the §12.1 claim primitive)"
```

---

## Task 2: Episode-aware `peek_terminal`

**Files:**
- Modify: `runstate/liveness.py` (`peek_terminal`)
- Test: `tests/test_liveness.py`

- [ ] **Step 1: Write the failing test**

```python
def test_peek_terminal_is_episode_aware(open_channel):
    ch = open_channel()
    # episode 1: started ... stopped
    ch.send({"handle": "local://h/1", "hostname": None, "attached_at": 0.0}, topic="lifecycle.started")
    ch.send({"reason": "completed", "error": None, "final_step": 5}, topic="lifecycle.stopped")
    assert peek_terminal(open_channel()).outcome == "completed"   # ep1 terminal
    # episode 2 attaches -> the old stopped is no longer terminal (a started follows it)
    ch.send({"handle": "local://h/2", "hostname": None, "attached_at": 1.0}, topic="lifecycle.started")
    assert peek_terminal(open_channel()) is None                  # ep2 live
    # episode 2 stops -> terminal again, with ep2's verdict
    ch.send({"reason": "completed", "error": None, "final_step": 9}, topic="lifecycle.stopped")
    assert peek_terminal(open_channel()).final_step == 9
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_liveness.py::test_peek_terminal_is_episode_aware -v`
Expected: FAIL — after ep2's `started`, current `peek_terminal` still returns ep1's stop (returns a RunResult, not None).

- [ ] **Step 3: Make the stopped/terminated checks episode-aware**

In `peek_terminal`, guard each terminal record by "no opener follows it by seq":

```python
def _terminal_unless_followed(channel, terminal_topic, opener_topic):
    term = channel.latest(terminal_topic)
    if term is None:
        return None
    opener = channel.latest(opener_topic)
    if opener is not None and opener.seq > term.seq:
        return None   # a newer episode opened after this terminal -> live
    return term

# stopped (clean), guarded by lifecycle.started:
stopped = _terminal_unless_followed(channel, "lifecycle.stopped", "lifecycle.started")
# ... existing reason->outcome mapping on `stopped` ...
# terminated (reaped), guarded by launcher.launched:
term = _terminal_unless_followed(channel, "launcher.terminated", "launcher.launched")
# ... existing mapping on `term` ...
```

- [ ] **Step 4: Run the liveness + watcher suites, verify green**

Run: `python -m pytest tests/test_liveness.py tests/test_watcher.py -q`
Expected: PASS (single-episode behavior unchanged; Watcher inherits via `peek_terminal`).

- [ ] **Step 5: Commit**

```bash
git add runstate/liveness.py tests/test_liveness.py
git commit -m "liveness: episode-aware peek_terminal (terminal iff no opener follows by seq)"
```

---

## Task 3: `Worker.steps(start=, total=)`

**Files:**
- Modify: `runstate/worker.py` (`steps`)
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing test**

```python
def test_steps_resumes_at_start_with_run_absolute_step(open_channel):
    orch = open_channel()
    orch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="r")
    with Worker(open_channel(), now=lambda: 0.0) as w:
        for step in w.steps(start=5, total=8):
            w.set("loss", float(step))
    steps = [v.body["step"] for v in open_channel().read(topics=["value"])]
    assert steps == [5, 6, 7]                                   # run-absolute, not 0,1,2
    assert open_channel().latest("lifecycle.stopped").body["final_step"] == 7
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest "tests/test_worker.py::test_steps_resumes_at_start_with_run_absolute_step[memory]" -v`
Expected: FAIL — `steps()` got an unexpected keyword argument `start`.

- [ ] **Step 3: Add `start` to `steps`**

```python
def steps(self, total=None, *, start=0):
    step = start
    while total is None or step < total:
        self._last_step = step
        yield step
        reason = self.tick(step)
        if reason is not None:
            self._stop_reason = reason
            return
        step += 1
```

- [ ] **Step 4: Run the worker suite, verify green**

Run: `python -m pytest tests/test_worker.py -q`
Expected: PASS (default `start=0` keeps existing behavior).

- [ ] **Step 5: Commit**

```bash
git add runstate/worker.py tests/test_worker.py
git commit -m "worker: steps(start=) for run-absolute resume (with correct final_step/stop_reason)"
```

---

## Task 4: `handle.resolve()` — probe a `local://` token

**Files:**
- Modify: `runstate/vocabulary/handle.py` (read it first; it currently has `local_handle()`)
- Test: `tests/test_handle.py` (create)

- [ ] **Step 1: Write the failing test**

```python
import os
from runstate.vocabulary.handle import local_handle, resolve

def test_resolve_live_and_dead_local_handle():
    assert resolve(local_handle()) is True          # our own pid is alive
    # a pid that (almost certainly) doesn't exist
    assert resolve("local://anyhost/2147483646") is False
    assert resolve("slurm://12345") is None          # unknown scheme -> not locally resolvable
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/test_handle.py -v`
Expected: FAIL — cannot import `resolve`.

- [ ] **Step 3: Implement `resolve`** (match the format `local_handle()` produces — confirm it's `local://<host>/<pid>`)

```python
def resolve(handle: str) -> bool | None:
    """Liveness of a handle token, actor-independently. True/False for a
    `local://host/pid` (via os.kill(pid, 0)); None if the scheme isn't locally
    resolvable (caller falls back to heartbeat staleness)."""
    if not handle.startswith("local://"):
        return None
    try:
        pid = int(handle.rsplit("/", 1)[1])
    except (ValueError, IndexError):
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, not ours
```

- [ ] **Step 4: Run, verify green**

Run: `python -m pytest tests/test_handle.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runstate/vocabulary/handle.py tests/test_handle.py
git commit -m "handle: resolve(token) -> liveness via os.kill (the probe tier, string-based)"
```

---

## Task 5: Worker self-claims its episode (CAS `lifecycle.started`)

**Files:**
- Modify: `runstate/liveness.py` (add a `live_episode` helper)
- Modify: `runstate/worker.py` (claim at attach; short-circuit `steps`/`__exit__` on a loss)
- Test: `tests/test_liveness.py`, `tests/test_worker.py`

The guard lives in the worker, not the launcher: the worker has its own handle at attach (a subprocess launcher doesn't until *after* it spawns). On attach the worker CAS-claims its `lifecycle.started`; if a live episode already exists it loses and does nothing. The launcher is unchanged (it just spawns); `ThreadLauncher` losers are cheap threads. Launcher pre-check + `LocalLauncher` idempotent relaunch are deferred (spec §3 / non-goals).

- [ ] **Step 1: Add the `live_episode` helper to `runstate/liveness.py`.** First the failing test in `tests/test_liveness.py` (import `live_episode` from `runstate.liveness`, `local_handle` from `runstate.vocabulary.handle`):

```python
def test_live_episode_running_then_none_when_stopped(open_channel):
    ch = open_channel()
    assert live_episode(open_channel()) is None                      # nothing yet
    ch.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
            topic="lifecycle.started")
    assert live_episode(open_channel()) == local_handle()            # running (our pid alive)
    ch.send({"reason": "completed", "error": None, "final_step": 1}, topic="lifecycle.stopped")
    assert live_episode(open_channel()) is None                      # stopped -> not live
```

Run red (`python -m pytest "tests/test_liveness.py::test_live_episode_running_then_none_when_stopped" -q`), then implement in `liveness.py` (add `from .vocabulary.handle import resolve`):

```python
def live_episode(channel):
    """Handle of the currently-live episode, or None: the latest
    ``lifecycle.started`` with no following ``stopped`` whose worker resolves
    alive (a started-then-crashed episode resolves dead -> not live)."""
    started = channel.latest("lifecycle.started")
    if started is None:
        return None
    stopped = channel.latest("lifecycle.stopped")
    if stopped is not None and stopped.seq > started.seq:
        return None
    if resolve(started.body["handle"]) is False:
        return None
    return started.body["handle"]
```

- [ ] **Step 2: Write the failing worker test** in `tests/test_worker.py` (a second worker attaching to a channel that already has a live episode must lose — claim nothing, do nothing):

```python
def test_second_worker_loses_the_claim_and_does_no_work(open_channel):
    ch = open_channel()
    # ep1 is live: a started by *our* pid (resolves alive), no stopped
    ch.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
            topic="lifecycle.started")
    with Worker(open_channel(), now=lambda: 0.0) as w:
        assert w.claimed is False                       # lost: an episode is already live
        for step in w.steps(total=3):
            w.set("loss", float(step))                  # body must not run
    assert open_channel().read(topics=["value"]) == []  # the loser emitted no values
    assert len(open_channel().read(topics=["lifecycle.started"])) == 1   # no second started
```

Run red (`python -m pytest "tests/test_worker.py::test_second_worker_loses_the_claim_and_does_no_work" -q`) — today the Worker emits a second `started` unconditionally and runs the loop.

- [ ] **Step 3: Make the Worker CAS-claim its episode.** Read `runstate/worker.py` for where attach currently sends `lifecycle.started` (with its exact `Started(...)` fields) and replace the unconditional send with a claim loop; add `from .liveness import live_episode`:

```python
# at attach, replacing the unconditional started send:
self._lost = False
while True:
    envs = self._ch.read()
    last = envs[-1].seq if envs else 0
    if live_episode(self._ch) is not None:
        self._lost = True
        break
    if self._ch.send(asdict(Started(...)),           # reuse the existing Started(...) construction
                     topic="lifecycle.started", expected_seq=last) is not None:
        break                                          # won the claim
    # CAS failed (someone appended) -> re-read and re-check
```

Then short-circuit work and the dying breath on a loss:

```python
@property
def claimed(self) -> bool:
    return not self._lost

def steps(self, total=None, *, start=0):
    if self._lost:
        return                                         # benign no-op: another episode is live
    step = start
    # ... rest as in Task 3 ...

# in __exit__, before emitting stopped:
    if self._lost:
        return False                                   # never claimed an episode -> emit nothing
```

- [ ] **Step 4: Run liveness + worker suites, verify green**

Run: `python -m pytest tests/test_liveness.py tests/test_worker.py -q`
Expected: PASS — existing single-worker tests still win the claim and emit `started`; the new tests pass. (If a prior test reuses one channel for two attaches and now expects two `started`, fix it to the episode model.)

- [ ] **Step 5: Commit**

```bash
git add runstate/liveness.py runstate/worker.py tests/test_liveness.py tests/test_worker.py
git commit -m "worker: self-claim the episode via CAS lifecycle.started (loser exits before acting)"
```

---

## Task 6: Autonomous-extend integration

**Files:**
- Test: `tests/test_run_episodes.py` (create)

End-to-end: a resumable in-process worker runs to `k`, checkpoints (a temp file keyed by `run_id`); a relaunch with a higher target resumes (`steps(start=k)`) and extends; the log reads back as one series.

- [ ] **Step 1: Write the failing/then-passing integration test**

```python
import json, math
from pathlib import Path
import runstate

def _cell(channel, *, run_id, target, ckpt_dir):
    ckpt = Path(ckpt_dir) / f"{run_id}.json"
    start = json.loads(ckpt.read_text())["next_step"] if ckpt.exists() else 0
    with runstate.Worker(channel, now=lambda: 0.0) as w:
        for step in w.steps(start=start, total=target):
            w.set("loss", 5.0 * math.exp(-0.1 * step))
        ckpt.write_text(json.dumps({"next_step": target}))

def test_relaunch_extends_one_series(tmp_path):
    launcher = runstate.ThreadLauncher()  # memory backend, in-process
    rid = "exp"
    launcher.launch(rid, _cell, kwargs={"run_id": rid, "target": 5, "ckpt_dir": str(tmp_path)}).wait()
    # subscribe densely so the trace is logged, then extend to 10
    ch = launcher.open_channel(rid)
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs")
    # NOTE: subscribe must precede each episode; for the test, re-open and pre-stage before each launch
    launcher.launch(rid, _cell, kwargs={"run_id": rid, "target": 10, "ckpt_dir": str(tmp_path)}).wait()
    steps = sorted(v.body["step"] for v in ch.read(topics=["value"]))
    assert steps == list(range(10))                      # one continuous 0..9 series
    assert runstate.peek_terminal(ch).outcome == "completed"
```

(Refine in execution: pre-stage the `control.subscribe` *before each* launch so both episodes emit; assert no `step` appears twice.)

- [ ] **Step 2: Run, iterate to green** (this composes Tasks 1–5; no new production code — if it needs any, that's a gap to fix in the relevant task)

Run: `python -m pytest tests/test_run_episodes.py -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_run_episodes.py
git commit -m "test: autonomous-extend integration (relaunch resumes, one run-absolute series)"
```

---

## Task 7: Docs — the `run_id`-excludes-target rule + resume convention

**Files:**
- Modify: `docs/specs/run-id-recipe.md`

- [ ] **Step 1: Add a "Extendable runs" subsection** to the recipe doc:

```markdown
## Extendable runs: exclude the step-target

For a run you intend to *extend* (run further later, reusing the prefix), the
`run_id` must hash the trajectory-determining inputs **minus the step-target**
(`max_steps`/`N`) — the target is the *extend axis*, not identity. Relaunch the
same `run_id` with a higher target; the worker resumes from its `run_id`-keyed
checkpoint and continues the run-absolute `step`.

**Precondition (you own it):** the trajectory must be *target-independent* —
`loss[42]` is the same whether you asked for 100 or 500 steps. A schedule keyed
on the total (cosine-over-`max_steps`) breaks this: a different target is then a
different run, and reusing a shorter run's prefix is silently wrong. If your
schedule depends on the total, either key it on the `run_id` (a fixed horizon) or
don't treat the run as extendable.
```

- [ ] **Step 2: Commit**

```bash
git add docs/specs/run-id-recipe.md
git commit -m "docs(recipe): extendable runs exclude the step-target (+ target-independence caveat)"
```

---

## Self-review

- **Spec coverage:** §1 implicit boundaries → no schema task (correct). §2 episode-aware liveness → Task 2 (+ Watcher inherits). §3 single-spawn guard → Task 1 (CAS) + Task 5 (worker self-claims `lifecycle.started`; loser exits) + Task 4 (`resolve`, used by `live_episode`). §4 autonomous-extend → Task 3 (`steps(start=)`) + Task 6 (integration) + Task 7 (recipe rule). Deliverables all mapped.
- **Placeholders:** the `(refine in execution: …)` note in Task 6 is a *test-refinement* hint, not an impl placeholder; the production code in every step is shown. The worker's claim reads the last seq via `read()[-1].seq` (0 if empty) — no new channel surface.
- **Type consistency:** `resolve(handle)` (Task 4) is used by `live_episode` (Task 5); `send(..., expected_seq=)` (Task 1) is used by the worker's claim loop (Task 5); `live_episode` (Task 5) + `steps(start=)` (Task 3) feed the integration (Task 6). Consistent.

One known integration nuance for the executor: the `control.subscribe` must be pre-staged before *each* episode's launch (a fresh episode re-drains control from its cursor) — the Task 6 test note calls this out.
