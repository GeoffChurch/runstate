# If built today: a monotone store of terms, geometric logic, demand as an unsatisfied existential

**Status:** a target sketch. Not a plan to rewrite — the honest answer to *"what would this look like
from scratch, and is append-only load-bearing?"*

The answer to the second half is **no**. The load-bearing property is **monotonicity**; append-only was
one way to get it, and there is a theorem saying so (§"CALM").

## The headline

**Attribution defects die under this treatment. Forgery defects do not.** Measured against a schema
built verbatim from the design, one of three named defects dies and two survive unchanged.

Attribution defects come from inferring identity by *position* in a total order. Making identity
**data** — something the record carries — deletes them. Forgery defects come from the absence of write
authority, which no representation changes: every forged write in the tests was a **legal** write under
this design's own rules, because the design keeps *"cooperative, no enforcement."*

| defect | class | fixed by identity-as-data? |
|---|---|---|
| unaimed heartbeat moving `progress` 0 → 500 | attribution | **yes** |
| a displaced worker's own terminal read as the run's verdict | attribution | **yes** |
| #39, the swallowed operator halt — discharge is author- and body-blind | attribution | **yes** |
| forged verdict silently truncating `ensure` | forgery | no |
| the claim cascade — one forgery, unbounded double-live | forgery | no |

The corpus measures the attribution side as real: **11 of 37 stops discharged by a record the worker
did not write, 6 of them malformed**. That is the case for the rewrite. It is a narrow case, and
stating it narrowly is the point.

## CALM: why monotonicity, and not merely for tidiness

**Consistency As Logical Monotonicity** (Hellerstein 2010; proved by Ameloot, Neven & Van den Bussche
2013): *a query has a coordination-free distributed implementation **iff** it is monotone.*

That converts a preference into a requirement. If a querier is far from the data — another host,
another datacentre, another planet — then:

- **monotone** ⟹ information flows one way, no ownership protocol, no consensus, no round trips,
- **non-monotone** ⟹ you must know you have seen everything, which costs a coordination round.

Two scoping notes that stop this being over-read. It is a **safety** statement: a lost demand and a
slow handler leave a querier in byte-identical states, so *liveness* still needs an acknowledgement, and
a bounded reconnect still needs a cursor. And it applies to the **answer** relation; demand is control
(§"Demand is control").

## The model: a store of terms, one operation

```
post(c)        -- add a constraint to the store
```

Agents post. A **querier** and a **handler** are not different kinds of thing; they are agents posting
different constraints.

**There is no "cell."** The store is a growing set of atoms in relations. `loss(60, 0.5)` and
`loss(60, 0.4)` are two atoms, both true, and nothing combines them — a reader asking `loss(60, V)`
gets two answers. Everything that looks like combination is one of two things: **set semantics**
collapsing identical atoms, or **one atom refining** as a variable inside it is bound. Two posts never
merge; one post gets more instantiated.

That is what makes monotonicity free rather than argued for.

**An unsatisfied existential is demand.** Posting `loss(60, V)` with `V` fresh asserts *"there is a
loss at step 60, and it is V"* — since `V` is unconstrained, it asserts only **existence**, and making
an unsatisfied existential true is exactly production. Three concepts fuse:

| was | is |
|---|---|
| the `?` sentinel | an unbound variable |
| `⊥` — no information yet | an unbound variable |
| "this is demanded" | an existential not yet satisfied |

So there is no `demand` predicate at the surface, no `while` combinator, and no watcher concept. A
watch is a posted term with a free variable; bindings arrive as they are learned.

**Ground vs free is the whole modality.** A ground post is an assertion. A post with free variables is
a call yielding a stream of bindings — Prolog's `p(a).` versus `?- p(X)`, unified under "post and let
the store reconcile."

**Streaming is constraint propagation to aliases**, not separate machinery. A reader's variable is
unified into the term, so refinement propagates along the alias with no subscriber registry at all.
Appends ride it too when a growing set is an open-tailed list: adding a member binds the tail, settling
closes it. The one place it does not reach is a *replicated multi-writer set* — a list fixes an
insertion order and `[a,b|T]` does not unify with `[b,a|T']`, so nodes that saw different arrival
orders build non-unifiable lists. There, new members need a standing-call registry: ordinary tabling.

