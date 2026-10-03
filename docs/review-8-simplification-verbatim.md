<!--
PROVENANCE (not part of the review). Untracked file.
Eighth review, commissioned 2026-08-11 alongside the prior-art review (review-7) and run concurrently
with it. Two jobs: find simplifications, and attack the material developed after review 6.
It declares the collision with review-7 itself and credits the overlap. Verbatim.
-->

# Adversarial review 7b — `docs/backlog/if-built-today.md`

## VERDICT

**The largest simplification available is a 70-line deletion, and it is not where the doc is weak — it is where the doc is correct.** §"Why merging is declined" spends 64 of its 70 lines costing out an option its first six lines already delete, on a premise (fabrication) that is §"No functional dependency" applied to the term order. Nine further collapses follow, of which four remove sections outright.

**The new material fails in one place, and it fails on the same fault line as review 6 — one level deeper.** The doc absorbed review 6's *"extent, not question"* repair and answered the residual defect with *"that is a forgery."* Measured: it is not forgery, and the defect is worse than review 6 reported. `closed(Q,p)` over an extent is **non-monotone** — 26 previously-affirmed absences retracted when a live producer is re-demanded — so the doc faces a genuine dilemma: either closure goes stale, or a producer that was only ever asked for steps 0–100 permanently forecloses steps 101–1000. **A repair exists, is measured, is monotone, and uses only machinery the doc already owns** (timestamped observations + the internalised demand relation, which currently has no reader). It also makes masking and the CWA scope the same restriction.

**Second failure: "propagate, never label" does not delete the budget, it moves it.** Measured in SWI clpfd, propagation alone — no labelling anywhere — on a **two-variable, two-constraint** problem costs inferences *linear in the numeric size of the domain*: 4.55 M at N=10⁵, >30 s at N=10⁶. And on the design's own unbounded step axis the same constraints propagate instantly and **decide nothing**. Review 6's timeout-read-as-refutation is therefore not gated on an incomplete propagator; it is gated on domain magnitude.

**The valuation reading is sound but its two levels are one object,** and its monotonicity claim is false as written — measured, 8 descents once the producer set is allowed to grow. Fixing it is the same fix as collapsing the two-level closure.

**One collision to declare.** While I worked, a concurrent session wrote `docs/review-7-prior-art-verbatim.md` into the same repo. It independently reached my sheaf findings. I have demoted mine accordingly and report only what I add. It also asserts, as the design's *"defensible novelty"*, that `closed(Q,p)` **cannot go stale** — my finding A1 measures that this is false.

---

# JOB 1 — SIMPLIFICATIONS

## S1. §"Why merging is declined" is §"No functional dependency" applied to the term order — and 64 of its 70 lines price an option the first six delete. READ.

**What could go:** `:74–143`, in full (70 lines, 6% of the document): the order-dependence example, the clique/first-fit identification, the second lemma, the best/worst/average table, the crown at `⌈log₂n⌉` positions, Moon–Moser, and the "real room above it" paragraph.

**What it collapses into.** The decisive argument is six lines and is a pointer: *"Merging is sound only if the two posts describe **one** thing — which is a functional dependency, and §'No functional dependency' says why that cannot be asserted here"* (`:77–79`). And §"No functional dependency" already contains the general form: a join may be quotiented **only where it is the declared semantics** (`:584`, `:591–597`). Terms are the partial case, where *"there is nowhere to put the result, and completing it is the only way out"* (`:599`), and the free completion is taken. That is the whole decision, in two sentences, derived from a section that stands on its own.

The remaining 64 lines are governed by the doc's own concession — *"Two things are wrong with it, and only the first is decisive"* (`:75–76`) — and by *"there is real room above it **if the fabrication problem were ever solved**"* (`:138`), a condition §"No functional dependency" says is unmeetable. Correct mathematics, verified by review 5, attached to a decision it cannot affect.

