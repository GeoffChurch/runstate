# Citations for `backlog/if-built-today.md` — verification ledger

**Untracked working note, started 2026-08-14.** The doc presents prior art as reasoning in several
places (review 7's central finding). This file holds citations **verified against primary source text**,
so the rewrite can draw on them without re-checking. Nothing enters the doc from here until it is
marked CONFIRMED.

Losing novelty is an accepted and welcome outcome — it buys credibility. Findings are not softened to
preserve a claim.

## Status key

**CONFIRMED** — read in the primary source, quoted verbatim. **UNOBTAINED** — could not retrieve; nothing
about it is to be relied on. **UNVERIFIED** — recalled or agent-asserted, not yet checked.

---

## CONFIRMED

### Saraswat, Rinard & Panangaden, *The semantic foundations of concurrent constraint programming*, POPL 1991, 333–352

Retrieved from Panangaden's own page; quotes read off the rendered page images, not the OCR layer.

- **p. 337** — *"A simple constraint system is just an information system with the consistency structure
  removed, since it is natural in our setting to conceive of the possibility that the execution of a
  program can give rise to an inconsistent state of affairs."*
- **p. 338** — `false` = `D` = the **greatest element** of the information order, reachable by telling.
- **p. 335** — and it is **explosive**: *"the inconsistent store can answer any ask request"*, so the
  process becomes *"completely uncontrollable from the environment."*
- **p. 346 + fn. 18** — inconsistency is identified with **divergence**; distinguishing *"the process that
  diverges from the process that tells false"* is possible but *"outside the scope of this paper."*
- **p. 340** — `tell` is monotone, derived as one of the three closure-operator conditions; **p. 346**
  gives receptiveness (the environment can never be prevented from adding constraints) as an axiom.
- **p. 339 + fn. 10** — *"the logic implicit in our treatment is a form of coherent logic"*, credited to
  **Robert Seely and Phil Scott**. (The printed reference key `[MR97]` and year `197.` are typos for
  Makkai & Reyes, LNM 611, **1977**.)
- **Absent**: demand-driven production, memoisation, distribution. `ask` is *suspension on entailment*,
  not demand. *"Janus: a step towards distributed constraint programming"* is cited as the direction not
  taken.
- **Present, and worth having**: the abstract's *"a finite representation of a possibly infinite set of
  valuations"* — the finite-record-for-infinite-region apparatus, but for **positive** partial
  information only, with no polarity and no told-false region.

**How to use it.** The doc says *"CCP is the right model for the semantics"* — true, and the overlap is
larger than the doc knows, but **not** in the direction previously assumed here. CCP drops the
consistency structure *and then makes the result annihilating*. Keeping a contradiction **inert and
readable** is what CCP explicitly declines. That is a sharper contrast than "CCP made the same decision",
which was a mis-relay and is withdrawn.

### Małuszyński & Szałas, *Logical Foundations and Complexity of 4QL, a Query Language with Unrestricted Negation*, JANCL 21(2), 2011 (arXiv:1011.5105)

**Citation correction:** *"Living with inconsistency and taming nonmonotonicity"* is a **different**
paper — the companion in *Datalog Reloaded*, LNCS 6702, 2011, 384–398, **not obtained**. It is where the
modular architecture and the worked nonmonotonic encodings live.

- **Definition 5** — an interpretation is a **set of ground literals**; the four values are *derived from
  membership* of `ℓ` and `¬ℓ`. That is this design's store representation exactly: no per-atom value
  slot, status read off two polarities.
- **Negation in rule heads** is the paper's headline. Deriving `¬p` **inserts the literal into the
  negative extent**. If `p` is present the status becomes `i`; nothing fails, nothing retracts.
- **OWA over CWA, for our reason** — *"rather than starting with CWA, as most approaches do, we have
  accepted OWA"*, chosen to keep the base language *"fully monotonic"*. NAF explicitly rejected:
  *"without referring to provability, as the negation-as-failure does."*
- **Thm 38/39** — PTIME data complexity, and **captures** PTIME on ordered structures.
- **Lemma 21** — the base transformation is monotone w.r.t. ⊆, the same order this design aggregates by.
- **§6, external literals** (`ℓ IN T`) — a reflective test on an atom's *current* truth value, which makes
  the full language **nonmonotonic** and forces stratification (well-layering, Defs 33/35). Example 32
  uses it to **locally close a relation** — CWA reintroduced as a derived construct.
- **Two real divergences from this design.** (1) 4QL **propagates `i`**: an inconsistent body demotes a
  true head to `i`. This design says the two polarities are unrelated relations and no rule connects
  them, so contradiction is **inert**. (2) 4QL is **not Belnap** — it adopts a *linear* truth order
  `f < u < i < t` and says so, calling Belnap's truth ordering *"problematic in areas we focus on."*
  This design uses Belnap's **knowledge** order; 4QL has that too, but as the interpretation order.
- **Absent**: infinite domains (listed as future work), constraint domains, regions, distribution,
  demand-driven evaluation, incremental update. The last two are named as open problems.

**How to use it.** 4QL is **the logic**, and the doc should say so. This design's two genuine logical
moves are **subtractions**: declining §6's nonmonotonic layer, and declining `i`-propagation. Both are
defensible on CALM grounds — `i`-propagation makes a conclusion depend on the *absence* of a
contradiction elsewhere, which is a coordination point. Naming the trade is a stronger claim than
novelty.

### Constraint databases — Van den Bussche, *Constraint databases: a tutorial introduction*, SIGMOD Record 29(3), 2000

Obtained in full, free at sigmodrecord.org. Primary text by a principal of the field. KKR itself
(JCSS 51(1), 1995) is still unobtained, but the definitional claims below are quoted from this.

- **§7, the definition** — *"A constraint database over U is a finite collection of first-order formulas
  over U: (φ_R, φ_S, φ_T, …). Each formula defines a (possibly infinite) relation over U."*
- **§6** — *"We will represent such definable relations, which will often be infinite, simply by their
  defining formulas. This symbolic representation is a first major idea in constraint databases."*
- **§11** — decidable theory with quantifier elimination; formulas may be required quantifier-free
  without loss of expressive power.
- **§15, on safety** — *"we basically dismiss this problem… we simply allow infinite relations."*
- **§17** — a sample query applies `¬` **directly to a stored relation**, and the field's basic operators
  are *"just the basic operators of first-order logic FO."*

**This settles the hinge, and it costs us a claim.** An infinite extent finitely described by a
constraint formula is the **defining idea of the field**, canonical since KKR 1990. So:

- **Do not claim novelty** for "an infinite region finitely described", nor for "a query over such a
  region is still finitely representable".
- **The asserted-vs-computed distinction is real, and verified.** A constraint database holds **one
  formula per relation**; the complement is obtained by *applying an operator* (negate, eliminate
  quantifiers). There is no second extent anyone could post into, so told-false cannot be a *fact* there
  — only a *result*. Structural, not rhetorical.
- **But constraint databases do have asserted infinite regions — positive ones.** `φ_R = "S > 400"` is an
  assertion about infinitely many tuples, written by whoever populates the database. So *"nobody had
  asserted infinite regions before"* is **false** and must not be written. What survives: nobody made the
  **negative extent a separately-asserted, monotonically-accumulated peer of the positive one**, with
  contradiction retained and nothing-told distinct from told-false.
- **And a constraint database can *encode* our machinery** — name a relation `loss_false`, give it the
  formula `S > 400`. What it does not supply is any **semantics** for the pair: no four-status readout, no
  union across parties, no reading of a non-empty intersection as retained contradiction. **The
  contribution to claim is at the logic/semantics layer, not the representation layer.**

### Trân & Bagai, *Infinite Relations in Paraconsistent Databases*, ADBIS 1999, LNCS 1691, 275–287

Abstract, keywords and full reference list obtained from Springer; body paywalled.

**Attribution fix:** both "infinite relations" papers are **Trân & Bagai**, with *Nicholas Q. Trân* first
author — not Bagai solo, as an earlier note here had it. The IS 2000 paper is the journal version.

- Their infinite relations are **language- and machine-theoretic**, classified by complexity class:
  *"the classes of REGULAR and, under different conditions, CONTEXT-SENSITIVE as well as PSPACE
  paraconsistent relations"* work; CONTEXT-FREE and R.E. do not. **Not constraint-described regions**, and
  the 19-item reference list contains **no constraint-database work at all** — KKR is absent.
- Infinite relations are presented as **forced on the model** (*"must be capable"*, *"we show this
  necessity"*), i.e. arising from the algebra — the computed direction. (Inference from the abstract,
  medium confidence; the body would settle it.)
- The IBERAMIA 2000 companion adds: *"as the model (out of necessity) freely permits infinite relations,
  the issue of safety of calculus expressions… is not relevant any more."* Note "permits" leaves open
  whether a **stored** relation may be infinite — that is the hinge and the abstract does not close it.

**So the "paraconsistent + infinite" combination is genuinely occupied — but by a different object.**
Automaton-recognised sets of encoded tuples, not constraint-described regions, with no finite-cover
notion visible.

### Bagai & Sunderraman 1995 — abstract now in hand

Reconstructed from OpenAlex's inverted index (medium-high that it is the publisher's deposit). No mention
of infinite relations. The flagship application is *"a bottom-up method for constructing the weak
well-founded model of general deductive databases"* — the paraconsistent pair as the **carrier of a
computation**, not an accumulation of asserted facts. Suggestive, not decisive; the body is still needed.

