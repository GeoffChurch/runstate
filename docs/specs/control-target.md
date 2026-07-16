# Spec: control-target (the run's contract, on the log)

**Status:** PROPOSED 2026-07-16. Graduated from
[`../backlog/third-party-observer.md`](../backlog/third-party-observer.md) item 2 —
the one item in that ledger that is a **missing basis vector**, not a missing
surface (two adversaries working different lenses converged on it independently).
Depends on [`observer-clock.md`](observer-clock.md) (SHIPPED): a time-keyed target
needs a reliable run epoch, which `started.t`-required is what provides — the
ledger's "ship item 1 first, alone" was **structurally necessary**, not merely
prioritization.

## The problem: the log never says what the run was asked to do

> The log answers **how far it got** (`progress`) and never **how far it was
> asked to get**.

The run's target exists **only** as the caller's `ensure(until=…)` argument,
injected into the worker as a launch kwarg via `target_key="up_to"` — a hack that
reaches into the worker function's *signature*, and which `launch_producer` can
honor for `{"step": N}` **only** (`memoizer.py`: `if list(until.keys()) != ["step"]:
raise ValueError`). No convention body carries it: `Launched` is `{handle, status, t}`;
`Started` is `{handle, t}`.

Consequences, all real:

- a **launch-ignorant party cannot say "run to N"** — the TUI, a scheduler that
  outlives its runs, any third party that attached to a run it did not start;
- a **controller that dies cannot reconstruct any run's contract** — the target
  died with its process;
- a **viewer cannot show progress-toward-target** — it has the numerator and not
  the denominator;
- `ensure` **cannot express a non-step target at all** (time, `any`/`all`), because
  the plumbing is a scalar kwarg.

Like the observer clock before it, this is invisible to the party that launched the
run — that party *knows* what it asked for — and turns fatal the moment a third
party attaches.

## The model

> **`control.target {until: <Condition>}` — the run's contract, as a register.**
> Latest-wins (`channel.latest`), worker-directed, carrying exactly one concern
> (*how far*) and no launch recipe. **Discharge is derived, never recorded:** the
> target is met ⟺ `progress + 1 >= N` — the fencepost already documented on
> `observables.progress`. There is no counter-record, no positional fold, no lease,
> and no retraction verb.

Three properties make it a register rather than a command-fact:

- **latest-wins**: only the newest target is the contract; raising or lowering it is
  a fresh append that supersedes. No discharge fold — nothing "answers" a target.
- **cross-episode by construction**: a resumed episode re-drains from `seq 0` and the
  last target wins, so "resume toward the same (or updated) contract" needs no rule.
- **derived satisfaction**: met-ness is a fold over `progress`, not a record. So
  `peek_terminal` is **untouched** — a target adds no terminal tier.

### Target and stop are duals, not rivals (the ledger's fate-deciding question)

The ledger posed a dichotomy: either `target{until:{step:N}}` **subsumes**
`stop{from:{step:N}}` (deleting a primitive — a basis reduction) or the two overlap
and target **violates orthogonality**. **Both horns are false.** They are
*near-duals*, each carrying exactly one concern:

| | `control.stop {from: {step: N}}` | `control.target {until: {step: N}}` |
|---|---|---|
| kind | **transient** one-shot brake | **durable** standing contract |
| storage | a **set** — accumulates, OR-joined | a **register** — latest-wins |
| multi-party | **earliest-wins** (any party may brake) | **latest-wins** (last writer sets the goal) |
| lifecycle | self-clearing (discharged by its `stopped`) | persists until superseded |
| role on the axis | a **ceiling** (do not proceed past N) | a **floor** (you must reach at least N) |
| relaunch | never (a human is driving) | iff **short**; done iff met |

The floor/ceiling asymmetry is the crux: **a target is a floor you can fall short
of** — which is the entire reason `ensure` exists (detect the shortfall, relaunch).
A stop is a ceiling you *cannot* fall short of: you hit it or it is moot. "Relaunch
because the run died at N−5" is essential for a target and meaningless for a stop.

**The constructive subsumption attempt, and why it fails.** The strongest attack is
not "do they overlap" but *express stop as target*: "stop now" = set target to the
current frontier; "stop at N" = set target to N. It fails on three counts, the third
fatal:

1. **Stop-now is coordinate-free; target-now is not.** `stop{}` (no `from`) halts
   immediately with no read. Target-as-stop must first *read progress* to set
   `target = frontier`, and that read races the worker. A brake you cannot pull
   without asking where you are is a worse brake.
2. **Stops accumulate; targets overwrite.** A brakes at 10, B raises the goal to 20:
   as stops the brake still fires; as a register B's write silently erases A's brake.
   "Any party may stop it, and a later benign write cannot un-stop it" is *correct*
   for a brake; a register gives it away.
3. **Subsumption corrupts target's single concern.** If stops are spelled as
   target-writes, the register stops meaning "how far the run was asked to go" and
   starts also meaning "where someone momentarily braked" — a viewer would show a
   contract of 100 for a run whose goal is 1000, because someone paused to peek.
   **The pollution runs opposite to the ledger's worry:** the risk was never target
   overlapping stop; it is that forcing target to swallow stop would *destroy the
   very vector being added.*

(3) also kills the *partial* reduction — keep bare stop-now, absorb only conditional
stop. "Inspect at 100, then continue to 1000" would become lower-to-100 →
raise-to-1000, and in the window between, the recorded contract lies. **Conditional
stop earns its place even with target.**

They **compose** without fighting: target is the durable goal; stop is a transient
brake the next relaunch rides over — precisely `stop-discharge.md`'s own "the stop
wins once, the relaunch wins thereafter" (S2). Composition-without-collision is the
serendipity signature that the basis is right.

**The teaching burden this creates, stated loudly:** for *"pause this arm at N, maybe
resume later"* (the bandit case) the right primitive is **lowering the target**
(durable, resumable), **not** a stop (one-shot — the next relaunch rides over it). A
user will reach for `stop` and get transient behavior. **Durable pause = target;
momentary brake = stop.** This must be taught in the docstrings, not buried here.

### The verdict stays orthogonal

When the worker halts on target-met it emits the **default preempted `stopped`** —
*identical* to how `steps(1000)` exhaustion already exits today (`__exit__` →
`stopped(completed=False)`). The worker does **not** distinguish "halted because
`total` exhausted" from "target met" from "stop fired": all three leave the same
preempted dying breath, and the *reason* is recovered from the log — the same
"commandedness is recoverable from the `control.stop` on the log" doctrine (design
§7 / B′) that already removed `Stopped.reason`.

So **"reached target ✓" is a derivation** (`progress + 1 >= N`), never a record.
Three payoffs: `peek_terminal` is untouched; a *lowered* target cannot mislabel a
cut-short run as `completed`; and **bandit-resume stays coherent** — you never resume
a "completed" run, because target-met is a resumable frontier, not a terminus.
`completed` remains what it is today: the worker's separate opt-in claim.

## Why this is the canonical form (rubric)

- **Independence (necessity).** Nothing composes it. `progress` says how far it
  *got*. `Launched`/`Started` carry a handle, not a contract. Stop cannot express it
  (transient vs durable; see the duality). And the subscription algebra provably
  cannot: `until` bounds *firing*, not *membership* — **both spellings are refuted
  with reproductions** in the ledger (a recurring sub never expires → relaunch a
  finished run forever; a bare one-shot evaporates at step 0 → a crashed run's demand
  is gone). It is a genuinely missing vector.
- **Spanning (sufficiency).** With it, a launch-ignorant party can say "run to N" —
  the persona-defining gap. Nothing out-of-scope rides along: the target is a
  *coordinate*, not a launch recipe (see A2 below), so no process management enters
  the protocol.
- **Canonical form.** A register is the canonical shape for standing configuration
  (design §4's `latest` projection, already the substrate's own). The bound reuses the
  **existing condition-algebra** rather than inventing a `Bound`/`Target` type —
  `schedule.py`'s "kept as the wire dict by design". Satisfaction is derived from
  `progress` via the **already-documented** window fencepost, not a second
  arithmetic. Least arbitrary content available.
- **Orthogonality.** Two clean pairs. *Target vs stop*: durable contract vs transient
  brake (the table above). *Target vs progress*: **asked** vs **achieved** — the same
  two-viewpoints discipline that keeps `launcher.*` (external report) orthogonal to
  `lifecycle.*` (self report).
