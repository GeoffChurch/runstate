# Decisions and retractions — `../questions.md`

What was tried in this layer, what was withdrawn, and why. Conventions in `README.md`.

## The demand quantifier

**Withdrawn: the sketch's two readings, which contradicted.** §"The model" read
a demand existentially; §"Demand subsumption" read it universally. Both were stated flatly.

**Resolved: the quantifier is a property of the posting, not of the term** — which the sketch already said
elsewhere. A **bare pattern is `∃`**; a **`∀` demand must be *finitely coverable***.

> **Superseded 2026-09-30** (§"Record scope" below). A demand is no longer a bare pattern; it is an `asked`
> record holding a question whose quantifiers are written out, per variable.

**And "bounded" was wrong twice over**, in the first repair. *Named* is a re-description — the durable
record is named by its `request_id`, which the schema requires of every subscribe. *Bounded* is admission
control wearing a quantifier's clothes, and it forbids the demand this design is best at expressing:
*"every step, run to convergence, and tell me when there are no more."* The right precondition was
already present: **`ground(Q)` need not be finite; the cover must be.**

## Smaller reversals, recorded so they are not re-tried

- **The dead-end verdict on the per-producer answer tail was too strong.** It is the right *structure* in
  the wrong *representation*: one termination marker per producer per stream is precisely `¬Q`.
- **`every` is a delta, not a stride** — so exposing it on `ensure` is *not* an additive change, and the
  emission filter's deferral is load-bearing. See `../../memoizer-index-algebra.md`.

## The Skolem reading, and why it self-satisfies

**What it was (2026-08-25 → 2026-09-04).** After the reification above, §"The model" said a hole in a
posted term *"is an ordinary constant"*. Read literally, `loss(60, v37)` is then a **ground literal** — the
Skolem form of `∃x. loss(60, x)` — and the store stays a set of ground literals with no variable anywhere.
The sentence one paragraph over, *"a variable travels as a constructor `var(37)`"*, is a weaker claim (a
wire *name*, not a constant), and the two were treated as one.

**Why it was attractive**, recorded because it was argued for twice in one review. Posting a fact about a
name you minted is honest existential assertion — *"there is a loss at 60, and I call it `v37`"* — so a
demand is an ordinary post and the posting rule is not bent. The store is uniformly ground. And a producer
who has posted `¬Q` over the region can **dispute** the demand: the atom reads `{t,f}`, the disagreement is
readable, and that felt like the design working as intended.

**What killed it is what Skolemization does.** `P(c)` for fresh `c` does not merely *assert* `∃x. P(x)` — it
**witnesses** it. The demand is satisfied by its own placeholder. And §"The model" defines demand as an
***unsatisfied*** existential: *"making an unsatisfied existential true is exactly production."* Under the
constant reading there is nothing left to produce; the question answered itself with itself. Downstream,
the same fact made the residual subtract the demand atom from the region it defined, and made admission
control's signal — *how many atoms does this term denote?* — read **one** for every demand regardless of
region.

**And independently, no producer could have acted on it.** Three ways to recognise a placeholder, all
closed: *"still has a hole"* is `var/1`, antitone (§"The threshold rule"); recognising it by spelling is
the closed-set-in-an-open-namespace error (§"Polarity is schema"); recognising it by the *absence* of an
equality is observing absence. A named **variable** is recognised structurally — matching the functor
`var` is an ordinary positive match on a term that never changes — which is none of the three.