---

### Hellerstein & Alvaro, *Keeping CALM* + Ameloot, Ketsman, Neven & Zinn, TODS 40(4) 2016 — **CONFIRMED**

⚠️ The local *Keeping CALM* PDF is **arXiv:1901.01930v2 (2019)**, not the CACM 63(9) text. Cite the
preprint or verify against CACM before quoting section numbers.

**The operator-ban quotes are real — and the review that used them stopped one sentence short.** Both sit
in §2.2, defining the *monotonic-program class* for the transducer formalism, and the paragraph closes:
*"These informal descriptions elide a number of clever exceptions to these rules that still achieve
**semantic monotonicity despite syntactic non-monotonicity** [8, 18]"* — where **[8] is the TODS paper**.
So the scope is **(a) a syntactic sufficient condition**, self-described as conservative twice, not a
claim about what the theorem forbids. The theorem is stated over Def. 1, a semantic input→output map.

**The decisive citation for internal-vs-output is Ameloot's Thm 6(4) proof**, verbatim: *"We use deletion
to start afresh. **Since the query is monotone, no incorrect tuples are output.**"* — the reference
formalism, a monotone query, **deletion used internally**, correctness argued exactly that way. Better
evidence than Prop. 7. *Keeping CALM* itself never addresses the distinction; TODS §4.1.4 does
(*"once a fact is added to ϒ_out, it can never be retracted"*, while memory admits deletion).

**"Network membership / they do not query `All`" is Hellerstein & Alvaro's phrasing** (§2.2), confirmed —
the phrase appears in **neither** Ameloot paper. Cite them for the wording, Ameloot for the theorem.

**The refutation of "told falsity buys coordination-freeness" STANDS**, 3 reasons confirmed, 1 modified.
The extra knowledge is still a **system relation** (`policy_R ∈ ϒ_sys`), supplied by the operational
semantics, never received from a peer; and the mechanism is stated baldly (§4.3): *"if `R(a₁,…,a_k)` is
absent from the local input at x, node x can conclude that `R(a₁,…,a_k)` is actually **globally absent**"*
— a local closed world licensed by placement. Our `¬Q` is warranted by knowing. Also: **TODS §7 says two
of Zinn 2012's central theorems are incorrect**, so the earlier pass reasoned from retracted results and
happened to land right.

**The best citation available to this design, and the earlier pass missed it — Prop. 4.6's construction.**
Relations `R_notMsg` / `R_notMem` / `R_known`, with `Q_del` set to `∅` *"causing nodes to only accumulate
facts"*, broadcasting *"the absence of this fact… accumulated at all nodes"*, and a per-atom `R_known`
test over the pair. **That is this design's data structure, in a proof, in the literature.** What differs
is only the **warrant** for the negative fact. Claim that, not novelty.

**Three further gains.**
- **A sharper placement than `M`.** Prop. 3.6: `H ⊊ H_inj = M ⊊ E = M_distinct`. A logic of finite `∧`,
  arbitrary `∨`, `∃`, no negation **and no disequality** is preserved under arbitrary homomorphisms and
  sits in `H`, **strictly inside `M`**. Conditional and worth settling: **if constraint regions can
  express `≠`, we drop to `M`** (§7: Datalog vs Datalog(=)).
- **`F₀ = A₀ = M`** (Cor. 4.10) with preprint Cor. 17: monotone ⟺ computable without `All` **and** without
  `Id`. So this design needs **no party roster and no self-identity** — parties may join with nobody told.
  A property we have, and the theorem that licenses claiming it.
