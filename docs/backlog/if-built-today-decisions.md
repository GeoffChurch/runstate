# Decisions and retractions — `if-built-today.md`

Companion to `if-built-today.md`. The target sketch states **conclusions**; this file records **what was
tried, what was withdrawn, and why**, so that neither is re-proposed and neither has to be re-derived.

Split out 2026-08-15. Before that, the sketch narrated its own corrections inline at 29 sites, which is
what made it read as unstable. The narrative is worth keeping — it is what stops re-proposal — but it is
not the design.

Refuted *ideas* with diagnosis live in `../dead_ends/`. This file is for decisions **inside** a surviving
design: things narrowed, reversed, or renamed.

---

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
negative fact — it is just not the semantics of falsity. It costs a query about network membership, hence
a coordination round, priced exactly; it is paid once, scoped per functor; and the conclusion is posted as
an ordinary `¬Q` that everything downstream reads coordination-free. See the sketch's §"Falsity is told".

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

## The demand quantifier

**Withdrawn: the sketch's two readings, which contradicted.** §"An unsatisfied existential is demand" read
a demand existentially; §"Demand subsumption" read it universally. Both were stated flatly.

**Resolved: the quantifier is a property of the posting, not of the term** — which the sketch already said
elsewhere. A **bare pattern is `∃`**; a **`∀` demand must be *finitely coverable***.

**And "bounded" was wrong twice over**, in the first repair. *Named* is a re-description — the durable
record is named by its `request_id`, which the schema requires of every subscribe. *Bounded* is admission
control wearing a quantifier's clothes, and it forbids the demand this design is best at expressing:
*"every step, run to convergence, and tell me when there are no more."* The right precondition was
already present: **`ground(Q)` need not be finite; the cover must be.**

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

## CALM, read against the proof rather than the slogan

The `iff` is confirmed (Ameloot, Neven & Van den Bussche, Cor. 13). Three things around it were wrong.

- **Withdrawn: the unqualified `iff`.** It holds in a model where the partition is arbitrary and *unknown
  to the program*. Give nodes the partitioning policy and the class grows; give them the global active
  domain and **every computable query is coordination-free**. The `iff` is a statement about ignorance.
- **Withdrawn: "no round trips", and "CALM says it must cost a round".** Coordination-freeness is
  **existential over placements** — for every input there *exists* a partition needing no messages — not
  a promise that a real run sends none. The authors warn against exactly that reading and exhibit a
  coordination-free transducer that communicates on the obvious placement. The predicate is binary; the
  paper prices nothing.
- **Withdrawn: reading the theorem as an operator ban.** It constrains the computed **query**, never the
  operators. *"We use deletion to start afresh. Since the query is monotone, no incorrect tuples are
  output"* (Thm. 6(4)'s proof) is the reference formalism using deletion internally. So a residual
  computed **inside** an agent is fine; **outputting** one would not be.

**Also withdrawn: that "needs `All`" is stronger than "is non-monotone".** Cor. 17 composed with Cor. 13
makes them the same statement, and makes `All` and `Id` symmetric. It is more *vivid*, not more evidence.

**And withdrawn: that told falsity buys coordination-freeness.** The "extra knowledge" in the
weaker-monotonicity hierarchy is a **system relation** describing the distribution policy, and its
mechanism is *"policy says a matching fact would be here; it is not; therefore it does not exist"* —
falsity **inferred from absence**, the exact move this design forbids. The design does not need the
hierarchy because its computed query is monotone and it is already at the base.

## The banner

**Withdrawn: "geometric logic, which is what this language is".** It is **definite clauses** — one atomic
head, always. No `∨`, `⊥`, equality or `∃` in a head. That is two rungs below the banner: `∨`-in-heads
separates geometric from coherent, and `⊥`-in-heads separates Horn from definite.

**And lower is the point.** Being low buys the tractable corner, the placement strictly inside the
coordination-free class, and the no-value-invention property CALM's proof depends on. The banner pointed
up while the design's virtue points down.

## Smaller reversals, recorded so they are not re-tried

- **Reclamation is not retraction.** Evicting a *re-derivable* atom changes what is stored, not what is
  true. What the reclamation layer owes is a **caching invariant**, and the tiers invert what storage cost
  suggests: derived atoms evict freely, produced base facts cost a re-run *if the producer lives*, and
  **absence claims may be unrecoverable** — evicting one descends `{f} → ∅`, the single forbidden descent.
- **Coverage is an entailment claim, never a membership claim on the solution set.** A region with a hole,
  read generously, covers every atom it *might* contain, so binding shrinks it — measured, **12 of 12**
  bindings descend, against **0** for the entailed reading.
- **The dead-end verdict on the per-producer answer tail was too strong.** It is the right *structure* in
  the wrong *representation*: one termination marker per producer per stream is precisely `¬Q`.
- **`every` is a delta, not a stride** — so exposing it on `ensure` is *not* an additive change, and the
  emission filter's deferral is load-bearing. See `memoizer-index-algebra.md`.

## Prior art that narrowed the claim

Full verification ledger: `../if-built-today-citations.md`. In short, and welcomed rather than resisted:
the **store** is 4QL's Definition 5 and appears in a TODS proof (`R_notMsg`/`R_notMem`/`R_known`); the
**logic** is a fragment of Jakl's, with soundness and completeness proved; the **regions** are Kanellakis,
Kuper & Revesz's; **CCP** is the ask/tell model and dropped the consistency structure first, though it
then made `false` an explosive top; **Belnap** supplies the vocabulary verbatim.

What is not theirs: falsity **asserted** as the primitive act, refusing the tombstone **chain** so that
*told-both* survives, demand-driven production with the store as cache, and the distribution — no party
roster and no self-identity.
