# Why definite clauses, and not the neighbouring fragments

Forward-looking (surfaced 2026-08-18, from the homomorphism-preservation thread).
Background: [if-built-today](if-built-today.md) §"The language, and what each restriction buys", which
places its fragment *"two rungs below geometric"* and justifies each omission separately. This file asks
whether the fragment is **forced** rather than merely chosen, and finds a partial answer.

## Status: OPEN, with a partial result

Not refuted, not funded. Two papers are on disk and unread. The result below is reasoning, and the
literature has not been consulted — which in this repo is the failure mode that has already cost three
sections (`../dead_ends/topological-framings.md`).

## Why it would pay

`if-built-today` §"What makes the answer worth having" concedes, deliberately:

> Finite `∧` with arbitrary `∨` and affirmable-in-finite-time are one fact in two vocabularies — the second
> is the standard justification for the first, so they are not independent confirmations — and
> monotone-iff-coordination-free is a genuinely separate route to the same constraint set. **Two routes
> agreeing is weaker than three would be** and still worth having.

This is the candidate third route, and it arrives from a third field:

| route | field | statement |
|---|---|---|
| observability | domain theory (Smyth, Abramsky, Vickers) | affirmable in finite time ⟹ finite `∧`, arbitrary `∨`, no `¬` |
| distribution | CALM (Ameloot, Neven & Van den Bussche) | coordination-free ⟺ monotone |
| **algebra** | model theory / initial semantics | preserved under homomorphisms; the least Herbrand model is **initial** |

## The formulation to work from

Take a definite program, ground it (possibly infinitely), and apply an algebraic homomorphism to the
**terms** inside each rule. The result is a program in which **at least as many things are true** — because
a homomorphism may *identify* terms, and identification only ever makes more rule instances applicable.

Three faces of one property, and which is "defining" depends on what is being held:

- **monotonicity** is a property of the operator `T_P` — more in, more out;
- **preservation under homomorphisms** is a property of the *formulas*;
- **initiality** is a property of the *model* — a unique homomorphism from the least Herbrand model into
  every other model.

The bridge to this repo's existing argument is one sentence: **a homomorphism can add facts and identify
elements, so `¬P(x)` survives neither. "No negation" and "preserved under homomorphisms" are the same
constraint seen from two sides.**

## Definite rather than Horn, for a better reason than the doc currently gives

A goal clause / denial `:- G` is `G → ⊥` — *"this must not hold."* A homomorphism that identifies terms
can **make `G` hold**, so denials are not preserved. Horn = definite ∪ denials, and the denials are exactly
the half that breaks.

`if-built-today` currently reaches "definite, not Horn" from the absence of `⊥` in heads. This is the same
conclusion from a stronger premise, and should replace it if the literature holds up.

## The result, and it is not the clean maximality one expects

**The maximal homomorphism-preserved fragment is BIGGER than definite clauses** — it is positive-existential,
which permits both `∨` and `∃` in heads. So the algebra alone does not pick this fragment. What picks it is
two forces meeting:

> **Homomorphism-preservation draws the outer boundary** (no negation, no denials).
> **This design's own commitments cut inward** to definite clauses.

Neither alone selects the fragment; together they do. That is a better result than a single maximality
theorem would have been, because the inward cuts turn out to be commitments made for unrelated reasons.

| extension | preserved? | what actually blocks it here |
|---|---|---|
| `¬` | **no** | and it breaks CALM as well — the two routes agree |
| denials `:- G` | **no** | the argument above |
| `∨` in heads | **yes** | the store's type widens for everyone — see below |
| `∃` in heads | **yes** | a claim the language cannot discharge — see below |

### Why not `∨` in heads

Not complexity, first. **The store's type widens for everyone.** `A ∨ B` has no ground-literal form, so
either the store admits clause-shaped entries, or the engine branches into a *set* of stores and
merge-is-union stops being merge.

Runtime cost really is pay-per-use — a program with no disjunctive rule pays nothing. But this is a
**protocol**, and another language's implementation must handle every case the type admits, including ones
no local rule produces. *"The typed terms another language reimplements to interop"* is the stated bar, and
a wider type with no local inhabitants is still wider at the wire.

Second reason: a disjunction is *"I don't know which"*, which is precisely what §"An atom's status" already
refuses when it makes `∅` unaffirmable. The same decision, arrived at twice. Third: Σ₂ᵖ vs P data
complexity for disjunctive Datalog.