- **Tombstones** (*Keeping CALM* §3.2) are the design-pattern prior art for deletion-as-data — and our
  four-way lattice is **strictly richer**, because a tombstone lattice is a **chain**, so *told-both* is
  unrepresentable and contradiction cannot be retained. A difference in the lattice, not in the analysis.

**And a real constraint, now in the doc.** `⊒{t}` and `⊒{f}` are monotone; **`∅` is the joint negation of
two growing extents and can flip**, so by Cor. 13 it has no coordination-free implementation. Not merely
unaffirmable — *unanswerable without coordination*. The constraint binds on **output**, not computation.

**Do not cite any of the three for CWA** — grep: zero hits for "closed world"/"CWA"/"open world" in all
three. The mechanism is used and never named.

### Kanellakis, Kuper & Revesz, *Constraint Query Languages*, JCSS 51(1), 1995 — **CONFIRMED** (read by me, OCR'd locally)

- *"A generalized k-tuple is a **quantifier-free conjunction of constraints** on k variables, which range
  over a domain D."* · *"a generalized tuple of arity k is a **finite representation of a possibly
  infinite set** of tuples of arity k"* · *"A generalized relation of arity k is a **finite set of**
  [generalized k-tuples]."*
- So **"finitely representable = a finite union of constraint-described regions" is theirs**, confirmed at
  source. Do not claim it.
- The query language is *"the union of an existing database query language and a decidable logical theory
  — Relational calculus + the theory of real closed fields"*: first-order, hence **negation is an
  operation**, with closure by quantifier elimination.
- **Zero occurrences** of *incomplete*, *inconsistent*, *negative information*, *belief*, *paraconsistent*.
  No asserted negative regions, no incompleteness, no inconsistency.
- *"Monotone"* appears 5 times and **every one is the Tarski fixpoint** of a Datalog mapping. Same as
  Bagai & Sunderraman 1995, where "monotonic" appears once, also at the fixpoint. **In both lineages
  "monotone" means the operator has a least fixpoint — never that a store accumulates.** Worth saying
  when the doc uses the word.

### Jakl, *d-Frames as algebraic duals of bitopological spaces*, PhD thesis 2018, Ch. 6 — **CONFIRMED, with one claim of ours REFUTED**

Underlying venue for citation: Jakl, Jung & Pultr, *Bitopology and four-valued logic*, MFPS 32, ENTCS
2016 (the thesis, p. 4: *"Chapter 6 is based on the insights presented in [JJP16]"*).

- **The logic is exactly our fragment.** §6.3.1's grammar is `⊥, ⊤, tt, ff`, variables, **arbitrary joins,
  finite meets** — and nothing else. No negation, implication or quantifier in the object language.
- **Its order is the information order, and more strongly than we say**: the *truth* order is **definable**
  from it — `α ∧̇ β ≡ ((α ⊓ β) ⊓ tt) ⊔ ((α ⊔ β) ⊓ ff)`. Contradiction is the information join,
  `⊤ ⇒ tt ⊔ ff`.
- **Polarised atoms are a theorem there, not a stipulation.** Lemma 6.3.10: absent `con`/`tot` hypotheses,
  the positive and negative twins generate **two completely independent free frames** and nothing connects
  them.
- **Soundness (Prop. 6.3.5) and completeness (Thm. 6.3.14)** are proved against d-frames, unconditionally.
- **The lower-set fact is his axiom, verbatim** (§2.3.2, p. 17): *"con is **Scott-closed** and tot is
  upwards closed."* Note *Scott*-closed is **stronger** than a lower set — closed under directed joins —
  so consistency of a directed limit **does** follow from consistency of its members there. Do not claim
  our weaker version is his.
- **Motivation, verbatim**: *"bilattice logic cannot capture predicates which are only obtainable by an
  approximating computation"* — predicates as directed joins of finite approximations, i.e. our
  streaming/narrowing story.

**REFUTED: our recorded claim that Jakl's discipline is "consistency is not something a rule body can
claim."** It is not. `con(tt)` and `con(ff)` are **axioms**, and (d-Frm-1) is a full inference-rule package
for deriving `con`. His actual discipline is weaker and more specific: **consistency is a *judgement*,
never a *formula*** — enforced by the grammar, since `Jud` is a class disjoint from `Fm`.

**Five overreaches to avoid.** Do not cite him for *consistency is not assertable* (it is); for **regions
or `∃`** (purely propositional — this is the one axis on which our design is not an instance of his); for
*conflict is affirmable* (he has no judgement form for inconsistency at all); for completeness *of the
topological reading* (that needs d-compactness + d-regularity, syntactic side left open at §6.3.16); or by
importing **`tot`**, which carries a closed-world/exhaustiveness commitment we do not make and drives the
only rule coupling the two polarities.

**Relationship: our derivation logic is a *fragment* of his**, minus `tot`, plus `∃` and regions.

### Morton, *Contextuality from missing and versioned data*, arXiv:1708.03264 (2017) — **CONFIRMED** (read in full, 21 pp.), with review 7's claim about it **HALF REFUTED**

**The regime match is real and is now load-bearing.** §1 defines the **slow inconsistent regime**: *slow* —
*"analysis happens on the same timescale in which information is collected and transmitted"*; *inconsistent*
— *"agents … because analysis is slow never reach consensus."* And p. 9, verbatim: *"data generated by
networked computers is more like a person trying to hand-analyze data being collected on various star
systems spread throughout the local galactic neighborhood. Each agent never really knows what is going on;
by the time it does, it might not be relevant anymore; and every planet had a different, wildly
out-of-date, view of the universe's state."* Cited in §"What it is for".

**HALF REFUTED — review 7's *"his setting is ours verbatim"*, and its recommendation of him as the primary
citation for the sheaf material.** The *regime* is ours verbatim; the *mathematics* is one level above ours,
by his own propositions. Diagnosis in `dead_ends/topological-framings.md`.

**The propositions the doc now cites, all read:**

- **Prop. 2.1** — *"Categorical data with missing data can result in inconsistent marginal counts and
  proportions identical to those that arise from quantum nonlocality."*
- **Prop. 5.2** — *"Without missing data, restriction and summarization commute; with missing data they do
  not."* This is what closes the aggregation hazard: a settled region has no missing data, so summaries over
  it are restrictions of one global summary and glue by construction.
- **Prop. 6.4** (+ p. 18 for table-spaces) — every presheaf of tables completes to a sheaf.
- **Def. 6.8** — relation-spaces; restriction *"necessarily involves summing over indices."*
- **Thm. 6.9** — *"When the tables have missing data, a compatible family of local sections which glues to a
  global section can be sent by π to a compatible family of local sections which does not glue."*
