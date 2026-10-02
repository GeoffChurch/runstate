# Questions: asking, settledness and the residual

**Layer:** depends on [`0-substrate.md`](0-substrate.md), [`1-logic.md`](1-logic.md), [`2-polarity.md`](2-polarity.md). The dependency graph is in [`README.md`](README.md).

## A question is a tell of `asked`

**A question is a tell, too — of one relation.** A querier posts

```
asked(∃V. p(K, V)) :- K ≥ 1.
```

a region of the relation `asked`, whose argument is a **question**: a positive formula over atoms with its
quantifiers written out. The question's **free** variables — here `K`, universal as in any region — are
what it ranges over: for each value the body allows, an answer is wanted. Its **bound** variables — here
`V` — are what it wants witnessed. Nothing about the question's content is asserted: `asked` says only
that it was asked, which is true on posting, and *"this was not asked"* is never affirmable, so `asked` has
no polarity. A producer's rules match `asked` records and post tells, and that is the whole of
demand-gated production.

`asked` is **magic templates** — the magic-sets construction in its one-relation form. The per-relation
form — a separate `demand_p(X)` gating `p(X,V) :- demand_p(X), handler(X,V)` — carries the adornment,
*which position is bound*, as a second predicate for every relation, and so presupposes a key/value split
no relation has. `asked` is one relation for all of them and holds the whole question, so the adornment is
a property of the **question** rather than the relation: `asked(∃V. p(60, V))` and `asked(∃K. p(K, 0.31))`
ask two different things of one relation. A fully bound question — `asked(valid(c))`, the yes/no case —
is simply a ground record. The `asked` record is also the durable record of *"this was wanted"*, with no
second object needed. A producer needing something of its own posts an `asked` record too, which makes it
a querier — and that is how demand propagates to sub-demands, whether the agent does it in code or with
rules like `asked(∃V. loss(K, V)) :- asked(∃W. report(K, W))`, which are ordinary definite clauses
(§"Matching questions respects scope", below). The roles stay symmetric all the way down. It is also the
declaration of
intent that §"There is no `read`" says no syntactic property can replace. Quantifiers appear in questions
and nowhere in records that tell — [`README.md`](README.md) §"The order of the layers".

**An `asked` record does hold its question as a term, binders included — and that is not the
representing course** of [`1-logic.md`](1-logic.md) §"The constraint domain". Representing buys reflection because a user
rule may *interpret* a quoted pattern through `denote`. Here no rule does: a producer matches a question
of a fixed shape it was written for, and the one thing that interprets a question — deciding whether it is
settled and what its residual is — is the engine's coverage check, over a fixed question language:
positive formulas, explicit `∃` and `∀`, constraints from the fixed domain. The vocabulary is fixed at
design time, which is the axis that section turns on.

