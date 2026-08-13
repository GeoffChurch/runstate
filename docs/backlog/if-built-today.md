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
terms — no repeated variables — a term is a variable-disjoint partial function from paths to symbols,
so two terms merge iff they agree where both are defined and consistency is **pairwise**: a union of
partial functions is a function iff they are pairwise compatible. A mergeable group is therefore exactly
a **clique** in the compatibility graph `G`.

Greedy merging — put each arriving term in the first blob it fits, else start a new one — is then
**first-fit colouring of the complement `Ḡ`**, since a colour class in `Ḡ` is a clique in `G`. That
identification needs a second lemma, which is easy to miss: first-fit tests a candidate against *every
member* of a colour class, whereas blob-merging tests it against the blob's **merged term**. Those
coincide for linear terms, because the merge's domain is the union of the members' domains and its
values are inherited — measured, 26,612 pairs and 0 disagreements. For non-linear terms they do **not**
(321 disagreements in 6,441), which is a second reason the model is linear-only.

All three cases then come off the shelf:

| | value | |
|---|---|---|
| **best** | `χ(Ḡ)`, the clique cover number | the optimal ordering — NP-hard to find, and not merely by analogy: **every** graph is realisable as the conflict graph of linear terms (one position per edge, `u ↦ 0`, `v ↦ 1`, wildcards elsewhere), so the optimum *is* graph colouring |
| **worst** | `Γ(Ḡ)`, the **Grundy number** | the most parts first-fit can be made to produce, definitionally |
| **average** | → 2× optimum **asymptotically** | Grimmett–McDiarmid gives greedy `~n/log_b n` against Bollobás's `χ ~ n/(2 log_b n)` — but that is an Erdős–Rényi asymptotic and it converges glacially. Measured, the ratio is **1.04–1.20** up to n=24 with exact `χ`, and **1.21–1.32** up to n=500, on ER and on real term-conflict graphs alike |

**The gap is not a constant factor, and it is reachable at ordinary arity.** On the crown graph —
`K_{n,n}` minus a perfect matching — `χ = 2`, but first-fit on the interleaved order `u₁,v₁,u₂,v₂,…`
uses `n` colours. The naive term realisation would need arity `n`, which would make the blow-up an
artefact of absurd width; it does not. Taking a Sperner antichain `T₁…Tₙ ⊆ [k]` and setting `uᵢ = a` on
`Tᵢ`, `vᵢ = b` off `Tᵢ`, needs only `k ≈ ⌈log₂ n⌉` positions — measured, a **12-position functor gives
`χ = 2` against 924 first-fit blobs.**

So the average row and the crown row are not the same kind of statement: the crown is a real separation
at realistic arity, and the `2×` is an asymptote nothing at reachable sizes approaches.

Two caveats. Refusing to merge when *two* candidates match — a tempting way to buy determinism — is
strictly worse on size than first-fit, because it adds a part exactly where first-fit would have merged.
And **non-linear terms break the model** at both lemmas: with a repeated variable, pairwise consistency
no longer implies joint (`f(X,X)`, `f(a,Y)`, `f(Z,b)` are pairwise mergeable and jointly contradictory),
so the clique picture is a lower bound on the difficulty rather than the answer.

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
| *no information about this value yet* | an unbound variable |
| "this is demanded" | an existential not yet satisfied |

(The middle row said `⊥` in an earlier draft. Two different bottoms: an unbound variable is the least
element of the **instantiation** order on a value, where `∅` is the least element of the **status** order
on an atom. Nothing relates them, and one symbol for both invites reading a demand as a falsity.)

So there is no `demand` predicate at the surface, no `while` combinator, and no watcher concept. A
watch is a posted term with a free variable; bindings arrive as they are learned.

**Answers stream individually, and falsity is a separate posted fact.** A querier posts a pattern and
receives matching atoms one at a time; there is no answer *object* anywhere. What ends a stream is the
post `¬Q` — *"everything in Q is false"*, where `Q` is whatever the poster actually knows
(§"Falsity is told, never inferred") — delivered like any other fact. *Completeness* is not posted at
all; it is read off the statuses (§"The threshold rule").

That is worth stating because packaging the answers into a growing term is a tempting and dead end.
Nothing bindable is unordered: a set term is ground, so adding to it is not a binding but a different
term, which leaves a **list** — and a list fixes an insertion order, so two nodes learning the same
answers in different orders build `[a,b|T]` and `[b,a|T']`, which unification cannot reconcile because
it cannot reorder a spine. The divergence is representational rather than an information deficit, so
redelivering every message does not repair it. (A node merely *lagging* on an order-preserving link is
fine — lag and divergence are different failures.) A shared tail is also a lossy CAS: once one producer
binds `T = []`, another's next answer is rejected outright. One tail *per producer* fixes all of that,
since each stream is then single-writer — and that is the right **structure** in the wrong
**representation**. What a per-producer tail encodes is one termination marker per producer per stream,
which is precisely `¬Q`: the same marker, posted rather than bound, and readable without binding
anything. So what the dead end kills is the **term**, not the idea.

A set term with an unbound "rest" would sidestep the ordering, and costs more than it saves: union
modulo associativity, commutativity and idempotence is **finitary rather than unitary**, so the join of
two such terms is a *set* of most-general unifiers rather than one term, and matching modulo ACI is
NP-hard.

**So inferred absence needs no representation at all.** *"Nothing is there yet, so produce it"* looks
like it needs `¬∃` — and under an **open** world that is not merely banned but unknowable, since you can
never say *no answer exists*, only *no answer has arrived*. It turns out nothing needs it, because
production is **demand-gated**: a producer runs because somebody asked, never because something was found
missing, so no rule infers absence at all.

And "demand-gated" needs no machinery either. **A demand is a posted pattern; a producer is an agent
that watches for patterns it can serve, computes, and posts atoms that unify with them.** There is no
`demand_p` relation and no gating rule — which matters, because writing one as
`value(X,V) :- demand(X), handler(X,V)` smuggles a key/value split back in through the adornment, and
this design has no cells. A producer needing something of its own just posts a pattern too, which makes
it a querier; the roles stay symmetric all the way down.

What *is* representable is **told** falsity — somebody posting `¬Q` because they know it. That is an
ordinary fact, affirmable by exhibiting it, and it needs no closed world to license it (§ below). The
distinction is the whole of this design's negative story: falsity you **inferred from silence** is
unaffirmable and stays so; falsity somebody **posted** is data.

### Falsity is told, never inferred

**One predicate was doing two jobs**, and every confusion in this area comes from that. Only one of them
is a fact about the world, and it needs no predicate either — it is the other thing you can be told:

> **Post `Q` to be told true. Post `¬Q` to be told false.** One mechanism, two directions, matching a
> codomain in which `{t}` and `{f}` are the two things anybody can say.

An earlier draft wrote the second as `absent(Q, p)`, and both halves of that were wrong. **The wrapper
was a fossil** of the design where falsity was *derived* rather than told, and so needed machinery that
truth did not; under Belnap the two are symmetric and their syntax should be. **And the word fought the
design** — *absent* names falsity as a **lack**, something not being there, which is the reading this
section exists to delete. `{f}` is not a hole. It is an assertion somebody made.

The other job was *"p will send no more for `Q`"* — **exhaustion** — and it is not a predicate at all. It
is a claim about a **process**, which this library already produces three ways: `p`'s own
`lifecycle.stopped`, an observer's `launcher.terminated`, and a pid probe. Naming it alongside `absent`
would imply the two are one kind of object differing in content, where the whole finding is that they are
different kinds of thing.

