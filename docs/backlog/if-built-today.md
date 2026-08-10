# If built today: a monotone store of terms, geometric logic, demand as an unsatisfied existential

**Status:** a target sketch. Not a plan to rewrite — the honest answer to *"what would this look like
from scratch, and is append-only load-bearing?"*

The answer to the second half is **no**. The load-bearing property is **monotonicity**; append-only was
one way to get it, and there is a theorem saying so (§"CALM").

## The headline

**Attribution defects die under this treatment. Forgery defects do not.**

Attribution defects come from inferring identity by *position* in a total order — which episode a
heartbeat belongs to, which claim a terminal pairs with, which stop a terminal discharges. Put the
identity in the term as an ordinary argument and there is nothing left to infer: a report asking about
episode 2 cannot pick up episode 1's records, because they do not match.

Forgery defects come from the absence of write authority, which no representation changes. A forger
posting `stopped(episode2, completed)` is making a well-typed post that the store accepts, exactly as
the design intends — *"cooperative, no enforcement"* is kept, so nothing here touches it.

**Note what is measured and what is argued.** The **defect** is measured, in the current corpus. The
**fix** is an argument — that naming a thing removes the need to infer it — and it is a small and
obvious one. An earlier measurement of "one of three defects dies" was taken against a table-and-FK
schema that this design no longer contains, so it supports a claim about a different artifact and is
not carried forward.

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

**Why merging is declined, stated carefully.** The tempting optimisation is to unify what unifies and
union the rest, so `f(a,Y)` and `f(X,b)` compress to `f(a,b)`. Two things are wrong with it, and only
the first is decisive.

**It fabricates.** `f(a,b)` is a fact nobody posted. Merging is sound only if the two posts describe
*one* thing — which is a functional dependency, and §"No functional dependency" says why that cannot be
asserted here. Without it, merging invents.

**And the greedy form is order-dependent** — though note the culprit is the *fallback*, not unification.
Unification of a **set** is perfectly order-independent: `mgu({A,B,C})` is unique up to renaming and
undefined in every order or none. What breaks is falling back to union when a merge fails. With
`A = f(a,Y)`, `B = f(X,b)`, `C = f(c,Z)`:

```
(A ⊔ B) ⊔ C  =  f(a,b) ⊔ f(c,Z)  =  {f(a,b), f(c,Z)}
A ⊔ (B ⊔ C)  =  f(a,Y) ⊔ f(c,b)  =  {f(a,Y), f(c,b)}
```

`B` is compatible with both neighbours, but spending it on one destroys its compatibility with the
other, and which neighbour spends it is decided by arrival order. Two replicas then hold different
stores and give different answers, neither more right than the other.

**How much order-dependence costs is a known quantity: it is greedy graph colouring.** For **linear**
terms — no repeated variables — a term is a partial assignment, two terms merge iff they agree where
both are defined, and consistency is **pairwise**: if `A,B` agree and `B,C` agree and `A,C` agree then
`A∪B∪C` is well defined. So a mergeable group is exactly a **clique** in the compatibility graph `G`,
and greedy merging — put each arriving term in the first blob it fits, else start a new one — is
**first-fit colouring of the complement `Ḡ`**, since a colour class in `Ḡ` is a clique in `G`.

All three cases then come off the shelf:

| | value | |
|---|---|---|
| **best** | `χ(Ḡ)`, the clique cover number | the optimal ordering — NP-hard to find |
| **worst** | `Γ(Ḡ)`, the **Grundy number** | the most parts first-fit can be made to produce |
| **average** | ≈ **2×** optimum on random instances | greedy on a random order uses `~n/log_b n` colours against `~n/(2 log_b n)` |

**The gap is not a constant factor.** On the crown graph — `K_{n,n}` minus a perfect matching —
`χ = 2`, but first-fit on the interleaved order `u₁,v₁,u₂,v₂,…` uses `n` colours. One arrival order
gives 2 blobs where another gives `n`.

Two caveats. Refusing to merge when *two* candidates match — a tempting way to buy determinism — is
strictly worse on size than first-fit, because it adds a part exactly where first-fit would have merged.
And **non-linear terms break the model**: with a repeated variable, pairwise consistency no longer
implies joint (`f(X,X)`, `f(a,Y)`, `f(Z,b)` are pairwise mergeable and jointly contradictory), so the
clique picture is a lower bound on the difficulty rather than the answer.

