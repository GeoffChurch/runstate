# Polarity: told falsity and the four statuses

**Layer:** depends on `0-substrate.md`, `1-logic.md`. The dependency graph is in `README.md`.

**Polarity is one relation a schema may declare**, `told(A, P)` with `P ∈ {pos, neg}`, read as *"A is
true"* and *"A is false"*. Nothing connects `told(A, pos)` to `told(A, neg)`; the logic is unchanged
(`1-logic.md` §"The language"). `¬Q` is **notation in this document** for `told(Q, neg)`, not a construct the
language contains. In a record, `P` is always ground — a region over polarities would tell an atom true and
false at once. Why one relation rather than a partner per subject is §"Polarity is schema, not substrate",
below. A schema that declares no `told` keeps plain relations, and nothing in this file applies to it.

## The threshold rule, and its instances

> **Threshold claims are always available. Exact claims require settledness.**

This is a corollary of the affirmability shape, not an axiom — it states *"only opens are affirmable"* in
the form a rule author needs. What earns it a section is that it keeps instantiating. Four orders here
— a layer above that introduces another order adds its row — in every case the up-set affirmable and its
complement not:

| order | affirmable | never |
|---|---|---|
| **value** | *"a record has a value `⊒ t` here"* | *"and that is the only value"*, unless someone vouched for the key |
| **status** | `⊒ {f}` | `= {f}` |
| **coverage** | entailment | membership in the solution set |
| **constraint** | *"the excluded regions entail `V ≤ 0.35`"* | *"0.3 is still possible"* |

A rule that must be re-derived at each new carrier is a slogan; one that already holds there is a basis
vector.

**Exact equality is not lost, it is a threshold at the right place.** For a ground `a`, `↑a = {a}`, so
`V ⊒ a` *is* `V = a`. What is inexpressible is equality against a **non-ground** term — *"V is exactly
this partial term and no more instantiated"* — which requires ruling out further instantiation, i.e.
negation. Exactness is available precisely where it is meaningful: at maximal elements.

**Matching a non-ground pattern is a threshold claim** — the ordinary case. A body literal
`p(k, f(X))` with `X` free claims *"the value is known to be an `f`-term."* It suspends until that
threshold is reached, then binds `X`; once reached it stays reached. On an
**algebraic** domain the compact elements are the finite partial terms and the sets `↑p` form a basis of
the Scott topology — verified exhaustively for terms, `Flat`, `Set` and products — so *matching against a
pattern is exactly asking a basic open*, and the restriction is a **basis**, not a limit.

Rule bodies **resolve against records** — one-way against a ground fact, the pattern's variables binding
and the fact's not; against a region by renaming its variables apart and conjoining its constraints, which
is CLP resolution against a constrained fact — and agents **post**.

**There is no `var/1` to worry about.** Instantiation tests are the classic non-logical family, and in a
store whose terms refine, `var` is the antitone member of it. Here no posted record ever changes, so a
record's instantiation is a fixed syntactic property, not a state, and an earlier sixth row of the table —
*instantiation: `nonvar` affirmable, `var` never* — has nothing left to apply to. The one live case, waiting
until a pattern matches, is the value row.

Matching also means **every branch is a positive guard that blocks, and none fires *because the others did
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
contradiction *from*. *Ex falso* is not a rule of the logic — it needs a schema `⊥ ⊢ B` for arbitrary `B`,
which the logic lacks. A program could still write an explosive clause, a head ranging over every atom —
`told(B, pos) :- told(A, pos), told(A, neg), atom(B)` — but that is a rule somebody wrote, visible and
removable, not one the logic imposes. What a conflicted premise does is
derive an ordinary atom, which may then disagree with another at the head relation; that is the free
completion one level down, and where a **declared domain conflict** catches what the status layer cannot
(§"Two kinds of conflict", below).

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

**Settledness of a set of atoms.** No posted term ever refines, so there is no value waiting to be ground;
what can be complete or not is a *set of atoms* — a region `Q` — and its completeness splits:

| | what it means | where it lives |
|---|---|---|
| **knowledge** complete for `Q` | every atom of `Q` is **decided**: its status is `⊒ {t}` or `⊒ {f}` | affirmable by exhibiting a **finite cover** — a certificate — but **not derivable** in the fragment, whose bodies have no `∀` |
| **stream** complete | nothing further will be told about `Q`, by anybody | **exhaustion**, hence control |

Only the first is a question the logic can answer, and it needs no exhaustion: a region whose every atom
has been decided is settled whether or not anybody is still working on it. (Settledness of a *question*,
which may bind some of its variables, refines this one where questions are defined.)

**`Q` need not be finite; the *cover* must be.** The conjunction runs over the records exhibited, not over
the atoms they cover, so a single negative region settles an infinite region in one observation. Every
record decides what it covers — a ground fact one atom, a region every atom in it — because no posted
record is existential (`0-substrate.md` §"The model").

**Affirmable when a finite cover exists, and only then.** Three conditions hide in *"a finite cover"*. The
cover must exist: a producer that never finishes, posting one positive per key forever, settles the region
only in the limit. Checking it is entailment into a finite disjunction — decidable for difference and
linear constraints, not for every domain. And a region over an open sort — every member of a set that can
grow — can be covered only by a record over the whole sort, which a poster can honestly tell only if it
knows the sort is closed; that is knowledge of every participant again.

**What settledness does not buy: agreement.** The tempting citation is Power, Koutris & Hellerstein's
Props. 15–16 (2025) — in a join-semilattice, every free-termination state returns the same value — read as
*"two agents who can both stop cannot disagree."* Settledness is not free termination. It is an up-set, and
`{t}` is settled while still able to climb to `{t,f}`: a store holding `p(k, a)` and another holding
`¬p(K, V)` for `K > j`, with `k > j`, both settle the atom `p(k, a)`, answer `{t}` and `{f}`, and join at
`{t,f}`. What is true is weaker and holds of **every** pair of stores, settled or not — their testimony is
jointly satisfiable at the join, because `{t,f}` absorbs both. That is `1-logic.md` §"CALM"'s joint
consistency, not a property of settledness, and *"we never bought consistency"* already said it.

**What it does buy is permanence under growth.** Settledness is an up-set of a union-closed order, so it is
never lost by the **store** growing, and a region settled from any store is settleable from every other, by
adding that store's records. It can be lost only by the **region** growing — which is why a region is fixed
when it is named.

**There is no producer verb and no `freeze`** — `¬Q` is an ordinary post — so there is no
freeze-after-write race. There is no ownership rule either: a reclaimer posting `¬Q` without knowing it is
making a false claim exactly as it would by posting a false `p(k, a)`, under the same enforcement, which is
none.

## Falsity is told, never inferred

**Inferred absence needs no representation at all.** *"Nothing is there yet, so produce it"* looks like it
needs `¬∃` — and under an **open** world that is not merely banned but unknowable, since you can never say
*no answer exists*, only *no answer has arrived*. Nothing needs it, provided production runs because
somebody asked, never because something was found missing — which is how production is driven in the
layer above.

**What *is* representable is told falsity** — somebody posting `¬Q` because they know it. That is an
ordinary fact, affirmable by exhibiting it, and it needs no closed world to license it. The distinction is
the whole of the negative story: **falsity you inferred from silence is unaffirmable and stays so; falsity
somebody posted is data.**

### The rule for posting, in both directions

> **Post what you know.** Nothing retracts, so a post you are not sure of is a defect — in either
> direction, for the same reason, with no vocabulary of its own.

That is not a rule about negation; it is the ordinary honesty condition on any post. A producer that stops
looking and posts `¬Q` is posting what it does not know, which is exactly what posting an uncomputed
`p(k, a)` is.

| act | means |
|---|---|
| **post `Q`** | I know it is true |
| **post `¬Q`** | I know it is false |
| *(say nothing)* | I do not know — *including "I am not looking"* |

The third row is not a stance anybody posts: it is `∅`, the identity of the aggregation. Making it
**unpostable** is the structural half of the fix; the other half is that the mistake stops being silent —
a production inside a region somebody declared empty climbs the status to `{t,f}`.