The test that separates them: **could a third party post this knowing only that the process died?** For
exhaustion yes, for absence no — a dead producer's silence is not evidence about the world. That is the
launcher-versus-lifecycle split this library already has, kept orthogonal for exactly this reason, and
collapsing the two is what made a single closure look as though it needed a universal over the producer
set before it could mean anything.

**Exhaustion never enters the derivation layer, and keeping it out is load-bearing rather than tidy.**
Its consumers are control (*"stop waiting"*) and reports (*"this run was abandoned"* — a negation, hence
a report regardless). No rule reads it. Give it a predicate and somebody will quantify over it —
*"every producer for `Q` is done, and no atom of `Q` is `{t}`, therefore…"* — which is the unanimity move
again, needing to know who all the producers are. The vocabulary is where that deletion has to be
defended, because a name is an invitation.

**And its two signals differ in reliability, which is a second reason not to give them one name.** *"p is
done with `Q`"* is finer than *"p is dead"* — under demand-gating `p` may have finished one region while
still working another — but the fine version exists only if `p` volunteers it, and §"What is checked"
rules that out as a foundation: **reclamation must not depend on receiving a message.** The coarse probe
is the dependable one, and it **abstains off-host** (§"What it does NOT solve"), where the only signal
left is a record somebody posted. So exhaustion is a probe here and a log record there — and a record in
the log is still not a term in the logic.

**`¬Q` is the quantified form of `never`** — the same determination at a different granularity.
`never(a)` says nothing goes at one atom; `¬Q` says nothing goes anywhere in `ground(Q)`. Two forms exist
only because a region can be **infinite**: a producer that converged at step 400 is asserting `never`
about infinitely many atoms, and a quantified statement is the only finite way to say it. So there is one
concept here, not two.

**No author, for the same reason.** An earlier draft carried the producer as an argument, because the old
aggregation quantified over producers and needed an index into that quantification. Union does not: two
tellers is not more false than one, so `p` and `q` determining the same region empty is **one** atom
posted twice rather than two atoms, and set semantics collapses it correctly. What `p` was really buying
is **provenance** — and provenance is wanted for positive facts too (`loss(60,0.5)` names nobody either),
so it belongs to whatever mechanism eventually serves both, not to one side of a symmetric pair. It is an
open item, unbuilt. The cost of not having it is stated at the end of this section.

**And the rule for posting is the same in both directions:**

> **Post what you know.** Nothing retracts, so a post you are not sure of is a defect — in either
> direction, for the same reason, with no vocabulary of its own.

That is not a rule about negation. It is the ordinary honesty condition on any post, and an earlier
draft's *"I have determined there is nothing there"* was that condition dressed as a special epistemic
act — and phrased spatially besides, as though falsity were an empty place rather than a truth value.
Falsity needed the dressing while it was **inferred**, because you had to separate *"I know it is false"*
from *"I stopped looking"*. Told falsity needs none: a producer that stops looking and posts
`¬Q` is posting what it does not know, which is exactly what posting an uncomputed `loss(60, 0.5)` is.

It still decides every case, and more cheaply. A converged producer posting `¬Q` over the unbounded
pattern **knows** it — the run ended at 400, so there is no loss at 500. A producer asked for `0..100`
that posts `¬(0..1000)` knows nothing about `101..1000`. Posts **accumulate** — the known region only
grows, and a querier arriving after `p` exits inherits everything `p` posted, with subsumption working
inside it.

**And the region to post is not the one you were asked for.** Answering `Q₀`, a producer must post

```
¬(Q₀ ∖ everything in Q₀ it will ever post positively)
```

or it contradicts its own output. Note that region **is a residual** — `Q₀ ∖ E` with `E` finite, the same
construction §"What is checked" describes — now appearing on the negative-post path as well as the
production path. And note it is **forward-looking**, which is what makes *"post at convergence or exit"* a
consequence rather than a rule of thumb: you cannot compute *what I will ever produce* until you are done
producing.

**And it is load-bearing for the aggregation, more so than it used to be.** An atom is `{f}` as soon as
**one** producer says so (§"Where the rules come from"), so a single agent posting what it does not know
creates falsity outright, where an earlier draft's unanimity rule would have needed everybody.

| act | means |
|---|---|
| **post `Q`** | I know it is true |
| **post `¬Q`** | I know it is false |
| *(say nothing)* | I do not know — *including "I am not looking"* |

The third row is not a stance anybody posts: it is `∅`, the identity of the aggregation. Every failure
this design has had in this area was a case belonging in that row being posted as one of the first two,
and making the row **unpostable** is the structural half of the fix. The other half is that the mistake
stops being silent — a production inside a region somebody declared empty climbs the status to `{t,f}`,
where under unanimity a wrong refusal only ever contributed to a conjunction and nothing ever
contradicted it.

**The obligation is on the producer, it is not enforced, and it is not enforceable.** Producing inside a
region you declared empty contradicts your own assertion — and it needs no detector, because it *is* the
status `{t,f}`. Nothing rejects the post, and nothing could: refusing a contradicting post is
order-dependent, whoever arrives first winning, which is the argument §"No functional dependency" makes
against asserting functionality as an axiom. Same shape, same answer — record both, observe the conflict.

**But detectable is not the same as diagnosable, and that is where the missing provenance is felt.**
`{t,f}` says two claims disagree. It does not say whether that is a real disagreement about the world —
two producers with genuinely different results — or one producer that over-claimed the extent of its own
`¬Q` and then produced inside it. Those want opposite responses, and telling them apart needs to know who
posted what. So the sloppy-scope failure is **monotone** (a climb, never a retraction) and **visible**,
and still costs something: it spends a conflict report on a non-conflict. That is Open #7's ambiguity
arriving by a second route.

**A solver knows by proving unsatisfiability.** A propagator that has proved a region has no solutions may
post `¬Q`; one that has not is posting what it does not know. Not a solver-specific rule — the general one
with *know* instantiated for that kind of producer. And unsatisfiability needs no vocabulary of its own:
it is **grounds for a post**, not a third kind of absence.

Two refuted framings, recorded so they are not rediscovered:

- **Closure over a fixed *extent* — everything `p` was ever responsible for — rather than over what it
  has finished.** Tempting because one closure then covers every future question. But under
  demand-gating a producer emits only what was asked, so an extent-closure asserts refusal over regions
  it may still be asked about; produce there later and it has broken its word. Naming the finished region
  costs nothing and cannot be wrong.
- **"A producer that claims the whole axis and emits half of it is a forgery."** This indicts every
  honest producer: under demand-gating *every* producer emits a fraction of what it could. The defect
  was never dishonesty, it was closing more than you are done with.

**And there is no closed-world toggle.** An earlier draft had a second level — *"no further producer will
appear for `Q`"* — because falsity was **inferred** from unanimous refusal, and a universal over an open
set is unaffirmable. With falsity **asserted** instead, that level has no job: nobody needs to know who
else exists in order to state what they themselves determined. The per-functor scopes, the reified
`spawns(P,Q)`, and the write-authority worry about who may close a scope all go with it, since there is
no longer a scope to close.

That is Ameloot's characterisation paying for itself (§"CALM"): a universal over the producer set is a
query about **network membership**, which a coordination-free program may not ask. This design has
exactly one such query left — single-spawn — and it is already priced as irreducible (§Open).

