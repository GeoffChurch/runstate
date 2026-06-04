# B′: `completed` opt-in, `Stopped.reason` removed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The worker's terminal report becomes a single bit + a scoped diagnostic: `Stopped = {completed: bool, error: str|null, final_step: int|null}`. `completed` is the worker's sole opt-in claim; the default clean exit (`completed=False`, no error) projects to `preempted`. `reason` (and `"commanded"`) are removed.

**Architecture:** This is a **wire/convention change** (the `lifecycle.stopped` body shape changes), so the core (`payloads` → `worker` → `peek_terminal` → schema) changes atomically; then the test fallout (the B′ default flip means any worker that "completed" by falling off its loop is now `preempted` unless it claims `completed=True`).

**Tech stack:** Python 3.11+, stdlib + jsonschema (the conformance tests); pytest over both backends.

**Spec:** `docs/specs/completed-opt-in.md` (authoritative — read it). **Out of scope:** mycooc's emitter (separate repo).

---

## The migration rule (apply everywhere a stop is emitted/constructed/asserted)

**Stop *body* `{reason: R, error: E, final_step: F}` → `{completed: C, error: E', final_step: F}`:**
- `R == "completed"` → `completed=True, error=None`.
- `R == "errored"` → `completed=False, error=E`.
- `R == anything else` (`"preempted"`, `"commanded"`, …) → `completed=False, error=None`.

**`Worker.stopped(...)` calls:**
- `stopped(reason="completed", …)` → `stopped(completed=True, …)`.
- `stopped(reason="errored", error=E)` → `stopped(error=E)` (errored ⟺ `error is not None`; `completed=False` default).
- `stopped(reason="preempted"|"commanded")` → **drop it** (the context-manager `__exit__` emits the default `preempted`), or `stopped()` if a non-`with` test needs the record explicitly.

**A worker that "completes" by falling off its loop AND is asserted `outcome=="completed"`** → add `w.stopped(completed=True)` before exit (the default is now `preempted`).

**Assertions:**
- `r.reason == "completed"` / `"errored"` → still hold (`RunResult.reason` = `outcome` for the lifecycle tier).
- `r.reason == "commanded"` → `r.reason == "preempted"` (the verbatim label is gone; `reason` = `outcome`).
- `stopped.body["reason"] == X` → `stopped.body["completed"] == (X == "completed")` and/or `stopped.body["error"]`.
- `w.tick(...) == "commanded"` → `w.tick(...) is True`; `w.tick(...) is None` → `w.tick(...) is False`.

---

## Task 1: Core shape change (`payloads` + schema + `worker` + `peek_terminal`) + unit tests

**Files:** Modify `runstate/vocabulary/payloads.py`, `protocol/lifecycle-v0.2.schema.json`, `runstate/worker.py`, `runstate/liveness.py`; Test `tests/test_payloads.py`, `tests/test_schema.py`, `tests/test_worker.py`, `tests/test_liveness.py`.

The core is coupled — the `Stopped` shape ripples through construction (`worker`) and reading (`liveness`) at once — so it lands together. At the end of Task 1, those four test files are green; the broader suite may be RED (fixed in Tasks 2–3).

- [ ] **Step 1: `payloads.Stopped` — new shape + invariant.** Replace the class (`payloads.py:51-58`):
  ```python
  @dataclass(frozen=True)
  class Stopped:
      """The cooperative dying breath; its existence on the log = a clean, *resumable*
      halt (§7). ``completed=True`` is the worker's opt-in claim of intrinsic, permanent
      completion; otherwise the stop projects to ``preempted``. ``error`` is the failure
      diagnostic; a completed stop carries no error (enforced)."""

      completed: bool
      error: Optional[str]
      final_step: Optional[int]
      TOPIC: ClassVar[str] = "lifecycle.stopped"

      def __post_init__(self):
          # completed ⟹ error is None: keeps the two content fields non-overlapping, so
          # `error is not None` ⟺ errored holds globally (mirrors Terminated's exited-XOR-killed).
          if self.completed and self.error is not None:
              raise ValueError("a completed stop cannot carry an error (completed ⟹ error is None)")
  ```

- [ ] **Step 2: schema `Stopped`.** Replace the `$defs.Stopped` block (`lifecycle-v0.2.schema.json:48-56`):
  ```json
  "Stopped": {
    "type": "object", "required": ["completed", "error", "final_step"], "additionalProperties": false,
    "description": "The cooperative dying breath; its existence on the log = a clean, resumable halt (§7). completed=true is the worker's opt-in claim of intrinsic completion; otherwise the stop projects to preempted. error is the failure diagnostic; a completed stop carries no error.",
    "properties": {
      "completed": {"type": "boolean"},
      "error": {"type": ["string", "null"]},
      "final_step": {"type": ["integer", "null"], "minimum": 0}
    },
    "allOf": [
      {"if": {"required": ["completed"], "properties": {"completed": {"const": true}}},
       "then": {"properties": {"error": {"const": null}}}}
    ]
  }
  ```

