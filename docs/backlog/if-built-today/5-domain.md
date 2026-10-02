# The domain: runs, metrics and steps

**Layer:** one worked instance, depending on every layer. The dependency graph is in `README.md`.

## What the problem domain forces, whatever the design

These are not inherited from an incumbent; they are constraints the setting imposes, and any design for it
will contain something playing each role. They are listed because it is easy to mistake them for choices.

- **A run is a durable identity that outlives its processes.** The unit somebody asks about is not a
  process — it survives the death and relaunch of every process that ever served it.
- **Content-addressed identity.** If a run is named by what it computes, the name is also the cache key,
  and two agents asking for the same thing ask under the same name without arranging to.
- **The verdict as a join of two partial observers** — and it is a *report*, which is why it may use the
  narrowing reading that derivation may not.
- **Cooperative, no enforcement.**
- **`never` as a fact rather than a status** — it survives as `¬Q` over a singleton region. What nobody
  posts is the **status**, which is read, not written.
- **Status cycles; values do not.** `running → OOM → running` cannot live in a monotone order, so the
  attempt index goes in the term and the cycle lives in the *sequence of attempts*.
- **Structure goes in the key, not the value.**

## One demand, traced

Everything up to here has been argued rather than shown. Here is a single demand from posting to
settlement, so the rest has something to be about. Nothing in it is new — every step is a mechanism one of
the surrounding sections defends.

**A querier posts one record.** It wants the loss at every step of run `r`, for as long as the run lasts:

```
asked(∃V. metric(r, loss, V, S)) :- S ≥ 1.
```

`S` is free, so the question ranges over every step — integers, since the signature sorts `S` as a step;
`V` is bound, so each step wants *a* loss. The extent is **unbounded**. That is fine; what must be finite is the eventual cover, not the region.

**A producer is handed the residual, not the question.** Steps 1–60 already have a loss in the store, so
what reaches it is `S ≥ 61` — the question minus what is decided. Nobody detects that the first sixty were
subsumed; they simply contribute nothing.

**It produces, and the querier reads a threshold.** `metric(r, loss, 0.31, 61)` is posted. The querier's
read is `⊒{t}` on that atom, affirmed the only way anything is affirmed here: by exhibiting the record.

**At step 900 the querier sees `∅`, and may conclude nothing from it.** Nothing has been told about that
step. That is not *"there is no loss at step 900"* — it is *"nobody has said."* The querier may confirm it
has **left** `∅` the instant any record arrives, and may never confirm it is **in** it.

**The run converges at 743, and the producer says so.** It posts one negative region,
`told(metric(r, loss, V, S), neg) :- float(V), S > 743`. **That single record decides infinitely many
atoms** — and it is *told*, not inferred. Nobody derived it from the absence of anything: the producer
knew, and said.

**Now the question is settled.** Every step `S ≥ 1` is decided — a witness for each of the 743 produced
steps, and a refutation of every loss at every step beyond. The cover is finite (743 positives and one negative) though the
region is not, which is exactly what makes an unbounded demand dischargeable. The querier's *"tell me when
there are no more"* fires here, and not before — and it means no more **steps**. Because the question bound
`V`, it never asked whether a step has *other* losses; a later second value at step 61 is another answer,
not a contradiction.

**A second querier posts the identical question, and nothing runs.** Identical questions are one record,
and its residual is empty, so there is no work to hand anyone and no producer to launch. The store was the
cache; no cache was built. A querier asking the *stronger* question — `metric(r, loss, V, S)` with `V`
free, every step's set of losses complete — would find it unsettled, and it stays so unless the producer
vouches, at each step, that its loss is the only one (`2-polarity.md` §"The rule for posting").

**And if two producers disagree.** Two of them posting different losses at step 61 produce two *atoms*,
both `⊒{t}` — a **domain** conflict, invisible unless somebody wrote a rule saying loss is functional in
the step. A **valuation** conflict is a different thing: it takes a negative covering `metric(r, loss, 0.31,
61)` — a per-key complement from the other producer, say — and then that one atom reads `{t,f}`:
affirmable, inert, and poisoning nothing around it. Which of the two a disagreement becomes is decided by
the key's granularity and whether producers vouch — a schema's choice.

## The honest cost

A **rewrite, not a refactor**, with consumers on the current API. It trades a design whose failure modes
are intimately known for one whose failure modes would have to be learned. And storage grows wherever the
order is partial, since there the only sound completion is the free one.

**And the artifact is larger in one direction and smaller in the other.** Today's runstate is an *ordered*
topic log with typed conventions on top — a message protocol, and a thin one on purpose.