**What is lost.** The reassurance that *were* merging admissible, its cost is bounded (1.04–1.32 measured, crown separation at 12 positions). That is a real result and belongs in `docs/dead_ends/`, not on the critical path of a target sketch.

## S2. Containment compaction has no remaining job anywhere. The notification rescue does not hold. READ + INFERRED.

**What could go:** `:282–284` (the notification rescue) and the compaction half of §"Demand subsumption", `:789–802`.

The doc already deletes compaction from the production path (`:279–281`). It then rescues it: *"Compaction still earns its place in **notification**."* That rescue fails on three of the doc's own commitments:

1. **Deliveries are invariant under compaction.** *"Two queriers independently posting `loss(60,V)` hold **distinct** terms with distinct tails … the producer must bind both"* (`:385–387`). Dropping a subsumed *demand* does not drop its *subscriber*.
2. **Lookup is already output-proportional.** Finding which cursors sit above a new leaf is a walk up the trie (`:846–849`). Fewer cursors would not make that cheaper per delivery.
3. **The sharing that does help is variant keying, not subsumption** — the call table of `:388`, which is exact and free.

Meanwhile `:789–802` still presents compaction as live, contradicting `:279–281` directly.

> **Corrected statement.** `:282–284` is **wrong**. Notification is served by the per-functor term index plus variant keying, both already in the design. Combined with `:279–281`, containment compaction has **no consumer at all**, and with it goes review 6's finding 4 (whose 2.42×-the-minimum result priced a mechanism that need not exist). What survives of §"Demand subsumption" is the **contravariance itself**, needed for closure transfer, and the anti-unification warning as a recorded dead end.

## S3. The sheaf paragraph is vacuous on the design's own space; where it is not vacuous it is §"Continuous carriers". MEASURED.

**What could go:** `:345–359` (15 lines).

The topology is fixed by the doc itself: a demand denotes an open, and *"the maximal elements of `↑p` are exactly `p`'s ground instances"* (`:749–751`). So the opens are generated by `ground(p)`. Measured (`p4_space.py`, signature `{a,b}` + unary `f`, depth ≤ 3): **8/8 singletons are basic opens**, so the topology is **discrete**. Measured (`p5_boolean.py`): its frame is the full powerset, **256/256 opens complemented** — a complete *Boolean* algebra.

On a discrete space: "open subspace" = *any* subset; a sheaf is exactly a family of stalks; gluing is pointwise agreement. Every bullet is a re-description of "a partial function on the agent's demand set." The space is non-discrete exactly when a value sort is a continuous domain — which §"Continuous carriers" already handles.

## S4. The two-level closure is one level, and making it one is what makes the aggregation monotone. MEASURED.

**What could go:** the second row of the table at `:199–202`, the necessity of `spawns(P,Q)`, and the write-authority worry at `:251–253`.

Model the index set as **all possible producers**, an unspawned one holding stance *might* everywhere. Then *"no more producers for `loss`"* is exactly *"every unspawned producer refuses every `loss` atom"* — `closed` again, asserted in bulk over the complement of the spawned set. One predicate, one fact form, one aggregation.

Measured (`p2_valuation.py`): with a **growing** index set the aggregation suffers **8 descents** (witness: `[refuse]` → `false`; a producer appears → `[refuse, might]` → `unknown`). With the index set **fixed to all possible producers and closure modelled as `might → refuse`**, **0 descents** over the same 312 pairs. The collapse is the precondition of the doc's own monotonicity claim.

**Stronger version, offered as a question:** §"Types" argues signatures must be program-level, not data. The identical argument applies to the producer set. Producer *kinds* are program-level; only instances are dynamic. If kinds carry extents, scope closure becomes static and the runtime toggle disappears entirely.

## S5. §"The one rule" still settles queries by closing an answer-list tail — a mechanism §"Answers stream individually" deleted. READ.