**`¬Q` is the quantified form of `never`.** `never(a)` says nothing goes at one atom; `¬Q` says nothing
goes anywhere in `Q`. Two forms exist only because a region can be **infinite**: a producer that finished
at key `n` is asserting `never` about infinitely many atoms beyond it, and a region is the only finite way
to say it.

**No author.** Two producers posting the same negative region is **one** record posted twice, not two, and
set semantics collapses it correctly. What an author argument would buy is **provenance** — wanted equally
for positive facts, since `p(k, a)` names nobody either — so it belongs to whatever mechanism eventually
serves both. It is an open item, and the cost of not having it is at the end of this section.

**What a producer may post negatively is what it knows is absent — and that depends on the key.** A
producer that finished at key `n` knows there is nothing beyond it: the tail
`told(p(K, V), neg) :- K > n` is knowledge, because the producer stopped. Inside the produced range it
knows less. Having posted `p(k, a)`, it may add *"and no other value at `k`"* — `told(p(k, V), neg)`
under `V < a` and under `V > a` — only if it knows the key determines the value. That **per-key
complement** is testimony, which `1-logic.md` §"Constraints are asked" permits; it over-claims whenever
another honest producer could post a different value under the same key.

**So a key is a schema's claim about what determines the value, and its granularity is a trade-off.** A
**finer** key — one that names the producer, or a computation precise enough to be deterministic — makes
vouching honest, but a disagreement becomes two separate facts that never read as a conflict. A
**coarser** key makes a disagreement a visible `{t,f}`, but vouching over-claims unless the key really
determines the value. Choosing is the user's, per relation. Vouching is what settling the region
`p(k, V)` — every value at a key — needs (§"The threshold rule"); the posting rule obliges none of it. (An earlier draft obliged every producer to post
`¬(Q₀ ∖ E)` — everything asked other than what it produced is false — which is the per-key complement made
unconditional, and so an over-claim wherever the key does not determine the value.)

### How a party comes to know a negative fact

`¬Q` is the primitive. Coming to know one is a **list**, and this is where a closed-world argument
legitimately returns — as a *route*, not as the semantics.

**The test that separates knowing from merely stopping: could a third party post this knowing only that
the process died?** For **exhaustion** yes — a pid probe suffices. For **falsity** no, because a dead
producer's silence is not evidence about the world. Keep the two orthogonal: collapsing them is what made
a single closure predicate look as though it needed a universal over the producer set to mean anything.
Exhaustion is a fact about a process; none of the ways it arrives is a fact about what exists.

- **A finished producer.** It stopped at key `n`, so there is nothing at `n + 1`. It knows because it ran.
- **A solver.** A propagator that has proved a region has no solutions may post `¬Q`; one that has not is
  posting what it does not know. Not a solver-specific rule — the general one with *know* instantiated.
  Unsatisfiability needs no vocabulary of its own: it is **grounds for a post**, not a third kind of
  absence.
- **A closed producer set.** Everyone who could produce here is done, and no more will appear. This one
  **needs to know who all the producers are** — which is exactly the class of query a coordination-free
  program may not ask — and it is the one that needs arguing, because `1-logic.md` §"CALM" is about not needing them.

**The argument for the third, since the case against it is real.** The danger is not the reasoning but the
*name*: give exhaustion a predicate in the derivation layer and somebody will quantify over it — *"every
producer for `Q` is done, and no atom of `Q` is `{t}`, therefore…"* — which is the unanimity move, needing
to know who all the producers are. The route survives only if the quantification happens **below** the
logic and only the `¬Q` crosses up (`1-logic.md` §"Two layers"), so that **no predicate exists for a rule to quantify
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
(`1-logic.md` §"Types"); and the original global closed-world toggle — which was this move at the wrong granularity,
which is why it failed rather than why the move is wrong.

### The obligation, and the one thing it cannot buy