**What is true instead: asserted versus witnessed.** A partial term asserts existence and provides no
witness. It decides no atom (§"An atom's status": *"a partially instantiated positive post decides
nothing"*), so it cannot be wrong and cannot be disputed. The attractive dispute case does not vanish — it
lands differently and better. A producer's `¬Q` over the demand's region makes that region read entirely
`{f}`, which is the **affirmable** form of *"this demand cannot be satisfied"*: a finite cover of told-false
records, monotone, needing nothing new. The asker learns the negative by reading it, not by having posted
a falsehood that had to be caught.

**Formally.** The hole is a constraint-store variable in the CCP sense (§"Two layers" names CCP as the
semantics): it has an identity, it is not a value, and what is known about it accumulates. It is `∃`-bound
at **store** scope, not record scope — which is what lets a later post refer to it — and the wire name is
the **scope extrusion**, which is why the naming policy is load-bearing rather than cosmetic: two posts of
the same shape make two holes because they extrude two names.

> **Superseded 2026-09-30** (§"Record scope" below): store scope is withdrawn; a variable is bound by its
> record. What stands from this section is *asserted versus witnessed* — and it went one step further: a
> demand is not asserted either.

**Revival trigger.** A setting where a demand *should* be a claim — the asker genuinely knows the thing
exists and wants a disagreement to land as `{t,f}` on one atom. Then post a **ground** fact, which is what
a claim is. That is not a demand and never was.

## Record scope, `asked`, and quantifiers in questions (2026-09-25 → 09-30)

A review on 2026-09-25 found the store-scoped hole fabricating; five days of dialectic and two rounds of
two-reviewer checks replaced the demand layer. The trail, each step with the reason that killed it:

**1. Store-scoped holes answered by posted equality — withdrawn.** The default reader policy folded
equalities with a fresh side, on the theory that they close among the name's holders. Transitivity defeats
that: `v37 = 0.5` and `v37 = 0.4` are each fresh-sided, and together they equate two ambient values — the
merge §"Constraints are asked" refuses. Binding a hole to identities is worse: `winner(r, v37)` bound to
`ep1` and `ep2` answers a question about `ep2` with `stopped(ep1, …)`. The doc also had two defaults —
fold-fresh, and "the finest congruence" — and the equality path fabricated under one and delivered
nothing under the other. The flagship example answered with ground facts all along. **Why store scope,
not only the fold:** a variable shared across records, bound by two producers, forces a congruence
(fabrication), branching (possible worlds — what refusing `∨` in heads excludes), or one binding (an owner,
an arbiter, the roster). Record scope has no cross-record correlation. The considered alternative — keep
store scope and fold by one-way substitution — also avoids `0.5 ≡ 0.4`, but keeps a cross-host naming
protocol for a capability that brings the trilemma back the moment a hole is shared.

**Retired with it: the four-corner naming table** for holes. Its corners — distinctness from **content**
(ambient names disambiguated by arguments; the measured corpus had 24 ambient value names and zero fresh
variables), from **chance** (random names; a collision is an accidental forgery), from **membership**
(per-actor prefixes; self-identity, Ameloot's `Id`), and from **the link** (names per link end, no global
identity, a middleman's translation equality) — remain the right account of how *entity and occurrence*
names get distinct, which the doc's §Orders multiset row still needs. Only their application to holes is
gone.

**2. A positive partial term is an `∃`-assertion — withdrawn.** It breaks *post what you know*: a question
about step 900 of a run that stops at 743 is a false post nothing catches. And read as an assertion, the
question satisfies itself one level up — its own post witnesses the proposition it asks about. The Skolem
constant, the named hole and the unsatisfied existential were three forms of one mistake.

**3. A variable's presence marks a question — withdrawn.** A fully bound question (`valid(c)`, yes/no) has
no partial-term form, and the doc fell back on *"a plain trigger fact"*, the per-relation demand predicate it
argued against. And a question in the fact relation lets a reader rule bind a variable to the question's
`var(_)`, unless every matcher is kept from it.

**4. `pos | neg | ask` as three constructors — withdrawn.** `ask` is not independent of the other two; it is
their unknown. **5. A variable in the valuation slot marks a question — withdrawn** for the same leak as 3.
**6. Questions as headless goals with force but no truth value — withdrawn** by both reviewers: magic sets
need rules that *derive* questions, a head with no truth value has no least model, and *"this was asked"*
must be affirmable. The Horn clause/goal taxonomy lent the name and none of the meaning — a goal clause is
a denial.

**What stands.**
- **Variables are scoped to their record.**
- **Polarity is data, as schema:** `told(A, P)`, `P ∈ {pos, neg}`, declared or not (a first version made
  `value(A, P)` the base relation, which made polarity mandatory; `value` also collided with existing
  names).
- **The order is ground terms, variables, valuation, quantifiers.** No quantified sentence is told;
  quantifiers range over statuses. The argument against the swap is limited to *told* quantified sentences
  — Fitting's read-side quantifiers store nothing and are fine.
- **Every record is a tell,** a constrained fact with universal, explicitly ranged variables. No posted
  record is existential. Positive regions are legal when stated.
- **A question is an `asked` record** — magic templates, one generic relation, unpolarised because *"not
  asked"* is never affirmable.
- **Questions carry their quantifiers explicitly, per variable** (the user's layering, 2026-09-29). Free
  variables are what the question ranges over, bound ones what it wants witnessed, any prefix. An
  intermediate form listed the witnessed variables as `Out`; the reviewers showed that is the CQ
  distinguished/non-distinguished split named backwards, with a self-imposed `∀∃`-only limit.

**Why the quantifier returned, reversing e293327.** That commit removed a per-demand `∀`/`∃` bit because a
partial term already asserted existence (withdrawn above, as 2) and because *"decide every atom in this
region"* was never a request. The second was right about **production** and wrong about **control**: the
scheduler's residual differs by what the question binds. It also reverses the 2026-08-25 review's Part 5
verdict that `¬(Q₀ ∖ E)` *"settles the region correctly"*: expressible, yes, but it obliged every producer to
vouch that no other value exists at each produced key, which over-claims wherever the key does not
determine the value, and turned an honest second value into a `{t,f}`.

**Two strengths of settledness, and where each is needed.** Settled with the value bound — every step has a
loss — needs only positives and a tail. Settled with the value free — every step's losses complete — needs
vouching at every key. "No" answers to a question that constrains a bound variable, and summaries under
Morton's Prop. 5.2, need the strong one. Vouching is honest exactly when the key determines the value, so
a key is a schema's claim about what determines the value, and its granularity is the user's trade-off:
finer keys make vouching honest and hide disagreements as separate facts; coarser keys make disagreements
visible and make vouching an over-claim unless the key really determines the value.

**Weighed and not taken.** (a) *Every variable universal, and producers vouch per key* — covers "every
step, some loss" only where a producer can vouch, and cannot express *"is there any?"*, which should settle
at the first witness. (b) *Infer which variables are bound from which the constraint mentions* — not
invariant under rewriting, and wrong for a constrained witness (*"run until loss < 0.1"*).

**Revival trigger for store scope:** a case where one unknown must appear in several records posted before
it is known, the binder cannot re-post them, and a single binding is guaranteed — i.e. determinism is
required, which is the shared variable's trigger too, with the same price.