**And what makes the assertion coordination-free is not that it names an author** — it does not name one
— but that it is a **testimony rather than a survey.** Stating what you know consults nothing outside
you; inferring absence from everybody's silence consults everybody. An earlier draft credited the
producer argument for this, which was the old semantics' reason, not this one's.

**Which is why "CWA" is the wrong name for what is left, and this section no longer carries it.** The
closed-world assumption is an **inference rule** — *what is not provable is false* — and nothing here
applies it. Nor is `¬Q` a **local** closed-world statement (Etzioni/Golden/Weld; Levy), which is also an
inference licence: *"trust my silence in this region."* Reading `¬Q` means looking at what is **present**;
LCW means looking at what is **missing**, which is the non-monotone act this design exists to avoid. So
the completeness-statement lineage is the wrong one for the semantics — though its *machinery* still
applies, since transfer to a subsumed question is query containment either way.

Where the closed world does survive is where every non-monotone thing survives: a **report** may negate
over the store's contents, and *"which steps have no atom?"* is a closed-world question asked at the
boundary that permits them. It needs no toggle, no posted fact, and no section.

**One distinction survives the deletion**, and conflating it with absence is what makes a correct answer
look like a failure. *"No more answers will arrive for this query"* is **exhaustion**: a fact about
processes, that every producer which will ever act has finished. *"For every atom in the query I know
true or false"* is **knowledge**: the status layer. A region nobody ever asked about has a complete
stream and `∅` knowledge, and reporting *unknown* there is right rather than broken.

Exhaustion is what a standing query needs in order to **stop**, and as far as this design goes nothing
else needs it — so it stays outside, where the probe and the lifecycle records already are. The reason it
cannot be inside is the one §"CALM" gives: a producer's silence is indistinguishable from its slowness,
and no finite observation of the log tells them apart.

**One thing that is not an optimisation, though it looks like one.** Production is gated on demand, and
before running a producer the evaluator asks *"do I already have an answer?"* That memo check **is the
cache** — without it the producer re-runs on every call, and a run here is a six-hour job. It is also
semantics rather than housekeeping: where re-production is not bit-identical, removing it changes the
answer set outright and flips `conflicted(K)` from false to true. An optimisation may not change the
answer set; this one does.

**But it is not a memo *table*, and reading it as one imports a mechanism this design does not need.**
The store **is** the cache, and presence is checked **per atom** — a lookup in the index, not a hit-or-miss
verdict on a whole call. Two problems that look serious under the call-keyed reading evaporate under the
per-atom one:

- *"Do I already have an answer?"* keyed on **answers** is right for a ground demand and wrong for a
  pattern — one answer arriving for `loss(S,V)` would make the whole range look served. Per atom, the
  producer walks its extent, finds step 0 present, and carries on to step 1.
- Keyed on **calls**, two demands that overlap without either containing the other — `0..100` and
  `50..150` — each miss and each run in full, so the overlap is produced twice. Per atom, the second
  producer skips what is there and runs on `101..150`.

**So what the producer is handed is a residual, not a verdict**: the call minus everything already
settled either way — the `∅`-region of `Q`, which is `Q ∖ E` with `E` the finite set of atoms whose
status is anything but `∅`. Subtracting the `{f}` part matters as much as the `{t}` part: an atom
somebody determined absent is one nobody should be asked to produce. Same construction §"What is checked"
describes for the handler, applied one level earlier. It need not be materialised; walking the extent and skipping
present atoms computes it incrementally.

**And that removes a mechanism rather than adding one.** A demand entirely covered by another has an
**empty residual** and costs nothing, without anybody detecting that it was subsumed — so containment
compaction is unnecessary on the production path, and with it the whole question of how expensively
`n` demands can be compacted against each other. Compaction still earns its place in **notification**,
where the job is which of ten thousand standing cursors to walk when an atom lands; that is about who
gets told, not about what gets built.

What survives is the concurrent case: two producers serving overlapping demands can each produce an atom
before either sees the other's output. That is **single-spawn**, the one irreducibly coordinating
requirement, and it is priced there rather than here.

One representation note. Per-atom is the *semantics*; over a wide axis, *"what is present"* is naturally
stored compressed — intervals, runs — so that computing a residual does not mean walking a billion steps
to find the gaps. Same split as everywhere else here: meaning per atom, storage as dense as it likes.

### Where the rules come from: an atom's status

The rules above are easier to justify than to state, and the justification is a semantic model rather
than a second syntax. Geometric logic remains what you *write*; this is what the writing *means*.

**An atom's status is the set of things producers have told you about it.** There are two tellable
things — *true*, *false* — so there are four statuses, and they are the subsets of `{t, f}`:

| status | told | |
|---|---|---|
| `∅` | nothing | nobody has determined either way |
| `{t}` | true | some producer produced it |
| `{f}` | false | some producer determined it absent |
| `{t,f}` | both | one produced, another determined absent — a **valuation conflict** |

**The aggregation is union**, and that is all of it. Union only grows, so a status only ever climbs
`∅ → {t}`, `∅ → {f}`, `{t} → {t,f}`, `{f} → {t,f}` — monotone **by construction** rather than by
argument, and monotone in a *growing* producer set rather than only a fixed one, which is the regime
this design is actually in.

**And a status is computed, never stored.** The store holds records over *regions* — one `¬Q` speaks for
every atom of `ground(Q)` — so an atom's status is whatever the covering records say, joined.
That puts the weight on *covering*, and there is exactly one safe reading of it:

> **Coverage is an entailment claim, never a membership claim on the solution set.**

Which is §"The demand language"'s rule one level over, and load-bearing rather than tidy. A region may
itself have a hole — `¬(loss(S,V) ∧ S > N)` with `N` unbound is the same *"a hole is an open
question"* move §"Facts and demands are dual" makes uniform — and read generously such a claim covers
every atom `Q` *might* contain, so binding `N` **shrinks** it. Measured: the generous reading descends on
**12 of 12** bindings (witness — `N` unbound covers all twelve steps; `N := 0` drops step 0, whose status
falls `{f} → ∅`), where the entailed reading descends **0**. Entailed coverage grows under binding *and*
under narrowing, since fewer candidate values for `N` means more atoms provably above all of them.

The rest of the model holds as assumed: over 25 stores, statuses are **order-independent** (0
disagreements in 1,500 arrival shuffles) and **monotone** (0 descents in 7,800 arrivals).

**One consequence to state rather than leave to be discovered.** Deciding coverage runs through the
solver, and §"The demand language" makes entailment checking deliberately **incomplete** — so an agent
that has not propagated far enough reports `∅` where a better-propagated one reports `{f}`. Measured
sound: the under-propagated status sits **below** the complete one in 144 of 144 cases and never above.
Incompleteness costs knowledge, never soundness, which is the trade already accepted there. The price is
that a status is **relative to the propagation done**, hence local — which §Open #5 already says of
everything else here.

**It is the free completion, one layer up.** §"No functional dependency" meets the same fork at the value
layer — add a collapsing top, or take the powerset — and takes the powerset, because a top satisfies
every threshold. The status layer has the identical fork and gets the identical answer. Taking it in both
places is what makes these one design rather than two.

**Which is why there is no third stance to post.** *"I might"* is `∅`: nothing told, the identity of the
aggregation. It needs no representation, because you do not announce what you have not determined.

