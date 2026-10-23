# Convergence between if-built-today and runstate

**Status:** LIVING. Opened 2026-10-04 with the owner as "What carries over from if-built-today into
runstate"; renamed and corrected 2026-10-14 ([review](../review-2026-10-04-commits-and-lineage.md) §9 C1,
§12 item 0.11). [if-built-today](if-built-today/README.md) is a greenfield design: a monotone store of
polarised literals. This map runs both ways: which of its ideas runstate has adopted or should, and which of
runstate's measurements it has taken back. It cites if-built-today by section rather than paraphrasing it,
because four paraphrases in the first version drifted.

## The map

| Idea (if-built-today section) | In runstate | What it changes, or what is still open |
|---|---|---|
| Identity is data, never position (README §"Two commitments, and everything follows from them") | **adopted under one sequencer:** layers 1–2 name records by seq | a seq is a name only where one sequencer assigns it; minted ids are layer 6 ([lineage-graph](lineage-graph.md)) |
| Settledness: a question can know it is finished (`3-questions.md` §"Settledness of a question") | **adopted in layer 2,** as a property of a node ([spec](../specs/commits-and-lineage.md) §2) | a read of a closed lineage is settled for good; only the choice of head is a report |
| Values keyed by node, each node vouching for exactly its values (`5-domain.md` §"The schema") | **adopted in layer 2,** and taken back into if-built-today (review §12, item 11) | — |
| Declared signatures, never inferred from a first post (`1-logic.md` §"Types: many-sorted, per functor, structural") | **adopted in layer 2:** a claim declares its sources | dynamic sources, declared by the worker within its claim, are backlogged |
| Two viewpoints on how a claim ended, and a death report ending its claim (`5-domain.md` §"The schema") | **adopted in layer 2:** `lifecycle.stopped` and `supervisor.died` (review §12, items 5 and 13) | outcomes on death reports, below |
| Withdrawal as a positive record (`5-domain.md` §"Still wanted: leases", §"Halting and withdrawing") | **adopted by name in layer 2:** `control.withdraw` | hash-reveal, so that only the asker can withdraw, is backlogged |
| Forbidding further computation, apart from halting a claim (`5-domain.md` §"Halting and withdrawing") | **a candidate write path** for [run-scoped-halt](run-scoped-halt.md) | the scope of a halt, and its authority rule |
| Demand as `asked` facts (`3-questions.md`) | **the next layer** ([demand-streams](demand-streams.md)) | replaces subscriptions; brings `ensure` and lazy launch under one primitive |
| A local monotonic clock for durations (`1-logic.md` §"Two layers, and what crosses between them") | backlogged | staleness and lease expiry measured on a clock that cannot step |
| Reclamation as eviction, never retraction (`3-reclamation.md`) | backlogged | collecting abandoned branches and unvouched checkpoints |
| Identity kept through aggregation (`4-aggregation.md` §"Summaries, and the property a construction can forfeit") | backlogged | cross-run summaries say which rows contributed |
| Objections, not retraction (`3-provenance.md`) | **not adopted** | below |
| Naming the non-monotone core (`1-logic.md` §"CALM: why monotonicity, and not merely for tidiness") | the list below | record it in `design-v0.2.md` as the coordination budget |

## Identity kept through aggregation

**The idea:** gluing rows **by identity** cannot produce views that are locally consistent but globally
impossible; aggregating identity away can (`4-aggregation.md`, after Morton 2017).

**For runstate:**
- `aligned()` (layer 2) is a join on identity, so it is safe.
- Summaries across runs, in the cockpit or a separate viz project, should carry **which rows contributed**
  to each figure. A table of "best loss per config" should keep the run, node and stream behind each cell,
  and label latest-wins and argmax as summaries that drop identity.

**What would refute it:** a consumer summary that is useful only without its contributors, and whose
contributors cannot be kept at reasonable cost.

## Objections, not retraction

**The idea** is `3-provenance.md`'s: nothing is taken back, and a correction is a record that names what it
contests.

**For runstate, nothing contests anything yet.** Layer 1 made records name what they act on, which is
reference by name, not objection. What runstate had instead was retraction at read time: a claim's newest
terminal won. Layer 2 removes that instance by separating the viewpoints (review §12, item 5): the worker's
self-report, if readable, else the death reports. Several death reports are all kept, their outcomes as a
set (review §12, item 14), and dispute records that name what they contest are backlogged.

