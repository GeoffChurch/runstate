# `ensure`'s three unbounded/misreporting defects — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ensure` currently has two loops that can run forever and one report that names the wrong cause. Bound each loop with the signal that actually measures it, and diagnose a crashed spawn from local evidence. No new observable, no Protocol change, no claim-model change.

**Architecture:** Everything lands inside `runstate/memoizer.py`'s `ensure` loop body plus one arithmetic fix. `LiveHandle` / `Producer` / `LaunchHandle` are **unchanged** — verified by three independent reviews that every surface-widening proposal either fails to reach the defect or re-introduces a worse one. `observables.py` is untouched except by the test in Task 1.

**Tech stack:** Python 3.11+, stdlib; pytest over memory + sqlite + (skipped) postgres.

**Out of scope, each killed by measurement — do not revive without new evidence:**
- **`LiveHandle.failure()` + `SpawnFailedError`.** Unreachable in both shipped launchers (a launcher that can report a failure has already written `launcher.terminated`, which `ensure` raises on first). Where it *is* reachable it inverts plane precedence — the worker says "stopped cleanly, resumable", the handle says "killed by signal 9", and it believes the handle, re-introducing the bug mycooc fixed in `bcc8bb1`. Widening cascades to the exported `LaunchHandle` and crashes a real third-party producer with `AttributeError` **on the happy path**, because the guard site runs every redrive iteration.
- **`observed_episode` (the observation/claim split).** Killed by enumeration: of 96 reachable log shapes where it diverges from `live_episode`, `peek_terminal` is silent in **zero**. It tells `ensure` nothing it did not already know at `memoizer.py:377`; the defect is that control never *reaches* 377.
- **Arming the attach-CAS.** Two live claims, reproduced, in the sbatch/srun topology it would target.
- **A third `Terminated.reason`.** Separate question, answered separately: it cannot release a claim, so it cannot remove the forgery that motivated it.

---

## The evidence, measured on `master` (4dad10f)

| # | shape | `until` axis | behaviour |
|---|---|---|---|
| **A1** | own spawn dies pre-attach, resumed log | `any[step, time]` | returns `steps=[0,1,2]`, **no error**, ~1240 spawns / 0.95 s |
| **A2** | own spawn recordless, **cold** log | `{time_seconds}` | **never returns** — 284,014 extends in 3 s |
| **B1** | foreign-host / `slurm://` claim | step | **hangs**, 0 spawns |
| **B2** | same | `{time_seconds}` | returns `steps=[]`, no error |
| **C1** | own spawn dies pre-attach, resumed log | step | `NoProgressError(progress=2)` — **wrong cause** |

**A and B are distinct defects, and the proof is that the bounds are disjoint:** an extend ceiling catches A and never B (extend is called once, then the poll loop spins); a log-silence deadline catches B and never A. **C is neither** — no bound touches it.

**The asymmetry that drives the ordering, and it is the opposite of the intuitive reading:**

```
B (hang) : sleep called 61 times  -> a caller CAN bound this today with the shipped seam
A (storm): sleep called  0 times across 1240 spawns -> a caller CANNOT
```

The repo's own tests bound B this way (`hang_guard`). Nothing can bound A.

**Why C hides:** on a resumed log the crash *is* on the log, but the dead spawn never claimed, so its `launcher.terminated` names a launch no episode answered and the claim-correlated fold drops it; `_episode_stopped` then returns the *previous* episode's `stopped`. The absence of a claim is what conceals the death.

---

## Task 1: Pin the unpinned half of the claim-gate invariant

Do this first. Today a change that provably admits two live writers passes the entire suite: `test_a_death_record_never_revokes_a_claim_whose_probe_says_alive` asserts only the `resolve() -> True` direction, and its own comment says "what nothing asserted is the direction that admits a SECOND WRITER" — but the **abstain** direction (`resolve() -> None`, a foreign host) is still unasserted.

**Files:** Test `tests/test_observables.py`.

- [ ] **Step 1: Write the failing test**, beside the existing one.