- [ ] **Step 3: `worker.py`.** (a) Delete `self._stop_reason = None` (`:31`). (b) `__exit__` (`:57-64`):
  ```python
      def __exit__(self, exc_type, exc, tb) -> bool:
          if self._lost:
              return False
          if exc_type is not None:
              self.stopped(error=str(exc), final_step=self._last_step)
          else:
              self.stopped(final_step=self._last_step)   # default: no claim -> preempted
          return False
  ```
  (c) `steps` loop body (`:80-87`): replace the `reason = self.tick(...)` block with
  ```python
          while total is None or step < total:
              self._last_step = step
              yield step
              if self.tick(step):    # truthy -> a control.stop fired; stop at this safe point
                  return
              step += 1
  ```
  (d) `tick` (`:93-111`) returns `bool`:
  ```python
      def tick(self, step) -> bool:
          """Drain control, service due subscriptions, beacon a heartbeat. Returns
          True iff a control.stop fired this tick (stop at this safe point), else
          False. The worker's own *completion* is a separate opt-in claim
          (``w.stopped(completed=True)``); a commanded stop carries no reason —
          commandedness is recoverable from the control.stop on the log."""
          self._drain_control(step)
          self._service(step)
          self._ch.send(asdict(Heartbeat(step=step, consumed_seq=self._cursor)),
                        topic="lifecycle.heartbeat")
          return self._stop is not None and self._stop.tick(step=step, now=self._now()).fire
  ```
  (e) `stopped` (`:113-128`):
  ```python
      def stopped(self, *, completed: bool = False, error=None, final_step=None) -> None:
          """Emit the cooperative dying breath (lifecycle.stopped). Its existence = a
          clean, resumable halt. ``completed=True`` is the opt-in completion claim; the
          default (completed=False, no error) projects to ``preempted``; an ``error``
          projects to ``errored``. Idempotent — first writer wins."""
          if self._stopped:
              return
          self._stopped = True
          body = asdict(Stopped(completed=completed, error=error, final_step=final_step))
          self._ch.send(body, topic="lifecycle.stopped")
  ```

- [ ] **Step 4: `peek_terminal` lifecycle tier** (`liveness.py:82-93`):
  ```python
      stopped = _terminal_unless_followed(channel, "lifecycle.stopped", "lifecycle.started")
      if stopped is not None:
          s = Stopped(**stopped.body)
          if s.error is not None:          # NB: `is not None`, not truthiness — "" still errors
              outcome = "errored"
          elif s.completed:
              outcome = "completed"
          else:
              outcome = "preempted"
          return RunResult(outcome=outcome, reason=outcome, error=s.error, final_step=s.final_step)
  ```
  Update the `RunResult.reason` comment (`liveness.py:23-24`): the lifecycle tier's `reason` now equals `outcome` (no finer label — the verbatim worker reason is gone); the launcher tier still carries `exited`/`killed`.

- [ ] **Step 5: migrate the four unit test files** per the migration rule:
  - `tests/test_payloads.py:26-27` → `Stopped(completed=True, error=None, final_step=9)`, `Stopped(completed=False, error="boom", final_step=1)`. **Add** `test_completed_with_error_rejected`: `with pytest.raises(ValueError): Stopped(completed=True, error="x", final_step=None)`.
  - `tests/test_schema.py`: `:160` `payloads.Stopped(completed=True, error=None, final_step=9)`; `:98` additionalProperties body → `{"completed": True, "error": None, "final_step": None, "oops": 1}`; `:126` valid body → `{"completed": True, "error": None, "final_step": None}`; `:129-131` missing-field cases → drop `completed`/`error`/`final_step` respectively. **Add** a negative case: `{"completed": True, "error": "x", "final_step": None}` must FAIL validation (the if-then constraint).
  - `tests/test_worker.py`: `:58/:66/:162` `== "commanded"` → `is True`; `:65/:137-138/:161/:171-172` `is None` → `is False`; `:71-73` → `stopped(completed=True, final_step=500)` + body `{"completed": True, "error": None, "final_step": 500}`; `:79-84` → `stopped(error="boom")` + body `{"completed": False, "error": "boom", "final_step": None}`; `:258-270` (`test_steps_drives_ticks_and_stops_completed`) — falling off now yields `preempted`; **split/rename** into the default case (no claim → body `{"completed": False, "error": None, "final_step": 2}`) and an explicit-claim case (`w.stopped(completed=True)` inside the `with` → body `{"completed": True, …}`); `:273-285` (commanded) → body `{"completed": False, "error": None, "final_step": 2}` (and optionally assert the `control.stop` is on the log — commandedness recoverable); `:288-295` (errored) → body `{"completed": False, "error": "boom", "final_step": 1}`; `:298-303` (idempotent) → `stopped(completed=True)` then `stopped(error="x")`, body `{"completed": True, "error": None, "final_step": None}`.
  - `tests/test_liveness.py`: `:18-27` body `{"completed": True, …}` (assertions unchanged — `reason == "completed"` still holds via `reason=outcome`); `:30-38` body `{"completed": False, "error": "boom", …}`; `:41-51` (`test_commanded_is_stopped`) → **rename** `test_default_stop_is_preempted`, body `{"completed": False, "error": None, "final_step": 7}`, assert `outcome == "preempted"` and `reason == "preempted"`; `:66/:77/:85/:91` bodies → `{"completed": True, …}`.

