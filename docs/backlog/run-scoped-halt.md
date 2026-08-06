# A halt that survives an episode boundary

**The finding stands. The value-plane recipe proposed for it is REFUTED** — it repeats, almost
line for line, the inversion that killed `../specs/control-target.md`: it designs the register's
*read* path and asserts its *write* path. §4.

**Status:** open need, no mechanism. The concept is already named — `../specs/control-target.md`
R6 calls it the **durable ceiling** and identifies it as the one empty cell in the control 2×2 —
and `../specs/stop-discharge.md` A7 already assigned it a home. Read both before proposing anything.

## 1. The measurement

No forgery, no reclaim tool, no third party — an operator halts a run and an ordinary live worker
honours it:

```
operator sends control.stop   -> undischarged=[1]
worker honours it, stops      -> undischarged=[]

mycooc next_claimable would consider this run claimable: True
and a new episode claims it successfully:                 True
```

The discharge is **correct**. `../specs/stop-discharge.md` designates `lifecycle.stopped` as the
stop's *effect*; the stop was answered, so it is spent. The run then restarts. Issue #39's original
framing ("a third-party reclaim silently discharges…") named one route to this rather than its cause.

## 2. The discharge rule is right and must not be reopened

Letting a stop survive the boundary is the bug the rule was built to remove —
`../specs/stop-discharge.md` symptom 1, with a committed-RED test
(`tests/test_worker.py::test_resumed_episode_ignores_prior_episodes_stop`):

> A `control.stop` that halted episode 1 is re-drained by a resumed episode 2, re-armed, and honored
> again — the resume dies at its first step.

Three independent design reviews converged on the discharge fold. The gap is not in the rule.

## 3. The mismatch

| | question | scope |
|---|---|---|
| runstate | "was this stop request answered?" | the **episode** |
| the consumer | "should this run be running?" | the **run** |

`mycooc/rungraph/ports.py` documents `stopped(rid)` as *"Does this run carry an undischarged
stop?"*, and `rungraph/state.py::next_claimable` uses it as a scheduling predicate — the run-level
question answered with the episode-level fact.

## 4. The value-plane recipe — proposed, and why it fails

The proposal: don't add a `control.halt` verb (scheduling policy would enter the protocol); instead
bless a **recipe** — a stepless `value` register, shape blessed by runstate, meaning owned by the
consumer — copying the shipped completion-reason register.

**What survived scrutiny.** The one divergence flagged as load-bearing — that this reads
**run**-scoped where the completion-reason recipe's Rule 1 insists on *"episode-scope the read…
it is not optional"* — is a **real distinction, not a renamed hazard**. Rule 1 exists because those
registers' *referent* is an episode-scoped entity (this worker's phase, its pid, this dispatch's
reason), so nothing invalidates them when the entity is replaced and `latest_episode().seq` is
pressed in as a synthetic eliminator. A halt's referent is the run; no episode boundary makes it
false. mycooc's `graph_adapter.py` `input_provenance` register already reads unscoped on purpose.

**Why it fails anyway — five ways, and the first is decisive.**

1. **The write path was never designed, which is the exact inversion that already killed this
   mechanism.** `../specs/control-target.md` (REFUTED same day, 2026-07-16) proposed a multi-writer
   last-write-wins value register used, among other things, as a park flag. Measured there:
   *"**373 spawns in 3 seconds**, 748 target records… the operator's pause did not hold… **not 'last
   writer sets the goal' — the writer with the fastest poll loop wins**"*, and *"**No CAS.** Every
   other contested claim here is `expected_seq`-arbitrated (birth, death). The one piece of state two
   parties are expected to write is a bare append."* That doc's own through-line is *"v1 reasoned
   about the register's read path and never about its write path."* The recipe's four bullets are
   Shape / Eliminator / Reader — all read path — plus **"Writer: anyone who can append,"** asserted
   and unanalyzed.
2. **The value plane is the no-obligation case, and a halt is an obligation.**
   `protocol-algebra.md` defines it as *"the no-obligation case (no eliminator, no entry in Γ)."* A
   halt obliges someone to lift it or the run never runs again. Filing an obligation in the plane
   defined by having none is exactly the lossy reuse `faithful-representation-over-lossy-reuse`
   bans — and it makes this the only standing fact in the system with no designated eliminator,
   against L2's requirement that a standing fact's eliminator follow it by `seq`.
3. **The "ordered against the claim" argument is inert.** Measured, 60 concurrent trials on sqlite
   and Postgres: **60/60 claims land provably later in the total order than the halt they ignore.**
   The claim CAS (`worker.py`) is `expected_seq=last` and arbitrates claim-vs-claim only; an
   interposed halt makes it fail, retry, and claim straight past. A total order is not mutual
   exclusion, and nothing consults it. The §3 table in the previous revision claimed the log wins
   this row over a consumer-local marker. **Neither is ordered against the claim** — only the
   worker's claim CAS could be, and reaching it means a protocol verb, which is what this design
   set out to avoid.
