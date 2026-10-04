<!--
PROVENANCE (not part of the review). Untracked file.
Fifth adversarial review, commissioned 2026-08-10, deliberately narrow: two independent lenses
(corpus demand-shape boundary; the claims derived unaided). A prototype-driven review was scoped out
and is expected to follow. Everything below is the reviewer's response, verbatim.
-->

# Adversarial review — `docs/backlog/if-built-today.md`, Lens A (demand shape) and Lens B (unaided claims)

## VERDICT

**Lens A: no defeater. Across all four consumer repos I could not find a case where a demand's *shape* must be computed at runtime.** Every candidate resolves into a name or a value in an argument position of a fixed shape. The strongest structural case — the TUI accepting a free-text metric name that has never been emitted — is defeated by measurement, not by argument: on **821 real logs, all 24 distinct value names carry exactly one sort** (21 `float`, 3 `dict`), so the metric plane is expressible as **two fixed shapes with the name as data**, and nothing forces a per-name functor. The closed-vocabulary boundary in §"The demand language" is in the right place.

But the finding has a sharp edge that points at a *different* section. The only thing that would promote those runtime names into shapes is the doc's own §"Types" rule banning the flat relation — and that ban's stated evidence, *"the measured case of one name carrying `None` under one flag and a nested record under another"* (`:488`), **does not reproduce on the corpus.** It exists in source (mycooc `analyze_run.py:1160-1179`), as a two-branch Option inside one function, which deploy-time codegen covers. So §"The demand language" survives partly because §"Types" is stronger than its evidence.

**Lens B: 5 confirmed, 2 need qualification, 0 refuted.** Item 7 — flagged as most likely wrong — is **the most solidly correct of the seven**, and its weakest-looking claim (the crown separation) is in fact *stronger* than the doc states: I found a realisation needing only `⌈log n⌉` argument positions, so the 2-vs-`n` blow-up is reachable at ordinary arity, not only in absurdly wide terms. Its one soft spot is the "≈2×" average, which is an ER asymptotic that I measured at **1.04–1.32** on both ER and the actual model at every size I could reach. The two qualified items are 1 (the circularity argument is sound but is the weaker of two available arguments, and the decisive one is missing) and 3 (the "characterisation" is of *functors*, not formulas; the classifying-topos correspondence is up to Morita equivalence and non-canonical in one direction).

---

# LENS A — is there any case where a demand's shape cannot be fixed before deployment?

**Answer: no.** Four repos searched; one qualified near-miss; the rest clean negatives.

### A.1 The decisive measurement: the sort *is* a function of the name. MEASURED.

The whole question turns on whether a metric name must become a functor. Under the doc's §"Types" rule it must, *if* value sorts vary per name; otherwise the flat relation is sound and the name is data.

Read through the public API only (`attach_channel` + `Channel.read`; `runstate.__file__` asserted in-process as `/home/gchurchill/src/runstate/runstate/__init__.py`), over 821 readable logs copied out of `mycooc/outputs/runs`:

```
logs read = 821   unreadable = 3
distinct value names = 24
names carrying MORE THAN ONE sort = 0
  status              794 runs   {dict: 126977}      mean_p1 … mean_entropy (21 names)  {float: …}
  config              794 runs   {dict:    794}      stage, percentile                  {float: …}
  completion_reason   367 runs   {dict:    403}      input_provenance                   {dict: 2}
names per run: min 0, median 21, max 23
names in the 2nd half of the corpus not seen in the 1st half: 0
```

So the entire measured value plane is **two** relations — `metric(Name, Float, Step)` and `event(Name, Json, Step)` — both fixed shapes, name as data. The doc's own objection to the flat relation (`:482-485`, *"`value(loss, X, S)` and `value(converged, X, S)` may share `X`. That aliases a float slot to a bool slot"*) never arises, because the float and non-float partitions are separated by *relation*, not by name.

### A.2 The strongest structural candidate, and why it fails

`runstate-tui` is the textbook Lens A shape: a third-party observer of logs written by programs its deployer did not write, with a free-text metric entry. Verified by reading:

