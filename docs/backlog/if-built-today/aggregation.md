# Aggregation: orders and summaries

**Layer:** depends on `substrate.md`, `logic.md`, `polarity.md`, `questions.md`. The dependency graph is in `README.md`.

## Orders are mostly read-side

By **default** an order is not a storage type — it is how a **read** aggregates what it finds, which moves
the whole table off the safety path onto the cost path.

| aggregation | note |
|---|---|
| last-write-wins | `argmax` over `seq`; a report |
| `max` / `min` | a report |
| multiset union | counts rather than membership. An observation-count that only climbs, asked only **at least `n`**, is admissible as a **store** too — it is the `(ℕ, max)` join-semilattice, Bloom^L's `lmax` with `gt_eq`, and the at-least-only discipline is the threshold rule, forced not advisory (*exactly* flips on the next arrival; *at most* is an upper-bound test, the else-branch move). The subtlety is what the count **means**: bare totals have no coordination-free merge (`+` is not idempotent; `max` means *"some single lineage tallied `n`"* — sound for at-least, undercounting). **True totals are occurrence naming**: per-actor tallies (the G-counter; membership) or per-event ids — user-chosen key data, by content, a random id or a per-actor prefix — counted as a `π` reading. Multiplicity is identity, so the choice of occurrence key prices the count |
| set union | the identity read, and the only option for a **holistic** aggregate (median, percentile), where no bounded *exact* summary exists. Bounded *approximate* ones do: a 200-bucket sketch reproduced a 2000-sample bootstrap CI to **1.07% of its width** |
| "must all agree" | the `conflicted` predicate above |
| lexicographic | fine as a *selection* order, dangerous as a *combining* one: with `attempt` at the head, `(1,running)` and `(1,crashed)` have least upper bound `(2,⊥)` — it **fabricates attempt 2**, in a design about attribution |

**Where the narrowing (Smyth) construction goes.** The three powerdomains are the three ways to make "a
set of possibilities" a domain: Hoare/lower (*may*), Smyth/upper (*must*), Plotkin/convex. Putting the
upper one on the *value* side is a category error — read extensionally as a set of facts it is antitone,
so a rule body binding a variable to a member is non-monotone. Its natural home is **demand** (*must*
produce) — **and that is open**, suggestive and unworked.

**Continuous carriers are restricted, not broken.** In a continuous dcpo the basic opens are `⇈c`,
coinciding with `↑c` exactly in the algebraic case; an open interval **is** `⇈c` for the interval domain.
Nothing settles at a point under bisection, but *"is `S` in `(0.4, 0.6)`?"* becomes true the moment
narrowing puts the domain inside it. So: **settle the question, not the value.** Thresholds fire and
standing queries die. (GC is *not* on that list: on the boundary the query neither fires nor dies.)

**The constructive form of that rule is a value that streams.** Represent a real as an exponent plus a
growing list of expansion coefficients and it is already a monotone stream of information — the same shape
as the store — so every interval question becomes an ordinary threshold read, firing the moment enough
coefficients place the domain inside the interval. Not free: a streamed real is a **region of coefficient
atoms** rather than a `Float`, so the value plane acquires unbounded extents and `logic.md` §"Types" would owe it a
sort. Unworked.

**Terminology hazards, all live.** *Join* — relational `⋈` versus lattice `⊔`, and the lattice join versus
the powerdomain pair. *Union* — status aggregation in the knowledge order versus value aggregation under a
declared `Set` order; in the paraconsistent literature these are **different operators**, one intersecting
the negative halves. *Membership* — network membership versus membership in a solution set. *Finite* — the
cover, the requester's obligation, `∧` in the fragment, and `E` in the residual. *Closed* — closed-world,
topologically closed, upward/Scott-closed, a closed sort, a closed vocabulary. *Linear* — a term with no
repeated variables, an order that is a chain, and linear in domain magnitude. *Horn* — see §"The
language". Name them differently in any implementation.

## Summaries, and the property a construction can forfeit

Aggregation is the third branch, and the one where building upward **costs** a guarantee the base had. An
answer is truth restricted to a demand; different agents hold different overlapping regions and compute
over what they have, skipping what is still `∅`. That is Morton's **available-case analysis** (Obs. 6.10)
exactly, and his **Thm 6.9** says the resulting summaries can be pairwise consistent on every overlap and
admit **no global joint** — contextuality, manufactured out of a store that was never inconsistent. Every
agent's view is coherent and every pairwise check passes, so no participant can detect it.

**The store is not wrong; the guarantee does not lift.** Atoms converge without an arbiter; statistics over
different demand regions need not. The foreclosure of `README.md` §"Two commitments" holds exactly as far as identity
stays attached, and the summary map is where it stops.

**Settledness does not close it, and the strong kind does not either.** Prop. 5.2 says *without* missing
data, restriction and summarization commute — but of **one table**. Two agents hold two stores, and both
can be complete while disagreeing. Under the weak kind of settledness, two stores each holding one value
per key, different ones at key 61, are both settled for `∃V. p(K, V)`, their means differ, and nothing
reads `{t,f}`. Under the strong kind the same thing happens: each producer vouched *"0.31 and nothing
else"* or *"0.40 and nothing else"* at key 61, each store is strongly settled, and neither holds both
values. What the strong kind buys is that the disagreement is **visible at the join** — the union reads
`{t,f}` at key 61, a valuation conflict rather than a silent domain one (`questions.md` §"Settledness of a
question"). Gluing needs, in addition,
that the union is conflict-free over the region, which is `= {t}` and never affirmable. So a summary is a
**report** — it sits outside, like `argmax` — and a summary computed over a strongly settled region is
the one whose disagreement with another agent's will surface when their views meet. Whether that is a
usable discipline depends on strongly settled regions being large enough, often enough, and on the
key-granularity choice of `polarity.md` §"The rule for posting".

