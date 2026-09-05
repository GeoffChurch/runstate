# If built today: a monotone store of polarised literals

**Status:** a design in its own right, not a migration plan. It is written standalone — the question is
whether *this* is a good design, not whether it beats what exists.

**What it is for.** Several agents, spread over hosts, cooperating on work that takes hours and may die
partway: producing values, asking each other for values, and having to agree about what has been produced
and what never will be.

**The regime is a ratio.** Morton (2017) names it the **slow inconsistent regime**: *slow* — analysis
happens on the same timescale on which information is collected and transmitted; *inconsistent* — agents
*"because analysis is slow never reach consensus,"* and may hold irreconcilably different views. The test
is checkable and scale-free. Four hosts running six-hour jobs are in it for the same reason star systems
are: the answer is wanted while the thing it is about is still changing. Only the constant differs, and
the interstellar case is merely where the ratio is impossible to argue with.

The hard part is not the producing. It is that the data is elsewhere, messages are slow and lossy, agents
come and go, and everybody must still reach the same answer without stopping to confer.

**Companions.** `if-built-today-decisions.md` records what was tried and withdrawn, so this file can
state conclusions. `../if-built-today-citations.md` is the verification ledger — it marks each citation
**CONFIRMED** (read in primary source), **UNVERIFIED**, or **UNOBTAINED**, and anything below that is not
CONFIRMED there should be read as unchecked. `../dead_ends/topological-framings.md` records three refuted
framings so there is not a fourth.

## Two commitments, and everything follows from them

**Nothing is ever retracted.** Every record is permanent — not because a log is a convenient
implementation, but because *retraction is what costs coordination*, and there is a theorem saying so
(§"CALM"). Monotonicity is the load-bearing property; append-only is one way to get it and not the only
one.

**And a second theorem, about a different loss.** CALM prices retraction in coordination: you may have it
if you pay. The *inverse curse theorem* prices it in something unbuyable — if every state can be undone,
**no query ever knows it is finished**. (Power, Koutris & Hellerstein, ICDT 2025, Thm. 18: if every state
is invertible and `Q` is not constant, `Q` has no free-termination state.) The proof is three lines. From
an undoable state you can reach the state that undoes everything, and from there anything at all — so
nothing observed now constrains what is observed later, and every answer stays provisional forever. Their
own summary: the value of invertibility (DBSP, DBToaster) and the value of coordination-free monotonicity
(CALM, CRDTs) *"appear mutually exclusive."*

**Which makes this commitment a precondition for the rest of the design, not only its distribution story.**
Settledness, the threshold rule, the residual, a memo check that ever stops — every one is a claim that
*nothing more is coming*. Under retraction none of them can exist. The two theorems price the same refusal
and buy different halves: CALM buys soundness, never emitting a wrong answer; this buys completeness,
knowing there is no further answer to wait for.

**Identity is data, never position.** *"Which thing does this record belong to?"* is answered by an
argument inside the record, never by where it sits in an order. `heartbeat(episode2, 500)`, not *"the
heartbeat after the second `started`"* — so there is nothing to infer, because a query about episode 2
cannot match episode 1's records; the arguments do not unify.

**The second commitment is forced by the first.** Under permanence a mis-aimed record can never be
corrected, only supplemented by a correction that itself has to be trusted. So whatever a reader needs to
know about a record has to be **in the record, at write time**. Position is exactly the thing that is not.

**Calibration, since the second commitment sounds cheap.** It is the one place this design's problem class
has been measured, in an existing system with the opposite convention: **11 of 37 stops were discharged by
a record the worker did not write, 6 of them malformed** — a late heartbeat from a dead episode read as
current, a displaced worker's terminal read as the run's verdict, a halt swallowed because discharge was
author- and body-blind. Position-derived identity does not fail rarely.

Two things that measurement does **not** do. It does not support the rest of this document — identity-as-
data would work in a plain mutable database, and nothing here is measured against a system that exists.
And it does not touch **forgery**: a forger posting `stopped(episode2, completed)` makes a well-typed post
the store accepts, exactly as *"cooperative, no enforcement"* intends. Attribution defects die under these
commitments; forgery defects do not, and no representation changes that.

**And it forecloses a failure no census could have found.** *Contextuality* is the situation
where several agents each hold a coherent view, every pair agrees wherever they overlap, and no global
picture produces them all — formally identical to Bell's theorem, under a dictionary that is forced rather
than analogical (compatible family ↔ no-signalling, global section ↔ hidden-variable model). It is
undetectable by any agent, since every local and every pairwise check passes. Morton (2017) shows it arises
from thoroughly ordinary causes: from missing data alone, and from stale reads under snapshot isolation
alone. **It cannot arise at this layer.** Gluing indexed rows is a join on identity, so the glue is
determined rather than guessed, and his Prop. 6.4 constructs it for any compatible family; the obstruction
lives one level up, where restriction *"necessarily involves summing over indices"* (Def. 6.8) and nothing
records which row contributed what. Of his own versioning example: *"conflicts are resolved by version
numbers. Forgetting the version numbers, we get disagreement on indexed overlaps."* Contextuality requires
forgetting the discriminator; this commitment is the refusal to. **The foreclosure reaches exactly as far as
identity stays attached** — a consumer that aggregates performs the forgetting map itself, which is §Open.

## What makes the answer worth having

Three properties, stated as properties rather than as taste. Each is checkable, and each survives
translation into another language — which matters, since the point of a protocol is that somebody
reimplements it.

**Unsound things are unwritable, not forbidden.** Negation is not banned by discipline; it is absent.
*"This atom is false and nothing says otherwise"* is not against the rules — it has no syntax. The
alternative to unwritability is code review.

**The restrictions coincide.** Finite `∧` with arbitrary `∨` and affirmable-in-finite-time are one fact in
two vocabularies — the second is the standard justification for the first, so they are not independent
confirmations — and **monotone-iff-coordination-free is a genuinely separate route to the same
constraint set**, arrived at from distribution rather than from observability. Two routes agreeing is
weaker than three would be and still worth having: it is the difference between a constraint set that was
chosen and one that was met twice.

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
- **A completeness claim scoped to a region, posted as data, and composable across sources — that is
  Darari, Nutt, Pirrò & Razniewski** (ISWC 2013). `Compl(P₁ | P₂)` is a pattern plus a condition — *"a data
  source contains all triples in a pattern `P₁` that satisfy a condition `P₂`"* — published as RDF via
  `hasComplStmt`/`hasPattern`/`hasCondition`, with an entailment operator over sets of them; and **Def. 16
  indexes each statement to a source**, so a federated query is complete *"if evaluated over the **union**
  of all sources in the federation."* Their motivating case is a real *"verified as complete"* mark on an
  IMDb page. Two differences. Theirs is **metadata about sources** — which is why the index is needed at
  all — where `¬Q` is a record in the same store, claiming about a region of the world rather than about
  one source's coverage of it. And their semantics is relative to an **ideal graph**, against which a
  statement is objectively true or false, where nothing here defines correctness against a world: a false
  `¬Q` is a false post, under the same non-enforcement as a false value. They also reach this repo's dating
  repair independently — §6 recommends *"temporal guards … 'complete for movies by Tarantino **in
  2010**'."*