- `runstate/vocabulary/payloads.py:23-28` — *"The `name` axis stays open/app-owned (the consumer's concern, not the protocol's)."* The namespace is open **by protocol declaration**.
- `runstate-tui/docs/backlog/interactive-objective.md:89-91` — *"free text stays permanently, for a **structural reason rather than a contingent one**: **no enumeration can name a metric that has not been emitted yet**, and naming one before it appears is a real want."*
- `runstate-tui/docs/backlog/metric-discovery.md:29-32` — the runtime-discovered set *"is a lower bound"*, to be labelled *"seen so far (partial)"*.
- `runstate-tui/docs/backlog/per-run-objective.md:4-6` — one table can hold run classes reporting **different** metrics simultaneously.
- `runstate-tui/runstate_tui/types.py:147` — `value: tuple[str, object, int | None]  # (name, scalar, step)`, i.e. `value(Name, V, Step)` verbatim.
- `runstate/observables.py:525` — `value_series(channel) -> dict[str, dict[int, Any]]`, whose docstring makes the openness explicit: *"name enumeration = `.keys()`"*.

**Why this is not a shape defeater.** The TUI demands `value(Name, V, Step)` and supplies `Name` as data. A.1 shows `V`'s sort is determined for every name in the corpus, and the two-relation split is decidable by the *writer*, whose program declares it. The residue — that the TUI must *render* topics it was not compiled against (`detail.py:124-127`: an unknown family *"always shows"*) — is a pass-through display rule, not a demand. Rendering an unknown functor is not querying one.

### A.3 The other three repos

- **mycooc — clean negative.** The entire runstate write surface is string literals plus one computed name `f"{agg_prefix}_{metric_name.value}"` (`training.py:1020-1022`) whose `metric_name` ranges over a closed 15-member `StrEnum` (`config.py:9-27`): ≤51 names, all enumerable from two files. Hydra `_target_` exists but cannot widen the runstate vocabulary — metric names are literals in `training.py`, and the aligner only changes numbers. `--metrics` free text (`run_experiment.py:195`) is dead code with no consumer.
- **translation — clean negative.** Six literal carrier names (`translation`, `refs`, `index`, `hyp`, `estimate`, `ignition`); no computed `name=` outside test fixtures. The Hydra layer is closed by construction: `conf/factory.py:14-45` matches over a four-member dataclass union terminating in `assert_never`. No `eval`/`exec`/`importlib`/`entry_points`. The heterogeneous `ignition` record (float + bool + variable-length list + a branch-only `Verdict` dataclass with `ndarray` fields) is gated on a `consensus: bool = False` kwarg — two static branches, two codegen'd record sorts.
- **learn-and-teach — the one qualified near-miss.** `analysis/run_metrics.py:2946-2948, 2976-2977` put `m` in the metric position where `m` comes from `curriculum.eval.intrinsic`, and `curriculum` is *"a COMPILE-TIME dep expected as a sibling checkout at `../curriculum`"* (`requirements.txt:16-17`), put on `PYTHONPATH` by filesystem path at invocation (`Makefile:17-18`). `analysis/toc_metrics.py:59-71` is written to forward names it has never seen. **Why it still fails:** the names are literals in *curriculum's* source, so codegen given both trees enumerates them; and the value sort is uniformly float, so `m` sits in a key position of a fixed shape. What is impossible is codegen *from the consumer's own configuration* — a weaker claim than the lens asks.

