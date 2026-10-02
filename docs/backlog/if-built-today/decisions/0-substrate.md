# Decisions and retractions — [`../0-substrate.md`](../0-substrate.md)

What was tried in this layer, what was withdrawn, and why. Conventions in [`README.md`](README.md).

## What merging would have cost, since the sketch cites it

Merging is declined because it **fabricates** — that reason is decisive and is in the sketch. What it
would have *cost* was measured, and is here because a cost that cannot change the decision does not
belong in the design, but a measured null result that stops re-proposal does belong somewhere.

For **linear** terms — no repeated variables — a term is a variable-disjoint partial function from paths
to symbols, so two terms merge iff they agree where both are defined, and consistency is **pairwise**. A
mergeable group is therefore exactly a **clique** in the compatibility graph, and greedy merging (put each
arriving term in the first blob it fits, else start a new one) is **first-fit colouring of the
complement**.

That identification needs a second lemma which is easy to miss: first-fit tests a candidate against every
*member* of a colour class, where blob-merging tests it against the blob's **merged term**. Those coincide
for linear terms — measured, **26,612 pairs, 0 disagreements** — and for non-linear terms they do **not**
(**321 disagreements in 6,441**), which is the second reason the model is linear-only, and the source of
the qualifier the sketch's clash test carries.

| | value | |
|---|---|---|
| **best** | `χ(Ḡ)`, the clique cover number | NP-hard to find, and not by analogy: **every** graph is realisable as the conflict graph of linear terms, so the optimum *is* graph colouring |
| **worst** | `Γ(Ḡ)`, the **Grundy number** | definitionally the most parts first-fit can be made to produce |
| **average** | → 2× optimum **asymptotically** | an Erdős–Rényi asymptotic that converges glacially. Measured, **1.04–1.20** up to n=24 against exact `χ`, and **1.21–1.32** up to n=500 against a heuristic |

**The gap is not a constant factor, and it is reachable at ordinary arity.** On the crown graph `χ = 2`
while first-fit uses `n`. The naive term realisation would need arity `n`; taking a Sperner antichain
instead needs only `k ≈ ⌈log₂ n⌉` positions — measured, a **12-position functor gives `χ = 2` against 924
first-fit blobs**.

The canonical alternative is order-free and unbounded: replace each **maximal consistent subset** by its
mgu, a function of the whole set, whose size is the number of maximal cliques — up to `3^(n/3) ≈ 1.44ⁿ`
by Moon–Moser. So canonicity is available and may be exponentially larger than the input.

**Also withdrawn: the "one of three defects dies" measurement.** It was taken against a table-and-FK
schema this design no longer contains, so it supports a claim about a different artifact. Do not re-quote
it.

## Smaller reversals, recorded so they are not re-tried

- **Reclamation is not retraction.** Evicting a *re-derivable* atom changes what is stored, not what is
  true. What the reclamation layer owes is a **caching invariant**, and the tiers invert what storage cost
  suggests: derived atoms evict freely, produced base facts cost a re-run *if the producer lives*, and
  **absence claims may be unrecoverable** — evicting one descends `{f} → ∅`, the single forbidden descent.

## The shared variable, and why reifying it is a deletion

**What it was.** §"The model" committed to a **distributed unification engine**: a variable is one object
whose identity survives crossing a host, and *"when a producer binds `V`, the querier's own term refines —
the querier is holding that variable, not a copy of it."* It was named as the largest thing in the
document, and §"The honest cost" led with it.

**Why it was attractive**, which is the point of recording it. Oz/Mozart does it. It makes *"one
representation, four roles"* feel inevitable rather than argued: post `loss(60, V)` and the same term is
the question before binding and the answer after. And it looked like the only way to avoid a **delivery
mechanism** — without sharing, a demand and an answer seem to be different objects that merely resemble
each other, and something must carry one into the other.

**What killed it is two of its own sentences.** The same section said *"a binding is an ordinary posted
fact"* and *"two agents binding disagreement is readable."* Those cannot both hold alongside in-place
refinement, because **a term cannot refine to `0.31` and to `0.45`**. So either the second binding has
nowhere to go — and disagreement is *not* readable — or the refinement never literally happened to anyone's
term, and what happened is that two equalities are in the store. The second is the true one, and it is the
reified model. The doc had already written the right sentence and built a shared mutable object around it.

**And the delivery argument assumed the wrong alternative.** It weighed sharing against *copies*. Under a
reified equality there is no delivery either: the equality is a fact in the store, read like any other. So
the argument established less than it claimed.

**The reification is not a cost.** The closure work does not appear, it **moves** — shared variables do it
eagerly at bind time through whatever the hole was unified with; posted equalities do it lazily at read
time or into an index. Same computation, rescheduled. The one genuinely new cost is that an equality can be
**disputed**, which is the thing being bought.

**What survived.** Cross-host **naming** is still protocol: a variable needs an identity meaning the same
on two machines, and it travels as a constructor (`var(37)`), not as a spelling convention. What did not
survive is the *mutable object* — the variable is a durable name with the ordinary reclamation question,
and it **never fails to unify**: a second binding does not clash, it records disagreement, which is the
paraconsistent variant of a logic variable and the variant this design's setting calls for.

> **Superseded 2026-09-30** ([`3-questions.md`](3-questions.md) §"Record scope"). The durable name did not survive either: folding two
> bindings of one name is a congruence, and it fabricates.

**Revival trigger.** A case where **determinism** is required — where disagreement must be *impossible*
rather than *readable*. That is what the shared object uniquely buys, via an owner protocol ("the owner
accepts the first binding request and ignores all subsequent"), and it has a known price: an owner is an
arbiter, an arbiter needs membership, and membership is the single non-monotone input (§CALM). Reviving the
shared variable means paying for coordination, not avoiding it. (*"Is this hole still unbound?"* is **not**
a trigger: it is antitone — true now, false on the next arrival — so the design forbids asking it
everywhere, not only here.)