`:424–426` and `:431–432` still speak of *"closing the open tail of the branch list"*, but `:163–177` rules the answer-list out. The two are **the same mechanism**: the dead-end analysis independently derives *one termination marker per producer per stream*, and `closed(Q,p)` is that marker with an author's name on it. The dead-end's conclusion should not be *"machinery for nothing"* but *"right structure, wrong representation"*.

> **Corrected statement.** `:424–426` is **stale**: there is no branch list, and query settledness is not groundness of any term — it is the presence of `closed(Q,p)` for every `p` plus scope closure, a *family of atoms*. Three consequences to restate: (i) there is no freeze because closure is an ordinary post — same conclusion, different reason; (ii) the ownership rule is *only `p` may post `closed(Q,p)`* — currently anchored to a deleted object, and load-bearing; (iii) `:170–172` should read that the per-producer tail is the right structure and `closed(Q,p)` its attributable form.

## S6. The valuation reading's "two different kinds of object" are one three-element flat domain. MEASURED.

`:302–305` asserts the levels are different kinds; `:312–314` then says both climb flat orders. Those are **isomorphic three-element flat domains**, and the aggregation is `max` over `{-1,0,1}` — verified exactly, 363 vectors, 0 disagreements. The distinction has no mathematical content: `⊥` of a flat domain at both levels. The question the table answered — *"is `unknown` ambiguous?"* — is better answered by *"it is `⊥` of a flat domain at both levels; that is not ambiguity."*

## S7. "A demand is a total valuation" is unnecessary, and it manufactures the hazard it then warns against. READ.

Only the demand's *true-set* is ever used; totality does no work, and the "total map to Bool" reading is in tension with the demand relation being monotone and accumulating — the store can affirm *"asked"* but never *"not asked"*. Drop the valuation framing and a demand is an open set `U`, an answer is `truth|_U`, and conjunction is not a type-correct operation on a set and a partial map — so the hazard cannot be written and the caution against it is unnecessary.

## S8. Continuous carriers: one axis, not two — and it is a theorem, not an analogy. READ.

In a **continuous** dcpo with basis `B`, the sets `⇈b = {x : b ≪ x}` are a basis of the Scott topology; in an **algebraic** domain `⇈c = ↑c` for compact `c`. For the interval domain, `[a,b] ≪ [c,d]` iff `a<c` and `d<b`, so `⇈[a,b] ∩ maximal` is exactly the open interval `(a,b)`.

> **Corrected statement.** `:716–717`'s *"Same discipline, two axes"* should read: **one discipline, one axis.** The basic opens of a continuous domain are `⇈c`; `↑c` coincides exactly in the algebraic case; an open interval **is** `⇈c` for the interval domain. Thirteen lines become one sentence, and the claim gets stronger — a textbook theorem rather than an argued analogy.

## S9. Four names for one object.

The **call table / registry**, the **work set**, the **cursor into a per-functor term index**, and the **standing cursors** are one structure: the set of live demands, keyed by skeleton, indexed by pattern. Naming it once, and stating the division — *the call table decides who is told; the store decides what is computed* — removes a mechanism from the reader's model.

## S10. Three smaller collapses.

- **The residual is control, not an exception.** It *"need not be materialised; walking the extent and skipping present atoms computes it incrementally"* — so there is no negation-bearing *message*, only a scheduling decision, and §"Demand is control" already places scheduling outside the logic. Reclassify it and **§"Two exceptions" becomes one exception — `ensure`, the core operation.**
- **Admission control and the finiteness obligation are one test with two owners.** The quantity is `|ground(p)|`; it is finite exactly when the finiteness obligation is dischargeable. One test: the requester supplies the count, layer 7 meters it.
- **Four substrate jobs are three.** The liveness probe and "an observation that nothing changed" are the same category, and §"Two layers" already treats them as one. Persistence, indexing, and **an oracle channel whose outputs are timestamped into facts about the past**.

