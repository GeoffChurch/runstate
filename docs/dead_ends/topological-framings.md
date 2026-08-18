# Dead end: naming the store with a topological structure

**Status:** REFUTED 2026-08-14, on the third attempt, by two adversarial reviews plus measurement.
**Reconfirmed 2026-08-16 from primary source** — see the sheaves note below.
Three different structures were tried on `../backlog/if-built-today.md`; all three failed, and after
the third the *cause* is known and is common to all of them. Recorded so there is not a fourth.
The **insights survive** and are in the doc; the **borrowed names** do not.

## What was proposed, three times

| # | structure | what it was meant to supply |
|---|---|---|
| 1 | **Sheaves** over the space of ground atoms | `conflicted(K)` as failure of the gluing condition; geometric logic "forced by the architecture" |
| 2 | **Formal topology** `(S, ◁, Pos)` / overt locales | `Pos(Q)` = the `∃` demand, `settled(Q)` = the `∀` demand; one subsumption rule instead of a case split; `¬Q` as a single-record cover |
| 3 | **d-frames** `(L₊ × L₋; con, tot)` | `tot` = settledness, `con` = non-conflict, with paraconsistency explicit in the structure |

## The common cause, in two sentences

**We have two *extents* in one frame, not two *topologies*.** The "bi-" in bitopological space,
biframe and d-frame is about two topologies; our two-ness is two extents over one space, produced by
the same act, on the same class of regions.

**And the content that distinguishes the two extents lives in the *basis*, which frames forget.**
Positives decide one atom each; negatives decide a region each — so a finite cover of an infinite
region is necessarily a positive prefix plus a negative tail. That is a statement about how the two
halves are *presented* (singletons vs. residual-shaped regions) while both generate the same frame
`P(A)`. Frames — hence biframes, hence d-frames — are defined by forgetting exactly that. Choosing
among frame-level structures is choosing among names for `P(A) × P(A)`.

## The specific refutations, so they are not re-derived

