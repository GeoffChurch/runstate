# The substrate: records, variables, one operation

**Layer:** the base. The dependency graph is in `README.md`.

## The model: a store of literals, one operation

```
post(c)        -- add a literal to the store
```

Agents post. A **querier** and a **handler** are not different kinds of thing; they are agents posting
different records.

**There are no cells, and no built-in directions either.** The store is a growing set of records in
relations, and that is the whole of it. A schema that declares polarity (§"Polarity is schema, not
substrate") gets one relation, `told(A, P)` with `P ∈ {pos, neg}`, and reads `told(A, pos)` as *"A is
true"* and `told(A, neg)` as *"A is false"* — written `A` and `¬A` throughout this document. A schema that
declares none simply has relations, and the substrate never knows the difference. `loss(60, 0.5)` and
`loss(60, 0.4)` are two atoms, both true, and nothing combines them — a reader asking about `loss(60, V)`
gets two answers. The only combination is **set semantics** collapsing identical records. Two posts never
merge, and no post ever changes.

**Every record is a tell: a constrained fact**, `told(A(x̄), p) :- c(x̄)` — a head and a body of
constraints only. A ground fact is the case with no variables. A record with variables is a **region**,
and its variables are universal by the ordinary reading of a clause: `told(metric(r, loss, V, S), neg) :-
float(V), S > 743` says every atom in that region is false. **No posted record is existential** — tells
have no `∃` to write — and that is what stops a producer meaning *"some instance"* and being read as
*"every instance"*: there is no *"some"* to mean. Every variable must also occur in the body, so each one's
range is stated rather than left to its sort, and a record with an unranged variable is rejected on
arrival, a check on the message alone. That buys explicitness, not safety: in a many-sorted signature
`float(V)` restates `V`'s sort, and a producer who writes it by habit still says *"every float"*. Under
polarity, a tell's `P` is always ground — a region over polarities would tell an atom true and false at
once.

**A variable belongs to its record.** It is bound by the record it occurs in, numbered canonically by first
occurrence, and means nothing outside it — as a clause's variables mean nothing outside the clause. No
record refers to a variable in another, so there is no cross-host naming of unknowns, no answer by posted
equality, and no shared mutable object. Unification is local to a rule body inside one agent; sharing
across atoms is what a clause body is for, and it stays inside the clause.

**Why not variables scoped to the store**, which an earlier draft had (`decisions/questions.md`,
§"Record scope"). A variable that outlives its record lets one unknown appear in several records and be
bound once. The moment two producers bind it differently, a reader must choose one of three: a
**congruence**, which fabricates (`v = 0.5` and `v = 0.4` close to `0.5 ≡ 0.4`); **branching**, which is a
set of possible worlds — what refusing `∨` in heads excludes; or **one binding**, which needs an owner,
hence an arbiter, hence the roster. Oz/Mozart's owner protocol — *"the owner accepts the first binding
request and ignores all subsequent"* — is the third, and buys the determinism this design declines. With
variables scoped to records there is no correlation across records, and none of the three arises.

**What it costs is promise pipelining.** Posting `ckpt(r, V)` against a not-yet-known `best(r, V)` costs a
hop: ask for `best`, then ask for `ckpt` at the value. A user who wants the hop gone names the unknown by
its definition — a description term, `ckpt(r, best_of(r))`, plus a rule resolving `best_of(r)` through
`best(r, V)`. Nothing is minted, and if `best` has two values the rule derives two atoms, because the
correlation lives in a clause body. **Sharing belongs to clauses, not to the store.**

Nothing merges, which is what makes monotonicity free rather than argued for. Merging is declined because it
**fabricates** — `f(a,Y)` and `f(X,b)` compressing to `f(a,b)` asserts a fact nobody posted, which is
sound only under a functional dependency (`logic.md` §"Constraints are asked"). That reason is decisive on its
own; what merging would have *cost* was measured separately (greedy merging is first-fit colouring of the
compatibility complement, within 1.04–1.20× of exact `χ` to n=24 and 1.21–1.32× of a heuristic to n=500,
with a crown separation at **12 positions giving `χ = 2` against 924 first-fit blobs**) and is recorded in
`decisions/substrate.md`, since a cost that cannot change the decision does not belong here.

**Terms, not blobs**, because the engine must traverse a term to match a pattern. That is the whole
argument and it is **not** an indexing one: measured on 200k rows, a JSONB key with a btree expression
index runs the central range query in **0.085 ms** against a positional term layout's **0.089 ms** — the
term buys nothing, the btree does. Worse, positional indexing over *heterogeneous* terms is not merely
slow but wrong: with `loss(Config,Step)`, `grad(Config,Layer,Step)` and `ckpt(Run,Config,Shard,Step)` the
step axis sits at three different positions, and an axis-blind positional range returned **132,879 rows
against a correct 91,500**.

## What is checked, and what is the requester's

| | who |
|---|---|
| exact claims only on ground terms | **structural — not expressible otherwise** |
| sorts | **checked**, statically, at both ends |
| the quantity a demand denotes, and its finiteness | **one test, two owners**: the requester supplies the count, the declarative graph above runstate meters it (`../../layers.md`, layer 7) |
| a demand's extent is bounded | **the solver**, at post time; whether an unbounded extent is ever *covered* is an external producer's future — §Open |
| reclamation | policy, behind one interface |

**Finiteness is not a decidability claim.** Range-restriction over a grid looks like one and is not: it is
a syntactic test over a relation *asserted* finite, and *"is this derived relation finite"* is undecidable.
A run whose length is decided by convergence has no step grid known in advance. The guarantee is *what you
asked for is what you get*.

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
| a **produced** record whose producer still lives | a re-run — six hours, and *the same answer* only if production is deterministic | at a price, and only then |
| a **produced** record whose producer is gone | **unrecoverable** | no |

**The axis is re-derivability, and it has nothing to do with polarity** — an earlier draft made the last
row *"a negative claim"* and that was wrong in both directions. A positive fact from a run whose
checkpoint is deleted is exactly as unrecoverable, and evicting it descends `{t} → ∅`, the same forbidden
descent. And on determinism the ordering **inverts**: re-running a converged producer regenerates
`¬(S > 743)` exactly, where re-running a non-deterministic one may produce `loss(60, 0.5000001)` instead
of `0.5` — a *different atom*, which is a failed re-derivation dressed as a successful one.

So *"assuming the producer is still there, and deterministic"* is the clause the whole tiering turns on,
and neither clause is about which polarity the record carries.

## What the substrate is for

Three jobs, one of them not commodity:

- **persistence** — a store dies with its process, and runstate's whole contribution is durable identity
  outliving processes;
- **indexing** — noting that per-position term indexing over heterogeneous terms is not a database
  feature, and neither is unification;
- **an oracle channel whose outputs are timestamped into facts about the past** — the OS probe, the clock,
  the temporal delta, the fixpoint test. The store can tell you what happened; it cannot tell you that
  *nothing* happened, and `ensure` needs exactly that.

The third is the answer to *"why this library rather than Postgres plus a type discipline."* The buildable
object is closer to **a Prolog with a durable fact base and a pid probe** than to a schema. Coherent to
want; large to build.