- [ ] **Step 6: run the four files green.**
  Run: `pytest tests/test_payloads.py tests/test_schema.py tests/test_worker.py tests/test_liveness.py -q`
  Expected: PASS. (`pytest tests/ -q` will still have failures in watcher/sweep/integration/episodes/launcher/memoizer — Tasks 2–3.)

- [ ] **Step 7: commit.**
  ```bash
  git add runstate/vocabulary/payloads.py protocol/lifecycle-v0.2.schema.json runstate/worker.py runstate/liveness.py tests/test_payloads.py tests/test_schema.py tests/test_worker.py tests/test_liveness.py
  git commit -m "feat(lifecycle)!: Stopped = {completed,error,final_step}; completed opt-in, preempted default

  Remove Stopped.reason. completed=True is the worker's sole opt-in claim (invariant
  completed=>error is None); default clean exit (completed=False, no error) -> preempted;
  error present -> errored. tick returns a bool; _stop_reason/'commanded' retired. Wire
  change (lifecycle convention). Core + unit tests; consumer/memoizer fallout follows.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 2: Consumer + integration test fallout

**Files (test-only):** `tests/test_watcher.py`, `tests/test_inproc_integration.py`, `tests/test_sweep.py`, `tests/test_run_episodes.py`, `tests/test_local_launcher.py`, `tests/test_channel.py`.

Apply the migration rule. The **judgment** part: any worker whose run is asserted `outcome=="completed"` must now **claim** `completed=True` (the default flipped to `preempted`).

- [ ] **Step 1: raw stopped bodies → new shape.** `test_watcher.py:50/75/149/315`, `test_inproc_integration.py:42/45`, and `test_channel.py:44-45/124-125` (the latter use the body as an *opaque* substrate payload — migrate for consistency to `{"completed": True, "error": None, "final_step": None}`; the substrate doesn't validate, so correctness doesn't require it, but keep it honest).

- [ ] **Step 2: workers that complete by falling off → claim `completed=True`.** Audit `test_sweep.py` (the workers behind the `outcome=="completed"` asserts at `:36/66/75`), `test_run_episodes.py` (`:46`), `test_local_launcher.py` (`:43`), `test_inproc_integration.py` (`:45`). Each such worker that currently relies on the default `completed` must add `w.stopped(completed=True)` before leaving its `with` block. `test_sweep.py:93` (the commanded → `preempted` case) is **unchanged** — that worker is correctly `preempted` by default.

- [ ] **Step 3: assertion fixups.** `test_inproc_integration.py:56` `stopped.body["reason"] == "commanded"` → `stopped.body["completed"] == False` (and `:63` `r.reason == "commanded"` → `r.reason == "preempted"`). Any other `body["reason"]`/`r.reason` per the rule.

- [ ] **Step 4: run green + commit.**
  Run: `pytest tests/test_watcher.py tests/test_inproc_integration.py tests/test_sweep.py tests/test_run_episodes.py tests/test_local_launcher.py tests/test_channel.py -q` → PASS.
  ```bash
  git add tests/test_watcher.py tests/test_inproc_integration.py tests/test_sweep.py tests/test_run_episodes.py tests/test_local_launcher.py tests/test_channel.py
  git commit -m "test(lifecycle): migrate consumer/integration tests to Stopped{completed,error}; claim completed where genuinely done

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 3: memoizer fixtures

**Files (test-only):** `tests/test_memoizer.py`.

The fixtures encode the resumable/chunked discipline; B′ *inverts* how they signal it.

- [ ] **Step 1: resumable cells drop the explicit `preempted`.** `_cell` (`:111`): delete `w.stopped(reason="preempted")` — falling off the `with` now defaults to `preempted` (resumable), which is what `_cell` wants. (Its docstring should note: resumable by default; it never claims `completed`.)