**The transport this design needs is weaker than that.** The store is a growing **set** of ground terms and
merge is union, so unordered, duplicate-tolerant, loss-tolerant broadcast suffices: a reader holding one
agent's posts and not another's simply has a smaller store, which is sound. Not even causal order is
required — missing a cause costs completeness, never correctness. Order is needed in exactly one place, the
**claim**, where `send(expected_seq=)` is a compare-and-swap; and that is single-spawn, which `1-logic.md`
§"CALM" already concedes as the one coordinated act. Nothing in the semantics reads a sequence number.

**What is larger is the read side.** A per-functor term index that pattern-walks rather than key-looks-up,
a constraint solver in the read path, and a **coverage checker** that decides settledness and the residual
by finding a finite cover — incomplete checkers are safe, costing only spurious relaunches. (An earlier
draft also owed cross-host naming for the holes in posted terms; variables scoped to records removed it.)
That is the honest headline cost, and it dwarfs the fold rewrites below. It is also the thing a reader should weigh
first, because everything else here is downstream of being willing to build it.

**No fold ports as-is.** Measured across the whole of `observables.py`: 13 of 16 fold readings are
non-monotone, and the operator responsible is `latest` = `argmax(seq)`, which appears six times directly
plus three `[-1]`/`reversed` and three `max(…)` in 551 lines. Each becomes **dual plus subtraction** — the
monotone half derived inside, one complementation performed outside:

```
inside   discharged(C) :- stop(C), stopped(S), S > C.
outside  unhandled = stops − discharged
```

**The inside half is confirmed.** 13 of 13 duals measured monotone, and **9 of 9 folds reconstruct
exactly** across every scenario tested; the worked example reproduces the shipped `undischarged_stops`
verbatim, one subtraction.

**The outside half is "a subtraction" for 5 of those 9**, and the residuals differ in kind:

| residual | folds | what it is |
|---|---|---|
| subtraction only | `latest_episode`, `_episode_stopped`, `undischarged_stops`, `value_series`, `live_episode` | the clean case |
| + an **aggregate** over a non-`seq` order | `progress`, `last_activity` | a `max` over the survivors — and `last_activity`'s is over `t`, non-monotone against `seq` |
| + an **emptiness test** on an already-complemented set | `_launcher_terminal`, `peek_terminal`, the four-state projection | `¬∃ started`, which no finite observation can affirm — measured to **retract a published verdict** when a later claim arrives |

**And picking the dual is where the danger is.** The obvious dual of `progress` is the high-water mark
`reached(K) :- heartbeat(_,S), K ≤ S` — and it is **wrong**, for the reason `progress`'s own docstring
gives: *"a monotone watermark here would re-open the splice it just closed."* After an episode rewind it
reports the old frontier; `ensure`'s window test then passes and returns a **spliced series as complete**.
`last_activity` has the same trap one axis over.

So this is eight rewrites, none mechanical, and at least one where the *natural* dual silently
reintroduces a bug the fold exists to prevent.


## What a library adds over a database

Three jobs, one of them not commodity:

- **persistence** — a store dies with its process, and runstate's whole contribution is durable identity
  outliving processes;
- **indexing** — noting that per-position term indexing over heterogeneous terms is not a database
  feature, and neither is unification;
- **an oracle channel whose outputs are timestamped into facts about the past** — the OS probe, the clock,
  the temporal delta, the fixpoint test (`1-logic.md` §"Two layers"). The store can tell you what happened;
  it cannot tell you that *nothing* happened, and `ensure` needs exactly that.

The third is the answer to *"why this library rather than Postgres plus a type discipline."* The buildable
object is closer to **a Prolog with a durable fact base and a pid probe** than to a schema. Coherent to
want; large to build.

## Today's runstate, mapped

**`ensure` is the one place the library's core operation leaves the fragment.** Its loop condition is a
threshold claim on `progress`, a *retractable* quantity, and its two termination guards are a **temporal
delta** (*"nothing new was derived"*, which has no positive form) and an **inflationary fixpoint test**
(*"another lap can only reproduce them"*). Neither is expressible in the fragment — each compares two
moments, which no growing set of facts can do — and both feed demand, because both decide whether to
relaunch. `1-logic.md` §"Two layers" is where that belongs.

**Exhaustion arrives three ways in runstate today** — a worker's own `lifecycle.stopped`, an observer's
`launcher.terminated`, and a pid probe — of which only the last is dependable, since the first two exist
only if somebody volunteers them and the probe **abstains off-host**. That is the launcher-versus-lifecycle
split this library already has, kept orthogonal for exactly the reason `2-polarity.md` §"How a party comes to
know a negative fact" gives: exhaustion is a fact about a process, never about what exists.

