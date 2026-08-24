# The substrate, parametrically

Forward-looking (surfaced 2026-08-24, from the step-back question *"how much of this layer could be
separated from an even lower layer that gives the actual physics of determinism and communication?"*).
Background: [if-built-today](if-built-today.md), whose layering this page re-derives from a second,
independent premise.

## Status: a lens with one theorem-shaped observation and one audit, deliberately not a framework

The checkable content is the boundary test and the audit table below. A monad-indexed *theory* of
substrates would be the fourth topological framing (`../dead_ends/topological-framings.md`); the
discipline, as ever, is to use the structure to **locate**, never to **prove**.

## The three levels

- **Level 0 — physics.** What the transport gives: delivery order, duplication, loss, partition.
  `if-built-today` needs almost nothing here (unordered duplicate-tolerant broadcast plus one CAS), but
  that is a *choice of instance*, not a floor.
- **Level 1 — the possibility structure.** How many worlds a state describes and what an arrival does to
  them. This is the monad.
- **Level 2 — the logic.** Definite clauses over whatever level 1 hands up — unchanged across instances,
  which is the parametricity claim.

A store state is `M(World)`, and the design commitments are properties of `M`:

| `M` | regime | an arrival… | "prune" means |
|---|---|---|---|
| `Id` | one deterministic machine | replaces state | nothing — one branch |
| powerset-with-pruning | fast, consistent; an arbiter is affordable | **narrows** — rules worlds out | discard refuted branches; everyone learns promptly |
| **this design's** | Morton's slow inconsistent regime | **accumulates** — testimony rules nothing out | unavailable; its ghost is reader-side trust policy |

## Pruning is retraction

Discarding a branch un-asserts everything true only on it. So the inverse curse theorem
(Power, Koutris & Hellerstein 2025, Thm. 18) applies **at the level of `M`**: a monad with pruning has no
free termination unless refutations are *globally settled* — which needs exactly the arbiter the slow
regime denies. **The regime selects the monad**: fast-consistent settings can make "this branch is dead"
permanent, so they may prune; the slow regime cannot, so dead branches stay readable, which is `{t,f}`,
which is `if-built-today`. That document is the instance of the parametric substrate at the monad its
regime forces.

## The boundary test

> **Does this construct's meaning survive changing `M`?** If yes, it may sit in the substrate or the
> logic. If no, it must be a downstream convention or reading.

## The audit, and why it is the finding

`if-built-today`'s layering was drawn from the **opinion-free meta-constraint** — the substrate never
imposes a workload's semantics — *before* the parametric lens existed. Applying the test to every
construct:

| construct | survives changing `M`? | where the doc had already put it |
|---|---|---|
| send/receive ground terms | ✓ | substrate ✓ |
| definite clauses | ✓ — per-world, under any monotone accumulation | the logic ✓ |
| polarity | ✗ — under pruning, `¬Q` kills branches instead of testifying | declared schema, downstream ✓ |
| equality | ✗ — under a fast `M` it may quotient (EGDs, the chase); in the slow one it must be testimony | convention, opt-in ✓ |
| the four-value reading | ✗ — `{t,f}` does not exist where contradictions kill branches | derived reading, furthest downstream ✓ |
| settledness / thresholds | ✗ — under `Id` everything settles instantly | reading over the polarity convention ✓ |
| demand | ✗ — under a fast `M` it is an RPC | control, outside the logic ✓ |

**Every `M`-dependent construct was already below the line; nothing `M`-independent was.** Two independent
premises — opinion-freeness, and regime-parametricity — drew the same boundary, and the second was not
known when the first was applied. By the sketch's own standard (§"What makes the answer worth having"),
that is a constraint set *met twice* rather than chosen. A post-hoc rationalisation would have needed at
least one construct quietly moved to make the story work; the audit moved none.

## The commitments are the selection of `M`

`if-built-today`'s two commitments are not axioms under this lens — **permanence *is* "no pruning."** The
causal order is *universe → monad → constructions*: the regime forces the monad, the commitments state it,
everything else follows. The sketch's narrative already runs in exactly that order (§"What it is for",
then §"Two commitments", then the constructions), which nobody planned as universe-then-monad-then-instance.
A third convergence, smaller and of the same kind.

## What this is for

- **A placement criterion for future constructs**: apply the boundary test before deciding where a new
  idea lives.
- **The one-sentence version for the sketch** (landed in §"What gets built on top"): the constructions
  below the line are exactly those whose meaning varies with the universe the user inhabits, and the line
  was drawn before that criterion was known.
- **Not** a licence to develop the monadic story further inside `if-built-today`. If a fast-regime
  instance is ever wanted (real nondeterminism with branch-pruning, on one machine or under an arbiter),
  it is a *different instance of the same substrate*, and this page is where its design would start.

## Related

- [if-built-today](if-built-today.md) — the slow-regime instance, and the layering this page re-derives.
- [definite-clause-maximality](definite-clause-maximality.md) — the logic level's own characterisation
  (initial models ⟺ Horn up to definable partial functions), independent of `M`.
- `../dead_ends/topological-framings.md` — why this page stops at the boundary test.