### Why not `∃` in heads

**A correction is recorded here deliberately, because the first-pass reason was wrong twice over.**

The original objection was that a head existential invents an anonymous witness, colliding with the second
commitment, *identity is data, never position*. That is **misattributed and overstated**. A labelled null is
a generated *constant* — a name, not a position — and commitment #2 rules out ordinal identity (*"the
heartbeat after the second `started`"*), not variables or generated constants as data. This design in fact
**commits** to variables-as-data: it is a distributed unification engine in which a binding is an ordinary
posted fact and an unbound variable is a durable object with its own reclamation question.

What survives, in order of force:

1. **A head existential can assert a witness nothing could satisfy.** In a purely positive language
   `∃y. R(x,y)` is always satisfiable — invent an element — so the existential is free and harmless. It
   becomes a claim the language *cannot discharge* only when something can refute it, and this design has
   three such things: a constraint domain, disequality over closed sorts, and told falsity. So the `∃` is
   syntactic, with no guarantee of true existence. **This is the live objection.**
2. **Convergence — and it dissolves under Skolemisation.** Fresh nulls invented independently by two agents
   never unify, so `R(a,n₁)` and `R(a,n₂)` are two facts under merge-is-union with nothing making them
   equal — which would break the premise that two agents asking for the same thing ask under the same name
   without arranging to. But Skolemising instead (`R(x, f(x)) :- B(x)`) makes the witness
   **content-addressed by construction**, so both agents produce the same term. The standard Skolem chase
   already does this, and the objection does not survive it.
3. **Termination.** Skolem terms nest (`f(f(f(x)))`) and the chase need not terminate — which is what the
   guarded, sticky and weakly-acyclic fragments exist to bound.

So `∃` in heads is **defensible here if Skolemised**, and (1) is the reason to decline it rather than (2).

## The names, which `if-built-today` only half has

| fragment | name |
|---|---|
| definite + `∃` in heads | **tuple-generating dependencies** (TGDs), a.k.a. **existential rules**, **Datalog^∃**; **Datalog±** is the family that also adds negative constraints and EGDs. Decidable fragments: guarded, sticky, weakly-acyclic. |
| `∃` in *bodies* | unnamed — implicit in every Datalog, since body variables not in the head are already existential |
| definite + `∨` in heads | **disjunctive Datalog**; **disjunctive logic programming** (DLP) |
| **both** | **coherent logic** — a coherent implication is exactly `∀x̄. (A₁ ∧ … ∧ Aₙ → ∃ȳ.(B₁ ∨ … ∨ Bₘ))`. Also **disjunctive existential rules** / **disjunctive TGDs** in the Datalog± literature. |
| coherent + *infinitary* `∨` | **geometric logic** |

**Which closes a loop the doc left open.** §"The language" says the fragment sits *"two rungs below
geometric"* — and the rungs are exactly these: **coherent**, then **geometric**. The ladder is already
right. What is missing is the name **TGD / existential rules** for the intermediate step, which the doc
never mentions and which is by far the most-studied of the three.

## What to read — none of it read, all of the below is recall

Both papers are on disk (`../resources/`, gitignored):

- `makowsky-1987-why-horn-formulas-matter.pdf` — Makowsky, *Why Horn formulas matter in computer science:
  initial structures and generic examples*, JCSS 34(2/3), 1987. **Read first.** Believed to be the closest
  existing thing to the maximality result wanted, characterising the fragment with initial semantics.
  (Year confirmed from the PII; it is **not** 1985, as first recalled.)
- `horn-1951-sentences-true-of-direct-unions.pdf` — Alfred Horn, *On sentences which are true of direct
  unions of algebras*, JSL 16(1), March 1951. The origin: which sentences are invariant under direct union.

Still to obtain:

- **Homomorphism preservation theorem** — a first-order sentence is preserved under homomorphisms iff
  equivalent to an existential-positive one. **Rossman 2008** reportedly proves it survives restriction to
  **finite** structures, unlike Łoś–Tarski. The finite case is the one that matters here.
- **Horn ⟺ preserved under (reduced) products** — Chang & Keisler. A *different* fragment again, since it
  includes the denials.
- **Initial algebra semantics** — Goguen, Thatcher, Wagner & Wright.

## The categorical placement — checked 2026-08-20, and deliberately kept out of the doc

