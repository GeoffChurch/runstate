# Decisions and retractions — [`../2-polarity.md`](../2-polarity.md)

What was tried in this layer, what was withdrawn, and why. Conventions in [`README.md`](README.md).

## The big one: falsity is told, not inferred

**Withdrawn: falsity derived from unanimous refusal plus a closed producer set.**

The sketch once made global falsity an aggregate — *"false iff **every** producer refuses"* — over a
producer set that a separate "scope closure" declared complete. Two levels, both required.

**Why it went.** Unanimity is the De Morgan dual of *"true iff some producer produces"*, and that dual is
only correct if *will not* is the **complement** of *produce*. Once refusal means **determination** — a
positive claim about the world — the two are independent assertions and aggregate by **union**, not by a
dual pair. Measured over every stance-vector up to four producers: the unanimity rule **descends 8 times
in 360 extensions** of a growing producer set (witness: one refusal gives `false`; a second producer
appears and it drops back to `unknown`), where union descends **0**. Union is also at least as informative
at all 121 vectors and strictly more at 86 — of which **64** are cases unanimity reported as plain `true`
while some producer had determined the region empty.

**What it cost, honestly.** `{f}` now arrives on **one** party's word where it used to take everybody.
That is not a change in *forgery* cost — a forger could always post the whole family — but in
**honest-mistake** cost. The counterweight is that the mistake becomes visible as `{t,f}` the moment
anyone produces there, where under unanimity a wrong refusal only ever contributed to a conjunction and
nothing contradicted it.

**What came back, reclassified.** A **closed producer set** is still a legitimate way to *come to know* a
negative fact — it is just not the semantics of falsity. It needs to know who all the producers are, which
is the class of query a coordination-free program may not ask; that cost is paid **once**, scoped per
functor, and the conclusion is posted as an ordinary `¬Q` that everything downstream reads
coordination-free. See the sketch's §"Falsity is told", now [`../2-polarity.md`](../2-polarity.md). (Not *"priced in rounds"* — see §CALM in [`1-logic.md`](../1-logic.md).)

## Names that argued against the design

Three in a row, all the same error — a name that carried a reading the design had abandoned.

| withdrawn | why | now |
|---|---|---|
| `closed(Q,p)` | ambiguous between *"p will send no more"* (about a process) and *"there is nothing in Q"* (about the world). The aggregation used the first, the conflict table the second | split |
| `done(Q,p)` | symmetric naming implied exhaustion and falsity were one kind of object differing in content, when the finding is that they are different kinds. And **a name is an invitation**: give exhaustion a predicate and somebody will quantify over it | not a predicate at all |
| `absent(Q,p)` | a wrapper predicate is a fossil of falsity being *derived*; and *absent* names falsity as a **lack**, the reading the section exists to delete | post `Q`, or post `¬Q` |

**And the producer argument went with the third.** Union does not quantify over producers, so it needs no
index into that quantification: `p` and `q` determining the same region empty is **one** atom posted
twice, and set semantics collapses it correctly. What `p` was buying is **provenance** — wanted equally
for positive facts, since `loss(60,0.5)` names nobody either.

Two claims withdrawn as a consequence: coordination-freeness comes from the claim being a **testimony
rather than a survey**, not from its naming an author; and the settledness ownership rule does not get
re-anchored to the author, it simply does not survive. Posting a false `¬Q` is the same kind of defect as
posting a false `loss(60,0.5)`, under the same enforcement, which is none.

## CWA, and the lineage that turned out to be wrong twice

**Withdrawn: `¬Q` is a Clark completion.** Clark's is per-predicate over *all* clauses and cannot be
false; this is per-contributor, asserted at runtime, by a party that may be lying.

**Withdrawn: the local-completeness literature is the right lineage for the semantics.** It is not, and
the reason first recorded here was also wrong. An earlier note said *"LCW must be retracted as the KB
grows"* — verified against Denecker, Cortés-Calabuig, Bruynooghe & Arieli (TODS 35(3), 2010), that is
**false**, and the paper says the opposite twice: LCWAs are *"a more permanent form of knowledge than the
transient data"*, and *"just like integrity constraints, are fairly constant during the lifetime of a
database."*