**What ships already carries its extent as a constraint.** A subscription with an `until` is one durable
record denoting a bounded region — durable across the *producer's* death, because a worker re-drains the
control log and re-registers whatever is still unanswered, pinned by
`tests/test_run_episodes.py::test_relaunch_extends_one_series`: one subscribe posted *before episode 1
exists*, two episodes, ten steps, one series. `{"every": …}` with no `until` is schema-legal, documented
as *"forever"* — the unbounded region of `3-questions.md` §"Quantifiers live in questions", concrete rather
than hypothetical. (The bare subscribe is served by a poll of the register, `self._values.get(name)`, not
by anything waiting on a production; that is the gap between the shipped library and this design.)

## Still wanted: leases

`3-questions.md` §"Demand is control" leaves *still wanted* to a scheduling policy. runstate already has
both kinds of demand: `relaunch_if_needed` serves **durable** demand and `ensure_served` serves **leased**
demand (`../../specs/lazy-launch.md`). The proposed policy:

- **A lease is a duration, renewed by the asker** — posted periodically, one way, as today's heartbeat is.
  No reply, so no round trip. Durable demand is a lease of unbounded duration, and its risk — a question
  that never settles, relaunched forever — is `open.md` 10's admission rule, not a lease problem.
- **The scheduler times it from local receipt**, on its own monotonic clock: a question is still wanted
  while less than the duration has passed since the scheduler received the latest renewal. No clock is
  compared across hosts, matching runstate's original liveness design, where the Watcher knows when a
  beacon arrived *"because it was there"* (`../../specs/observer-clock.md`). The known cost is the same
  bounded one runstate accepts in `../../specs/time-lease-boundary.md`: a scheduler that attaches after a
  dead asker's last renewal receives it as fresh, and the question looks wanted for one more duration.

Two choices are open, with their trade-offs:

- **One relation or two.** The lease as an argument of `asked` — `asked(Q, lease(N, D))`, with *"Q was
  asked"* the projection `∃N, D` — or a separate renewed `wanted` record beside a permanent `asked`. One
  relation is minimal, since the permanent form is derivable; two keep *"asked"* and *"still wanted"* as
  separate concerns. No semantic difference.
- **Early withdrawal, or lapse only.** With a renewal counter `N`, the live lease is the highest-`N` record
  — a `max`, so monotone — and a renewal of duration zero withdraws at once. Without `N`, an asker can only
  stop renewing and wait out the duration. Withdrawal also needs leases kept per asker, so several askers
  of one question do not cancel each other; a random session id serves, with no roster.

## What the measurements say

Every corpus figure the layers lean on, kept here so the layers themselves stay workload-free.

**Terms, measured** (for `0-substrate.md` §"The model"'s *"terms, not blobs"*). Measured on 200k rows, a
JSONB key with a btree expression index runs the central range query in **0.085 ms** against a positional
term layout's **0.089 ms** — the term buys nothing, the btree does. Worse, positional indexing over
*heterogeneous* terms is not merely slow but wrong: with `loss(Config,Step)`, `grad(Config,Layer,Step)` and
`ckpt(Run,Config,Shard,Step)` the step axis sits at three different positions, and an axis-blind positional
range returned **132,879 rows against a correct 91,500**. `1-logic.md` §"Types"'s hoisting wrapper is the fix.

**What keeping every record costs** (for `1-logic.md` §"Constraints are asked"). Measured over **823 real
logs, 2.5M records**: the compression given up is **0.34%**, and cells whose values genuinely fail to join
are **0.072%**. Both are a footnote. Where the partial case *does* land supports keeping the atoms —
**1,714 of the 1,719 divergent cells are `status`**, an app event mirrored onto the value plane at a reused
step, where last-write-wins reports a run as *saving* at step 87 of training. Conflict is reachable without
forgery: two honest producers differing by one ulp (`0.30000000000000004` vs `0.3`) do not reconcile, and
`mycooc/analyze_run.py` already hand-rolls a guard against exactly this. The corpus has **16 hand-rolled
guard sites**, sixteen per-relation conflict declarations rather than one missing primitive.

**The value plane's sorts** (for `1-logic.md` §"Types"). Measured over 821 real logs: 24 distinct value
names, **none carrying more than one sort** (21 `float`, 3 `dict`), and no new names in the corpus's second
half. So the entire measured value plane is **two** relations — `metric(Name, Float, Step)` and
`event(Name, Json, Step)` — both fixed shapes with the name as **data**; the aliasing objection never
arises, because the partitions are separated by *relation* rather than by name. The case that motivates
per-functor sorts is genuine — `mycooc`'s `permutation` carries `None` under one flag and a nested record
under another, in **source** — and it is a hazard the rule forecloses rather than damage it repairs, since
**0 of 24** names show sort drift. (Whether metric names belong in functors or in data is open: names
known at schema time belong where a tool can check them; a free-text metric name typed into a UI at
runtime is the open-set case.)

(The 821-vs-823 discrepancy is unexplained; the two figures may be one corpus counted twice.)
