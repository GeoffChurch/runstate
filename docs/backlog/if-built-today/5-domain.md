# The domain: runs, metrics and steps

**Layer:** one worked instance, depending on every layer. The dependency graph is in [`README.md`](README.md).

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

## The schema

**A computation is recorded one event per record, and the kind of event is the relation:**

```
claim(E, I, From)            claim E, at place I in its run's claim order, starts from From
commit(c(E, K), P, Ms)       claim E's record K: a node extending P, with exactly the values Ms
rewind(E, K, To)             claim E's record K restates its position as To
exit(end(E), P, Ms, O, Hs)   claim E's final node: outcome O, honouring the stops Hs
died(E, Why)                 a third party saw claim E's process die, with the evidence Why

O : completed | preempted | errored(Reason)
N : Node = Run | Commit | Exit
M : Metric = loss(Float) | accuracy(Float) | converged(Bool) | …
```

**A node is a state of the computation.** A run `r` is the root of a tree, and a node itself, at depth 0:
its content-addressed id ([`../../specs/run-id-recipe.md`](../../specs/run-id-recipe.md): the pattern is runstate's, the choice of
inputs the user's) already names the initial state those inputs determine. A commit is the state after one
more iteration. An exit is a claim's last state: it takes a step of its own, carrying whatever it committed
on the way out, so a lineage of `n` commits that ends in an exit is `n + 1` deep.

**A node's name says who computed it, in what order, and of which kind.** `E` is minted, unique across
runs, so no node's name depends on the arbiter. `K` orders claim `E`'s records: it is the writer's own
counter, shared by its commits and rewinds, which needs no coordination, and under one sequencer a sequence
number can serve as it. `c(E, K)` is then a commit, and `end(E)` is `E`'s exit, of which there is at most
one. That is [`README.md`](README.md) §"Two commitments"'s `heartbeat(episode2, 500)` shape: order as data, never
position. Because the kind is in the name, `P`, `From` and `To` are required to be a commit or the run,
and **an exit cannot be extended**: a record that names `end(E)` as its parent fails the sort check on the
message alone — unwritable, not forbidden. A forged second exit for a claim lands on the same name: values
it lists differently read `{t,f}`, and a different outcome beside the first is two atoms at one key, a
conflict for whoever declares an exit functional in its claim, rather than a second ending under a name of
its own. And a commit whose parent is a node of its own claim must have the larger `K`, also a check on the
message alone, so a writer that reuses its counter cannot make `parent` cyclic; a cycle across claims needs
a record naming a parent that does not exist yet, which only a forger writes.

**The order among claims is data too.** `I` is the claim's place in its run's claim order, which the
compare-and-swap decides, the one coordinated act ([`open.md`](open.md) 5). Under one sequencer per run the claim's
sequence number serves as `I`, and a claim that resumes from another's nodes is admitted after it. A
claim's run is the root its `From` descends from, so `I` is compared among claims of one root. Identity and
order stay apart: a failed arbiter can leave a tie in `I`, a defect of the reports that read the newest
claim, but never two nodes under one name. How a claimant chooses `I` with no arbiter at all is
[`open.md`](open.md) 5's.

**The head is a report.** To read *"the run's series"*, a reader must choose a node to read along. The usual
choice is the head: the position the run's newest claim, the one with the highest `I`, last declared or
reached —

1. its exit, if it has one;
2. else the position of its highest-`K` record, a commit being its own and a rewind's its `To`;
3. else the `From` its claim declared;
4. and the run itself, before any claim.

Every input to that choice is permanent, but which claim is newest, and which of its records is last, is not,
so the head is a report, not a derivation ([`1-logic.md`](1-logic.md) §"Two layers"). Reusing a `K` across a commit and a
rewind is a writer's bug that only the head sees.

**The rest is derived.** Every metric is read through one relation, `at`, typed per metric, with the node
hoisted to one position ([`1-logic.md`](1-logic.md) §"Types", the wrapper):

```
parent(N, P)              :- commit(N, P, _).                    and likewise for exit
at(N, M)                  :- commit(N, _, Ms), M ∈ Ms.           and likewise for exit
told(at(N, M), neg)       :- commit(N, _, Ms), M ∉ Ms.           and likewise for exit
depth(R, 0)               :- R : Run.
depth(N, D + 1)           :- parent(N, P), depth(P, D).
ancestor_or_self(N, N)    :- N : Node.
ancestor_or_self(A, N)    :- parent(N, P), ancestor_or_self(A, P).
finished(H, A)            :- exit(H, _, _, completed, _), ancestor_or_self(A, H).
ended(E)                  :- exit(end(E), _, _, _, _).
ended(E)                  :- died(E, _).
```

**These are views, and nobody posts into them.** `parent`, `at`, `depth`, `ancestor_or_self`, `finished`
and `ended`, and `along` and `at_step` below, have no source but their rules. A post into one is a writer's
bug or a forgery: where the view has a negative rule it reads `{t,f}`, and elsewhere it reads as the view's
own atom, which is forgery's ordinary reach ([`README.md`](README.md) §"What it does NOT solve"). The negative rules
are of two kinds. `at`'s is the node's own vouching, told by the producer that knew. `along`'s, below, are
that view's completion — the explicit, scoped closure [`2-polarity.md`](2-polarity.md) §"How a party comes to know a
negative fact" permits, made once in the schema rather than inferred from anything's absence.

`finished(H, A)` says a completed lineage passes through `A`: through the run, when `A` is one, and through
any other node otherwise, a checkpoint for instance, so *"finish from here"* is the same question asked of
another node — one that, unlike the run's, also bounds where a scheduler may resume.

**How a claim ended is told from two viewpoints.** An exit is the claim's own word. A death report is a
third party's, which may post exhaustion knowing only that the process died — the test of
[`2-polarity.md`](2-polarity.md) §"How a party comes to know a negative fact" — but never the worker's own record. The
verdict on a claim is its exit's outcome if one is readable, else the death reports naming it: the join of
the two partial observers of §"What the problem domain forces". It is a report, since the *else* tests for
an absence.

A misspelt metric is an undeclared constructor, rejected on arrival; each metric has its own value sort;
and *"every metric at node `n`"* is the one pattern `at(n, M)`. The metrics are the ones the schema
declares. Sharing schemas between agents at runtime, so that new metrics appear without redeploying, is
out of scope.

**One record per event is what makes a node complete when present.** A node's parent, its claim and every
value it committed arrive together, so a node a reader finds on a lineage is all there. Runstate measured
the alternative: with values apart from the record that commits them, a reader saw 12 transient holes
where node records saw none ([`../lineage-graph.md`](../lineage-graph.md)). On an unordered transport nothing else closes
that hole, so here the values go inside.

**Every node vouches for its values, honestly.** By [`2-polarity.md`](2-polarity.md) §"The rule for posting", a key is a
claim about what determines the value, and **a node is a key that always does**, since it names one
execution of one step. So the vouching rule — *"0.31, and no other loss, and no other metric, at `n`"* — is
knowledge whether or not the computation is deterministic. It is *"complete here, and here is what is in
it"*, with the exceptions carried in the region's description, as [`2-polarity.md`](2-polarity.md) §"Not CWA, and not
local CWA" says such a record must. `Ms` is a set, so *"exactly these"* fixes no order. `M ∉ Ms` is
notation for the positive region over the declared metrics — every constructor absent from `Ms`, and the
values either side of each present one — wherever the value sort is closed or ordered, as
[`1-logic.md`](1-logic.md) §"Sort closure, and disequality" would write it. An unordered value sort, a `dict`, takes a
real disequality, legal in a constraint body ([`1-logic.md`](1-logic.md) §"The constraint domain") at the price
§"Sort closure, and disequality" names.

**Two more records serve the trace:**

```
checkpoint(N)         a producer saved N's state
eval(N, M)            a measurement of N taken after N was committed
```

`checkpoint(N)` records that a state was saved, not the state. `eval` is its own relation because a
commit's vouching closed `at(N, ·)` when it was posted: a later measurement under `at` would read `{t,f}`.

**The step is a view, not a key.** A node's depth is permanent, so the per-step series a reader wants is
a rule along one lineage, and so are its negatives:

```
along(H, S, M)              :- ancestor_or_self(N, H), depth(N, S), at(N, M).
told(along(H, S, M), neg)   :- ancestor_or_self(N, H), depth(N, S), told(at(N, M), neg).
told(along(H, S, M), neg)   :- depth(H, D), S > D, M : Metric.
```

The second is sound because a lineage has exactly one node at each depth, `parent` being functional; a
forged record under an existing name breaks that, and the result is `{t,f}` at the `along` atoms of every
lineage through it, contained per atom as any valuation conflict is. The third is sound because a lineage
ends at its head. The rules are monotone outright, `H` being one of their variables; which `H` to read is
the head's report, above. `S` is depth. A user's own step label, where it differs — a training step that
advances several per node, or one re-based on resume — is a metric like any other, `at(n, step(100))`. No
relation carries the step as a key, so the positional-indexing defect measured in §"What the
measurements say" is moot rather than fixed. What remains is that a range of steps is a walk along a
lineage (§"The honest cost").

**The coarse key survives as a view.** Two claims resumed from one checkpoint post **sibling** nodes:
separate facts, each vouched for by the claim that computed it. That is [`2-polarity.md`](2-polarity.md) §"The rule for
posting"'s finer key, and it pays that key's price: a disagreement does not read as a conflict. Reading
through the coarse key makes it one again:

```
at_step(R, S, M)  :- R : Run, ancestor_or_self(R, N), commit(N, _, _), depth(N, S), at(N, M).
```

Over that view, two siblings at depth 61 are two atoms at one key: a **domain** conflict, for whoever
declares loss functional in run and step. Both keys are available at once, the fine one as records and
the coarse one as a view. Through it a recomputed step reads as a disagreement exactly when it differs,
rewinds and relaunches included, so declaring that dependency is a determinism audit: honest where
re-execution should agree. It reads commits only, since an exit's step is not an iteration.

**Who computes and where it computes from are separate facts.** A claim names the computation, which the
compare-and-swap makes exclusive, and which holds liveness and whatever lives only in its process. It also
declares where the computation starts. Its commits advance that position, and a rewind restates it
without changing who holds the claim. A rewind is its own record because the head must move when a claim
abandons its tip, not only when its next node lands; without it, a reader would follow the abandoned branch
until then. The claim's liveness records are left out of this schema, since nothing here reads them;
§"Still wanted: leases" gives their shape.

## One demand, traced

Everything up to here has been argued rather than shown. Here is a single demand from posting to
settlement, so the rest has something to be about. Nothing in it is new — every step is a mechanism one of
the layers defends.

**A querier posts one record.** It wants run `r` computed to the end:

```
asked(∃H. finished(H, r)).
```

`H` is bound, so one witness is the whole answer, the case [`3-questions.md`](3-questions.md) §"The residual, assembled"
gives of a question that one answer empties. Its negative reading, *"`r` will never finish"*, is knowledge
nobody has, so nobody tells it.

**Until a witness arrives, the querier sees `∅`, and may conclude nothing from it.** That is not *"`r` has
not finished"* — it is *"nobody has said."* The querier may confirm it has **left** `∅` the instant a
witness arrives, and may never confirm it is **in** it.

**The scheduler, not the question, chooses where to compute from.** The question was asked, is unsettled and
is still wanted, so a producer is launched ([`3-questions.md`](3-questions.md) §"Demand is control"). An earlier claim, `e0`,
computed sixty nodes and saved a checkpoint at the last, `c(e0, 60)`, before its process died, so the run's
second claim starts there: `claim(e, 2, c(e0, 60))`. Choosing that checkpoint — one the scheduler can trace
to `r`, `ancestor_or_self(r, c(e0, 60))` holding in its store, since a checkpoint whose node or any ancestor
never arrived would resume a lineage with no path to `r` — is a report, computed locally on the control side
as the residual is. Lost or merely late, a missing ancestor costs the same: the scheduler falls back to an
earlier checkpoint it can trace, or to `r` itself, and pays in recomputation, never in a hole. The claim then
posts its choice as a fact about what happened, which is how a report's output enters the store
([`1-logic.md`](1-logic.md) §"Two layers"). The sixty nodes are not recomputed: `e` extends them.

**Each node it commits decides infinitely many atoms.** It posts `commit(c(e, 1), c(e0, 60), {loss(0.31)})`,
the node at depth 61. That makes `at(c(e, 1), loss(0.31))` true and, by the vouching rule, every other value
of every metric there false: one ground record read as the region [`2-polarity.md`](2-polarity.md)'s per-key complement
describes, infinitely many atoms *told*, not inferred. Nobody derived the negatives from the absence of
anything; the producer knew what it computed, and said.

**The run converges, and the question is settled — permanently.** Claim `e` ends with
`exit(end(e), c(e, 682), {converged(true)}, completed, {})` at depth 743, so `finished(end(e), r)` holds, and
the querier's read is `⊒{t}` on it, affirmed the only way anything is affirmed here: by exhibiting the
records. Its *"tell me when it is done"* fires here and not before. Another claim may later finish another
lineage of `r`, giving the question a second witness, but nothing can unfinish this one: settledness is
permanent under growth ([`2-polarity.md`](2-polarity.md) §"The threshold rule").

**The series is then a question about a fixed subject.** A querier wanting the loss curve asks along the
witness:

```
asked(∃V. along(end(e), S, loss(V))) :- S ≥ 1.
```

`S` is free, so the question ranges over every step — an **unbounded** extent; `V` is bound, so each step
wants *a* loss. It is settled the moment it is asked. Steps 1–742 have their losses; step 743 is the exit,
which vouched that it committed none; and past 743 the lineage has ended. The cover is finite — 743 records,
read through the schema's rules — though the region is not, which is what makes an unbounded question
dischargeable. Because the question bound `V`, it never asked whether a step has *other* losses — and it
need not have: the stronger question, with `V` free over `Float`, is settled by the same records, since
every node vouches for its values.

**A second querier posts the identical question, and nothing runs.** Identical questions are one record,
and this one is settled, so there is no work to hand anyone and no producer to launch. The store was the
cache; no cache was built.

**A question over nodes has a residual per node.** Steps are not keys, so a residual ranges over nodes. An
asker that wants the fifteen checkpoints along the finished lineage scored names them, since a posted record's
body holds constraints only ([`0-substrate.md`](0-substrate.md) §"The model") and a question must stay the same question
([`3-questions.md`](3-questions.md) §"Settledness of a question"):

```
asked(∃V. eval(N, accuracy(V))) :- N ∈ {n₁, …, n₁₅}.
```

An evaluator is handed the residual, not the question: if ten are scored, the other five reach it. An
asker that wants each checkpoint scored as it appears runs a rule instead, posting one such question per
checkpoint it finds, as an agent posts questions by rule ([`3-questions.md`](3-questions.md) §"A question is a tell of
`asked`").

**Watching the curve while it is computed is a subscription, not a demand.** A reader that wants each loss
as it lands follows the nodes of `r` as they arrive, reading along whichever head it chooses: its own rule
plus routing ([`3-questions.md`](3-questions.md) §"What to internalise"). Its head is a reader's report, outside the
demand, as the resume point is the scheduler's. Nothing about it is posted, so nothing about it has to
settle.

**And if two producers disagree.** Two claims that both resume from `c(e0, 60)` and compute different losses
post sibling nodes, `c(e, 1)` and `c(e′, 1)`. Neither contradicts the other, and a reader following one head
never sees the other. Through the coarse view of §"The schema" they are two atoms at one key,
`at_step(r, 61, ·)`: a **domain** conflict, visible to whoever declares loss functional in run and step,
and invisible otherwise. A **valuation** conflict takes two records under one *node* name — a forgery, or
a writer that reused its counter — each vouching for its own values. A loss that one lists and the other
does not then reads `{t,f}`: affirmable, inert, and poisoning nothing around it.

## Halting and withdrawing

**Two acts, one for each half of a claim.** A claim is a *who* and a *where* (§"The schema"), and halting
divides the same way. A **stop** names a claim, *who*: halt this computation. A **forbid** names a node,
*where*: compute nothing more from here. The need is measured. In runstate's lineage-graph tests, with two
or three claimants and no claim gate, another claimant computed one more step after a stop was honoured in
20 of 120 runs, and in one, a claimant that attached after the honouring exit treated the stop as spent
and ran to completion, a log indistinguishable from a legitimate relaunch
([`../lineage-graph.md`](../lineage-graph.md)). A stop addressed to *"the run"* cannot say which computations it means;
these two can.

```
stop(X, E)            a stop named X asks claim E to halt
refused(X, E)         claim E refuses stop X
forbidden(F, N)       somebody forbade extending N or anything below it
withdrawn(Z)          the secret Z, revealed by whoever drew it

revealed(hash(Z))     :- withdrawn(Z).
```

**Both are named, as a lease is, by a commitment to a secret:** `X = hash(Z)` or `F = hash(Z)`, for a
secret `Z` the poster drew, so revealing `Z` withdraws whatever its hash names, unforgeably and with no
ordering (§"Still wanted: leases"). `hash` in `revealed`'s head is a function of the constraint domain, as
`+` is in `depth`'s rule, evaluated once its argument is ground — and in a `withdrawn` record `Z` is always
ground, as a polarity is ([`2-polarity.md`](2-polarity.md)): a region over secrets would reveal every one, withdrawing
every stop, forbid and lease at once. A `withdrawn` record with a variable is rejected on arrival, a check
on the message alone.

**A stop is process control, discharged by name.** One name may cover several stops, so a stop is the pair
of its name and its claim, and is discharged pair by pair:

```
discharged(X, E)  :- stop(X, E), ended(E).
discharged(X, E)  :- stop(X, E), refused(X, E).
discharged(X, E)  :- stop(X, E), revealed(X).
```

Once `E` has ended (§"The schema"), by its own exit or a death report, nothing is left for the stop to halt.
Whether `E` honoured it is the exit's `Hs`, which answers the asker and discharges nothing: `Hs` lists the
stops naming `E` that it had seen when it exited, whatever its outcome, and with `preempted` they are the
reason. Before then `E` may refuse it, and its asker may withdraw it. `ended` is an observer's reading: the
claim itself acts on every stop naming it that is neither refused nor withdrawn, so a mistaken death report
hides nothing from it. No roster is needed.

**A forbid gates production, and is never discharged.** `forbidden(F, N)` is a positive fact about an act:
somebody forbade extending `N` or anything below it. No claim, commit or rewind that would do so is posted
by anyone who has seen the forbid and not `revealed(F)`. A running claim checks its control before each
commit, and on seeing a forbid over its position exits `preempted`, its exit being the one node it may
still add there, since nothing extends an exit. A scheduler does not launch there either: its control gate
reads forbids beside *still wanted* ([`3-questions.md`](3-questions.md) §"Demand is control"), though a forbid is not a
demand lapsing but a third party's veto, issued while the asker may still want the result. So a forbid
halts what is running in its scope as well as what would start there, and needs no roster, because it is
never discharged, only withdrawn by its poster's reveal. Whether a node lies below `N` is permanent, so a
rollback moves nothing. It is posted as what happened, not as a negative over every node that might extend
`N`: nobody knows that no one will, only that they forbade it. Who may post it is the authority rule
[`README.md`](README.md) §"What it does NOT solve" already owes.

**They overlap in effect, not in concern.** A forbid also halts the claims running in its scope, which is
how it needs no roster. A stop keeps what a forbid cannot give: an acknowledgement, a refusal, and *"halt this
attempt only"*, which leaves a relaunch allowed.

**Withdrawals pull opposite ways.** Withdrawing a lease asks for less; withdrawing a stop or a forbid
permits more. One reveal withdraws whichever of the three its secret names, and the poster chooses the mix.
So a forbid meant to outlive its poster's session takes a secret of its own, or the session's closing
reveal lifts it. An operator who forbids a run and stops its running claim as one act may share one secret:
the forbid halts the claim too, but only the stop is discharged, so the operator sees it land, and one
reveal undoes both. A forbid whose secret is lost stands for good, since nothing expires it. A secret once
revealed is spent: anything named by its hash afterwards is born withdrawn.

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
**claim**, where `send(expected_seq=)` is a compare-and-swap; and that is single-spawn, which [`1-logic.md`](1-logic.md)
§"CALM" already concedes as the one coordinated act. Nothing in **derivation** reads a sequence number: the
claim needs order, a fold ported as a report may still compare positions, and a reconnecting reader's cursor
is transport.

**And the claim's order is what the observer plane leans on.** Verdict, progress and liveness answer for *"the
latest claim"*, and without the compare-and-swap two concurrent claims have no order: in runstate's
lineage-graph tests the verdict and progress folds varied in 52–85 of 120 histories with concurrent claims,
even with every read seeing the full prefix ([`../lineage-graph.md`](../lineage-graph.md)). Node keys shrink what lineage reads need
from that order to one choice. Every answer about a given node is permanent — its values, its depth, its
parent and, for an exit, its outcome — and the report is which node is the head (§"The schema"). That needs
the order among claims, `I`, and each claim's own counter, `K`, both data in its records; the second needs no
coordination, and the first needs it only to exclude ties ([`open.md`](open.md) 5). The verdict needs one report
more, its precedence between viewpoints (§"The schema"), and liveness needs a clock.

**What is larger is the read side.** A per-functor term index that pattern-walks rather than key-looks-up,
a constraint solver in the read path, and a **coverage checker** that decides settledness and the residual
by finding a finite cover — incomplete checkers are safe, costing only spurious relaunches. (An earlier
draft also owed cross-host naming for the holes in posted terms; variables scoped to records removed it.)
Node keys add one more: a range of steps is a walk along a lineage, from a chosen head through `parent`
with a depth filter — linear in the depth, or an index of lineage membership that nobody has costed. That
is the honest headline cost, and it dwarfs the fold rewrites below. It is also the thing a reader should
weigh first, because everything else here is downstream of being willing to build it.

**No fold ports as-is.** Measured across the whole of `observables.py`: 13 of 16 fold readings are
non-monotone, and the operator responsible is `latest` = `argmax(seq)`, which appears seven times directly
plus three `[-1]`/`reversed` and two `max(…)` in 551 lines. Each becomes **dual plus subtraction** — the
monotone half derived inside, one complementation performed outside:

```
inside   discharged(X, E) :- …                  the rules of §"Halting and withdrawing"
outside  unhandled = stops − discharged         over pairs (X, E)
```

**The inside half was confirmed on the forms the folds had then.** 13 of 13 duals measured monotone, and
**9 of 9 folds reconstruct exactly** across every scenario tested, the worked example of the time
reproducing the then-shipped `undischarged_stops` verbatim, one subtraction. Those forms paired by
position; the rules above name what they answer, and §"Runstate, mapped" compares them with runstate's.

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

**Node keys name the right dual, and leave the wrong one tempting.** Per node, `depth(N, D)` is permanent,
so *"the lineage ending at `h` has `D` steps"* is monotone for every `h`, and a rollback moves the head to
an earlier node rather than shrinking any number under a key. But the head-free dual — *some* node of the
run reached depth `K` — is still the high-water mark. An abandoned branch that reached 900 satisfies it
while the head's lineage is at 600, and a series read along the head then splices: the question was
answered on one branch and the series read on another. So the head is chosen first and every read taken
along it. Runstate's layer 2 makes that unavoidable: its `prefix_len` takes the head as an argument.


## What a library adds over a database

Three jobs, one of them not commodity:

- **persistence** — a store dies with its process, and runstate's whole contribution is durable identity
  outliving processes;
- **indexing** — noting that per-position term indexing over heterogeneous terms is not a database
  feature, and neither is unification;
- **an oracle channel whose outputs are timestamped into facts about the past** — the OS probe, the clock,
  the temporal delta, the fixpoint test ([`1-logic.md`](1-logic.md) §"Two layers"). The store can tell you what happened;
  it cannot tell you that *nothing* happened, and `ensure` needs exactly that.

The third is the answer to *"why this library rather than Postgres plus a type discipline."* The buildable
object is closer to **a Prolog with a durable fact base and a pid probe** than to a schema. Coherent to
want; large to build.

## Runstate, mapped

Shipped and decided are kept apart. **What ships** is runstate as it is, on log format 0.3.0. **What layer 2
has decided** is the walkthrough recorded in [`../../review-2026-10-04-commits-and-lineage.md`](../../review-2026-10-04-commits-and-lineage.md) §12, not yet
shipped.

### What ships

**`ensure` is the one place the library's core operation leaves the fragment.** Its loop condition is a
threshold claim on `progress`, a *retractable* quantity, and its two termination guards are a **temporal
delta** (*"nothing new was derived"*, which has no positive form) and an **inflationary fixpoint test**
(*"another lap can only reproduce them"*). Neither is expressible in the fragment — each compares two
moments, which no growing set of facts can do — and both feed demand, because both decide whether to
relaunch. [`1-logic.md`](1-logic.md) §"Two layers" is where that belongs.

**Exhaustion arrives four ways in runstate today, and is inferred a fifth.** It arrives as a worker's own
`lifecycle.stopped`, an observer's `launcher.terminated`, a pid probe, and on Postgres the release of the
episode's advisory lock; heartbeat staleness infers it. The first two exist only if somebody volunteers
them, and the probe **abstains off-host**. The lock observes a death across hosts, but runstate keeps it a
Watcher signal, never a claim gate ([`../../specs/channel-postgres.md`](../../specs/channel-postgres.md)). Staleness is an inference, and
[`../../dead_ends/failure-detector.md`](../../dead_ends/failure-detector.md) shows it must never arbitrate a claim. That is the launcher-versus-lifecycle
split this library already has, kept orthogonal for exactly the reason [`2-polarity.md`](2-polarity.md) §"How a party comes to
know a negative fact" gives: exhaustion is a fact about a process, never about what exists.

**What ships already carries its extent as a constraint.** A subscription with an `until` is one durable
record denoting a bounded region — durable across the *producer's* death, because a worker re-drains the
control log and re-registers whatever is still unanswered, pinned by
`tests/test_run_episodes.py::test_relaunch_extends_one_series`: one subscribe posted *before episode 1
exists*, two episodes, ten steps, one series. `{"every": …}` with no `until` is schema-legal, documented
as *"forever"* — the unbounded region of [`3-questions.md`](3-questions.md) §"Quantifiers live in questions", concrete rather
than hypothetical. (The bare subscribe is served by a poll of the register, `self._values.get(name)`, not
by anything waiting on a production; that is the gap between the shipped library and this design.)

**Stops address the run.** Runstate's `control.stop` names no claim; the claim's compare-and-swap makes
*"the run's computation"* one claimant at a time. Since log format 0.3.0 a stop is spent by name, by the
exit that honours it or by a `nak`, and a stop its claim did not honour — unseen at exit, or outlived by a
death — stays pending for the next claim.

### What layer 2 has decided

**Who and where.** The claim carries its resume point, `resume_from`, and a rollback is its own record,
`lifecycle.rewind` (§12, items 3 and 4): §"The schema"'s split of a claim into who and where. Layer 2's count
of commits along a lineage, `{commits: n}`, is the lineage's depth less the exits on it, since an exit that
neither completed nor discarded a torn iteration can be extended there (§12, item 13), where this design's
exits cannot. Its step is that count, not depth; whether this design's should follow is open.

**Values apart from their commit.** Layer 2 keeps a commit's values in records of their own, named by the
commit, and closes lineage-graph's hole by ordering the commit after its values, on a transport that shows
every reader a prefix (§12, group A and item 1). This design's transport promises neither, which is why
its values go inside the commit.

**A death report, and with it the join this file opens with.** A third party that saw a worker die posts a
death report naming the claim (`died(E, Why)` here), beside `supervisor.terminated`, which names a launch.
Without it, a reaper that could name no launch wrote the dead worker's own `lifecycle.stopped` for it: a
forgery in form ([`README.md`](README.md) §"Two commitments"). The verdict is §"The schema"'s: the worker's self-report if
one is readable, else the external reports (§12, item 5). Its *else* is the emptiness test of §"The honest
cost"'s residuals. The report ends its claim, as `ended(E)` does here, and is written from evidence of the
death, never inferred from staleness or silence (§12, items 13 and 14). Unlike here, a worker that reads a
report naming its own claim stops writing, so a mistaken report costs at most the iteration in flight, never
two live writers.

**Withdrawing a stop.** Layer 2 adds the asker's `control.withdraw`, which spends a stop by name
(§12, item 10b), so any party that can name a stop can withdraw it; §"Halting and withdrawing" withdraws by
reveal instead. Its stops also address a claim, not the run, so a stop the claim did not honour lapses with
it, and the next claim is a forbid's business. Layer 2 has no forbid.

**Sources and eager streams.** The shipped `control.subscribe` is the record [`3-questions.md`](3-questions.md) §"Demand is
control" replaces: a read, a route and a trigger in one record, whose `request_id` survives only as a routing
handle. Layer 2 narrows the gap the shipped subscribe leaves. A subscription there targets a **source**, a
value that exists on demand and is sampled when asked, or nothing: a pure demand, which only keeps a worker
serving. Either is a demand; values told anyway are read from the log and never subscribed, which is the
reader's own routing. That is [`3-questions.md`](3-questions.md) §"What to internalise"'s split between demand and
subscription. A source with no value yet waits instead of answering `None`. What remains is the gap itself,
made smaller: a source is still computed every iteration, and only its logging waits on demand.

## Still wanted: leases

[`3-questions.md`](3-questions.md) §"Demand is control" leaves *still wanted* to a scheduling policy. runstate already has
both kinds of demand: `relaunch_if_needed` serves **durable** demand and `ensure_served` serves **leased**
demand ([`../../specs/lazy-launch.md`](../../specs/lazy-launch.md)). The policy:

- **A lease is a duration, renewed by the asker** — posted periodically, one way, as today's heartbeat is.
  No reply, so no round trip. Durable demand is a lease of unbounded duration, and its risk — a question
  that never settles, relaunched forever — is [`open.md`](open.md) 10's admission rule, not a lease problem.
- **The scheduler times it from local receipt**, on its own monotonic clock: a question is still wanted
  while less than the duration has passed since the scheduler received the latest renewal. No clock is
  compared across hosts, matching runstate's original liveness design, where the Watcher knows when a
  beacon arrived *"because it was there"* ([`../../specs/observer-clock.md`](../../specs/observer-clock.md)). The known cost is the same
  bounded one runstate accepts in [`../../specs/time-lease-boundary.md`](../../specs/time-lease-boundary.md): a scheduler that attaches after a
  dead asker's last renewal receives it as fresh, and the question looks wanted for one more duration.

- **The lease is an argument of the question record**, so a renewal is just the question asked again:

  ```
  asked(Q, lease(C, J, D))      C = hash(k), for a secret k the asker drew at random
  withdrawn(k)                  withdraws every lease whose C is hash(k)
  ```

  The questions layer's `asked(Q)` is the projection `∃C, J, D`, so *"Q was asked"* is derived rather
  than kept as a second record. A separate renewed `wanted` record beside a permanent `asked` behaves
  identically and is one relation more. `J` only keeps renewals distinct: without it, renewal eight is the
  same record as renewal seven, and set semantics hides the arrival the scheduler times from.
- **These are ordinary facts; *still wanted* is the reading.** Both records are posted like any other and
  stay true forever. They say *"wanted for `D` from when you receive this"* and *"lease `C` is
  withdrawn"*, never *"wanted now"*. Only the scheduler, holding a clock, turns them into a gate, which is
  what [`3-questions.md`](3-questions.md) §"Demand is control" requires.
- **An asker can withdraw early, by revealing its secret.** A lease is live while its latest renewal is
  younger than `D` and no `withdrawn(k)` with `hash(k) = C` has arrived — `revealed(C)`, in §"Halting and
  withdrawing"'s terms. This assumes every asker can draw unguessable random numbers, and a one-way hash.
  Content-addressed run ids already assume a collision-resistant hash; this needs preimage resistance, which
  the usual hashes also give. What it buys:
  - **Withdrawal is unforgeable on a public store**, with no private channel. A plain session id would sit
    in every renewal, and any reader could withdraw under it. Renewals stay forgeable, which is harmless:
    forging one grants nothing that asking `Q` directly would not.
  - **Withdrawal needs no ordering.** `C` is fresh, and never reused once revealed, and a reveal is
    permanent, so a withdrawal beats a renewal that arrives after it. Runstate's layer 2 gets this half by
    name instead: a request id is never reused (log format 0.3.0), and `control.withdraw` names the request it
    spends, for good. It does not get unforgeability, since any party can withdraw any request it can name, so
    hash-reveal stays its backlogged upgrade.
  - **Several askers of one question cannot withdraw each other**, since each holds its own `k`, and no
    roster is kept.
  - **The asker chooses the granularity.** One `k` per question withdraws questions singly. One `k` shared
    across a session's questions withdraws them all with one reveal. A rule that derives a question from
    another can pass the parent's `C` through, so the derived question is withdrawn with its parent.
  - **Stops and forbids share it,** named by the same commitments (§"Halting and withdrawing").
- **Withdrawal adds to lapse; it does not replace it.** An asker that crashes cannot reveal anything, so
  expiry stays the backstop. The pair mirrors runstate's `lifecycle.stopped` for a clean exit and heartbeat
  staleness for a crash. Runstate's layer 2 also records the lapse, in the `expired` field of the node at
  which a subscription ended: the worker dating its own observation that it ended, which is [`1-logic.md`](1-logic.md) §"Two
  layers"'s repair — timestamp the observation and make it a fact about the past.
- **The same mechanism runs the other way.** A lease is an asker telling the scheduler how long its demand
  lasts; a liveness promise is a worker telling observers how long until its next sign of life. Runstate
  has backlogged exactly that form ([`../liveness-promise.md`](../liveness-promise.md)): each liveness record carries
  `next_within`, a duration timed from local receipt, `null` for no promise. One mechanism, timed the same
  way, in both directions.

## What the measurements say

Every corpus figure the layers lean on, kept here so the layers themselves stay workload-free.

**Terms, measured** (for [`0-substrate.md`](0-substrate.md) §"The model"'s *"terms, not blobs"*). Measured on 200k rows, a
JSONB key with a btree expression index runs the central range query in **0.085 ms** against a positional
term layout's **0.089 ms** — the term buys nothing, the btree does. Worse, positional indexing over
*heterogeneous* terms is not merely slow but wrong: with `loss(Config,Step)`, `grad(Config,Layer,Step)` and
`ckpt(Run,Config,Shard,Step)` the step axis sits at three different positions, and an axis-blind positional
range returned **132,879 rows against a correct 91,500**. [`1-logic.md`](1-logic.md) §"Types"'s hoisting wrapper is the fix.

**What keeping every record costs** (for [`1-logic.md`](1-logic.md) §"Constraints are asked"). Measured over **823 real
logs, 2.5M records**: the compression given up is **0.34%**, and cells whose values genuinely fail to join
are **0.072%**. Both are a footnote. Where the partial case *does* land supports keeping the atoms —
**1,714 of the 1,719 divergent cells are `status`**, an app event mirrored onto the value plane at a reused
step, where last-write-wins reports a run as *saving* at step 87 of training. Conflict is reachable without
forgery: two honest producers differing by one ulp (`0.30000000000000004` vs `0.3`) do not reconcile, and
`mycooc/analyze_run.py` already hand-rolls a guard against exactly this. The corpus has **16 hand-rolled
guard sites**, sixteen per-relation conflict declarations rather than one missing primitive.

**The value plane's sorts** (for [`1-logic.md`](1-logic.md) §"Types"). Measured over 821 real logs: 24 distinct value
names, **none carrying more than one sort** (21 `float`, 3 `dict`), and no new names in the corpus's second
half. So the entire measured value plane is **two** relations — `metric(Name, Float, Step)` and
`event(Name, Json, Step)` — both fixed shapes with the name as **data**; the aliasing objection never
arises, because the partitions are separated by *relation* rather than by name. The case that motivates
per-functor sorts is genuine — `mycooc`'s `permutation` carries `None` under one flag and a nested record
under another, in **source** — and it is a hazard the rule forecloses rather than damage it repairs, since
**0 of 24** names show sort drift. The two-relation shape avoids aliasing but leaves the name in an open
namespace, where a typo is a new atom; §"The schema" puts each declared metric in its own constructor
instead, which the measured names — all known when the schema is written — fit without exception.

(The 821-vs-823 discrepancy is unexplained; the two figures may be one corpus counted twice.)

**A relaunch loop the guard did not stop** (for [`3-questions.md`](3-questions.md) §"The residual, assembled"'s residuals that
never empty). When runstate's `control.target` design was refuted ([`../../specs/control-target.md`](../../specs/control-target.md), R5), a
relaunch loop its no-progress guard did not stop made **373 spawns in 3 s**: the failure shape of a residual
that never empties, there because the guard could not fire on time targets.