- [ ] **Step 2: `chunked` (`:186-197`) inverts — claim `completed` only when it reaches the full target.** Currently: `if stop < up_to: w.stopped(reason="preempted")` else (default `completed`). New:
  ```python
          with runstate.Worker(channel, now=lambda: 0.0) as w:
              for step in w.steps(start=start, total=stop):
                  w.set("loss", float(step))
              if stop >= up_to:
                  w.stopped(completed=True)   # reached the full target -> intrinsic done
              # else: fall off -> default preempted (more to do)
  ```
  Likewise the `stuck` fixture (`:162-169`): delete `w.stopped(reason="preempted")` (default preempted keeps `ensure` re-driving so the no-progress guard fires).

- [ ] **Step 3: raw seeded bodies → new shape.** `_seed_episode` (`:302-317`, its `stopped_reason=` param + the `{"reason": …}` send at `:314`): change to a `completed: bool` param and emit `{"completed": …, "error": None, "final_step": …}`; update call sites `:326` (`completed=True`) and `:349` (`completed=False`). The `_extend_side_effect` completed-episode send (`:363-366`) → `{"completed": True, "error": None, "final_step": M}`. The `_FakeProducer`/`_ZeroStepTimeProducer`/`_StepThenWaitProducer` preempted sends (`:259`, `:423`, `:498`) → `{"completed": False, "error": None, "final_step": …}`. `_TimeChunkProducer` (`:533/543` constructed with `reason="preempted"|"completed"`): change its ctor to take `completed: bool` and emit `{"completed": completed, "error": None, "final_step": …}` (the `reason="completed"` case → `completed=True`; `reason="preempted"` → `completed=False`).

- [ ] **Step 4: run green + commit.**
  Run: `pytest tests/test_memoizer.py -q` → PASS (the `ensure` behavior is unchanged; only the fixtures' stop-signaling moved to B′).
  ```bash
  git add tests/test_memoizer.py
  git commit -m "test(memoizer): migrate fixtures to B' stop-signaling (claim completed; default preempted)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Task 4: Example verification + docs

**Files:** `examples/reuse/driver.py` (verify, likely no change), `examples/minimal/driver.py` (verify), `docs/design-v0.2.md`, `docs/specs/preempted-vs-completed.md`.

- [ ] **Step 1: verify the reuse example now demonstrates the fix.** `examples/reuse/driver.py`'s `train` already falls off without claiming `completed`, so under B′ it defaults to `preempted` and `extend` resumes. Run `python examples/reuse/driver.py` and confirm the `extend` line now prints **`asked 20, got 20; one series 0..19`** (was `got 8`). No code change expected — if `train` somehow still truncates, that's a finding. The inline comment `# extend: resume 8..19` is now accurate.
- [ ] **Step 2: verify the minimal example.** `examples/minimal/driver.py:63` prints `reason={result.reason!r}` — `RunResult.reason` still exists (= outcome for lifecycle), so it runs. Run `python examples/minimal/driver.py` to confirm.
- [ ] **Step 3: docs prose.** `docs/design-v0.2.md` §7 (and the `Stopped` schema/prose references) "a clean stop = the run finished" → "a clean, resumable halt; `completed=True` is the worker's opt-in claim, else `preempted`." `docs/specs/preempted-vs-completed.md`: retune its "`preempted` is the worker's cooperative declaration" / "worker self-reports" lines to "the worker claims `completed` or stays unmarked (`completed=False`); `preempted` is the consumer-side projection; the worker emits no `reason`." Keep edits proportional.
- [ ] **Step 4: full suite + commit.**
  Run: `pytest tests/ -q` → PASS (whole suite). `python examples/reuse/driver.py` and `python examples/minimal/driver.py` run clean.
  ```bash
  git add examples/ docs/design-v0.2.md docs/specs/preempted-vs-completed.md
  git commit -m "docs+example(lifecycle): B' prose; reuse example now resumes on extend (footgun fixed)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## Final verification (after all tasks)

- [ ] `pytest tests/ -q` — whole suite green over both backends.
- [ ] `grep -rn 'reason=\|"reason"\|"commanded"\|_stop_reason' runstate/ tests/ examples/` — no `Stopped`-`reason` remnants (hits should be only `Nak`/`Terminated` reasons, or `RunResult.reason`/comments).
- [ ] `grep -rn 'stopped(reason=' runstate/ tests/ examples/` — none.
- [ ] Spec cross-check: the `{completed, error}` projection, the invariant, the `tick`→bool, the `reason` removal, the example fix — each realized.
- [ ] Dispatch the final code-reviewer over the whole diff, then `superpowers:finishing-a-development-branch`.
