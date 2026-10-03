# Fresh-eyes adversarial pass — `backlog/if-built-today.md`

**Untracked working note, 2026-08-25; Part 1 revised 2026-09-04.** Point-in-time; delete when consumed.
This is TODO 2 of `handoff-if-built-today.md`, now spent. The doc was at 1,937 lines, branch
`spec/episode-aim`, clean. Part 1's original diagnosis was superseded by a ten-day design dialectic; the
revision records where it landed and what was folded into the doc.

**Method.** Five independent adversaries with distinct mandates (internal consistency; citation/epistemics
against the ledger; formal/mathematical; hostile cold reader; the repo's own orthonormal-basis rubric),
none given the handoff's predicted soft-spot list, plus one own-read. ~60 raw findings. **Every CRITICAL
below was verified against primary text before being recorded here; three red-team claims were refuted and
are in Part 5.** Convergence is noted where more than one adversary reached a finding independently.

---

## Part 1 — The structural finding (revised 2026-09-04)

**One decision settles the demand-layer findings: a hole in a posted term is a variable with a wire
name, not a constant.** Folded into the doc 2026-09-04 (`:194` retracted; `if-built-today-decisions.md`
§"The Skolem reading").

The 2026-08-25 version of this section said *"the design cannot say ask from tell"* and proposed an
`intent(A) = tell(A) | ask(A)` wrapper. **That was a symptom, and the wrapper was patching it.** Worked
through 2026-08-29 → 09-04:

- **The over-claim.** `:194` said a hole *"is an ordinary constant"*; `:311` said a variable *"travels as
  a constructor `var(37)`"*. Different strengths. The first makes `loss(60, v37)` a ground literal; the
  second only names an unknown. Only the second is needed for *"sharing is a posted equality"*, which is
  the load-bearing deletion — the decisions file's own "What survived" paragraph already said *"a durable
  **name**"*. The main doc had drifted from its trail.
- **Why the constant reading fails, twice.** (i) No producer can act on it monotonically: *"still has a
  hole"* is `var/1`, antitone (`:462`); recognising it by spelling is the open-namespace error; by
  absence-of-equality is observing absence. (ii) **Skolemization witnesses.** `loss(60, c)` for fresh `c`
  *satisfies* `∃x. loss(60,x)`, and `:257` defines demand as an ***unsatisfied*** existential. The demand
  answers itself; the residual subtracts it from the region it defines; admission control's quantity
  reads **one** for every demand.
- **What a demand is: asserted, not witnessed.** A partial term asserts existence, provides no witness,
  decides no atom (`:519`). It cannot be disputed. A producer's `¬Q` over its region makes the region
  read entirely `{f}` — the affirmable form of *unsatisfiable* (runstate's `is_unsatisfiable()`).
- **Formally.** A constraint-store variable (CCP, `:1012`): identity, not a value, information
  accumulates. `∃`-bound at **store** scope; the wire name is the **scope extrusion** — which is why the
  naming table is load-bearing. `request_id` in the shipped protocol is this name in disguise.
- **Quantifiers.** Four remain and none is a term-former: clause-`∀` (being a rule), body-`∃` (a
  body-only variable), store-`∃` (a variable in a post), region-`∀` (being negative). Read as a
  polarity × quantifier grid, **positive-`∀` is the empty cell** — the one quantifier not derivable
  from shape, hence the one needing a wire flag (`:1071`), hence the source of finite-coverability and
  §Open 1. Part 1b checks whether it can go.

**What this closes.** Gap 1 (variable-ness *is* the marker, structurally); the control/logic fork (a
partial term is legitimate logical content); the self-answering residual; the spurious `{t,f}`; and
**the handoff's TODO 1** — the quantity signal survives the variable reading and not the constant one.

**What it does not touch.** D4 (`¬Q` still carries falsity *and* termination; exhaustion has no shape),
D5, D1, P1.

**Push vs pull is not a choice (corrected 2026-09-04).** An earlier revision proposed "bottom-up
production as the model." That was a false dichotomy, twice over. (i) *Finiteness*: the fragment has
function symbols — `S+1`, the `Fact` sum — so `expected(S+1) :- expected(S)` is a legal definite clause
and forward chaining from one base fact does not terminate. No-value-invention (`:325`, `:352`) blocks a
fresh Skolem, not term growth; Datalog safety needs *no function symbols*, which this fragment does not
have. (ii) *Semantics*: the least model is the same under either strategy; what differs is
materialization, and `:1596` already books derived atoms as an evictable cache.

What is real is **magic sets**: forward chaining over base facts *plus demand facts* computes exactly what
goal-directed evaluation would, terminates when it would, and **the demand facts are what bound the
recursion**. **The design already has it.** `:556` rejects the separate-predicate form — `demand(X)`
*"smuggles a key/value split back in through the adornment"* — but the adornment is a property of the
*query*, not the term, and the partial term `loss(60, var(37))` **is the magic fact** with the `var`
functor as the adornment, folded into the base relation. `:198`'s *"the same term is the question and the
answer"* is what folding the magic predicate in looks like. The doc rejected the thing it was doing.

**Matching `var(N)` is monotone.** The producer's rule `produce(S,N) :- loss(S, var(N))` matches a
*functor* — a fixed property of an immutable term. It is not Prolog's `var/1` (`:462`), which tests
mutable binding state; there is none here, because binding is a posted equality and the term is never
touched. Derivation runs on syntax (`:364`: *"joins on syntactic coincidence"*); congruence closure is a
*reading*, downstream. Whether to *spend* six hours — *is `var(N)` filled yet?* — reads `∅` and is the
residual: control, local, never published (`:791`, `:822`). Reification made the antitone test unwritable
for posted terms: `:238`'s "holds by shape" once more.

**What survives of the proposal.** A "subscription" is a local rule seen from the transport and gates
nothing; a demand is a partial-term fact and gates a rule — different things. The shipped subscription is
still diagnosed by the design's own rules: three concerns in one primitive, and **identity by position**
(the "positional answer fold"), which `:48` forbids. What triggers an *external* producer — a ground
precondition or a partial-term demand — is "which fact is the trigger," and both are facts.

*Converged: formal skeptic (C3), rubric attacker (F1, F4, F5), and the handoff's parked TODO 1 — all
found the symptom; the decision above is what resolves them.*

### Part 1b — the `∀`-demand check (2026-09-04)

**Claim checked:** the demand the doc calls itself best at — *"every step, run to convergence, and tell
me when there are no more"* (`:1086`) — needs no positive-`∀`, so the empty cell can stay empty.

**Result: holds, and more simply than first stated.** Neither half is a positive-`∀` request:

- *"every step"* — the producer emits every step because that is what a triggered producer does; the
  asker's rule `handle(S,V) :- loss(c,S,V).` fires per arrival. Not a demand.
- *"tell me when there are no more"* — the residual rule (`:597`) **obliges** a converged producer to post
  `¬(Q₀ ∖ E)`; it arrives unbidden, and the asker reads it with an ordinary `⊒{f}` body literal (`:481`).
  Not a demand.
- *triggering a producer that isn't running* — an `∃`-demand `loss(c, var(1), var(2))` (asserts at least
  one loss for `c`; decides nothing; the producer's rule matches the `var` functor), or a plain tell of
  whatever the producer keys on. Not `∀`.

So positive-`∀` was never a request; it is what producers *do*. The variant first proposed — an
`∃`-demand on the negative relation, `¬loss(c,S,V), S > var(4)`, a variable inside a region's
constraint — also works (a partial *negative* term, deciding nothing until `var(4)` is bound) but is not
needed.

**Rule-producers discharge the obligation in the fragment.** `neg(avg(c,S,V)) :- neg(loss(c,S,V))` is a
definite clause over the polarised signature (atomic head), so a derived relation's `¬Q` is a rule.

**What bounds a demand's extent is its CLP constraint, not a quantifier.** `loss(c, var(1), var(2)),
var(1) ≤ 1000` is bounded; `S ≥ 1` is not; the solver reads which. **So §Open 1 splits.** For a
*rule*-producer, *"is this region bounded?"* is a constraint-solver question — checkable, and never
assigned because it was bundled with the other half. For an *external* producer it is a claim about a
process's future — unfalsifiable, and that is **D4**'s halted case.

**What is lost.** The ability to ask for a region to be decided that no producer will decide — steps
301..1000 from a run halted at 300. That request was never fulfillable; the uncheckable half of §Open 1
existed to detect exactly it. Dropping positive-`∀` deletes that half with the request. The halted
producer itself is **D4**, unchanged.

**Consequence if adopted:** delete the `∀` flag at `:1071` and the inverted-contravariance case at
`:1141`; restate §Open 1 as the rule-half only, owned by the solver. Nothing on the wire carries a
quantifier — a demand's extent travels as its constraint.

---

## Part 2 — Verified design-level findings

### D1. The `≠` fault line — the fragment violates the requirement that selects it

> **Resolved 2026-09-25.** `≠` in a body was ruled out long ago (§"Sort closure": *"never in the
> derivation layer"*; `20ab08e`: *"nothing writes `≠`"*). `beaten`'s `A ≠ A2` was added ten days
> *earlier* by `508c738`, fixing a fresh-reader finding (an arm with two values beat itself), and
> `20ab08e` never audited it. Fix: `beaten(A, V)` — per value, not per arm, which is the well-posed
> question once there is no FD. An intermediate proposal to keep the `≠` via Ameloot's `H_inj = M` was
> **wrong**: it used homomorphisms *of models*, where the doc's criterion is algebra homomorphisms on
> Herbrand terms. Still open: `:364`'s *"strictly inside the coordination-free class"* infers an
> Ameloot class (model-homomorphism) from an algebraic premise — see `handoff-if-built-today.md` item A1.
*Converged three ways: consistency reader, citation auditor, formal skeptic.*

`:345-353` selects the fragment by two requirements, one being **preservation under homomorphisms**.
- `:415` uses `≠` in a derivation rule and calls it load-bearing: `beaten(A) :- value(A,V),
  value(A2,V2), A ≠ A2, V2 > V` *"only ever grows"*, *"(The disequality is not decoration…)"*
- `:1505` concedes: *"What it costs is preservation: `≠` is **not preserved under homomorphisms**"*
- `:1527` states flatly: *"**nothing writes `≠`** and nothing translates it"*
- `:1253` calls the state test *"**forbidden**"* — while `:89` is the headline property, *"Unsound things
  are **unwritable, not forbidden**"*
- `if-built-today-citations.md:184-187` (**CONFIRMED**) conditions the placement claim on its absence:
  *"…no negation **and no disequality** … sits in `H`, **strictly inside `M`**. Conditional and worth
  settling: **if constraint regions can express `≠`, we drop to `M`**"*
- `:361` states the conclusion anyway: *"the placement **strictly inside** the coordination-free class"*

**Concrete failure** (formal skeptic): store `value(a1,5)`, `value(a2,7)`. Reader R₁ derives `beaten(a1)`.
A middleman posts the translation equality `eq(a1,a2)` — legal under `:218`. Reader R₂, who folds it,
**loses** `beaten(a1)`. The only worked example of derivation-layer monotonicity is monotone in the store
and antitone in the congruence.

**Scope:** the design stays coordination-free — `H → M` loses *strictly inside*, not membership.
**Fix:** scope preservation to the `≠`-free sub-fragment and admit CLP constraints sit outside the
guarantee, or fix the congruence at the finest for derivation (making the lens lattice read-side only).
`:361`'s "strictly inside" goes either way.

### D2. Props. 15/16 applied with the wrong predicate — and the error is in the ledger
`:531-533`: *"Two stores both settled for a question have a least upper bound reachable from each; each
being settled forces it to agree with that bound … Two agents who can both stop **cannot disagree**."*

The ledger states Def. 3 correctly at `:590`: a **free-termination state** has `Q(s) = Q(s')` for every
reachable `s'`. Settledness (`:510`) is an **up-set** — `{t}`, `{f}`, `{t,f}` all satisfy it — and `{t}`
is settled but not fixed.

**Counterexample:** `S₁ = {metric(loss,0.31,61)}`, `S₂ = {¬metric(loss,V,S) for S>40}`. Both settled for
that atom; answers `{t}` and `{f}`; join `{t,f}`. Neither agrees with the bound.

**The slide originates at `if-built-today-citations.md:601-603`**, which quotes the theorem correctly and
then concludes *"so settledness is consistent and never unreachable — both worth claiming and neither
currently claimed."* The doc took the advice. **Fix both files.** The true weaker claim: two settled
agents' *testimony* is jointly satisfiable at the join, because `{t,f}` absorbs both. That is
consistency-of-testimony, not agreement — and `:479` (*"We never bought consistency"*) already says so.

### D3. Preservation under homomorphisms is claimed of *formulas*; definite clauses fail it

> **Downgraded and fixed 2026-09-25: a wording mislocation, not a wrong criterion.** The counterexample
> below uses homomorphisms *of models*. The companion means **algebra homomorphisms applied to the terms
> in each rule** (its own `:44`), and in that sense the argument holds — including the denial case,
> since identifying terms can make a denial's body hold. `:51` now says *"a property of the program
> under transport"*. The ANSWERED status stands. The entry below is kept as the record of the
> conflation.
`definite-clause-maximality.md:51`: *"preservation under homomorphisms is a property of the *formulas*"*.

**Counterexample:** `φ = ∀x.(p(x) → q(x))`, a definite clause. `M`: `p=q=∅`, `M ⊨ φ`. `N`: `p={a}`,
`q=∅`, `N ⊭ φ`. `id : M → N` is a homomorphism (both relations empty in `M`). So the criterion as stated
selects nothing.

The working property is about **queries over instances**: `h(lfp T_P(I)) ⊆ lfp T_P(J)`, i.e. Datalog
queries are unions of conjunctive queries, hence existential-positive, hence preserved. The companion
states this correctly at `:44-46` and mislocates it one paragraph later. Under the repair, *"denials are
not preserved"* is a category error (a denial defines no output relation), so the "outer boundary" claim
needs rewriting, not relabelling. **`definite-clause-maximality.md:8` marks the question ANSWERED; it is
not.**

### D4. `¬Q` carries two orthogonal concerns
`:275` — *"What ends a stream is the post `¬Q`"* — against `:511`, where **stream**-complete is
*"exhaustion, hence control"*, and `:613`'s own separating test: *"could a third party post this knowing
only that the process died? For **exhaustion** yes … For **falsity** no."*

`if-built-today-decisions.md:47-49` records the split as a **finding** and the refusal as deliberate
(*"a name is an invitation"* → *"not a predicate at all"*).

**Cost, measured in the doc and never connected to it:** `:1905` — **411 of 2,743 stops (15%) carry no
`final_step`**, so they *"close nothing and delimit no region."* A halted (not converged) producer knows
nothing about the remaining region and must post nothing (`:571`); a pid probe answers correctly and has
no wire form (`:631`, `:634`). Those streams never end.

### D5. Settledness is the residual's emptiness test — one primitive, two names, opposite sides of the line
`residual(Q) ≡ { a ∈ ground(Q) : status(a) = ∅ }`; `settled(Q) ≡ residual(Q) = ∅`. The doc states the
equation itself at `:1048`: *"Its residual against a settled region is empty."* Yet `:822` puts the
residual outside derivation (*"not a derivation … local, best-effort … control"*) and `:510` puts
settledness inside (*"**derivable**, for a `Q` whose extent is fixed"*).

What is real is that the two directions differ in affirmability: **non**-emptiness is a down-set,
unaffirmable, correctly local; **emptiness** is witnessed by a finite cover, affirmable, publishable. One
object, two directions — not two objects.

**Related (rubric attacker, unverified by me):** settledness may not be generically derivable at all —
*"every atom of `ground(Q)`"* is a `∀` over a region, the language has no `∀` (`:300`), and `Open` is
declared opaque with no `denote` (`:1164`). Derivable per-axis by hand-written induction; inexpressible
for a region like `grad(Config, Layer, Step)` with `Config` open. **Worth working through** — it bears on
`:1780`, which makes settledness the precondition for all aggregation.

---

## Part 3 — Verified doc-level findings

| # | site | defect |
|---|---|---|
| P1 | `:1043` | **The worked example.** Claims `S ≥ 1` settled by *"743 positives and one negative"*. Needs an FD; `:186` denies it, `:518` says a ground positive *"decides one"*, and `:1053` — **ten lines later, same example** — says loss is not functional in the step *"unless somebody wrote a rule"*. `decisions.md:136-140` already names this exact error as refused. Correct post is `¬(Q₀ ∖ E)` per `:597`; cover is ~683 records larger than claimed, so `:1040`'s *"That single record decides infinitely many atoms"* holds only for the tail. *Converged: fresh reader + own read.* |
| P2 | `:1194-1195` | **Sharing reification didn't propagate.** Still says *"distinct **tails**"* and *"variables being **shared** … whoever holds a **reference** to *that* variable"*, citing §"The model" — which says the opposite at `:194`, as does `:1824`. "Tails" is the growing-term representation killed at `:282`. *Converged: consistency reader + own read.* |
| P3 | `:773` vs `:822` | **A retracted phrase, rebuilt on.** `:773`: *"**Not** unanswerable without coordination — answerable in one direction."* `:822` rebuilds the residual's justification on *"unanswerable without coordination"*. `:802` calls the residual *"the design's most load-bearing multi-hop argument."* |
| P4 | `:706-708` | **"Exactly the six" is five.** 6 monotone predicates of 16 (correct), but only **5** are expressible from `⊒{t}`, `⊒{f}` under `∧`/`∨` — verified by enumeration. The missing one is the empty up-set, which needs `⊥`, the connective `:323` bans. **The ledger had it right**: `citations.md:594-596` says *"any monotone Boolean query must be a Boolean threshold query"* **"(excluding the always-false one)"**. The doc dropped the parenthetical. `:745-747` propagates the same off-by-one. |
| P5 | `:203`,`:1193` vs `:215` | **The call table's necessity is corner-relative.** *"Two posts of the same shape still make **two** holes … It is why a call table is still needed"* — but `:215` records that under the **content** corner *"two holes above stops being true"*, and names that corner as *"what the measured corpus already does (24 ambient value names, zero fresh variables)"*. *Converged: consistency reader, rubric attacker, own read.* |
| P6 | `:1378` / `:1396` | **821 vs 823 real logs**, 18 lines apart, for what read as the same corpus. Neither describes what a log is, which repos, or over what window. The corpus is invoked as one object and is at least five (`:58` n=37, `:290` 200k rows, `:736` 25 stores, `:1890` 3,165, `:1895` 2,300). |
| P7 | `:1089` vs `:1039` | Running example converges at **400** in three places, **743** in one. |
| P8 | `:793` / `:970-971` | *"output append-only by hypothesis, working memory admitting deletion"* — verbatim twice. |
| P9 | `:211`, `:1579` | *"the claim's CAS"* never defined (only `send(expected_seq=)` at `:1819`); **"layer 7"** has no referent — the doc numbers no layers. |
| P10 | `:1935` | `send(expected_seq=)` is **consensus ∞** where single-spawn (mutual exclusion) is **consensus 2**. The doc has the fact in §Related and doesn't act on it; §"The honest cost" could claim a uniformly weaker transport. |

**Cross-reference hygiene is good:** all 59 `§"…"` references resolve (checked mechanically). Three point
at bolded run-in paragraphs rather than headings — harmless for a reader, invisible to a heading search.

---

## Part 4 — Process findings (the ledger)

The ledger discipline is applied rigorously to the ~15 works that have entries and **not at all** to the
~10 that do not, while `:20-24` tells the reader the opposite.

- **Belnap 1977 has no ledger entry.** Its five ledger hits are secondhand mentions inside *other*
  papers' entries. `:495-500` quotes him **twice verbatim with a section locator** plus an interpretive
  claim about his motive. Against `citations.md:5-6`: *"Nothing enters the doc from here until it is
  marked CONFIRMED."*
- **Also no entry:** Reiter (3 substantive claims, incl. *why* he needed the axiom), Vickers/Smyth (the
  affirmability route — which `:93-98` counts as one of two independent confirmations), Oz/Mozart (a
  verbatim protocol quote), CHR, Nix/Bazel/Shake (empirical claims about three real systems),
  Bellman–Ford, the three powerdomains, Hellerstein 2010.
- **An explicit instruction ignored:** `citations.md:403-404` — *"§5 unread — **restate it precisely
  before relying on it.**"* `:340` relies on it.
- **~11 strings in quotation marks that the ledger records differently or not at all** — see the citation
  auditor's table; the ones that matter are `:151`, `:155`, `:945-947`, `:965-966`, `:926-928`, `:110`.
- **Ledger hygiene:** Bagai & Sunderraman 1995 appears both as CONFIRMED (`:296`, *"Read in full
  2026-08-16"*) and under `## UNOBTAINED` (`:826-831`, *"Nothing about this paper is currently
  verified"*). The stale block was never struck.
- **Support the doc declined to take** (four of five, so the omissions do not favour the design): Bloom^L
  credit for the else-branch/upper-bound framing; Trân & Bagai as *published precedent* for dropping
  unary `∸`; Denecker's one-line multi-source contrast and tractable-corner result. **The one omission
  that does favour the design:** Darari's *"All completeness checks presented in this paper are
  NP-complete"* — the doc's coverage-as-entailment reading inherits the class and states no cost.

---

## Part 5 — Refuted; do not re-propose

- **"The design cannot express its flagship demand" / "a `∀S ∃V` quantifier form is missing."** It can:
  `:597-605` gives `¬(Q₀ ∖ E)` — *"everything in `Q₀` other than `E` is false"* — which settles the region
  correctly, and `:691` already concedes the exceptions go into the region description. What survives is
  P1 (the example uses the wrong region) and an uncosted growth in cover size.
- **"Equality in heads is excluded at `:324` then prescribed at `:1304`."** Not a contradiction. `:364`
  makes `eq/2` an *uninterpreted* posted relation, so `eq(...)` is an ordinary atomic head; `:324` bans
  *interpreted* equality. The table row wants a one-clause annotation, nothing more.
- **"`loss⁻` at `:303` is the partner-relation encoding §Polarity rejects."** `:309` and `:1456` both say
  `¬Q` is notation for `neg(Q)`. Readability nit.

---

## Part 6 — What survived contact

Recorded so a fix session does not "repair" working parts.

- **The bilattice truth-order paragraph (`:709-714`) is the sharpest in the document** and the arithmetic
  checks: `≤_t` on the four values has exactly **4** covering relations, of which **2** drop a negative,
  so *"half of `≤_t`'s covering steps run against permanence"* is literally true; and `{t}` is
  unreachable once anything false is told.
- **Fitting's conflation (`:777-783`)** computed correctly, and the reframing — declining CWA, holding
  `∅` unaffirmable, and needing no roster are **one** refusal, testable as *"does this reverse `≤_k`?"* —
  is a genuine result.
- **The frame-homomorphism counterexample for `→` (`:1271-1274`)** verified: `h(a→b)=0` while
  `h(a)→h(b)=1`. The semantic guardrail beats the syntactic one it replaces.
- ***Ex falso* is unformulable rather than blocked (`:485-490`)** — structurally paraconsistent without
  weakening entailment, correctly distinguished from Belnap's and 4QL's positions.
- **The `pol(A)` wrapper (`:1457`)** — one structural fact yielding three consequences, plus a wire
  argument that is the repo's static-checkability rule applied to a protocol field. *The design's
  best-executed primitive; Part 1's fix is "do this again for intent."*
- **The valuation census (`:1895-1903`)** — reports a clean zero, refuses the inference, diagnoses the
  structural reason, names the category (*inapplicable*), and self-reports its own first-pass error.
- **Epistemic scoping generally.** No unscoped novelty claims found; `:161`/`:170` are explicitly scoped;
  `:93-98` volunteers that two of its three routes are one fact; `:1101` volunteers that a whole framing
  is inert.

---

## Part 7 — Not covered by this pass

- The rubric attacker's findings 6–12 (delete the `∃` demand; the quantifier's encoding; the constraint
  domain as an undeclared global; the signature-global `Fact` sum; `flipped/2` enumerating a closed set)
  are **argued but unverified by me**. Several look strong.
- The fresh reader's structural recommendations (move §"Whose this already is" after §"The language";
  promote `:937-953` before §CALM; split §"The model" and §"The threshold rule"; a terms block; the
  glossary at `:1564-1571` moved to the front) are judgment, not defect — recorded, not adjudicated.
- **Findings D1–D5 and Part 1 are design work.** Whatever replaces them needs its own fresh eyes, in a
  session that has not read this file.
