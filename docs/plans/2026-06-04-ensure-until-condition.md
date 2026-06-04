# `ensure(until=<condition>)` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the memoizer's drive-target from the scalar `ensure(up_to: int)` to `ensure(until: Condition)` over the subscription condition-algebra (`step`/`time_seconds`, `any`/`all`), so step *and* wall-clock milestones share one loop.

**Architecture:** `up_to=N` becomes `until={"step":N}` (exact-preserving — the `+1` half-open convention). Satisfaction uses the shipped `satisfied()`; the time axis reads the **consumer's own poll-clock** (`clock()−epoch`, injectable) — no wire change. The producer self-bounds the worker; the no-progress guard becomes axis-aware. `count` as a drive-target is rejected at entry.

**Tech stack:** Python 3.11+, stdlib only; pytest over both channel backends (`open_channel` fixture). The change is confined to `runstate/memoizer.py` + its tests; one example + two docs.

**Spec:** `docs/specs/ensure-until-condition.md` (authoritative — read it). **Out of scope:** the `from`/`every` emission filter (`ensure(schedule=)`, tracked in `docs/backlog/memoizer-index-algebra.md`); any schema change; mycooc's own producer.

---

## File structure

- `runstate/memoizer.py` — the whole change: `ensure(up_to)`→`ensure(until)` + `clock=time.time`; helpers `_elapsed`, `_window_step`, `_satisfied`, `_requires_step`, `_reject_count`; axis-aware guard; `_LaunchProducer.extend(until)` extract-scalar/reject; rename `up_to`→`until` in the producer; a comment pointing at the backlog residue.
- `tests/test_memoizer.py` — migrate all `up_to=N`→`until={"step":N}` (exact); add time/guard/producer-reject/compound/count/preempted-on-time tests.
- `examples/reuse/driver.py` — migrate `ensure(... up_to=)` calls + prose.
- `docs/specs/memoizer.md` — update the `ensure` semantics (condition target, poll-clock, the `[0,N)` window, the producer seam, the axis-aware guard).

Each task ends green and is committed. The whole suite is ~130 tests, sub-1s.

---

## Task 1: Migrate the step axis — `ensure(up_to)` → `ensure(until)`, exact-preserving

**Files:**
- Modify: `runstate/memoizer.py` (`ensure`, `_LaunchProducer.extend`, `launch_producer` docstring)
- Test: `tests/test_memoizer.py` (migrate all existing `up_to=`/`extend(` call sites; add one reject test)

The correctness review verified `satisfied({"step":N}, step=_progress+1) ⇔ _progress >= N-1` on every edge case (`progress=-1`, `N=0/1`, past). This task is that substitution plus the producer extract/reject. Time/guard/count come in later tasks; here the clock is **wired but step-inert** (step conditions ignore `time_seconds`).

