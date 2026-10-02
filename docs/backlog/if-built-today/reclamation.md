# Reclamation: eviction, never retraction

**Layer:** depends on `substrate.md`, `logic.md`, `polarity.md`. The dependency graph is in `README.md`.

## Reclamation is policy behind one interface

**Reclamation is evolvable policy behind a fixed mechanism** — a lease, a budget, a settled extent, an
explicit guard, or eventually theorems about queries. Build it as one injected interface. The hard
constraint: **reclamation must not depend on receiving a message**, because the requester may crash and
the link may be long — the same argument that makes a liveness probe check a pid rather than trust a dying
process to announce itself.

**And reclaiming a derived truth is eviction, not retraction.** The line is re-derivability: an atom a rule
can recompute costs only time to lose, so deleting it changes what is *stored* and not what is *true*. What
the reclamation layer owes is a **caching invariant** — anything evicted must be re-derivable on demand —
which keeps the *observable* store monotone while the stored one is not. Deciding what qualifies is
reachability, and the tiers are not in the order storage cost suggests:

| | cost to lose | evictable? |
|---|---|---|
| a **derived** atom | recompute | freely, while its premises survive |
| a **produced** record whose producer still lives | a re-run — and *the same answer* only if production is deterministic | at a price, and only then |
| a **produced** record whose producer is gone | **unrecoverable** | no |

**The axis is re-derivability, and it has nothing to do with polarity** — an earlier draft made the last
row *"a negative claim"* and that was wrong in both directions. A positive fact from a producer whose
inputs are gone is exactly as unrecoverable, and evicting it descends `{t} → ∅`, the same forbidden
descent. And on determinism the ordering **inverts**: re-running a finished producer regenerates its
negative tail exactly, where re-running a non-deterministic one may produce `p(a, 0.5000001)` instead of
`p(a, 0.5)` — a *different atom*, which is a failed re-derivation dressed as a successful one.

So *"assuming the producer is still there, and deterministic"* is the clause the whole tiering turns on,
and neither clause is about which polarity the record carries.
