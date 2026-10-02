# Questions: asking, settledness and the residual

**Layer:** depends on `substrate.md`, `logic.md`, `polarity.md`. The dependency graph is in `README.md`.

## A question is a tell of `asked`

**A question is a tell, too — of one relation.** A querier posts

```
asked(∃V. metric(r, loss, V, S)) :- S ≥ 1.
```

a region of the relation `asked`, whose argument is a **question**: a positive formula over atoms with its
quantifiers written out. The question's **free** variables — here `S`, universal as in any region — are
what it ranges over: for each value the body allows, an answer is wanted. Its **bound** variables — here
`V` — are what it wants witnessed. Nothing about the question's content is asserted: `asked` says only
that it was asked, which is true on posting, and *"this was not asked"* is never affirmable, so `asked` has
no polarity. A producer's rules match `asked` records and post tells, and that is the whole of
demand-gated production.

`asked` is **magic templates**: one generic relation holding the whole pattern, rather than one demand
predicate per relation, so the adornment stays in the pattern and no key/value split is presupposed
(`polarity.md` §"Falsity is told"). It is also the declaration of intent that §"There is no `read`" says no syntactic
property can replace. Quantifiers appear in questions and nowhere in tells — valuation sits below
quantification, and a quantified sentence is never told (`logic.md` §"The language").

**Asking claims nothing, and that is load-bearing.** An earlier draft read a posted pattern `loss(60, V)` as
asserting *"there is a loss at step 60"* — which the asker does not know, so it broke *post what you know*,
and a question about step 900 of a run that stops at 743 was a false post nothing caught. Read as an
assertion, the question also satisfies itself: its own post witnesses the existential it asks about. The
Skolem constant, the named hole and the unsatisfied existential were three forms of that one mistake
(`decisions/questions.md`, §"The Skolem reading", §"Record scope").

So there is exactly one demand predicate, no `while` combinator and no watcher concept. A watch is a
reader's own rule over the store; only `asked` triggers production.

**Answers stream individually.** An answer is any tell whose region meets the question's — a ground fact
in range, or a region — and a querier receives them one at a time; there is no answer *object* anywhere.
What ends a stream is the question becoming **settled** (`polarity.md` §"The threshold rule"), read off the statuses —
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
(`polarity.md` §"The rule for posting"). The dead end kills the term, not the idea.

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
`∃S, V. metric(r, loss, V, S) ∧ V < 0.1`, one witness is the whole answer. What the question binds is what
says which.

**So what the producer is handed is a residual, not a verdict**: the question's instances not yet decided
either way. Subtracting the negative part matters as much as the positive: an instance somebody determined
absent is one nobody should be asked to produce.

**And here the four hops meet.** An undecided instance is exactly the thing that is *not readable* — a
down-set, answerable only in the direction of leaving it (`polarity.md` §"An atom's status"). So the residual is **not a
derivation**. It survives because it is never published: it is computed **locally**, **best-effort**, and
consumed as a **scheduling decision**, which is control (§"Demand is control"). It need not be
materialised; walking the extent and skipping decided instances computes it incrementally, and the
producer's output need not cross the link, since the handler is near the data.

**Its emptiness is settledness.** A question's residual is empty exactly when every instance is decided,
which is the knowledge-complete row of `polarity.md` §"The threshold rule". So the two are one object read in two
directions: *empty* is witnessed by a finite cover — affirmable, publishable — and *non-empty* is a
down-set, local and best-effort. Settledness is not a second primitive on the other side of the line; it is
the residual's one affirmable reading.

**Some residuals never empty, honestly.** A question with a free variable over an unbounded value sort —
every loss value at every step — is settled only by vouching at every produced key, and a producer that
cannot vouch leaves it open forever. A scheduler that relaunches on a non-empty residual then relaunches
forever. So admission owes a rule for such questions, and the no-progress guard must hold against them;
the `control.target` design was refuted by exactly this storm (`../../specs/control-target.md`, R5: 373
spawns in 3 s).

**That removes a mechanism rather than adding one.** A demand entirely covered by another has an **empty
residual** and costs nothing, without anybody detecting that it was subsumed — so containment compaction
is unnecessary. What survives is the concurrent case: two producers serving overlapping demands can each
produce before either sees the other's output. That is **single-spawn**, priced in §Open.

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
the scheduler computes does. The residual of `∃V. metric(r, loss, V, S)` empties when every step has a
loss; the residual of `metric(r, loss, V, S)` with `V` free empties only when every step's losses are
vouched complete; and one witness empties `∃S, V. metric(r, loss, V, S) ∧ V < 0.1`. Three different
scheduling decisions over one pattern — so the quantifiers travel, per variable, with the question
(`decisions/questions.md`, §"Record scope").

**What ships already carries its extent as a constraint.** A subscription with an `until` is one durable
record denoting a bounded region — durable across the *producer's* death, because a worker re-drains the
control log and re-registers whatever is still unanswered, pinned by
`tests/test_run_episodes.py::test_relaunch_extends_one_series`: one subscribe posted *before episode 1
exists*, two episodes, ten steps, one series. `{"every": …}` with no `until` is schema-legal, documented
as *"forever"* — the unbounded region, which is what makes the case below concrete rather than
hypothetical. (The bare subscribe is served by a poll of the register, `self._values.get(name)`, not by
anything waiting on a production; that is the gap between the shipped library and this design.)