- [ ] **Step 1: Migrate every test call site (RED — signature mismatch).** In `tests/test_memoizer.py`, replace:
  - every `ensure(producer, "loss", up_to=N, ...)` → `ensure(producer, "loss", until={"step": N}, ...)` (keep any `sleep=`/other kwargs);
  - `producer.extend(3)` (in `test_launch_producer_extend_injects_target_and_runs`) → `producer.extend({"step": 3})`;
  - `_FakeProducer.extend(self, up_to)` → `def extend(self, until):` (body unchanged — it's a no-op counter; the local name is now `until`);
  - leave every **worker**'s `*, up_to` kwarg and `w.steps(total=up_to)` **unchanged** (that kwarg is what the producer injects; the producer extracts the scalar — see Step 3).

- [ ] **Step 2: Run the suite to confirm RED.**
  Run: `pytest tests/test_memoizer.py -x -q`
  Expected: FAIL — `ensure() got an unexpected keyword argument 'until'` (and/or `up_to`).

- [ ] **Step 3: Implement the `until` signature + step satisfaction in `runstate/memoizer.py`.**
  Add imports and helpers (near the top, after the existing imports):

  ```python
  from .vocabulary.schedule import satisfied   # add alongside the existing Subscription import
  ```

  Replace the `_LaunchProducer.extend` method and `target_key` usage so it translates the condition:

  ```python
  def extend(self, until):
      """Trigger production toward `until`: relaunch iff not already live.
      The default producer translates ONLY a step condition -- it injects the
      scalar `until["step"]` under `target_key`. Any other shape (time_seconds,
      count, any/all) needs a launcher whose worker accepts that bound, i.e. the
      user's own producer (.channel/.run_id/.extend(until)); reject it loudly
      rather than inject a dict the worker can't consume."""
      if list(until.keys()) != ["step"]:
          raise ValueError(
              f"the default launch-producer translates only {{'step': N}}; got "
              f"{until!r}. Bring your own producer (.channel/.run_id/.extend(until)) "
              f"for time/compound milestones."
          )
      target = until["step"]
      launch_kwargs = dict(self._variant.launch_kwargs)
      worker_kwargs = dict(launch_kwargs.get("kwargs") or {})
      worker_kwargs[self._target_key] = target
      launch_kwargs["kwargs"] = worker_kwargs
      return relaunch_if_needed(
          self._launcher, self._variant.run_id, self._variant.target, **launch_kwargs
      )
  ```

  Add the satisfaction helper (above `ensure`), with the load-bearing convention in its docstring:

  ```python
  def _window_step(channel) -> int:
      """The step coordinate for window-close satisfaction: `_progress + 1`.

      `ensure(until={step:N})` drives the half-open window `[0, N)` -- the
      worker's exclusive target (steps `0..N-1`, reaching `progress = N-1`).
      `_progress + 1 >= N` <=> `_progress >= N-1` is exactly the old `up_to-1`
      hit, and agrees with the read-side `Subscription` expiry gate (which
      excludes the boundary point `N`). The `+1` is applied HERE, in the
      coordinate -- never by rewriting the condition (`{step:N}`->`{step:N-1}`
      would break `any`/`all`, which evaluates every atom against the same
      passed coordinates). `[0,N)` <-> `range(N)`/`up_to` <-> read-side expiry
      is the triple that makes this exact."""
      return _progress(channel) + 1

  def _satisfied(channel, until, *, clock) -> bool:
      """Has the run closed the `until` window? Coordinates read live: step from
      the dense axis (`_window_step`), time from the consumer's poll-clock
      (`_elapsed`). `count=0` -- the count drive-axis is rejected at entry."""
      return satisfied(until, step=_window_step(channel),
                       time_seconds=_elapsed(channel, clock), count=0)
  ```

  Add the poll-clock reader (below `_progress`):

  ```python
  def _elapsed(channel, clock) -> float:
      """Run-relative seconds on the consumer's OWN poll-clock (dense, monotone,
      gap-inclusive; no wire dependency -- see the spec's clock rationale).
      Returns 0.0 before the run has started (no epoch yet -> time conditions
      are inert until the run begins)."""
      started = channel.read(topics=["lifecycle.started"], limit=1)
      if not started or started[0].body.get("attached_at") is None:
          return 0.0
      return clock() - started[0].body["attached_at"]
  ```

  Rewrite `ensure`'s signature and the step-relevant lines (leave the failure / completed / re-drive structure intact; full axis-aware guard is Task 2):

  ```python
  def ensure(producer, name, *, until, poll_interval=0.01, sleep=time.sleep,
             clock=time.time) -> list[dict]:
      """Return ``name``'s series for the window ``until`` (a Condition from the
      subscription algebra: ``{"step":N} | {"time_seconds":S} | any/all``),
      producing the missing suffix on a miss. Window-closed (or worker-declared
      ``completed``) -> a pure log read; else ``producer.extend(until)`` and wait,
      re-driving ``preempted`` and raising on a failure outcome or no progress.

      `up_to=N` is `until={"step":N}` (the half-open window `[0, N)`). Time is the
      consumer's poll-clock; the generalization to the emission filter
      (`from`/`every`) is deferred -- see docs/backlog/memoizer-index-algebra.md.
      No hang timeout (unchanged)."""
      channel = producer.channel
      dense = {"every": {"step": 1}, "until": until}
      result = peek_terminal(channel)
      if _satisfied(channel, until, clock=clock) or (
          result is not None and result.outcome == "completed"):
          return history(channel, name, dense)

      while not _satisfied(channel, until, clock=clock):
          before = _progress(channel)
          handle = producer.extend(until)
          while not _satisfied(channel, until, clock=clock):
              if handle is not None:
                  if not handle.is_alive():
                      handle.wait()
                      break
              elif peek_terminal(channel) is not None:
                  break
              sleep(poll_interval)
          else:
              return history(channel, name, dense)
          result = peek_terminal(channel)
          if result is not None and result.outcome in _FAILURES:
              raise RuntimeError(
                  f"run {producer.run_id!r} failed: {result.outcome}/{result.reason}"
              )
          if result is not None and result.outcome == "completed":
              return history(channel, name, dense)
          if handle is not None and _progress(channel) <= before:
              raise RuntimeError(
                  f"run {producer.run_id!r} made no progress toward {until} "
                  f"(stuck at {_progress(channel)}); cannot extend"
              )
      return history(channel, name, dense)
  ```

  (Note: the `_progress(channel) <= before` guard here is still step-only — correct for every existing test, which is step-targeted. Task 2 makes it axis-aware. Do NOT add time tests yet.)

- [ ] **Step 4: Add the default-producer rejection test.** Append to `tests/test_memoizer.py`:

  ```python
  def test_launch_producer_rejects_non_step_condition():
      launcher = runstate.ThreadLauncher()
      variant = runstate.Variant("exp", lambda channel, *, up_to: None, {"kwargs": {}})
      producer = launch_producer(launcher, variant)
      for bad in ({"time_seconds": 5}, {"count": 3},
                  {"all": [{"step": 1}, {"time_seconds": 2}]}):
          with pytest.raises(ValueError, match="bring your own producer|only"):
              producer.extend(bad)
  ```

- [ ] **Step 5: Run memoizer tests (GREEN).**
  Run: `pytest tests/test_memoizer.py -q`
  Expected: PASS (all migrated tests + the new reject test). The RETURN assertions are byte-identical to before — this is the exactness proof.

- [ ] **Step 6: Run the full suite + check no other caller broke.**
  Run: `grep -rn 'up_to=' runstate/ tests/ examples/ ; pytest tests/ -q`
  Expected: any remaining `up_to=` are **worker kwargs** (e.g. `def worker(..., up_to)`, `w.steps(total=up_to)`), NOT `ensure(...up_to=)` / `extend(...up_to)`. Full suite PASS except possibly `examples/reuse/driver.py` (migrated in Task 5; if a test imports it, note it and proceed).

- [ ] **Step 7: Commit.**
  ```bash
  git add runstate/memoizer.py tests/test_memoizer.py
  git commit -m "feat(memoizer): ensure(up_to=int) -> ensure(until=Condition), step axis (exact)

  up_to=N becomes until={step:N} (half-open [0,N); satisfied(step=_progress+1)
  reproduces _progress>=N-1 exactly). Producer extract-scalar-for-{step}, reject
  others. Poll-clock wired (clock injectable) but step-inert. Time axis + axis-aware
  guard + count rejection follow.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 2: Time axis — poll-clock satisfaction + axis-aware no-progress guard

**Files:**
- Modify: `runstate/memoizer.py` (`_requires_step`; the guard line)
- Test: `tests/test_memoizer.py` (time-satisfaction, sparse no-livelock, guard regressions)

The poll-clock is already wired (Task 1). Two things remain: a `{time_seconds}` milestone whose chunk advances 0 steps must NOT raise "no progress" (the critical-c fix), and tests proving time drives correctly even when `value` is sparse.

- [ ] **Step 1: Write the guard regression test (RED).** Append to `tests/test_memoizer.py`. The guard is gated on `handle is not None`, so the test MUST return a non-None (already-dead) handle — otherwise the step-only guard is skipped and the bug isn't exercised. A monotone ramp clock guarantees `_elapsed` crosses the budget (no fragile fixed tick list):

  ```python
  class _DeadHandle:
      """A LaunchHandle that reports its episode already finished."""
      def is_alive(self): return False
      def wait(self): pass

  class _RampClock:
      """Monotone poll-clock: +`step` per call, so _elapsed crosses any budget."""
      def __init__(self, step=1.0): self.t = -step; self.step = step
      def __call__(self): self.t += self.step; return self.t

  class _ZeroStepTimeProducer:
      """Each extend drives a chunk that makes NO step progress and ends
      `preempted` (a live episode we drove -> handle is not None)."""
      run_id = "fake"
      def __init__(self, channel): self._c = channel; self.calls = 0
      @property
      def channel(self): return self._c
      def extend(self, until):
          self.calls += 1
          self._c.send({"reason": "preempted", "error": None, "final_step": 0},
                       topic="lifecycle.stopped")
          return _DeadHandle()

  def test_ensure_time_milestone_does_not_false_raise_on_zero_step_progress():
      """A {time_seconds} chunk that advances 0 steps while the clock advances must
      NOT trip the no-progress guard (critical-c). With the OLD step-only guard the
      first drive raises (progress 0 <= before 0); with the axis-aware guard the
      ramp clock eventually satisfies and ensure returns."""
      from runstate.channel.memory import MemoryChannel
      from runstate.vocabulary.handle import local_handle

      ch = MemoryChannel()
      ch.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
              topic="lifecycle.started")            # epoch 0.0
      ch.send({"step": 0, "consumed_seq": 0}, topic="lifecycle.heartbeat")  # 0 steps
      ch.send({"value": 0.0, "step": 0, "t": 0.0}, topic="value", name="loss")

      series = ensure(_ZeroStepTimeProducer(ch), "loss", until={"time_seconds": 5},
                      clock=_RampClock(), poll_interval=0)
      assert [b["step"] for b in series] == [0]      # returned, did not raise
  ```

- [ ] **Step 2: Run to confirm RED.**
  Run: `pytest tests/test_memoizer.py::test_ensure_time_milestone_does_not_false_raise_on_zero_step_progress -q`
  Expected: FAIL — `RuntimeError: ... made no progress` (the step-only guard false-fires on the first drive, before the ramp clock crosses 5).

- [ ] **Step 3: Make the guard axis-aware in `runstate/memoizer.py`.** Add the helper:

  ```python
  def _requires_step(cond: dict) -> bool:
      """Does satisfying `cond` REQUIRE step progress (a stallable axis)? If not,
      the poll-clock alone reaches it, so a step-stall is not a livelock and the
      no-progress guard must not fire. `all` needs step iff ANY child does; `any`
      iff ALL children do (one non-step child can satisfy it). time_seconds never
      requires step; count is rejected before we get here."""
      if "step" in cond:
          return True
      if "time_seconds" in cond:
          return False
      if "all" in cond:
          return any(_requires_step(c) for c in cond["all"])
      if "any" in cond:
          return all(_requires_step(c) for c in cond["any"])
      return False
  ```

  Change the guard line in `ensure`:

  ```python
          if handle is not None and _requires_step(until) and _progress(channel) <= before:
              raise RuntimeError(
                  f"run {producer.run_id!r} made no progress toward {until} "
                  f"(stuck at {_progress(channel)}); cannot extend"
              )
  ```

- [ ] **Step 4: Add the time-satisfaction + sparse-no-livelock test.** Reuse `_ZeroStepTimeProducer` + `_RampClock` from Step 1. The point: `value.t` is frozen at 0 (only step 0 ever emitted) yet the ramp poll-clock crosses the budget, so satisfaction comes from the clock — no livelock.

  ```python
  def test_ensure_time_milestone_satisfies_via_poll_clock_even_when_value_sparse():
      """Sparse `value` (only step 0 emitted, value.t frozen at 0) must not livelock a
      {time_seconds} milestone: satisfaction reads the poll-clock, not value.t."""
      from runstate.channel.memory import MemoryChannel
      from runstate.vocabulary.handle import local_handle

      ch = MemoryChannel()
      ch.send({"handle": local_handle(), "hostname": None, "attached_at": 0.0},
              topic="lifecycle.started")
      ch.send({"value": 0.0, "step": 0, "t": 0.0}, topic="value", name="loss")  # value.t frozen at 0
      ch.send({"step": 0, "consumed_seq": 0}, topic="lifecycle.heartbeat")

      series = ensure(_ZeroStepTimeProducer(ch), "loss", until={"time_seconds": 5},
                      clock=_RampClock(), poll_interval=0)
      assert [b["step"] for b in series] == [0]   # crossed the budget on the clock, not value.t
  ```

- [ ] **Step 5: Confirm the step-stall guard still fires.** The existing `test_ensure_raises_when_run_makes_no_progress` (now `until={"step":5}`) must still raise — `_requires_step({"step":5})` is `True`. Keep it.

- [ ] **Step 6: Run + commit.**
  Run: `pytest tests/test_memoizer.py -q`  → PASS
  ```bash
  git add runstate/memoizer.py tests/test_memoizer.py
  git commit -m "feat(memoizer): axis-aware no-progress guard + poll-clock time satisfaction

  The guard now fires only when `until` requires step progress AND step stalled, so
  a {time_seconds} chunk advancing 0 steps no longer false-raises. Time satisfaction
  reads the consumer poll-clock (dense, gap-inclusive), so a sparse value emitter
  can't livelock a time milestone.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 3: Reject the `count` drive-axis at entry