- **Obs. 6.10** — available-case analysis is `π_U ∘ τ_{U←[n]}`; Thm 6.9 *"shows that available case analysis
  can produce a contextual empirical model."*
- **Def. 6.1 / 6.2** — compatible family; *contextual* = Locality without Gluing.

**Verified rather than merely read.** He states his §2 tables *"give the Bell family"* without checking it.
Scaling by 1/8 and taking `E(X,Y) = P(agree) − P(disagree)`: `E(A,B) = 1`, `E(A,B') = E(A',B) = 1/2`,
`E(A',B') = −1/2`, so CHSH `S = 2.5` — above the local-hidden-variable bound of 2, below Tsirelson's
`2√2 ≈ 2.828`. The example is a genuine quantum-realizable correlation, not an arbitrary inconsistency.

**Taken from him but not yet used — a live loose end.** The *"blurry causet"* (§4): the causal order is
unknowable at short timescales because the clocks themselves are wrong, so an event stamp should be an
**interval** `[t_s, t_e]` (after Corbett et al., TrueTime), with strict causality only where the gap exceeds
light-travel time. The doc's *"date the observation and it becomes a fact about the past"* repair currently
implies a **point**, which is not honest at the regime's own timescale.

### The paraconsistent lineage — Bagai & Sunderraman 1995, Trân & Bagai 2000, Liu & Sunderraman 1990 — **CONFIRMED**

**A claim recorded here one pass ago is REFUTED.** The constraint-database pass concluded that told-false
could only ever be a *result* and never a *fact*, making "asserted told-false over an infinite region"
ours. That held against KKR and **fails against this lineage**, verbatim:

> *"This becomes an issue in paraconsistent relations because, **unlike in ordinary relations, all known
> (or believed) negative information is stored explicitly.** It is therefore not enough to stay within the
> class of finite p.r.'s."* — Trân & Bagai 2000, §2.2

And they assert infinite negative extents for **base** relations, not only derived ones (`Employee⁻`
holds ⟨John, 39999⟩, ⟨John, 40001⟩, …). Three operators force infiniteness — **join, selection,
projection** — the finite class not being closed under them, by explicit contrast with Codd.

**Retained contradiction is also occupied**, close to our own words. 1995 §1: *"inconsistency is also at
the tuple level, in that **a particular tuple may be considered to be both in and out of a relation**"*;
both papers say *"we do not assume `R⁺` and `R⁻` to be mutually disjoint"*; the four values are Belnap's,
cited. **Even the multi-party motivation is theirs** — 2000 §1 argues from two disagreeing TB tests
(*"it is important to pay heed to **both**"*) and a tank's three disagreeing sensors. Cite, don't re-derive.

**Two honest qualifications, both real.** (1) 1995 gives **no semantics on contradictory inputs** — every
justification proposition is proved for *consistent* operands, `comps` is undefined otherwise, and the
conclusion says the guarantee holds *"for … operands that contain consistent information."* Retained and
readable: yes. Retained and *reasoned about*: no. (2) 1995's flagship application never enters the
contradictory quadrant and gets its negatives from the **CWA** — `p⁻ = {tuples : p(b̄) ∉ P_E*}` — the
exact inverse of asserted falsity. 2000, which drops the consistency apparatus entirely, is the cleaner
citation.

**What is left, stated as a conjunction rather than a property.** Nobody has stored-asserted told-false
**and** a *constraint-described* region at once: 2000 answers the representation question with **automata**
(one 2-NFA per component; "constraint" occurs **zero** times), names the gap constraints would fill
(*"comparisons and arithmetic **cannot be performed** with regular paraconsistent relations"*), and closes
it by padding into CONTEXT-SENSITIVE/PSPACE instead. And **multi-party monotone accumulation is absent
from both** — neither defines an order on paraconsistent relations, a merge, or an update. Both algebras
*happen* to be knowledge-monotone by inspection; neither says so or uses it.

**The primitive act is still ours.** Neither paper exhibits an agent *asserting* a negative fact. The
**storage** of the result is theirs; the **assertion as the primitive act** is not.

#### Four things to take from it

- **Projection is the cost cliff, and it is a `∀`.** `π̇_Δ(R)⁻` requires *all* extensions be false — a
  universal over a fibre — and 2000 names it as the operation that *"prevents most of the infinite
  relation classes from becoming systems"*, doubly exponential where every other operator is polynomial.
  A direct warning if this design ever adds projection.
- **Selection is where an infinite told-false region is *born*, and it is born constraint-described**:
  1995's `σ̇_F(R)⁻ = R⁻ ∪ σ_{¬F}(τ(Σ))`. So the region shape *is* in this lineage — as an operator's
  output, never a stored record, and discarded for an automaton the moment 2000 asks how to represent it.
- **"Union" is two different operators, and this is a live collision.** Truth-order `⊍̇` **intersects** the
  negative halves; knowledge-order `∪` unions them. 1995's `⊍̇` is therefore the *wrong* operator for
  cross-party aggregation. Add to §"Terminology hazard".
- **The `p⁻⁻ = p` involution exists here, and 2000 drops it.** 1995 Def. 8: `(∸R)⁺ = R⁻, (∸R)⁻ = R⁺`, with
  Table 1's *"double complementation"* and De Morgan laws. That is precisely the swap discussed earlier
  in this session. **2000 removes unary `∸` from the operator set** — so the infinite-relations paper is
  the *monotone fragment*, which is where this design sits. A published precedent for the choice.

**Liu & Sunderraman 1990 is a different axis and not a predecessor on this question.** Zero occurrences of
"false", "negation", "complement", "contradiction". It models **disjunctive and "maybe"** information —
uncertainty about *which positive atom* holds — with no negative polarity at all. Its `REDUCE` operator is
however a clean published **foil** for *nothing retracts*: it deletes subsumed records and demotes the
residue to "maybe", with the information loss acknowledged in the paper's own motivating example.

## ✅ RETRIEVED 2026-08-18 — the T1 batch (review 7's retraction list), all open-access, none paywalled

All in `docs/resources/` (gitignored). **All UNREAD.** Retrieved to action T1: the doc presents three
things as reasoning that review 7 says are published, and review 7's *other* confident claims put three
sections in `dead_ends/` — so nothing goes into the doc until it is read here.