**There is no `read`, and no syntactic substitute for one.** A tempting test — *"does the posted term
have a free variable in key position?"* — cannot carry the distinction. It is not invariant under
rewriting (`loss(S,V) ∧ S=60` would classify opposite to the identical `loss(60,V)`), and it
presupposes a key/value split no term carries: `verdict(Outcome, FinalStep)` has two value positions,
`provenance(key, Prid, Sha)` has two key positions, and nothing in either term says which. What two
verbs were really buying was **a declaration of intent that survives rewriting**, and a syntactic
property of a term cannot replace that.

Consequently **admission control needs an explicit mechanism**, and the natural signal is a *quantity*
rather than a flag: how many atoms does this posted term denote? That is layer 7's to meter; the
substrate's job is to make demand visible to it.

**Terms, not blobs**, because the engine must traverse a term to match a pattern. That is the whole
argument, and it is enough — it is **not** an indexing argument. Measured on 200k rows, a JSONB key
with a btree expression index runs the central range query in **0.085 ms** against a positional term
layout's **0.089 ms**: the term buys nothing, the btree does. Worse, positional indexing over
*heterogeneous* terms is not merely slow but wrong — with `loss(Config,Step)`,
`grad(Config,Layer,Step)` and `ckpt(Run,Config,Shard,Step)` the step axis sits at three different
positions, and an axis-blind positional range returned **132,879 rows against a correct 91,500**.

## The one rule

> **Threshold claims are always available. Exact claims require settledness.**

A rule body may claim `V ⊒ t` — *"the value carries at least this much information."* Monotone by
construction: values only go up, so once true, always true.

**And exact equality is not lost, it is a threshold at the right place.** For a ground `a`, `↑a = {a}`,
so `V ⊒ a` *is* `V = a`. What is inexpressible is equality against a **non-ground** term — *"V is
exactly this partial term and no more instantiated"* — which requires ruling out further instantiation,
i.e. negation. Exactness is available precisely where it is meaningful: at maximal elements.

**Settledness is groundness. Always, and at every level.** A value is settled when its term is ground; a
query is settled when the term representing its answer set is ground — closing the open tail of the
branch list. Same test, different subject. There is no producer verb and no `freeze`: closing a tail is
an ordinary post of the terminating constructor. Consequently there is no freeze-after-write race,
because there is no freeze.

It follows that **nothing may derive settledness from demand going quiet.** Demand disappearing grounds
nothing, so it cannot produce settledness — and the discipline that enforces this is ordinary ownership:
only whoever is producing the answers may close the tail. A reclaimer must never bind it.

**Matching a non-ground pattern is a threshold claim** — the ordinary case, not an exotic one. A body
literal `loss(60, f(X))` with `X` free claims *"the value is known to be an `f`-term."* It suspends
until that threshold is reached, then binds `X`. Once reached it stays reached and `X` can only refine,
so the whole thing is monotone. If the term holds `f(Y)` with `Y` unbound, `X` aliases `Y` and later
bindings propagate — the same mechanism, not an exception to it.

That is why "threshold claims only" costs so little. On an **algebraic** domain the compact elements are
the finite partial terms and the sets `↑p` form a **basis of the Scott topology** — verified
exhaustively for terms, `Flat`, `Set` and products. So *matching against a pattern is exactly asking a
basic open*, and every observable property is an arbitrary union of such matches: finite `∧`, arbitrary
`∨`. That is geometric logic again, and it means the restriction is a **basis**, not a limit.