- **Serendipity (the payoff).** The `target_key`/`up_to` hack **dies**; `ensure`
  **generalizes past step-only targets** (the worker evaluates a Condition, so
  `{time_seconds:S}` and `any`/`all` targets work — the restriction existed *only*
  because a kwarg must be scalar); `examples/redrive` drops its `REDRIVE_UP_TO` env
  side-channel (the target is on the log the subprocess already reads); the viewer
  gets **progress-toward-target** for free; `steps(start=k)`'s `k` becomes derivable
  from `progress`; and the run-epoch rule gets **promoted to its rightful public
  home** by acquiring a second consumer (below).

  **And one found by looking at a real consumer, not by reasoning:** injecting the
  target into the worker's kwargs makes the target's *name* a reserved word in the
  user's own config namespace. `translation/runner.py` carries an explicit guard —
  `if "up_to" in cfg: raise ValueError("'up_to' is reserved (injected by
  ensure_run)")` — against a user config key colliding with the injected target.
  **With the target on the log, nothing is injected, so the collision class ceases to
  exist and the guard is deleted outright.** A hazard the hack *created* and the
  consumer had to defend against by hand.

### Corrected from the ledger: the crash-loop guard

The ledger claims the guard "falls out" for `ensure_served` — *"target unmet ∧
frontier did not advance ⟹ do not relaunch … which `ensure_served` cannot express
today"*. **This is over-claimed, and the correction is load-bearing.**
`ensure_served` gates on **leased demand**, so it drives `serve()` workers — which
tick **stepless** by construction (`tick(step=None)`), so `progress` is permanently
`None` (verified: a 3-iteration `serve()` worker yields `progress(channel) → None`,
`heartbeat.step → None`). **A step frontier can never guard them.**

The accurate, narrower claim: a **target-driven standing daemon** — driving
`steps()` workers toward a step target, which is what the refuted
demand-via-`control.subscribe` alternative was reaching for — becomes *expressible*,
because those workers have a frontier. `ensure_served`'s stepless services need a
different coordinate entirely (backoff, episode count) and **do not benefit from this
spec**. Neither the daemon nor that coordinate is in scope here.

## Semantics (scenario matrix)

| scenario | today | this spec |
|---|---|---|
| T1: `ensure(until={"step": N})` | scalar `N` injected as the `up_to` kwarg | `control.target{until:{step:N}}` on the log; the worker reads it |
| T2: `ensure(until={"time_seconds": S})` | **`ValueError`** — the producer translates only `{"step":N}` | works: the worker evaluates the Condition on the run-relative clock |
| T3: target raised mid-run (100 → 1000) | impossible | the live worker's next drain sees it and keeps going (per-tick) |
| T4: target lowered mid-run (1000 → 100) | impossible | the live worker halts at 100, preempted, **resumable** (raise to resume) |
| T5: run dies at N−5 | `ensure` relaunches (it holds `until` in memory) | any party relaunches — the contract is on the log |
| T6: target met | `steps(total)` exhausts → preempted | halts → preempted, **identical**; "met ✓" is derived |
| T7: target + a stop at M < N | n/a | stop fires at M (preempted, discharged); the next relaunch rides over it toward N — `stop-discharge` S2, unchanged |
| T8: resumed episode | `up_to` re-injected by the relauncher | re-drains from `seq 0`; the latest target wins — no rule needed |
| T9: `{step:N}` target on a **stepless** (`serve()`) worker | n/a | **nak `unsatisfiable`** — the existing "a step threshold is never satisfied for a stepless worker" rule, no new vocabulary |
| T10: malformed target (`count`, extra key, bad type) | n/a | **nak `malformed`** — parity with subscribe/stop's structural gate |

**The fencepost coincides exactly, by construction.** `steps(total=N)` yields
`0…N−1`. A target `{step:N}` is the half-open window `[0, N)`, met iff
`progress + 1 >= N` ⟺ after step `N−1`. **The two drive identically** — which is what
makes replacing the `total` kwarg with the register a faithful refactor rather than
an off-by-one migration.

## Design decisions

### D1 — The worker consults the register per-tick (not launch-time translation)

The rejected alternative (**orchestration-only**: dispatchers read the register; the
producer translates it to a scalar drive-bound at launch) kills the *kwarg injection*
but keeps a **translation shim in the producer**, which "migrate, never accommodate"
says to delete rather than preserve. Worse, it forfeits the two headline payoffs:

- **generality** requires the worker to evaluate a *Condition* — the `{"step"}`-only
  restriction exists precisely because a kwarg must be scalar;
- **live raise/lower** (bandit) requires a running worker to notice a re-issued target.

Cost is near-zero: `control.target` arrives through the **same `_drain_control` read
the worker already performs each tick**, and lands in a single slot.

### D2 — Target is a halt condition for both drivers, parallel to stop

`steps(total)` halts when: `total` exhausted **∨** a pending stop fires **∨** target
met. `serve()` halts when: demand drains to zero (`retire`) **∨** a pending stop
fires **∨** target met.

This does **not** muddle the "two protocol-visible continuation policies"
(`specs/service-worker.md`): the *policies* — the launch contract's `total`, the
log's leased demand — are what make a driver *continue*; a target, like a stop, is a
*bound* that applies to either. Stop is already a halt condition for both; target
joins it. Uniformity buys the stepless case for free: a `{step:N}` target on a
`serve()` worker naks `unsatisfiable` under the **existing** rule (T9), with no
policy carve-out and no new nak reason.

### D3 — `steps(total)`'s local arg survives

The register is the *orchestrated, updatable* target; `total` is the *local default*
for a self-directed `for step in w.steps(1000)`, which must not have to send itself a
control message. They coexist, halting at whichever comes first — exactly the
coexistence stop already has with `steps(total)`. A run with no target register
behaves **precisely as it does today**: `steps(None)` remains "step until commanded
stop", now also "…or until a target is met" — a strict addition.

### D4 — The target's time axis is run-relative, not episode-local

`{time_seconds: S}` means **seconds since the run began** — `now − run_epoch`, where
the epoch is the first `lifecycle.started`'s `t` — matching `memoizer._elapsed`'s
gap-inclusive `clock() − epoch`, **not** stop's episode-local `now − registered_at`.

Decisive: a durable contract must span episodes. On episode-local time a 1h target
restarts its clock on every resume, so a run crashing every 50min **runs forever and
never converges**. (Stop keeps its episode-local re-anchor — that is its spec'd
at-least-once posture, and no relaunch flap is reachable through a transient brake.)

**This forces a promotion.** The epoch rule lives today as `memoizer._epoch`
(private), whose docstring already names itself *"the ONE epoch reader"*. The worker
is its **second consumer** — the trigger to move it to its rightful home as public
`observables.run_epoch(channel)`, called by both. Re-deriving it worker-side would be
exactly the F7 re-derivation smell the codebase already litigated. Definition is
unchanged and shared verbatim: the **first** `lifecycle.started` by `seq`, its `t`
(not `min(t)` — matching the existing convention under skew). On a first episode the
run's epoch is the worker's own `started.t`, already on the log by the time it is read.

**The edge this opens, stated rather than discovered later.** A time-keyed target is
now evaluated by **two clocks against one epoch**: the worker's (`self._now()`, which
decides when to *halt*) and `ensure`'s consumer poll-clock (`memoizer._elapsed`, which
decides when to stop *relaunching*). Skewed apart, they disagree — and the disagreement
has a shape: a worker whose clock runs **ahead** halts on target-met while `ensure`
still reads the window open, so `ensure` re-extends, the worker halts again without
advancing, and the own-spawn no-progress guard raises **`NoProgressError`**. That is
the guard working — it refuses to spin — but the *diagnosis* is a clock, not a stuck
worker, so it must be named here. It is **not a regression** (time targets raise
`ValueError` today, so there is no behavior to preserve) and it is unreachable
same-host, where both reference launchers put `ensure` and the worker on one clock;
it bites only a cross-host worker (`PostgresChannel`) on a time-keyed target. Step
targets — the overwhelmingly common case — are clock-free and wholly unaffected.

### D5 — No retraction verb

Latest-wins with no "clear": you may raise or lower, never remove. Removal has no use
case — an orchestrated run always has a finite contract, and the unbounded model is
`serve()` (leased demand), not a target-less `steps()`. This is `stop-discharge`'s A7
reasoning (no `control.unstop`): do not add a retraction for something that does not
need one.

### D6 — The convention is renamed `subscription` → `control`