Recorded so it is not re-derived, and **not** carried into `if-built-today.md`, where it would do no work.
The sibling `../dead_ends/topological-framings.md` exists because borrowed machinery that does no work has
already cost that document three sections; this is placement, which is checkable, and nothing more.

**The ladder.** `⊤, ∧, ∃!` = **cartesian** ⊂ `⊤, ∧, ∃` = **regular** ⊂ `+⊥, +∨` = **coherent** ⊂
`+ infinitary ∨` = **geometric**, these being the internal logics of cartesian, regular, coherent and
geometric categories. **Definite clauses are regular with atomic heads** — a body-only variable is an
unrestricted antecedent `∃`, so the fragment is *not* cartesian — and **regular sequents `φ ⊢ ∃ȳ ψ` are
exactly tuple-generating dependencies**. So "definite clauses plus `∃` in heads" is not a hand-cut
fragment: it is regular logic.

**Why disjunction in an antecedent is free.** `φ ∨ φ' ⊢ ψ` holds iff `φ ⊢ ψ` and `φ' ⊢ ψ`, and geometric
logic's distributive laws float `∨` out of `∧` and `∃` to the top, so every antecedent reduces to a family
of `∨`-free ones. Infinitary antecedent `∨` splits into an infinite *family of sequents* — fine for a
theory, which is why the obstacle to it is finite presentability of a **program**, not semantics.

**Classifying toposes — read, and it stops one rung short of us** (`../resources/beke-theories-of-presheaf-type.pdf`,
Beke, *Theories of presheaf type*, 2004; §§0–2 read):

- **Definition:** a geometric theory is *of presheaf type* if its classifying topos is equivalent to a
  presheaf topos.
- **Cartesian is settled, and it is a biconditional** — Remark 1.2 cites the **Gabriel–Ulmer theorem**,
  *"that the classifying toposes of finite limit theories are precisely the presheaf toposes `Pre(𝒞)` where
  `𝒞` has finite limits."*
- **Coherent is not uniformly presheaf type**, with a sharp counterexample: *"there exists **no coherent
  presheaf type axiomatization of fields**."*
- **Recognition is delicate.** Remark 1.5: *"whether or not `T⁺` is of presheaf type is **not determined by
  the abstract category `Mod(T⁺)` alone**; so any categorical recognition method must employ some auxiliary
  device."* Prop. 0.1 gives the finitely-accessible characterisation, and the *"some"* in its clause (iv)
  is where the gap hides.

So the clean theorem sits one rung **below** the fragment and the delicacy starts one rung **above** it,
which leaves Open #5.

## Open

1. **Does a genuine Lindström theorem for definite/Horn logic exist?** Abstract model theory for logics
   with initial semantics. Unknown; do not assume it does. The "each extension breaks a different property"
   pattern above is Lindström-*shaped* but is not itself such a theorem.
2. **Does `⊥` in heads break preservation, or only the store?** Unchecked.
3. **Abstract interpretation.** Homomorphism-preservation is the soundness condition for abstract
   interpretation over a positive program (Cousot & Cousot): computing in the coarser image
   over-approximates, so a **negative** result there is sound for the original. Whether that buys anything
   here — cheap refutation of a demand, *"nothing in this region is derivable"*, without running the job —
   is unexplored, and looks worth an hour given how much of the design is about deciding what not to
   compute.
4. **Whether route three is actually independent.** CALM's monotonicity and homomorphism-preservation may
   be closer than they look. If they are two statements of one fact, the doc gains a better *phrasing* but
   not a third route, and it should say so rather than count to three.
5. **Is every *regular* theory of presheaf type?** Gabriel–Ulmer settles cartesian affirmatively; Beke
   exhibits a coherent theory (fields) that is not. Regular sits between, and the question was not answered
   in the sections read. The literature speaks of *characterisations of* the regular theories classified by
   a presheaf topos, which suggests a proper subclass — but that is an inference from a phrasing, not a
   result. **Worth an actual attempt**: the gap may be open for want of anyone asking rather than for
   difficulty, and the two ends are close together.

## Related

- [if-built-today](if-built-today.md) §"The language, and what each restriction buys" — the fragment and
  the per-omission justifications this file tries to replace with one argument.
- [if-built-today](if-built-today.md) §"What makes the answer worth having" — the two-routes concession
  that motivates this.
- `../dead_ends/topological-framings.md` — the standing reminder that reasoning ahead of the literature has
  already failed three times on the sibling document.