**The canonical alternative is order-free and unbounded.** Replace each **maximal consistent subset** by
its mgu: a function of the whole set, so no ordering enters. Its size is the number of maximal cliques,
up to `3^(n/3) ≈ 1.44ⁿ` by Moon–Moser — an element is duplicated into every clique it belongs to. So
canonicity is available and may be exponentially larger than the input.

None of that makes greedy merging unusable, and there is real room above it if the fabrication problem
were ever solved: buffer arrivals and compact only under memory pressure, so the ordering is chosen
rather than inflicted; or refrain from merging when a heuristic suspects the merge will turn out to have
blocked a better one, trading a little size for a lot of variance. But the free completion's join —
union, then drop anything subsumed — is associative, bounded, deterministic, and needs none of it. It
compresses strictly less, and that is the cheap side of the trade.

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

**Answers stream individually, and completeness is a separate posted fact.** A querier posts a pattern
and receives matching atoms one at a time; there is no answer *object* anywhere. Closure is the atom
`closed(Q, p)` — *"producer p will send no more for Q"* — delivered like any other fact.

That is worth stating because packaging the answers into a growing term is a tempting and dead end.
Nothing bindable is unordered: a set term is ground, so adding to it is not a binding but a different
term, which leaves a **list** — and a list fixes an insertion order, so two nodes learning the same
answers in different orders build `[a,b|T]` and `[b,a|T']`, which unification cannot reconcile because
it cannot reorder a spine. The divergence is representational rather than an information deficit, so
redelivering every message does not repair it. (A node merely *lagging* on an order-preserving link is
fine — lag and divergence are different failures.) A shared tail is also a lossy CAS: once one producer
binds `T = []`, another's next answer is rejected outright. One tail *per producer* fixes all of that,
since each stream is then single-writer — but it is machinery for nothing, because **no rule ever
observes absence** (§ below), and `closed(Q,p)` beats `T = []` anyway by being attributable.

A set term with an unbound "rest" would sidestep the ordering, and costs more than it saves: union
modulo associativity, commutativity and idempotence is **finitary rather than unitary**, so the join of
two such terms is a *set* of most-general unifiers rather than one term, and matching modulo ACI is
NP-hard.

**So absence needs no representation until somebody closes the world.** *"Nothing is there yet, so
produce it"* looks like it needs `¬∃` — and under an **open** world that is not merely banned but
unknowable, since you can never say *no answer exists*, only *no answer has arrived*. It turns out
nothing needs it, because production is **demand-gated**: a producer runs because somebody asked, never
because something was found missing, so no rule mentions absence at all.

And "demand-gated" needs no machinery either. **A demand is a posted pattern; a producer is an agent
that watches for patterns it can serve, computes, and posts atoms that unify with them.** There is no
`demand_p` relation and no gating rule — which matters, because writing one as
`value(X,V) :- demand(X), handler(X,V)` smuggles a key/value split back in through the adornment, and
this design has no cells. A producer needing something of its own just posts a pattern too, which makes
it a querier; the roles stay symmetric all the way down.

Under a **closed** world `¬∃` becomes an ordinary negative fact you may affirm. Which regime you are in
is a choice somebody makes and posts.

### CWA is a posted fact, and it has scopes

Completeness is **two facts, and you need both**:

| level | the fact | what it is about |
|---|---|---|
| per producer | `closed(Q, p)` — *"p will send no more for Q"* | one agent |
| the scope | *"no further producer will appear for Q"* | the world |

Together they make settledness a `∀` over a **known finite** set, i.e. a finite conjunction — and that
is exactly when `¬∃` becomes affirmable, because *"no answer arrived"* is then decidable by inspecting
finitely many exhausted streams rather than by surveying an open world.

| producer scope | the `∀` | consequence |
|---|---|---|
| **open** | unaffirmable | nothing settles; absence stays permanently provisional |
| **closed** | a finite conjunction | geometric — settledness, and negation, become available |

So *"no more producers"* is an ordinary monotone post, and **posting it is the closed-world toggle.**
That is better than leaving the assumption to the evaluator, because it makes the assumption **visible
and attributable**: somebody asserted it, and if they were wrong, a producer appearing afterwards is a
plain contradiction the store can detect.

**And it should be scoped, not global.** A global producer set is the coarsest possible toggle and
almost never what you want. Per-functor scopes — each a subset of the global one — may be closed
*earlier* and independently: *"no more producers for `loss`"* makes `loss` queries settleable while
everything else stays open.