> **Corrected statement — and it targets §"Types", not §"The demand language".** `:650-654`'s boundary (*"the vocabulary should stay closed"*) is **confirmed**: nothing in four consumer repos needs a shape computed at runtime. But `:488`'s justification for the rule that would break it — *"the measured case of one name carrying `None` under one flag and a nested record under another"* — is not reproduced by measurement. On 821 logs, **0 of 24 names carry more than one sort**, and the namespace does not drift (0 new names in the corpus's second half). The case exists in source at `mycooc/analyze_run.py:1160-1179` (`permutation = None` on one branch, `{"is_transport": …, "results": …}` on another, both emitted at `:1392` as `topic="value", name=k`) — but it is an **Option sort declared in one function**, which is exactly what deploy-time codegen emits. The sentence should read *"the source case of one name carrying `None` under one flag and a nested record under another"*, or cite the measurement that produced it. As written it is the doc's only load-bearing evidence for a ban that, if enforced literally, would turn `--objective NAME` into an uncompiled functor and defeat the very boundary `:650-654` defends.

---

# LENS B — the claims derived unaided

## B7 — greedy merging ≡ first-fit colouring of the complement. **CONFIRMED, and stronger than stated.** (Checked first, as instructed.)

**The load-bearing step first.** `:96-99`: *"For **linear** terms — no repeated variables — a term is a partial assignment, two terms merge iff they agree where both are defined, and consistency is **pairwise**."*

MEASURED, with real unification (occurs check, variable-disjoint terms), over flat and nested random linear terms, all pairwise-compatible subsets of size 3–5:

| generator | pairwise-compatible groups | jointly inconsistent |
|---|---|---|
| linear flat d=4 m=3 | 2,990 | **0** |
| linear flat d=6 m=2 | 2,569 | **0** |
| linear nested d=3 m=3 | 1,585 | **0** |
| non-linear flat d=4 m=3 | 863 | 268 |
| non-linear flat d=6 m=2 | 678 | 178 |

7,144 linear witnesses, zero violations; the doc's own non-linear caveat reproduces (`f(X,X), f(a,Y), f(Z,b)`: pairwise `[True,True,True]`, joint `False`). The step is also provable, which is why it holds: a variable-disjoint linear term is exactly a partial function from paths to symbols, and a union of partial functions is a function iff they are pairwise compatible.

**The step the doc does *not* state, which the reduction actually needs.** "First blob it fits" tests the new term against the blob's **merged term**, whereas first-fit colouring tests it against **every member**. These must coincide or the reduction is false. MEASURED: 26,612 linear (blob, term) pairs, **0 disagreements**; 6,441 non-linear pairs, **321 disagreements**. It coincides for linear terms because the merge's domain is the union of the members' domains and its values are inherited — so the identification is sound, but it is a second lemma, not a restatement of the first.

**Best / worst / average.**

- **best = `χ(Ḡ)`**, `:106`. CONFIRMED — some ordering always makes first-fit optimal (order by an optimal colour class), and E3 exhibits it: the crown at `n=10` gives 10 blobs interleaved and **2** blocked.
- **"NP-hard to find"**, `:106`. CONFIRMED by construction: **every** graph is the conflict graph of linear terms — one position per edge, `u ↦ 0`, `v ↦ 1`, wildcards elsewhere. MEASURED 40/40 random graphs realised exactly. So the optimum is graph colouring. The reduction costs `Θ(n²)` positions; see the next point for why that is not the constraint it looks like.
- **worst = `Γ(Ḡ)`**, `:107`. CONFIRMED, definitionally (the Grundy number *is* the max colours greedy can be made to use) and corroborated in the literature for the crown.
- **average ≈ 2×**, `:108`. **NEEDS QUALIFICATION** — see below.

**The crown, `:110-112`.** CONFIRMED — and I found a construction the doc does not have. The doc's phrasing suggests a wide term; the naive realisation (`u_i = f(_,…,1@i,…,_)`, `v_j = f(0,…,_@j,…,0)`) needs arity `n`, which would have made the blow-up an artefact of absurd term width. It does not: take an antichain `T_1…T_n ⊆ [k]` (Sperner, `k ≈ log₂ n`), set `u_i = a` on `T_i` and `v_i = b` off `T_i`. MEASURED:

| positions `k` | pairs `n` | is crown | `χ` | first-fit interleaved | first-fit blocked |
|---|---|---|---|---|---|
| 4 | 6 | yes | 2 | 6 | 2 |
| 8 | 70 | yes | 2 | 70 | 2 |
| 10 | 252 | yes | 2 | **252** | 2 |
| 12 | 924 | yes | 2 | **924** | 2 |

A **12-ary functor** suffices for a 2-vs-924 gap. `:110`'s *"The gap is not a constant factor"* is right, and right for a better reason than the doc gives.

**The average is where it bends.** `:108` cites the correct literature (Grimmett–McDiarmid 1975 for greedy `~n/log_b n`; Bollobás 1988 for `χ ~ n/(2 log_b n)`) — those are asymptotics for **Erdős–Rényi** `G(n,p)`, and they converge glacially. MEASURED, with exact `χ` by branch-and-bound at small `n` and best-of-iterated-greedy (an upper bound on `χ`, so the ratio is a *lower* bound) at large `n`:

| instance | ratio first-fit / optimum |
|---|---|
| ER `G(n,½)`, exact `χ`, n=12 / 16 / 20 / 24 | 1.084 / 1.142 / 1.173 / 1.199 |
| ER `G(n,½)`, heuristic `χ`, n=30 / 120 / 500 | 1.267 / 1.283 / 1.206 |
| **conflict graphs of random linear terms**, exact `χ`, n=20–30, `d`=2–12, `m`=2–4 | **1.040 – 1.150** |
| same, heuristic `χ`, n=60–500 | 1.162 – 1.321 |

> **Corrected statement.** `:96-101` is **confirmed** — pairwise consistency implies joint consistency for linear terms (7,144 witnesses, 0 violations), and greedy merging really is first-fit colouring of `Ḡ`, though that needs a second lemma the doc omits: *compatible with the blob's merged term ⟺ compatible with every member*, verified 26,612/0 for linear terms and false 321/6,441 for non-linear. `:106-107` (best `χ(Ḡ)`, NP-hard; worst `Γ(Ḡ)`) are confirmed, the hardness by an explicit realisation of every graph as a conflict graph of linear terms. `:110-112` is confirmed **and understated**: the crown separation needs only `⌈log₂ n⌉` argument positions, not `n` — measured, a 12-position functor gives `χ = 2` against **924** first-fit blobs. `:108`'s *"≈ **2×** optimum on random instances"* should read *"→ 2× asymptotically on Erdős–Rényi instances"*: the 2 is the ratio of leading terms, and measured at every size reachable the ratio is **1.04–1.20 at n ≤ 24 (exact `χ`) and 1.21–1.32 at n ≤ 500**, on ER *and* on conflict graphs of random linear terms alike. The "average" row and the crown row are therefore not the same kind of statement — the crown is a real separation at realistic arity; the 2× is an asymptote nothing in this corpus approaches.

## B1 — the `∨`-over-given-families / circularity argument. **NEEDS QUALIFICATION.**

`:670-674`: *"That definition needs a **comprehension** — the family is carved out by a condition — whereas geometric logic's arbitrary `∨` ranges over a **given** index family, never one a predicate selects. Nor can the condition be smuggled in as a conjunct instead: `c ∧ a ≤ b` is entailment, and internalising entailment as a formula *is* implication. The bootstrap is circular."*

**Both halves are sound.** Geometric syntax admits `⋁_{i∈I} φ_i` for a set `I` fixed in the metatheory; there is no term-former building an index family by an internal predicate over opens. And the dodge is genuinely circular: in a frame, `c ≤ b ⟺ (c → b) = ⊤`, so a formula whose truth value is the entailment is interdefinable with `→` given the arbitrary `∨` the design already has. One small imprecision: comprehension alone is not sufficient; you need comprehension **plus** a join ranging over the comprehended family. In *this* design `∨` is arbitrary, so the doc's *"one line away"* (`:678`) is correct — but it is correct contingently, not structurally.

**The real problem is that this is the weaker of two available arguments, and the doc chose it.** The syntactic argument says *the current syntax has no such former*. That is a statement about a syntax, and it makes the guardrail exactly as strong as nobody adding a family-former — which is why the doc has to concede *"the guardrail stays syntactic"*. The decisive argument is semantic and is the same invariance the rest of the doc rests on: **frame homomorphisms preserve finite `∧` and arbitrary `∨` but not `→`.** Only `h(a → b) ≤ h(a) → h(b)` holds, and it is strict. Witness, checked by hand: `L = O(ℝ)`, `M = 2`, `h` = the point `0` (a frame homomorphism); `a = ℝ∖{0}`, `b = ∅`. Then `a → b = int({0}) = ∅`, so `h(a→b) = ⊥`; but `h(a) = ⊥` and `h(b) = ⊥`, so `h(a) → h(b) = ⊤`. Since every geometric formula's interpretation *is* preserved, **no geometric formula can define `→` uniformly** — no survey of dodges required.

This also sharpens `:678-680` correctly. A user who hand-enumerates a finite family and disjoins it has written a geometric formula that *happens* to equal `a → b` in one frame and **stops equalling it after base change**. The right slogan is not "`→` is unwritable" but "`→` is not *uniformly* definable".

> **Corrected statement.** `:670-674` → the circularity argument is sound, with one repair (comprehension gives `→` only in the presence of the arbitrary `∨`, which this design supplies). But it establishes the conclusion by exhausting two dodges, and the conclusion follows in one step from the invariance the doc already owns: *frame homomorphisms preserve finite `∧` and arbitrary `∨` but not `→` — `h(a→b) ≤ h(a)→h(b)` is strict, e.g. `O(ℝ) → 2` at the point `0` with `a = ℝ∖{0}`, `b = ∅`, where `h(a→b) = ⊥` and `h(a)→h(b) = ⊤`. Every geometric formula is preserved; `→` is not; therefore no geometric formula defines it.* That upgrades `:674`'s *"the guardrail stays syntactic"* from a concession to a choice: a semantic guardrail is available and is the one the design should state.

## B2 — geometric logic is essentially the *positive* fragment. **CONFIRMED, with a qualification that matters.**

`:576-578`. The connective assignment is standard: in polarised/focused sequent calculi (Andreoli; Liang–Miller LJF) `∧⁺`, `∨`, `∃`, `⊤⁺`, `⊥` are positive and `⊃`, `∀`, `∧⁻` negative, with `¬A = A ⊃ ⊥` therefore negative. Geometric formulas are exactly atoms closed under `⊤`, `⊥`, finite `∧`, arbitrary `⋁`, `∃`, `=` — the infinitary positive fragment; the literature calls finitary positive logic *"the finitary part of geometric logic"*. The companion claim *"a fact is data where a demand is a continuation"* is the standard reading of polarity (positive = values/data, negative = computations/continuations).

Two qualifications, one cosmetic and one substantive.

- **Cosmetic:** a geometric *theory* is axiomatised by sequents `∀x̄(φ ⊢ ψ)`, whose outer structure is `∀` and `→` — negative. So the positive-fragment claim is about *formulas*; the axioms are one negative shell around positive cores. The doc knows this at `:379-381` (*"The `∀` and `→` live at the sequent level"*) but not here.
- **Substantive: polarity does not predict the two restrictions the design actually depends on.** Focusing makes `∧⁺` positive with no cardinality condition, so nothing in polarity explains why `∧` must be **finite** while `∨` may be **arbitrary**. That asymmetry comes from left-exactness — inverse images preserve *finite* limits and *all* colimits — which is a different fact wearing the same word. Under the categorical heuristic the doc leans on elsewhere (positive = colimit, negative = limit), `∧` is a *limit* and would read as negative.

> **Corrected statement.** `:576-577` → *"geometric logic is essentially the positive fragment"* is right as a listing of connectives and right about the data/continuation reading, but it should not be asked to carry the design's content. Polarity assigns `∧`, `∨`, `∃` positive and `→`, `∀`, `¬` negative; it says nothing about why `∧` is restricted to **finite** conjunction while `∨` is arbitrary. That asymmetry is left-exactness (finite limits, all colimits), not polarity — and it is the asymmetry `:331`'s frame-distributivity row and `:302`'s "finite `∧`, arbitrary `∨`" actually rely on.

## B3 — characterisation by preservation; geometric theories ↔ classifying toposes. **NEEDS QUALIFICATION (both halves).**

`:663-665`: *"Geometric logic is *characterised* by the preservation property rather than chosen and found convenient, and geometric theories are exactly those with classifying toposes."*

**First half.** The forward direction is a theorem: inverse image functors preserve finite limits and all colimits, hence preserve the interpretation of geometric formulas and carry models to models. The *characterisation* in the literature is of **functors**, not formulas — nLab: *"a functor between Grothendieck topoi is geometric … iff it preserves finite limits and small colimits."* A converse at the level of formulas ("every formula preserved by all inverse images is geometric") can only hold **up to logical equivalence**, and trivially fails read syntactically: `¬⊥` is not a geometric formula and is preserved by everything. The rigorous analogue is a Lyndon/homomorphism-style preservation theorem, which is exactly an up-to-equivalence statement.

**Second half.** Every geometric theory (over a small signature) has a classifying topos, and every Grothendieck topos is the classifying topos of *some* geometric theory — but the map is **not a bijection**: theories with equivalent classifying toposes are precisely the **Morita-equivalent** ones, and the topos→theory direction is explicitly non-canonical (nLab: *"every Grothendieck topos is the classifying topos of some theory"*; Caramello: *"although not canonically"*, and *"in fact, of infinitely many theories"*). There is also a direction the phrasing hides: a non-geometric first-order theory can be Morleyised into a coherent theory with the same **Set**-models but different models in a general topos. So *"having a classifying topos"* is a property of a theory **qua topos-indexed model functor**, not of its set-models — which is the sense that makes the doc's use of it legitimate, and is worth saying.

> **Corrected statement.** `:663-665` → *"Geometric logic is **characterised** by the preservation property"* holds as a theorem about **functors** (a functor between Grothendieck toposes is geometric iff it preserves finite limits and small colimits) and as a **theorem in the forward direction only** about formulas (geometric formulas are preserved by inverse images). Any converse at the formula level is up to logical equivalence — `¬⊥` is preserved and is not geometric. And *"geometric theories are exactly those with classifying toposes"* is a **surjection up to Morita equivalence, non-canonical in one direction**: every geometric theory has a classifying topos, every Grothendieck topos classifies *some* geometric theory (in fact infinitely many), and two theories share one exactly when they are Morita-equivalent. Neither correction damages the use the doc makes of them — *"step outside and both characterisations fail at once"* (`:665-666`) survives, because stepping outside breaks the preservation *theorem*, which is the half that is unconditional.

## B4 — inverse images do not preserve exponentials. **CONFIRMED, with one word to change.**

`:659-661`. Constructed counterexample (by hand; the calculation is elementary and checkable): let `X = {0} ∪ {1/n : n ∈ ℕ} ⊂ ℝ` and `Δ : Set → Sh(X)` the inverse image of the unique geometric morphism to `Set`. Since `Δ` preserves colimits, `Δ(B) = ∐_B 1`, so
`Δ(A)^{Δ(B)}(U) = Hom_{Sh(U)}(∐_B 1, Δ(A)) = LC(U, A)^B`, while `Δ(A^B)(U) = LC(U, A^B)`.
Take `A = 2`, `B = ℕ`, `U = X`, and `f_n = ` the indicator of the isolated point `1/n`. Each `f_n` is locally constant, so `(f_n)_n ∈ LC(X,2)^ℕ`; but `g(x) = (f_n(x))_n` sends `0 ↦ 0̄` and `1/k ↦ e_k`, so `g` is constant on no neighbourhood of `0` and `(f_n) ∉ LC(X, 2^ℕ)`. The canonical `Δ(A^B) → Δ(A)^{Δ(B)}` is therefore not iso. (Note the failure needs an infinite exponent; for finite `B` it holds. And for locally connected `X` it holds for all `B` — which is why *"the inverse image part defines an exponential ideal"* is a studied, non-automatic condition, e.g. arXiv:2105.10143.)

**The word to change is "base change."** In topos theory "base change" most often names the pullback `f^* : E/Y → E/X` along a morphism — and *that* does preserve exponentials, since toposes are locally cartesian closed and pullback functors between slices are cartesian closed. The failure is specific to the **inverse image of a geometric morphism**.

> **Corrected statement.** `:659-661` → *"inverse images of geometric morphisms preserve finite limits and all colimits but **not** exponentials"* is confirmed (witness: `Δ : Set → Sh(X)` for `X` a convergent sequence, `Δ(2^ℕ) ≇ Δ(2)^{Δ(ℕ)}`) — but *"so an exponential does not survive base change"* should read **"need not survive the inverse image of a geometric morphism."** Base change in the slice sense (`f^* : E/Y → E/X`) *does* preserve exponentials, toposes being locally cartesian closed; and the inverse image case is not universal either — it holds for finite exponents, and for all exponents when the morphism is locally connected. The conclusion `:661` (*"'Geometric logic with exponentials' … is not geometric"*) stands regardless, because it needs only that preservation **fails in general**.

## B5 — exponentials + `Ω` give power objects give comprehension. **CONFIRMED, essentially by definition.**

`:658-659`. A category with finite limits, exponentials, and a subobject classifier **is** an elementary topos (that is one of the standard definitions); every topos has power objects `P(A) ≅ Ω^A`, with `Ω = P(1)`; and *"the existence of a subobject classifier can be construed as a comprehension principle, hence any topos satisfies the axiom of comprehension"*. Two clarifications that do not weaken the claim: finite limits are needed alongside the two ingredients named, and the comprehension delivered is separation (a subobject of an existing object), which is exactly the form `{c ∈ L : c ∧ a ≤ b}` requires. The chain the doc needs — exponentials + `Ω` ⟹ internal families of opens carved out by a predicate ⟹ `a → b = ⋁{c : …}` ⟹ `¬a = a → ⊥` — is intact.

## B6 — finite partial terms are exactly the compact elements; `↑c` is Scott-open; thresholds are a *basis*. **CONFIRMED.**

`:569-570` and `:300-302`. All three links are standard domain theory:
- In an algebraic dcpo, an element is compact **iff** its principal filter `↑c` is Scott-open (`↑c` is upward closed, and compactness is exactly the directed-sup condition an open needs).
- `B` is a basis for a dcpo iff `{↑b : b ∈ B}` is a basis for the Scott topology; an algebraic dcpo has a smallest basis, its compact elements.
- The term domain — finite partial terms under instantiation, ideal-completed with the infinite/rational terms — is algebraic with the finite partial terms as its compacts. (This holds for the standard tree reading and for the ideal completion of terms-with-shared-variables alike, since the compacts of `Idl(P)` are `P`.)

So *"matching a pattern is asking a basic open"* is exact, and *"the restriction is a **basis**, not a limit"* (`:302`) is the correct word.

Two qualifications, and the doc already carries both elsewhere, so this is a note about where they live rather than a correction:
- **Dense carriers.** `:533` says it: on a dense carrier the only compact element is `⊥`, so `↑c` is not a basis and the affirmable claim becomes strict `⊐`. The flat statement at `:569-570` should point at that row, since `Float` is the design's most common value sort (and is discrete, so it is fine — but a real-valued or interval carrier is not).
- **CLP demands are not all basic opens.** Under §"The demand language" a demand may carry constraints. An upward-closed decidable constraint yields a union of basic opens — still open, so the basis framing holds. **Disequality does not**: `X ≠ Y` is not upward closed (two unbound variables may yet be identified), so it denotes no open at all. `:642-643` states exactly this restriction; the duality bullet should not be read as saying every CLP demand is a `↑p`.

---

## What I could not break

- **B7's pairwise step**, the one flagged as most likely wrong: 7,144 linear witnesses, 0 violations, plus a proof. The non-linear counterexample the doc supplies is real and reproduces.
- **B7's crown**: not only correct but achievable at `k = ⌈log₂ n⌉` positions — I tried to break it by arguing the blow-up needed absurd term width and the construction refuted me.
- **B5**: definitional; nothing to break.
- **B6**: standard, and the doc's two exceptions (dense carriers, disequality) are already in the doc.
- **Lens A**: four repos, ~180 files read between the surveys; no shape-constructing consumer. `mycooc` and `translation` are clean negatives with closed vocabularies enforced by `StrEnum` and `assert_never` respectively.

---

## Environment and hygiene

Python 3.12.9, stdlib only, plus the real package — `runstate.__file__` asserted in-process as `/home/gchurchill/src/runstate/runstate/__init__.py` in every script that imports it. Web search/fetch for the topos-theory and random-graph literature. SWI-Prolog and XSB were **not** used — Python's own unifier was sufficient and let me instrument the merge. **No Postgres was started**: `/tmp/rs-pg` does not exist on this machine, port 55432 was never contacted, no `psql`/`pg_ctl` was invoked.

**Nothing was edited.** No file under `docs/` was touched in any repo. `git status --porcelain` at session end matches session start exactly. I only read from the consumer repos and `cp`'d `.db` files **out** to scratchpad; nothing was written into any consumer repo. No `git add`, `commit`, `stash` or `checkout` anywhere. **Nothing is left running.**

**Prototypes**, all in the session scratchpad:

| file | what it does |
|---|---|
| `b7_merge.py` | E1–E6: real unification; pairwise⟹joint for linear vs non-linear; merge-vs-members; crown realisation + first-fit; universality of conflict graphs; exact `χ` by branch-and-bound; ratio sweeps |
| `b7_crown_narrow.py` | the Sperner-antichain crown: `χ = 2` vs `n` first-fit blobs at `k ≈ log₂ n` positions |
| `b7_scale.py` | first-fit / best-found colouring at n = 30…500 on ER and on term-conflict graphs |
| `lensA_sorts.py` | per-name value-sort census over the corpus through the public API |
| `e5.out`, `e6.out`, `scale.out`, `lensA.out` | their outputs |

**Sources** (Lens B literature): nLab on geometric logic / geometric theory / geometric morphism / subobject classifier; Caramello, *Grothendieck toposes as unifying 'bridges'*; arXiv:2105.10143 on exponential ideals; Kang & McDiarmid, *Colouring random graphs*; Bollobás, *The chromatic number of random graphs*; Grundy number (HandWiki); de Jong, *Apartness, sharp elements, and the Scott topology of domains*; Barrett, *Elementary topoi*; Vickers, *Continuity and geometric logic*.
