# Spec: control-target (the run's contract, on the log)

**Status: REWORKING (2026-07-16).** Proposed and **refuted the same day** by a
four-lens adversarial pass (protocol/basis; build-it-for-real; concurrency/failure;
consumer/bandit — independent, no shared context). The **diagnosis stands and the
vector is real**; the **solution below does not ship**. This document is now the
decision trail: §"The adversarial pass" is what it found, §"What survived" is what
the rework must keep, and §"The narrowed question" is what the rework must answer.
The proposed model is preserved verbatim *as the refuted v1* — read it as history,
not as instruction.

The pass's most instructive result: an adversary **implemented this spec exactly as
written and the suite went green** — 723 passed vs a 725 baseline, the delta being
exactly the two tests the migration obsoletes. **The design is implementable, its
tests pass, and it is wrong.** Green tests do not validate a design whose concerns
are overloaded.

Graduated from [`../backlog/third-party-observer.md`](../backlog/third-party-observer.md)
item 2. Depends on [`observer-clock.md`](observer-clock.md) (SHIPPED).

---

## The problem: the log never says what the run was asked to do

*(This section survives the pass intact. All four lenses affirm the diagnosis; the
protocol lens explicitly refuted the meta-constraint attack — `control.target` is
**not** workload-opinion creep, because `step` is already a protocol coordinate
(`Heartbeat.step`, `observables.progress`, the condition-algebra, `stop{from:{step:N}}`).
The protocol already holds *how far it got*; *how far it was asked to get* is the same
coordinate from the other viewpoint.)*

> The log answers **how far it got** (`progress`) and never **how far it was
> asked to get**.

The run's target exists **only** as the caller's `ensure(until=…)` argument,
injected into the worker as a launch kwarg via `target_key="up_to"` — a hack that
reaches into the worker function's *signature*, and which `launch_producer` can
honor for `{"step": N}` **only**. No convention body carries it.

Consequences, all real: a **launch-ignorant party cannot say "run to N"**; a
**controller that dies cannot reconstruct any run's contract**; a **viewer cannot
show progress-toward-target**; and `ensure` **cannot express a non-step target at
all**, because the plumbing is a scalar kwarg.

**Two consumers independently corroborate that the target is a coordinate, not
identity** — mycooc's `run_id.py`: *"the target must NOT be part of identity …
Confirmed target-independent"*; and both wrote step-axis-only decomposers by hand,
mycooc's under a header that literally reads **"STEP-AXIS ONLY"**. The restriction is
real and a real consumer documented living with it.

---

## The adversarial pass: what it refuted

Findings are grouped by root cause, not by agent. Every one below is a **reproduction
or a code reading**, not an argument. Counts note how many independent lenses reached
it — convergence between agents that shared no context is the strongest signal here.

### R1 — `ensure` writes a *read window* into a *contract* slot **[blocker · 3 lenses]**