`protocol/subscription-v0.2.schema.json` ("subscription convention") already pins
`control.stop` — not a subscription — and `control.target` deepens the misnomer. The
argument is not tidiness but a **rule**: every other convention schema is named after
its topic family (`lifecycle-*` pins `lifecycle.*`, `launcher-*` pins `launcher.*`,
`value-*` pins `value`). `subscription` is the **only** one that breaks it. Renaming
to `control-v0.3.schema.json` restores a rule that holds everywhere else, at the
cheapest moment it will ever be (the bump is happening anyway). Cost is small and
fully enumerated: the schema's `$id`/title, one docstring in `schedule.py`,
`tests/test_schema.py`'s validator, and design-doc prose.

## Where the alternatives fall short

**A1 — Demand-via-`control.subscribe`** (a step-conditioned lease as durable demand,
a daemon translating `until` into the launch target). **DEAD in both spellings, in
opposite directions, each reproduced** (ledger): a *recurring* sub (`every` +
`until:{step:N}`) never expires — `steps(N)` yields `0…N−1` and the expiry gate is
`step >= N`, so no counter-record is written and demand outlives a *clean completion*
(a daemon would relaunch a finished run forever); a *bare one-shot* (`until:{step:N}`)
fires once and evaporates at step 0, so a crashed run's demand is gone and the daemon
never revives it — the one thing it exists for. `until` bounds **firing**, not
**membership**. It is also an **audience conflation**: the worker reads the message as
a sampling schedule while the daemon reads it as a launch order.