**And why it is not *"false iff every producer refuses"***, which an earlier draft had. That rule is the
De Morgan dual of *"true iff some producer produces"*, and the dual is correct only while *will not* is
the **complement** of *produce* — *"I won't send you one."* Determination makes it an independent
positive assertion about the world, at which point two independent claims aggregate by union and the dual
is the wrong shape. Measured over every stance-vector up to four producers: the unanimity rule **descends
8 times in 360 extensions** of the producer set (witness — one refusal gives `false`; a second producer
appears and it drops back to `unknown`), where union descends **0**. Union is also at least as
informative at all 121 vectors and strictly more at 86, of which **64 are cases unanimity reported as
plain `true`** while some producer had determined the region empty.

**What is readable is the up-sets, and there are exactly four.** A rule body may claim `⊒ {t}`
(*somebody produced it*), `⊒ {f}` (*somebody determined it absent*), `⊒ {t,f}` (*disputed*), or their
union (*somebody said something*). Each is affirmed by exhibiting a witness, so each is an open — this is
§"The threshold rule" at the status layer, not a new discipline.

`∅` is the one thing **not** readable: *"nothing has been told about this"* is a down-set, so no finite
observation affirms it. That is the open-world assumption recovered as a fact about the codomain's
topology rather than stipulated — and it is why the residual, which wants exactly the `∅`-region of a
call, is a report and not a derivation.

**A demand is an open set, and an answer is truth restricted to it.** Not a *total* valuation: only the
demand's true-set is ever used, and the store can affirm *asked* but never *not asked*, so totality does
no work and manufactures a hazard. With status held as a pair of extents — what has been told true, what
has been told false — restriction **is** conjunction, applied to both, and the coercion that made masking
look one operation from a bug (`unknown ↦ false`, then `v ∧ false = false`) is not writable at all.

**One retraction.** An earlier draft called the negative post a **Clark completion**. It is not: Clark's is
per-predicate over *all* clauses and cannot be false, where this is per-contributor, asserted at runtime,
by a party that may be lying. The right lineage is the local-completeness literature, and the citation is
outstanding.

**Streams need no concept at this level.** A producer's contribution is a valuation that climbs; the
"stream" is the climbing, not an object — which is why answers stream individually and no answer term
exists anywhere.

**And restriction is not a poor relation of conjunction; it is the sheaf map.** Since a demand set is a
union of `↑p` it is **open**, so a local view is truth restricted to an open — an agent is an **open
subspace**, and its view is a *section*. Truth is then a **sheaf** over the space of ground atoms, and
three things stop needing separate arguments:

- **Geometric logic becomes forced rather than chosen.** Agents are open subspaces, the maps between
  them are inclusions, and geometric logic is exactly what transports along those. §"CALM" argues this
  from coordination; here it falls out of the architecture.
- **`conflicted(K)` is failure of the gluing condition.** Sheaf gluing requires sections to agree on
  overlaps; two producers disagreeing at one atom is an **incompatible family** — local sections that
  cannot be assembled into a global one. That is a better account of conflict than "two atoms with no
  upper bound," and it is the same fact.
- **Lagged replication stops being an optimisation.** A local store *is* the demanded fragment by
  definition; lag is a section not yet extended.

The semantics is therefore defined **as if demand were the only channel between an agent and the world**.
That is a statement about meaning, not a prohibition: anything arriving unbidden is a cache warm-up with
no semantic status, so an implementation may broadcast freely without the model noticing.

**And the treatment stays uniform right up into the question.** A demand whose extent is itself partial —
`loss(S,V) ∧ S ≤ N` with `N` unbound — is a query with a hole, sharpening as somebody binds `N`. Nothing
here needs it and nothing forbids it; it is worth noticing because it is the same *"a hole is an open
question"* move one level up, and a framing that had to make an exception there would be the wrong
framing.

**Two cautions.** A valuation over all ground atoms is an infinite object: this is semantics, not a
storage proposal, and the store is its sparse, mostly-`unknown` representation. And valuations into a
chain under pointwise operations form a complete distributive lattice, which *has* implication whether
anyone wants it or not — the same caution as the frame (§"The demand language"), with the same answer:
the model may support it, the syntax does not write it.

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

## The threshold rule, and its five instances

> **Threshold claims are always available. Exact claims require settledness.**

**This is a corollary, and an earlier draft billed it as an axiom.** It predates the geometric framing and
was the original organising principle; §"Geometric logic" then subsumed it, its own table carrying the
row *threshold claims only | **opens***. So what this states is *"only opens are affirmable"*, in the form
a rule author needs it — which earns a section, but not primacy.

**The interesting fact is that it keeps instantiating.** It has landed on five different orders, two of
them arrived at long after it was written, and in every case the up-set is affirmable and its complement
is not:

| order | affirmable | never |
|---|---|---|
| **value** | `V ⊒ t` | `V = t` on an unsettled term |
| **instantiation** | `nonvar`, `ground` | `var` |
| **status** (§"Falsity is told, never inferred") | `⊒ {f}` | `= {f}` |
| **coverage** (§"Where the rules come from") | entailment | membership in the solution set |
| **constraint** (§"The demand language") | *"the store entails `S ≤ 100`"* | *"5 is still possible"* |

That recurrence is the claim worth making. A rule that has to be re-derived at each new carrier is a
slogan; one that turns out to already hold there is a basis vector, which is what §"Design rigor" means by
the payoff being *serendipity*.

A rule body may claim `V ⊒ t` — *"the value carries at least this much information."* Monotone by
construction: values only go up, so once true, always true.

**And exact equality is not lost, it is a threshold at the right place.** For a ground `a`, `↑a = {a}`,
so `V ⊒ a` *is* `V = a`. What is inexpressible is equality against a **non-ground** term — *"V is
exactly this partial term and no more instantiated"* — which requires ruling out further instantiation,
i.e. negation. Exactness is available precisely where it is meaningful: at maximal elements.

