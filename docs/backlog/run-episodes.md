# Run episodes (a `run_id` hosts many worker episodes)

**Status:** design direction, not built. Unifies three deferred items into one
model: lazy-launch (§12.1), the lifeline service-worker, and the
"completed-but-extendable" gap surfaced by mycooc (see the cli-status /
Store backlog and the mycooc adoption assessment).

## The idea

Today runstate implicitly treats a `run_id` as a *single* worker run: one
`lifecycle.started … stopped`, and `peek_terminal` reports the latest `stopped`
as terminal. Generalize that:

> A `run_id` is a **durable log that can host multiple worker *episodes*.**
> Episode 1 attaches → `started … stopped`; later, episode 2 attaches to the
> **same** `run_id` (same channel, same log) → `started … stopped`, resuming from
> the run-keyed state it left behind.

This rests on a property runstate already has: `open_channel(run_id, root)` is
deterministic (same `run_id` → same channel), and the channel is **durable and
worker-liveness-agnostic** — you can address a run before any worker exists, and
messages wait in the log until a worker attaches and drains them.

## What it dissolves

**"Completed-but-extendable."** mycooc re-opens a DONE run when the step budget
rises (resume from checkpoint, run more). runstate had no "re-openable terminal."
Under run episodes there's nothing to re-open: a `stopped` ends *an episode*, not
the *run*; extending is just a new episode appending to the same log. No new
"terminal" concept — only episode boundaries.

**Idempotent relaunch.** "Launch on demand if not already running" is exactly the
lazy-launch single-spawn guard (§12.1): before spawning, check liveness / a
`launcher.launched` spawn-intent record **inside the channel lock**, and no-op if
a live episode exists. That guard *is* idempotent relaunch ("re-run = no-op if
alive"). Designing episodes forces solving §12.1, and mycooc's PID-file relaunch
idiom comes for free.

## One primitive, two policies

Episodes are the shared primitive. The *trigger to relaunch* and the *condition
to stop* differ by workload:

- **Service worker (lifeline-driven).** Exists to serve subscriptions. Lazy-launch
  on `control.subscribe`; **reap when the subscription ref-count drains to zero**
  (no demand → sleep); a later subscribe relaunches it. Right for on-demand
  compute (inference, a metric server). "Resume" may be trivial (recompute) or a
  reload, per the worker.
- **Autonomous run (target-driven).** Runs to its own target (e.g. `max_steps`)
  **regardless of who is watching** — you must NOT gate training on subscriptions
  (a disconnected dashboard must not stop training). "Extend" = explicitly
  relaunch the same `run_id` with a higher target; the worker resumes from its
  run-keyed checkpoint and runs to the new target.

The distinction is policy on top of episodes; the substrate/lifecycle/launcher
machinery is identical.

## The lifeline reap needs no grace period

Naively, "reap when `_subs` is empty" looks like it would reap a freshly-launched
worker (empty `_subs` on tick 1). It doesn't, because launch is
**subscription-triggered** and the channel is durable: the `control.subscribe`
that caused the launch is already in the log, so the worker's first
`_drain_control` (which runs *before* the reap check in the tick) registers it —
the worker is never empty-at-startup. "No pending subscription" genuinely means
"no reason to exist," so idling immediately is correct, not a bug to suppress.

The only transient-zero is a **keepalive refresh** (unsub `s1` + sub `s2` meant
to be atomic): the existing **register-before-reap** invariant (§6 — drain the
whole tick's control before the reap check) means `_subs` is never observed empty
mid-refresh. A refresh genuinely separated in time (demand really ended, then
returned) reaps then relaunches — correct service behavior. So the reap is one
clause evaluated once per tick after the full drain, like the commanded-stop
check.

**Precondition (documented, not enforced):** service-worker launch is
subscription-driven (automatic under lazy-launch — the trigger sub *is* the
pre-launch sub).

## Episode-aware liveness (the required refinement)

Multi-episode runs make `peek_terminal` / the Watcher episode-aware. Today
`peek_terminal` returns `latest("lifecycle.stopped")`; for ep1(`started…stopped`)
then ep2(`started…`) it would wrongly report ep1's stop as terminal while ep2 is
live. Fix: **a `stopped` is terminal only if no `lifecycle.started` follows it**
(by `seq`). Same for the Watcher's tiers. Small, but load-bearing for the model.

## Resume is the worker's job; runstate gives no directory

runstate manages the *channel* (per `run_id`); it does **not** give the worker a
working directory or checkpoint location — that's the worker's/launcher's
concern. So "new dir vs resume" is a **convention**, not a runstate setting:
relaunch with the same `run_id`, and have the worker key its checkpoint off
`run_id` (or a launcher-provided workdir). Reuse the `run_id` → the worker can
resume (and can even read its own past emissions from the log); mint a fresh
`run_id` → a fresh run. The reference `Worker` is a fake loop with no checkpoint;
a real worker owns resume.

## Built vs. not

- **Built:** stable `run_id`→channel (durable, liveness-agnostic); the worker
  tracks `_subs`; register-before-reap is the loop invariant.
- **Not built:** lazy-launch-on-demand + the single-spawn guard (§12.1); the
  lifeline reap (stop on zero subs); episode-aware `peek_terminal`/liveness;
  worker resume (out of scope — worker's job, but the convention needs writing).

## Open questions

- Episode boundaries in the schema/conventions: is "episode" implicit (a
  `started` after a `stopped`) or does it earn an explicit marker / id?
- Does the lifeline ref-count live in the reference `Worker` as an opt-in *mode*,
  or in a separate service-worker recipe? (Keep autonomous runs unaffected.)
- How does episode-aware liveness interact with `RunResult` for a run that has
  finished its *last* episode vs. is between episodes (idle, may relaunch)?
- Artifact/checkpoint location convention: a launcher-provided workdir keyed by
  `run_id`? (Stays app/convention-level; runstate transports messages, not files.)
- mycooc is the validating use case for the autonomous-extend half; an on-demand
  metric/inference server is the validating use case for the service half.
- **Control cursor across episodes.** ~~A fresh episode's worker drains
  `control.*` from `seq` 0, which correctly *re-derives standing subscriptions*
  but would also *replay one-shot commands* (a prior episode's `control.stop` →
  the new episode stops immediately).~~ **Resolved 2026-06-09 — specced and shipped
  as the discharge fold: [stop-discharge](../specs/stop-discharge.md)**, which
  carries the rule and the refutation of this entry's original episode-fencing
  sketch (its A2; the once-xfail pin now passes). Cursor persistence stays a
  §12.5 *efficiency* item, orthogonal to correctness.
