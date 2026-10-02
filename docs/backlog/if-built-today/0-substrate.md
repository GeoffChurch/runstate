# The substrate: records, variables, one operation

**Layer:** the base. The dependency graph is in `README.md`.

## The model: a store of records, one operation

```
post(c)        -- add a record to the store
```

Agents post. A **querier** and a **handler** are not different kinds of thing; they are agents posting
different records.

**There are no cells, and no built-in directions either.** The store is a growing set of records in
relations, and that is the whole of it. What a relation *means* — that one is read as a negation of
another, that one is a request — is schema the substrate never sees. `p(a, 1)` and `p(a, 2)` are two atoms,
and nothing combines them — a reader asking about `p(a, Y)` gets two answers. The only combination is **set
semantics** collapsing identical records. Two posts never merge, and no post ever changes.

**Every record is a constrained fact**, `A(x̄) :- c(x̄)` — a head and a body of constraints only. A ground
fact is the case with no variables. A record with variables is a **region**, and its variables are
universal by the ordinary reading of a clause: `p(X, Y) :- Y > 10` says every atom in that region holds.
**No posted record is existential** — a record has no `∃` to write — and that is what stops a poster
meaning *"some instance"* and being read as *"every instance"*: there is no *"some"* to mean. Every variable
must also occur in the body, so each one's range is stated rather than left to its sort, and a record with
an unranged variable is rejected on arrival, a check on the message alone. That buys explicitness, not
safety: in a many-sorted signature a range literal that merely restates a variable's sort still says
*"every value of the sort"*.

**A variable belongs to its record.** It is bound by the record it occurs in, numbered canonically by first
occurrence, and means nothing outside it — as a clause's variables mean nothing outside the clause. No
record refers to a variable in another, so there is no cross-host naming of unknowns, no answer by posted
equality, and no shared mutable object. Unification is local to a rule body inside one agent; sharing
across atoms is what a clause body is for, and it stays inside the clause.

**Why not variables scoped to the store**, which an earlier draft had (`decisions/0-substrate.md`,
§"The shared variable"). A variable that outlives its record lets one unknown appear in several records and
be bound once. The moment two posters bind it differently, a reader must choose one of three: a
**congruence**, which fabricates (`v = a` and `v = b` close to `a ≡ b`); **branching**, which is a set of
possible worlds — what refusing `∨` in heads excludes; or **one binding**, which needs an owner, hence an
arbiter, hence knowledge of every participant. Oz/Mozart's owner protocol — *"the owner accepts the first
binding request and ignores all subsequent"* — is the third, and buys a determinism this design declines.
With variables scoped to records there is no correlation across records, and none of the three arises.

**What it costs is promise pipelining.** Posting `g(a, V)` against a not-yet-known `f(a, V)` costs a hop:
learn `f`'s value, then post `g` at it. A user who wants the hop gone names the unknown by its definition —
a description term, `g(a, f_of(a))`, plus a rule resolving `f_of(a)` through `f(a, V)`. Nothing is minted,
and if `f` has two values the rule derives two atoms, because the correlation lives in a clause body.
**Sharing belongs to clauses, not to the store.**

Nothing merges, which is what makes monotonicity free rather than argued for. Merging is declined because it
**fabricates** — `f(a,Y)` and `f(X,b)` compressing to `f(a,b)` asserts a fact nobody posted, sound only
if something guaranteed one record per key, which nothing here does. That reason is decisive on its own;
what merging would have *cost* was measured separately (greedy merging is first-fit colouring of the
compatibility complement, within 1.04–1.20× of exact `χ` to n=24 and 1.21–1.32× of a heuristic to n=500,
with a crown separation at **12 positions giving `χ = 2` against 924 first-fit blobs**) and is recorded in
`decisions/0-substrate.md`, since a cost that cannot change the decision does not belong here.

**Terms, not blobs**, because the engine must traverse a term to match a pattern. That is the whole
argument, and it is **not** an indexing one: an index on a serialised key does as well, and positional
indexing over terms of different shapes is not merely slow but wrong, since one axis sits at a different
position in each.