`ensure(until=…)` is a **read window** ("Return `name`'s series for the window
`until`"). `control.target{until}` is **the run's contract**. Two concerns. v1 plumbed
the first into the second (*"`ensure` writes the register before extending, iff it
differs"*). Reproduced:

```
seq  1: [A] control.target{until:{step:1000}}      <- the contract (a TUI; launch-ignorant)
seq  2: worker claims, drains, _target={step:1000}
        [B] ensure(producer, "loss", until={"step": 8})   # B only wants 8 points to READ
seq 13: [B.ensure] control.target{until:{step:8}}  <- "iff it differs" -> WRITE
seq 14: worker drains, latest-wins -> _target={step:8} -> halts
RESULT: progress=7, peek_terminal=preempted, latest target={step:8}
```

**Nothing on the log remembers 1000 was ever asked for** — D5 forbids retraction, A5
forbids a shortfall record, T6 makes the verdict byte-identical to a crash-preempt.
And it is a **regression**, proven by control arm: today `extend` → `relaunch_if_needed`
→ live → `foreign_episode`, the kwarg never reaches the live worker, and A's run
finishes at 999. **v1 converts a per-launch parameter into shared mutable global state
and has a read-shaped, cache-shaped API write it on every miss.**

Corollary (mycooc): `ensure` writes **before** `extend`, i.e. **upstream of** the
producer's own `if live_episode(...): return foreign_episode(...)` gate — the gate that
exists to make a losing dispatcher harmless. A loser now halts the winner's worker
before politely deferring.

### R2 — No actuator: the Spanning claim is circular **[blocker · 3 lenses]**

*"With it, a launch-ignorant party can say 'run to N'"* — **only while an episode is
live.** A live worker is the only thing in the library that reads the register.
`relaunch_if_needed` doesn't; `ensure` gates on its own in-memory `until`; A2 (recipe
on the log) is rejected; A3 (daemon) is rejected **and** listed under Non-goals. On a
cold run the write is inert, and the next real driver overwrites it unread.

> The register is **write-effective only in the state where the launching party is
> already present** — which is the state the spec says doesn't need fixing.

The bandit round-trip inherits this: "raise to resume" has **no resumer**. The
circularity to break: the persona's gap needs a daemon; A3 refutes the daemon for
lacking a flap guard; the flap guard was supposed to *be* item 2.

### R3 — The taught park idiom commits the pollution v1 uses to reject subsumption **[blocker · 2 lenses]**

v1 rejects subsumption via objection (3): spelling stops as target-writes makes the
register mean *"where someone momentarily braked"* as well as *"how far the run was
asked to go"* — *"the pollution would destroy the very vector being added."* Fifteen
lines later it teaches: *"for 'pause this arm at N, maybe resume later' the right
primitive is **lowering the target**."*

**These are the same operation.** After a park, the goal `1000` is **nowhere on the
log** — verbatim v1's own problem statement (*"a controller that dies cannot
reconstruct any run's contract"*). No fold recovers it: `latest` gives the park; `max`
over history is wrong for a legitimate permanent lowering. And objection (1) lands too
— parking requires reading `progress` then writing the frontier, i.e. *"a brake you
cannot pull without asking where you are."* Measured: park at 106 while the worker
advanced to 112 → **recorded contract 106, achieved 112, progress bar 107%**.

**v1 cannot hold that a target-write-to-frontier is pollution in §subsumption and the
correct idiom in §teaching-burden.**

### R4 — The worker cannot read its contract before it acts **[blocker · 3 lenses]**

Structural: `_drain_control` runs only inside `tick()`, and `tick()` only **after** the
body executes a step. Two manifestations:

- **Consumers need the scalar before the loop.** 4 of 5 `translation` workers are
  batch-compute-then-emit: `hyps = translator.translate(..., stop=up_to)` runs *before
  `runstate.Worker(channel)` is constructed*. `up_to` is a **data-slicing scalar**, not
  a loop bound. v1's stated migration (`def worker(channel)` + `w.steps()`) is wrong for
  all of them, and D1's live-retarget payoff is **fatal** there: raising the target
  mid-run makes `hyps[i]` throw `IndexError` → `errored` → `RunFailedError`. **A third
  party's benign "run further" kills the run.**
- **The worker overshoots an already-met target.** Reproduced: episode 1 → progress 4;
  target `{step:5}` (window `[0,5)`) is MET; episode 2 **runs step 5 anyway** — outside
  the window — because `_target` is None until the first drain, which is after the first
  step. Not fixable by moving the check; it needs a prologue drain, or `Worker.target`
  resolved at claim time from `__init__`'s existing read (the same-read fusion).

Both consumers already hand-wrote the missing primitive. A consumer reaching around the
vocabulary into the raw log is what this ledger's item 1 calls *"the strongest possible
evidence of a missing primitive"* — and v1 **deletes both decomposers and provides no
replacement**.

### R5 — The no-progress guard has **two independent holes** → relaunch storm **[blocker · 2 lenses]**

D4 claimed the clock-skew edge resolves to `NoProgressError` — *"that is the guard
working."* **The guard does not fire.** Two mechanisms, each verified:

1. **`float("inf")`.** `memoizer.py`'s third clause evaluates `satisfied(until, …,
   time_seconds=float("inf"), …)`, which is True for any bare time atom or any `any`
   containing one → `not satisfied(...)` is False → **guard structurally dead**. Verified:
   ```
   {'step': 10}                            -> CAN FIRE
   {'time_seconds': 5}                     -> STRUCTURALLY DEAD
   {'any': [{'step':100},{'time_seconds':5}]} -> STRUCTURALLY DEAD
   {'all': [{'step':100},{'time_seconds':5}]} -> CAN FIRE
   ```
   That `inf` was safe **only because time targets were impossible**. v1 removes the
   `ValueError` that made them impossible, converting a dead branch into a live hole
   **for exactly the target class it newly enables.**
2. **The `+1` overshoot (R4).** Each relaunch yields one step, so `progress` advances,
   so `_progress(channel) <= before` is False → guard never fires.

Measured under two rival writers: **373 spawns in 3 seconds**, 748 target records,
progress creeping ~1/relaunch, no raise. With `LocalLauncher` that is 373 subprocesses.
And the operator's pause did not hold (pinned at 100, run advanced to 471): **not "last
writer sets the goal" — the writer with the fastest poll loop wins.**

### R6 — The 2×2 has an empty cell, and it is what the bandit wants **[major · 1 lens, structural]**

v1's own duality table declares **kind** ∈ {transient, durable} × **role** ∈ {ceiling, floor}:

| | ceiling (*do not pass N*) | floor (*reach at least N*) |
|---|---|---|
| **transient** | `control.stop` | vacuous |
| **durable** | **— empty —** | `control.target` |

The **durable ceiling is park/suspend**: *do not proceed past here, across relaunches,
until I lift it.* That is what the bandit means, what a quota guard means, what an
operator's "hold this run" means. v1 never names the cell and makes the durable **floor**
impersonate the durable **ceiling** — R3 is the price. The tell was sitting in D5:
*"removal has no use case"* is true of a floor and **false of a ceiling** — a park you
cannot lift is a kill. The teaching burden was not a docstring problem; it was users
correctly perceiving a missing basis vector and grabbing the nearest wrong one.

### R7 — Write-path rules v1 declared unnecessary **[major · 2 lenses]**

v1: *"no discharge floor and no positional answer fold."* But it **mandates two naks**
(T9, T10) — and *a nak is an answer*. `stop-discharge`'s unifying drain rule: *"every
control fact is live until its counter-record, and the worker folds the whole log
applying counter-records."* Target has counter-records and no fold:

- **Duplicate naks per episode** — 5 episodes → 5 identical naks for one bad target.
  Verbatim the regression `service-worker.md` shipped the answer fold to kill
  (*"today's unbounded duplicate-nak growth"*).
- **Refused, then silently honored.** A `{step:100}` target naked `unsatisfiable` by a
  `serve()` episode is **honored by the next `steps()` episode** (ran to 99). The
  requester was told no, and the refusal then took effect. Worse than either policy.
- **No observer fold.** v1 promotes `_epoch` → `observables.run_epoch` explicitly to
  avoid the F7 re-derivation smell, then ships **no `observables.target(channel)`** while
  creating three readers with three rules (worker: nak-aware; `ensure`: raw `latest`;
  viewer: raw `latest`). They disagree. `undischarged_stops` at least *documents* its
  over-report; target has no home to document anything in.
- **`retire()` buries a target.** Its rescue gate is `if self._subs:` — a target is not
  demand, so a target landing in the death window is drained, assigned, and orphaned as
  the episode dies 0s into an hour-long contract.
- **No CAS.** Every other contested claim here is `expected_seq`-arbitrated (birth,
  death). The one piece of state two parties are *expected* to write is a bare append.

### R8 — The fencepost claim is false at both ends **[major · 2 lenses]**

*"The two drive identically"* holds **iff `start < N`**, and only for N ≥ 1:

```
   N start |  steps(total=N,start) | steps(start)+target{step:N} | agree
   5     0 |      n=5  prog=4      |       n=5  prog=4           | YES
   5     5 |      n=0  prog=None   |       n=1  prog=5           | *** NO ***
   0     0 |      n=0  prog=None   |       n=1  prog=0           | *** NO ***