**Bounded is a property of the constraint, not admission control in a quantifier's clothes.** `S ≤ 1000`
is bounded; `S ≥ 1` is not; either is a legal demand. The unbounded one is the demand this design is best
at expressing, and it is perfectly dischargeable because `ground(Q)` need not be finite provided the
*cover* is — for a question that binds the value, a positive prefix to step 743 plus one `¬(S > 743)`.

**Who checks it splits in two.** Whether the extent is bounded is the solver's, at post time — the
checkable half, previously unassigned because it was bundled with the other. Whether a finite cover will
*ever* exist for an unbounded extent is a claim about an external producer's future, and no post-time
check reaches it: a producer halted short of convergence knows nothing about the rest and must post
nothing (`polarity.md` §"The rule for posting"). That half is `open.md`, and it is the exhaustion question, not a
quantifier one.

### Facts and demands are dual, and the duality is exact

**The points are ground atoms**, and this follows from the layering rather than being chosen for the
topology: truth-bearers are ground atoms, each with a status, and quantified sentences have none
(`logic.md` §"The language"). (An earlier draft argued it from the producer's obligation to post `¬(Q₀ ∖ E)` — a region
that is not upward-closed — and that obligation is gone; `polarity.md` §"The rule for posting".)

**Say the consequence out loud: on that space the topology does no work.** Ground atoms are maximal, so
the space is **discrete** and its frame is the complete **Boolean** powerset — measured three times
independently, first exhaustively on a small carrier (**8/8** singletons basic open, **256/256** opens
complemented) and twice since on larger ones. Every open is affirmable, nothing is forbidden, and any structure defined by which sets are
open is inert here. What survives is not topology but the **basis**, which frames deliberately forget.
`../../dead_ends/topological-framings.md` records the three attempts that foundered on this. Two carve-outs:
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
relation (`divergence_bounds(r, 100, 200)`), which is an ordinary ground fact that questions and regions can match.

That is Stone duality, and the proof-theoretic sense of polarity is apt too: **a fact is data where a
demand is a continuation**. But polarity should not be asked to carry more — it explains neither
restriction the design leans on, since focusing makes `∧⁺` positive with no cardinality condition. The
asymmetry between finite `∧` and arbitrary `∨` is **left-exactness**, a different fact wearing the same
word.

### Demand subsumption

**Subsumption is a preorder on questions, and it runs two ways at once** (§"Quantifiers live in
questions"). Over the **free** variables it runs against instantiation, because `↑` is order-reversing: for
facts more instantiated is higher, for questions `p ⊑ q` gives `↑p ⊇ ↑q`, so the **more general** question
subsumes the specific one — having asked `∃V. loss(r, V, S)` for every `S`, a standing `∃V. loss(r, V, 12)`
is redundant. Over the **bound** variables it runs the other way: a tighter constraint on a witnessed
variable is a *stronger* question. `∃V. loss(r, V, 12) ∧ V < 0.1` is not redundant after `∃V. loss(r, V,
S)`, which a loss of `0.31` at step 12 already settles. So the call table keys a question by its pattern
**and** its binders.

But go no further. **Anti-unification is the join in the demand order and it over-approximates**:
generalising `loss(V,12)` and `loss(V,13)` yields `loss(V,S)`, which demands *every* loss. On the fact
side the join is safe because it adds information; on the demand side it adds *work*, and here a unit of
work is a six-hour job. One caveat on the compression: **derive the work set, never destructively shrink
it.** A demand, once recorded, is never withdrawn — but a *subscription* can end, and recomputing the work
set from the surviving subscriptions handles that for free.

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
— the positional answer fold — which `README.md` §"Two commitments" forbids. Its `request_id` survives only as a
routing handle — who asked, so who is told — kept outside the question's identity, or identical questions
stop being one record.

**It does not carry a dependency graph, and the logical layer needs none.** When a handler serving
`report` posts an `asked` record for `loss`, the store cannot tell that from an unrelated querier — symmetric
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
the call table, the registry, the work set and the cursor are the set of live questions, keyed by pattern
and binders, indexed by pattern. The division: *the call table decides who is told; the store decides
what is computed.* Two queriers independently asking the same question post one record — variables are
numbered canonically within a record, so identical questions are identical records — and what remains of
the table is its *routing* role: who is told. That needs a canonical form for constraints, too. Without
one, identical questions fail to coincide, which wastes work — and worse, a producer rule written for one
shape of a question never fires on an equivalent question shaped differently, which costs liveness. Still
nothing unsound.

**There is no `read`, and no syntactic substitute.** A tempting test — *"does the posted term have a free
variable in key position?"* — cannot carry the distinction. It is not invariant under rewriting
(`loss(S,V) ∧ S=60` classifies opposite to the identical `loss(60,V)`), and it presupposes a key/value
split no term carries: `verdict(Outcome, FinalStep)` has two value positions, `provenance(key, Prid, Sha)`
has two key positions. What two verbs were buying is **a declaration of intent that survives rewriting**,
which a syntactic property cannot replace — and `asked` is that declaration, a relation rather than a
syntactic test. The same argument rules out reading which variables a question *binds* off which ones its
constraint mentions: a vacuous `V > -∞` would flip a variable's role without changing the question. Hence
the explicit binders. **Admission control still needs an explicit mechanism**, and the natural signal is a
*quantity*: how many instances does this question range over, and can its residual ever empty?