**Files:**
- Modify: `runstate/memoizer.py` (`_reject_count`; call at `ensure` entry)
- Test: `tests/test_memoizer.py`

An un-driven `count` atom would satisfy `satisfied(..., count=0)` never → livelock. Reject it loudly, for every producer.

- [ ] **Step 1: Write the test (RED).**
  ```python
  def test_ensure_rejects_count_drive_condition():
      from runstate.channel.memory import MemoryChannel
      producer = _FakeProducer(MemoryChannel())
      for bad in ({"count": 3}, {"any": [{"step": 5}, {"count": 3}]}):
          with pytest.raises(ValueError, match="count"):
              ensure(producer, "loss", until=bad)
  ```

- [ ] **Step 2: Run to confirm RED.**
  Run: `pytest tests/test_memoizer.py::test_ensure_rejects_count_drive_condition -q`
  Expected: FAIL (no raise — `_FakeProducer.extend` is a no-op and `_satisfied` returns False forever; the test would hang, so set `poll_interval=0` is NOT enough — it must raise BEFORE looping). This is why rejection is at entry.

- [ ] **Step 3: Implement `_reject_count` and call it first in `ensure`.**
  ```python
  def _reject_count(cond: dict) -> None:
      """ensure does not drive the count axis (no use case; an un-driven count atom
      would never satisfy -> livelock). Reject at entry, walking any/all. (count
      stays legal in a *subscription* until -- only the ensure drive-target rejects it.)"""
      if "count" in cond:
          raise ValueError(
              "ensure(until=...) does not support a 'count' condition (no driven "
              "count axis); use step / time_seconds")
      for key in ("any", "all"):
          for c in cond.get(key, ()):
              _reject_count(c)
  ```
  In `ensure`, as the **first** line of the body: `_reject_count(until)`.