**Partial closure buys nothing toward a global answer**, and it is worth saying so because it looks like
it should. Answers accumulate monotonically, so whatever has arrived is already a lower bound whether or
not anyone is finished; knowing `p` is done adds no answers. Closure is an **upper**-bound statement —
*"p contributed exactly this"* — and upper bounds only compose when you have all of them. Where partial
closure does pay is a query *scoped to that producer*: `closed(Q, p)` settles *"what did p produce?"*
completely.

**Where new producers come from is worth reifying.** If the set is open, producers emerge from
somewhere; naming that — `spawns(P, Q)` — makes the producer set the transitive closure of spawning from
declared roots, and closing it becomes *"no more spawning,"* which is a fact about a *finite* thing.
This is the neighbourhood of embedded implication and hypothetical reasoning, but it does not need
either: *"if this scope is closed then Q is settled"* is a **geometric sequent** (geometric antecedent,
geometric consequent), so it lives in the theory rather than needing `→` in the goal language.

Who may close a scope is a write-authority question, and `../specs/write-authority.md` says that is
unenforceable. So the toggle is honour-system like everything else: a wrongly-closed scope is a defect
the store can **detect** but not **prevent**.

**One thing that is not an optimisation, though it looks like one.** Production is gated on demand, and
before running a producer the evaluator asks *"do I already have an answer?"* That memo check **is the
cache** — without it the producer re-runs on every call, and a run here is a six-hour job. It is also
semantics rather than housekeeping: where re-production is not bit-identical, removing it changes the
answer set outright and flips `conflicted(K)` from false to true. An optimisation may not change the
answer set; this one does.

**Ground vs free is the whole modality.** A ground post is an assertion. A post with free variables is
a call yielding a stream of bindings — Prolog's `p(a).` versus `?- p(X)`, unified under "post and let
the store reconcile."

**Streaming is constraint propagation to aliases**, not separate machinery. A reader's variable is
unified into the term, so refinement propagates along the alias; appends ride the same mechanism, since
adding to a producer's stream binds its tail.

It does **not** remove the need for a call table, and an earlier claim that it did was wrong. Two
queriers independently posting `loss(60,V)` hold **distinct** terms with distinct tails, so one
producer answer does not satisfy both — the producer must bind both, which requires knowing both calls
exist. The two calls are `variant`-equal, i.e. they share a table key, so what is needed is a set of
outstanding calls keyed by skeleton. That is ordinary tabling, it is the registry, and the honest claim
is only that **no separate conflict-notification registry is needed** (§"No functional dependency").

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
positions, so an axis-blind positional range over position 1 also matches on `Config` and `Layer` and
returns the wrong rows.

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

**Two exceptions are real and do not repair**, so the split is a discipline with named exceptions rather
than a structural guarantee.

- **The residual** is a negation-bearing message (`Q ∧ ¬E`) sent to a handler that produces from it,
  which is feeding demand by definition. Benign where re-production is idempotent.
- **`ensure` itself**, which is the library's core operation. Measured: its loop condition is a threshold
  claim on `progress`, a *retractable* quantity, and its two termination guards are a **temporal delta**
  (`progress` now versus `progress` before — *"nothing new was derived"*, which has no positive form)
  and an **inflationary fixpoint test** (*"another lap can only reproduce them"*, per its own
  `RecordlessExitError` docstring). Neither is geometric, and both feed demand, because both decide
  whether to relaunch.

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

**What this costs, and where it does not.** The rejection above is narrower than it looks: it applies
only where the order is **partial** in the sense that some pair of values has *no least upper bound* —
`must-all-agree` on distinct values, unification on a clash. Linearity is irrelevant; set union is not a
chain and joins always exist there.

> **Any join-semilattice may be quotiented at the storage layer, freely and soundly.**

Sound in one line: `a ⊔ b ⊒ a` and `a ⊔ b ⊒ b`, and thresholds are upward-closed, so nothing either post
satisfied can stop being satisfied. That is §"The one rule" doing its job, and it is why the classic
`X = 0` counterexample does not bite — that is an exact claim on an unsettled relation, already banned.

**But off a chain, the join *synthesizes*.** `a ⊔ b ∈ {a, b}` exactly when the order is total; otherwise
the join can be a third thing. `{a} ⊔ {b} = {a,b}`, and nobody said `{a,b}`.