```python
def test_a_death_record_does_not_revoke_a_claim_whose_probe_ABSTAINS(open_run):
    # The sibling of the test above, for the direction that is actually reachable
    # cross-host: resolve() returns None (not our pid table), so nothing on this
    # host can distinguish a live foreign worker from a corpse. A correlated
    # launcher death must STILL not release the claim -- arming that was measured
    # to produce two live claims on the wrapper (sbatch/srun) topology.
    ch = open_run()
    foreign = "local://some-other-host/2147483646"
    ch.send({"handle": foreign}, topic="launcher.launched", request_id="L1")
    ch.send({"handle": foreign, "t": 0.0}, topic="lifecycle.started", request_id="L1")
    ch.send(
        {"reason": "killed", "exit_code": None, "signal": 9, "t": 1.0},
        topic="launcher.terminated",
        request_id="L1",
    )
    assert peek_terminal(open_run()).outcome == "killed"  # the verdict plane may say so
    assert live_episode(open_run()) == foreign  #           the claim plane must not
```

- [ ] **Step 2: Run it.** `python -m pytest tests/test_observables.py -k ABSTAINS -q` — expect PASS on master (it pins current behaviour).
- [ ] **Step 3: Mutation-test it.** In a scratch copy, make `live_episode` consult `_launcher_terminal` when `resolve` returns None. Run from the copy's own tree (`cd <copy> && python -m pytest`), **not** via `PYTHONPATH` — an editable install shadows it and produces a false green. Expect the new test to FAIL on every backend.
- [ ] **Step 4: Commit.** `test: pin the abstain direction of the claim-gate invariant`

## Task 2: `_elapsed` must not report 0.0 forever on a log with no epoch

`memoizer.py:279-280` returns `0.0` when `_epoch(channel)` is None. The docstring says time conditions are "inert until the run begins" — but if no spawn ever claims, they are **unsatisfiable forever**, which is A2's 284,014 extends. `clock()` is never called on that path, so the injected `clock` seam is dead there and the bug is untestable through the public seam.

**Decision required before implementing** (owner): mirror `history`, which already raises for exactly this condition (`memoizer.py:242-243`), **or** anchor the clock at first poll. Raising is consistent and loud; anchoring keeps `ensure` usable on a not-yet-started run. This plan assumes **raise**, matching `history`; change the steps if the owner rules otherwise.

**Files:** Modify `runstate/memoizer.py`; Test `tests/test_memoizer.py`.

- [ ] **Step 1: Write the failing test.**

```python
def test_ensure_refuses_a_time_condition_on_a_log_with_no_epoch(tmp_path):
    # No claim ever landed, so there is no run epoch to anchor a time-relative
    # window. Returning 0.0 elapsed makes the condition unsatisfiable FOREVER --
    # measured at 284,014 extends in 3s. history() already raises here; ensure
    # must not silently spin.
    launcher = runstate.ThreadLauncher()

    def never_attaches(channel, **kwargs):
        raise RuntimeError("dies before claiming")

    variant = runstate.Variant("exp", never_attaches, {"kwargs": {}})
    producer = launch_producer(launcher, variant)
    with pytest.raises(ValueError, match="epoch"):
        ensure(producer, "loss", until={"time_seconds": 1.0})
```

- [ ] **Step 2: Run it.** Expect it to hang or spin — kill it, and note that as the failure.
- [ ] **Step 3: Implement.** In `_elapsed`, raise the same `ValueError` `history` raises when `_epoch(channel)` is None, rather than returning `0.0`.
- [ ] **Step 4: Verify.** The new test passes; full suite; `mypy --strict`; `black --check runstate/ tests/`.
- [ ] **Step 5: Commit.** `fix(memoizer): a time condition on an epochless log must raise, not spin forever`

## Task 3: `max_extends` — bound the outer loop

The only defect no shipped seam can bound (0 sleeps across 1240 spawns). Counts **loops that produced no verdict**, and resets whenever the frontier advances.

**Decision required** (owner): the default. `None` (no behaviour change) is the safe ship; any number changes a legitimately long re-drive. Also: **which exception**. A brand-new `StalledError` will not be caught by `except (NoProgressError, RunFailedError)` — which is exactly what the real consumer writes, with no broad `except` — so a new sibling crashes their sweep loop instead of classifying the variant. Subclassing `NoProgressError` avoids that; the owner should rule. This plan assumes `default=None` and a `StalledError` subclassing `NoProgressError`.

**Files:** Modify `runstate/memoizer.py`; Test `tests/test_memoizer.py`.

- [ ] **Step 1: Write the failing test** — A1's shape, with a custom producer (the default `launch_producer` refuses non-step targets, so the storm is only reachable bring-your-own).
- [ ] **Step 2: Run it.** Expect `returned steps=[0,1,2]` with no error — the silent short return.
- [ ] **Step 3: Implement.** `max_extends: int | None = None` on `ensure`; a counter incremented per `extend`, reset when `_progress(channel)` exceeds the value seen at the previous pass; raise when it trips.
- [ ] **Step 4: Verify** the storm becomes one loud, reversible error; and that `test_ensure_redrives_within_one_call_to_reach_target` still passes with the default (it must, since the default is `None`).
- [ ] **Step 5: Commit.** `feat(memoizer): optional max_extends -- bound the re-drive loop`