| file | for which claim |
|---|---|
| `power-koutris-hellerstein-2025-free-termination.pdf` | *"threshold claims always, exact claims at settledness"* — Props 9, 13/14, Thm 24 |
| `conway-2012-logic-and-lattices-bloomL.pdf` | the same claim (`gt_eq`/`when_true`), **and** the no-else-branch rule (footnote 2), **and** `lcart` for closure-as-fact |
| `kuper-newton-2013-lvars.pdf` | threshold reads |
| `kuper-2015-dissertation-lvars.pdf` | bonus — the LVars work in full, incl. *Freeze After Writing* quasi-determinism, which §"An atom's status" already gestures at |
| `darari-2013-completeness-statements-rdf.pdf` | closure as a posted fact — Def. 16, a pair `(C,k)` of statement plus producer |

**Power, Koutris & Hellerstein is `The Free Termination Property of Queries over Time`**, ICDT 2025,
LIPIcs vol. 328 paper 32 — review 7 gave the authors and proposition numbers but not the title, and the
title matters: *free termination* is *"in the absence of coordination, what query properties allow nodes to
unilaterally terminate even though they may receive additional data in the future."* That is this design's
**settledness question**, published, with a semiautomata model bridging relational transducers to CRDTs.
Expect it to be more directly on target than review 7 suggested, and possibly to price more than one claim.

### Darari, Nutt, Pirrò & Razniewski, *Completeness Statements about RDF Data Sources and Their Use for Query Answering*, ISWC 2013 — **CONFIRMED** (read in full, 18 pp.). **It refutes a novelty claim written into the doc on 2026-08-18**

`darari-2013-completeness-statements-rdf.pdf`. Review 7 was **right** about this one, and precise: it named
Def. 16 and described it as *"a pair `(C,k)` = statement plus producer."* That is exactly what Def. 16 is.

**What they have, and it is the doc's `¬Q` story:**

- **Scoped, not global.** Def. 3: `Compl(P₁ | P₂)` — `P₁` a pattern, `P₂` a condition; *"a data source
  contains all triples in a pattern `P₁` that satisfy a condition `P₂`."* Their running example is
  *"complete for all movies directed by Tarantino"*, which is a region, not a database.
- **Posted as data, machine-readable.** §3.2 gives the RDF vocabulary — `hasComplStmt`, `hasPattern`,
  `hasCondition` — so the claim is published *by the source, as triples*, and their motivating figure is a
  real *"verified as complete"* mark on an IMDb page.
- **Composable.** §4's operator `T_C(G) = ⋃_{C∈C} Q_C(G)`, with entailment `C ⊨ Compl(Q)`, and Thm. 14 for
  RDFS closure.
- **Multi-source, with union semantics.** Def. 15 (incomplete federated data source), **Def. 16 (indexed
  completeness statement, `(C,k)`)**, Def. 18: *"Q is complete if evaluated over the **union** of all
  sources in the federation."* Prop. 19 and Thm. 20 (smart rewriting) do the reasoning.

**Consequence: the paragraph added to §"Whose this already is" on 2026-08-18 is wrong.** It claimed *"a
completeness claim scoped to a region"* is not in any of the prior art, and called scope *"the entire
difference"* from free termination's global `All()`. Darari et al. published scoped-posted-composable-
multi-source completeness in 2013. **Delete the claim.**

**The residue, stated narrowly and worth much less than what it replaces:**

- Theirs is a **metadata layer about sources**, which is why Def. 16 must index a statement to a source
  IRI. Ours is a record in the **same store**, merged by the same union, needing no index — because `¬Q`
  claims about a region of the world rather than about one source's coverage of it.
- Their semantics is relative to an **ideal graph** `Gⁱ`, *"all the facts that hold in the world"* (Def.
  15), against which a statement is true or false; §6 concedes it *"rests on the assumption that a domain
  'expert' has the necessary background knowledge."* This design defines no correctness against a world at
  all — a false `¬Q` is a false post, under the same non-enforcement as a false value.

**And an independent convergence worth recording.** §6, *Maintenance*: *"For non-authoritative sources,
**temporal guards** can be used; e.g., instead of saying 'complete for all movies by Tarantino', one would
say 'complete for movies by Tarantino **in 2010**'."* That is this repo's *date the observation and make it
a fact about the past*, reached independently for the same reason.

**Complexity, for the coverage check:** *"All completeness checks presented in this paper are NP-complete"*
— conjunctive query containment reduces into completeness checking. The doc's coverage-as-entailment
reading inherits the same class; their mitigation is that queries and statements are small in practice.

**Calibration note on review 7.** It is accurate on the database prior art and unreliable on the
topological material — the same review produced `dead_ends/topological-framings.md` and understated the
free-termination paper, but named Darari's Def. 16 exactly right. Treat its database citations as leads
worth chasing and its category-theoretic ones as suspect.

### Power, Koutris & Hellerstein, *The Free Termination Property of Queries over Time*, ICDT 2025 (LIPIcs 328:32) — **CONFIRMED** (read in full, 22 pp.). Review 7's claim about it is **understated**

`power-koutris-hellerstein-2025-free-termination.pdf`. Review 7 cited it only for *"threshold claims
always, exact claims at settledness"*. It is much more than that: **free termination is this design's
settledness question**, and the paper's opening complaint is this document's, from the other side.

**The gap they name is the gap this design fills — and by a different mechanism.** On grow-only-set CRDTs:
*"CRDTs provide coordination-free consistency, but do not support free termination. In the absence of
coordination, we do not have a mechanism for determining locally whether we have received all the elements
in the network … **This is not a particularly satisfactory contract between the system and the user: what
good is distributed state if you do not know when you can query it reliably?**"* Their answer is to
*derive* termination from the query's algebra. **This design's answer is that settledness is told, not
derived** — a producer who knows posts it. The two are complementary, and the difference should be stated:
theirs works with no cooperating producer and only for queries with the right algebra; ours works for any
query but needs somebody who knows.

**Definitions and the results that bear on the threshold rule:**

- **Def. 3 (free termination state):** `s` is one for `Q` if `Q(s) = Q(s')` for every `s'` reachable from
  `s`. *"the distributed system can output the value of `Q` without the need to continue the computation."*
- **Def. 12 + Prop. 13 (threshold queries):** a threshold line is an **antichain** `C`, and
  `Q_C(s) = ⋁_{c∈C}(s ⊒ c)`. Under an inflationary semiautomaton the free-termination states are
  **exactly** the elements at or above `C`. And — *"any monotone Boolean query **must be** a Boolean
  threshold query"* (excluding the always-false one). So the threshold form is **forced**, not chosen;
  §"An atom's status" is not one design among several.