That is not unsoundness — under a declared `Set` order the stored value *is* the union, so the claim is
simply true, and the un-quotiented reading is asking a different question. What it costs is
**provenance**: on a chain the stored value is always one somebody actually posted, and off a chain
*"who said this?"* has no answer. Worth knowing before choosing a non-chain order for anything whose
authorship matters.

Where the order *is* partial there is nowhere to put the result, and completing it is the only way out.
There are exactly two completions:

| | |
|---|---|
| add a `⊤` | total and compressing — and it explodes, since a top satisfies every threshold |
| take the **powerset** | total and sound — and it does not compress |

The design takes the free completion, which is what "keep the atoms" has meant throughout.

**And the whole argument is smaller than it reads.** Measured over **823 real logs, 2.5M records**: the
compression given up is **0.34%**, and cells whose values genuinely fail to join are **0.072%**. Both
directions are a footnote, not a design axis. Where the partial case *does* land is instructive and
supports keeping the atoms — 1,714 of the 1,719 divergent cells are `status`, an app event mirrored onto
the value plane at a reused step, where last-write-wins picks one and reports a run as *saving* at step
87 of training.

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

**Wrappers recover what the flat relation was for, as schema rather than machinery.** Per-functor sorts
make `loss/2` and `accuracy/2` different relations, so *"every metric"* would be `∃F. F(S,V)` — not
first-order. A user wraps, and nothing in the engine changes, because a wrapper is only a term:

```
metric(loss(60, V))        -- Metric = loss(Step,Float) | converged(Step,Bool) | …
table1(loss(V), 60)        -- Metric = loss(Float)      | converged(Bool)      | …
```

The first buys **rangeability**: `metric(M)` with `M` free enumerates every metric, and distinct
constructors cannot cross-unify, so the ill-typed aliasing a flat relation admits is still impossible.
Step stays inside each constructor at whatever position that constructor chose, so *"everything at step
60"* is not yet one pattern.

The second **hoists the axis out**, making step a shared position so `table1(M, 60)` *is* one pattern.
That is exactly the defect the indexing measurement found — with `loss(Config,Step)`,
`grad(Config,Layer,Step)` and `ckpt(Run,Config,Shard,Step)` the step axis sits at three different
positions, and an axis-blind positional range returned **132,879 rows against a correct 91,500**.
Reifying the axis fixes it in the user's own schema with zero engine support.

Which is *"structure goes in the key"* applied one level up: how much shape to expose is the schema's
decision, and the substrate stays ignorant of all of it.

With a static signature a type conflict **is not expressible at runtime**. It is a program that does not
typecheck, caught twice: locally before anything is sent, and again on receipt, because at an
honour-system boundary a peer's message is never trusted. Neither check coordinates, and the reason is
precise: **a type error is a property of the message alone** — no store state is consulted — so
rejecting it is order-independent. That is exactly what a *value* conflict is not, which is why one is
checked at the door and the other is recorded and observed.

Agreeing the signature is deployment, not runtime. **The residual is real:** if signatures were
themselves data, posted like anything else, the problem returns unchanged. Keep them in the program.

### Orders are mostly read-side

By **default** an order is not a storage type — it is how a **read** aggregates what it finds, which
moves the whole table off the safety path and onto the cost path. A relation whose order is a
join-semilattice may additionally be quotiented at storage (§"No functional dependency"); that changes
no answer **on a chain**, and off one it synthesizes values nobody posted:

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
it has no representable bottom and narrowing never reaches a singleton — bisection converges without
terminating — so settledness there arrives by naming the value, not by narrowing toward it.

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

### Facts and demands are dual, and the duality is exact

- A **fact** is a ground term: a maximal element, a **point**.
- A **demand** is a pattern, and a pattern denotes `↑p`. Finite partial terms are exactly the **compact**
  elements, and `↑c` for compact `c` is a **Scott-open**. So a demand *is* a basic open.
- Satisfaction is `x ∈ U` — the pairing between a space and its frame, the same shape as a vector
  against a covector.

That is Stone duality (`Loc ≃ Frm^op`), not the sense of "polarity" in which positives and negatives
cancel. But the proof-theoretic sense of polarity *is* apt, and says the same thing twice over:
**geometric logic is essentially the positive fragment** (`∧`, `∨`, `∃` are positive; `→`, `∀`, `¬` are
negative), and **a fact is data where a demand is a continuation** — *"I want this"* being a computation
waiting on a value. It is also why the querier's and handler's interfaces keep coming out as dual
session types: the same duality, seen a third way.

