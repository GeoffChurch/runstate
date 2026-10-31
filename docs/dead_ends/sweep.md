# Dead end: `sweep` and `Variant` — a batch loop as library surface

**Deleted 2026-10-30** (owner ruling, during layer 2's live layer,
`../plans/2026-10-28-layer2-live.md`). `sweep` and its run record `Variant` are
gone from the 0.4.0 live layer; 0.3.0's `runstate/sweep.py` leaves with the
cutover (plan Task 19), and nothing replaces it. **What survives is the loop, as a
recipe** — see *Where the live work went*.

## What it proposed

`sweep(variants, launcher, *, watcher, resume, stop_on_failure, on_event)`: for each
`Variant(run_id, target)`, in order, launch the target into its run and
`watcher.wait` for its verdict, returning one `RunResult` per run reached.
`resume` skipped a run whose log already held a verdict; `stop_on_failure` halted
once a verdict had an outcome in `Outcome.failures()`. It shipped in v0.2
(`design-v0.2.md` §9) as the **batch-sweep persona's entry point**, and
`window-closed.md` banked it as "not sugar" on that ground.

## Why it died

**It fails the orthonormal-basis rubric** (`CLAUDE.md`, "Design rigor"):

- **Independence.** It is about ten lines composed from the public `Launcher` and
  `Watcher` (the loop below), and no protocol rule recognizes it: no record, read
  or decider depends on whether a run was launched by a sweep. The rubric's test
  for a helper — does the protocol itself need to recognize it, the way
  `lifecycle.stopped` drives `peek_terminal` — answers no.
- **Opinion creep.** Every one of its parameters is workload policy. Whether
  `resume` should skip only completed runs, or preempted ones too (an extendable
  exit a larger target would continue from), was an open question the protocol
  has no basis to decide. So is stop-on-failure, and so is sequential against
  parallel. `CLAUDE.md` already lists "full process management" and "run config
  management" as likely never in the core; a batch loop with a resume policy is
  both.
- **It cost a re-thread per rule change.** Every library rule that moved under it
  had to be carried through it: the typed `Launcher[T]` and its target records
  (plan, open question 18), the required `watcher`, `resume` and `stop_on_failure`
  (open question 13), the set-valued `outcomes`, and batch 8's two Watcher fixes,
  each with a sweep test beside the Watcher's own (a held handle reads its launch's
  verdict; the startup-death catch).

**Nobody uses it.** A census of every repo under `~/src` (2026-10-30):

- no consumer calls runstate's `sweep()` — not mycooc, translation, runstate-tui
  or reblock. mycooc's "sweep" hits are its own hyperparameter sweeps, and
  runstate-tui's are run names;
- no in-repo example calls it;
- `Variant`'s one user is translation's `translation/runner.py:31`, which builds one
  only because 0.3.0's `launch_producer` takes one.

The census bounds priority, not soundness (`CLAUDE.md`, "But a census bounds
APPLICABILITY and COST — never soundness"): it says deleting `sweep` breaks no
one. The refutation is the rubric argument above.

## The root error (one line)

**A persona's entry point was mistaken for a primitive:** the batch persona needs
*a* loop, but which runs it skips, when it halts and how many it runs at once are
that persona's choices, so the loop belongs in its code, built from the primitives
the protocol does recognize.

## What survives (genuine — keep)

- **The loop, as a recipe.** Over the 0.4.0 surface it is:

  ```python
  results = []
  for run_id, target in runs:
      watcher.add(launcher.launch(run_id, target))
      result = watcher.wait(run_id)
      results.append(result)
      if result.outcomes & Outcome.failures():
          break
  ```

  A resume check is `peek_terminal` through `launcher.attach_channel`, a
  `RunNotFound` meaning never launched; which verdicts count as done is the
  caller's to decide.
- **`Outcome.failures()`**, which `ensure` tests too.
- **What sweep's tests found in the Watcher**, now stated for every caller in
  `../specs/commits-and-lineage.md` §5 and pinned by the Watcher's own tests: a
  Watcher holding a launch's handle reads that launch's verdict, so a relaunch of a
  run that already has an exit never reads the previous claim's; and a worker that
  hangs before it claims is presumed dead past `heartbeat_timeout` (the
  startup-death catch).
- **`Launcher[T]` typed by its target record** (open question 18), which the
  deciders and `launch_producer` keep.

## Where the live work went

- **`launch_producer(launcher, run_id, target)`**, each argument required: the
  producer takes the run and its target record directly, so `Variant` has no user
  left. translation's runner builds the `ThreadTarget` itself
  (`../specs/commits-and-lineage.md` §8).
- **Cross-run coordination** stays the Watcher's `broadcast` and `wait_all`.
- **The backlog's "`sweep` under placement"** one-liner (its one-root assumption
  needed a per-rid wrapper under `../specs/run-id-recipe.md`'s placement) leaves
  with it: a caller's own loop opens each run where its placement puts it.

## What would revive it

A consumer whose orchestration needs a sweep that **the protocol itself must
recognize**: a record, read or decider that has to know a set of runs was launched
as one sweep. A consumer that merely wants the loop writes the recipe above.