- **Prop. 9 / Prop. 10:** inflationary plus a maximal state, or a monotone query at a maximal answer, gives
  free termination.
- **Prop. 14:** even a *non-monotone* query with any free-termination state has an antichain threshold —
  above it the behaviour is governed, below it unconstrained.
- **Prop. 15 / 16 (join-semilattice):** all free-termination states return the **same value**, and from any
  state a free-termination state is **reachable**. This design's order is set inclusion, so settledness is
  consistent and never unreachable — both worth claiming and neither currently claimed.

**Thm. 18, the "inverse curse theorem" — the retraction commitment, proved independently of CALM.** *"Let
`Q` be a non-constant query. If every state of `D` is invertible, then `Q` has **no free termination
states**."* Cor. 20: the same when `(D,U)` forms a group. Their gloss: *"Two parallel lines of work have
shown the value of invertibility in data systems (DBSP, DBToaster) and the value of coordination-free
monotone queries (CALM theorem, CRDTs), but **the benefits of these properties appear mutually
exclusive**."*

So §"Two commitments" has a second, independent argument: allow retraction and **nothing ever knows it is
finished**. Not a CALM restatement — CALM is about soundness, this is about completeness, and the paper
says so.

**Thm. 24 — and it corrects the doc's justification for `= {f}`.** *"A Boolean query `Q` is positively
(resp. negatively) coordination-free if and only if `Q` is monotone (resp. **antitone**)."* Positive
coordination-freeness *"is exactly the notion of query coordination-freeness used for transducer
networks"*. So antitone queries — emptiness among them — **are** coordination-free, in the *negative*
direction: Example 11 freely terminates on *"every element of the stream is an `a`"* the moment a non-`a`
arrives. And Ameloot's exclusion of them is diagnosed as an artefact: *"because of … the encoding of the
boolean values True and False being the presence of an empty tuple and the absence of a tuple."*

The doc's §CALM currently says `= {f}` has no syntax *"because the theorem licenses it."* That reasoning is
too crude. The precise statement: an emptiness query is antitone, so it free-terminates exactly where a
**witness** appears — which is the direction that finds an atom, never the direction that finds none. The
asymmetry survives intact and is now derived rather than borrowed.

**Thm. 22 — coordination-freeness is a property of a (query, input) pair**, not of a query alone: *"we
avoid coordination for a given query on some inputs, but not all inputs!"* The doc's language-level ban is
therefore **conservative** — it buys the guarantee for every input, at the cost of refusing queries that
would have been fine on the inputs actually seen. Worth stating as a deliberate trade.

**§5.2 — the roster, a third time.** Model `All()` as a nullary relation; states with `All = T` get
self-loops, so every such state is a free-termination state. *"Of course, the tradeoff is that updating
`All` requires coordination between the nodes."*

### Hellerstein, *Complete CALM: A Coordination Criterion for Specifications*, arXiv:2602.09435v4 (14 June 2026) — **CONFIRMED** (read in full, 26 pp.), and it **corrects §CALM**

`hellerstein-2026-complete-calm.pdf`. **Single-authored — Hellerstein, not Power**; a search result
grouped it with Power's ICDT paper and that was wrong. Two months old, postdating every other CALM reading
here. It is the most consequential paper in this ledger.

**The framework.** A **specification** is `Spec = (E, Obs, ≼)` (Def. 4): an event universe, a map
`Obs : H → P(O)` from histories to admissible outcomes, and a **declared** partial order `≼` where
`o₁ ≼ o₂` means *"o₂ refines o₁ without contradicting it."* Histories are Lamport partial orders; a future
`H₁ ⊑ₕ H₂` may add causally later events but **not insert predecessors** (Def. 3) — *"the past is fixed;
only the future is open."*

- **Def. 8 (monotone spec):** for all `H₁ ⊑ₕ H₂` and all `o ∈ Obs(H₁)`, some `o' ∈ Obs(H₂)` has `o ≼ o'`.
- **Thm. 1 (Complete CALM):** *"A specification is coordination-free iff it is monotone."* The proof is
  immediate — he says so: *"the natural CALM 'theorem' above is definitional."* The content is the framing.
- **Thm. 2 (Operational Complete CALM):** the same, against I/O automata, for the full interface contract.

**CORRECTION 1 — §CALM's deflation is an artefact of Ameloot's formalism, and the property is stronger
than this ledger and the doc have been treating it.** The doc says coordination-freeness is *"existential
over placements … not a promise that a real run sends none,"* hence *"'no round trips' is not licensed."*
That is right about Ameloot. **Def. 9 (coordination-free, operational)** is a different and much stronger
condition: *"for every process `pᵢ` and every client invocation `inv(e)ᵢ`, the response `resp(e,v)ᵢ` is
enabled **immediately**: there exists an execution fragment from `pᵢ`'s post-invocation state consisting
only of internal and output actions at `pᵢ` … **without requiring any further input action** at `pᵢ`."*

So it **is** a responsiveness guarantee: no request ever waits on a message. Not *"no messages are sent"* —
the sufficiency proof's causal-view protocol gossips on every event — but **no response blocks on one**.
The doc's *"what survives the deflation is a correctness property, not a performance one"* (added
2026-08-18) is therefore too weak and should be rewritten.

**CORRECTION 2 — coordination-freedom and replica convergence are two properties, not one, and the doc
conflates them.** §6.1: *"the transducer model studies coordination-free computation of a common output
set, so **replica agreement is built into that formulation**. Complete CALM **decouples** the two
properties: monotonicity characterizes when coordination is avoidable; **replica consistency is a separate
structural property of `≼`**."* §7.4: *"if `≼` admits a join-semilattice structure … monotonicity implies
convergence; if `≼` lacks joins, monotonicity guarantees safe independent action but not convergence."*
This design has both — but because `≼` is set inclusion, which has joins, and that reason should be stated
rather than assumed.

**Confirmations of things the doc already says or wants:**

- **Remark 2 (joint consistency without agreement)** — independently chosen responses at different
  processes are *"jointly consistent without any inter-process agreement protocol … Joint consistency is
  not an additional assumption—it is a **free consequence of monotonicity** applied to the full history."*
  That is §"What it is for"'s *"everybody must still reach the same answer without stopping to confer"*,
  proved.