**It is not enforced and it is not enforceable.** Producing inside a region you declared empty contradicts
your own assertion — and needs no detector, because it *is* the status `{t,f}`. Nothing rejects the post,
and nothing could: refusing a contradicting post is order-dependent, whoever arrives first winning, which
is `1-logic.md` §"Constraints are asked"'s argument against asserting a constraint as an axiom.

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
polarities with `∧` and `∨` and no negation — six of sixteen, and the same six. The two constants count:
`⊤` is the empty `∧`, a fact, and `⊥` is the empty `∨`, a predicate with no clause; the language bans `⊥`
only in heads.

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
one. And it **glues distributively**: an atom's status in the union of two stores is the join of its
statuses in each, exactly — the global picture is computed from the local ones with nothing lost, however
much the stores overlap.

**It is the free completion, one layer up.** `1-logic.md` §"Constraints are asked" meets the same fork at the value
layer — add a collapsing top, or take the powerset — and takes the powerset. The status layer gets the
identical answer.

**A status is computed, never stored.** The store holds records over *regions* — one `¬Q` speaks for every
atom of `ground(Q)` — so an atom's status is whatever the covering records say, joined. That puts weight
on *covering*, and there is one safe reading:

> **Coverage is an entailment claim, never a membership claim on the solution set.**

When holes in posted terms could later be bound, the generous reading — cover every atom a region *might*
contain — shrank on binding: measured, it descended on **12 of 12** bindings where the entailed reading
descended on **0**. With variables scoped to records no region is ever narrowed after posting, but the rule
still decides what a coverage checker may conclude. The rest of the model holds as assumed: over 25 stores, statuses are order-independent (0 disagreements in 1,500 arrival shuffles) and
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
roster, which `1-logic.md` §"CALM" names as the single non-monotone input. It also replaces a stance with a test that has
a yes-or-no answer — *does this operation reverse `≤_k`?*

**The constraint binds on output, not computation.** A scheduler may consult the `∅`-region mid-flight
freely; publishing such a reading as an answer is what leaves the monotone class. The reference model
makes exactly that split — output append-only by hypothesis, working memory admitting deletion.

**Restricting to a set of atoms is the knowledge-order meet.** With status held as a **pair of extents**
`⟨P, N⟩`, restriction to a set `D` **is** intersection applied to both, `⟨P ∩ D, N ∩ D⟩` — the meet `⊗`
with `⟨D, D⟩`. It is not the truth-order `∧` with `D` read as a total valuation `⟨D, D̄⟩`, which gives
`⟨P ∩ D, N ∪ D̄⟩` and marks every atom outside `D` false: the coercion that made masking look one
operation from a bug, and not writable here.

## Two kinds of conflict

| | what disagrees | how many atoms | who says so |
|---|---|---|---|
| **valuation conflict** | whether the atom is there at all | **one** | the store, structurally: the status is `{t,f}` |
| **domain conflict** | two atoms violate a constraint somebody declared | **two** | a user-written rule (`1-logic.md` §"Constraints are asked") |

The generic half is the status: `⊒ {t,f}` needs no declaration, no key, and no functor-specific
knowledge. A domain conflict is per relation, declared by whoever knows which positions determine which.
And one turns into the other through vouching: `p(k, a)` and `p(k, b)` are a domain conflict, until a
producer posts the per-key complement for `a` (§"The rule for posting") — then `p(k, b)` reads `{t,f}`.

The status order has a top, `{t,f}`, and it is harmless where a value-order top would not be: it is a top
over *what you were told*, not over the value, so it satisfies no threshold a rule reads on the value.

## Narrowing is excluding

Narrowing what is known about a value across records needs no unknown shared between them: it is posting
**negative regions** keyed by content. A producer that has bounded the value at `k` to `[0.25, 0.35]` posts
`told(p(k, V), neg)` under `V < 0.25` and under `V > 0.35`. Those accumulate, the tightest bound is the
union of what has been excluded, and a threshold read — *"is the value at `k` below 0.4?"* — is decided the
moment the excluded regions cover everything at or above it. The key, not a variable, ties the narrowings
together; and several producers narrowing the same key compose without coordination, since each posts
only what it excluded.

