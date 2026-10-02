# The logic: definite clauses over constraints

**Layer:** depends on `0-substrate.md`. The dependency graph is in `README.md`.

## The language, and what each restriction buys

The derivation language is **definite clauses** — one atomic head, a body of finite `∧`, `∨` and `∃`, with
constraints from a fixed CLP domain as ordinary body literals. **No `¬`, no `→`, no `∀`** as operations —
a clause's own variables are universal, but that is the reading of a rule, not a connective a body can use;
and no `∨`, no `⊥`, no equality and no `∃` in a *head*. Posted records are the special case whose body is
constraints only (`0-substrate.md` §"The model").

**It is the language the design can check, not the language agents must be written in.** An agent may be
written in anything that can build and read terms and post records; the design constrains only what it
posts and what it reads. What it posts is testimony, under the same honour system as every post. The
definite-clause language is where a derivation's monotonicity — and so everything §"CALM" buys — holds by
construction rather than by the author's care. Code in any other language that derives and posts is a
producer like any other.

**There is no `¬` in the language at all.** The store holds records in relations, and no axiom connects
any relation to any other. A schema may declare two relations to be read as a statement and its denial;
that reading is schema the user chooses and the logic is ignorant of, exactly where sorts sit (§"Types").
Nothing is excepted, because nothing negates.

Variables are written Prolog-style in this document — `V`, capitalised. How they travel is
`0-substrate.md` §"The wire format".

**What is missing, and why — the reasons are two, not four.**

| a richer fragment has | here | why |
|---|---|---|
| arbitrary `∨`, `∃`, finite `∧` **in bodies**; clause-level `∀` | **yes** | — |
| arbitrary `∨` **in heads** | **no** | a disjunctive fact has nowhere to live: this store is a **set of literals**, one model. `φ ⊢ a ∨ b` needs a set of *models*, or the disjunctive chase |
| `⊥` in heads — integrity constraints | **no** | a store that must accept what it is given can only **reject a post** (order-dependent) or **go inconsistent** (§"Constraints are asked") |
| equality in heads | **no** | that *is* a functional dependency — the same reason again |
| `∃` in heads — value invention | **no** | the coordination-freeness proof's quiescence argument **requires** no value invention. A rule minting a fresh variable is excluded, and no posted record is existential either (`0-substrate.md` §"The model") |

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
the ledger's own condition for it, no `≠` in a constraint region, fails as soon as a constraint body uses `≠`, which §"The constraint domain"
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
output."* So a non-monotone computation **inside** an agent is not a violation; **outputting** one would be.

**And their emptiness query is this design's `¬∃`, worked — though the reason is finer than it first
looks.** It is Ameloot's exhibited non-coordination-free construct: since every node may hold part of the
input, the nodes must flood identifiers and check them against the system relation naming all participants.
But Power, Koutris & Hellerstein's Thm. 24 splits the property — a Boolean query is *positively*
coordination-free iff **monotone**, *negatively* coordination-free iff **antitone** — and diagnoses
Ameloot's exclusion of antitone queries as an artefact of the transducer output encoding, where false is an
*absent* tuple. So emptiness is not uncomputable, and this design should not lean on a claim that it is. It
free-terminates **exactly where a witness appears**: the direction that finds an atom, never the direction
that finds none. An emptiness test has no syntax here for that reason.

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
needs a cursor. And it applies to what is **output**, not to what an agent chooses to compute next.

**Which is where single-spawn sits, and it is not an exception.** *"Run iff no other agent is running
this"* is mutual exclusion, not a query, so Ameloot's theorem is silent on it and a roster is needed —
priced in `open.md`. But it is the *general* pattern rather than this design's private embarrassment;
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
  test, and any conclusion that needs to know every participant;
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

## The constraint domain: constraints from a fixed domain

Talking *about* a set of atoms — a region, or anything else a pattern describes — means handling the
variables in a pattern, and there are exactly three ways — **erase** them into a finite tag (adornments,
fixed at compile time), **delegate** them to the metalanguage (exponentials), or **represent** them as a
term plus a `denote` relation (quotation). The axis underneath is *when the pattern vocabulary is fixed*.