4. **Scope is wrong, and this is the sharpest one.** The log is **run**-scoped, but identical configs
   in two experiments converge on **one home**, so a halt on the log aliases across every experiment
   sharing that run id. `../specs/store.md` makes exactly this distinction load-bearing: *"an
   operator's `touch cell/.skip` must not skip the run for every claiming experiment."* The halt's
   natural scope is the **demand**, not the run — which argues the fact does not want the log at all.
5. **Observability, the other half of the boundary argument, needs a change it did not price.**
   `runstate-tui` has no value fold; its single value read is driven by `--objective`, so pointing it
   at a halt register would *evict* the metric from the only value column. Today such a register
   surfaces only in the raw drill-down log pane. The claim "observable by tools that speak runstate"
   should read **"not today; needs a TUI change."**

**Two corrections to the previous revision, and one non-correction.**

- *"Every spawn is the consumer's"* was **false as written**: `LocalLauncher.launch` calls
  `subprocess.Popen`, `ThreadLauncher` starts a thread, and `sweep.py` runs a
  read-the-log-then-`launch()` loop **inside** runstate. The narrower claim survives and is what the
  boundary argument needs: *runstate never **originates** a spawn — every spawn traces to a
  consumer-initiated call, and the consumer chooses the launcher.*
- The pollution worry was **overstated in the safe direction**: a stepless register does *not* appear
  in name enumeration (`value_series` skips `step=None`) and `sweep` never touches the value plane.
  The real residue is that `history(ch, "halted", …)` **raises**.
- *"#19 made exactly that read index-served"* is **correct**, and a review measuring 35 ms on a
  branch cut before that merge is measuring a stale tree. Re-measured on master, 50k value records:
  hit **0.006 ms**, miss **0.003 ms**, `SEARCH log USING COVERING INDEX idx_log_topic_name_seq`.
  Cost was never the objection here.

## 5. Where this actually goes

**The need is real and already named.** `../specs/control-target.md` R6:

> The **durable ceiling is park/suspend**: *do not proceed past here, across relaunches, until I lift
> it.* That is what the bandit means, what a quota guard means, what an operator's "hold this run"
> means.

— the one empty cell in the transient/durable × ceiling/floor 2×2. And
`../specs/stop-discharge.md` A7 already assigned it a home: *"If a durable 'never run again' is ever
wanted, that is a hold/park concept — a different convention, or the caller's relaunch policy — not a
stop that outlives its answer… the named home is now the cell-local `.skip` policy file / the
caller's relaunch policy."*

**`.skip` ships and is the wrong shape, which is why the cell is still empty.** It is a *permanent
exclusion* — mycooc's `Fold.complete` counts a skipped cell as done — where a halt is a *resumable
hold*. So A7 pointed at the right layer and the wrong artifact.

**What has to be designed first, and in this order:**

1. **The write path.** Who may write it, what arbitrates rival writers, what discharges it. Not the
   read path. This is the inversion that refuted `control-target.md` and the recipe above.
2. **R6's own structural question** — is the missing vector the durable ceiling alone, or a
   ceiling/floor pair?
3. **Scope: demand or run?** §4.4 says demand. If so the fact belongs beside `.skip`, at the
   consumer, and the whole log question dissolves.
4. **Then the bar**, on both halves. `claim-eviction.md` was deferred on a corpus scan (*"6 times, in
   2 runs, from one experiment and one tool"*). This need has had **one hand-run repro and no corpus
   count**. Claiming it clears a bar that another design failed requires measuring the same thing.

## 6. What this rules out

- **Patching the consumer's reclaim tool** to re-issue swallowed stops — wrong layer; it
  hand-maintains run-level state across a boundary the protocol deliberately clears, and leaves the
  ordinary-worker route untouched.
- **`lifecycle.evicted`** (`claim-eviction.md`) — it refuses to discharge, closing the third-party
  route while the honest route still discharges. #39 was one of the two defects that design was
  justified by; it was aimed at the wrong plane.
- **A `control.halt` verb** — scheduling policy in the protocol.
- **The value-plane recipe** — §4.

## 7. Related

- `../specs/control-target.md` — **R6** names the cell; **R1/R7** already measured the mechanism
- `../specs/stop-discharge.md` — **A7** assigns the home; the discharge rule itself
- `../specs/store.md` — the cell-vs-run scope rule §4.4 turns on
- `claim-eviction.md` — deferred; would not have fixed this
- `lifecycle-stopped-unbundling.md` — adjacent, but a bundled job, not a missing scope