**Where it should live: the term level, not the logic.** Two sorts — `Point` for ground facts, `Open` for
patterns — with the pairing between them. Geometric logic over a two-sorted signature is unchanged
geometric logic; nothing about `∧`/`∨`/`∃` moves.

**Demand subsumption runs the other way, because `↑` is order-reversing.** For facts, more instantiated
is higher, and compression drops the *less* instantiated. For demands, `p ⊑ q` gives `↑p ⊇ ↑q`, so the
**more general** demand subsumes the specific one: having posted `loss(V,S)`, a standing `loss(V,12)`
is redundant for production and can be dropped from the work set.

But go no further than subsumption. **Anti-unification is the join in the demand order and it
over-approximates**: generalising `loss(V,12)` and `loss(V,13)` yields `loss(V,S)`, which demands *every*
loss. On the fact side the join is safe because it adds information; on the demand side the join is
dangerous because it adds *work*, and here a unit of work is a six-hour job. The asymmetry is exactly
the contravariance. One caveat on the compression itself, and it is the ordinary one: **derive the work
set, never destructively shrink it.** A demand, once recorded, is never withdrawn — but a *subscription*
can end, and when the subscription to `loss(V,S)` does, `loss(V,12)` may still be live. Recomputing the
work set from the surviving subscriptions handles that for free; deleting the covered entry when it was
first subsumed does not.

**What to internalise, and what not to.**

| | monotone? | where |
|---|---|---|
| the **demand relation** — *"this was wanted; this depends on that"* | yes, accumulates | internalised, a fact about an `Open` |
| the **live subscription** — *"someone is listening now"* | no, revocable | control, outside the store |

Production is triggered by the subscription; the internalised demand is a durable record for reasoning —
dependency graphs, admission analysis, *"what has ever been asked of this producer."* That makes
*"what is demanded"* an ordinary query rather than a snapshot the core layer has to inject, while
leaving the non-monotone half exactly where this section already puts it.

Concretely a subscription is a **cursor into a per-functor term index** — walk the trie with your
pattern, get notified when new leaves appear beneath it. That keeps `post` the only store-mutating
operation: reading is walking the index, and a standing call is the evaluator remembering where you
were walking.

**Internalising does not put `→` within reach, and the reason says what to guard instead.** Opens form a
**frame**, and a frame necessarily *has* implication: completeness forces `a → b = ⋁{c : c ∧ a ≤ b}` to
exist. But having it mathematically is not being able to write it. That definition needs a
**comprehension** — the family is carved out by a condition — whereas geometric logic's arbitrary `∨`
ranges over a **given** index family, never one a predicate selects. Nor can the condition be smuggled
in as a conjunct instead: `c ∧ a ≤ b` is entailment, and internalising entailment as a formula *is*
implication. The bootstrap is circular, so `→` stays unreachable and the guardrail stays syntactic.

What that identifies is the thing actually worth guarding — not internalisation, but **comprehension**.
Any construct that forms a family of opens by a predicate over opens puts implication one line away, and
with it `¬a = a → ⊥`. A user who enumerates a finite family by hand and disjoins whichever members
satisfy some condition has merely written a fixed formula, which is monotone and transports; the danger
lies only in a join whose family is *recomputed as the frame grows*.

## What the substrate is for — four jobs, two not commodity

- **persistence** — a store dies with its process, and runstate's whole contribution is durable identity
  outliving processes,
- **indexing**,
- **liveness resolution** — an OS probe. `live_episode` calls `resolve()`, and it is why a crashed claim
  holder does not strand the run forever,
- **an observation that nothing changed.** The store can tell you what happened; it cannot tell you that
  *nothing* happened. Facts only arrive, a non-arrival leaves no trace, and `ensure` needs exactly that
  — it gives up by reading how far a run got, running a cycle, and finding the number unmoved. Comparing
  two moments is not something a growing set of facts can do.

The last two are the answer to *"why this library rather than Postgres plus a type discipline."* Note
what the first two are not, either: per-position term indexing over heterogeneous terms is not a database
feature, and neither is unification. The buildable object is closer to **a Prolog with a durable fact
base and a pid probe** than to a schema. Coherent to want; large to build.

## Two layers, and what crosses between them