The distinction to keep is **matching versus unification**: matching is one-way (the pattern's
variables bind, the term's do not) and is a *read*; unification is two-way and is a *post*. Rule bodies
match; agents post. That recovers `ask` and `tell` as a property of *position* rather than as two
operations.

**And it is not `var/1` in disguise**, though the objection is fair — instantiation tests are the
classic non-logical family, and `V ⊒ f(⊥)` *is* asking how far a term is instantiated. But that family
splits along the line already drawn: `nonvar` and `ground` are **monotone** (false, then true, never
back), `var` is **antitone**.

> You may test that instantiation has **reached** a threshold. You may never test that it has not.

which is precisely why `var/1` is non-logical and `ground/1` is not. It also means a rule whose body
fails to match must simply *not fire* — never take an else-branch — and with no negation there is no
else-branch to take. Failure of a match is unobservable, so the order in which matches succeed cannot
be detected: derivation *times* differ, the answer set does not.

## Geometric logic, which is what this language is

The derivation language is **geometric logic**: finite ∧, arbitrary ∨, ∃. **No ¬, no →, no ∀.**

Every restriction arrived at here independently is one of its clauses:

| decided here | geometric logic |
|---|---|
| definite clauses, no negation | no ¬ |
| carry all branches rather than backtrack | **arbitrary ∨** |
| conjuncts filter branches (the list-monad bind) | frame distributivity, `a ∧ ⋁bᵢ = ⋁(a ∧ bᵢ)` |
| threshold claims only | **opens** |
| monotone ⟺ coordination-free (CALM) | Scott-continuity |
| settled = ground = maximal; exact claims only there | total elements of a domain |
| negation confined to reports | **closed** sets — refutable, not affirmable |

The shape has a reason rather than an axiom (Vickers, *Topology via Logic*): **an open set is an
affirmable property** — confirmable in finite time from finite information, never refutable from it.
You may conjoin *finitely many* observations, because each takes finite time; you may disjoin
*arbitrarily many*, because any one suffices. That is a frame, and it is exactly this design's
constraint set.

**Why `¬` had to go.** To affirm `φ` you need a finite observation. To affirm `¬φ` you must rule out
*ever* affirming `φ`, which is a survey of everything there is. That survey **is** the coordination
round CALM prices. So "no negation," "opens are affirmable," and "monotone ⟺ coordination-free" are one
fact in three vocabularies — and reports are where that cost is paid, which is why they belong next to
the data.

**The derivation/report split is the open/closed split.** Derivation affirms; reporting refutes. They
cannot mix for the same reason the complement of an open is not open.

| | negation? | may feed demand? |
|---|---|---|
| **derivation** — what to produce | no; geometric | yes |
| **reporting** — what is missing, what is best, what diverged | **yes, inherently** | **no** |

`argmax` is therefore not expressible in derivation, so a bandit's one non-monotone step is forced to
the boundary. Its monotone half stays inside: `beaten(A) :- value(A,V), value(A2,V2), V2 > V` only ever
grows.

**One exception is real and does not repair.** The **residual** is a negation-bearing message
(`Q ∧ ¬E`) sent to a handler that produces from it, which is feeding demand by definition. It is benign
where re-production is idempotent. So the split is a discipline with one named exception, not a
structural guarantee.

## No functional dependency, and why

The tempting move is to declare a relation functional — *"for each key, exactly one value"* — so the
store can combine posts and compress. It should be resisted, and the reason is not the one it first
appears to be.

**It is not a syntactic problem.** `p(X,Y) ∧ p(X,Y') ⊢ Y = Y'` is a perfectly good geometric sequent:
geometric antecedent, atomic consequent. The `∀` and `→` live at the sequent level, which geometric
theories permit.

**The problem is what asserting it does to a store that must accept what it is given.** A store holding
`loss(60,0.5)` and `loss(60,0.4)` is then not a *model* of its own theory. There are three responses and
none survives:

| | |
|---|---|
| refuse the second post | order-dependent — whoever arrives first wins, and the store's contents depend on timing |
| derive the consequence | `0.5 = 0.4`, so the theory is inconsistent, so everything follows |
| record both and note the violation | fine — but then it was never an axiom |

Only the third works, and it is not a functional dependency at all: it is an **observation**. So:

> **Functionality is something a reader may ask about, never something the store asserts.**

And the affirmable half is the negative one. *"This relation is not functional at this key"* is a
positive existential over a growing set — monotone, geometric, decidable:

```
conflicted(K) :- loss(K,V1), loss(K,V2), V1 ⊔ V2 undefined.
```

*"This relation is functional here"* is its complement, hence closed, hence a report or a claim about a
settled relation. That asymmetry is structural, not stipulated: the well-formed region is a **lower
set**, so its complement is an **upper set**, so **conflict is affirmable and consistency is not.** You
may react to a conflict the instant one exists; you may never conclude there is none from partial
information.

It also explains why *non-joinability* is the right test rather than joinability: non-joinability is
stable (`f(a)` and `g(b)` clash and always will) where joinability is not (`f(X)` and `f(a)` join until
`X ↦ b`). Verified: zero counterexamples to stability across terms, `Flat` and `Lex`, under all
refinements. And the clash test itself is genuinely geometric — a finite disjunction over positions and
symbol pairs, each conjunct a threshold claim, agreeing with the unifier on every case tested. No
metalevel test is smuggled into the derivation layer.

**Two consequences of having no functional dependency.**

*No `⊤`, and none needed.* A per-relation top is what a collapse would land on, and a top necessarily
satisfies every threshold — that is what being the top means — so one disagreement would fire every rule
mentioning the relation. Keeping the atoms apart is what keeps the top out of reach. A "broken" flag is
the same defect wearing different clothes: discarding values and recording a bit is the one operation
that moves *down*, and it retracts — every rule that fired on `loss(60, f(X))` must un-fire.

*Nothing needs broadcasting.* Ask whether a reader who already got `f(a)` must be told when `g(b)`
arrives. A **threshold** claim is still true — somebody did post `f(a)`, and a later post does not
unpost it. An **exact** claim was never legitimate on an unsettled term. **No legitimate claim is
invalidated by a conflict**, so there is nothing to push and no registry of past contributors to keep.
The store owes a queryable predicate, not a notification.

**And conflict is reachable without forgery**, which is why it has to be designed for rather than
assumed away: two honest producers differing by one ulp (`0.30000000000000004` vs `0.3`) do not
reconcile. `mycooc/analyze_run.py` already hand-rolls a guard against exactly this — *"a crash-retry
episode fills gaps instead of re-emitting, so recompute jitter can never poison the log with divergent
same-cell values."*

**What this costs: compression.** With nothing combining, the store grows with every distinct post.
Duplicates collapse by set semantics and terms may share structure physically, but there is no *logical*
compression, and there cannot be without the declaration this section rejects. That is the price, and it
is the same trade as choosing a free join: pay storage, keep monotonicity.

## Types: many-sorted, per functor, structural

Each functor declares the sorts of its arguments and its result. The signature belongs to the
**program**, never to the data — no type is inferred from whoever posts first, which would make the type
check a first-writer-wins register decided by arrival order.

**Sorts are per functor and there is no untyped escape hatch.** A single flat relation —
`value(Name, V, Step)` for every metric — looks like it buys an open namespace, and it is unsound:
one functor has one signature, so every value position shares a sort, so `value(loss, X, S)` and
`value(converged, X, S)` may share `X`. That aliases a float slot to a bool slot with nothing to object,
and binding `X` silently gives `converged` the value `0.5`. Per-functor signatures reject it at the
alias: `loss/2` and `converged/2` give `X` two different sorts.

So a consumer with thirty metrics declares thirty signatures. That is a schema, and it is what catches
the measured case of one name carrying `None` under one flag and a nested record under another.

With a static signature a type conflict **is not expressible at runtime**. It is a program that does not
typecheck, caught twice: locally before anything is sent, and again on receipt, because at an
honour-system boundary a peer's message is never trusted. Neither check coordinates, and the reason is
precise: **a type error is a property of the message alone** — no store state is consulted — so
rejecting it is order-independent. That is exactly what a *value* conflict is not, which is why one is
checked at the door and the other is recorded and observed.

Agreeing the signature is deployment, not runtime. **The residual is real:** if signatures were
themselves data, posted like anything else, the problem returns unchanged. Keep them in the program.

### Orders are read-side

With nothing combining in the store, an order is not a storage type — it is how a **read** aggregates
what it finds. That moves the whole table off the safety path and onto the cost path:

| aggregation | note |
|---|---|
| last-write-wins | `argmax` over `seq`; a report |
| `max` / `min` | a report. On a **dense** carrier the only compact element is `⊥`, so `↑c` is not a basis and the affirmable claim is strict `⊐`, not `⊒` |
| set union | the identity read — everything, no compression, and the only option for a **holistic** aggregate (median, percentile), where Gray et al.'s taxonomy says no bounded *exact* summary exists. Bounded *approximate* ones do: a 200-bucket sketch reproduced a 2000-sample bootstrap CI to **1.07% of its width** |
| "must all agree" | the `conflicted` predicate above — **16 hand-rolled guard sites in the corpus**, the one primitive visibly missing |
| lexicographic | fine as a *selection* order, and dangerous as a *combining* one: with `attempt` at the head, `(1,running)` and `(1,crashed)` have least upper bound `(2,⊥)` — it **fabricates attempt 2**, silently, in a design about attribution. Nothing combines here, so this is a hazard avoided rather than managed |

**Where the narrowing (Smyth) construction goes.** The three powerdomains are the three ways to make "a
set of possibilities" into a domain: **Hoare/lower** (ordered by inclusion — *may* — accumulation),
**Smyth/upper** (reverse inclusion — *must* — narrowing), **Plotkin/convex** (both). Putting the upper
one on the *value* side is a category error: read extensionally as a set of facts it is antitone, so a
rule body binding a variable to a member is non-monotone, which breaks CALM at exactly the point CALM is
load-bearing. Its natural home is **demand** (*must* produce). That is open. And on a continuous carrier
it has no representable bottom and narrowing never reaches a singleton — measured to stall at 54
bisections — so settledness there arrives by naming the value, not by narrowing toward it.

**Terminology hazard.** Relational **⋈** and lattice **⊔** are both called "join," as are the lattice
join and the powerdomain pair. Name them differently in any implementation.

## Demand is control

Demand is not monotone: a lease expires, a querier withdraws, an operator halts a run. That is fine, and
it is worth saying why rather than listing it as a leak.

**Nothing derived becomes false; some things never get derived.** A withdrawn demand means a term is not
produced, so a reader's threshold claim never fires — it suspends forever. That is a **liveness**
failure, not a safety one. Two replicas with different demand produce different *subsets*; every atom
either of them holds is correct, and atoms are still only ever added. CALM is about replicas disagreeing
on an answer, and they do not.

All three mechanisms are control: resource management, a querier changing its mind, and somebody
deliberately stopping a machine. The logic never had jurisdiction over any of them, any more than it can
stop you pulling the power. What it does owe is the liveness back-channel named in §"CALM" — a lost
demand and a slow handler are indistinguishable — and the settledness footgun in §"The one rule".

## What the substrate is for — three jobs, one not commodity

- **persistence** — a store dies with its process, and runstate's whole contribution is durable identity
  outliving processes,
- **indexing**,
- **liveness resolution** — an OS probe. `live_episode` calls `resolve()`, and it is why a crashed claim
  holder does not strand the run forever. **A probe is neither persistence nor indexing**, and it is the
  honest answer to *"why this library rather than Postgres plus a type discipline."*

Note what the other two are not: per-position term indexing over heterogeneous terms is not a database
feature, and neither is unification. The buildable object is closer to **a Prolog with a durable fact
base and a pid probe** than to a schema. Coherent to want; large to build.

CCP (`ask`/`tell` over a monotone store) is the right model for the *semantics*; CHR is the right model
for the *execution*, where simplification keeps the store small without losing anything, provided the
body entails the head. One caution: CHR's store is a multiset, and idempotence is what makes one-way
replication safe — use set semantics.

## What is checked, and what is the requester's

| | who |
|---|---|
| exact claims only on ground terms | **checked** |
| sorts | **checked**, statically, at both ends |
| finiteness of what you asked for | **the requester's obligation** |
| admission control | the consumer's — layer 7 |
| reclamation | policy, behind one interface |

**Finiteness is not a decidability claim.** Range-restriction over a grid looks like one and is not: it
is a syntactic test over a relation *asserted* finite, it does not make one finite, and *"is this
derived relation finite"* is undecidable. A run whose length is decided by convergence has no step grid
known in advance. The guarantee is *what you asked for is what you get*.

**Reclamation is evolvable policy behind a fixed mechanism** — a lease, a budget, a settled extent, an
explicit guard, a user-supplied proof of an implicit guard, or eventually theorems about queries. Build
it as one injected interface. The hard constraint: **reclamation must not depend on receiving a
message**, because the requester may crash and the link may be long — the same argument that makes
`resolve()` probe a pid rather than trust a dying process to announce itself.

**The residual is not a blocker.** Ship `Q ∧ ¬E` with `E` the finite finished set and **do not
normalise**: `Q`'s structure survives intact, and `E` need not cross the link at all, since the handler
is near the data. What it *is* is a report, with the exception noted above.

## What survives from runstate

- **The run as a durable identity outliving its processes.** The actual contribution, now explicit.
- **Content-addressed run ids.** Becomes the cache key, unchanged.
- **The verdict as a join of two partial observers** — and it is a *report*, which is why it may use the
  narrowing reading that derivation may not.
- **Cooperative, no enforcement.** Load-bearing for the headline.
- **`never` is a value, not a status** — a fact about the world, not about an attempt.
- **Status cycles; values do not.** `running → OOM → running` cannot live in a monotone order, so the
  attempt index goes in the term and the cycle lives in the *sequence of attempts*, never in one fact.
- **Structure goes in the key, not the value.**

## What it does NOT solve

- **Cross-host liveness.** You still need a handle and a probe, and it still abstains off-host — and
  that abstention must not become a stored verdict.
- **The artifact plane.** Checkpoints on a filesystem remain unmodelled, and remain where a double-live
  worker's real damage lands.
- **Enforcement.** Still honour-system. This is why forgery defects survive.
- **The halt does not dissolve.** Self-withdrawal is free — scope a posted demand to its subscription
  and disconnecting ends it, with no authority question because you own your own connection. But the
  measured case is an **operator** stopping a run a **scheduler** relaunches, i.e. withdrawing *someone
  else's* demand. That needs a write and an authority rule, and always did.

## The honest cost

A **rewrite, not a refactor**, with consumers on the current API. It trades a design whose failure modes
are intimately known for one whose failure modes would have to be learned. And storage grows, since
nothing combines.

**No fold ports as-is.** Measured across the whole of `observables.py`: 13 of 16 fold readings are
non-monotone, and the operator responsible is `latest` = `argmax(seq)`, which appears six times directly
plus three `[-1]`/`reversed` and three `max(…)` in 551 lines. Each becomes **dual plus subtraction** —
the monotone half derived inside, one complementation performed outside:

```
inside   discharged(C) :- stop(C), stopped(S), S > C.
outside  unhandled = stops − discharged
```

All nine duals (`superseded`, `ended`, `discharged`, `answered`, `reached`, …) measured monotone, so the
inside half is where the work is and the outside half is a subtraction. That is the same discipline as
*fold the observers separately, join only at the verdict* — but it is eight rewrites, and none of them
is mechanical.

## Open

1. **Whether the upper (Smyth) powerdomain belongs on the demand side.** It is the *must* construction
   and demand is a *must*; suggestive and unworked.
2. **How much a quotient of the term algebra buys.** Imposing equations makes the join E-unification,
   which is well-defined only for unitary or finitary theories; in the finitary case it lands in a set
   of most-general unifiers rather than one term.
3. **Where the query language stops.** It need not be decided up front. What constrains it is
   **pushdown**: the more expressive the language, the less of it runs where the data lives, and
   locality is what CALM makes non-negotiable.
4. **Whether a partially-narrowed value may be streamed.** Safe under exactly one discipline — consumers
   may make threshold claims, never membership claims — which is §"The one rule" applied in flight.

## Related

- `demand-driven-reads.md` — the consumer-facing target. **Stale**: it still describes a
  LEFT-JOIN-over-a-grid, `?` as a value, `read`/`force` as verbs, and LISTEN/NOTIFY. Its §5a taxonomy
  survives, with the Pitman–Koopman–Darmois paragraph corrected to Gray's holistic class.
- `prolog-query-layer.md` §3 — the measured answer-subsumption results, reinterpreted: the defect is an
  exact claim on an unsettled term, not aggregation-in-recursion, and it does not arise here because
  nothing aggregates at write time.
- `../specs/write-authority.md` — unchanged by any of this; a unique constraint is test-and-set
  (consensus 2), where `send(expected_seq=)` is compare-and-swap (consensus ∞).
- `../layers.md`, `../positioning.md` — where this sits.
- Vickers, *Topology via Logic* — opens as affirmable properties; the source of the geometric framing.