---

# JOB 2 — THE ATTACK

## A1. The "forgery" answer indicts every honest producer, and extent-closure *retracts*. A monotone repair exists and is measured. MEASURED.

**Claim tested.** `:206–210` — closure over the producer's extent, transferring to subsumed questions — with the doc's answer to review 6's second horn at `:215–216`: *"a producer that claims the whole axis and emits half of it is a forgery, and forgery survives."*

**Measured** (`p6_closure.py`): producer `p` with extent `S ∈ 0..1000`, sparse series (even steps), demand-gated.

```
horn 1  closure keyed by the asker's question
  settled(S=<100) while p alive         : True
  settled(S=<200) after p exits         : False      <- permanently unsettleable  [review 6, reproduced]

horn 2  closure over p's extent, as the doc now has it
  p is honest: it truly sends no more   : True
  false(loss(51,_))  [asked, gap]       : True
  false(loss(150,_)) [NEVER ASKED]      : True       <- absence affirmed for uncomputed data
  ... then p is re-demanded on 200..250:
  |false| before : 249    |false| after : 223    RETRACTED : 26
```

**Two findings, one new.**

*First, "forgery" is the wrong diagnosis.* Under demand-gating, **every** producer emits less than its extent. That is not a defect; it is the design. So the sentence, read literally, makes every demand-gated producer that closes its extent a forger. And a producer that has exited and posts *"I will send no more"* has said something **true**. The defect is in what `closed` licenses, not in the producer's honesty.

*Second, and not in review 6: extent-closure is non-monotone.* Measured, 26 affirmed absences retracted when a still-live `p` is re-demanded. The dilemma has no comfortable horn:

- treat `closed(Q,p)` as revocable — it is then not monotone, and CALM applies to it;
- treat it as binding — then a producer asked only for steps 0–100 has **permanently foreclosed** steps 101–1000 for the whole run, which is the opposite of demand-gating.

This contradicts the concurrent prior-art review's stated *"defensible novelty"*. The staleness is not about `Q`'s content; it is about `p`'s future *emission*, which demand gates.

**The repair, measured.** Timestamp the closure and gate negation on the internalised demand relation:

```
closed(Q, p, T)                  "as of T I will send no more for Q"     -- durable, survives exit
demanded(a, T')                  the monotone "this was wanted" relation -- currently unread
false_from(p,a) :- closed(Q,p,T), a ∈ ground(Q), demanded(a,T'), T' ≤ T, ¬produced(p,a).
```

```
repair
  false(loss(51,_))  [asked before T]    : True     <- settles
  false(loss(150,_)) [never asked]       : False    <- correctly not affirmed
  settles after p exited (durable)       : True     <- beats horn 1
  false(loss(150,_)) after a LATE demand : False    <- a late demand cannot retro-close
  retractions under later demand         : 0        <- monotone
```

Every ingredient is already in the doc: `T' ≤ T` is a comparison of two *recorded* times, which is §"Two layers"'s own device; the demand relation is the internalised one; the constraint literal is ordinary CLP.

**Two things this unifies.** (i) The internalised demand relation currently has **no reader anywhere in the doc**; by the doc's own standard for deleting the dependency graph it should be deleted, and the repair is its one legitimate consumer. (ii) It makes **masking and the CWA scope the same restriction**: you may complete the world exactly where you asked.

## A2. The sheaf identification is right and inert, and "geometric logic forced" is backwards. MEASURED.

Measured (`p1_frames.py`, exhaustive over all finite frames on ≤ 4 join-irreducibles):

| map | triples checked | failures to preserve `→` |
|---|---|---|
| restriction `V ↦ V ∩ U` to an **open** subspace | 139,202 | **0** |
| a **point** frame hom `O(X) → 2` | 70,036 | **4,516** |