**Settledness is groundness for a *value*, and something else for a *query*.** A value is settled when
its term is ground, and that half is unchanged. But a query has no answer term to ground (§"Answers
stream individually"), so its settledness is not the same test on a different subject. It splits, along
the line everything else here splits along:

| | what it means | where it lives |
|---|---|---|
| **knowledge** complete for `Q` | every atom of `ground(Q)` has status `⊒ {t}` or `⊒ {f}` | affirmable by exhibiting a **finite cover** of `Q` by posted records — **derivable** |
| **stream** complete for `Q` | no further answer will arrive, from anybody | **exhaustion**, hence control (§"Falsity is told, never inferred") |

Only the first is a question the logic can answer, and answering it needs no exhaustion: a region whose
every atom has been determined is settled whether or not anybody is still working on it. The second is a
fact about processes, and the logic never had jurisdiction over those.

**`ground(Q)` need not be finite; the *cover* must be.** That is the ordinary geometric condition rather
than a new one — the conjunction runs over the records exhibited, not over the atoms they cover, so a
single `absent` claim settles an infinite region in one observation. Which is exactly why the quantified
form exists at all (`:219`): requiring a finite region would make quantifying pointless. A region
determined only atom-by-atom, with no quantified claim anywhere, is not affirmable — and is not complete
either, so nothing is lost. (Finiteness of what you *asked for* is a separate obligation with a separate
owner, §"What is checked".)

**There is still no producer verb and no `freeze`**, though the reason has changed: it used to be that
closing a tail is an ordinary post of the terminating constructor, and it is now that `¬Q` is an ordinary
post, full stop. Either way nothing is frozen, so there is no freeze-after-write race.

It follows that **nothing may derive settledness from demand going quiet.** Demand disappearing
determines nothing, so it moves no atom out of `∅`. What used to guard that was an ownership discipline —
*only whoever is producing the answers may close the tail* — and the honest outcome is that **no ownership
rule survives.** A reclaimer posting `¬Q` is making a false claim, which is the same kind of defect as
posting a false `loss(60, 0.5)`, under the same enforcement, which is none. The special case was an
artifact of the tail representation and dies with it. (An intermediate draft re-anchored the rule to a
producer named inside the claim; that argument goes with the author, and loses nothing, because the
general case already covers it.)

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

**The same rule holds at the status layer, and there it is enforced rather than observed.** You may test
that an atom has been told false; you may never test that it has *only* been told false. And you cannot:
writing `= {f}` needs the conjunct *"and nothing told true"*, which is `¬∃` and has no syntax. The
restriction to up-sets is not a discipline a rule author keeps — it is what the absence of `¬` and `var`
leaves expressible, exactly as the absence of an else-branch is. `= {f}` becomes writable in a **report**,
which may negate, and nowhere else.

**Which is why admitting `{f}` into rule bodies costs nothing** — and why `¬` may be the mark on a posted
record while remaining absent from the language:

> **You may never *derive* `¬φ`. You may be *told* `¬φ`.**

Reading a told-false record is reading an
atom — a positive literal, affirmable from finite information, no membership survey. It is not
negation-as-failure and it observes no absence. And a rule reading `⊒ {f}` fires identically on `{f}` and
on `{t,f}`, so the climb between them is **unobservable to derivation**, for the same reason a failed
match is. Conflict is contained not because rules cannot see absence claims, but because the only thing
they can see about one is upward-closed.

**And a conflicted premise does not explode.** *Ex falso* needs a schema `⊥ ⊢ B` for arbitrary `B`, and a
definite clause's head is always a specific atom; `{t,f}` is a status, not the formula `A ∧ ¬A`, and the
object language has no `¬` with which to form the premise. What a conflicted premise does instead is
derive an ordinary atom, which may then disagree with another derived atom at the head relation — the
free completion again, one level down, and where a **declared domain conflict** (§"No functional
dependency") catches what the status layer cannot. The residue is that a conclusion may stand on a
premise that later became disputed; nothing retracts, but §"Demand is control" owes an account of who can
still find it.

## Geometric logic, which is what this language is

The derivation language is **geometric logic**: finite ∧, arbitrary ∨, ∃. **No ¬, no →, no ∀** — as
**operations**. A posted `¬Q` is neither a counterexample nor an exception to that, on the rule
§"The threshold rule" states: you may be **told** `¬φ`, and you may never **derive** it.

Every restriction arrived at here independently is one of its clauses:

| decided here | geometric logic |
|---|---|
| definite clauses, no *derived* negation | no ¬ |
| carry all branches rather than backtrack | **arbitrary ∨** |
| conjuncts filter branches (the list-monad bind) | frame distributivity, `a ∧ ⋁bᵢ = ⋁(a ∧ bᵢ)` |
| threshold claims only | **opens** |
| monotone ⟺ coordination-free (CALM) | Scott-continuity |
| a **value** is settled = ground = maximal; exact claims only there | total elements of a domain |
| *derived* negation confined to reports | **closed** sets — refutable, not affirmable |

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

- **The residual** is a complement-bearing message (`Q ∖ E`) sent to a handler that produces from it,
  which is feeding demand by definition. Benign where re-production is idempotent. Note `∖` here is
  **set difference in a region description**, not the posted `¬` — `E`'s atoms are excluded from the
  region, never asserted false.
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

**But there are two kinds of conflict, and an earlier draft ran them together.**

| | what disagrees | how many atoms | who says so |
|---|---|---|---|
| **valuation conflict** | whether the atom is there at all | **one** | the store, structurally: the status is `{t,f}` |
| **domain conflict** | two atoms violate a constraint somebody declared | **two** | a user-written rule |

The rule above is a *domain* conflict, and writing it as though it were generic is a defect:
`loss(60,0.5)` and `loss(60,0.4)` are two atoms, both told true, disagreeing with **nothing** until
somebody declares that `loss` is functional on its step — and the rule hardcodes position 1 as key and
position 2 as value, which is exactly the key/value split §"There is no `read`" says no term carries.
Written honestly it is one declaration among many, and the **16 hand-rolled guard sites** in the corpus
(§"Orders are mostly read-side") are sixteen such declarations rather than one primitive the substrate is
missing.

The generic half is the status. `⊒ {t,f}` needs no declaration, no key, and no functor-specific
knowledge: one producer determined a region empty, another produced inside it. Under the previous
aggregation that case returned `true` **in silence**; here it is a value you can read.

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

*No top on the **value** order, and none needed.* A per-relation top is what a collapse would land on,
and a top necessarily satisfies every threshold — that is what being the top means — so one disagreement
would fire every rule mentioning the relation. Keeping the atoms apart is what keeps it out of reach.
(The **status** order does have a top, `{t,f}`, and it is harmless for exactly the reason this paragraph
gives: it is a top over *what you were told about an atom*, not over the atom's value, so it satisfies no
threshold a rule reads on the value. Different orders, unrelated tops.) A "broken" flag is
the same defect wearing different clothes: discarding values and recording a bit is the one operation
that moves *down*, and it retracts — every rule that fired on `loss(60, f(X))` must un-fire.

*Nothing needs broadcasting.* Ask whether a reader who already got `f(a)` must be told when `g(b)`
arrives. A **threshold** claim is still true — somebody did post `f(a)`, and a later post does not
unpost it. An **exact** claim was never legitimate on an unsettled term. **No legitimate claim is
invalidated by a conflict**, so there is nothing to push and no registry of past contributors to keep.
The store owes something *readable* — a status for a valuation conflict, a queryable predicate for a
declared domain one — never a notification.

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
satisfied can stop being satisfied. That is §"The threshold rule" doing its job, and it is why the classic
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

**But the partition is by *sort*, not by name, and that is far cheaper than it first looks.** Measured
over 821 real logs: 24 distinct value names, **none carrying more than one sort** (21 `float`, 3 `dict`),
and no new names appearing in the corpus's second half. So the entire measured value plane is **two**
relations —

```
metric(Name, Float, Step)        event(Name, Json, Step)
```

— both fixed shapes with the name as **data**. The aliasing objection above never arises, because the
float and non-float partitions are separated by *relation* rather than by name: `metric(loss, X, S)` and
`metric(acc, X, S)` may share `X` and both are floats, while `converged` lives in the other relation
where no alias can reach it.

A consumer therefore declares one signature per value *sort*, not one per metric — two, on this corpus,
against the twenty-four names. And the case that motivates the rule is a genuine one: `mycooc`'s
`permutation` carries `None` under one flag and a nested record under another, in **source** at
`analyze_run.py:1160-1179`. Note what kind of case that is: an Option sort declared inside one function,
which is exactly what deploy-time codegen emits. It does not appear in the corpus — 0 of 24 names show
sort drift — so it is a hazard the rule forecloses rather than damage the rule repairs.

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
| `max` / `min` | a report. On a **dense** carrier the only compact element is `⊥`, so `↑c` is not a basis and the affirmable claim is strict `⊐`, not `⊒` — which is to say: **ask open intervals, never points** (below) |
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

**Continuous carriers are restricted, not broken: ask open intervals, never points.** Nothing settles at
a point on a dense carrier — bisection narrows forever and can never confirm exact equality — which is
why `↑c` stops being a basis there. But an **open interval** is affirmable: *"is `S` in `(0.4, 0.6)`?"*
becomes true the moment narrowing puts the domain inside it, even though `S` itself never settles. So:

> **On a continuous carrier, settle the *question*, not the *value*.**

Thresholds fire, standing queries die, GC reclaims — everything the dense case appeared to lose. And it
pairs with the other axis rather than being a special case: `↑c` is the open question on the
**instantiatedness** order, an open interval is the open question on the **value** order. Same
discipline, two axes, and a point is not an open question on either.

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
demand and a slow handler are indistinguishable — and the settledness footgun in §"The threshold rule".

### Facts and demands are dual, and the duality is exact

- A **fact** is a ground term: a maximal element, a **point**.
- A **demand** is a pattern, and a pattern denotes `↑p`. Finite partial terms are exactly the **compact**
  elements, and in an algebraic domain `c` is compact *iff* `↑c` is Scott-open. So a demand *is* a basic
  open — with two exceptions the rest of this doc already names: on a **dense carrier** the only compact
  element is `⊥`, and a **disequality** constraint denotes no open at all, since `X ≠ Y` is not
  upward-closed (two unbound variables may yet be identified).
- Satisfaction is `x ∈ U` — the pairing between a space and its frame, the same shape as a vector
  against a covector.

**A pattern and its grounding are interchangeable**, which is what makes extents work at all. The
maximal elements of `↑p` are exactly `p`'s ground instances — its *grounding*, in the CSP sense, possibly
infinite — and over a signature rich enough that every partial term has ground instances (any infinite
Herbrand universe), distinct opens have distinct groundings. So nothing operational separates the
pattern from the set it names, and **subsumption of patterns is inclusion of groundings**: `q ⊑ p` iff
`ground(q) ⊆ ground(p)`. That is the one-line reason closure over an extent transfers to every subsumed
question, and it is also the contravariance of §"Demand subsumption" seen extensionally.

The caveat is where the richness fails: with only constants in the signature, `f(X)` and `f(a)` can have
the same grounding while `↑f(X) ⊋ ↑f(a)`. Nothing in this design has a signature that poor, but the
correspondence is a fact about the universe, not about the topology.

**And the quantifier lives in the posting, never in the term.** A term with holes uniformly denotes a
set; what you do with the set is decided by how it is posted. `¬Q` reads *universally* over `Q`'s
grounding (nothing is there); a posted answer reads *existentially* (this one fact lies
somewhere in that set, described as far as it is known); a posted demand asks for the members. One
representation, three roles, no modality — which is why a **partially instantiated answer** needs no
special case: it is an answer about what is known and a question about what is not, simultaneously,
because a hole *is* an open question wherever it appears.

That is Stone duality (`Loc ≃ Frm^op`), not the sense of "polarity" in which positives and negatives
cancel. But the proof-theoretic sense of polarity *is* apt: **geometric logic is essentially the positive
fragment** (`∧`, `∨`, `∃` are positive; `→`, `∀`, `¬` are negative), and **a fact is data where a demand
is a continuation** — *"I want this"* being a computation waiting on a value. It is also why the
querier's and handler's interfaces keep coming out as dual session types: the same duality, seen a third
way.

**Polarity should not be asked to carry more than that, though.** It assigns the connectives and it
gives the data/continuation reading, and it explains **neither** restriction the design actually leans
on: focusing makes `∧⁺` positive with no cardinality condition, so nothing in polarity says why `∧` must
be **finite** while `∨` may be **arbitrary**. That asymmetry is **left-exactness** — inverse images
preserve *finite* limits and *all* colimits — a different fact wearing the same word. (Under the
colimit-is-positive heuristic, `∧` is a limit and would read negative, which is the tell.) A geometric
*theory* is also axiomatised by sequents `∀x̄(φ ⊢ ψ)`, so the positive claim is about **formulas**, with
one negative shell around positive cores.

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
| the **demand relation** — *"this was wanted"* | yes, accumulates | internalised, a fact about an `Open` |
| the **live subscription** — *"someone is listening now"* | no, revocable | control, outside the store |

Production is triggered by the subscription; the internalised demand is a durable record of *"what has
ever been asked of this producer."* That makes *"what is demanded"* an ordinary query rather than a
snapshot the core layer has to inject, while leaving the non-monotone half where this section puts it.

**It does not carry a dependency graph, and the logical layer needs none.** When a handler serving `report`
posts a pattern for `loss`, the store cannot tell that from an unrelated querier asking for losses —
symmetric roles means indistinguishable, and indistinguishable means no edge. Nor is it recoverable from
ordering: if two handlers both need `loss`, the demand is posted **once** and the second is a cache hit,
so the second edge was never an event. That looks alarming until you ask what would use it:

| candidate use | why it does not need the edge |
|---|---|
| **admission control** | reads *what is demanded now* — the live subscription set, already available. What a graph adds is *prediction* ("granting this will pull in that"), which is a convenience |
| **cancellation** | when a handler's own demand goes away it stops, and stopping drops its sub-demands. Each agent knows its own reasons because it is the one that has them; the cascade is agent-local |
| **provenance** — *"why is this job running?"* | a log question, answerable from agent-local traces |
| **cycle detection** — A needs B needs A | without it you hang, which finiteness already makes the requester's problem |
| **taint** — *"which conclusions rest on a premise that has since become `{t,f}`?"* | agent-local again: the agent that fired the rule is the one that read the disputed premise, and can record that it did |
| **reclamation reachability** — *"is this atom re-derivable, so may I evict it?"* | this one **does** need a graph — and it is the reclamation layer's, not the logic's (§"What is checked") |

**The build-system analogy needs restating, because the old form of it was wrong.** Shake, Bazel and Nix
use their graph for **invalidation**, and an earlier draft concluded that with no retraction there is
nothing to invalidate. Two things escape that argument. A premise can climb `{f} → {t,f}`, which retracts
nothing and still leaves a conclusion standing on disputed ground — a *taint* question, not an
invalidation one. And **Nix keeps its graph despite never invalidating anything**, because deletion needs
**reachability**. Neither reopens the edge inside the logic; both land in the layer below it.

So the graph is a convenience, not a repair — and if it is ever wanted, the cheap form is **structural
rather than instance-level**. A handler declares `needs(report, loss)` once, alongside its sorts: one
edge however many times anyone asks, no demand identity, and no per-agent "which request am I serving"
state, which is exactly what symmetric roles was buying. Recording *instance* lineage — *"this posting
caused that one"* — would be monotone and therefore useless for admission anyway, since it accumulates
what was **ever** wanted while admission needs what is wanted **now**.

**And `needs` posted at runtime is legal, with one interesting collision.** It is a fact rather than a
signature, so a producer that loads a rule and gains a capability may simply post the corresponding
edge. But if it has already posted `¬Q` and the new capability falls inside `Q`, producing
anything there contradicts its own claim, and others may already have read those atoms as `{f}`. No new
machinery is needed to catch that: the status climbs to `{t,f}` like any other valuation conflict. Gain
capabilities before you assert absence, not after; break the rule and the store can tell.

Concretely a subscription is a **cursor into a per-functor term index** — walk the trie with your
pattern, get notified when new leaves appear beneath it. That keeps `post` the only store-mutating
operation: reading is walking the index, and a standing call is the evaluator remembering where you
were walking.

### The demand language: constraints from a fixed domain

Talking *about* demands means handling the holes in a pattern, and there are exactly three ways to do
it — which is the whole design space:

| | the holes are | who may make a new demand *shape*, and when |
|---|---|---|
| **adornments** | **erased** into a finite tag (*"argument 2 is bound"*), the bound values becoming ordinary arguments | the compiler, at program-write time |
| **exponentials** | **delegated** to the metalanguage: a pattern *is* a map `Point → Ω` | anyone, at runtime, by abstraction |
| **quotation** | **represented** as a term denoting its own syntax, plus a `denote` relation | anyone, at runtime, by building a term |

Erase, delegate, or represent — and the axis underneath is *when the demand vocabulary is fixed*.
Everything else follows from that.

**Take the middle road: CLP.** A demand carries constraints from a **fixed domain**, with a solver
deciding satisfiability. `loss(V,S), S ≤ 100` has shape *"range constraint"* and data `100`. This is the
CLP(X) schema: pick a constraint domain, and the constraint is an ordinary positive literal in the body,
so the logic is untouched and stays first-order and decidable. Adornments are the degenerate case where
X is bare equality; ordinary logic programming is CLP over the Herbrand domain.

**Propagate, never label.** A propagator narrows domains by local reasoning; when narrowing cannot decide
a question, the usual fallback is *search* — try a value, propagate, backtrack. Don't. Answer
**undecided**, which is `unknown`, and let the caller suspend until enough arrives for propagation to
decide. That is the threshold discipline already in force, applied to the solver, and it removes four
hazards at once:

| hazard | why it goes |
|---|---|
| hypotheticals reaching the store | no labelling, so nothing conditional is ever asserted |
| solver isolation | nothing to isolate — and measurement shows the obvious boundary does not work anyway, since copying a term copies its suspended goals |

**Banning labelling does not by itself delete the budget, and an early draft claimed it did.** Measured
in `clpfd` with no labelling anywhere, `X in 1..N, Y in 1..N, X #> Y, Y #> X` — two variables, two
constraints, obviously unsatisfiable — costs **989 inferences at N=10 and 4,550,534 at N=10⁵**, because
bounds propagation raises each bound by one per step. Linear in the numeric magnitude of the domain, i.e.
exponential in its encoding. Worse, on a step axis (`0..sup`: bounded below, unbounded above) the same
ascent has no ceiling to hit and does not terminate at all; with no bounds either side it terminates
instantly and concludes nothing.

**But that is an algorithm mismatch, not a property of the problem.** `X > Y > X` is a system of
**difference constraints**, decided in polynomial time by negative-cycle detection — `O(V·E)`,
**independent of domain magnitude**, and fine unbounded. A propagator-producer may run Bellman–Ford
instead of generic interval narrowing, exactly as §"the solver is producers" allows any sound backend:
its narrowings are entailed either way. The ping-pong appears only when a generic propagator is pointed
at a class that has a specialised procedure.

So the rule needs no repair, but the open question it leaves does (§Open): for classes **with** a cheap
decision procedure, use it, and unsatisfiability becomes a fact rather than a cost. For richer classes —
general linear integer constraints are NP-hard — propagate as far as is cheap and report **undecided**,
which is the rule as stated.

What it costs is **incompleteness**: entailments a search would have found go unreported, so a demand
that *is* subsumed may not be recognised and redundant work happens. That is the right trade —
incompleteness costs work, search costs soundness. And it draws a clean line: propagation's narrowing is
**real information**, monotone, and *should* wake the streaming aliases; labelling's is **conditional**
and must never reach them.

**And then the solver is not a component at all — it is producers.** A propagator watches, derives and
posts; with labelling banned that is *all* it does, so it is an ordinary agent. Propagation is ordinary
rules — `in(S,[50,100]) :- leq(S,100), geq(S,50)` — positive, monotone, with the relation of derived
bounds accumulating while the tightest bound narrows, which is the shape everything else here has.
Entailment checking is an ordinary query. Fixpoint is what the evaluator does anyway. So *"which
constraint domain"* is not a separate decision; it is *"which rules do you write"*, and they run where
the data is, which is the pushdown requirement satisfied for free.

Specification and implementation stay independent, as everywhere else here: a propagator-producer may
run bounds consistency or AC-3 internally and post what it derives, because sound propagation only ever
removes values that appear in no solution — its narrowings are entailed, so posting them is posting
derivable facts. Three things follow. Such a producer **demands** the constraints it needs and **posts**
the narrowings, symmetric with any other agent. Several propagators over the same constraints
**compose without coordination**, since all their narrowings are entailed and the tightest is a read.
And a propagator that derives *less* than the rules would is simply incomplete, which is safe — under an
open world that is "not yet known", and later propagation may still add it.

`all_different` is worth checking because it looks like it should fail: it does not. Disequality is legal
as a **posted constraint**, and narrowing from it (`X ≠ 3` with `X ∈ {2,3,4}` gives `X ∈ {2,4}`) is
positive derivation. Only the *state test* — *"are these currently different?"* — is forbidden, and
propagation never needs it.

**It does not threaten monotonicity, and the discipline that protects it is the one already in force.**
The constraint store only accumulates, so it grows in the information order. The *solution set* shrinks,
which is the narrowing direction and antitone as a relation — so the rule is exactly §"The threshold rule"
again: **entailment claims are monotone, membership claims on the solution set are not.** *"The store
entails `S ≤ 100`"* is affirmable and permanent; *"5 is still possible"* is neither. Disequality is the
sharp case: `X ≠ Y` is fine as a posted constraint and as an entailment, and not as a test of the
current state, since two unbound variables may yet be identified.

**Imposing equations is a special case, not a separate mechanism.** Quotienting the term algebra by a
theory `E` is CLP where the constraint domain is E-equality; the join becomes E-unification, which is
well behaved only for unitary or finitary theories, and in the finitary case lands in a *set* of
most-general unifiers rather than one term.

**The boundary worth stating.** Everything stays first-order while the constraint *vocabulary* is fixed
— arbitrary data in a fixed set of shapes. Opening it, so that a demand's shape is itself computed at
runtime, buys **reflection** and its costs. What genuinely needs it is the system reasoning about
itself: demands about demands, or producers advertising *"I serve any pattern of this shape."* Nothing
this design requires does, so the vocabulary should stay closed.

**And exponentials are the wrong escape from that boundary**, tempting as they look. Finite limits,
exponentials and `Ω` together *are* an elementary topos, so they give power objects, power objects give
comprehension (as separation, which is exactly the form `{c : c ∧ a ≤ b}` needs), and comprehension is
the one thing standing between this logic and `a → b`, hence `¬a = a → ⊥`.

Worse, **inverse images of geometric morphisms preserve finite limits and all colimits but need not
preserve exponentials** — witness `Δ : Set → Sh(X)` for `X` a convergent sequence, where
`Δ(2^ℕ) ≇ Δ(2)^{Δ(ℕ)}`. Two precisions, because the loose version of this is wrong: *base change* in
the usual slice sense (`f^* : E/Y → E/X`) **does** preserve exponentials, toposes being locally
cartesian closed; and the inverse-image failure is not universal either, holding for finite exponents
and for locally connected morphisms. The conclusion needs only that it fails **in general** — so
"geometric logic with exponentials" is not a richer geometric logic; it is not geometric.

That is also the honest form of the intuition that this sits at a sweet spot, with the honesty mattering
in both halves. Geometric logic is *characterised* by the preservation property rather than chosen and
found convenient — as a theorem about **functors** (a functor between Grothendieck toposes is geometric
iff it preserves finite limits and small colimits), and about **formulas in the forward direction**;
any formula-level converse holds only up to logical equivalence, since `¬⊥` is preserved and is not
geometric. And geometric theories are those with classifying toposes — a surjection **up to Morita
equivalence**, non-canonical in one direction, since every Grothendieck topos classifies *infinitely
many* geometric theories and two theories share one exactly when they are Morita-equivalent.

Neither qualification damages the use made of them: step outside and the preservation **theorem** fails,
and that is the half that is unconditional. The absence of `→`, `∀` and `¬` is the price of transport,
not an oversight.

**Internalising does not put `→` within reach, and the argument for that should be the semantic one.**
Opens form a **frame**, and a frame necessarily *has* implication: completeness forces
`a → b = ⋁{c : c ∧ a ≤ b}` to exist. But it is not *definable* in this logic, and the one-step reason is
the invariance everything else here rests on —

> **Frame homomorphisms preserve finite `∧` and arbitrary `∨`, and do not preserve `→`.** Only
> `h(a → b) ≤ h(a) → h(b)` holds, and it is strict: take `O(ℝ) → 2` at the point `0`, with
> `a = ℝ∖{0}` and `b = ∅`. Then `a → b = int({0}) = ∅`, so `h(a→b) = ⊥`, while `h(a) = h(b) = ⊥` gives
> `h(a) → h(b) = ⊤`.

Every geometric formula's interpretation *is* preserved; `→` is not; therefore no geometric formula
defines it. That needs no survey of dodges, and it makes the guardrail **semantic** rather than
syntactic — which matters, because a syntactic guardrail is only as strong as nobody adding a
term-former.

The syntactic argument is also sound and worth keeping as the operational reading: naming
`⋁{c : c ∧ a ≤ b}` needs a **comprehension**, and geometric `∨` ranges over a **given** index family,
never one a predicate selects; nor can the condition be smuggled in as a conjunct, since `c ∧ a ≤ b` is
entailment and internalising entailment as a formula *is* implication. One repair: comprehension yields
`→` only *together with* an arbitrary `∨`, which this design supplies — so the danger is contingent on a
feature we have, not structural.

What that identifies is the thing actually worth guarding — not internalisation, but **comprehension**.
And it sharpens what a hand-written approximation is: a user who enumerates a finite family and disjoins
whichever members satisfy some condition has written a geometric formula that *happens* to equal `a → b`
in one frame and **stops equalling it after base change**. The slogan is not *"`→` is unwritable"* but
**"`→` is not uniformly definable."**

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
  fixpoint test. **Exhaustion** is not a fifth entry — it is the probe and the lifecycle records already
  listed, read for a different purpose (§"Falsity is told, never inferred");
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

**And reclaiming a derived truth is eviction, not retraction.** The line is re-derivability: an atom a
rule can recompute from premises still present costs only time to lose, so deleting it changes what is
*stored* and not what is *true*. What the reclamation layer owes is therefore a **caching invariant** —
anything evicted must be re-derivable on demand — which keeps the *observable* store monotone while the
stored one is not. Same split as §"the store is the cache", one layer down, and it is why this layer sits
**below** the logic while being free to **read** from it.

Deciding what qualifies is reachability, and the tiers are not in the order storage cost suggests:

| | cost to lose | evictable? |
|---|---|---|
| a **derived** atom | recompute | freely, while its premises survive |
| a **produced** base fact | a six-hour job — *if the producer still lives* | at a price, and only then |
| an **absence** claim | possibly **unrecoverable** | no |

The last row is the surprise. A `¬Q` is a determination made at a moment, and a fresh producer may
have no way to re-make it; evicting one descends the status `{f} → ∅`, which is the single descent this
design does not permit. So the cheapest-looking records in the store are the ones that must never be
collected — and *"assuming the producers are still there"* is the clause the whole tiering turns on.

**The residual is not a blocker.** Ship `Q ∖ E` with `E` the finite settled set and **do not
normalise**: `Q`'s structure survives intact, and `E` need not cross the link at all, since the handler
is near the data. What it *is* is a report, with the exception noted above.

## What survives from runstate

- **The run as a durable identity outliving its processes.** The actual contribution, now explicit.
- **Content-addressed run ids.** Becomes the cache key, unchanged.
- **The verdict as a join of two partial observers** — and it is a *report*, which is why it may use the
  narrowing reading that derivation may not.
- **Cooperative, no enforcement.** Load-bearing for the headline.
- **`never` as a fact rather than a status** — it survives as `¬Q` over a singleton region
  (§"Falsity is told, never inferred"). The *"fact about the world"* reading is the right one, and it is
  what any one poster asserts; what nobody posts is the **status**, which is read, not written.
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
2. **Which constraint domain — *and which algorithm for it*.** The two are not separable: measured, a
   generic bounds propagator on difference constraints ping-pongs (Θ of the domain magnitude, and
   non-terminating on a half-bounded axis) where negative-cycle detection decides the same system in
   `O(V·E)` regardless of magnitude. So the question is not decidability of a domain, nor completeness of
   a propagator, but whether the pair is matched. For a step axis the answer is difference constraints
   with cycle detection, and the hazard does not arise. The corpus has not been surveyed for what else it
   would want.
3. **Where the query language stops.** It need not be decided up front. What constrains it is
   **pushdown**: the more expressive the language, the less of it runs where the data lives, and
   locality is what CALM makes non-negotiable.
4. **Whether a partially-narrowed value may be streamed.** Safe under exactly one discipline — consumers
   may make threshold claims, never membership claims — which is §"The threshold rule" applied in flight.
5. **No central store.** Each agent holds a lagged local copy and replicates preferentially what it
   demands; the "global" store is the union of the local ones, with no ground truth anywhere. This
   follows from monotonicity and needs no coordination — nobody deletes, sharding and replication are
   free, and there is no single point to lose. The consequence to be explicit about: the memo check
   becomes **local**, so caching is best-effort, and two agents demanding the same cell without having
   replicated each other's answer both run the six-hour job. That is precisely single-spawn, the one
   irreducibly coordinating requirement, and CALM says it must cost a round.
6. **Whether demand should be the only interconnect, and not merely the only one that means anything.**
   The semantics already reads that way (§"Where the rules come from"). Making it architectural
   — a local view receives *nothing* it did not ask for — would bound coordination by the number of live
   channels, since the demands would be the only edges in the system. It would also forbid unsolicited
   broadcast, which is a real affordance worth keeping, so the two readings should probably stay apart.
7. **What a conflict means, given that three different things produce one.** Neither kind can be read as
   *"something is wrong"* without more than the conflict itself. **Domain**: duplicate production plus
   re-production jitter yields two slightly different atoms — a *correct* report of a real disagreement
   the system itself caused when single-spawn did not hold. **Valuation** (`{t,f}`): either two posters
   genuinely disagree, or one over-claimed the extent of its own `¬Q` and then produced inside it
   (§"Falsity is told, never inferred"). Telling those apart is what the absent provenance would buy.
   Measured counterweight for the first: 0 of 3,165 numeric re-productions diverged on the real corpus,
   so the hazard is real and the consumers' hand-rolled guards are working. The valuation case has not
   been measured — see the census on the tracker.

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
