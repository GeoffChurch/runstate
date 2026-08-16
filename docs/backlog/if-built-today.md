# If built today: a monotone store of polarised literals

**Status:** a target sketch. Not a plan to rewrite — the honest answer to *"what would this look like
from scratch, and is append-only load-bearing?"*

The answer to the second half is **no**. The load-bearing property is **monotonicity**; append-only was
one way to get it, and there is a theorem saying so (§"CALM").

**Companions.** `if-built-today-decisions.md` records what was tried and withdrawn, so this file can
state conclusions. `../if-built-today-citations.md` is the verification ledger — every citation here that
matters has been read in primary source. `../dead_ends/topological-framings.md` records three refuted
framings so there is not a fourth.

## What prompted this

Not one defect. A slew of rough edges and inelegances, of which the sharpest is measured.

**The measured one is attribution.** A great deal of *"which thing does this record belong to?"* is
currently answered by **where the record sits in the log** — which episode a heartbeat belongs to, which
claim a terminal pairs with, which stop a terminal discharges. Order lies: a worker from a dead episode
emits late, position says it is current, and `progress` jumps 0 → 500. In the corpus, **11 of 37 stops
were discharged by a record the worker did not write, 6 of them malformed.**

The fix is small and does not need anything else in this document: **put the identity in the term as an
ordinary argument.** `heartbeat(episode2, 500)`, not *"the heartbeat after the second `started`"*. Then
there is nothing to infer, because a query about episode 2 cannot match episode 1's records — the
arguments do not unify. It would work in a plain mutable database.

**And append-only is why it matters here**, which is worth saying because it is a fact about the *status
quo*, not a payoff of what follows: a store that cannot retract cannot re-attribute. A mis-aimed record
is permanent, and the only recourse is appending a correction that itself needs to be trusted.

| defect | class | fixed by identity-as-data? |
|---|---|---|
| unaimed heartbeat moving `progress` 0 → 500 | attribution | **yes** |
| a displaced worker's own terminal read as the run's verdict | attribution | **yes** |
| #39, the swallowed operator halt — discharge is author- and body-blind | attribution | **yes** |
| forged verdict silently truncating `ensure` | forgery | no |
| the claim cascade — one forgery, unbounded double-live | forgery | no |

**Forgery defects come from the absence of write authority, which no representation changes.** A forger
posting `stopped(episode2, completed)` is making a well-typed post the store accepts, exactly as
*"cooperative, no enforcement"* intends. Nothing here touches that.

**The rest of this document is a different question**, prompted by the same friction: what the whole thing
looks like if the store is designed around monotonicity rather than around a log. The attribution
measurement does not support it. Nothing here is measured against a system that exists.

## What makes the answer worth having

Three properties, stated as properties rather than as taste. Each is checkable, and each survives
translation into another language — which matters, since the point of a protocol is that somebody
reimplements it.

**Unsound things are unwritable, not forbidden.** Negation is not banned by discipline; it is absent.
*"This atom is false and nothing says otherwise"* is not against the rules — it has no syntax. The
alternative to unwritability is code review.

**The restrictions coincide.** Finite `∧` with arbitrary `∨`, monotone-iff-coordination-free, and
affirmable-in-finite-time are one fact in three vocabularies. A constraint set that three independent
routes agree on is **forced** rather than chosen, which is the strongest thing available to say about a
design decision.

**A checker can enforce it.** The discipline is a property of the language, so a tool can hold it. That
is the static-checkability principle applied to a language rather than to a field access.

## Whose this already is

Placed early so that nothing later reads as an unearned discovery. Losing novelty is welcome: a design
that turns out to be a known-good combination is better founded than one that is new.

- **A store of ground literals of both polarities, with four statuses read off membership of `ℓ` and
  `¬ℓ` — that is 4QL's Definition 5** (Małuszyński & Szałas, JANCL 21(2), 2011), and chosen there for our
  reason: OWA over CWA, *"to start with a fully monotonic query language."* Negation in rule heads is
  their told-false. Two differences: 4QL **propagates** inconsistency through derivation where here it is
  inert, and 4QL's §6 adds a stratified nonmonotonic layer that re-derives CWA, which this design
  declines. **We take its monotone core and refuse its top layer, because coordination-freeness is worth
  more here than default reasoning.**
- **A logic of arbitrary joins, finite meets and nothing else, in the information order, with polarised
  atoms — that is a fragment of Jakl's** (thesis 2018 Ch. 6; Jakl, Jung & Pultr, MFPS 32, 2016), with
  soundness and completeness proved. Polarisation is a *theorem* there: absent consistency hypotheses the
  two polarities generate independent free frames. We add `∃` and regions, which are not in it.
- **A relation whose extent is infinite, held as a finite constraint formula — that is Kanellakis, Kuper
  & Revesz** (JCSS 51(1), 1995). *"A generalized k-tuple is a quantifier-free conjunction of
  constraints"*; a generalized relation is a finite set of them. Finitely-representable-as-a-finite-union
  is theirs. What they do not have is a *second, separately asserted* extent: one formula per relation,
  complement by operator.
- **An accumulating store of positive and negative facts with a per-atom presence test over the pair
  exists in a proof** — Ameloot, Ketsman, Neven & Zinn (TODS 40(4), 2016) Prop. 4.6, with deletion set to
  `∅` *"causing nodes to only accumulate facts"*, broadcasting absences that are *"accumulated at all
  nodes."*