**The space is degenerate.** Points are ground atoms (forced — see the doc's §"Facts and demands are
dual"; a residual is not upward-closed, so partial terms are unavailable). Ground atoms are maximal,
so the space is **discrete** and the frame is the complete **Boolean** powerset. Measured three times
independently. Every open is affirmable; nothing is forbidden; a structure defined by which sets are
open cannot do work here.

**Sheaves — confirmed from primary source 2026-08-16.** Review 7 recommended Morton (*Contextuality
from missing and versioned data*, arXiv:1708.03264) as *"the doc's primary citation for §'the sheaf
map',"* calling his setting *"ours verbatim."* Reading him settles it the other way. His two levels
that **keep** row identity — tables (Def. 6.3) and table-spaces (Def. 6.5) — are *always* separated and
*always* completable to a sheaf (Prop. 6.4; p. 18), because a compatible family of indexed rows has a
canonical glue: take the shared `(v, i)`, concatenate the states. The obstruction exists only at the
level of **relations** (Def. 6.8), where restriction *"necessarily involves summing over indices"* and
identity is gone. Our records carry identity in their arguments, so this design sits permanently at the
level where the phenomenon is **unavailable** — Abramsky's *"quite trivial … functions on a discrete
space"* aside, now with a construction behind it. **Do not cite Morton in support of a sheaf framing
here; he is evidence against one.** What he is good for is the regime
(`../backlog/if-built-today.md` §"What it is for") and the aggregation hazard (§"What gets built on
top"), both now in the doc.

**Formal topology.** `Pos` is *unique given* `◁` (Ciraulo & Sambin 2018, Prop. 4.6; Aczel via
Coquand), so it is not ours to redefine as a state predicate — and under our `◁` the forced `Pos` is
satisfiability, true on 127/130 regions against an **empty store**. Separately, with finite covers
`Pos` exists iff `a ◁ ∅` is decidable (Coquand), which is the entailment question this design
deliberately answers *undecided*. And overtness gives **positivity, not points** (Spitters; Manuell):
a positive open need not contain a point, so `Pos` is too weak for an `∃` demand even setting the
layering aside. Curi (MLQ 2010, Prop. 4.3): no non-trivial **Boolean** formal space can be proved
overt — so on a space of our shape overtness is classically free and constructively unavailable.

**d-frames.** Jung & Moshier Cor. 5.13: a *spatial* d-frame is derived from a biframe, and ours is
spatial by construction — so all the generality d-frames have over biframes is provably absent.
Worse, the formulas we actually use (`Q ∧ t_D ≠ ⊥`, `Q ≤ t_D ∨ f_D`, `t_D ∧ f_D ≠ ⊥`) take meets and
joins **across** `L₊` and `L₋`, and no such operation exists in a d-frame — that is precisely why
`con`/`tot` must be primitive. Those are **biframe** formulas. The stated motive fails too:
`(1,1) ∉ con` is a theorem in every non-degenerate d-frame, delivered equally by anything with a
product and a disjointness predicate. And JJP say the intent in print: *"The theory of d-frames works
best when the two topologies complement each other … If this is not the case, then the two relations
`con` and `tot` tend to be trivial"* — their degenerate example being **the same topology twice**,
which is our case. Their `±` is an *observational* polarity; ours is a *provenance* polarity.

**One deeper mismatch, which is ours rather than any framework's.** The store is not a pair of
extents. Two stores with identical `(t_D, f_D)` can answer differently: post nothing, versus post
`loss(3,V)` with `V` free. The second decides no atom — so both are `(∅, ∅)` — yet only the second
satisfies *"is there a loss at step 3?"* The store is **two extents plus a set of existential
claims**, and no algebraic framing tried has a slot for the third component.

## What survives, and is in the doc

- **`ground(Q)` need not be finite; the cover must be.** The genuine covering insight, and the design's
  best idea in this area.
- **`conflicted` is monotone; consistency is not.** Jung & Moshier reach this asymmetry from Stone
  duality (`con` is a downset, `tot` an upset); the doc reaches it from CALM. Measured over all 64
  states on a 3-atom universe: no read is ever lost. A real convergence, and the strongest evidence
  the exercise was worth running.
- **Refusing to collapse is the actual differentiator**, and it is now citable against named
  alternatives: Scott's information systems give an inconsistent token set **no state**; CCP
  (Saraswat, Rinard & Panangaden, POPL '91) *removes* the consistency structure and makes `false` a
  reachable **top**; d-frames **record** inconsistency in `con`. This design is a fourth position —
  both polarities kept, no top.
- **Uncited prior art found on the way**: Jakl's thesis Ch. 6, *"Belnap–Dunn logic of bispaces"* —
  geometric logic in the information order with `con`/`tot` as **judgement forms rather than
  formulas**, so consistency is not something a rule body can claim. That is this design's discipline,
  published, with soundness and completeness.

## What would revive it

A demonstration that the space is **not** discrete where it matters. The one live candidate is
**continuous value carriers** (§"Orders are mostly read-side"), which are genuinely non-discrete —
`↑c` stops being a basis and `⇈c` takes over. Two cautions: IEEE floats are a finite set, so density
is a modelling choice; and the *step* axis, where all the interesting structure lives, is discrete
under any reading.

Not a revival: pairing the constraint store's information-growth with its solution-set-narrowing as
the "two topologies". Both of **our** polarities *grow* — a negative post adds a record, it does not
remove a possibility — so `L₋` is a copy of `L₊`, not a dual. Checked 2026-08-14.

## Related

- `../backlog/if-built-today.md` §"Facts and demands are dual" — the point set, and why it is forced.
- `../backlog/if-built-today.md` §"The threshold rule" — covering vs. deciding, the surviving content.
- Reviews 7 and 8 (untracked) said it first: *"state the topology first, or the claim is vacuous."*