```

`total`'s check is **pre-yield** (`while step < total`); v1's target check is
**post-tick**. **Two different fenceposts at two different sites, and v1 never noticed
there are two.** N=0 also contradicts `progress`'s own docstring — v1's cited authority
— which says *"`N == 0` is trivially reached."* The disagreement set is exactly T8
(resume) composed with the `steps(start=k)`-from-`progress` claim, producing frontier
creep: `1001/1000`, `1002/1000`, `1003/1000` …

### R9 — Null-epoch: agreement is not safety **[major · 2 lenses]**

D4's null-epoch rule made the worker and `ensure` **agree** — on `elapsed = 0.0`
forever. So a time target is never met, `ensure`'s `while not _satisfied(...)` never
exits, the guard is bypassed (R5), and `ensure` has *"No hang timeout (unchanged)"*.
**The agreed value is a deadlock.** v1 traced the skew edge and never traced its own
null-epoch rule to its conclusion. Reachable only because T2 makes time targets possible.

### R10 — "The subscription algebra **provably cannot**" is false **[major · 1 lens, reproduced twice]**

The ledger tested two spellings and generalized to a proof; v1 inherited it. A **third**
spelling refutes both horns at once — independently re-verified here:

```python
{"from": {"step": N-1}, "until": {"count": 1}}
```
```
grammar : LEGAL          unsat@0 : False
A clean completion : progress=4  live_demand=[]          <- demand CLEARS  (horn 1 dead)
B crash at step 2  : progress=1  live_demand=['demand']  <- demand SURVIVES (horn 2 dead)
```

The conclusion likely still holds — but on reasons v1 never gives: it does not tell the
**worker** where to halt; it bakes the `N-1` fencepost into the wire (coordinate-dependent
→ fails Canonical form); it fires a spurious `value` point at `N-1` (a real audience
conflation, polluting the data plane); raise/lower is unsubscribe+resubscribe, racy.

**The honest claim is a split: a missing vector for the worker's durable bound; a
canonical-form improvement for demand.** This matters because v1's persona argument
leans entirely on the demand half — the half that is expressible today.

### R11 — Smaller, but each a real defect

- **Latest-wins is asserted, not derived.** Clobbering is charged as a defect against
  target-as-stop (objection 2) and accepted unargued for target itself. Floors have a
  join — **max** — under which no party un-satisfies another. The real fork v1 never
  states: **latest-wins buys *lowering*, and lowering is clobbering.**
- **`ensure`'s "iff it differs" compares Conditions** — the first site in the codebase
  to do so, against CLAUDE.md's own rationale that the algebra is *free* precisely
  *"since conditions are never compared or hashed."* `{"step":1000}` vs
  `{"all":[{"step":1000}]}` are semantically identical and `!=` by dict equality → two
  benign parties ping-pong writes through R5.
- **T9's prescription is literally broken.** `_satisfiable_stepless({'step': 1000})` is
  `False`, so as written the gate naks **every step target on every worker** — T1, the
  headline scenario. It needs `step is None and not satisfiable_stepless(until)`, which
  surfaces the real point: **the worker has no "am I stepless" property**; steppedness is
  a per-tick coordinate, so refusal depends on *which tick drained it*.
- **`steps(start=k)` from `progress` is a trap, not a serendipity** *(3 lenses)*. The log
  frontier is not the checkpoint frontier: crash at 4 → `progress`=4, `ckpt["next"]`=3 →
  `start=5` **silently skips steps 3–4**. Same class as the resume bug the ledger already
  fixed in that very example, whose docstring teaches *"Checkpoint what you did, not what
  you were asked to do."* **Delete the claim.**
- **`ensure`'s `until` is not the contract for mycooc** — it calls `ensure` once per
  *milestone* over a ladder, so the register would record where *this chunk* ends; and
  patience-only variants pass `_UNBOUNDED_STEPS = 10**9`, so the register reads
  **1,000,000,001 forever**, rendering `151 / 1,000,000,001 = 0.00002%` beside a ✅
  COMPLETED run. *(Verified in the consumer.)*
- **mycooc uses neither reference driver** (`main.py`: *"the surgical adoption never calls
  `w.steps()`"*), and its fencepost differs from `Worker.steps`'s by one (a documented,
  worker-specific `-1`). D2's "both drivers" covers neither. Its migration is a driver
  rewrite, not a plumbing deletion — v1 costed it as the latter.
- **"Progress-toward-target for free" and "generalizes past step-only" are mutually
  exclusive.** A `Condition` is not a scalar; a bar needs a scalar. There is no
  denominator for `{"any":[…]}`, and a *time*-target's bar on a **dead** run creeps past
  100% — item 1's exact bug, one column over, fixable only with `last_activity`, which v1
  never mentions.
- **T4's row is impossible as written** — a worker at step 500 whose target drops to 100
  halts at **500**, not 100.
- **`target_met` inherits neither of `stop_pending`'s documented caveats** — it reads
  False pre-drain, and False for a claim loser (where `stop_pending` returns True), so a
  callback-guest polling it as its sole halt gate never stops.
- **The schema enforces nothing at runtime** — there is no validator in the library
  (deliberately: the worker needs no `jsonschema`), so `_reject_count` cannot be deleted
  and the Canonical-form bullet must not lean on the schema as enforcement.
- **A second test must be deleted**, not rewritten: `test_launch_producer_rejects_non_step_condition`.

### The through-line

> **v1 reasoned about the register's *read* path and never about its *write* path.**

The "needs no rule" claim is **half right**: a register genuinely *converges* under
seq-ordered re-drain (verified). Every blocker lives on the **write** side — who may
write it (R1, R7's no-CAS), what a write *answers* (R7's naks), what happens to a write
nobody reads (R2, R7's `retire`). Stop, subscribe, and the time-lease each earned their
rule from a bug on the **read** path, so v1 checked itself against the wrong three
precedents. The precedent it needed was this codebase's own: **episodes are CAS-claimed
at both ends.**

Underneath it, one sentence: **the register is asked to be three things** — the run's
*contract* (viewer, third party), the current driver's *read window* (`ensure`, mycooc's
ladder), and the *park flag* (bandit) — arbitrated by latest-wins among writers with
contradictory policies. That is an orthogonality violation **inside the primitive**. The
rubric was run on target-vs-stop and never turned on target itself.

---

## What survived (the rework must keep these)

- **The diagnosis and the vector.** Real, and the meta-constraint attack **does not
  land**: `step` is already a protocol coordinate, so this is not opinion creep.
- **The stop/target duality.** The constructive subsumption attack (*express stop as
  target*) fails on all three counts; count (2) — *"stops accumulate; targets overwrite…
  a register gives it away"* — is called exactly right, and is the count that indicts R1.
- **The register's convergence (read path).** Latest-wins genuinely needs no discharge
  fold; `__init__` is genuinely untouched (no capped-read entry, no floor, no answer fold,
  no boundary list — the same-read fusion undisturbed); the claim-race loser is safe for
  free via `_lost`; the death-CAS survives target's naks; **`peek_terminal` really is
  untouched** (verified byte-identical verdicts). These are real, non-obvious properties
  of choosing a register.
- **A5 (no `lifecycle.target_met`)** — *"a terminal you can un-reach by raising the target
  is not a terminal"* is the argument, not decoration. **A2 (no launch recipe)** — the RCE
  hazard is real; *"the instinct that a message was needed was right; the payload was
  wrong"* is the correct diagnosis, and R2 is the unfinished half of that thought: **the
  message is right and the actuator is missing.**
- **The verdict stays orthogonal.** Default preempted, no new tier, reason recovered from
  the log — a clean application of the settled B′ doctrine.
- **D4's run-relative time axis.** *"On episode-local time a 1h target restarts its clock
  on every resume, so a run crashing every 50min never converges"* is a proof, not a
  preference — verified end-to-end across a restart. **Only the null-epoch corner (R9)
  resolves the wrong way.**
- **`observables.run_epoch`.** Correct on its own merits, with a genuine second-consumer
  trigger. **Shipping separately, independent of this spec's fate.**
- **D6 (`subscription` → `control`).** Right, and argued as a rule (every other convention
  schema is named for its topic family) rather than tidiness. Zero external blast radius
  **verified** — neither consumer references the schema files or `jsonschema`.
- **The `ensure_served` self-correction** — called "the spec at its best": it refuted its
  own parent ledger, verified the refutation against the code, and narrowed the claim.
- **The consumer-found wart.** `translation/runner.py`'s hand-written *"'up_to' is
  reserved"* guard is a hazard the hack created. **`REDRIVE_UP_TO` genuinely dies** — and
  T2's generality is mechanically real (verified: `ensure(until={"time_seconds": 0.2})`
  returned 20 points where today it raises).
- **Live retarget of a running worker** is a genuinely new capability with no substitute
  (today expressible only by kill-and-relaunch). **It is the real, defensible payoff — and
  it is not the persona's gap.** Sell it as what it is.

---

## The narrowed question the rework must answer

Item 2 is **three features** wearing one name, and v1's failure is the proof:

1. **The worker's durable bound** — genuinely missing; enables live retarget; needs the
   worker to *read* its contract before it acts (R4) and a fencepost that is idempotent
   under resume (R8).
2. **The observer's contract** — the viewer's denominator. Needs a *stable* meaning no
   read-window write may perturb (R1), an observer fold that accounts for naks (R7), and
   an honest answer for runs whose real bound is not a coordinate at all (mycooc's
   patience, R11).
3. **Durable demand for a relauncher** — **expressible today** (R10), and blocked not on
   vocabulary but on an **actuator**: a daemon, which A3 refutes for lacking a flap guard,
   which was supposed to be item 2. **That circularity must be broken deliberately, not
   inherited.**

And the structural question R6 forces: **is the missing vector the durable *floor* (goal)
or the durable *ceiling* (park)** — or both, as two registers? Two clean registers would
fix R3 (the viewer reads the goal from the floor and *parked* from the ceiling; nothing
lies), fix the bandit round-trip (park = set ceiling; resume = lift it; the goal is never
touched), and leave a floor-writer coherent. **Not proposed as the answer** — but
Spanning requires the cell be named, and v1 shipped without naming it.

---

## The proposed model (REFUTED v1 — preserved as history)

> **`control.target {until: <Condition>}` — the run's contract, as a register.**
> Latest-wins (`channel.latest`), worker-directed. **Discharge is derived, never
> recorded:** met ⟺ `progress + 1 >= N`. No counter-record, no positional fold, no
> lease, no retraction verb.

Decisions as proposed, with their post-pass status:

| | decision | status |
|---|---|---|
| **D1** | the worker consults the register per-tick, not launch-time translation | **holds in principle; R4 shows per-tick is not enough** — the contract must be readable *before* the first step |
| **D2** | target is a halt condition for both drivers, parallel to stop | **refuted in part** — R7 (`retire` buries it), R11 (mycooc uses neither driver) |
| **D3** | `steps(total)`'s local arg survives | **holds, and is load-bearing** — it confines the migration to the orchestrated seam |
| **D4** | the time axis is run-relative, forcing `run_epoch`'s promotion | **holds** — except the null-epoch corner (R9) and the guard hole (R5) |
| **D5** | no retraction verb | **refuted** — cites A7's precedent while lacking A7's premise (a stop self-clears via the `stopped` it requests; a target does not), and R6 shows removal is exactly what a *ceiling* needs |
| **D6** | rename `subscription` → `control` | **holds; ships independently** |

**Rejected alternatives that survive the pass unchanged:** A1 (demand-via-subscribe —
now *narrowed* by R10: refuted as canonical form and audience, **not** as impossible);
A2 (launch recipe → RCE); A3 (standing daemon → crash-loop generator); A5 (a target-met
terminal record); A6 (unify stop and target under one verb → forces one storage model on
two that want different ones).