**A2 — `control.launch` carrying the launch recipe (cmd/env).** Rejected on the
**meta-constraint**: it bakes process management into the protocol ("the library
transports messages, not processes") and turns any channel with write access into a
**remote-code-execution surface**. The recipe stays in a trusted, off-log table. *(The
instinct that a message was needed was right; the payload was wrong — it is the
target, not the recipe.)*

**A3 — A standing daemon as the fix.** `ensure_served` gates on `live_demand ∧ no live
episode` and **nothing else** — no failure gate (unlike `ensure`, which has both
`RunFailedError` and `NoProgressError`). Promoting the caller-invoked recipe to an
unattended daemon converts that into a **crash-loop generator** at poll cadence. Not
this spec's business; see the correction above for why item 2 does **not** hand
`ensure_served` a guard.

**A4 — Target as a launch-time kwarg, kept (the status quo).** It *is* the bug: the
contract lives in the caller's head, dies with the caller's process, and is invisible
to every third party. It also caps `ensure` at step-only targets forever.

**A5 — A new terminal record for target-met (`lifecycle.target_met`).** Fails
Independence — met-ness is a pure fold of `progress` against a register already on the
log, so the record is derivable and therefore redundant. It would also break
bandit-resume (a terminal you can un-reach by raising the target is not a terminal)
and add a tier to `peek_terminal` for zero information.

**A6 — Unify stop and target as `control.halt{at: C, verdict: …}`.** Forces one
storage model on both, but stop genuinely wants a **set** (accumulating, OR-joined)
and target genuinely wants a **register** (exactly one contract). A verdict parameter
also cannot express "relaunch if short" — inherent to a floor, meaningless for a
ceiling. Strictly worse than two primitives.

## Implementation

**Protocol (`protocol/`)** — `git mv subscription-v0.2.schema.json
control-v0.3.schema.json`; `$id` → `…/control-v0.3.json`; title → "runstate v0.3
control convention"; add `"control.target"` to the `control.*` topic enum; add a
`Target` `$def`:

```json
"Target": {
  "type": "object", "additionalProperties": false, "required": ["until"],
  "description": "control.target is the run's contract: a register (latest-wins) carrying how far the run was asked to get. `count` is excluded -- there is no driven count axis.",
  "properties": {"until": {"$ref": "#/$defs/Condition"}}
}
```

`Condition` (**count-free**) is the right `$ref`, not `UntilCondition`: a count
threshold is not a drivable target — the schema now enforces at the wire what
`memoizer._reject_count` enforces in Python. `request_id` stays **optional**
traceability (as for `stop`): a register is not a correlated operation.

**Vocabulary** — `Topic.CONTROL_TARGET = "control.target"` on the closed enum. The
body stays a **wire dict**, not a dataclass: `control.*` bodies are condition-algebra
dicts modelled in `schedule.py` (payloads.py's docstring is explicit that the
schedule is "deliberately NOT here"). Add `malformed_target(body)` beside
`malformed_stop_trigger`: keys ⊆ `{until}`, `until` required, a count-free Condition.

The **unsatisfiability gate is condition-level, not schedule-level**: `is_unsatisfiable`
takes a *schedule* (`from`/`every`/`until`) and is the wrong shape here. The target's
`until` is a bare Condition, so the T9 check is `schedule._satisfiable_stepless(until)`
— which exists and is exactly the rule ("could this ever be satisfied with no step?").
It is private today; the worker's target branch is its **second consumer**, so promote
it to a module-public `satisfiable_stepless` rather than reach through the underscore.

**Observables** — promote `memoizer._epoch` → public `observables.run_epoch(channel)`
(definition verbatim; D4). `memoizer._epoch` becomes a call to it.

**Worker** — `self._target: Condition | None` (a slot, not a set); a
`Topic.CONTROL_TARGET` branch in `_handle_control` (structural gate → `malformed`
nak; stepless-unsatisfiable → `unsatisfiable` nak; else assign — latest-wins falls out
of seq-ordered drain, with **no** discharge floor and **no** positional answer fold);
`_target_met(step)` applying the window fencepost:

```python
def _target_met(self, step: int | None) -> bool:
    if self._target is None:
        return False
    window_step = step + 1 if step is not None else None   # progress+1; None stays None (stepless)
    return satisfied(self._target, step=window_step,
                     time_seconds=self._run_elapsed(), count=0)
```

checked in `steps()` and `serve()` after `tick`. `tick()`'s return and `stop_pending`
keep meaning **the commanded-stop decision** — target-met is a *distinct* level, so
the two are not conflated; expose it as a `target_met` property (the `stop_pending`
shape).

`_run_elapsed()` is `self._now() - run_epoch(self._ch)` (D4) — worker-side, and
deliberately **not** named `_elapsed` to avoid colliding with `memoizer._elapsed`,
the consumer-side reader of the same axis. The epoch is resolved **lazily and
memoized**: only a time-referencing target ever reads it, so the common step-target
path adds **zero** I/O, and a step-only worker never touches the clock at all.

**The null-epoch rule, which must match `memoizer._elapsed` exactly.** `run_epoch`
returns `Optional[float]` — it yields None for a junk-typed `t` on a hand-composed or
foreign `started` (the measurement rule: junk earns no epoch). The worker's own claim
always carries a valid `t`, but the epoch is the **first** started by `seq`, which a
prior foreign episode may own — so the worker can face a null epoch even though it is
itself well-formed. It then does what `memoizer._elapsed` already does: **treat
elapsed as `0.0`, making time-keyed targets inert** (step-keyed targets are
unaffected). This is not a free choice — the two readers of one axis must agree, or a
worker and its `ensure` would disagree about whether the same target is met. One rule,
both sides, spelled once in `run_epoch`'s docstring.

**Memoizer** — `ensure` writes the register before extending, **iff it differs from
the current latest** (a write per poll-loop iteration would spam the log);
`_LaunchProducer.extend` drops the kwarg injection *and* the step-only `ValueError`;
`launch_producer`'s `target_key` param is **deleted**. `ensure(until=…)`'s public
signature is unchanged — only the plumbing moves. `extend(until)` keeps its parameter
for custom producers (ray/submitit workers that are not the reference `Worker`).

**Examples** — `reuse/driver.py`: drop the `up_to` param, `w.steps(start=start)`.
`redrive/worker.py`: drop `REDRIVE_UP_TO`; read the target from the log (the
demonstration that the env side-channel is gone).

## Migration

**No log migration at all.** A run with no `control.target` register behaves exactly
as today (D3). This spec *adds* a topic; it changes no existing body — unlike
observer-clock, there is nothing on disk to rewrite.

**No consumer schema coupling.** Verified: neither consumer references the schema
files or `jsonschema` — the stack is validated only by runstate's own
`tests/test_schema.py`. So the D6 rename has **zero blast radius** outside this repo.

The code migration is the worker-function signature: `def worker(channel, *, up_to)` +
`w.steps(total=up_to)` → `def worker(channel)` + `w.steps()`. Surveyed against the
real consumers (read-only), it splits three ways:

- **Forced — `translation`.** `runner.py:32` calls `launch_producer(launcher, variant,
  target_key="up_to")`; deleting `target_key` breaks it at the call. Its `ensure_run`
  harness, its worker signatures, and its reserved-kwarg guard all migrate together
  (the guard is *deleted*, not ported — see Serendipity).
- **Not forced, but should — `mycooc`.** It has its **own** subprocess producer
  (`run_experiment.py:498`), so nothing breaks: it may keep plumbing its own `up_to`
  via env/CLI, with the register on the log beside it. That redundancy is exactly the
  wart "migrate, never accommodate" forbids — and the block's own header reads
  *"Phase-4 subprocess producer for `runstate.ensure(until=)` (**STEP-AXIS ONLY**)"*.
  The restriction this spec lifts is one a real consumer **documented in a comment**
  because it had to live with it.
- **Untouched — every self-directed call site.** `examples/minimal/worker.py:19`
  (`steps(total=50)`), `mycooc/analyze_run.py:1406`, `translation/tests/test_workers.py:58`
  all pass a *local* total and never see a register. **This is D3 earning its keep**:
  keeping the local arg is what confines the migration to the orchestrated seam
  instead of every loop in three repos.

Both consumer repos are read-only and under concurrent development, so **their
migration requires the owner's explicit authorization**, as the observer-clock one
did. Per "no legacy compatibility": migrate and delete the old path — no dual-read,
no `target_key` deprecation shim.

## Non-goals

- **A retraction verb** (D5) and **a target-met terminal record** (A5).
- **The launch recipe on the log** (A2) — the meta-constraint holds.
- **A standing daemon**, and **a crash-loop guard for `ensure_served`** — the latter
  is not derivable from this spec (see the correction), and neither is in scope.
- **Any change to `peek_terminal`, `RunResult`, or the `Outcome` vocabulary.**
  Target-met is a derivation, never a verdict.
- **The emission filter** (`from`/`every` on `ensure`) — still deferred
  (`../backlog/memoizer-index-algebra.md`). `until` is the run *bound*; this spec
  moves *that* onto the log and nothing else.

## Tests (TDD targets)

- **Schema:** a conforming `control.target` validates; `count` in `until` **rejects**;
  an extra body key rejects; a missing `until` rejects. `tests/test_schema.py`'s
  scenario emits `control.target` and validates against `control-v0.3`.
- **Fencepost equivalence (the load-bearing one):** `target{step:N}` and
  `steps(total=N)` produce **identical** trajectories (`0…N−1`, `progress = N−1`).
- **T2 generality (the payoff):** `ensure(until={"time_seconds": S})` works
  end-to-end — today it raises `ValueError`. Likewise an `any`/`all` target.
- **T3/T4 live update:** target raised mid-run → the worker continues past the old
  bound; lowered mid-run → halts at the new one, `preempted`, and a **raise + relaunch
  resumes** (the bandit round-trip).
- **T6 verdict:** target-met leaves a `preempted` `stopped` — *not* `completed`; and
  `peek_terminal` is byte-identical to the `steps(total)`-exhaustion case.
- **T7 composition:** target `N` + stop at `M < N` → halts at `M`; the stop is
  discharged; the next relaunch runs to `N` (`stop-discharge` S2 still green).
- **T8 cross-episode:** a resumed episode drives to the *current* target, including
  one written while it was down.
- **T9/T10 naks:** `{step:N}` on a `serve()` worker → `unsatisfiable`; malformed
  target → `malformed`; both leave the worker running (one bad request is never fatal).
- **D4 time axis:** a `{time_seconds:S}` target spans episodes — the clock does **not**
  restart on resume (the regression that would let a crash-looping run never converge).
- **D4 null epoch:** a junk-`t` foreign `started` at `seq 0` → the worker's time-keyed
  target is **inert** and its step-keyed target still fires; and the worker's verdict
  matches `memoizer._elapsed`'s on the same log (the one-rule-both-sides guarantee).
- **Regression:** `launch_producer` no longer accepts `target_key`;
  `tests/test_memoizer.py`'s `up_to`-injection test is **rewritten**, not kept beside
  a shim.
- **Log hygiene:** `ensure`'s poll loop writes the register **once**, not per iteration.
