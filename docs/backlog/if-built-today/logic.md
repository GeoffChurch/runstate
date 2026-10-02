# The logic: definite clauses over constraints

**Layer:** depends on `substrate.md`. The dependency graph is in `README.md`.

## The language, and what each restriction buys

The derivation language is **definite clauses** — one atomic head, a body of finite `∧`, `∨` and `∃`, with
constraints from a fixed CLP domain as ordinary body literals. **No `¬`, no `→`, no `∀`** as operations —
a clause's own variables are universal, but that is the reading of a rule, not a connective a body can use;
and no `∨`, no `⊥`, no equality and no `∃` in a *head*. Posted records are the special case whose body is
constraints only (`substrate.md` §"The model"). **Questions** are a separate, read-side language — positive formulas with
explicit `∃` and `∀` — and are never told, only asked.

**The order is: ground terms, then variables, then the valuation, then quantifiers.** Truth-bearers are
ground atoms, each with a status; quantifiers range over statuses, in questions and reads, and a quantified
sentence is never told and has no status of its own. Told quantified sentences would give a sentence two
sources of truth — told directly, and derived from its instances — and would make polarity interact with
the quantifiers, since `¬∀` is `∃¬`. Tarski's order is the same: quantifiers are defined through
satisfaction, which presupposes a valuation of atoms.

**There is no `¬` in the language at all, and no polarity either.** The store holds records in relations.
Polarity is one relation a schema may declare, `told(A, P)` with `P ∈ {pos, neg}`, and nothing connects
`told(A, pos)` to `told(A, neg)` — so polarity sits exactly where sorts sit, as schema the user chooses and
the substrate is ignorant of (§"Types"). A user who declares no `told` has ordinary relations and no
four-value reading; a user who declares it gets it.

`¬Q` is therefore **notation in this document** for `told(Q, neg)`, not a construct the language contains.
Nothing is excepted, because nothing negates.

The same split covers variables. In this document they are written Prolog-style — `V`, capitalised — and on
the wire a variable travels as a **constructor**, `var(37)`, an ordinary term with a known functor, scoped
to the record it occurs in (`substrate.md` §"The model"). Neither a capital letter nor a sigil is wire format, because both
make a receiver decide variable-ness from the spelling of a name — the closed-set-in-an-open-namespace error
the polarity representation (`polarity.md` §"Polarity is schema, not substrate") exists to avoid.

**What is missing, and why — the reasons are two, not four.**

| a richer fragment has | here | why |
|---|---|---|
| arbitrary `∨`, `∃`, finite `∧` **in bodies**; clause-level `∀` | **yes** | — |
| arbitrary `∨` **in heads** | **no** | a disjunctive fact has nowhere to live: this store is a **set of literals**, one model. `φ ⊢ a ∨ b` needs a set of *models*, or the disjunctive chase |
| `⊥` in heads — integrity constraints | **no** | a store that must accept what it is given can only **reject a post** (order-dependent) or **go inconsistent** (§"Constraints are asked") |
| equality in heads | **no** | that *is* a functional dependency — the same reason again |
| `∃` in heads — value invention | **no** | the coordination-freeness proof's quiescence argument **requires** no value invention. A rule minting a fresh variable is excluded, and no posted record is existential either (`substrate.md` §"The model") |

So three omissions are one reason — **the store must accept what it is given** — and the fourth is CALM's.
None is taste.

**Which names the fragment rather than subtracting toward it: regular logic with atomic heads.** Regular
logic is `⊤`, `∧` and `∃` — the internal logic of regular categories — and the ladder runs **cartesian**
(`∃` only where provably unique) ⊂ **regular** ⊂ **coherent** (adds `⊥` and finite `∨`) ⊂ **geometric**
(the `∨` made infinitary). Definite clauses sit inside regular: bodies are conjunctions, body-only
variables are an antecedent `∃`, heads are atoms.

**Each step up is priced, and the prices differ in kind.**

| step | admits | costs |
|---|---|---|
| drop the atomic-head restriction | `∃` in heads — tuple-generating dependencies | Skolemising keeps the least model, so every result here survives; the chase need not terminate, and against a constraint domain a head `∃` can assert a witness nothing satisfies |
| → **coherent**, via `∨` | `∨` in heads | **the least model.** Minimal models instead, so truth stops being witnessed by a record. A boundary, not a price |
| → **coherent**, via `⊥` | goal clauses | monotonicity — a denial is not preserved under homomorphisms |
| → **geometric** | infinitary `∨` | nothing new in kind: in heads the same boundary, in bodies infinitely many rules |