**Matching questions respects scope.** A program that reads a stored question reads a formula with
binders in it, and the classic hazard of that is **capture**: a variable of the reading program coming to
stand for a variable the question binds, and being carried into some other question where it means
something else. In the rule language it cannot be written. A rule's question pattern carries its own
binders — `asked(∃W. report(K, W))` — and the engine matches it against stored questions up to renaming of
bound variables. Canonical numbering already makes renamed variants one record, so this is cheap. The
rule's `K` lies outside the scope of `∃W`, so it cannot stand for `W`: ordinary lexical scope, the same as
in any language with lambdas, and the freshness side-condition of matching under binders (nominal logic;
Miller's higher-order patterns). A rule that tries to take hold of a bound part of a question has no way
to be written. For agents written in other languages, the same guarantee is a small **question API** —
build a question from a template; open a stored one with fresh names — so no code handles the wire
encoding directly.

**Three kinds of variable, kept apart.** Those of a posted record are scoped to it, and on the wire they
are `var(N)` ([`0-substrate.md`](0-substrate.md) §"The wire format"). Those of a rule are scoped to its clause. And a **shared unknown** across
records is not a variable at all: it is a ground name — `x37`, or a description term like `best_of(r)` — on
which agents post `eq` records, and which a reader folds with the rest of an equality class only if its
lens opts in ([`1-logic.md`](1-logic.md) §"The language"). That needs nothing from the core, and the trade-off of
[`0-substrate.md`](0-substrate.md) §"Why not variables scoped to the store" — fold two bindings of one name and they
identify two values — becomes the user's explicit choice.

**Asking claims nothing, and that is load-bearing.** An earlier draft read a posted pattern `p(60, V)` as
asserting *"there is a value at key 60"* — which the asker does not know, so it broke *post what you know*,
and a question about key 900 of a producer that stops at key 500 was a false post nothing caught. Read as an
assertion, the question also satisfies itself: its own post witnesses the existential it asks about. The
Skolem constant, the named hole and the unsatisfied existential were three forms of that one mistake
([`decisions/3-questions.md`](decisions/3-questions.md), §"The Skolem reading", §"Record scope").

So there is exactly one demand predicate, no `while` combinator and no watcher concept. A watch is a
reader's own rule over the store; only `asked` triggers production.

**Answers stream individually.** An answer is any tell whose region meets the question's — a ground fact
in range, or a region — and a querier receives them one at a time; there is no answer *object* anywhere.
A question is an open set and an answer is truth restricted to it, which is the knowledge-order meet
([`2-polarity.md`](2-polarity.md) §"An atom's status"): the store can affirm *asked* but never *not asked*, so nothing outside
the question is read as false. What ends a stream is the question becoming **settled** (§"Settledness of a
question", below), read off the statuses —
never the arrival of one record, since on an unordered transport a producer's `¬` can arrive before its last
positives. *Completeness* is not a record anyone posts: it is read off the statuses, and shown, when
needed, by exhibiting the cover.

Packaging answers into a growing term is a tempting dead end. Nothing bindable is unordered: a set term is
ground, so adding to it is a different term, which leaves a **list** — and a list fixes an insertion
order, so two nodes learning the same answers in different orders build `[a,b|T]` and `[b,a|T']`, which
unification cannot reconcile because it cannot reorder a spine. The divergence is representational, so
redelivering every message does not repair it. A shared tail is also a lossy CAS: once one producer binds
`T = []`, another's next answer is rejected. One tail *per producer* fixes both — and that is the right
**structure** in the wrong **representation**, since what it encodes is one termination marker per
producer per stream — and that is **exhaustion**, a fact about a producer, not `¬Q`, which has no author
([`2-polarity.md`](2-polarity.md) §"The rule for posting"). The dead end kills the term, not the idea.

## Settledness of a question

[`2-polarity.md`](2-polarity.md) §"The threshold rule" defines settledness for a set of atoms: every atom decided. A question
refines it, because a question may bind some of its variables.

**For every tuple of its free variables in range, the question is decided**: its positive reading holds,
or its negative reading does. Its established name is **query completeness** (see the ledger) — with one
difference: that literature assumes what is held is true, where here an instance may be decided as
`{t,f}`. It is affirmable by a finite cover, under the conditions [`2-polarity.md`](2-polarity.md) states, and not derivable in
the fragment, whose bodies have no `∀`.

**The two readings follow the connectives.** For an atom, the positive reading is `told(a, pos)` and the
negative is `told(a, neg)`. Conjunction is positive when both halves are and negative when either is,
dually for disjunction; and `∃y` is positive when some instance is, **negative when every instance is**,
dually for `∀y`. A constraint relativises both: the negative reading of `∃V. c ∧ φ` is *"every `V`
satisfying `c` makes `φ` negative"*. These are the strong-negation clauses (Nelson), and both readings
mention `told` only positively, so settledness is monotone in the store, for any order of quantifiers,
over a fixed universe.

**How strong a settledness is depends on what the question binds.** Settled for `∃V. p(K, V)` means
every key has *a* value, or has none — and says nothing about whether a key has *other* values, since
binding `V` asked for one. Settled for `p(K, V)` with `V` free as well means every (key, value) pair is
decided — the set-of-atoms settledness of [`2-polarity.md`](2-polarity.md) — and every key's set of values is complete. The
second needs someone to vouch, at each produced key, that there is no other value there, which is honest
exactly when the key determines the value ([`2-polarity.md`](2-polarity.md) §"The rule for posting"). **A "no" answer to a
question that constrains a bound variable needs the second.**

**An `asked` record decides nothing about its content**: it is a fact about asking. And **nothing may
derive settledness from demand going quiet** — demand disappearing determines nothing, so it moves no
atom out of `∅`.

**And the question must stay the same question**, or settledness is not a monotone read: settledness is
antitone in the extent, so a question whose extent grows can flip from settled to unsettled with nothing
descending in the store. With variables scoped to records, no posted question grows on its own. It can
still grow if the signature grows, if a reader's lens coarsens, or if a constraint mentions a description
term whose meaning is later resolved — so constraints in questions mention ground constants only.

**The threshold rule's fifth instance.** [`2-polarity.md`](2-polarity.md)'s table gains a row here:

| order | affirmable | never |
|---|---|---|
| **demand** | *"this was asked"* | *"this was not asked"* |

## The residual, assembled

The production path is the design's most load-bearing multi-hop argument, and it is easier to get wrong in
pieces than whole.

Production is gated on demand, and before running a producer the evaluator asks *"do I already have an
answer?"* That memo check **is the cache** — without it the producer re-runs on every call, and a run here
is a six-hour job. It is semantics rather than housekeeping: where re-production is not bit-identical,
removing it changes the answer set outright.

**But it is not a memo *table*, and decidedness is checked per instance** — for each tuple of the
question's free variables, a lookup in the index, not a hit-or-miss verdict on a whole call. Keyed on
**calls**, two demands that overlap without either containing the other each miss and each run in full,
where per instance the second runs only on the difference. Keyed on **answers**, one answer makes the whole
question look served — which is wrong for a free variable and exactly right for a bound one: for
`∃K, V. p(K, V) ∧ V < 0.1`, one witness is the whole answer. What the question binds is what
says which.

**So what the producer is handed is a residual, not a verdict**: the question's instances not yet decided
either way. Subtracting the negative part matters as much as the positive: an instance somebody determined
absent is one nobody should be asked to produce.

**And here the four hops meet.** An undecided instance is exactly the thing that is *not readable* — a
down-set, answerable only in the direction of leaving it ([`2-polarity.md`](2-polarity.md) §"An atom's status"). So the residual is **not a
derivation**. It survives because it is never published: it is computed **locally**, **best-effort**, and
consumed as a **scheduling decision**, which is control (§"Demand is control"). It need not be
materialised; walking the extent and skipping decided instances computes it incrementally, and the
producer's output need not cross the link, since the handler is near the data.

**Its emptiness is settledness.** A question's residual is empty exactly when every instance is decided,
which is §"Settledness of a question". So the two are one object read in two
directions: *empty* is witnessed by a finite cover — affirmable, publishable — and *non-empty* is a
down-set, local and best-effort. Settledness is not a second primitive on the other side of the line; it is
the residual's one affirmable reading.

**Some residuals never empty, honestly.** A question with a free variable over an unbounded value sort —
every value at every key — is settled only by vouching at every produced key, and a producer that
cannot vouch leaves it open forever. A scheduler that relaunches on a non-empty residual then relaunches
forever. So admission owes a rule for such questions, and the no-progress guard must hold against them.
The same failure shape, a relaunch loop the guard did not stop, was measured when the `control.target`
design was refuted ([`../../specs/control-target.md`](../../specs/control-target.md), R5: 373 spawns in 3 s); there the cause was a guard
that could not fire on time targets.

**That removes a mechanism rather than adding one.** A demand entirely covered by another has an **empty
residual** and costs nothing, without anybody detecting that it was subsumed — so containment compaction
is unnecessary. What survives is the concurrent case: two producers serving overlapping demands can each
produce before either sees the other's output. That is **single-spawn**, priced in [`open.md`](open.md).

One representation note. Per-instance is the *semantics*; over a wide axis, *"what is settled"* is
naturally stored compressed — intervals, runs — so computing a residual does not mean walking a billion
steps. Meaning per instance, storage as dense as it likes.

## Demand is control

A demand is a post, and monotone like any other. What is not monotone is *listening*: a lease expires, a
querier withdraws, an operator halts a run. That is fine.

**Nothing derived becomes false; some things never get derived.** A subscription that ends means nobody is
told; a producer stopped by an operator's `stop` — a told fact, never an inferred silence — means a term
is not produced, so a reader's threshold claim never fires and suspends forever. That is a **liveness**
failure, not a safety one. Two replicas with different demand produce different *subsets*; every literal in either
is correct.

All three mechanisms are control: resource management, a querier changing its mind, and somebody
deliberately stopping a machine. The logic never had jurisdiction over any of them.

**So production is gated on three things, and only two are facts.** A producer is launched for a question
that **was asked**, is **unsettled**, and is **still wanted**. The first two are monotone reads of the
store. The third is control: an `asked` record is permanent, so if *still wanted* were read off it, an
asker that went away would leave a question that gates production forever — and one that can never settle
would be relaunched forever. *Still wanted* is supplied by the non-monotone core, like any reading of a
clock, and is never a fact anyone posts as true. How it is supplied — a lease, a session, a durable
standing order — is a scheduling policy, not part of this layer.

### Quantifiers live in questions, and nowhere else

> **A demand is an `asked` record holding a question.** Its extent is the region its constraints denote —
> bounded or not, and the solver reads which. Its quantifiers are written out: free variables are what it
> ranges over, bound variables what it wants witnessed.

**Two shapes carry every quantifier, and each is read off the shape.** In a **tell**, every variable is
universal — clause-`∀`, from being a region — and none is existential, because no posted record is. In a
**question**, the free variables range and the bound ones are whatever their binders say, `∃` or `∀`, in
any order: *"some config that passes on every seed"* is `∃C. ∀Seed. passes(C, Seed)`. Rules keep body-`∃`
from body-only variables. Nothing else carries a quantifier.

**This reverses an earlier simplification, and the reason is control.** A draft carried one `∀`/`∃` bit per
demand; a later one deleted it, on the grounds that a partial term already asserts existence and that
*"decide every atom in this region"* was never a request, since a producer derives everything its rules
reach. The first ground fell with the reading of a demand as an assertion (§"A question is a tell of `asked`"). The second was
right about **production** and wrong about **control**: what a producer does needs no quantifier, but what
the scheduler computes does. The residual of `∃V. p(K, V)` empties when every key has a value; the
residual of `p(K, V)` with `V` free empties only when every key's values are vouched complete; and one
witness empties `∃K, V. p(K, V) ∧ V < 0.1`. Three different
scheduling decisions over one pattern — so the quantifiers travel, per variable, with the question
([`decisions/3-questions.md`](decisions/3-questions.md), §"Record scope").

**Bounded is a property of the constraint, not admission control in a quantifier's clothes.** `K ≤ 1000`
is bounded; `K ≥ 1` is not; either is a legal demand. The unbounded one is the demand this design is best
at expressing, and it is perfectly dischargeable because the question's extent need not be finite provided
the *cover* is — for a question that binds the value, a positive prefix to key `n` plus one `¬(K > n)`.

**Who checks it splits in two.** Whether the extent is bounded is the solver's, at post time — the
checkable half, previously unassigned because it was bundled with the other. Whether a finite cover will
*ever* exist for an unbounded extent is a claim about an external producer's future, and no post-time
check reaches it: a producer halted short of convergence knows nothing about the rest and must post
nothing ([`2-polarity.md`](2-polarity.md) §"The rule for posting"). That half is [`open.md`](open.md), and it is the exhaustion question, not a
quantifier one.

### Facts and demands are dual, and the duality is exact

**The points are ground atoms**, and this follows from the layering rather than being chosen for the
topology: truth-bearers are ground atoms, each with a status, and quantified sentences have none
([`1-logic.md`](1-logic.md) §"The language"). (An earlier draft argued it from the producer's obligation to post `¬(Q₀ ∖ E)` — a region
that is not upward-closed — and that obligation is gone; [`2-polarity.md`](2-polarity.md) §"The rule for posting".)

**Say the consequence out loud: on that space the topology does no work.** Ground atoms are maximal, so
the space is **discrete** and its frame is the complete **Boolean** powerset — measured three times
independently, first exhaustively on a small carrier (**8/8** singletons basic open, **256/256** opens
complemented) and twice since on larger ones. Every open is affirmable, nothing is forbidden, and any structure defined by which sets are
open is inert here. What survives is not topology but the **basis**, which frames deliberately forget.
[`../../dead_ends/topological-framings.md`](../../dead_ends/topological-framings.md) records the three attempts that foundered on this. Two carve-outs:
**continuous value carriers** are the one place the space is genuinely not discrete — though IEEE floats
are a finite set, so treating a value axis as dense is a modelling choice — and the **instantiation** order
on partial terms is real and used throughout, simply not the order whose points these are.

With that stated, the duality is worth having:

- A **fact** is a ground term: a maximal element, a **point**.
- A **question**'s free-variable pattern denotes `↑p`. Finite partial terms are exactly the **compact**
  elements, so a demand *is* a basic open — with two exceptions this document names: on a **dense
  carrier** the only compact element is `⊥`, and a **disequality** denotes no open, since `X ≠ Y` is not
  upward-closed. The duality is about the free-variable pattern only: a question's bound quantifiers —
  `∃C. ∀Seed. passes(C, Seed)` — sit outside it.
- Satisfaction is `x ∈ U` — the pairing between a space and its frame.

**A pattern and its grounding are interchangeable**, which is what makes regions work: the maximal
elements of `↑p` are exactly `p`'s ground instances, and over a signature rich enough that every partial
term has ground instances, distinct opens have distinct groundings. So **subsumption of patterns is
inclusion of groundings**, which is the one-line reason a negative claim transfers to every subsumed
question.

**A term with variables uniformly denotes a set, and what is done with the set is decided by the
relation it is posted in.** A tell reads *universally* over the set, whichever its polarity; an `asked`
record reads *interrogatively* — it asks about the set and asserts nothing of it. Two forces, one
representation, and the force is a relation, not a modality. What this gives up is the **partially
instantiated answer** — *"the result is some `tree(a, _)`"* — as a single record: no posted record is
existential. Knowing that something exists without knowing it is expressed at user level, as a coarser
relation (`bounds(x, 100, 200)`), which is an ordinary ground fact that questions and regions can match.

That is Stone duality, and the proof-theoretic sense of polarity is apt too: **a fact is data where a
demand is a continuation**. But polarity should not be asked to carry more — it explains neither
restriction the design leans on, since focusing makes `∧⁺` positive with no cardinality condition. The
asymmetry between finite `∧` and arbitrary `∨` is **left-exactness**, a different fact wearing the same
word.

### Demand subsumption

**Subsumption is a preorder on questions, and it runs two ways at once** (§"Quantifiers live in
questions"). Over the **free** variables it runs against instantiation, because `↑` is order-reversing: for
facts more instantiated is higher, for questions `p ⊑ q` gives `↑p ⊇ ↑q`, so the **more general** question
subsumes the specific one — having asked `∃V. p(K, V)` for every `K`, a standing `∃V. p(12, V)` is
redundant. Over the **bound** variables it runs the other way: a tighter constraint on a witnessed
variable is a *stronger* question. `∃V. p(12, V) ∧ V < 0.1` is not redundant after `∃V. p(K, V)`, which a
value of `0.31` at key 12 already settles. So the call table keys a question by its pattern
**and** its binders.

But go no further. **Anti-unification is the join in the demand order and it over-approximates**:
generalising `p(12, V)` and `p(13, V)` yields `p(K, V)`, which demands *every* key. On the fact
side the join is safe because it adds information; on the demand side it adds *work*, and here a unit of
work is a six-hour job. One caveat on the compression: **derive the work set, never destructively shrink
it.** An `asked` record is never withdrawn, but whether its question is still wanted is control
(§"Demand is control"), and recomputing the work set from the questions still wanted handles an asker
going away for free.

**What to internalise, and what not.**

| | monotone? | where |
|---|---|---|
| the **demand** — an `asked` record | yes, an ordinary post | the store; it gates a producer's rule |
| the **subscription** — *"tell me when this changes"* | no, revocable | the reader's own rule plus routing: the rule is program, the routing is transport |

Production is triggered by a demand matching a producer's rule. A subscription triggers nothing: it is a
body literal that fires on arrival, seen from the transport's side, and it needs no object in the store —
what a subscriber missed is still there when it returns, since nothing retracts. No `Open` sort and no
`denote` are needed, so no reflection is bought. The shipped `control.subscribe` is what this replaces:
three concerns in one record (a read, a route, a trigger), pairing answers to requests **by log position**
— the positional answer fold — which [`README.md`](README.md) §"Two commitments" forbids. Its `request_id` survives only as a
routing handle — who asked, so who is told — kept outside the question's identity, or identical questions
stop being one record.

**It does not carry a dependency graph, and the logical layer needs none.** When a handler serving
`g` posts an `asked` record for `p`, the store cannot tell that from an unrelated querier — symmetric
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
`needs(g, p)` once, alongside its sorts. Posting `needs` at runtime is legal, with one collision:
if the producer has already posted `¬Q` and the new capability falls inside `Q`, producing there
contradicts its own claim — caught as `{t,f}` like any other valuation conflict.

Concretely a subscription is a **cursor into a per-functor term index** — walk the trie with your pattern,
get notified when new leaves appear beneath it. **This is one object with four names in earlier drafts**:
the call table, the registry, the work set and the cursor are the set of questions still wanted, keyed by
pattern and binders, indexed by pattern. The division: *the call table decides who is told; the store decides
what is computed.* Two queriers independently asking the same question post one record — variables are
numbered canonically within a record, so identical questions are identical records — and what remains of
the table is its *routing* role: who is told. That needs a canonical form for constraints, too. Without
one, identical questions fail to coincide, which wastes work — and worse, a producer rule written for one
shape of a question never fires on an equivalent question shaped differently, which costs liveness. Still
nothing unsound.

**There is no `read`, and no syntactic substitute.** A tempting test — *"does the posted term have a free
variable in key position?"* — cannot carry the distinction. It is not invariant under rewriting
(`p(K,V) ∧ K=60` classifies opposite to the identical `p(60,V)`), and it presupposes a key/value
split no term carries: `verdict(Outcome, FinalStep)` has two value positions, `provenance(key, Prid, Sha)`
has two key positions. What two verbs were buying is **a declaration of intent that survives rewriting**,
which a syntactic property cannot replace — and `asked` is that declaration, a relation rather than a
syntactic test. The same argument rules out reading which variables a question *binds* off which ones its
constraint mentions: a vacuous `V > -∞` would flip a variable's role without changing the question. Hence
the explicit binders. **Admission control still needs an explicit mechanism**, and the natural signal is a
*quantity*: how many instances does this question range over, and can its residual ever empty?


## What is checked, and what is the requester's

| | who |
|---|---|
| exact claims only on ground terms | **structural — not expressible otherwise** |
| sorts | **checked**, statically, at both ends ([`1-logic.md`](1-logic.md) §"Types") |
| the quantity a demand denotes, and its finiteness | **one test, two owners**: the requester supplies the count, a planner above this layer meters it |
| a demand's extent is bounded | **the solver**, at post time; whether an unbounded extent is ever *covered* is an external producer's future — [`open.md`](open.md) |

**Finiteness is not a decidability claim.** Range-restriction over a grid looks like one and is not: it is
a syntactic test over a relation *asserted* finite, and *"is this derived relation finite"* is undecidable.
A producer whose extent is decided by when it finishes has no key grid known in advance. The guarantee is
*what you asked for is what you get*.