- **Ask/tell over a monotone store is CCP** (Saraswat, Rinard & Panangaden, POPL '91), which made the
  storage decision first — *"a simple constraint system is just an information system with the
  consistency structure removed"* — and then went the other way, making `false` the **top**, explosive
  (*"the inconsistent store can answer any ask request"*) and identified with divergence.
- **A store holding both polarities with contradiction *retained* is the paraconsistent relational model**
  — Bagai & Sunderraman (IJCM 55(1–2), 1995) and Trân & Bagai (Information Systems 25(8), 2000): a pair
  `⟨R⁺, R⁻⟩`, *"we do not assume `R⁺` and `R⁻` to be mutually disjoint"*, and *"a particular tuple may be
  considered to be both in and out of a relation."* Their *"all known (or believed)
  negative information is stored explicitly"*, over extents that may be **infinite** — though represented
  by automata rather than constraints, and with no accumulation or multi-source union.
- **Free termination — *"can a node know its output is final without coordinating?"* — is Power, Koutris
  & Hellerstein** (ICDT 2025). That is settledness, and their opening complaint is this document's: CRDTs
  give coordination-free consistency but no local way to know everything has arrived, and *"what good is
  distributed state if you do not know when you can query it reliably?"* Their answer **derives**
  termination from the query's algebra. They have the told form too — §5.2's nullary `All()`, *"true if we
  know that all machines have sent all their local data"* — and note that *"updating `All` requires
  coordination between the nodes."*
- **The four values are Belnap's** (1977): the four subsets of `{t, f}`, read as told-true, told-false,
  told-neither, told-both.

**What is not in any of them**, stated narrowly. Falsity **asserted by an agent** as the primitive act:
4QL derives `¬p` into the negative extent by rule, and the paraconsistent model's own worked construction
populates it by **CWA** — the storage of a negative extent is theirs, an agent *positing* one is not.
**Multi-party monotone accumulation**: neither paraconsistent paper defines an order on those pairs, a
merge, or an update. Refusing the tombstone **chain**, so that *told-both* is representable at all.
Demand-driven production with the store as the cache — named as *open work* in 4QL.

Not the distribution property itself: **no party roster and no self-identity is Ameloot's**, and
is now stated three times over (Cor. 13; *Complete CALM* Remark 3, *"membership knowledge is the single
non-monotone input that renders all subsequent computation monotone"*; free termination §5.2). What is not
in any of them is a data model built so that **every readable predicate sits inside that class by
construction**, rather than a language in which one may or may not stay there.

## The model: a store of literals, one operation

```
post(c)        -- add a literal to the store
```

Agents post. A **querier** and a **handler** are not different kinds of thing; they are agents posting
different literals.

**There are no cells, and no built-in directions either.** The store is a growing set of ground literals
in relations, and that is the whole of it. A schema that declares two relations a **complementary pair**
gets to read one as *"true"* and the other as *"false"* — written `Q` and `¬Q` throughout this document —
and a schema that declares no pair simply has relations. The substrate never knows the difference; the
pairing is a signature fact, like a sort (§"Types"). `loss(60, 0.5)` and `loss(60, 0.4)` are two atoms,
both true, and nothing combines them — a reader asking `loss(60, V)` gets two answers. Everything that
looks like combination is one of two things: **set semantics** collapsing identical literals, or **one
literal refining** as a variable inside it is bound. Two posts never merge; one post gets more
instantiated.

**Unification is local; sharing is a posted equality.** A rule body unifies against the store the ordinary
way, inside one agent. What does *not* cross a host is a shared mutable variable: a hole in a posted term
is a **named variable** — `loss(60, v37)`, with `v37` the name of an unknown and never a value — and
*"`v37` is `0.31`"* is a posted fact like any other, carrying the ordinary four-valued status. It is not a
constant, and the difference is load-bearing: a value in that position would *witness* the existential the
post asserts, and a witnessed existential leaves nothing to produce (`if-built-today-decisions.md`,
§"The Skolem reading").

**That is what makes *"one representation, four roles"* (§"Facts and demands are dual") a mechanism rather
than a slogan.** Post `loss(60, v37)`: with no equality told it asserts existence and is a question; once
`v37 = 0.31` is told, the same term read through that equality **is** the answer. No delivery mechanism is
needed and no copy is made — the equality is a fact in the store, read like any other — which is why
§"Answers stream individually" can kill the delivery machinery without a shared object to replace it.

Two posts of the same shape still make **two** holes, and that is not a contradiction: `v37` and `v38` are
different constants, so one producer's equality does not answer both. It is why a call table is still
needed (§"Demand subsumption").

**Though "two holes" is a naming policy, not a fact — and where distinctness comes from is the policy's
axis.** Deterministic gensym is unavailable coordination-free: *"each actor has a unique name known to
them"* is **self-identity**, Ameloot's `Id`, the membership input at the name layer — its fourth
appearance, after single-spawn, the claim's CAS, and `All()`. The corners:

| distinctness from | mechanism | price |
|---|---|---|
| **content** | ambient names disambiguated by discriminating arguments — which is content-addressing done by hand, and what the measured corpus already does (24 ambient value names, zero fresh variables) | identical questions share a hole, so one equality answers both: automatic memo, smaller call table, and "two holes" above stops being true |
| **chance** | random names | almost-sure freshness; a collision is an *accidental forgery*, surfacing as spurious sharing or `{t,f}` — the class §"Two commitments" already scopes out |
| **membership** | per-actor prefixes | self-identity — the bootstrap paid once (*Complete CALM* Remark 3), and what `local://host/pid` already borrows from the OS |
| **the link** | pairwise sessions: a variable is named per link end, so identity is *indexical* — "my end / your end" — and **no global identity exists at all** | no broadcast of variable-bearing terms (ground facts still broadcast; consistent with demand being control). Cross-link identity is a middleman's posted **translation equality**, `eq(v_ab, v_bc)` — scope extrusion by reification, no new machinery |

The link corner is the only one that is deterministic *and* oblivious; the content corner is the only one
where a name collision *means* something (the questions coincide) rather than failing. Nothing below the
substrate decides among them — naming is a convention like polarity, and a deployment picks its corner.

**A binding is an ordinary posted fact**, and this is where it pays. Two agents binding disagreement is
**readable** — two equalities, and `v37 = 0.31` reads `{t,f}` if somebody denies it. Under a shared mutable
variable that sentence cannot be true: a term cannot refine to `0.31` *and* to `0.45`, so either the second
binding has nowhere to go or the refinement never happened. **Conflicting bindings are the free completion
one level down**, at the hole rather than at the atom.

That is also why **Oz/Mozart's owner protocol is not needed here** — *"the owner accepts the first binding
request and ignores all subsequent"* buys **determinism**, and determinism is exactly what this design has
already declined. Oz needs one binding to win consistently everywhere; here both are kept and the
disagreement is readable. The paraconsistent stance and this are not two commitments, they are one.

**And unboundness stops being observable.** With a shared variable, *"is this bound?"* is answered by
looking at your own term — which is **observing absence**, the move this design forbids everywhere, and
which §"There is no `read`" must prohibit by hand. Under a reified equality, unbound is `∅`, and `∅` is
unaffirmable by construction. A rule enforced by discipline now holds by shape, which is what
§"What makes the answer worth having" means by *unsound things are unwritable, not forbidden*.

**What it costs.** A hole needs an identity that survives crossing a host, so **naming is still protocol**
and belongs in the wire format. What it no longer costs is a durable *mutable* object: `v37` is an ordinary
constant with the ordinary reclamation question. The closure work does not appear — it **moves**. Shared
variables do it eagerly, at bind time, through whatever the hole was unified with; posted equalities do it
lazily, at read time or into an index. The one genuinely new cost is the one being bought: an equality can
be disputed, so readers with different trust policies close over different subsets and reach different
congruences — which §"What gets built on top" already books as the price of trust being policy.

That is what makes monotonicity free rather than argued for. Merging is declined because it
**fabricates** — `f(a,Y)` and `f(X,b)` compressing to `f(a,b)` asserts a fact nobody posted, which is
sound only under a functional dependency (§"Constraints are asked"). That reason is decisive on its
own; what merging would have *cost* was measured separately (greedy merging is first-fit colouring of the
compatibility complement, within 1.04–1.20× of exact `χ` to n=24 and 1.21–1.32× of a heuristic to n=500,
with a crown separation at **12 positions giving `χ = 2` against 924 first-fit blobs**) and is recorded in
`if-built-today-decisions.md`, since a cost that cannot change the decision does not belong here.

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

**There is no `¬` in the language at all, and no polarity either.** The store holds literals in relations;
`loss` and `loss⁻` are **two relations that no axiom connects**. What makes them a *pair* is a declaration
in the **signature**, and nothing else — so polarity sits exactly where sorts and wrappers sit, as schema
the user chooses and the substrate is ignorant of (§"Types"). A user who declares no pairing has ordinary
relations and no four-value reading; a user who declares one gets it.

`¬Q` is therefore **notation in this document** for a literal of a declared complementary relation, not a
construct the language contains. Nothing is excepted, because nothing negates.

The same split covers variables. In this document they are written Prolog-style — `V`, capitalised — and on
the wire a variable travels as a **constructor**, `var(37)`, an ordinary term with a known functor. Neither
a capital letter nor a sigil is wire format, because both make a receiver decide variable-ness from the
spelling of a name — the closed-set-in-an-open-namespace error the polarity representation (§"Polarity is
schema, not substrate") exists to avoid.

**What is missing, and why — the reasons are two, not four.**

| a richer fragment has | here | why |
|---|---|---|
| arbitrary `∨`, `∃`, finite `∧` **in bodies**; clause-level `∀` | **yes** | — |
| arbitrary `∨` **in heads** | **no** | a disjunctive fact has nowhere to live: this store is a **set of literals**, one model. `φ ⊢ a ∨ b` needs a set of *models*, or the disjunctive chase |
| `⊥` in heads — integrity constraints | **no** | a store that must accept what it is given can only **reject a post** (order-dependent) or **go inconsistent** (§"Constraints are asked") |
| equality in heads | **no** | that *is* a functional dependency — the same reason again |
| `∃` in heads — value invention | **no** | the coordination-freeness proof's quiescence argument **requires** no value invention. An existential *post* is fine; a rule minting a fresh variable is not |

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

Read downward, the same facts buy the tractable corner (PTIME, against disjunctive Datalog's `Σ₂ᵖ`) from
the first, and the placement strictly inside the coordination-free class from the second.
`definite-clause-maximality.md` works the boundary out.

**And one connective is absent from the logic rather than priced on the ladder: interpreted equality.**
Regular logic ordinarily includes a substitutive `=`; here equality is a **convention** — an ordinary
posted relation whose congruence closure is a *reading*, taken per trust policy (§"The model"). Variable
repetition in a body still joins on syntactic coincidence, which the logic's diagonal gives free. The cost
of the reading splits on **freshness, not on syntax**: an equality with a **fresh side** — a variable,
a name only its holders can utter (freshness being itself a naming policy; §"The model") — closes among
those holders, inside the window whose question it is. An equality **between ambient terms** — names
anyone may utter at any time — can arrive from anywhere, and a single one makes every class it touches
global. Which is why a reader's default policy folds fresh-sided equalities and declines ambient-ambient
ones, and why FD-violation detection stays a reader-side query (§"Constraints are asked") rather than a
derived equality.

**Whether the fragment is *forced* rather than chosen is asked separately** in
`definite-clause-maximality.md`, via a third route — preservation under algebraic homomorphisms. Its
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
merely close.

**Why `¬` had to go.** To affirm `φ` you need a finite observation. To affirm `¬φ` you must rule out
*ever* affirming `φ`, which is a survey of everything there is. That survey is **exactly what
coordination-freeness excludes** (§"CALM" — the predicate is binary, and nothing here is priced in
rounds). So "no negation," "opens are affirmable," and "monotone ⟺ coordination-free" are one fact in
three vocabularies.

**The derivation/report split is the open/closed split.** Derivation affirms; reporting refutes. They
cannot mix, for the same reason the complement of an open is not open.

| | negation? | may feed demand? |
|---|---|---|
| **derivation** — what to produce | no | yes |
| **reporting** — what is missing, what is best, what diverged | **yes, inherently** | **no** |

`argmax` is therefore not expressible in derivation, so a bandit's one non-monotone step is forced to the
boundary. Its monotone half stays inside: `beaten(A) :- value(A,V), value(A2,V2), A ≠ A2, V2 > V` only
ever grows. (The disequality is not decoration — without it, §"Constraints are asked" lets one arm
carry two values, and `A` beats itself.)

**One exception is real and does not repair.** `ensure`, the library's core operation: its loop condition
is a threshold claim on `progress`, a *retractable* quantity, and its two termination guards are a
**temporal delta** (*"nothing new was derived"*, which has no positive form) and an **inflationary
fixpoint test** (*"another lap can only reproduce them"*). Neither is expressible in the fragment — each
compares two moments, which no growing set of facts can do — and both feed demand, because both decide
whether to relaunch. §"Two layers" is where that belongs.

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

It also means **every branch is a positive guard that blocks, and none fires *because the others did
not***. That is CCP's `ask` and LVars' threshold read, and it needs no closed sort — though over a closed
sum, exhaustive case analysis by constructor is perfectly available and monotone, since enumerating
constructors is not negation. What is unavailable is only the branch taken on a *failure to match*. And
failure to match is unobservable here, so the order in which matches succeed cannot be detected:
derivation *times* differ, the answer set does not.

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

**Keeping a conflict visible is Belnap's argument; the reason for it here is not his.** *"If the computer
would not report our contradictions in answer to our questions, we would have no way of knowing that its
data-base harboured contradictory information"* (1977, §II) — suppress a conflict and you destroy the
evidence that it exists. But he declines to *resolve* conflicts only for want of a method: *"since I have
never heard of a practical, reasonable, mechanizable strategy for revision of belief in the presence of
contradiction, I can hardly be faulted for not providing my computer with such."* Here it is not a want.
**Precluding conflict costs coordination** — agreeing in advance that two producers will not disagree
means knowing who they are — so what was an admission becomes the load-bearing reason. Detection is
preserved; preclusion is the thing declined.

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

**Two properties of the derivable half, both from the order having joins.** Status aggregates by union, so
the state order is a **join-semilattice** — a stronger hypothesis than monotonicity, and it pays twice
(Power, Koutris & Hellerstein 2025, Props. 15–16).

- **States that can stop agree.** Two stores both settled for a question have a least upper bound reachable
  from each; each being settled forces it to agree with that bound, so they agree with each other. Two
  agents who can both stop **cannot disagree**. Without joins this fails outright — two settled states
  could have no common future and disagree permanently.
- **No store is a dead end.** If a question can be settled at all, it can be settled from wherever you are
  now. A *possibility* claim, not liveness: it rules out dead ends, not stalls.

**Which pairs with the caveat above.** Settledness can be lost — but only by the **question** growing, never
by the **store** growing. That is the whole content of requiring `Q`'s extent to be fixed, and the other
direction needs no guard at all.

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

Note the two marks are different operations and must not be run together. **`∖` is set difference in a
region description** — `E`'s atoms are excluded from the region, never asserted false — where the posted
`¬` is the polarity. `¬(Q₀ ∖ E)` says *"everything in `Q₀` other than `E` is false"*, and says nothing
whatever about `E`.

### How a party comes to know a negative fact

`¬Q` is the primitive. Coming to know one is a **list**, and this is where a closed-world argument
legitimately returns — as a *route*, not as the semantics.

**The test that separates knowing from merely stopping: could a third party post this knowing only that
the process died?** For **exhaustion** yes — a pid probe suffices. For **falsity** no, because a dead
producer's silence is not evidence about the world. That is the launcher-versus-lifecycle split this
library already has, kept orthogonal for exactly this reason, and collapsing the two is what made a
single closure predicate look as though it needed a universal over the producer set to mean anything.
Exhaustion arrives three ways — `p`'s own `lifecycle.stopped`, an observer's `launcher.terminated`, and a
pid probe — of which only the last is dependable, since the first two exist only if somebody volunteers
them and the probe **abstains off-host**. None of them is a fact about what exists.

- **A converged producer.** The run ended at 400, so there is no loss at 500. It knows because it ran.
- **A solver.** A propagator that has proved a region has no solutions may post `¬Q`; one that has not is
  posting what it does not know. Not a solver-specific rule — the general one with *know* instantiated.
  Unsatisfiability needs no vocabulary of its own: it is **grounds for a post**, not a third kind of
  absence.
- **A closed producer set.** Everyone who could produce here is done, and no more will appear. This one
  **needs to know who all the producers are** — which is exactly the class of query a coordination-free
  program may not ask — and it is the one that needs arguing, because §"CALM" is about not needing them.

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
is §"Constraints are asked"'s argument against asserting a constraint as an axiom.

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

**And the other order is not merely unused — it is not traversable.** In the bilattice these four values
live in, the truth order is `⟨P₁,N₁⟩ ≤_t ⟨P₂,N₂⟩` iff `P₁ ⊆ P₂` **and `N₂ ⊆ N₁`** — note the reversal.
Truth rises by adding a positive *or by dropping a negative*, and dropping a negative is **retraction**.
Half of `≤_t`'s covering steps therefore run against permanence, and the truth-order top `{t}` becomes
unreachable the moment anything false is told. A store that only grows travels `≤_k` freely and `≤_t` only
partway, so the informational reading is not a preference between two available orders: only one of them
is a direction the store can move.

**An atom's status is the set of things producers have told you about it**: `∅`, `{t}`, `{f}`, `{t,f}` —
the subsets of `{t, f}`, and Belnap's four. **The aggregation is union**, so a status
only ever climbs — monotone **by construction**, and in a *growing* producer set rather than only a fixed
one.

**It is the free completion, one layer up.** §"Constraints are asked" meets the same fork at the value
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

**What is readable is the up-sets, and there are exactly four non-trivial ones.** `⊒ {t}`, `⊒ {f}`,
`⊒ {t,f}` (*disputed*), or their union. Each is affirmed by exhibiting a witness. (Six up-sets in all —
these four plus the empty and total ones — which is the six of the six-of-sixteen measurement above.)

**Those four are threshold reads, and refusing the top is what fixes their shape.** LVars' `get` (Kuper &
Newton, FHPC 2013) is the same operation — block until the state is *at or above* an element of a
**threshold set** — but theirs must be **pairwise incompatible**, *"the lub of any two distinct elements in
`Q` is `⊤`"*, because `get` returns the unique element crossed and uniqueness is proved from that premise.
Pairwise incompatibility presupposes a `⊤`, and theirs is the **error** state produced by conflicting
writes. The free completion was taken here instead, so `⊒{t} ∨ ⊒{f}` — minimal elements `{t}` and `{f}`,
joining to `{t,f}` — is an illegal LVars threshold *set* and a perfectly legal threshold *line* in the
antichain sense. Each package is internally forced:

| | LVars | here |
|---|---|---|
| conflicting writes | `⊤`, an error | `{t,f}`, a status |
| threshold shape | pairwise incompatible | antichain |
| what a read returns | the unique element crossed | true or false |

**And their restriction is unnecessary here because their return is.** Uniqueness exists to hand back a
*value*; every readable status here is a Boolean *"is it at or above X?"*, which needs none. One decision —
keep both polarities, take no top — settles all three rows.

**`∅` is the one thing not readable**, and it fails twice. *"Nothing has been told about this"* is a
down-set, so no finite observation affirms it — the open-world assumption recovered as a fact about the
status reading rather than stipulated. And the sharper statement is about **direction**. `⊒{t}` and `⊒{f}`
are each a union over parties and therefore monotone, where `∅` is the conjunction of two negated growing
extents, hence **antitone** — and an antitone query free-terminates only where a witness refutes it
(Power, Koutris & Hellerstein 2025, Thm. 24). So `∅` is decidable exactly in the direction of its own
destruction: a reader can always confirm it has **left** `∅`, never that it is **in** it. Not unanswerable
without coordination — answerable in one direction, and that direction is the useless one. Same for *"and
nobody disputes it"*.

**And the operation this forecloses has a name.** A bilattice carries two symmetries (Fitting, *Bilattices
Are Nice Things*, Def. 2.4): **negation** reverses the truth order and fixes the information order;
**conflation** reverses the information order and fixes truth. Negation is this design's polarity swap and
costs nothing. Conflation is the other one, and on these four values it is `−∅ = {t,f}`, `−{t,f} = ∅`, with
`{t}` and `{f}` fixed — **its entire content is what it does with `∅`**. Fitting's multi-source gloss says
what that is: the affirmers after conflation are *"the people who originally did not deny."* The
closed-world assumption, written as an operation.

**So three refusals stated separately here are one.** Declining CWA, holding `∅` unaffirmable, and needing
no roster are not three commitments but one: **conflation is unavailable**. Its defining case requires
recognising `∅`; recognising `∅` requires knowing nobody will ever tell you; and that is the participant
roster, which §CALM names as the single non-monotone input. It also replaces a stance with a test that has
a yes-or-no answer — *does this operation reverse `≤_k`?*

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
contradict each other. That is the *"without stopping to confer"* of §"What it is for", proved rather than
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
  test, and the closed-producer-set conclusion of §"Falsity is told";
- the **monotone layer** derives over them and owns everything it can.

**The boundary is not a convenience — and it has a name.** *Complete CALM* §4 calls this **proper
coordination**: *"coordination is a means, not an end. A system may use coordination internally to resolve
a non-monotone specification, producing a monotone output interface for downstream consumers."* Def. 11
makes it precise — restrict the admissible outcomes enough to restore monotonicity, then test the residual.
Everything above the line here is monotone and coordination-free; everything pushed below is exactly what
is not. Three constructs have now tried to get in and been reclassified rather than accommodated, and the
layer needed no change in any case. The reference model draws the same line: output append-only by
hypothesis, working memory admitting deletion.

**And there is a barrier here that this design steps around by construction.** Thm. 3:
relational-transducer CALM *cannot in general verify* proper coordination — a non-monotone specification
implemented in Datalog must contain negation, adding coordination rules leaves the negation in place, so
the syntactic check false-negatives, and deciding monotonicity in stratified Datalog is undecidable. The
claim above is therefore checkable only at the **specification** level, not with Cor. 13. What rescues it
here is that the non-monotone work never enters the program: there is no stratified negation to defeat the
check, because there is no negation. **Separating rather than stratifying is what keeps the residual
syntactically evident** — which is what the next paragraph's rule is really for.

**What crosses is literals**, and the mechanism is already in this repo — `prolog-query-layer.md` says of
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

## One demand, traced

Everything up to here has been argued rather than shown. Here is a single demand from posting to
settlement, so the rest has something to be about. Nothing in it is new — every step is a mechanism one of
the surrounding sections defends.

**A querier posts one record.** It wants the loss at every step of a run, for as long as the run lasts: the
term `metric(loss, V, S)` with `S ≥ 1`, posted as a **`∀`** demand — *decide every atom in this region*,
not *find me one*. The region is **unbounded**, and that is fine; what must be finite is the eventual
cover, not the region.

**A producer is handed the residual, not the demand.** Steps 1–60 are already in the store, so what reaches
it is `S ≥ 61` — the demand minus what is settled. Nobody detects that the first sixty were subsumed; they
simply contribute nothing.

**It produces, and the querier reads a threshold.** `metric(loss, 0.31, 61)` is posted. The querier's read
is `⊒{t}` on that atom, affirmed the only way anything is affirmed here: by exhibiting the record.

**At step 900 the querier sees `∅`, and may conclude nothing from it.** Nothing has been told about that
atom. That is not *"there is no loss at step 900"* — it is *"nobody has said."* The querier may confirm it
has **left** `∅` the instant any record arrives, and may never confirm it is **in** it.

**The run converges at 743, and the producer says so.** It posts one negative record, `¬metric(loss, V, S)`
for `S > 743`. **That single record decides infinitely many atoms** — and it is *told*, not inferred.
Nobody derived it from the absence of anything: the producer knew, and said.

**Now the region is settled.** Every atom of `S ≥ 1` is decided — `⊒{t}` for the 743 produced steps, `⊒{f}`
beyond. The cover is finite (743 positives and one negative) though the region is not, which is exactly
what makes an unbounded demand dischargeable. The querier's *"tell me when there are no more"* fires here,
and not before.

**A second querier posts the identical demand, and nothing runs.** Its residual against a settled region is
empty, so there is no work to hand anyone and no producer to launch. The store was the cache; no cache was
built.

**And if two producers disagree.** Two of them posting different losses at step 61 produce two *atoms*,
both `⊒{t}` — a **domain** conflict, invisible unless somebody wrote a rule saying loss is functional in
the step. A **valuation** conflict is a different thing: it takes a post of `¬metric(loss, 0.31, 61)`, and
then that one atom reads `{t,f}` — affirmable, inert, and poisoning nothing around it.

## Demand is control

Demand is not monotone: a lease expires, a querier withdraws, an operator halts a run. That is fine.

**Nothing derived becomes false; some things never get derived.** A withdrawn demand means a term is not
produced, so a reader's threshold claim never fires — it suspends forever. That is a **liveness** failure,
not a safety one. Two replicas with different demand produce different *subsets*; every literal in either
is correct.

All three mechanisms are control: resource management, a querier changing its mind, and somebody
deliberately stopping a machine. The logic never had jurisdiction over any of them.

### The quantifier is a property of the posting

> **A bare pattern is `∃`** — one production satisfies it. **A `∀` demand must be *finitely coverable***.

**The `∀` half ships; the `∃` half does not, and the gap should be known.** With a schedule and an
`until`, a subscription is **one durable record** denoting a region and demanding every atom in it —
durable across the *producer's* death, because a worker re-drains the control log and re-registers
whatever is still unanswered, pinned by
`tests/test_run_episodes.py::test_relaunch_extends_one_series`: one subscribe posted *before episode 1
exists*, two episodes, ten steps, one series.

The `∃` half is a target, not a description: `worker.py` serves a bare subscribe by reading the register —
`self._values.get(name)` — and firing at the next safe point with `None` if nothing was ever set. That is
a **poll**, not an unsatisfied existential waiting on a production. And `{"every": …}` with no `until` is
schema-legal, documented as *"forever"*, which is what makes the unbounded `∀` demand below concrete
rather than hypothetical.

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
independently, first exhaustively on a small carrier (**8/8** singletons basic open, **256/256** opens
complemented) and twice since on larger ones. Every open is affirmable, nothing is forbidden, and any structure defined by which sets are
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
computed.* It does not remove the need for it — two queriers independently posting `loss(60,V)` hold two
**differently named** variables, so one producer's equality answers only the name it mentions, and the
producer posts one equality per name. That is the naming policy of §"The model" doing its work, and it
makes the call table's *identity* role corner-relative: under the content corner the two names coincide,
one equality answers both, and what remains of the table is its *routing* role — who is told.

**There is no `read`, and no syntactic substitute.** A tempting test — *"does the posted term have a free
variable in key position?"* — cannot carry the distinction. It is not invariant under rewriting
(`loss(S,V) ∧ S=60` classifies opposite to the identical `loss(60,V)`), and it presupposes a key/value
split no term carries: `verdict(Outcome, FinalStep)` has two value positions, `provenance(key, Prid, Sha)`
has two key positions. What two verbs were buying is **a declaration of intent that survives rewriting**,
which a syntactic property cannot replace. Consequently **admission control needs an explicit mechanism**,
and the natural signal is a *quantity*: how many atoms does this posted term denote?

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

**This is also what keeps the contextuality foreclosure intact** (§"Two commitments"). An enforced
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

### Polarity is schema, not substrate

A **complementary pair** is a signature declaration like any other: it says two relations of the same
shape are to be read as the two directions on one subject. The substrate stores literals and knows
nothing about it; the four-value reading, `⊒{f}`, and everything §"An atom's status" says all exist
**relative to a declared pairing** and are simply unavailable to a schema that declares none.

Three things follow. **Polarity is opt-in** — a user who wants plain relations has them, and nothing in
the substrate is wasted. **It rides the signature channel**, so two agents disagreeing about a pairing is
the same failure as disagreeing about a sort, with the same fix and the same unenforceability. And
**nothing about a negative relation is structurally special**: it is permanent, unauthored and
region-capable exactly as a positive one is, which is why the reclamation tiering above turns on
re-derivability rather than on direction.

What *is* asymmetric is informational and belongs to the data rather than the schema: absence is uniform,
so a negative relation's extension usually compresses into one record where a positive one does not. That
gives a negative record more **reach**, so a wrong one does proportionally more damage — a reason for care,
not a reason to treat the direction as a different kind of thing.

**The representation: one wrapper functor, not a naming convention per subject.** Declare

```
pol(A)  =  pos(A) | neg(A)
```

once, and keep `pol(A)` **out of the term algebra**, so `pol(A) ≠ A`. Atoms are untouched — `loss(V, S)`
keeps its own per-functor signature, and `pos(loss(V,S))` / `neg(loss(V,S))` are its two directions. `¬Q`
is notation for `neg(Q)`.

**What changes is the shape of the atom set.** A partner relation per subject gives `Atoms(p) ⊔ Atoms(p⁻)`
— a coproduct of two independently declared functors, isomorphic to `2 × Atoms(p)` only when the
declaration is honoured, which nothing verifies. The wrapper gives `2 × Atom` by construction, and three
things follow from that one fact rather than from three separate arguments:

- **`¬¬Q` is a type error.** `neg(neg(x))` needs `x : Atom` and is handed `pol(Atom)`. This is exactly why
  `pol` stays out of the algebra: an endomorphic `¬` would have to be a constructor *of* the term algebra,
  and in a free algebra `not(not(Q))` is a **distinct term** from `Q` that no type discipline can collapse.
- **The two directions cannot drift.** Both constructors take the same argument sort, so a mismatched arity
  between them is unwritable.
- **The status fold is a transpose.** *"The set of things producers have told you about it"* is
  `Set(2 × A) → (A → P(2))` — currying, nothing more. With a partner relation the fold must consult the
  pairing to know which atoms to group, and the pairing is what no tool checks.

**And it is the form that generalises.** §"What gets built on top" reads polarity as the two-element case
of a declared marker set: under a wrapper a third marker is a third constructor, under partner relations a
third naming convention with nothing relating it to the first two.

**The price is polarity-polymorphism, and it is small.** `conflict(X) :- X, ¬X` is unwritable, since `¬X`
would need `X : Atom` and it is `pol(Atom)`. Little is lost: `conflict(A) :- pos(A), neg(A)` is one rule,
generic over atoms, and it names the conflict by the **atom** rather than by a direction the conflict does
not have. Where a rule must genuinely leave the direction open — *dispute the opposite of what an untrusted
source said* — two facts supply it,

```
flipped(pos(A), neg(A)).
flipped(neg(A), pos(A)).
```

and involutivity is **provable from them** rather than asserted. The rules needing this are a small fixed
set of meta-rules, so the verbosity is bounded.

**On the wire this is the closed-set rule rather than a preference.** Serialisation forces polarity into a
field whatever the language does; what differs is that field's domain. A partner relation per subject puts
it in the **functor name** — an open namespace, recovered by string surgery on a prefix. The wrapper puts
it in an outermost functor with **exactly two values**, which a schema can pin with
`additionalProperties: false`.

**None of which prevents anything.** The substrate typechecks nothing, and a schema declaring its own
endomorphic `not/1` gets double negation with no objection — the same unenforceability that applies to
sorts, above. The claim is only static checkability's: a closed set known at authoring time should be
spelled so a tool can check it, and two polarities is closed where a functor namespace is open.

### Sort closure, and disequality

**Disequality is a constraint-domain predicate, never a logical connective**, so it was never in the
derivation layer. What it costs is preservation: `≠` is not preserved under homomorphisms, because a
homomorphism may identify two constants — the same *"two unbound variables may yet be identified"* seen a
third time.

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

### Orders are mostly read-side

By **default** an order is not a storage type — it is how a **read** aggregates what it finds, which moves
the whole table off the safety path onto the cost path.

| aggregation | note |
|---|---|
| last-write-wins | `argmax` over `seq`; a report |
| `max` / `min` | a report |
| multiset union | counts rather than membership. An observation-count that only climbs, asked only **at least `n`**, is admissible as a **store** too — it is the `(ℕ, max)` join-semilattice, Bloom^L's `lmax` with `gt_eq`, and the at-least-only discipline is the threshold rule, forced not advisory (*exactly* flips on the next arrival; *at most* is an upper-bound test, the else-branch move). The subtlety is what the count **means**: bare totals have no coordination-free merge (`+` is not idempotent; `max` means *"some single lineage tallied `n`"* — sound for at-least, undercounting). **True totals are occurrence naming**: per-actor tallies (the G-counter; membership) or per-event ids (the naming table's corners), counted as a `π` reading — multiplicity is identity, so the naming policy prices the count |
| set union | the identity read, and the only option for a **holistic** aggregate (median, percentile), where no bounded *exact* summary exists. Bounded *approximate* ones do: a 200-bucket sketch reproduced a 2000-sample bootstrap CI to **1.07% of its width** |
| "must all agree" | the `conflicted` predicate above |
| lexicographic | fine as a *selection* order, dangerous as a *combining* one: with `attempt` at the head, `(1,running)` and `(1,crashed)` have least upper bound `(2,⊥)` — it **fabricates attempt 2**, in a design about attribution |

**Where the narrowing (Smyth) construction goes.** The three powerdomains are the three ways to make "a
set of possibilities" a domain: Hoare/lower (*may*), Smyth/upper (*must*), Plotkin/convex. Putting the
upper one on the *value* side is a category error — read extensionally as a set of facts it is antitone,
so a rule body binding a variable to a member is non-monotone. Its natural home is **demand** (*must*
produce) — **and that is open**, suggestive and unworked.

**Continuous carriers are restricted, not broken.** In a continuous dcpo the basic opens are `⇈c`,
coinciding with `↑c` exactly in the algebraic case; an open interval **is** `⇈c` for the interval domain.
Nothing settles at a point under bisection, but *"is `S` in `(0.4, 0.6)`?"* becomes true the moment
narrowing puts the domain inside it. So: **settle the question, not the value.** Thresholds fire and
standing queries die. (GC is *not* on that list: on the boundary the query neither fires nor dies.)

**The constructive form of that rule is a value that streams.** Represent a real as an exponent plus a
growing list of expansion coefficients and it is already a monotone stream of information — the same shape
as the store — so every interval question becomes an ordinary threshold read, firing the moment enough
coefficients place the domain inside the interval. Not free: a streamed real is a **region of coefficient
atoms** rather than a `Float`, so the value plane acquires unbounded extents and §"Types" would owe it a
sort. Unworked.

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
| a **produced** record whose producer still lives | a re-run — six hours, and *the same answer* only if production is deterministic | at a price, and only then |
| a **produced** record whose producer is gone | **unrecoverable** | no |

**The axis is re-derivability, and it has nothing to do with polarity** — an earlier draft made the last
row *"a negative claim"* and that was wrong in both directions. A positive fact from a run whose
checkpoint is deleted is exactly as unrecoverable, and evicting it descends `{t} → ∅`, the same forbidden
descent. And on determinism the ordering **inverts**: re-running a converged producer regenerates
`¬(S > 400)` exactly, where re-running a non-deterministic one may produce `loss(60, 0.5000001)` instead
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

## What the problem domain forces, whatever the design

These are not inherited from an incumbent; they are constraints the setting imposes, and any design for it
will contain something playing each role. They are listed because it is easy to mistake them for choices.

- **A run is a durable identity that outlives its processes.** The unit somebody asks about is not a
  process — it survives the death and relaunch of every process that ever served it.
- **Content-addressed identity.** If a run is named by what it computes, the name is also the cache key,
  and two agents asking for the same thing ask under the same name without arranging to.
- **The verdict as a join of two partial observers** — and it is a *report*, which is why it may use the
  narrowing reading that derivation may not.
- **Cooperative, no enforcement.**
- **`never` as a fact rather than a status** — it survives as `¬Q` over a singleton region. What nobody
  posts is the **status**, which is read, not written.
- **Status cycles; values do not.** `running → OOM → running` cannot live in a monotone order, so the
  attempt index goes in the term and the cycle lives in the *sequence of attempts*.
- **Structure goes in the key, not the value.**

## What gets built on top

The base is **definite clauses over an unspecified universe**, and nothing else. Everything else this
document treats as part of the design is a construction above it. Drawing the dependencies is worth more
than listing them, because it shows what a reader who rejects one construction still keeps.

There is also a second, independent reason the line sits where it does, found after it was drawn: the
constructions below it are exactly the ones whose **meaning varies with the universe the user inhabits** —
polarity, equality, the four-value reading and settledness all mean something else (or nothing) in a
regime where an arbiter is affordable and a refuted branch can be pruned. The audit and the criterion are
in `substrate-parametrically.md`; by §"What makes the answer worth having"'s own standard, a boundary met
twice rather than chosen.

```
definite clauses, parametric over the universe
├── polarization — complementary relation pairs, declared in the signature
│   ├── told falsity (¬Q), and stream termination
│   ├── settledness → the threshold rule → its six instances
│   └── conflict {t,f} → the Belnap reading
├── annotation — provenance
│   ├── dispute → readjudication
│   └── trust policy
└── aggregation — summaries over a demand region
```

**The two upper branches are independent, and that is why the graph is worth drawing.** Provenance
annotates derivations and combines them; no step of it mentions a polarity, and neither does dispute nor
trust. So the answer to *"nothing can ever be taken back"* — below — survives a reader who rejects
polarization outright, which is this design's most contestable choice. Two edges cross: diagnosing a
`{t,f}` consumes provenance, and objecting to a polarity assignment needs both.

**Polarization is a two-element marker set, not a commitment to two-valuedness.** Polarity is declared
schema and the substrate never sees it (§"Polarity is schema, not substrate"), so a richer marker set is
*more declared relations* and the base does not move. A third marker — `undecidable(Q, x)`, say — is posted
positively, told rather than inferred, exactly as §"Falsity is told" requires. Belnap is therefore not
*the* logic here but **the reading of the two-marker instance**; over a larger set the reader supplies
whatever lattice they like, and nothing underneath changes.

### Retraction's effect, without retraction

The largest apparent sacrifice is that nothing can be taken back, so one bad producer would poison the
store permanently. It does not, and the mechanism needs nothing the base lacks. **Objections are derived**,
from grounds that are ordinary posted facts:

```
disputes(F) :- produced_by(F, S), miscalibrated(S), timing_sensitive(F).
```

`disputes/1` takes the fact as a **term**, which is the wrapper pattern of §Types doing a second job:
`Fact = stopped(Episode, Outcome) | metric(Metric) | disputes(Fact) | …` is a declared sum sort like any
other. Because it is **recursive**, objecting to an objection needs nothing added. Who objected is
provenance on the `disputes` record rather than an argument, so an objection is worth something only where
provenance exists — the edge drawn above. And because a term carries variables, an objection is
**region-scoped exactly as a demand is**: `disputes(stopped(episode2, _))` rejects a claim, a rule bodied
on `produced_by(F, bob)` rejects a producer.

What follows:

**It recovers retraction's effect at none of its cost.** A reader derives from everything its policy does
not exclude — monotone in the store, no roster, no message to anybody.

**It is strictly better than deletion, not equivalent with extra steps.** A derived `disputes(f)` is
permanent like everything else, so an objection cannot be un-derived — but nothing was ever removed. `f` is
still there, counter-grounds are postable, and a reader seeing both folds differently. Under real deletion
an erroneous deletion destroys `f` and no later discovery recovers it. **Readjudication is free here and
impossible there.**

**You cannot object without grounds.** That falls out of dispute being derived rather than posted, and it
is a constraint worth stating rather than discovering: reasons enter the shared record, and bare suspicion
— *"I simply do not trust Bob"* — is holdable as policy but not postable as fact.

**`disputes`/`upholds` is a complementary pair**, so the polarization branch applies to the annotation
branch unchanged: conflicting objections get `{t,f}` and the Belnap reading with no new machinery.

**And this is the one place readers legitimately diverge.** Evidence is shared and permanent; the *fold*
from evidence to acceptance is per-reader, so two readers with different policies reach different answers.
That is a genuine exception to everything-converges, and the right one — trust is a policy, not a fact.

### Speculation, which needs nothing new and is not recommended

Blocking is not the only way to handle a branch whose guard is undecided. An agent may **speculate**: post
`dif(X, a)`, proceed down the else branch, and let a later `X = a` produce a contradiction. Nothing has to
be added for this to work.

| what it needs | where it already is |
|---|---|
| `dif(X, a)` as an ordinary post | the constraint domain, §Open |
| a contradiction that does not explode | `{t,f}`, affirmable and **inert** |
| finding what the contradiction poisoned | provenance |
| declining the poisoned derivations | the trust policy above |

**And two decisions taken separately turn out to need each other.** Because conflict is **inert** here —
4QL's `i`-propagation is declined — a violated `dif` does not automatically taint what was derived from it,
so provenance is what locates the damage. Propagating conflict would over-poison; no provenance would
under-poison, silently. Precise poisoning needs exactly that pair, and neither was chosen for this.

There is no backtracking to simulate, because nothing is undone. What speculation produces is
**abandonment with a permanent record**: the derivations stay, marked.

**Its cost is not soundness.** A speculative derivation is not a false assertion but a **contingent** one —
true given its recorded hypotheses — and an implication whose antecedent turns out false is *vacuous*, not
wrong. So *"a partial store is sound, never wrong"* (§CALM) survives intact. The hypothesis's own state is
read by machinery already here: pending is `∅`, confirmed `⊒{t}`, refuted `⊒{f}`.

**What it costs is that facts stop being autonomous.** Every other record here means what it says on its
own; a speculative derivation means what it says *given* something still open. Two consequences follow.
Provenance becomes a **requirement** rather than an enhancement — and it must live **in the record**, since
under partial replication (§Open) a conclusion that outran its premises would be a bare assertion again,
which is §"Two commitments" applied to derivations rather than to episodes. And the remaining cost is
ordinary waste: work done under an antecedent that fails is work done.

**So blocking is the default for being simpler, not for being safer.** A blocked guard needs no provenance
and wastes nothing; speculation buys progress under uncertainty and pays for it in bookkeeping.

### Summaries, and the property a construction can forfeit

Aggregation is the third branch, and the one where building upward **costs** a guarantee the base had. An
answer is truth restricted to a demand; different agents hold different overlapping regions and compute
over what they have, skipping what is still `∅`. That is Morton's **available-case analysis** (Obs. 6.10)
exactly, and his **Thm 6.9** says the resulting summaries can be pairwise consistent on every overlap and
admit **no global joint** — contextuality, manufactured out of a store that was never inconsistent. Every
agent's view is coherent and every pairwise check passes, so no participant can detect it.

**The store is not wrong; the guarantee does not lift.** Atoms converge without an arbiter; statistics over
different demand regions need not. The foreclosure of §"Two commitments" holds exactly as far as identity
stays attached, and the summary map is where it stops.

**Settledness closes it, which enlarges settledness's job.** Prop. 5.2: *without* missing data, restriction
and summarization commute — so summaries taken over **settled** regions are restrictions of one global
summary, and glue by construction. The obstruction needs an unsettled atom to hide in. Settledness has been
treated here as a memo-check device; this makes it the precondition under which anything may be aggregated
at all. What is open is its cost: whether settled regions are large enough, often enough, for that to be a
usable discipline rather than a theoretical one.

### What stays outside, and it is not a meta level

Two things, both already named. The **non-monotone core** (§"Two layers"): a clock reading is not logic,
and the existing repair applies unchanged — date the observation and it becomes a permanent fact about the
past, which is precisely what a `clock_skew(bob, t₁, t₂, δ)` ground *is*. And the **reader's verdict**:
folding evidence into acceptance is not a statement, so it is neither a fact nor derivable. It sits outside
because it is a **decision**, not because it is meta. No stratification is needed anywhere above the base,
and none is used.

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

**And the artifact is larger in one direction and smaller in the other.** Today's runstate is an *ordered*
topic log with typed conventions on top — a message protocol, and a thin one on purpose.

**The transport this design needs is weaker than that.** The store is a growing **set** of ground terms and
merge is union, so unordered, duplicate-tolerant, loss-tolerant broadcast suffices: a reader holding one
agent's posts and not another's simply has a smaller store, which is sound. Not even causal order is
required — missing a cause costs completeness, never correctness. Order is needed in exactly one place, the
**claim**, where `send(expected_seq=)` is a compare-and-swap; and that is single-spawn, which §CALM already
concedes as the one coordinated act. Nothing in the semantics reads a sequence number.

**What is larger is the read side.** A per-functor term index that pattern-walks rather than key-looks-up,
a constraint solver in the read path, and cross-host naming for the holes in posted terms — that last being
what survives of §"The model"'s sharing story now that the shared mutable variable does not. That is the
honest headline cost, and it dwarfs the fold rewrites below. It is also the thing a reader should weigh
first, because everything else here is downstream of being willing to build it.

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
2. **Provenance.** Not built. What it blocks meanwhile: diagnosing `{t,f}`, taint after a premise becomes
   disputed, and attributing a wrong `¬Q` — positive facts need it equally, so it is one mechanism. What it
   would unlock is the annotation branch of §"What gets built on top", including the whole dispute story,
   so this is the single highest-leverage unbuilt thing here.
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
   one irreducibly coordinating requirement — and note it is *not* CALM that prices it: *"run iff no other
   agent is running this"* is a mutual-exclusion requirement, not a query, so the theorem does not speak
   to it. What CALM does say is that any *query* whose answer needs the participant roster is
   non-monotone; single-spawn needs the roster for a different reason, and needs it just as badly.
6. **Whether demand should be the only interconnect**, and not merely the only one that *means* anything.
   The semantics already reads the second way: an answer is truth restricted to the demand, so anything
   arriving unbidden is a cache warm-up with no semantic status, and an implementation may broadcast
   freely without the model noticing. Making it **architectural** — a local view receives *nothing* it did
   not ask for — would bound coordination by the number of live channels, and would forbid unsolicited
   broadcast, which is a real affordance. So the two readings should probably stay apart.
7. **What a conflict means, given that three different things produce one.** **Domain**: re-production
   jitter under a single-spawn violation — a *correct* report of a disagreement the system itself caused.
   **Valuation**: two posters genuinely disagreeing, or one over-claiming its own `¬Q`. Measured
   counterweight for the first: **0 of 3,165** numeric re-productions diverged on the real corpus — which
   measures that the consumers' hand-rolled guards are **working**, not that the hazard is absent; a
   census sees only harms detectable in a log, and is biased low in proportion to the machinery already
   preventing them.

   **The valuation case is now measured (2026-08-22), and the number is not the finding.** Across 2,300
   logs from the two consumer repos: **2,743** `lifecycle.stopped` records and **0** values at a step
   beyond the `final_step` their stop declared; **1,140** `launcher.terminated` records and **0**
   subsequent values or heartbeats. But the zero is structural rather than fortunate — **the deployed
   protocol has no `¬Q`**. The only closure claim is a worker's own `stopped`, so the two independent
   claimants that `{t,f}` represents cannot both exist; and the one *external* claim,
   `launcher.terminated`, is posted after reaping, when the worker is already gone. The census therefore
   shows the mechanism **inapplicable to this corpus** — a legitimate census conclusion — and says nothing
   about a design that adds `¬Q`, since `¬Q`'s absence is the whole explanation.

   Two incidental findings. **411 of 2,743 stops (15%) carry no `final_step`**, so they close nothing and
   delimit no region — which bears on whether settledness would be derivable in practice. And **1,643
   values do arrive after their stop**, every one of them at a step *within* the closed region: teardown
   telemetry (`status`, `phase: "saving"`). Under this design those are ordinary positive posts inside a
   settled region, not conflicts — but the first pass of this census counted them as violations, so the
   loose predicate *"any value after a stopped"* is recorded here as the wrong one.
8. **What `every` is** — a `∀` over a strided region, or a firing schedule. The wire format has three
   demand shapes and the quantifier rule covers two; a sampling demand fits neither comfortably, and the
   library defers exactly this. See `memoizer-index-algebra.md`, where it is recorded that the current
   delta reading is **non-monotone on a read path**.
9. **Whether settled-only aggregation is usable.** §"What gets built on top" closes the summary hazard by
   aggregating only over settled regions, on Morton's Prop. 5.2. That is sound and may be impractical: it
   is untested whether settled regions are large enough, often enough, to compute anything anybody wants.
   The corpus has not been asked, and the question is measurable.

## Related

- `if-built-today-decisions.md` — what was tried and withdrawn, and why.
- `../if-built-today-citations.md` — the verification ledger; every citation above marked CONFIRMED there
  has been read in primary source.
- `../dead_ends/topological-framings.md` — sheaves, formal topology, d-frames: three framings, one cause.
- `definite-clause-maximality.md` — whether this fragment is forced rather than chosen. A third route
  (preservation under homomorphisms) that would answer §"What makes the answer worth having"'s own
  concession that *"two routes agreeing is weaker than three would be"*. Open; two papers unread.
- `demand-driven-reads.md` — the consumer-facing target. **Stale**: it still describes a
  LEFT-JOIN-over-a-grid, `?` as a value, `read`/`force` as verbs, and LISTEN/NOTIFY. Its §5a taxonomy
  survives.
- `prolog-query-layer.md` §3 — the measured answer-subsumption results, reinterpreted: the defect is an
  exact claim on an unsettled term, and it does not arise here because nothing aggregates at write time.
- `memoizer-index-algebra.md` — the emission filter, and why exposing it is not additive.
- `../specs/write-authority.md` — unchanged by any of this; a unique constraint is test-and-set (consensus
  2), where `send(expected_seq=)` is compare-and-swap (consensus ∞).
- `../layers.md`, `../positioning.md` — where this sits.
