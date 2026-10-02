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

**Below this layer everything glues, distributively** (`polarity.md` §"An atom's status"): the global store
is the union of the local ones, and each atom's global status is the join of its local statuses. What a
summary keeps of that depends on the summary, and there are exactly three tiers.

**Which summaries keep it.** Stores are finite sets under union — the free join-semilattice — so the
summaries that keep *distributive* gluing are exactly the maps `S(A) = ⨆_{r ∈ A} f(r)`, for some
per-record `f` into a join-semilattice: the join-homomorphisms. Commutative and associative is not enough;
**idempotence** is the condition, because agents' stores overlap and union counts the overlap once. `sum`
and `count` are commutative monoids and fail exactly there: `sum(A ∪ B) ≠ sum(A) + sum(B)` when `A` and `B`
share records.

| tier | class | gluing | examples |
|---|---|---|---|
| **1** | join-homomorphisms, `⨆ f(r)` | **distributive** — the global summary is the join of the local ones, mergeable like a CRDT | per key, the set of told values; `max`; `min`; *"is there a value below 0.1?"*; the statuses themselves |
| **2** | monotone, not join-preserving | **by common refinement** — the summary of the union refines each local one, so none can contradict another, but the global one is recomputed from the *records* | the set of possible means (below); the count of distinct records; settledness |
| **3** | not monotone | **none** — a report, and it sits outside like `argmax` | *the* mean; last-write-wins; median; `sum` over overlapping stores |

Tier 1 is Bloom^L's *morphisms* as against its monotone functions, and tier 2 holds by `logic.md`
§"CALM"'s joint consistency applied to summaries. **Every summary factors** as a tier-1 map into a
lattice — the set of records, or each key's set of values — followed by one final read, so its tier is
that read's tier; Bloom^L's non-monotone `reveal` is the tier-3 read. And a non-idempotent tally climbs
into tier 2 by counting occurrences named as data rather than arrivals (§"Orders are mostly read-side",
the multiset row).

**The tier-3 case, concretely.** Two stores each hold one value per key, 0.31 and 0.40 at key 61. Both are
settled for `∃V. p(K, V)`; their means differ, and nothing reads `{t,f}`, because neither holds both
values. Strong settledness does not change that — each producer vouched *"0.31 and nothing else"* or
*"0.40 and nothing else"*, and the disagreement becomes **visible at the join**, as a valuation conflict
rather than a silent domain one (`questions.md` §"Settledness of a question"), but not before. A picking
summary would need the union conflict-free over the region, which is `= {t}` and never affirmable. Prop.
5.2's commutation is about **one table**; two agents hold two.

### The may-lift: any summary, moved into tier 2

The tier-3 mean has a tier-2 counterpart, and the construction is general. For a summary `g` defined on
data with one value per key, its **may-lift** is

```
may(g)(store) = { g(c) : c chooses one told-true value for each key in range }
```

— every value of `g` that some told choice supports. Its properties hold for every `g`:

- **It is tier 2.** A new positive adds choices; a negative removes none, since it cannot withdraw a
  told-true value. So `may(g)` only grows, and two agents' may-summaries are always jointly consistent.
- **It is empty until the region is weakly settled.** A choice needs a value at *every* key in range, so
  while one key has none there is no choice and `may(g) = ∅`. Restricting to the keys that do have values
  would not fix this, since a new key would then change every mean, which is tier 3. Weak settledness —
  every key has a value or is told to have none — is the lift's precondition; keys told to have none drop
  out of range.
- **It collapses to the scalar.** When each key has exactly one told value, `may(g)` is the singleton
  `{g(…)}`, so the ordinary summary is its special case and nothing is lost by computing the lift.
- **It is exhaustive for one store when that store is strongly settled** — every key's set of values
  complete. Across stores it is not: conflict-freedom is never affirmable, so *"these are all the possible
  means"* stays a report.
- **Its reads split along the threshold rule.** *"Some supported mean is below 0.5"* is affirmable and
  stays true; *"every supported mean is below 0.5"* is antitone in the positives, and is a report.
- **It has a cheap coarsening.** The set of choices is exponential in the number of keys, but for a `g`
  monotone in each value — the mean is — the interval `[g(min choice), g(max choice)]` is computed per key in
  linear time, and is itself tier 2.

It is the Hoare, or *may*, reading of §"Orders are mostly read-side" applied to summaries, and it is what
a reader that shows summaries across agents would compute. Whether may-summaries are useful in practice,
and whether strongly settled regions are large enough often enough to make them exhaustive, is
`open.md` 9.

