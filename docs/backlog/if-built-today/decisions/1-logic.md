# Decisions and retractions — `../1-logic.md`

What was tried in this layer, what was withdrawn, and why. Conventions in `README.md`.

## The banner

**Withdrawn: "geometric logic, which is what this language is".** It is **definite clauses** — one atomic
head, always. No `∨`, `⊥`, equality or `∃` in a head. That is two rungs below the banner: `∨`-in-heads
separates geometric from coherent, and `⊥`-in-heads separates Horn from definite.

**And lower is the point.** Being low buys the tractable corner, the placement strictly inside the
coordination-free class, and the no-value-invention property CALM's proof depends on. The banner pointed
up while the design's virtue points down.

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