The true differentiator is better than the withdrawn one. Post an atom inside a region declared complete
and **nothing is retracted, nothing is violated, no inconsistency arises** (their Prop. 4: a locally
closed database is *always* consistent) — the negative conclusion simply **stops being entailed**,
silently, with no event to observe. `¬Q` does the opposite: the record stands and the region climbs to
`{t,f}`.

**Also withdrawn: calling an LCWA an "inference licence."** That reconstruction's entire contribution is
that it is not one — its semantics at a store is a plain first-order sentence, and Levesque's `K` operator
is shown *eliminable* in favour of it. Their own phrase is stronger and is a quote: **"a nonmonotonic
construct."**

**And two attributions corrected.** Cite **Levy 1996** as the LCW exemplar, not Etzioni/Golden/Weld —
theirs is Motro's completeness-constraint form, and they store *explicit negative literals*, which makes
them a poor foil. And *"query containment"* is not that literature's term: Levy reduces to
**query-update independence**, Motro-style constraints to **answering queries using views**.

## The four values

**Demoted twice.** First from a codomain the logic computes with, to a derived reading; then from a
framing to vocabulary.

**Why.** Measured: the monotone predicates on the four values are **exactly** the six a rule can express
over the two polarities with `∧` and `∨` and no negation — six of sixteen, and the same six. So the four
values never enter the logic. And `∅` is **unaffirmable** — twice over, as a down-set and via CALM — so
one of the four can never be observed and the other three are conjunctions of two presence checks.

**A four-valued logic whose bottom you cannot observe is not a good thing to organise a document around**,
and organising around it is what invited three failed topological framings (`../dead_ends/`).

**What survives.** The vocabulary — *told true / told false / told neither / told both* — which is
Belnap's verbatim and which citing 4QL or Jakl requires. And the **extent pair** as objects, because the
unwritability of `= {f}` and the unanswerability of `∅` both need them.

## Covering, deciding, and an over-claim

**Withdrawn: "positives decide one atom each, negatives decide a region each" as a structural fact.** A
positive post *can* decide a whole region — `loss(S, 0.2) ∧ S > 400` is pure `∀` and decides infinitely
many atoms. The polarities are symmetric in what the logic permits.

**What is true is informational, not logical:** *absence is uniform, so it always compresses; presence
compresses only when the values do.* A loss curve's do not, which is why *in practice* a finite cover is a
positive prefix plus a negative tail — **a fact about scientific data, not a theorem about the logic.**

**What is separately true and was conflated with it:** covering is not deciding. A partially instantiated
positive post is existential and **decides nothing** — it covers `loss(3,V)` while leaving every atom in
it at `∅`.

**And the apparent quantifier obstacle was an artifact.** Reading a region as pure `∀` is right; the
alternation only appears if a free value position is existentially closed. That form — *"every step past
400 has **some** loss"* — is the existence half of a **functional dependency**, which §"No functional
dependency" refuses on independent grounds. The fragment and the ban forbid the same sentence.

> **Scoped 2026-09-30** ([`3-questions.md`](3-questions.md) §"Record scope"). Both paragraphs above hold of *told* sentences, and still
> do: no posted record is existential. *"Every step has some loss"* is not refused as a **question** —
> nobody tells it, and settling it needs a witness or a refutation per step, not a dependency. And the
> partially instantiated positive post that "decides nothing" no longer exists: every posted record is a
> region and decides what it covers.

## Smaller reversals, recorded so they are not re-tried

- **Coverage is an entailment claim, never a membership claim on the solution set.** A region with a hole,
  read generously, covers every atom it *might* contain, so binding shrinks it — measured, **12 of 12**
  bindings descend, against **0** for the entailed reading.