- [ ] **Step 4: Run + commit.**
  Run: `pytest tests/test_memoizer.py -q`  → PASS
  ```bash
  git add runstate/memoizer.py tests/test_memoizer.py
  git commit -m "feat(memoizer): reject a count drive-condition at ensure entry (no silent livelock)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 4: Compound `all` + the `completed`/`preempted` discipline on the time axis

**Files:**
- Test: `tests/test_memoizer.py` (compound-all; preempted-accumulates vs completed-truncates on time)

These assert that existing mechanisms compose on the time axis; expect **no production code change** (if a test reveals one, that's a finding — report it).

- [ ] **Step 1: Compound `all` (step ∧ time) drive + return.** A `_FakeProducer` whose `extend` appends a started/heartbeat/values episode; an injected clock that crosses the time bound only after the step bound is met. Assert `ensure(until={"all":[{"step":N},{"time_seconds":S}]})` returns once BOTH hold, and the guard does not false-raise (`_requires_step` is True, but step advances). Model it on `test_ensure_preempted_redrives_then_stops_on_completion`'s seeding.

- [ ] **Step 2: `preempted` accumulates to the time budget.** A producer whose `extend` drives one timed chunk per call (each emits `preempted` below the budget, advancing the injected clock partway); assert `ensure(until={"time_seconds":S})` re-drives across chunks until `_elapsed ≥ S`, returning the accumulated series — and `extend_calls > 1`.

- [ ] **Step 3: `completed` per chunk truncates (documents the discipline).** Same shape but the chunk emits `completed`; assert `ensure` returns after the FIRST chunk (does NOT accumulate). Add a comment in the test: this is why a time-budgeted resumable worker MUST emit `preempted`, never `completed`.

- [ ] **Step 4: Run + commit.**
  Run: `pytest tests/test_memoizer.py -q`  → PASS
  ```bash
  git add tests/test_memoizer.py
  git commit -m "test(memoizer): compound-all drive + preempted-accumulates/completed-truncates on time

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 5: Migrate the example + docs; add the backlog pointer comment