- **§4 Proper coordination** is §"Two layers, and what crosses between them", published. Def. 11
  (properly coordinated variant): shrink `Obs` enough to restore monotonicity, then test the residual.
  **Thm. 3 (Separation): relational-transducer CALM *cannot in general* verify proper coordination** —
  adding coordination rules to a Datalog program leaves negation in the program, the syntactic check gives
  a false negative, and deciding monotonicity in stratified Datalog is undecidable. So the doc's claim that
  its layer boundary *"coincides with the CALM boundary"* needs the **specification-level** criterion; the
  one it currently cites cannot check it.
- **Example 6 + Remark 3 — the roster criterion, published.** *"membership knowledge is the single
  non-monotone input that renders all subsequent computation monotone,"* generalising Ameloot's
  non-oblivious result; *"membership is configured once; everything downstream is actually
  coordination-free."* **Thm. 4:** coordination can always be factored into membership authority + an
  ordering service.
- **Prop. 4 (CRDTs are monotone)** — inflationary updates on a join-semilattice with the lattice order as
  outcome order. The grow-only-set observation added to §CALM is an instance of this, not an analogy.
- **§3.6** — *"monotonicity is guaranteed for positive queries (Datalog without negation) … for Datalog,
  the absence of negation ensures monotonicity with respect to set inclusion."* Supports §"What makes the
  answer worth having"'s *"a checker can enforce it"*; and checking monotonicity on general specifications
  is **undecidable** (Rice-like), so a syntactic fragment is the only tractable route.

**And it settles the relationship to the other T1 paper.** §9: *"Power et al. study whether a node can know
its output is **final** without coordination—**strictly stronger than monotonicity**. The two results are
**orthogonal**."* Free termination is the settledness question and it is a *different axis* from CALM —
which means the doc's settledness material and its CALM material price different things, per both authors.

*Disclosure noted in the paper's acknowledgments: generative AI was used as a writing and reviewing
assistant, with all content reviewed and approved by the author.*

## ✅ RETRIEVED 2026-08-14 — all five, in `docs/resources/` (gitignored)