## Naming the non-monotone core

**The idea:** monotone parts are free of coordination (CALM), and the non-monotone core is exactly where
coordination is paid (`1-logic.md` §"CALM: why monotonicity, and not merely for tidiness"). Name the core,
and every later design question gets easier.

**runstate's non-monotone core:**
- **the sequencer** on every append, and **the claim CAS**, the one arbiter, ordering claims;
- **the claim gate's `live_episode is None`**, an absence;
- **the dying breath:** a CAS over a fully read control tail;
- **lease expiry and voiding:** a lease ends because time passed, or because its episode ended;
- **the liveness tiers:** a probe, staleness and the episode lock each conclude a death from an absence;
- **the subtracting folds:** pending stops and live demand are requests minus their answers;
- **`await_consumed`'s "accepted"**, which is the absence of a `nak`;
- **terminal resolution:** the verdict's *else* tests for the absence of a readable self-report;
- **`last_activity`**, a latest-of;
- **`ensure`'s no-progress and fixpoint guards;**
- **`LatestClaimHead`'s choice of head.** Its outcomes are not on the list: a read's (outcome, node) pair is
  permanent under either head Strategy.

**Off the list: register reads.** Sampling a source is computation whose output, a committed sample keyed by
its node, is a monotone fact.

**Next step:** record the list in `design-v0.2.md` as the coordination budget.

## Where layer 2 is stricter than if-built-today

From the layer-2 walkthrough (review §12, item 8).

**Where the two agree.** The source/eager duality is if-built-today's own split between **demand** and
**subscription** (`3-questions.md` §"Demand subsumption", "What to internalise, and what not").
- Notification about eager data becomes the reader's own cursor.
- Only demand for values that exist on demand reaches the worker.

**Stricter 1: a `nak` for sampling demand on an eager name.** if-built-today leaves a question that matches no
producer rule unanswered, and serves demand for facts told anyway by scheduling (runstate's `ensure`). The
`nak` is the explicit form of "matches no producer rule", given declared sources. It is kept because a
silently unanswered subscription would pin a `serve()` worker. This is a mild tension.

**Stricter 2: one kind per name.**
- **The case if-built-today allows:** one relation with some instances told eagerly and others produced on
  demand ("loss every 100 iterations, sampled on request in between").
- **What layer 2 does instead:** it takes two names. Today's code supports the mixed case, because `emit`
  updates the register.
- **Where it comes from:** counting, since a count over demand-dependent instances depends on the asker
  (review D6). if-built-today has only identity keys.
- **How it converges:** through node keys, which both now have (`5-domain.md` §"The schema"). Counts apply
  only to names that are eager throughout the lineage, and any declared source can be sampled at any node,
  with sampling a node already told answered from the log (if-built-today's residual). This adds
  capabilities and changes no record.
- **Revival trigger:** a consumer that wants sparse eager points and on-demand samples under one name.

## Where they still differ

- **A stop names a claim in if-built-today,** and lapses with it (`5-domain.md` §"Halting and withdrawing").
  runstate's names none, so one its claim never honoured is honoured by the next claim (spec §12).
- **Withdrawal:** by reveal of a commitment there, by name here.
- **A death report:** `died(E, Why)` carries no outcome there, so death reports cannot disagree; here
  `supervisor.died` carries one, since a consumer needs to tell a preemption from a failure. Several reports
  can then disagree; the verdict keeps all their outcomes as a set, so a consumer sees the disagreement.
- **A released worker** stops writing here once a death report names its claim, so a mistaken report costs
  one iteration; there a claim may continue past one (review §12, item 13).
- **Extending an exit:** here an exit that neither completed nor discarded a torn iteration can be extended,
  so the step is the count of commits, not depth; there no exit can be extended, which a node's name lets a
  reader check on the message alone (review §12, item 13).
- **A subscription's life:** a question there names its subject node, and is settled per lineage. Here a
  durable subscription is served per lineage but spent by name for the decision to launch, and a lease
  lapses with its claim (review §12, item 13).
- **The claim order:** data there (`claim(E, I, From)`, with a Lamport clock where there is no arbiter;
  `open.md` item 5), the claim CAS here.