**So the fragment is chosen by two requirements and one further restriction, not by a list of refusals.**

- **A least model** — every read here is *"is this witnessed?"*, a threshold, a settledness claim, a
  residual, and `∨` in heads is precisely the construct that destroys it.
- **Preservation under homomorphisms** — which is what excludes `⊥` from heads: a homomorphism may identify
  terms and so make a denial's body hold, where nothing positive can be falsified that way.
- **Atomic heads**, a restriction rather than a requirement, since `∃` in heads is compatible with both of
  the above. It is taken for the **no-value-invention** property the proof depends on: a conclusion is
  built only from terms already in the body, so no fresh element is ever minted.

**The first two are independent, and Makowsky is why both must be named**: *"a first-order theory admits
initial models iff there is a set of definable partial functions such that adding those functions to the
vocabulary of `T` gives us a theory `T₁` which is equivalent to a **universal Horn** theory"* (JCSS 34,
1987). Initiality alone reaches **Horn**, which still admits denials. Definite is where the two meet.

Read downward, the first buys the tractable corner (PTIME, against disjunctive Datalog's `Σ₂ᵖ`), and
monotonicity — which definite clauses have by construction — buys membership in the coordination-free class
(§"CALM"). Nothing sharper is claimed. Placement *strictly inside* that class, in Ameloot's `H`, is stated
in homomorphisms of **models**, where the second requirement means algebra homomorphisms on **terms**; and
the ledger's own condition for it, no `≠` in a constraint region, fails as soon as a constraint body uses `≠`, which §"The demand language"
permits.
`../definite-clause-maximality.md` works the boundary out.

**And one connective is absent from the logic rather than priced on the ladder: interpreted equality.**
Regular logic ordinarily includes a substitutive `=`; here equality is a **convention** — an ordinary
posted relation between terms, whose congruence closure is a *reading* a reader opts into per trust
policy. Variable repetition in a body still joins on syntactic coincidence, which the logic's diagonal gives
free. No equality is folded by default: the default reading is the finest congruence (§"Sort closure"). An
equality can arrive from anywhere, and a single folded one makes every class it touches global — which is
why folding is opt-in, and why FD-violation detection stays a reader-side query (§"Constraints are asked")
rather than a derived equality. (An earlier draft folded equalities on *fresh* names by default, on the
theory that they stay local. Transitivity defeats that: `v = 0.5` and `v = 0.4` are each fresh-sided, and
together they equate two ambient values. Record-scoped variables remove fresh names from the store
altogether.)

**Whether the fragment is *forced* rather than chosen is asked separately** in
`../definite-clause-maximality.md`, via a third route — preservation under algebraic homomorphisms. Its
partial answer: preservation draws the **outer** boundary (it rules out `¬`, and rules out goal clauses
because a homomorphism can make a denial's body hold), while `∨` and `∃` in heads *are* preserved and are
excluded by this design's own commitments instead. So the omissions above have two different kinds of
cause, and the section does not currently distinguish them.

⚠️ **Terminology**: *Horn* means "at most one positive literal" in logic programming — admitting goal
clauses — and in the categorical hierarchy means formulas from `⊤`, atoms and finite `∧`, which already
excludes `⊥` and `∨`. Say which is meant.

The shape has a reason rather than an axiom (Vickers, *Topology via Logic*; the affirmability reading is
Smyth's): **an open set is an affirmable property** — confirmable in finite time from finite information,
never refutable from it. You may conjoin *finitely many* observations, because each takes finite time; you
may disjoin *arbitrarily many*, because any one suffices.

**And that fixes the evaluation strategy, which is otherwise easy to leave implicit.** Body disjunction is
carried as **branches rather than backtracked over** — arbitrary `∨` means alternatives accumulate, they
do not get retried — and a conjunct **filters** the branches it meets, which is frame distributivity
(`a ∧ ⋁bᵢ = ⋁(a ∧ bᵢ)`) and, operationally, the list-monad bind. Monotone-iff-coordination-free is the
same shape once more, as **Scott-continuity**: a function that commutes with directed joins is one whose
answer on the limit is the limit of its answers, which is what lets a lagged replica be right rather than
merely close. Continuity is the stronger property in general; the two coincide for a finitary query, and
every definite program's consequence operator is one, since a body reads finitely many facts.

**Why `¬` had to go.** To affirm `φ` you need a finite observation. To affirm `¬φ` you must rule out
*ever* affirming `φ`, which is a survey of everything there is. That survey is **exactly what
coordination-freeness excludes** (§"CALM" — the predicate is binary, and nothing here is priced in
rounds). So "no negation," "opens are affirmable," and "monotone ⟺ coordination-free" are one
constraint set in three vocabularies, reached by **two** routes rather than one fact: the first two are one
fact (`README.md` §"What makes the answer worth having"), and the third arrives from distribution, on different
premises — give nodes knowledge of the partition and the coordination-free class grows (§"CALM"), where
affirmability does not move.

**The derivation/report split is the open/closed split.** Derivation affirms; reporting refutes. They
cannot mix, for the same reason the complement of an open is not open.

| | negation? | may feed demand? |
|---|---|---|
| **derivation** — what to produce | no | yes |
| **reporting** — what is missing, what is best, what diverged | **yes, inherently** | **no** |

`argmax` is therefore not expressible in derivation, so a bandit's one non-monotone step is forced to the
boundary. Its monotone half stays inside: `beaten(A, V) :- value(A, V), value(_, V2), V2 > V` only ever
grows. It is about a **value**, not an arm, because with no functional dependency (§"Constraints are
asked") an arm may carry two values, and *"is this arm beaten?"* is then not well-posed — beaten on one
value, unbeaten on the other. Per value it is exact, and needs no disequality: an arm's lower value
beaten by its own higher one is simply true.

**One exception is real and does not repair.** `ensure`, the library's core operation: its loop condition
is a threshold claim on `progress`, a *retractable* quantity, and its two termination guards are a
**temporal delta** (*"nothing new was derived"*, which has no positive form) and an **inflationary
fixpoint test** (*"another lap can only reproduce them"*). Neither is expressible in the fragment — each
compares two moments, which no growing set of facts can do — and both feed demand, because both decide
whether to relaunch. §"Two layers" is where that belongs.

## CALM: why monotonicity, and not merely for tidiness

**Consistency As Logical Monotonicity** (Hellerstein 2010; proved by Ameloot, Neven & Van den Bussche,
PODS 2011 / JACM 2013, **Cor. 13**): *a query has a coordination-free distributed implementation **iff**
it is monotone.*

That converts a preference into a requirement. If a querier is far from the data, then **monotone** ⟹
information flows one way, no ownership protocol, no consensus; **non-monotone** ⟹ you must know you have
seen everything, which is what coordination is for.

**Scoping notes, several of them repairing a natural over-reading.**

**It is relative to a model, and the model is the one where nobody knows the distribution.** Cor. 13 holds
where the partition is arbitrary and *unknown to the program*, with asynchronous fair runs and
**non-retractable output** — the last being a hypothesis of the model, which an append-only store happens
to satisfy. Give the nodes knowledge of the partitioning policy and the class grows; give them the global
active domain and **every computable query is coordination-free**. So the `iff` is a statement about
ignorance, not about queries alone.

**Which puts a line in front of this design's own placement story.** Content-addressed placement *is* a
partitioning policy, and *"is this my address?"* is exactly the decision oracle that moves a system out of
the model the `iff` is stated in. Replicating by address is fine; **concluding absence from ownership is
not.**

**"Coordination-free" is weaker than it sounds *in the transducer formalism*.** There the definition is
*existential over placements* — for every input there **exists** some partition on which the computation
quiesces with no messages — not a promise that a real run sends none. The authors warn against that reading
and exhibit a coordination-free transducer that communicates on the obvious placement. So in that setting
*"no round trips"* is not licensed, and neither is pricing coordination in rounds.

**But the property is not the formalism, and the 2026 restatement is stronger.** Hellerstein's *Complete
CALM* moves the criterion off programs onto **specifications** — a triple `(E, Obs, ≼)`: an event universe,
a map from histories to admissible outcomes, and a *declared* order where `o₁ ≼ o₂` means `o₂` refines `o₁`
**without contradicting it**. Monotone means every outcome admitted now still has a refinement admitted at
every causally later history (Def. 8), and Thm. 1 is *"coordination-free iff monotone."* Its operational
form (Def. 9) is a genuine responsiveness guarantee: a response is *"enabled **immediately** … without
requiring any further input action"* at that process. Not *"no messages are sent"* — the sufficiency
proof's protocol gossips on every event — but **no answer ever blocks on one**, which is the property this
regime actually needs.

**What that buys, free.** Independently chosen answers at different agents are **jointly consistent with no
agreement protocol between them** — Remark 2, and it is *"not an additional assumption … a free consequence
of monotonicity applied to the full history."* Two agents answering from disjoint causal views cannot
contradict each other. That is the *"without stopping to confer"* of `README.md` §"What it is for", proved rather than
asserted.

**What it does *not* buy, and this design has it for a separate reason.** Coordination-freedom is not
convergence. The transducer model computes a common output set, so replica agreement is built into that
formulation; at the specification level the two come apart (§6.1). Whether agents *converge* is a
structural property of `≼`: with joins, monotonicity implies convergence; without them it gives *"safe
independent action but not convergence"* (§7.4). Here `≼` is set inclusion, which has joins — so this
design gets both, and gets the second **from the shape of the order rather than from the theorem**. A
reimplementation that changed the order would have to check it again.

**The theorem constrains the computed query, never the operators.** Every query distributedly computable
by a first-order transducer is computable by one using negation internally, and the proof of the monotone
case reads *"we use deletion to start afresh; since the query is monotone, no incorrect tuples are
output."* So the residual computed **inside** an agent is not a violation; **outputting** one would be.

**And their emptiness query is this design's `¬∃`, worked — though the reason is finer than it first
looks.** It is Ameloot's exhibited non-coordination-free construct: since every node may hold part of the
input, the nodes must flood identifiers and check them against the system relation naming all participants.
But Power, Koutris & Hellerstein's Thm. 24 splits the property — a Boolean query is *positively*
coordination-free iff **monotone**, *negatively* coordination-free iff **antitone** — and diagnoses
Ameloot's exclusion of antitone queries as an artefact of the transducer output encoding, where false is an
*absent* tuple. So emptiness is not uncomputable, and this design should not lean on a claim that it is. It
free-terminates **exactly where a witness appears**: the direction that finds an atom, never the direction
that finds none. `= {f}` has no syntax here for that reason.

**One property worth claiming, which the theorem licenses.** Monotone ⟺ computable without the
all-participants relation **and** without self-identity. So this design needs **no party roster and no
self-identity**: parties may join with nobody told. (*"Network membership"* is Hellerstein & Alvaro's
phrase for it, not Ameloot's — the theorem is Ameloot's, the wording theirs.)

**And the roster is not one obstruction among several — it is the only one.** *Complete CALM* Remark 3:
*"membership knowledge is the single non-monotone input that renders all subsequent computation
monotone,"* generalising Ameloot's non-oblivious result; Example 6 decomposes consensus into exactly that
shape, a non-monotone membership phase followed by monotone vote-counting; and Thm. 4 shows coordination
can **always** be factored into a membership authority plus an ordering service. So *"what needs a roster"*
is not a list to be enumerated — it is one item, and everything downstream of it is free.

Two further scoping notes. It is a **safety** statement: a lost demand and a slow handler leave a querier
in byte-identical states, so *liveness* still needs an acknowledgement, and a bounded reconnect still
needs a cursor. And it applies to the **answer** relation; demand is control.

**Which is where single-spawn sits, and it is not an exception.** *"Run iff no other agent is running
this"* is mutual exclusion, not a query, so Ameloot's theorem is silent on it and a roster is needed —
priced in §Open. But it is the *general* pattern rather than this design's private embarrassment;
Hellerstein's own reading is that *"the architecture of Paxos-based systems reflects this: membership is
configured once; everything downstream is actually coordination-free."* And it is once — *"membership
establishment need only happen once … after the initial bootstrap, the chain of authority transitions is
monotone."* Two things stay true together: the answers are jointly consistent with no agreement, **and**
two agents may separately spend six hours computing the same one. Only the first was ever a claim about
correctness.

**And none of what follows needs the theorem at all.** The criterion above is semantic — its proof is
immediate from the definitions, which is the point of the framing rather than a weakness — but the
properties below do not appeal to it, to a model of the network, or to any hypothesis about placement.
They follow from the shape of the store.

**A partial store is sound, never wrong.** `S' ⊆ S` implies everything derivable from `S'` is derivable
from `S`, so a crash mid-write leaves a subset, and every subset is a valid store: no repair pass, no torn
state, no reconciliation. The contrast is the point. Under retraction that same subset may hold a fact
whose retraction has not arrived, so a lagging replica is not an incomplete view but an **incorrect** one,
and it has no way to tell which it is.

**Delivery gets its properties free.** Merge is union — idempotent, so at-least-once delivery gives
exactly-once semantics with no dedup table; commutative, so reordering needs no sequencing; associative, so
batching is arbitrary. That is not an analogy to CRDTs but an instance of them: Prop. 4 — *"any
specification whose updates are inflationary in a join-semilattice and whose outcome order is the lattice
order is monotone"* — and a grow-only set under union satisfies the hypothesis exactly. Slow and lossy
messages are the premise of the regime, and none of this has to be built.

**The converse is the whole bet in one picture.** *"A replicated counter with a **reset** operation is not
inflationary, hence not monotone — coordination is required to implement reset consistently"* (§7.3).
**A reset is a retraction.** Everything this design refuses, and everything it gets for refusing, is that
sentence at scale.

## Two layers, and what crosses between them

Not everything can be monotone, and nothing is gained by pretending. The shape that works is **a
non-monotone core underneath a monotone layer**, where each pushes as much as it can downward:

- the **core** makes observations the logic cannot: an OS probe, a clock, a temporal delta, a fixpoint
  test, and the closed-producer-set conclusion of `polarity.md` §"Falsity is told";
- the **monotone layer** derives over them and owns everything it can.

**The boundary is not a convenience — and it has a name.** *Complete CALM* §4 calls this **proper
coordination**: *"coordination is a means, not an end. A system may use coordination internally to resolve
a non-monotone specification, producing a monotone output interface for downstream consumers."* Def. 11
makes it precise — restrict the admissible outcomes enough to restore monotonicity, then test the residual.
Everything above the line here is monotone and coordination-free; everything pushed below is exactly what
is not. Three constructs have now tried to get in and been reclassified rather than accommodated, and the
layer needed no change in any case.

**And there is a barrier here that this design steps around by construction.** Thm. 3:
relational-transducer CALM *cannot in general verify* proper coordination — a non-monotone specification
implemented in Datalog must contain negation, adding coordination rules leaves the negation in place, so
the syntactic check false-negatives, and deciding monotonicity in stratified Datalog is undecidable. The
claim above is therefore checkable only at the **specification** level, not with Cor. 13. What rescues it
here is that the non-monotone work never enters the program: there is no stratified negation to defeat the
check, because there is no negation. **Separating rather than stratifying is what keeps the residual
syntactically evident** — which is what the next paragraph's rule is really for.

**What crosses is literals**, and the mechanism is already in this repo — `../prolog-query-layer.md` says of
the one existing case, *"pass the probe result in as a parameter rather than calling out."* The
generalisation is the repo's recurring repair applied to oracles: **timestamp the observation and make it
a fact about the past.** *"Nothing changed between T₁ and T₂"* is permanently true once observed, where
*"nothing has changed"* never is.

Two consequences. The core should be as small as possible, and every primitive it hands upward is one the
monotone layer gets to reason about. And because the timestamps are **per-observer** — clock skew already
breaks `last_activity` in the measurements — the literals must carry *who observed, by whose clock*, and
any comparison across observers is a report rather than a derivation.

**Which clock, for which question.** Three questions get conflated, and the one that cannot be answered is
the one nothing here asks.

- **Order** — *did A precede B?* — needs no physical clock at all. Message counters with acknowledgement
  give the causal partial order exactly, and §CALM already requires that machinery for an unrelated
  reason: liveness needs an acknowledgement, a bounded reconnect needs a cursor. The counter is the cursor.
- **Duration** — *has it been five minutes?* — is what liveness and staleness genuinely need, and it is a
  **local** question, so a local monotonic clock answers it.
- **Absolute time** — *when, in UTC?* — is needed for nothing here, and is the only one unobtainable.
  Morton §4: the order on events is unknowable at short timescales because the clocks themselves are wrong,
  so an eventstamp is honestly an **interval** `[t_s, t_e]`, and strict causality holds only where the gap
  exceeds light-travel time. Even TrueTime, which he adopts, is an engineered guarantee that *"could
  fail"*, not a proved one.

**And the local stamp must come from a monotonic source, which is not automatic.** A wall clock steps — NTP
corrections, leap seconds — so `T₁ < T₂` can be false on a *single* machine, making *"nothing changed
between T₁ and T₂"* wrong under the very rule meant to make per-observer stamps safe. The core's clock
primitive reads a monotonic source or the repair does not hold.

CCP (`ask`/`tell` over a monotone store) is the right model for the *semantics*; CHR is the right model
for the *execution*, where simplification keeps the store small provided the body entails the head. One
caution: CHR's store is a multiset, and idempotence is what makes one-way replication safe — use set
semantics.

## The demand language: constraints from a fixed domain

Talking *about* demands means handling the holes in a pattern, and there are exactly three ways —
**erase** them into a finite tag (adornments, fixed at compile time), **delegate** them to the
metalanguage (exponentials), or **represent** them as a term plus a `denote` relation (quotation). The
axis underneath is *when the demand vocabulary is fixed*.

**Take the middle course between erasing and representing: CLP.** A demand carries constraints from a
**fixed domain**, with a solver deciding satisfiability. `loss(V,S), S ≤ 100` has shape *"range
constraint"* and data `100`. The constraint is an ordinary positive literal in the body, so the logic is
untouched and stays first-order and decidable. Adornments are the degenerate case where the domain is bare
equality.

**An `asked` record does hold its question as a term, binders included — and that is not the
representing course.** Representing buys reflection because a user rule may *interpret* a quoted demand
through `denote`. Here no rule does: a producer's rule matches a question of a fixed shape it was written
for, as it would match any term, and the one thing that interprets a question — deciding whether it is
settled and what its residual is — is the engine's coverage check, over a fixed question language:
positive formulas, explicit `∃` and `∀`, constraints from the fixed domain. The vocabulary is fixed at
design time, which is the axis this section turns on.

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
information**, monotone, and may be posted; labelling's is **conditional** and must never be.

**Propagation happens in two places, and neither needs an unknown shared across records.** *Inside one
record or question*, it is the solver simplifying that record's own constraint body — the coverage check
and the residual run it. *Across records*, narrowing what is known about a keyed value is posting
**negative regions** about it: a producer that has bounded the loss at step 61 to `[0.25, 0.35]` posts
`told(metric(r, loss, V, 61), neg)` under `V < 0.25` and under `V > 0.35`. Those accumulate, the tightest
bound is the union of what has been excluded, and a threshold question — *"is the loss at 61 below 0.4?"*
— settles the moment the excluded regions cover everything above it. The key, not a variable, is what
ties the narrowings together.

**And then the solver is not a component — it is producers.** A propagator watches, derives and posts
regions; with labelling banned that is all it does. Its rules are ordinary positive rules with constraint
bodies, monotone, and their output only accumulates. Entailment checking is an ordinary query; fixpoint is
what the evaluator does anyway. So *"which constraint domain"* is not a separate decision; it is *"which
rules do you write"*, and they run where the data is.

Several propagators over the same key **compose without coordination**, since each posts only regions it
derived, and the tightest bound is a read. A propagator that derives *less* than the rules would is simply
incomplete, which is safe.

**`all_different` is worth checking because it looks like it should fail: it does not.** Disequality is
legal inside a constraint body, and narrowing from it (`X ≠ 3` with `X ∈ {2,3,4}` gives `X ∈ {2,4}`) is
positive derivation within the record or question that states it. Only the *state test* — *"are these
currently different?"* — is forbidden, and propagation never needs it. **Entailment claims are monotone; membership claims on the solution set are
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

## Constraints are asked, never asserted

The tempting move is to declare a relation functional — *"for each key, exactly one value"* — so the store
can combine posts and compress. It should be resisted, and the reason is not the one it first appears.

**It is not a syntactic problem.** `p(X,Y) ∧ p(X,Y') ⊢ Y = Y'` is a perfectly good sequent; the `∀` and
`→` live at the sequent level, which theories permit.

**The problem is what asserting it would have to do to a store that must accept what it is given.** A
store holding `loss(60,0.5)` and `loss(60,0.4)` faces two exits, not three:

| | |
|---|---|
| **refuse** the second post | order-dependent — whoever arrives first wins, contents depend on timing, and admissibility becomes a function of state, so there is no fixed algebra at all |
| **record** | the pair; or the derived consequence `eq(0.5, 0.4)`; or both — and every one of these is an **observation** |

There is no explosion exit. Ex falso would need an interpreted `=` for the derived equation to contradict,
and the logic has none (§"The language"): `eq(0.5, 0.4)` is an ordinary ambient-ambient equality —
testimony, disputable to `{t,f}`, folded or declined per reader policy. The records stay; what a folding
reader sees is a coarser quotient, and what a declining reader sees is two values. **Deriving the
consequence *is* recording an observation.**

So the store does not forbid asserting a constraint — **it renders assertion into testimony.** Write the
dependency as a rule and what it derives is equalities that readers weigh; they are ambient-ambient, hence
declined by default, so the constraint constrains exactly the readers who opt into it. The store cannot be
commanded, only informed.

> **A constraint is something a reader may ask about, never something the store asserts.**

**This is also what keeps the contextuality foreclosure intact** (`README.md` §"Two commitments"). An enforced
dependency is precisely the construct that would make two local views pairwise-consistent yet unglueable —
`{loss(60,0.5)}` admissible, `{loss(60,0.4)}` admissible, their union not. Rendered into testimony, the
union is always admissible, and what would have been a gluing failure is a **conflicted equality atom**,
readable. Gluing never fails at the atom layer *because* nothing there can be violated.

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
position 1 as key and position 2 as value, which is exactly the split `questions.md` §"There is no `read`" says no term
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
A **threshold** claim is still true; an **exact** claim — *"this is the only value"* — was never available unless someone vouched for the key. **No
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

## Sort closure, and disequality

**Disequality is a constraint-domain predicate, never a logical connective**, so it was never in the
derivation layer. What it costs is preservation: `≠` is not preserved under homomorphisms, because a
homomorphism may identify two constants.

**And the Unique Name Assumption is not needed — it dissolves into a lattice of lenses.** `a ≠ b` is
licensed only by UNA — *different names denote different things* — one of the three components of Reiter's
CWA formalisation. This design assumes nothing of the kind, anywhere. The substrate needs only **syntactic
record identity** — tree-equality of posted records, for dedup; set semantics is about records, not
denotations, and the wire obligation it implies is a canonical encoding, not a semantic assumption. Every
*denotational* sameness is a **reader's congruence**: the finest, or posted equalities folded per policy,
or a non-free theory quotient nobody posted (`0.50 ≡ 0.5`, units) — a producer that normalises before
posting has simply chosen one at write time. Congruences form a lattice, and the finest is **initial**:
every reading is a quotient of it, which is why it serves as the default — not privileged, just the one
everything factors through, and the only one whose closure stays bounded (§"The language"); every
coarsening buys merges and pays closure. What Reiter needed as an *axiom*, because his reading was
two-valued and global, appears here as the initial object of the lens lattice, assumption-free.

**No sugar is provided for `≠`, and that is a decision rather than an omission.** The tempting replacement
— `X ≠ a` over closed `{a,b,c}` written as `X = b ∨ X = c` — is not an equivalence: the disjunction is
monotone and survives coarsening, the disequality is **antitone** and does not, and they come apart at
exactly the merge a folded equality performs. Sugaring an antitone test would be the opposite of *unsound
things are unwritable*. A rule needing case analysis over a closed sort writes the **positive disjunction
directly**, says which cases it means, and survives every lens; nothing writes `≠` and nothing translates
it. And the one place `≠` looked forced — the residual over an unbounded axis — **decomposes into
intervals**, which are `≤`/`>`. `all_different` above is already the closed case.

## What stays outside, and it is not a meta level

Two things, both already named. The **non-monotone core** (§"Two layers"): a clock reading is not logic,
and the existing repair applies unchanged — date the observation and it becomes a permanent fact about the
past, which is precisely what a `clock_skew(bob, t₁, t₂, δ)` ground *is*. And the **reader's verdict**:
folding evidence into acceptance is not a statement, so it is neither a fact nor derivable. It sits outside
because it is a **decision**, not because it is meta. No stratification is needed anywhere above the base,
and none is used.