**Files:**
- Modify: `examples/reuse/driver.py` (`ensure(... up_to=)` → `until={"step":…}` + any prose)
- Modify: `docs/specs/memoizer.md` (the `ensure` semantics)
- Modify: `runstate/memoizer.py` (a one-line comment in `ensure`/near it pointing at the backlog residue)

- [ ] **Step 1: Migrate `examples/reuse/driver.py`.** Replace each `ensure(..., up_to=N)` with `until={"step": N}`; update any prose/docstring that says "`up_to`". Run the example end-to-end:
  Run: `python examples/reuse/driver.py`
  Expected: runs to completion, same output shape as before (the example is step-based).

- [ ] **Step 2: Update `docs/specs/memoizer.md`.** Find the `ensure` section; update: the target is a Condition (`until=`, not `up_to`); the half-open `[0,N)` window; time = the consumer poll-clock; the producer-translation seam (self-bound worker); the axis-aware guard; a pointer to `docs/specs/ensure-until-condition.md` for the full rationale and to `docs/backlog/memoizer-index-algebra.md` for the `from`/`every` residue. (Keep edits proportional — this is a semantics refresh, not a rewrite.)

- [ ] **Step 3: Add the backlog-pointer comment in `runstate/memoizer.py`.** In `ensure` (or just above it), one line so the next reader sees the deliberate scoping:
  ```python
      # `until` is the run *bound*; the emission *filter* (`from`/`every`, the
      # ensure(I) strided case) is deferred -- docs/backlog/memoizer-index-algebra.md.
  ```

- [ ] **Step 4: Full suite + commit.**
  Run: `pytest tests/ -q`  → PASS (whole suite)
  ```bash
  git add examples/reuse/driver.py docs/specs/memoizer.md runstate/memoizer.py
  git commit -m "docs+example(memoizer): migrate to ensure(until=); note the from/every residue

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Final verification (after all tasks)

- [ ] `pytest tests/ -q` — whole suite green over both backends.
- [ ] `grep -rn 'up_to=' runstate/ examples/ docs/` — remaining hits are worker kwargs / spec prose only; no `ensure(...up_to=)` or `extend(...up_to=)` survive.
- [ ] `grep -rn 'ensure(' runstate/ tests/ examples/` — every call uses `until=`.
- [ ] Spec cross-check: each of the spec's "Test plan" bullets maps to a test added above (step-exact, time-satisfy, guard-time, producer-reject, completed/preempted-on-time, compound-all, count-reject).
- [ ] Dispatch the final code-reviewer subagent over the whole diff (spec compliance + quality), then `superpowers:finishing-a-development-branch`.