Mac Lane–Moerdijk **IV.7.2**: `k*` *"preserves the subobject classifier and exponentials, and hence is a logical morphism"*; **IV.8.1**: pullback induces a **homomorphism of Heyting algebras**.

**Two additions.** (1) **Openness is *necessary*.** MM Ch. X Lemma 3.2 plus its converse: preserving first-order formulas *forces* openness. The premise the doc uses to force geometric logic is the one that licenses more than geometric logic. (2) **The doc's own semantic guardrail is witnessed at a non-open inclusion** — `O(ℝ) → 2` at the point `0`, and `{0} ↪ ℝ` is not open. The two paragraphs are 610 lines apart and pull in opposite directions.

> **Corrected statement.** `:350–352` is **false as stated**. Restriction along an inclusion of open subspaces is **logical**. Geometric logic is what transports along **arbitrary** geometric morphisms, of which the architecture as described contains none. The bullet should be deleted; §"CALM" and the affirmability argument carry the restriction alone — and they carry it from finite observation, not from the space of ground atoms, whose frame is measured to be a complete **Boolean** algebra and therefore forbids nothing.

## A3. "Propagate, never label" does not delete the budget; it moves it into propagation. MEASURED.

**Measured (SWI 10.0.0 + clpfd, no labelling), `X in 1..N, Y in 1..N, X #> Y, Y #> X`:**

| N | result | inferences | ms |
|---|---|---|---|
| 10 | failed | 989 | 1 |
| 100 | failed | 5,084 | 1 |
| 1,000 | failed | 46,034 | 5 |
| 10,000 | failed | 455,534 | 104 |
| 100,000 | failed | 4,550,534 | 8,194 |
| 1,000,000 | — | **>30 s, killed** | — |

Cost is **linear in the numeric magnitude of the domain** — the standard bounds-propagation ping-pong. Nothing about *problem size* protects you: this is the two-variable instance.

**And the unbounded case decides nothing.** With `X, Y in inf..sup`, the identical constraints post successfully — no failure, no inconsistency detected. The design's step axis is explicitly unbounded.

> **Corrected statement.** *"no search, so no budget to exhaust"* is **wrong**: propagation to fixpoint is pseudo-polynomial. Banning labelling removes the *search* budget and leaves the *propagation* budget; the timeout hazard and its anti-CALM direction survive intact, gated on domain magnitude rather than propagator completeness. Open #2 tightens further: it is not enough that the propagator be complete for entailment — the *carrier* must be small enough that reaching the fixpoint is affordable, or large enough that propagation is trivially inconclusive.

## A4. `closed(Q,p)` is not a Clark completion. READ/INFERRED.

Two structural mismatches. **(1)** Clark completion is per-predicate over *all* clauses; completing one contributor's clauses while others may still fire is not a completion of anything. The completion is the **whole family**, not `closed(Q,p)` alone. **(2)** A Clark completion **cannot be false** — it is stipulated in the program, and that is what licenses NAF. `closed(Q,p)` is asserted at runtime by a party that may be lying and — per A1 — may cease to be true.

> **Corrected statement.** `closed(Q,p)` is a **local completeness statement** about one source's coverage of one region. The *conjunction* of all per-producer statements with scope closure is what plays the completion's role. Naming it correctly is worth more than the analogy, because the completeness-statement literature already answers *when does a set of local completeness statements make a query complete?* — by **query containment**, which is precisely the subsumption test the design already performs.

## A5. The aggregation is monotone only on a fixed producer set. MEASURED.

With the index set fixed, **780 comparable pairs, 0 descents**. With the producer set growing, **8 descents in 312 pairs**; witness `[refuse] → false`, then `[refuse, might] → unknown`. Since §"CWA is a posted fact" exists precisely because the producer set is open, the regime in which the claim fails is the design's actual regime.

The rest checks out: `max` over `{-1,0,1}` reproduces the aggregation exactly (363 vectors, 0 disagreements), and the unanimity requirement **does** fall out — it is the De Morgan dual of *"true iff some producer produces"*.