## Polarity is schema, not substrate

Polarity is a **signature declaration** like any other: a schema declares the relation `told(A, P)`, and
thereby reads each atom in two directions. The substrate stores records and knows nothing about it; the
four-value reading, `⊒{f}`, and everything §"An atom's status" says all exist **relative to that
declaration** and are simply unavailable to a schema that makes none.

Three things follow. **Polarity is opt-in** — a user who wants plain relations has them, and nothing in
the substrate is wasted. **It rides the signature channel**, so two agents disagreeing about it is the
same failure as disagreeing about a sort, with the same fix and the same unenforceability. And
**nothing about a negative relation is structurally special**: it is permanent, unauthored and
region-capable exactly as a positive one is, which is why the reclamation tiering (§"What is checked, and
what is the requester's", below) turns on re-derivability rather than on direction.

What *is* asymmetric is informational and belongs to the data rather than the schema: absence is uniform,
so a negative relation's extension usually compresses into one record where a positive one does not. That
gives a negative record more **reach**, so a wrong one does proportionally more damage — a reason for care,
not a reason to treat the direction as a different kind of thing.

**The representation: one relation with the valuation as data, not a naming convention per subject.**
Declare

```
told(A, P)      A : Atom,   P : Pol = pos | neg
```

once, and keep `told` **out of `Atom`**, so no atom is itself a `told` record. Atoms are untouched —
`p(K, V)` keeps its own per-functor signature, and `told(p(K,V), pos)` / `told(p(K,V), neg)` are
its two directions. `¬Q` is notation for `told(Q, neg)`.

**What changes is the shape of the atom set.** A partner relation per subject gives `Atoms(p) ⊔ Atoms(p⁻)`
— a coproduct of two independently declared functors, isomorphic to `2 × Atoms(p)` only when the
declaration is honoured, which nothing verifies. `told` gives `Atom × 2` by construction, and four things
follow from that one fact rather than from four separate arguments:

- **`¬¬Q` is a type error.** `told(told(x, neg), neg)` needs its first argument in `Atom`, and `told` is not
  in it. An endomorphic `¬` would have to be a constructor *of* the term algebra, and in a free algebra
  `not(not(Q))` is a **distinct term** from `Q` that no type discipline can collapse.
- **The two directions cannot drift.** Both are the same relation at the same argument sort, so a
  mismatched arity between them is unwritable.
- **The status fold is a transpose.** *"The set of things producers have told you about it"* is
  `Set(A × 2) → (A → P(2))` — currying, nothing more; with polarity as data the store *is* the relation
  being curried. With a partner relation the fold must consult the pairing to know which atoms to group,
  and the pairing is what no tool checks.
- **Polarity is polymorphic for free.** Because `P` is an argument, a rule may leave it open.
  `conflict(A) :- told(A, pos), told(A, neg)` is one rule, generic over atoms, naming the conflict by the
  **atom** rather than by a direction the conflict does not have. *Dispute the opposite of what an
  untrusted source said* needs two facts over `Pol` alone — `flipped(pos, neg). flipped(neg, pos).` — and
  involutivity is provable from them.

**And it is the form that generalises.** `README.md` §"What gets built on top" reads polarity as the two-element case
of a declared marker set: a third marker is a third value of `Pol`, where under partner relations it would
be a third naming convention with nothing relating it to the first two.

**On the wire this is the closed-set rule rather than a preference.** Serialisation forces polarity into a
field whatever the language does; what differs is that field's domain. A partner relation per subject puts
it in the **functor name** — an open namespace, recovered by string surgery on a prefix. `told` puts it in
a field with **exactly two values**, which a schema can pin with `additionalProperties: false`. The field
stays two-valued because a record's `P` is always ground (top of this file).

**None of which prevents anything.** The substrate typechecks nothing, and a schema declaring its own
endomorphic `not/1` gets double negation with no objection — the same unenforceability that applies to
sorts, above. The claim is only static checkability's: a closed set known at authoring time should be
spelled so a tool can check it, and two polarities is closed where a functor namespace is open.