**Take the middle course between erasing and representing: CLP.** A pattern carries constraints from a
**fixed domain**, with a solver deciding satisfiability. `p(X, Y), Y ≤ 100` has shape *"range
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
step. On a half-bounded axis the ascent does not terminate at all. **But that is an algorithm
mismatch, not a property of the problem**: `X > Y > X` is a system of **difference constraints**, decided
by negative-cycle detection in `O(V·E)` independent of magnitude. A propagator-producer may run
Bellman–Ford, exactly as any sound backend is allowed.

What it costs is **incompleteness**: entailments a search would have found go unreported, so a pattern that
*is* covered may not be recognised and redundant work happens. That is the right trade — incompleteness
costs work, search costs soundness. And it draws a clean line: propagation's narrowing is **real
information**, monotone, and may be posted; labelling's is **conditional** and must never be.

**Propagation happens inside one record or one rule body**: the solver simplifying that unit's own
constraints. It needs no unknown shared across records. (Narrowing what is known about a value *across*
records is a different act — posting what has been excluded — and needs a reading of records as denials,
which is schema above this layer.)

**And then the solver is not a component — it is producers.** A propagator watches, derives and posts
regions; with labelling banned that is all it does. Its rules are ordinary positive rules with constraint
bodies, monotone, and their output only accumulates. Entailment checking is an ordinary query; fixpoint is
what the evaluator does anyway. So *"which constraint domain"* is not a separate decision; it is *"which
rules do you write"*, and they run where the data is.

Several propagators over the same constraints **compose without coordination**, since each posts only what
it derived, and the tightest bound is a read. A propagator that derives *less* than the rules would is simply
incomplete, which is safe.

**`all_different` is worth checking because it looks like it should fail: it does not.** Disequality is
legal inside a constraint body, and narrowing from it (`X ≠ 3` with `X ∈ {2,3,4}` gives `X ∈ {2,4}`) is
positive derivation within the record or rule body that states it. Only the *state test* — *"are these
currently different?"* — is forbidden, and propagation never needs it. **Entailment claims are monotone; membership claims on the solution set are
not.**

**The boundary worth stating.** Everything stays first-order while the constraint *vocabulary* is fixed.
Opening it, so a pattern's shape is computed at runtime, buys **reflection** and its costs. What genuinely
needs it is the system reasoning about itself — patterns about patterns, or producers advertising *"I serve
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
store holding `p(k, a)` and `p(k, b)` faces two exits, not three:

| | |
|---|---|
| **refuse** the second post | order-dependent — whoever arrives first wins, contents depend on timing, and admissibility becomes a function of state, so there is no fixed algebra at all |
| **record** | the pair; or the derived consequence `eq(a, b)`; or both — and every one of these is an **observation** |

There is no explosion exit. Ex falso would need an interpreted `=` for the derived equation to contradict,
and the logic has none (§"The language"): `eq(a, b)` is an ordinary ambient-ambient equality —
testimony, folded or declined per reader policy. The records stay; what a folding
reader sees is a coarser quotient, and what a declining reader sees is two values. **Deriving the
consequence *is* recording an observation.**

So the store does not forbid asserting a constraint — **it renders assertion into testimony.** Write the
dependency as a rule and what it derives is equalities that readers weigh; they are ambient-ambient, hence
declined by default, so the constraint constrains exactly the readers who opt into it. The store cannot be
commanded, only informed.

> **A constraint is something a reader may ask about, never something the store asserts.**

**This is also what keeps the contextuality foreclosure intact** (`README.md` §"Two commitments"). An enforced
dependency is precisely the construct that would make two local views pairwise-consistent yet unglueable —
`{p(k, a)}` admissible, `{p(k, b)}` admissible, their union not. Rendered into testimony, the union is
always admissible, and what would have been a gluing failure is a pair of records and a derivable
equality, readable. Gluing never fails at the atom layer *because* nothing there can be violated.

**And the affirmable half is the negative one.** *"This relation is not functional at this key"* is a
positive existential over a growing set — monotone, decidable:

```
conflicted(K) :- p(K,V1), p(K,V2), V1 ⊔ V2 undefined.
```

Writing that rule as though it were generic is a defect: it hardcodes position 1 as key and position 2 as
value, and no term carries such a split — `verdict(Outcome, FinalStep)` has two value positions,
`provenance(key, Prid, Sha)` two key positions. Written honestly it is one declaration among many, made
per relation by whoever knows which positions determine which.

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
Keeping the atoms apart keeps it out of reach. A "broken" flag is the same defect in different clothes:
discarding values and recording a bit is the one operation that moves *down*, and it retracts.

*Nothing needs broadcasting.* Ask whether a reader who already got `f(a)` must be told when `g(b)` arrives.
A **threshold** claim is still true; an **exact** claim — *"this is the only value"* — was never
available from the records alone. **No legitimate claim is invalidated by a conflict**, so there is
nothing to push and no registry of past contributors to keep. The store owes something *readable* — a
queryable predicate for a declared conflict — never a notification.

**And conflict is reachable without forgery**, which is why it has to be designed for: two honest
producers whose results differ in the last bit do not reconcile.

**What this costs, and where it does not.** The rejection applies only where the order is **partial** in
the sense that some pair has *no least upper bound*. Any join-semilattice may be quotiented at storage,
freely and soundly: `a ⊔ b ⊒ a` and thresholds are upward-closed, so nothing either post satisfied can
stop being satisfied. **But off a chain, the join *synthesizes*** — `{a} ⊔ {b} = {a,b}`, and nobody said
`{a,b}`. That is not unsoundness; what it costs is **provenance**, since on a chain the stored value is
always one somebody posted.

Where the order *is* partial there are exactly two completions: add a `⊤` (total and compressing, and it
explodes) or take the **powerset** (total and sound, and it does not compress). The design takes the free
completion, which is what "keep the atoms" has meant throughout.

## Types: many-sorted, per functor, structural

Each functor declares the sorts of its arguments and its result. The signature belongs to the
**program**, never to the data — no type is inferred from whoever posts first, which would make the type
check a first-writer-wins register decided by arrival order.

**Sorts are per functor and there is no untyped escape hatch.** A single flat relation —
`attr(Name, K, V)` for every attribute — looks like it buys an open namespace and is unsound: one functor
has one signature, so every value position shares a sort, so `attr(size, K, X)` and `attr(done, K, X)` may
share `X`. That aliases an integer slot to a boolean one with nothing to object. Per-functor signatures
reject it at the alias. (A flat relation per *sort*, with the name as data, avoids the alias; it is the
right shape exactly when the names are an open set, chosen at runtime — otherwise a known name belongs
where a tool can check it.)

**Wrappers recover what the flat relation was for, as schema rather than machinery.** Per-functor sorts
make `size/2` and `done/2` different relations, so *"every attribute"* would be `∃F. F(K,V)` — not
first-order. A user wraps, and nothing in the engine changes:

```
attr(size(K, V))           -- Attr = size(Key,Int) | done(Key,Bool) | …
at(K, size(V))             -- Attr = size(Int)     | done(Bool)     | …
```

The first buys **rangeability**; the second **hoists the key out**, making it a shared position so
*"everything at key k"* is one pattern — which fixes, in the user's own schema with zero engine support,
the defect that positional indexing over heterogeneous terms exhibits.

With a static signature a type conflict **is not expressible at runtime**. It is a program that does not
typecheck, caught twice: locally before anything is sent, and again on receipt, because at an
honour-system boundary a peer's message is never trusted. Neither check coordinates, and the reason is
precise: **a type error is a property of the message alone** — no store state is consulted — so rejecting
it is order-independent. That is exactly what a *value* conflict is not.

Agreeing the signature is deployment, not runtime. **The residual is real:** if signatures were themselves
data, the problem returns unchanged. And it is more than a typing discipline: a region's set of ground
atoms is computed against a signature, so two agents disagreeing about the signature disagree about what
any region covers.

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