## Task 4: `stall_timeout` — bound the inner loop

Fires on **log silence**: seconds during which `channel.last_seq()` does not advance, on the observer's own clock. Measured at 1.6 µs/poll against a 10,000 µs `poll_interval` (0.016%). Correctly keeps waiting on a live incumbent, including the long-single-step case where a step-based counter false-fires.

**Two bounds, not one:** a unified iterations-since-progress counter catches all four unbounded shapes but **false-fires on a live worker inside one long step** — the dead-vs-busy ambiguity of design §8. Frontier progress and log arrival-time activity measure different things.

**Files:** Modify `runstate/memoizer.py`, `docs/api.md`; Test `tests/test_memoizer.py`.

- [ ] **Step 1: Write two failing tests** — B1 (foreign handle, step axis) trips the deadline; a live foreign incumbent still writing heartbeats does **not**.
- [ ] **Step 2: Run them.** Expect the first to hang (bound it in the test with a counting `sleep`).
- [ ] **Step 3: Implement.** `stall_timeout: float | None = None`; track `last_seq()` and the clock in the inner poll loop; raise when silence exceeds it.
- [ ] **Step 4: Document.** `docs/api.md` currently describes `ensure` with no mention of the unbounded wait; `memoizer.py:351`'s "No hang timeout (unchanged)" is the only statement of it. Say that `sleep` is a test seam, not the intended hang bound.
- [ ] **Step 5: Commit.** `feat(memoizer): optional stall_timeout -- bound the poll loop on log silence`

## Task 5: Diagnose a crashed spawn from local evidence (Defect C)

No bound fixes this and no fold change should. `ensure` already knows enough: record `channel.last_seq()` before `producer.extend(...)`, and when the own spawn is dead with nothing appended since, say **"the spawn wrote no records"** rather than "made no progress". That distinguishes crashed-at-launch from ran-and-stalled using only the observer's own reads — and it is precisely the diagnosis the real consumer forged a `lifecycle.stopped` to obtain.

**Files:** Modify `runstate/memoizer.py`; Test `tests/test_memoizer.py`.

- [ ] **Step 1: Write the failing test** — C1's shape via `launch_producer` (reachable on the shipped path); assert the message distinguishes the two causes.
- [ ] **Step 2: Run it.** Expect `NoProgressError: made no progress toward {'step': 20} (progress=2)`.
- [ ] **Step 3: Implement.** Watermark before `extend`; in the guard, branch the message on whether any record landed after it. Keep the exception type unless Task 3's ruling says otherwise — a new type breaks the documented catchers.
- [ ] **Step 4: Verify** the cold-log path still raises `RunFailedError` (pinned by `test_ensure_surfaces_a_die_before_attach_without_hanging`), and the full suite.
- [ ] **Step 5: Commit.** `fix(memoizer): name a spawn that wrote nothing, instead of "no progress"`

---

## Incidental findings — file separately, do not fold in

1. **A pre-existing silent truncation, independent of all of the above.** A foreign wrapper exiting 0 while its worker runs on reads `COMPLETED`, and `ensure`'s pre-loop short-circuit (`memoizer.py:356-359`) returns without ever calling `extend`: `ensure(until={"step": 50})` returned `[0, 1, 2]`, no error.
2. **`docs/backlog/ensure-redrive-recoverable-terminations.md:34` documents the caller recipe as `except RuntimeError:`** — but `RunFailedError` and `NoProgressError` both derive from plain `Exception`. The documented recipe catches neither.
3. **The reverted give-up budget is distinguishable from Tasks 3–4**, on that document's own reasoning: it governed *how many deaths to auto-retry*; these bound *looping with no verdict at all*. Its justification — "the retry budget and the eyes belong to the caller" — presupposes that `ensure` returns. For the hang it never does. Record this in that entry so the ruling is not re-applied by mistake.

## Method note for whoever implements this

Three independent reviews of this area each produced at least one false green from **import shadowing**: an editable install of `runstate` at `/home/gchurchill/src/runstate` wins over `PYTHONPATH`, so a prototype in a copied tree silently tests master. Assert `runstate.__file__` from inside a test before trusting any figure, and run prototypes from the copy's own working directory.