Not everything can be geometric, and nothing is gained by pretending. The shape that works is **a
non-geometric core underneath a geometric layer**, where each layer pushes as much as it can downward:

- the **core** makes observations that the logic cannot: an OS probe, a clock, a temporal delta, a
  fixpoint test;
- the **geometric layer** derives over them and owns everything it can.

What crosses is **atoms**, and the mechanism is already in this repo — `prolog-query-layer.md` says of
the one existing case, *"pass the probe result in as a parameter rather than calling out."* The
generalisation that makes it work for the harder cases is the repo's recurring repair, applied to
oracles rather than to data: **timestamp the observation and make it a fact about the past.** *"Nothing
changed between T₁ and T₂"* is permanently true once observed, where *"nothing has changed"* never is.

Two consequences. The core should be as small as possible, and every primitive it can hand upward as an
atom is one the geometric layer gets to reason about instead. And because the timestamps are
**per-observer** — clock skew already breaks `last_activity` in the measurements — the atoms must carry
*who observed, by whose clock*, and any comparison across observers is a report rather than a derivation.

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
are intimately known for one whose failure modes would have to be learned. And storage grows wherever
the order is partial, since there the only sound completion is the free one.

**No fold ports as-is.** Measured across the whole of `observables.py`: 13 of 16 fold readings are
non-monotone, and the operator responsible is `latest` = `argmax(seq)`, which appears six times directly
plus three `[-1]`/`reversed` and three `max(…)` in 551 lines. Each becomes **dual plus subtraction** —
the monotone half derived inside, one complementation performed outside:

```
inside   discharged(C) :- stop(C), stopped(S), S > C.
outside  unhandled = stops − discharged
```

**The inside half is confirmed.** 13 of 13 duals measured monotone, and **9 of 9 folds reconstruct
exactly** from them across every scenario tested. The worked example above reproduces the shipped
`undischarged_stops` verbatim, one subtraction.

**The outside half is "a subtraction" for 5 of 9**, and the residuals differ in kind:

| residual | folds | what it is |
|---|---|---|
| subtraction only | `latest_episode`, `_episode_stopped`, `undischarged_stops`, `value_series`, `live_episode` (+ the probe) | the clean case |
| + an **aggregate** over a non-`seq` order | `progress`, `last_activity` | a `max` over the survivors of the subtraction — and `last_activity`'s is over `t`, which its own docstring notes is non-monotone against `seq` |
| + an **emptiness test** on an already-complemented set | `_launcher_terminal`, `peek_terminal`, the four-state projection | `¬∃ started`, which no finite observation can affirm — measured to **retract a published verdict** when a later claim arrives |

**And picking the dual is where the danger is.** The obvious dual of `progress` is the high-water mark
`reached(K) :- heartbeat(_,S), K ≤ S` — and it is **wrong**, for the reason `progress`'s own docstring
gives: *"a monotone watermark here would re-open the splice it just closed."* A watermark cannot roll
back, so after an episode rewind it reports the old frontier; `ensure`'s window test then passes and
returns a **spliced series as complete** — runstate's own known splice bug, reintroduced by the repair.
The correct dual is the complement of *"a later heartbeat exists."* `last_activity` has the same trap
one axis over: its threshold dual takes a max over `t`, so a single fast clock poisons it and the GC's
grace window reads the poisoned value.

So this is eight rewrites, none mechanical, and at least one where the *natural* dual silently
reintroduces a bug the fold exists to prevent.

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
5. **No central store.** Each agent holds a lagged local copy and replicates preferentially what it
   demands; the "global" store is the union of the local ones, with no ground truth anywhere. This
   follows from monotonicity and needs no coordination — nobody deletes, sharding and replication are
   free, and there is no single point to lose. The consequence to be explicit about: the memo check
   becomes **local**, so caching is best-effort, and two agents demanding the same cell without having
   replicated each other's answer both run the six-hour job. That is precisely single-spawn, the one
   irreducibly coordinating requirement, and CALM says it must cost a round.
6. **What `conflicted(K)` means when single-spawn did not hold.** Duplicate production plus re-production
   jitter yields two slightly different atoms, which is a *correct* report of a real disagreement that
   the system itself caused. So `conflicted` cannot be read as "something is wrong" without knowing
   whether one producer was guaranteed. Measured counterweight: 0 of 3,165 numeric re-productions
   diverged on the real corpus, so the hazard is real and the consumers' hand-rolled guards are working.

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