> **Corrected statement.** Add the qualifier: **monotone in the stances, at a fixed index set.** The fix is S4's — take the index to be all possible producers, unspawned holding *might*, so closing a scope is a bulk `might → refuse`, a climb.

## A6. The bug is totalisation, not conjunction. READ.

The strong-Kleene arithmetic is right, but truth is a *partial* map and the demand a *total* one, so pointwise `∧` is not type-correct until you totalise truth by `unknown ↦ false` — and that coercion **is** the bug, independent of which connective follows.

> **Corrected statement.** *"One operation apart"* should read **one coercion apart**. Represent knowledge as a pair of extents and restriction *is* conjunction, applied to both.

## A7. Grounding is signature-relative, which makes signature agreement load-bearing for *negation*. READ/INFERRED.

**The caveat is mis-stated.** If `f` is a **function** symbol the universe is infinite and the groundings differ, so the example refutes itself; it works only reading `f` as a **predicate** symbol over a signature with no function symbols.

**The substantive point.** `ground(p)` is computed against a signature. Since closure transfers by grounding-inclusion, and closure licenses negation, **two agents that disagree about the signature derive different negative facts** — not merely different subsets. Signature agreement is a soundness precondition for the closed-world machinery, not only a typing discipline.

## A8. "GC reclaims" overclaims by exactly the boundary case. INFERRED.

The negative answer is available only when the narrowed domain leaves the interval's **closure**. On the boundary the query neither fires nor dies. The sentence should stop after *"thresholds fire"*.

## A9. Half of tabling applies exactly, and the doc says so 120 lines later. READ.

A tabling table is a *call* key plus an *answer list*; the design keeps the call key wholesale and replaces the answer list with per-atom presence. **Exactly one half of it applies**: the call table is the delivery index, the store is the memo. The two paragraphs currently read as a contradiction.

---

## What I could not break

- **`max` over `{-1,0,1}`** reproduces the aggregation exactly — 363 vectors, 0 disagreements.
- **The unanimity claim.** It genuinely falls out as the De Morgan dual. Not stipulated.
- **`all_different` under propagate-never-label.** The singleton test is monotone, hence `ground/1`, which the doc permits. Régin-style matching reads the whole domain only internally, and its narrowings are entailed.
- **The transfer property.** `closed(Q,p)` really does transfer to every subsumed question, durably and after `p` exits. The defect is in what closure *licenses*, not in the transfer.
- **The residual's coverage of overlap.** Per-atom presence really does handle the `0..100` / `50..150` case.
- **The rejection of a syntactic read/write test.** Correct, and already better answered by *"a hole **is** an open question wherever it appears"*, which needs no syntactic classification.

---

## Environment and hygiene

SWI-Prolog 10.0.0 with `library(clpfd)`; Python 3 stdlib. XSB not needed. The `runstate` package was not imported and the corpus was not read — reviews 5 and 6 measured it; this review is about the design's mathematics and structure. One literature-verification subagent was used for topos/domain-theory citations.

**No Postgres was started.** **Nothing was edited**; nothing written outside the scratchpad; no git write in any repo. One caveat declared: `runstate`'s untracked count went 7 → 8 during the session — `docs/review-7-prior-art-verbatim.md`, written by a concurrent session; read only to check for duplicate findings, and credited where overlapping.

**Prototypes** in `…/scratchpad/r7/`: `p1_frames.py` (open-subspace vs point frame homs, exhaustive); `p2_valuation.py` (aggregation, monotonicity at fixed vs growing index); `p3_prop.pl`, `t3.pl` (clpfd propagation cost and the unbounded case); `p4_space.py` (the topology generated by `ground(p)` — discreteness); `p5_boolean.py` (the frame is a complete Boolean algebra); `p6_closure.py` (the three closure readings and the timestamped repair).