- **Ask/tell over a monotone store is CCP** (Saraswat, Rinard & Panangaden, POPL '91), which made the
  storage decision first — *"a simple constraint system is just an information system with the
  consistency structure removed"* — and then went the other way, making `false` the **top**, explosive
  (*"the inconsistent store can answer any ask request"*) and identified with divergence.
- **The vocabulary is Belnap's** (1977), verbatim: told true / told false / told neither / told both.

**What is not in any of them.** Falsity **asserted** as the primitive act — no paper here exhibits an
agent doing it; the storage of the result is theirs, the assertion is not. Refusing the tombstone
**chain**, so that *told-both* is representable at all. Demand-driven production with the store as the
cache. And the distribution: **no party roster and no self-identity** — parties may join with nobody told.

## The model: a store of literals, one operation

```
post(c)        -- add a literal to the store
```

Agents post. A **querier** and a **handler** are not different kinds of thing; they are agents posting
different literals.

**There are two directions and no cells.** Post `Q` to say it is true; post `¬Q` to say it is false. The
store is a growing set of ground literals in relations. `loss(60, 0.5)` and `loss(60, 0.4)` are two atoms,
both true, and nothing combines them — a reader asking `loss(60, V)` gets two answers. Everything that
looks like combination is one of two things: **set semantics** collapsing identical literals, or **one
literal refining** as a variable inside it is bound. Two posts never merge; one post gets more
instantiated.

That is what makes monotonicity free rather than argued for. Merging is declined because it
**fabricates** — `f(a,Y)` and `f(X,b)` compressing to `f(a,b)` asserts a fact nobody posted, which is
sound only under a functional dependency (§"No functional dependency"). The cost of the free completion
over greedy merging is priced in `../dead_ends/`: first-fit is graph colouring, measured at 1.04–1.32×
optimum, with a real separation reachable at logarithmic arity.

**An unsatisfied existential is demand.** Posting `loss(60, V)` with `V` fresh asserts *"there is a loss
at step 60, and it is V"* — since `V` is unconstrained, it asserts only **existence**, and making an
unsatisfied existential true is exactly production. Three concepts fuse:

| was | is |
|---|---|
| the `?` sentinel | an unbound variable |
| *no information about this value yet* | an unbound variable |
| "this is demanded" | an existential not yet satisfied |

(An unbound variable is least in the **instantiation** order on a value; `∅` is least in the **status**
order on an atom. Nothing relates them, and one symbol for both invites reading a demand as a falsity.)

So there is no `demand` predicate at the surface, no `while` combinator, and no watcher concept. A watch
is a posted term with a free variable; bindings arrive as they are learned. The reading is `∃` because the
pattern is **bare**, not because demands are existential — see §"Demand is control".

**Answers stream individually.** A querier posts a pattern and receives matching literals one at a time;
there is no answer *object* anywhere. What ends a stream is the post `¬Q`, delivered like any other fact.
*Completeness* is not posted at all; it is read off the statuses.

Packaging answers into a growing term is a tempting dead end. Nothing bindable is unordered: a set term is
ground, so adding to it is a different term, which leaves a **list** — and a list fixes an insertion
order, so two nodes learning the same answers in different orders build `[a,b|T]` and `[b,a|T']`, which
unification cannot reconcile because it cannot reorder a spine. The divergence is representational, so
redelivering every message does not repair it. A shared tail is also a lossy CAS: once one producer binds
`T = []`, another's next answer is rejected. One tail *per producer* fixes both — and that is the right
**structure** in the wrong **representation**, since what it encodes is one termination marker per
producer per stream, which is precisely `¬Q`, carrying no binding and readable by anyone. The dead end
kills the term, not the idea.

**Terms, not blobs**, because the engine must traverse a term to match a pattern. That is the whole
argument and it is **not** an indexing one: measured on 200k rows, a JSONB key with a btree expression
index runs the central range query in **0.085 ms** against a positional term layout's **0.089 ms** — the
term buys nothing, the btree does. Worse, positional indexing over *heterogeneous* terms is not merely
slow but wrong: with `loss(Config,Step)`, `grad(Config,Layer,Step)` and `ckpt(Run,Config,Shard,Step)` the
step axis sits at three different positions, and an axis-blind positional range returned **132,879 rows
against a correct 91,500**.

## The language, and what each restriction buys

The derivation language is **definite clauses** — one atomic head, a body of finite `∧`, `∨` and `∃`, over
a **polarised signature**, with constraints from a fixed CLP domain as ordinary body literals. **No `¬`,
no `→`, no `∀`** as operations; and no `∨`, no `⊥`, no equality and no `∃` in a *head*.

**There is no `¬` in the language at all.** `¬Q` is a **polarised literal** — the mark is part of the
relation's name — and what the logic sees is an ordinary positive one. Nothing is excepted, because
nothing negates.

**What is missing, and why — the reasons are two, not four.**

| a richer fragment has | here | why |
|---|---|---|
| arbitrary `∨`, `∃`, finite `∧` **in bodies**; clause-level `∀` | **yes** | — |
| arbitrary `∨` **in heads** | **no** | a disjunctive fact has nowhere to live: this store is a **set of literals**, one model. `φ ⊢ a ∨ b` needs a set of *models*, or the disjunctive chase |
| `⊥` in heads — integrity constraints | **no** | a store that must accept what it is given can only **reject a post** (order-dependent) or **go inconsistent** (§"No functional dependency") |
| equality in heads | **no** | that *is* a functional dependency — the same reason again |
| `∃` in heads — value invention | **no** | the coordination-freeness proof's quiescence argument **requires** no value invention. An existential *post* is fine; a rule minting a fresh variable is not |

So three omissions are one reason — **the store must accept what it is given** — and the fourth is CALM's.
None is taste.

**Which places the language two rungs below "geometric", and lower is the point.** Lacking `⊥` in heads
there are no goal clauses, so this is **definite**, strictly below Horn in the logic-programming sense;
and `∨`-in-heads is what separates geometric from coherent. Being low is what buys the tractable corner
(monotone queries answerable directly, in PTIME), the placement strictly inside the coordination-free
class, and the no-value-invention property the proof depends on.

⚠️ **Terminology**: *Horn* means "at most one positive literal" in logic programming — admitting goal
clauses — and in the categorical hierarchy means formulas from `⊤`, atoms and finite `∧`, which already
excludes `⊥` and `∨`. Say which is meant.

The shape has a reason rather than an axiom (Vickers, *Topology via Logic*; the affirmability reading is
Smyth's): **an open set is an affirmable property** — confirmable in finite time from finite information,
never refutable from it. You may conjoin *finitely many* observations, because each takes finite time; you
may disjoin *arbitrarily many*, because any one suffices.

**Why `¬` had to go.** To affirm `φ` you need a finite observation. To affirm `¬φ` you must rule out
*ever* affirming `φ`, which is a survey of everything there is. That survey **is** the coordination round
CALM prices. So "no negation," "opens are affirmable," and "monotone ⟺ coordination-free" are one fact in
three vocabularies.

**The derivation/report split is the open/closed split.** Derivation affirms; reporting refutes. They
cannot mix, for the same reason the complement of an open is not open.

| | negation? | may feed demand? |
|---|---|---|
| **derivation** — what to produce | no | yes |
| **reporting** — what is missing, what is best, what diverged | **yes, inherently** | **no** |

`argmax` is therefore not expressible in derivation, so a bandit's one non-monotone step is forced to the
boundary. Its monotone half stays inside: `beaten(A) :- value(A,V), value(A2,V2), V2 > V` only ever grows.

**One exception is real and does not repair.** `ensure`, the library's core operation: its loop condition
is a threshold claim on `progress`, a *retractable* quantity, and its two termination guards are a
**temporal delta** (*"nothing new was derived"*, which has no positive form) and an **inflationary
fixpoint test** (*"another lap can only reproduce them"*). Neither is geometric, and both feed demand,
because both decide whether to relaunch. §"Two layers" is where that belongs.

### The threshold rule, and its six instances

> **Threshold claims are always available. Exact claims require settledness.**

This is a corollary of the affirmability shape, not an axiom — it states *"only opens are affirmable"* in
the form a rule author needs. What earns it a section is that it keeps instantiating. Six orders now, in
every case the up-set affirmable and its complement not:

| order | affirmable | never |
|---|---|---|
| **value** | `V ⊒ t` | `V = t` on an unsettled term |
| **instantiation** | `nonvar`, `ground` | `var` |
| **status** | `⊒ {f}` | `= {f}` |
| **coverage** | entailment | membership in the solution set |
| **constraint** | *"the store entails `S ≤ 100`"* | *"5 is still possible"* |
| **demand** | *"this was asked"* | *"this was not asked"* |

A rule that must be re-derived at each new carrier is a slogan; one that already holds there is a basis
vector.

**Exact equality is not lost, it is a threshold at the right place.** For a ground `a`, `↑a = {a}`, so
`V ⊒ a` *is* `V = a`. What is inexpressible is equality against a **non-ground** term — *"V is exactly
this partial term and no more instantiated"* — which requires ruling out further instantiation, i.e.
negation. Exactness is available precisely where it is meaningful: at maximal elements.

**Matching a non-ground pattern is a threshold claim** — the ordinary case. A body literal
`loss(60, f(X))` with `X` free claims *"the value is known to be an `f`-term."* It suspends until that
threshold is reached, then binds `X`; once reached it stays reached and `X` can only refine. On an
**algebraic** domain the compact elements are the finite partial terms and the sets `↑p` form a basis of
the Scott topology — verified exhaustively for terms, `Flat`, `Set` and products — so *matching against a
pattern is exactly asking a basic open*, and the restriction is a **basis**, not a limit.

The distinction to keep is **matching versus unification**: matching is one-way (the pattern's variables
bind, the term's do not) and is a *read*; unification is two-way and is a *post*. Rule bodies match;
agents post. That recovers `ask` and `tell` as a property of *position*.

**And it is not `var/1` in disguise.** Instantiation tests are the classic non-logical family, and
`V ⊒ f(⊥)` *is* asking how far a term is instantiated — but that family splits along the line already
drawn: `nonvar` and `ground` are **monotone**, `var` is **antitone**.

> You may test that instantiation has **reached** a threshold. You may never test that it has not.

It also means a rule whose body fails to match must simply *not fire* — never take an else-branch, and
with no negation there is none to take. Failure of a match is unobservable, so the order in which matches
succeed cannot be detected: derivation *times* differ, the answer set does not.

**The same holds at the status layer, and there it is enforced rather than observed.** You may test that
an atom has been told false; you may never test that it has *only* been told false — and you cannot,
because `= {f}` needs the conjunct *"and nothing told true"*, which is `¬∃` and has no syntax. `= {f}`
becomes writable in a **report**, and nowhere else.

**Which is why reading `⊒{f}` in a rule body costs nothing.** It is an ordinary positive literal,
affirmable by exhibiting a record. And a rule reading `⊒{f}` fires identically on `{f}` and `{t,f}`, so
the climb between them is **unobservable to derivation** — conflict is contained not because rules cannot
see negative records, but because the only thing they can see about one is upward-closed.

**And a conflicted premise does not explode — for free rather than cheaply.** Paraconsistent logics
normally *pay*, weakening entailment so a contradiction stops implying everything. Nothing is weakened
here: `Q` and `¬Q` are two relations that **no axiom connects**, so there is nothing to derive a
contradiction *from*. *Ex falso* is unformulable rather than blocked — it needs a schema `⊥ ⊢ B` for
arbitrary `B`, and a definite clause's head is always a specific atom. What a conflicted premise does is
derive an ordinary atom, which may then disagree with another at the head relation; that is the free
completion one level down, and where a **declared domain conflict** catches what the status layer cannot.

> **We did not buy paraconsistency. We never bought consistency.**

**Settledness is groundness for a *value*, and something else for a *query*.** A value is settled when its
term is ground. A query has no answer term to ground, so its settledness splits:

| | what it means | where it lives |
|---|---|---|
| **knowledge** complete for `Q` | every atom of `ground(Q)` has status `⊒ {t}` or `⊒ {f}` | affirmable by exhibiting a **finite cover** of `Q` by *deciding* records — **derivable**, for a `Q` whose extent is fixed |
| **stream** complete for `Q` | no further answer will arrive, from anybody | **exhaustion**, hence control |

Only the first is a question the logic can answer, and it needs no exhaustion: a region whose every atom
has been determined is settled whether or not anybody is still working on it.

**`ground(Q)` need not be finite; the *cover* must be.** The conjunction runs over the records exhibited,
not over the atoms they cover, so a single `¬Q` settles an infinite region in one observation — which is
why the quantified form exists at all. **But covering is not deciding.** A record decides only what it
speaks about universally: `¬Q` decides every atom in `Q`; a *ground* positive literal decides one; a
**partially instantiated positive post decides nothing**, since `loss(3,V)` with `V` free is existential
and covers the region while leaving every atom in it at `∅`.

**And `Q`'s extent must be fixed**, or settledness is not a monotone read: for a hole-bearing region the
*entailed* extent grows as the hole narrows, and settledness is antitone in the extent, so pure
information gain can flip it true → false. Nothing descends in the store; the *question* got bigger.

**There is no producer verb and no `freeze`** — `¬Q` is an ordinary post — so there is no
freeze-after-write race. And **nothing may derive settledness from demand going quiet**: demand
disappearing determines nothing, so it moves no atom out of `∅`. There is no ownership rule to enforce
that; a reclaimer posting `¬Q` is making a false claim exactly as it would by posting a false
`loss(60, 0.5)`, under the same enforcement, which is none.

## Falsity is told, never inferred

**Inferred absence needs no representation at all.** *"Nothing is there yet, so produce it"* looks like it
needs `¬∃` — and under an **open** world that is not merely banned but unknowable, since you can never say
*no answer exists*, only *no answer has arrived*. Nothing needs it, because production is
**demand-gated**: a producer runs because somebody asked, never because something was found missing.

And demand-gating needs no machinery either. **A demand is a posted pattern; a producer is an agent that
watches for patterns it can serve, computes, and posts literals that unify with them.** There is no
`demand_p` relation in any rule body that *gates production* — writing one as
`value(X,V) :- demand(X), handler(X,V)` smuggles a key/value split back in through the adornment, and this
design has no cells. (§"Demand is control" internalises *"this was wanted"* as a queryable fact, which is
a different object and gates nothing.) A producer needing something of its own posts a pattern too, which
makes it a querier; the roles stay symmetric all the way down.

**What *is* representable is told falsity** — somebody posting `¬Q` because they know it. That is an
ordinary fact, affirmable by exhibiting it, and it needs no closed world to license it. The distinction is
the whole of the negative story: **falsity you inferred from silence is unaffirmable and stays so; falsity
somebody posted is data.**

### The rule for posting, in both directions

> **Post what you know.** Nothing retracts, so a post you are not sure of is a defect — in either
> direction, for the same reason, with no vocabulary of its own.

That is not a rule about negation; it is the ordinary honesty condition on any post. A producer that stops
looking and posts `¬Q` is posting what it does not know, which is exactly what posting an uncomputed
`loss(60, 0.5)` is.

| act | means |
|---|---|
| **post `Q`** | I know it is true |
| **post `¬Q`** | I know it is false |
| *(say nothing)* | I do not know — *including "I am not looking"* |

The third row is not a stance anybody posts: it is `∅`, the identity of the aggregation. Making it
**unpostable** is the structural half of the fix; the other half is that the mistake stops being silent —
a production inside a region somebody declared empty climbs the status to `{t,f}`.

**`¬Q` is the quantified form of `never`.** `never(a)` says nothing goes at one atom; `¬Q` says nothing
goes anywhere in `ground(Q)`. Two forms exist only because a region can be **infinite**: a producer that
converged at step 400 is asserting `never` about infinitely many atoms, and a quantified statement is the
only finite way to say it.

**No author.** `p` and `q` posting the same negative region is **one** literal posted twice, not two, and
set semantics collapses it correctly. What an author argument would buy is **provenance** — wanted equally
for positive facts, since `loss(60,0.5)` names nobody either — so it belongs to whatever mechanism
eventually serves both. It is an open item, and the cost of not having it is at the end of this section.

**The region to post is not the one you were asked for.** Answering `Q₀`, a producer must post
`¬(Q₀ ∖ everything in Q₀ it will ever post positively)`, or it contradicts its own output. That region is
a **residual**, and it is **forward-looking**, which is what makes *"post at convergence or exit"* a
consequence rather than a rule of thumb: you cannot compute *what I will ever produce* until you are done.

### How a party comes to know a negative fact

`¬Q` is the primitive. Coming to know one is a **list**, and this is where a closed-world argument
legitimately returns — as a *route*, not as the semantics.

- **A converged producer.** The run ended at 400, so there is no loss at 500. It knows because it ran.
- **A solver.** A propagator that has proved a region has no solutions may post `¬Q`; one that has not is
  posting what it does not know. Not a solver-specific rule — the general one with *know* instantiated.
  Unsatisfiability needs no vocabulary of its own: it is **grounds for a post**, not a third kind of
  absence.
- **A closed producer set.** Everyone who could produce here is done, and no more will appear. This one
  **costs a query about network membership**, hence a coordination round, priced exactly by the CALM
  results — and it is the one that needs arguing, because §"CALM" is about not needing such queries.

**The argument for the third, since the case against it is real.** The danger is not the reasoning but the
*name*: give exhaustion a predicate in the derivation layer and somebody will quantify over it — *"every
producer for `Q` is done, and no atom of `Q` is `{t}`, therefore…"* — which is the unanimity move, needing
to know who all the producers are. The route survives only if the quantification happens **below** the
logic and only the `¬Q` crosses up (§"Two layers"), so that **no predicate exists for a rule to quantify
over**. Then: coordination is paid **once**, at the point of conclusion, **scoped per functor**, and what
comes out is an ordinary monotone fact that everything downstream reads coordination-free.

It is NAF-like in effect and positive in form — every premise is a posted fact, and the non-monotonicity
concentrates in one attributable claim. Two things it owes, both reopened by admitting it: **who may
declare a set closed** is a write-authority question, unenforceable like every other; and the measured
stake is that a wrong closure produces a false `¬Q` **systematically** rather than by carelessness.

This is the design's characteristic manoeuvre, and naming it once saves three sections from re-deriving
it:

> **Make the assumption explicit, post it, scope it, pay for it once.**

Four instances, arrived at separately: this route; **signature agreement**; **sort closure**
(§"Types"); and the original global closed-world toggle — which was this move at the wrong granularity,
which is why it failed rather than why the move is wrong.

### The obligation, and the one thing it cannot buy

**It is not enforced and it is not enforceable.** Producing inside a region you declared empty contradicts
your own assertion — and needs no detector, because it *is* the status `{t,f}`. Nothing rejects the post,
and nothing could: refusing a contradicting post is order-dependent, whoever arrives first winning, which
is §"No functional dependency"'s argument against asserting functionality as an axiom.

**But detectable is not diagnosable, and that is where the missing provenance is felt.** `{t,f}` says two
claims disagree. It does not say whether that is a real disagreement about the world — two producers with
genuinely different results — or one producer that over-claimed the extent of its own `¬Q`. Those want
opposite responses.

**Two refuted framings, recorded so they are not rediscovered.** Closure over a *fixed extent* — everything
`p` was ever responsible for — asserts refusal over regions it may still be asked about, because under
demand-gating a producer emits only what was asked. And *"a producer that claims the whole axis and emits
half of it is a forgery"* indicts every honest producer, for the same reason; the defect was never
dishonesty, it was claiming more than you know.

### Not CWA, and not local CWA

The closed-world assumption is an assumption *about absence* — Reiter's completeness axiom, whose whole
content is that an atom not in the store is false — and nothing here applies it.

Nor is `¬Q` a **local** closed-world statement (Levy 1996; Denecker, Cortés-Calabuig, Bruynooghe & Arieli,
TODS 35(3), 2010). An LCWA narrows the axiom to a described region, and falsity is still read off
**emptiness** — the operator computing it fires on *"not in the database"*. Its meaning is therefore a
function of the store, which those authors say is inescapable, and from which they conclude, in their
words, that *"a local closed world assumption is a **nonmonotonic construct**."*

**The difference is in the dynamics, not the sentences.** At a store whose `Q`-region is empty,
`LCWA(P, Q)` entails exactly `∀x̄. Q[x̄] → ¬P(x̄)` — which *is* `¬Q`. So this design does not out-express
LCW; it **promotes LCW's conclusion to a primitive**, cut loose from the store it was computed against,
and that promotion is what buys monotonicity. What happens on growth is the whole of the difference: post
an atom inside a region declared complete and **nothing is retracted, nothing is violated, and no
inconsistency arises** — their Prop. 4 makes a locally closed database *always* consistent — the negative
conclusion simply **stops being entailed**, silently, with no event to observe. `¬Q` behaves oppositely:
the record stands and the region climbs to `{t,f}`.

What the promotion costs: *"complete here, and here is what is in it"* is no longer one record — over a
non-empty region the exceptions must go into the region description.

**One thing they reach for and do not build is this design's record.** Materialising a told-false extent
is unsafe in their setting, and their proposed remedy is verbatim: *"symbolic query answering methods that
return **queries with constrained variables** … techniques from **constructive negation** in logic
programming could be useful."*

## An atom's status

The rules above are easier to justify than to state, and the justification is a semantic model rather than
a second syntax. The language remains what you *write*; this is what the writing *means*.

**The logic is two-valued and positive; the four values below are a *reading*.** A rule asks only whether
a literal is derivable, and that question is two-valued — the two values being **not affirmed <
affirmed** rather than false < true. That order is informational, and the distinction is the whole
open-world story: if the logic's bottom meant *false*, a negative post would have nothing to do. Measured:
the monotone predicates on the four values are **exactly** the six a rule can express over the two
polarities with `∧` and `∨` and no negation — six of sixteen, and the same six.

**An atom's status is the set of things producers have told you about it**: `∅`, `{t}`, `{f}`, `{t,f}` —
the subsets of `{t, f}`, and Belnap's four, using his words. **The aggregation is union**, so a status
only ever climbs — monotone **by construction**, and in a *growing* producer set rather than only a fixed
one.

**It is the free completion, one layer up.** §"No functional dependency" meets the same fork at the value
layer — add a collapsing top, or take the powerset — and takes the powerset. The status layer gets the
identical answer.

**A status is computed, never stored.** The store holds records over *regions* — one `¬Q` speaks for every
atom of `ground(Q)` — so an atom's status is whatever the covering records say, joined. That puts weight
on *covering*, and there is one safe reading:

> **Coverage is an entailment claim, never a membership claim on the solution set.**

A region may itself have a hole, and read generously such a claim covers every atom `Q` *might* contain,
so binding **shrinks** it. Measured: the generous reading descends on **12 of 12** bindings, the entailed
reading **0**. Entailed coverage grows under binding *and* under narrowing. The rest of the model holds as
assumed: over 25 stores, statuses are order-independent (0 disagreements in 1,500 arrival shuffles) and
monotone (0 descents in 7,800 arrivals).

**Deciding coverage runs through a deliberately incomplete checker**, so an agent that has not propagated
far enough reports `∅` where a better-propagated one reports `{f}`. Measured sound: the under-propagated
status sits **below** the complete one in 144 of 144 cases, never above. Incompleteness costs knowledge,
never soundness — the price is that a status is **relative to the propagation done**, hence local.

**What is readable is the up-sets, and there are exactly four.** `⊒ {t}`, `⊒ {f}`, `⊒ {t,f}` (*disputed*),
or their union. Each is affirmed by exhibiting a witness.

**`∅` is the one thing not readable**, and it fails twice. *"Nothing has been told about this"* is a
down-set, so no finite observation affirms it — the open-world assumption recovered as a fact about the
codomain rather than stipulated. And CALM says it more sharply: `⊒{t}` and `⊒{f}` are each a union over
parties and therefore monotone, where `∅` is the **joint negation of two growing extents** and can flip,
so it has **no coordination-free implementation**. Not merely unaffirmable — unanswerable without a
coordination round. Same for *"and nobody disputes it"*.

**The constraint binds on output, not computation.** A scheduler may consult the `∅`-region mid-flight
freely; publishing such a reading as an answer is what leaves the monotone class. The reference model
makes exactly that split — output append-only by hypothesis, working memory admitting deletion.

**A demand is an open set, and an answer is truth restricted to it.** Not a *total* valuation: only the
demand's true-set is ever used, and the store can affirm *asked* but never *not asked*. With status held
as a **pair of extents**, restriction **is** conjunction applied to both, and the coercion that made
masking look one operation from a bug is not writable.

## The residual, assembled

The production path is the design's most load-bearing multi-hop argument, and it is easier to get wrong in
pieces than whole.

Production is gated on demand, and before running a producer the evaluator asks *"do I already have an
answer?"* That memo check **is the cache** — without it the producer re-runs on every call, and a run here
is a six-hour job. It is semantics rather than housekeeping: where re-production is not bit-identical,
removing it changes the answer set outright.

**But it is not a memo *table*, and presence is checked per atom** — a lookup in the index, not a
hit-or-miss verdict on a whole call. Two problems that look serious under the call-keyed reading
evaporate: keyed on **answers**, one answer arriving for `loss(S,V)` would make the whole range look
served, where per atom the producer walks its extent and carries on; keyed on **calls**, two demands that
overlap without either containing the other each miss and each run in full, where per atom the second runs
only on the difference.

**So what the producer is handed is a residual, not a verdict**: the call minus everything already settled
either way — the **`∅`-region** of `Q`. Subtracting the `{f}` part matters as much as the `{t}` part: an
atom somebody determined absent is one nobody should be asked to produce.

**And here the four hops meet.** The `∅`-region is exactly the thing that is *not readable* — down-set,
and unanswerable without coordination. So the residual is **not a derivation**. It survives because it is
never published: it is computed **locally**, **best-effort**, and consumed as a **scheduling decision**,
which is control (§"Demand is control"). It need not be materialised; walking the extent and skipping
settled atoms computes it incrementally, and `E` need not cross the link, since the handler is near the
data.

**That removes a mechanism rather than adding one.** A demand entirely covered by another has an **empty
residual** and costs nothing, without anybody detecting that it was subsumed — so containment compaction
is unnecessary. What survives is the concurrent case: two producers serving overlapping demands can each
produce before either sees the other's output. That is **single-spawn**, priced in §Open.

One representation note. Per-atom is the *semantics*; over a wide axis, *"what is settled"* is naturally
stored compressed — intervals, runs — so computing a residual does not mean walking a billion steps.
Meaning per atom, storage as dense as it likes.

## CALM: why monotonicity, and not merely for tidiness

**Consistency As Logical Monotonicity** (Hellerstein 2010; proved by Ameloot, Neven & Van den Bussche,
PODS 2011 / JACM 2013, **Cor. 13**): *a query has a coordination-free distributed implementation **iff**
it is monotone.*

That converts a preference into a requirement. If a querier is far from the data, then **monotone** ⟹
information flows one way, no ownership protocol, no consensus; **non-monotone** ⟹ you must know you have
seen everything, which is what coordination is for.

**Four scoping notes, three of them repairing a natural over-reading.**

**It is relative to a model, and the model is the one where nobody knows the distribution.** Cor. 13 holds
where the partition is arbitrary and *unknown to the program*, with asynchronous fair runs and
**non-retractable output** — the last being a hypothesis of the model, which an append-only store happens
to satisfy. Give the nodes knowledge of the partitioning policy and the class grows; give them the global
active domain and **every computable query is coordination-free**. So the `iff` is a statement about
ignorance, not about queries alone.

**"Coordination-free" is weaker than it sounds.** The definition is *existential over placements* — for
every input there **exists** some partition on which the computation quiesces with no messages — not a
promise that a real run sends none. The authors warn against that reading and exhibit a coordination-free
transducer that communicates on the obvious placement. So *"no round trips"* is not licensed, and neither
is pricing coordination in rounds: the predicate is binary.

**The theorem constrains the computed query, never the operators.** Every query distributedly computable
by a first-order transducer is computable by one using negation internally, and the proof of the monotone
case reads *"we use deletion to start afresh; since the query is monotone, no incorrect tuples are
output."* So the residual computed **inside** an agent is not a violation; **outputting** one would be.

**And their emptiness query is this design's `¬∃`, worked.** It is their exhibited non-coordination-free
construct: since every node may hold part of the input, the nodes must flood identifiers and check against
the membership relation. *"Is there no atom at step 60?"* is that query over a region — which is why
`= {f}` has no syntax here, and why the design's answer is the one the theorem licenses rather than a
stipulation.

**One property worth claiming, which the theorem licenses.** Monotone ⟺ computable without the membership
relation **and** without self-identity. So this design needs **no party roster and no self-identity**:
parties may join with nobody told.

Two further scoping notes. It is a **safety** statement: a lost demand and a slow handler leave a querier
in byte-identical states, so *liveness* still needs an acknowledgement, and a bounded reconnect still
needs a cursor. And it applies to the **answer** relation; demand is control.

## Two layers, and what crosses between them

Not everything can be monotone, and nothing is gained by pretending. The shape that works is **a
non-monotone core underneath a monotone layer**, where each pushes as much as it can downward:

- the **core** makes observations the logic cannot: an OS probe, a clock, a temporal delta, a fixpoint
  test, and the closed-producer-set conclusion of §"Falsity is told";
- the **monotone layer** derives over them and owns everything it can.

**The boundary is not a convenience — it coincides with the CALM boundary.** Everything above is monotone
and coordination-free; everything pushed below is exactly what is not. Three constructs have now tried to
get in and been reclassified rather than accommodated, and the layer needed no change in any case. The
reference model draws the same line: output append-only by hypothesis, working memory admitting deletion.

**What crosses is literals**, and the mechanism is already in this repo — `prolog-query-layer.md` says of
the one existing case, *"pass the probe result in as a parameter rather than calling out."* The
generalisation is the repo's recurring repair applied to oracles: **timestamp the observation and make it
a fact about the past.** *"Nothing changed between T₁ and T₂"* is permanently true once observed, where
*"nothing has changed"* never is.

Two consequences. The core should be as small as possible, and every primitive it hands upward is one the
monotone layer gets to reason about. And because the timestamps are **per-observer** — clock skew already
breaks `last_activity` in the measurements — the literals must carry *who observed, by whose clock*, and
any comparison across observers is a report rather than a derivation.

CCP (`ask`/`tell` over a monotone store) is the right model for the *semantics*; CHR is the right model
for the *execution*, where simplification keeps the store small provided the body entails the head. One
caution: CHR's store is a multiset, and idempotence is what makes one-way replication safe — use set
semantics.

## Demand is control

Demand is not monotone: a lease expires, a querier withdraws, an operator halts a run. That is fine.

**Nothing derived becomes false; some things never get derived.** A withdrawn demand means a term is not
produced, so a reader's threshold claim never fires — it suspends forever. That is a **liveness** failure,
not a safety one. Two replicas with different demand produce different *subsets*; every literal either
holds is correct.

All three mechanisms are control: resource management, a querier changing its mind, and somebody
deliberately stopping a machine. The logic never had jurisdiction over any of them.

### The quantifier is a property of the posting

> **A bare pattern is `∃`** — one production satisfies it. **A `∀` demand must be *finitely coverable***.

Both ship. A subscription with no repeat is one-shot: it fires once and the worker posts the expiry
counter-record. With a schedule and a bound it is **one durable record** denoting a region and demanding
every atom in it — durable across the *producer's* death, because a worker re-drains the control log and
re-registers whatever is still unanswered, pinned by
`tests/test_run_episodes.py::test_relaunch_extends_one_series`: one subscribe posted *before episode 1
exists*, two episodes, ten steps, one series.

**Finitely coverable, not bounded.** Boundedness is admission control wearing a quantifier's clothes, and
it forbids the demand this design is best at expressing: *"every step, run to convergence, and tell me
when there are no more."* That demand is unbounded and perfectly dischargeable, because `ground(Q)` need
not be finite provided the cover is — a positive prefix to step 400 plus one `¬(S > 400)`.

**Open: who checks it.** Whether a finite cover will *ever* exist depends on a producer's future, so it
may not be checkable at post time at all. §"What is checked" assigns owners to the other obligations and
cannot yet assign one to this.

### Facts and demands are dual, and the duality is exact

**The points are ground atoms**, and this is forced rather than chosen: a converged producer must post
`¬(Q₀ ∖ E)`, and a residual is **not upward-closed**, so under a partial-terms reading the negative post's
region is not Scott-open and therefore not a region at all.

**Say the consequence out loud: on that space the topology does no work.** Ground atoms are maximal, so
the space is **discrete** and its frame is the complete **Boolean** powerset — measured three times
independently. Every open is affirmable, nothing is forbidden, and any structure defined by which sets are
open is inert here. What survives is not topology but the **basis**, which frames deliberately forget.
`../dead_ends/topological-framings.md` records the three attempts that foundered on this. Two carve-outs:
**continuous value carriers** are the one place the space is genuinely not discrete — though IEEE floats
are a finite set, so treating a value axis as dense is a modelling choice — and the **instantiation** order
on partial terms is real and used throughout, simply not the order whose points these are.

With that stated, the duality is worth having:

- A **fact** is a ground term: a maximal element, a **point**.
- A **demand** is a pattern, and a pattern denotes `↑p`. Finite partial terms are exactly the **compact**
  elements, so a demand *is* a basic open — with two exceptions this document names: on a **dense
  carrier** the only compact element is `⊥`, and a **disequality** denotes no open, since `X ≠ Y` is not
  upward-closed.
- Satisfaction is `x ∈ U` — the pairing between a space and its frame.

**A pattern and its grounding are interchangeable**, which is what makes regions work: the maximal
elements of `↑p` are exactly `p`'s ground instances, and over a signature rich enough that every partial
term has ground instances, distinct opens have distinct groundings. So **subsumption of patterns is
inclusion of groundings**, which is the one-line reason a negative claim transfers to every subsumed
question.

**And the quantifier lives in the posting, never in the term.** A term with holes uniformly denotes a set;
what you do with the set is decided by how it is posted. `¬Q` reads *universally* over `Q`'s grounding; a
posted answer reads *existentially*; a posted demand asks for the members — *one* of them if the pattern is
bare, *all* of them if it is posted `∀`. One representation, four roles, no modality — which is why a
**partially instantiated answer** needs no special case: it is an answer about what is known and a
question about what is not, simultaneously.

That is Stone duality, and the proof-theoretic sense of polarity is apt too: **a fact is data where a
demand is a continuation**. But polarity should not be asked to carry more — it explains neither
restriction the design leans on, since focusing makes `∧⁺` positive with no cardinality condition. The
asymmetry between finite `∧` and arbitrary `∨` is **left-exactness**, a different fact wearing the same
word.

### Demand subsumption

**Everything here is about `∀` demands.** For a bare `∃` pattern the contravariance below **inverts** —
the *specific* demand subsumes, because a smaller open is harder to inhabit — and the anti-unification
hazard goes with it, since generalising an `∃` demand *reduces* work.

**Subsumption runs the other way, because `↑` is order-reversing.** For facts, more instantiated is
higher. For demands, `p ⊑ q` gives `↑p ⊇ ↑q`, so the **more general** demand subsumes the specific one:
having posted `loss(V,S)`, a standing `loss(V,12)` is redundant for production.

But go no further. **Anti-unification is the join in the demand order and it over-approximates**:
generalising `loss(V,12)` and `loss(V,13)` yields `loss(V,S)`, which demands *every* loss. On the fact
side the join is safe because it adds information; on the demand side it adds *work*, and here a unit of
work is a six-hour job. One caveat on the compression: **derive the work set, never destructively shrink
it.** A demand, once recorded, is never withdrawn — but a *subscription* can end, and recomputing the work
set from the surviving subscriptions handles that for free.

**What to internalise, and what not.**

| | monotone? | where |
|---|---|---|
| the **demand relation** — *"this was wanted"* | yes, accumulates | internalised, a fact about an `Open` |
| the **live subscription** — *"someone is listening now"* | no, revocable | control, outside the store |

Production is triggered by the subscription; the internalised demand is a durable record of *"what has
ever been asked."* (`Open` here is an opaque sort with no `denote`, so no reflection is bought.)

**It does not carry a dependency graph, and the logical layer needs none.** When a handler serving
`report` posts a pattern for `loss`, the store cannot tell that from an unrelated querier — symmetric
roles means indistinguishable, and indistinguishable means no edge.

| candidate use | why the logic does not need the edge |
|---|---|
| **admission control** | reads *what is demanded now*, already available. A graph adds *prediction*, a convenience |
| **cancellation** | when a handler's own demand goes away it stops, and stopping drops its sub-demands; the cascade is agent-local |
| **provenance** — *"why is this job running?"* | a log question, answerable from agent-local traces |
| **cycle detection** | without it you hang, which finiteness already makes the requester's problem |
| **taint** — *"which conclusions rest on a premise that has since become `{t,f}`?"* | agent-local again: the agent that fired the rule read the disputed premise and can record that it did |
| **reclamation reachability** | this one **does** need a graph — and it is the reclamation layer's, not the logic's |

**The build-system analogy needs care.** Shake, Bazel and Nix use their graph for **invalidation**, and
with no retraction there is nothing to invalidate — but two things escape that. A premise can climb
`{f} → {t,f}`, which retracts nothing and still leaves a conclusion on disputed ground. And **Nix keeps
its graph despite never invalidating anything**, because deletion needs **reachability**.

If a graph is ever wanted, the cheap form is **structural rather than instance-level**: a handler declares
`needs(report, loss)` once, alongside its sorts. Posting `needs` at runtime is legal, with one collision:
if the producer has already posted `¬Q` and the new capability falls inside `Q`, producing there
contradicts its own claim — caught as `{t,f}` like any other valuation conflict.

Concretely a subscription is a **cursor into a per-functor term index** — walk the trie with your pattern,
get notified when new leaves appear beneath it. **This is one object with four names in earlier drafts**:
the call table, the registry, the work set and the cursor are the set of live demands, keyed by skeleton,
indexed by pattern. The division: *the call table decides who is told; the store decides what is
computed.* It does not remove the need for it — two queriers independently posting `loss(60,V)` hold
distinct terms with distinct tails, so one producer answer does not satisfy both.

**There is no `read`, and no syntactic substitute.** A tempting test — *"does the posted term have a free
variable in key position?"* — cannot carry the distinction. It is not invariant under rewriting
(`loss(S,V) ∧ S=60` classifies opposite to the identical `loss(60,V)`), and it presupposes a key/value
split no term carries: `verdict(Outcome, FinalStep)` has two value positions, `provenance(key, Prid, Sha)`
has two key positions. What two verbs were buying is **a declaration of intent that survives rewriting**,
which a syntactic property cannot replace. Consequently **admission control needs an explicit mechanism**,
and the natural signal is a *quantity*: how many atoms does this posted term denote?

### The demand language: constraints from a fixed domain

Talking *about* demands means handling the holes in a pattern, and there are exactly three ways —
**erase** them into a finite tag (adornments, fixed at compile time), **delegate** them to the
metalanguage (exponentials), or **represent** them as a term plus a `denote` relation (quotation). The
axis underneath is *when the demand vocabulary is fixed*.

**Take the middle course between erasing and representing: CLP.** A demand carries constraints from a
**fixed domain**, with a solver deciding satisfiability. `loss(V,S), S ≤ 100` has shape *"range
constraint"* and data `100`. The constraint is an ordinary positive literal in the body, so the logic is
untouched and stays first-order and decidable. Adornments are the degenerate case where the domain is bare
equality.

**Propagate, never label.** A propagator narrows domains by local reasoning; when narrowing cannot decide,
the usual fallback is *search*. Don't. Answer **undecided** and let the caller suspend. That removes
hypotheticals reaching the store (no labelling, so nothing conditional is ever asserted) and the solver
isolation problem (nothing to isolate — and measurement shows the obvious boundary does not work anyway,
since copying a term copies its suspended goals).

**It does not by itself delete the budget.** Measured in `clpfd` with no labelling anywhere,
`X in 1..N, Y in 1..N, X #> Y, Y #> X` — two variables, two constraints, obviously unsatisfiable — costs
**989 inferences at N=10 and 4,550,534 at N=10⁵**, because bounds propagation raises each bound by one per
step. On a half-bounded step axis the ascent does not terminate at all. **But that is an algorithm
mismatch, not a property of the problem**: `X > Y > X` is a system of **difference constraints**, decided
by negative-cycle detection in `O(V·E)` independent of magnitude. A propagator-producer may run
Bellman–Ford, exactly as any sound backend is allowed.

What it costs is **incompleteness**: entailments a search would have found go unreported, so a demand that
*is* subsumed may not be recognised and redundant work happens. That is the right trade — incompleteness
costs work, search costs soundness. And it draws a clean line: propagation's narrowing is **real
information**, monotone, and *should* wake the streaming aliases; labelling's is **conditional** and must
never reach them. (Which settles whether a partially-narrowed value may be streamed: it may, under exactly
that discipline.)

**And then the solver is not a component — it is producers.** A propagator watches, derives and posts;
with labelling banned that is all it does. Propagation is ordinary rules — `in(S,[50,100]) :- leq(S,100),
geq(S,50)` — positive, monotone, with the relation of derived bounds accumulating while the tightest
narrows. Entailment checking is an ordinary query; fixpoint is what the evaluator does anyway. So *"which
constraint domain"* is not a separate decision; it is *"which rules do you write"*, and they run where the
data is.

Several propagators over the same constraints **compose without coordination**, since all their narrowings
are entailed and the tightest is a read. A propagator that derives *less* than the rules would is simply
incomplete, which is safe.

**`all_different` is worth checking because it looks like it should fail: it does not.** Disequality is
legal as a **posted constraint**, and narrowing from it (`X ≠ 3` with `X ∈ {2,3,4}` gives `X ∈ {2,4}`) is
positive derivation. Only the *state test* — *"are these currently different?"* — is forbidden, and
propagation never needs it. **Entailment claims are monotone; membership claims on the solution set are
not.**

**The boundary worth stating.** Everything stays first-order while the constraint *vocabulary* is fixed.
Opening it, so a demand's shape is computed at runtime, buys **reflection** and its costs. What genuinely
needs it is the system reasoning about itself — demands about demands, or producers advertising *"I serve
any pattern of this shape."* Nothing here does.

**And exponentials are the wrong escape.** Finite limits, exponentials and `Ω` together *are* an
elementary topos, which gives power objects, hence comprehension, hence `a → b` and `¬a = a → ⊥`. Worse,
inverse images of geometric morphisms preserve finite limits and all colimits but **need not preserve
exponentials** — so "geometric logic with exponentials" is not richer, it is not geometric.

**Internalising does not put `→` within reach either, and the argument should be the semantic one.** Opens
form a **frame**, and a frame necessarily *has* implication. But it is not *definable*, and the one-step
reason is the invariance everything else rests on:

> **Frame homomorphisms preserve finite `∧` and arbitrary `∨`, and do not preserve `→`.** Only
> `h(a → b) ≤ h(a) → h(b)` holds, and it is strict: take `O(ℝ) → 2` at the point `0`, with `a = ℝ∖{0}` and
> `b = ∅`.

Every geometric formula's interpretation *is* preserved; `→` is not; therefore no geometric formula
defines it. That makes the guardrail **semantic** rather than syntactic — which matters, because a
syntactic guardrail is only as strong as nobody adding a term-former. The syntactic reading is worth
keeping as the operational one: naming `⋁{c : c ∧ a ≤ b}` needs a **comprehension**, and geometric `∨`
ranges over a **given** index family. So the thing to guard is not internalisation but **comprehension**,
and the slogan is not *"`→` is unwritable"* but **"`→` is not uniformly definable."**

## No functional dependency, and why

The tempting move is to declare a relation functional — *"for each key, exactly one value"* — so the store
can combine posts and compress. It should be resisted, and the reason is not the one it first appears.

**It is not a syntactic problem.** `p(X,Y) ∧ p(X,Y') ⊢ Y = Y'` is a perfectly good sequent; the `∀` and
`→` live at the sequent level, which theories permit.

**The problem is what asserting it does to a store that must accept what it is given.** A store holding
`loss(60,0.5)` and `loss(60,0.4)` is then not a *model* of its own theory. There are three responses and
none survives:

| | |
|---|---|
| refuse the second post | order-dependent — whoever arrives first wins, and contents depend on timing |
| derive the consequence | `0.5 = 0.4`, so the theory is inconsistent, so everything follows |
| record both and note the violation | fine — but then it was never an axiom |

Only the third works, and it is not a functional dependency at all: it is an **observation**.

> **Functionality is something a reader may ask about, never something the store asserts.**

**And the affirmable half is the negative one.** *"This relation is not functional at this key"* is a
positive existential over a growing set — monotone, decidable:

```
conflicted(K) :- loss(K,V1), loss(K,V2), V1 ⊔ V2 undefined.
```

**There are two kinds of conflict.**

| | what disagrees | how many atoms | who says so |
|---|---|---|---|
| **valuation conflict** | whether the atom is there at all | **one** | the store, structurally: the status is `{t,f}` |
| **domain conflict** | two atoms violate a constraint somebody declared | **two** | a user-written rule |

The rule above is a *domain* conflict, and writing it as though it were generic is a defect: it hardcodes
position 1 as key and position 2 as value, which is exactly the split §"There is no `read`" says no term
carries. Written honestly it is one declaration among many, and the **16 hand-rolled guard sites** in the
corpus are sixteen such declarations rather than one missing primitive. The generic half is the status:
`⊒ {t,f}` needs no declaration, no key, and no functor-specific knowledge.

*"This relation is functional here"* is the complement, hence closed, hence a report. That asymmetry is
structural: the well-formed region is a **lower set**, so its complement is an **upper set**, so **conflict
is affirmable and consistency is not.** You may react to a conflict the instant one exists; you may never
conclude there is none from partial information.

It also explains why *non-joinability* is the right test rather than joinability: non-joinability is
stable (`f(a)` and `g(b)` clash and always will) where joinability is not. Verified: zero counterexamples
to stability across terms, `Flat` and `Lex`. And the clash test is a finite disjunction over positions and
symbol pairs, each conjunct a threshold claim — agreeing with the unifier on every case tested **for
linear terms**; with a repeated variable a positional test is a lower bound and `conflicted` under-reports.

**Two consequences of having no functional dependency.**

*No top on the **value** order, and none needed.* A per-relation top is what a collapse would land on, and
a top satisfies every threshold — so one disagreement would fire every rule mentioning the relation.
Keeping the atoms apart keeps it out of reach. (The **status** order does have a top, `{t,f}`, and it is
harmless for exactly that reason: it is a top over *what you were told*, not over the value, so it
satisfies no threshold a rule reads on the value.) A "broken" flag is the same defect in different
clothes: discarding values and recording a bit is the one operation that moves *down*, and it retracts.

*Nothing needs broadcasting.* Ask whether a reader who already got `f(a)` must be told when `g(b)` arrives.
A **threshold** claim is still true; an **exact** claim was never legitimate on an unsettled term. **No
legitimate claim is invalidated by a conflict**, so there is nothing to push and no registry of past
contributors to keep. The store owes something *readable* — a status for a valuation conflict, a queryable
predicate for a declared domain one — never a notification.

**And conflict is reachable without forgery**, which is why it has to be designed for: two honest
producers differing by one ulp (`0.30000000000000004` vs `0.3`) do not reconcile. `mycooc/analyze_run.py`
already hand-rolls a guard against exactly this.

**What this costs, and where it does not.** The rejection applies only where the order is **partial** in
the sense that some pair has *no least upper bound*. Any join-semilattice may be quotiented at storage,
freely and soundly: `a ⊔ b ⊒ a` and thresholds are upward-closed, so nothing either post satisfied can
stop being satisfied. **But off a chain, the join *synthesizes*** — `{a} ⊔ {b} = {a,b}`, and nobody said
`{a,b}`. That is not unsoundness; what it costs is **provenance**, since on a chain the stored value is
always one somebody posted.

Where the order *is* partial there are exactly two completions: add a `⊤` (total and compressing, and it
explodes) or take the **powerset** (total and sound, and it does not compress). The design takes the free
completion, which is what "keep the atoms" has meant throughout.

**And the whole argument is smaller than it reads.** Measured over **823 real logs, 2.5M records**: the
compression given up is **0.34%**, and cells whose values genuinely fail to join are **0.072%**. Both are a
footnote. Where the partial case *does* land supports keeping the atoms — **1,714 of the 1,719 divergent
cells are `status`**, an app event mirrored onto the value plane at a reused step, where last-write-wins
reports a run as *saving* at step 87 of training.

## Types: many-sorted, per functor, structural

Each functor declares the sorts of its arguments and its result. The signature belongs to the
**program**, never to the data — no type is inferred from whoever posts first, which would make the type
check a first-writer-wins register decided by arrival order.

**Sorts are per functor and there is no untyped escape hatch.** A single flat relation —
`value(Name, V, Step)` for every metric — looks like it buys an open namespace and is unsound: one functor
has one signature, so every value position shares a sort, so `value(loss, X, S)` and `value(converged, X,
S)` may share `X`. That aliases a float slot to a bool slot with nothing to object. Per-functor signatures
reject it at the alias.

**But the partition is by *sort*, not by name, and that is far cheaper than it looks.** Measured over 821
real logs: 24 distinct value names, **none carrying more than one sort** (21 `float`, 3 `dict`), and no new
names in the corpus's second half. So the entire measured value plane is **two** relations —
`metric(Name, Float, Step)` and `event(Name, Json, Step)` — both fixed shapes with the name as **data**.
The aliasing objection never arises, because the partitions are separated by *relation* rather than by
name. A consumer declares one signature per value *sort*, not one per metric: two, against twenty-four
names. The case that motivates the rule is genuine — `mycooc`'s `permutation` carries `None` under one
flag and a nested record under another, in **source** — and it is a hazard the rule forecloses rather than
damage it repairs, since **0 of 24** names show sort drift.

**Wrappers recover what the flat relation was for, as schema rather than machinery.** Per-functor sorts
make `loss/2` and `accuracy/2` different relations, so *"every metric"* would be `∃F. F(S,V)` — not
first-order. A user wraps, and nothing in the engine changes:

```
metric(loss(60, V))        -- Metric = loss(Step,Float) | converged(Step,Bool) | …
table1(loss(V), 60)        -- Metric = loss(Float)      | converged(Bool)      | …
```

The first buys **rangeability**; the second **hoists the axis out**, making step a shared position so
*"everything at step 60"* is one pattern — which is exactly the defect the indexing measurement found,
fixed in the user's own schema with zero engine support.

With a static signature a type conflict **is not expressible at runtime**. It is a program that does not
typecheck, caught twice: locally before anything is sent, and again on receipt, because at an
honour-system boundary a peer's message is never trusted. Neither check coordinates, and the reason is
precise: **a type error is a property of the message alone** — no store state is consulted — so rejecting
it is order-independent. That is exactly what a *value* conflict is not.

Agreeing the signature is deployment, not runtime. **The residual is real:** if signatures were themselves
data, the problem returns unchanged. And it is a soundness precondition for the negative side, not only a
typing discipline — `ground(Q)` is computed against a signature, so two agents disagreeing about the
signature assert **different `{f}` regions**.

### Sort closure, and disequality

**Disequality is a constraint-domain predicate, never a logical connective**, so it was never in the
derivation layer. What it costs is preservation: `≠` is not preserved under homomorphisms, because a
homomorphism may identify two constants — the same *"two unbound variables may yet be identified"* seen a
third time.

**And it is a type-level closed-world assumption.** `a ≠ b` is licensed only by the Unique Name
Assumption, which is one of the three components of Reiter's CWA formalisation. So:

> **Disequality over a *closed* sort is sugar for a finite `∨` of equalities — positive, homomorphism-
> preserved, free. Over an *open* sort it is a genuine primitive, and it costs the stronger preservation
> property.**

Marking a sort closed is a **signature-level** fact, so it rides the deployment channel rather than
needing a runtime membership query — the same channel as signature agreement, with the same failure mode
if two agents disagree. And the one place `≠` looked forced — the residual over an unbounded axis —
**decomposes into intervals**, which are `≤`/`>`. `all_different` above is already the closed case.

### Orders are mostly read-side

By **default** an order is not a storage type — it is how a **read** aggregates what it finds, which moves
the whole table off the safety path onto the cost path.

| aggregation | note |
|---|---|
| last-write-wins | `argmax` over `seq`; a report |
| `max` / `min` | a report. On a **dense** carrier the only compact element is `⊥`, so the affirmable claim is strict `⊐` — **ask open intervals, never points** |
| set union | the identity read, and the only option for a **holistic** aggregate (median, percentile), where no bounded *exact* summary exists. Bounded *approximate* ones do: a 200-bucket sketch reproduced a 2000-sample bootstrap CI to **1.07% of its width** |
| "must all agree" | the `conflicted` predicate above |
| lexicographic | fine as a *selection* order, dangerous as a *combining* one: with `attempt` at the head, `(1,running)` and `(1,crashed)` have least upper bound `(2,⊥)` — it **fabricates attempt 2**, in a design about attribution |

**Where the narrowing (Smyth) construction goes.** The three powerdomains are the three ways to make "a
set of possibilities" a domain: Hoare/lower (*may*), Smyth/upper (*must*), Plotkin/convex. Putting the
upper one on the *value* side is a category error — read extensionally as a set of facts it is antitone,
so a rule body binding a variable to a member is non-monotone. Its natural home is **demand**.

**Continuous carriers are restricted, not broken.** In a continuous dcpo the basic opens are `⇈c`,
coinciding with `↑c` exactly in the algebraic case; an open interval **is** `⇈c` for the interval domain.
Nothing settles at a point under bisection, but *"is `S` in `(0.4, 0.6)`?"* becomes true the moment
narrowing puts the domain inside it. So: **settle the question, not the value.** Thresholds fire and
standing queries die. (GC is *not* on that list: on the boundary the query neither fires nor dies.)

**Terminology hazards, all live.** *Join* — relational `⋈` versus lattice `⊔`, and the lattice join versus
the powerdomain pair. *Union* — status aggregation in the knowledge order versus value aggregation under a
declared `Set` order; in the paraconsistent literature these are **different operators**, one intersecting
the negative halves. *Membership* — network membership versus membership in a solution set. *Finite* — the
cover, the requester's obligation, `∧` in the fragment, and `E` in the residual. *Closed* — closed-world,
topologically closed, upward/Scott-closed, a closed sort, a closed vocabulary. *Linear* — a term with no
repeated variables, an order that is a chain, and linear in domain magnitude. *Horn* — see §"The
language". Name them differently in any implementation.

## What is checked, and what is the requester's

| | who |
|---|---|
| exact claims only on ground terms | **structural — not expressible otherwise** |
| sorts | **checked**, statically, at both ends |
| the quantity a demand denotes, and its finiteness | **one test, two owners**: the requester supplies the count, layer 7 meters it |
| a `∀` demand's finite coverability | **unassigned** — see §"Demand is control" |
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
| a **produced** base fact | a six-hour job — *if the producer still lives* | at a price, and only then |
| a **negative** claim | possibly **unrecoverable** | no |

The last row is the surprise. A `¬Q` is a determination made at a moment, and a fresh producer may have no
way to re-make it; evicting one descends the status `{f} → ∅`, the single descent this design does not
permit. So the cheapest-looking records are the ones that must never be collected — and *"assuming the
producers are still there"* is the clause the whole tiering turns on.

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

## What survives from runstate

- **The run as a durable identity outliving its processes.** The actual contribution, now explicit.
- **Content-addressed run ids.** Becomes the cache key, unchanged.
- **The verdict as a join of two partial observers** — and it is a *report*, which is why it may use the
  narrowing reading that derivation may not.
- **Cooperative, no enforcement.**
- **`never` as a fact rather than a status** — it survives as `¬Q` over a singleton region. What nobody
  posts is the **status**, which is read, not written.
- **Status cycles; values do not.** `running → OOM → running` cannot live in a monotone order, so the
  attempt index goes in the term and the cycle lives in the *sequence of attempts*.
- **Structure goes in the key, not the value.**

## What it does NOT solve

- **Cross-host liveness.** You still need a handle and a probe, and it still abstains off-host — and that
  abstention must not become a stored verdict.
- **The artifact plane.** Checkpoints on a filesystem remain unmodelled, and remain where a double-live
  worker's real damage lands.
- **Enforcement.** Still honour-system. This is why forgery defects survive.
- **The halt does not dissolve.** Self-withdrawal is free — scope a posted demand to its subscription and
  disconnecting ends it. But the measured case is an **operator** stopping a run a **scheduler** relaunches,
  i.e. withdrawing *someone else's* demand. That needs a write and an authority rule, and always did.
- **Diagnosing a conflict.** `{t,f}` is affirmable; telling a genuine disagreement from an over-claimed
  region is not, without provenance nobody has built.

## The honest cost

A **rewrite, not a refactor**, with consumers on the current API. It trades a design whose failure modes
are intimately known for one whose failure modes would have to be learned. And storage grows wherever the
order is partial, since there the only sound completion is the free one.

**No fold ports as-is.** Measured across the whole of `observables.py`: 13 of 16 fold readings are
non-monotone, and the operator responsible is `latest` = `argmax(seq)`, which appears six times directly
plus three `[-1]`/`reversed` and three `max(…)` in 551 lines. Each becomes **dual plus subtraction** — the
monotone half derived inside, one complementation performed outside:

```
inside   discharged(C) :- stop(C), stopped(S), S > C.
outside  unhandled = stops − discharged
```

**The inside half is confirmed.** 13 of 13 duals measured monotone, and **9 of 9 folds reconstruct
exactly** across every scenario tested; the worked example reproduces the shipped `undischarged_stops`
verbatim, one subtraction.

**The outside half is "a subtraction" for 5 of those 9**, and the residuals differ in kind:

| residual | folds | what it is |
|---|---|---|
| subtraction only | `latest_episode`, `_episode_stopped`, `undischarged_stops`, `value_series`, `live_episode` | the clean case |
| + an **aggregate** over a non-`seq` order | `progress`, `last_activity` | a `max` over the survivors — and `last_activity`'s is over `t`, non-monotone against `seq` |
| + an **emptiness test** on an already-complemented set | `_launcher_terminal`, `peek_terminal`, the four-state projection | `¬∃ started`, which no finite observation can affirm — measured to **retract a published verdict** when a later claim arrives |

**And picking the dual is where the danger is.** The obvious dual of `progress` is the high-water mark
`reached(K) :- heartbeat(_,S), K ≤ S` — and it is **wrong**, for the reason `progress`'s own docstring
gives: *"a monotone watermark here would re-open the splice it just closed."* After an episode rewind it
reports the old frontier; `ensure`'s window test then passes and returns a **spliced series as complete**.
`last_activity` has the same trap one axis over.

So this is eight rewrites, none mechanical, and at least one where the *natural* dual silently
reintroduces a bug the fold exists to prevent.

## Open

1. **Who checks finite coverability**, and whether it is checkable at post time at all — it is a claim
   about a producer's future. §"Demand is control".
2. **Provenance.** Not built, and now wanted in three places: diagnosing `{t,f}`, taint after a premise
   becomes disputed, and attributing a wrong `¬Q`. Positive facts need it equally, so it is one mechanism.
3. **Which constraint domain — *and which algorithm for it*.** Not separable: a generic bounds propagator
   on difference constraints ping-pongs where negative-cycle detection decides the same system in `O(V·E)`.
   For a step axis the answer is difference constraints with cycle detection. If strided demands are ever
   wanted, add congruences with CRT, and note the corpus has not been surveyed for what else it needs.
4. **Where the query language stops.** What constrains it is **pushdown**: the more expressive, the less
   runs where the data lives.
5. **No central store.** Each agent holds a lagged local copy and replicates preferentially what it
   demands; the "global" store is the union of the local ones. This follows from monotonicity and needs no
   coordination. The consequence: the memo check becomes **local**, so two agents demanding the same region
   without having replicated each other's answer both run the six-hour job. That is **single-spawn**, the
   one irreducibly coordinating requirement, and CALM says it cannot be coordination-free.

   **And there is a line here the design must not cross.** Content-addressed placement *is* a partitioning
   policy, and *"is this my address?"* is exactly the decision oracle that moves a system out of the model
   CALM's `iff` is stated in. Replicating by address is fine; **concluding absence from ownership is not.**
6. **Whether demand should be the only interconnect**, and not merely the only one that means anything.
   Making it architectural would bound coordination by the number of live channels, and would forbid
   unsolicited broadcast, which is a real affordance worth keeping.
7. **What a conflict means, given that three different things produce one.** **Domain**: re-production
   jitter under a single-spawn violation — a *correct* report of a disagreement the system itself caused.
   **Valuation**: two posters genuinely disagreeing, or one over-claiming its own `¬Q`. Measured
   counterweight for the first: **0 of 3,165** numeric re-productions diverged on the real corpus. The
   valuation case has not been measured.
8. **What `every` is** — a `∀` over a strided region, or a firing schedule. The wire format has three
   demand shapes and the quantifier rule covers two; a sampling demand fits neither comfortably, and the
   library defers exactly this. See `memoizer-index-algebra.md`, where it is recorded that the current
   delta reading is **non-monotone on a read path**.

## Related

- `if-built-today-decisions.md` — what was tried and withdrawn, and why.
- `../if-built-today-citations.md` — the verification ledger; every citation above marked CONFIRMED there
  has been read in primary source.
- `../dead_ends/topological-framings.md` — sheaves, formal topology, d-frames: three framings, one cause.
- `demand-driven-reads.md` — the consumer-facing target. **Stale**: it still describes a
  LEFT-JOIN-over-a-grid, `?` as a value, `read`/`force` as verbs, and LISTEN/NOTIFY. Its §5a taxonomy
  survives.
- `prolog-query-layer.md` §3 — the measured answer-subsumption results, reinterpreted: the defect is an
  exact claim on an unsettled term, and it does not arise here because nothing aggregates at write time.
- `memoizer-index-algebra.md` — the emission filter, and why exposing it is not additive.
- `../specs/write-authority.md` — unchanged by any of this; a unique constraint is test-and-set (consensus
  2), where `send(expected_seq=)` is compare-and-swap (consensus ∞).
- `../layers.md`, `../positioning.md` — where this sits.