`bagai-sunderraman-1995-paraconsistent-relational-model.pdf` ·
`tran-bagai-2000-infinite-relations.pdf` ·
`kanellakis-kuper-revesz-1995-constraint-query-languages.pdf` ·
`denecker-cortes-calabuig-bruynooghe-arieli-2010-locally-closed-databases.pdf` ·
`liu-sunderraman-1990-indefinite-and-maybe.pdf` · plus
`morton-contextuality-missing-versioned-data.pdf` (unrequested; the paper review 7 called *"our setting
verbatim"* for the now-dead sheaf material). **Read in full 2026-08-16 — see CONFIRMED above.**

All have extractable text layers **except KKR**, which is a 27-page scan (27 chars of text). No OCR
tooling on this machine; it must be read as page images. Its questions are the lowest-value on the list
now that Van den Bussche supplied the definitions in primary text.

**Consequence: the remaining passes need no web access at all**, which matters — search budget is
exhausted at 200/200.

## ⛔ PULL LIST — superseded, retained for the questions each paper must answer

Web-search budget is exhausted for the session (200/200); WebFetch still works, so *named* documents are
still reachable, but these five are paywalled or blocked. Ordered by value.

1. **Trân, N. Q. & Bagai, R., *Efficient representation and algebraic manipulation of infinite relations
   in paraconsistent databases*, Information Systems 25(8):491–502, 2000.** DOI
   `10.1016/S0306-4379(00)00032-6`. **The single most important document.** Need: is a stored relation
   allowed an infinite component, or only a derived one (the asserted-vs-computed hinge)? Which operator
   forces infiniteness? Is `R⁺ ∩ R⁻ ≠ ∅` retained over *infinite* components? Are the operators monotone
   w.r.t. componentwise ⊆? Any accumulation / multi-source story?
2. **Kanellakis, Kuper & Revesz, *Constraint Query Languages*, JCSS 51(1):26–52, 1995.** DOI
   `10.1006/jcss.1995.1051` (also PODS'90, `10.1145/298514.298582`). Need: the exact *generalized tuple*
   definition; the closure result's wording on negation; whether an **asserted** negative constraint
   region is anywhere contemplated. Confidence is already high from Van den Bussche — this converts it to
   confirmed-at-source.
3. **Bagai & Sunderraman, IJCM 55(1–2):39–55, 1995.** DOI `10.1080/00207169508804361`. Six questions from
   the previous pass still open; the load-bearing one is whether `R⁺ ∩ R⁻ ≠ ∅` is *retained* or excluded
   by a well-formedness condition.
4. **Denecker, Cortés-Calabuig, Bruynooghe & Arieli, *Towards a logical reconstruction of a theory for
   locally closed databases*, TODS 35(3), 2010.** DOI `10.1145/1806907.1806914`. See the LCW note below.
5. **Liu, K.-C. & Sunderraman, R., *Indefinite and Maybe Information in Relational Databases*, TODS 15(1),
   1990.** DOI `10.1145/77643.77644` — **free at ACM**, merely 403 to the agent. The direct predecessor of
   the Bagai–Sunderraman model, same second author. Cheapest window onto that lineage.

### Denecker, Cortés-Calabuig, Bruynooghe & Arieli, *Towards a Logical Reconstruction of a Theory for Locally Closed Databases*, TODS 35(3), 2010 — **CONFIRMED**

Read from `docs/resources/`; quotes below grep-verified by me against the text layer, not relayed.

- **Def. 3** — `LCWA(P(x̄), Φ[x̄])`, a predicate plus a **window of expertise** (a *first-order* formula,
  strictly more expressive than Levy's). Reading: *"for all x̄ such that Φ[x̄] holds in the real world, if
  P(x̄) is true in the real world, then P(x̄) occurs in the database."*
- **Def. 4** — its semantics at a store is a plain first-order sentence, `∀x̄. Φ[x̄] → (P(x̄) → P(x̄) ∈ D)`,
  with `P(x̄) ∈ D` a **disjunction of equalities** (Notation 1). §2.5 shows Levesque's `K` operator is
  **eliminable** in favour of exactly this.
- **Def. 14 / Def. 16** — negative conclusions come from **absence**: the operator fires on `P(d̄) ∉ D`;
  the certain-false rule is *"tuples … that do not occur in the database and for which the window of
  expertise is certainly satisfied."* **No construct anywhere asserts falsity directly.**
- **Verbatim** — *"a change in the database modifies the extended relational formula … It follows that a
  local closed world assumption is a **nonmonotonic construct**."*
- **Prop. 4** — a locally closed database is **always consistent**. Their logic is three-valued Kleene;
  **there is no top**, so *told-both* has no image in the formalism.
- Multi-source aggregation (§2.6) is **union of the positive extents** — so more parties make every
  closure **weaker**. This design's union of told-false extents makes them **stronger**. Cleanest one-line
  contrast available.
- **Prop. 12** — certain answers to **monotone** queries are computable directly from `D` in PTIME. Their
  intractability (`CWI` coNP-complete; undecidable over all databases) is bought entirely by negation
  *inside* windows. **A design with no negation as an operation sits in their tractable corner by
  construction** — worth stating as a positive result, not only as a contrast.

**A recorded claim of ours is REFUTED.** *"LCW must be retracted as the KB grows"* is false, and the paper
says the opposite twice, verbatim: LCWAs are *"a more permanent form of knowledge than the transient
data"*, and *"just like integrity constraints, are fairly constant during the lifetime of a database."*
What actually happens is **better** for the design: post inside a declared-complete region and nothing is
retracted, nothing violated, no inconsistency (Prop. 4) — the negative conclusion **silently stops being
entailed**, with no event and no trace. `¬Q` climbs to `{t,f}` instead. Fixed in the doc.

**Two attribution fixes.** Cite **Levy 1996** as the LCW exemplar, not Etzioni/Golden/Weld — E/G/W's is
Motro's *completeness-constraint* form, and, awkwardly for us, **they store explicit negative literals**.
And *"query containment"* is not their term: Levy reduces to **query-update independence**, Motro-style
constraints to **answering queries using views**.

**And their §7.1 future work is this design's record.** Materialising a told-false extent is unsafe in
their setting (*"unsafe as m and id are unconstrained"*), and their proposed remedy is verbatim:
*"symbolic query answering methods that return **queries with constrained variables** to correctly
represent the answer in arbitrary domains … techniques from **constructive negation** in logic programming
could be useful [Chan 1988]."* **A constraint-described negative region is what they reach for and do not
build.** Strong support, and the best citation found so far.

**Do not cite GCWA** as a neighbour — §6.2.2: *"the GCWA is a very different principle than LCWA's."*

## Superseded: the earlier note on local closed-world assumptions

Flagged by this pass as threatening the region property **more than constraint databases do**: a party
asserts the database is *complete for a described region*, and negative facts follow. That is
"asserted region + absence-of-evidence ⇒ told-false" — nearly the same destination by the mechanism this
design says it does *not* use.

An earlier pass concluded LCW is an **inference licence** rather than a positive assertion, with the
differentiator being that LCW must be **retracted** as the KB grows while a told-false fact never is —
and `c0a18ba` wrote that into the doc. **That conclusion is itself unverified against primary text**, and
the doc is currently leaning on it. Sources: Levy, VLDB 1996, 402–412; Cortés-Calabuig et al., AAAI 2007;
Denecker et al., TODS 35(3), 2010. Also unverified even bibliographically: Motro TODS 1989;
Razniewski & Nutt PVLDB 2011.

**This is the next pass**, and it should verify the doc's existing claim rather than assume it.

## Not chased, noted

- **Koubarakis, indefinite constraint databases** — LNCS 1994 `10.1007/3-540-58601-6_106`; Information
  Systems 1994 `10.1016/0306-4379(94)90008-6`; TCS 1997 `10.1016/s0304-3975(96)00124-7` (marked OA).
  Indefiniteness (disjunction / possible worlds) under classical logic — expected adjacent, untested.
- **The two literatures never met.** Trân & Bagai cite no constraint-database work; Van den Bussche's
  field survey cites no paraconsistent or incomplete-information work. Two asserted extents *over
  constraint-described regions* is a bridge neither line built. That is an argument, not a census.

---

## UNOBTAINED — superseded by the pull list above

### Bagai & Sunderraman, *A paraconsistent relational data model*, Int. J. Computer Mathematics **55(1-2)**, 1995, 39–55. DOI `10.1080/00207169508804361`

Closed access; no OA copy exists (Unpaywall `oa_status: closed`, no repository copy; CiteSeerX defunct;
both author pages gone). **Nothing about this paper is currently verified.**

What must be checked, in priority order:

1. Is a paraconsistent relation literally a **pair ⟨R⁺, R⁻⟩** over one scheme?
2. Is `R⁺ ∩ R⁻ ≠ ∅` **permitted and retained** as the representation of inconsistency — or excluded by a
   well-formedness condition, with the algebra confined to a consistent subclass? **The load-bearing one.**
3. Componentwise ⊆ as the order, componentwise union as the join? Any explicit lattice claim, any top?
4. How is **complement/negation** defined? If it is the `⟨R⁻, R⁺⟩` swap it is an involution, which differs
   meaningfully from a positive-only logic.
5. Any **non-ground / intensional / infinite** extents, or is `R⁻` strictly finite extensional?
6. Any distribution, replication or multi-source aggregation?

*Low-confidence pointer, not evidence*: Bagai's later *"Infinite Relations in Paraconsistent Databases"*
(1999) and *"Efficient representation and algebraic manipulation of infinite relations in paraconsistent
databases"* (2000) exist, weakly suggesting the 1995 model is finite-extensional.

---

## UNVERIFIED — queued

- **Constraint databases** (Kanellakis, Kuper & Revesz, ~1990) — flagged by the CCP/4QL pass as the most
  likely home for *told-false over an infinite constraint-described region*, which is simultaneously this
  design's most load-bearing property and the one most likely to lose novelty. **Check before claiming
  anything about regions.**
- Jakl, PhD thesis 2018 Ch. 6, *"Belnap–Dunn logic of bispaces"* — geometric logic in the information
  order, `con`/`tot` as **judgement forms rather than formulas**.
- Ameloot, Ketsman, Neven & Zinn, TODS 40(4), 2016 — weaker forms of monotonicity; candidate formal home
  for "told falsity buys coordination-freeness".
- Belnap 1977 — the "told true / told false / told neither / told both" vocabulary, used verbatim here.
- Gelfond & Lifschitz explicit negation minus the consistency constraint; Blair & Subrahmanian;
  Kifer & Subrahmanian GAP.
- Review 7's retraction list (punctuations, LVars/Bloom^L thresholds, Bloom^L fn. 2, residuation/Hanus,
  Oz `ByNeed`, the ICDT 2025 termination results).

## Where the contribution stands, as of this pass

Two properties are **unclaimed by CCP or 4QL**: distribution / CALM (CCP predates it by ~20 years and
cites *Janus* as the road not taken; 4QL is a centralised fixpoint), and **demand-driven production with
the store as cache** (named as open work in 4QL). The region property is **unresolved** and should be
checked next.
