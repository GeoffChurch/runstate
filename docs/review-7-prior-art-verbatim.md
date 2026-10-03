<!--
PROVENANCE (not part of the review). Untracked file.
Seventh review, commissioned 2026-08-11: prior art and convergence. Not adversarial — the question was
"has somebody already built this, and how close did they get?"
Assembled from two messages: the main body (verdict, near-neighbours, questions 1-4, the two lists),
and a final addendum correcting its own answer to question 5 (the sheaf material), which is placed
last here. Everything below is the reviewer's response, verbatim.
-->

# Prior-art convergence review: `if-built-today.md`

## 1. Verdict

**Yes. Something has converged, and the doc cites none of it.**

The closest existing thing is not one system but a **lineage**: Hellerstein's Berkeley group, 2010–2026. Across Dedalus → Bloom → Bloom^L → Blazes → Edelweiss → Hydro → *Free Termination* → *Complete CALM*, that group has independently built or proved **most of this design's load-bearing parts**, including two the doc presents as its own reasoning.

**(a) `closed(Q,p)` is a punctuation.** Tucker, Maier, Sheard & Fegaras, *"Exploiting Punctuation Semantics in Continuous Data Streams"*, TKDE 2003 — **READ (delegate)**:

> "*a punctuation indicates no more tuples will follow that match the punctuation… We instead represent punctuations as **data*** [to allow their easy storage, searching, and manipulation]."

Scoped by pattern, emitted by a named producer, propagating through operators — **and the doc's two-level structure is theirs too**: "*When punctuations from **all sensors** for a particular hour have been received by union, we know there will be no reports from any sensor for that hour.*" Blazes (ICDE 2014, arXiv:1309.3324) carries it into the CALM world as **sealing**; the Dedalus confluence TR has the literal predicate `p_done()` with **Lemma 5 named "Sealing."** Maier co-authored both the punctuation paper and Bloom^L.

**(b) "Threshold claims always; exact claims only at settledness" is a theorem, not a discipline.** Power, Koutris & Hellerstein, *"The Free Termination Property of Queries Over Time"*, ICDT 2025 / arXiv:2502.00222 — **READ (me, full text)** — asks the doc's exact question:

> "*in the absence of coordination, what query properties allow nodes to unilaterally terminate execution even though they may receive additional data in the future? This **completeness** question is complementary to the soundness questions studied in the CALM literature.*"

- **Prop. 13/14**: in an inflationary system, any query with a free-termination state is essentially a **threshold query above an antichain**.
- **Prop. 9**: free termination exactly at **maximal elements** — the doc's "exactness is available precisely where it is meaningful: at maximal elements."
- **Thm 24**: positively coordination-free ⟺ **monotone**; negatively ⟺ **antitone** — the doc's derivation/report, open/closed, affirm/refute split.
- **Thm 18 + Cor. 20 ("the inverse curse theorem")**: if updates form a group, **no non-constant query has a free-termination state** — "*in view maintenance, which often studies rings rather than semirings, free termination is impossible… the benefits of these properties appear mutually exclusive.*"

Their related-work paragraph names the family the design belongs to — "*several systems have combined semilattice state convergence with monotonic queries… including Bloom^L, Lasp, Datafun and Hydroflow*" — and the gap it is trying to fill: "*Efforts at adding non-monotonic functions or queries to these languages have typically **fallen back to the use of coordination**.*"

**The genuine novelty is narrower and sharper than the doc claims, and lives in five places:**

- **Refusing the value layer.** Every durable-fact system in this space — Datomic, XTDB, Datahike, compacted Kafka, quad stores — is a monotone log **with last-write-wins on top**. The design keeps the log and deletes that layer. "Monotone store" does not communicate this; it is the crisp, locatable contribution.
- **`conflicted(K)` as an affirmable, derivable predicate.** LVars has this exact state — it is `⊤`, "*the 'error' state that results from conflicting updates*" — and **crashes**. Bloom^L names the set-collecting merge in footnote 5 and calls it a fallback. Nobody makes non-joinability a first-class monotone predicate in the rule language.
- **Demand-gating with symmetric producer/querier roles.** Absent from the entire CALM/lattice/durable-fact cluster.
- **Closure statements that cannot go stale**, because `Q` names a content-addressed immutable computation.
- **The synthesis itself.** The CCP/constraint lineage and the lattice-variable/CALM lineage independently reinvented "monotone store + blocking threshold read" and **never cited each other** — Kuper's dissertation covers Bloom^L, FlowPools, DPJ and separation logic, and does not mention Saraswat, `ask`/`tell`, or Oz at all. Joining them is not a re-tread.

**Four independent threads converged on Dar et al. 1996 for the residual; two on punctuations for `closed`.** Neither is novel, and both carry decades of theory the design can inherit rather than re-derive.

---

## 2. Ranked near-neighbours

### 1. Bloom^L (and Bloom / Blazes / Edelweiss) — the closest whole system

**How close.** Datalog + lattices + CALM; `<=`/`<+` merge operators; **no deletion for lattices**; a monotone/morphism distinction the compiler uses for whole-program CALM analysis. Its `lbool.when_true` and `lmax.gt_eq(n)` **are** threshold reads. Footnote 2 is the doc's own rule, published 2012 — **READ (me)**:

> "*Observe that an "else" clause would test for an upper bound on the final lattice value, which is a **non-monotonic** property!*"

That is verbatim "you may test that instantiation has **reached** a threshold; you may never test that it has not," and "a rule whose body fails to match must simply not fire — never take an else-branch."

Its shopping-cart study is a posted completeness declaration — **READ (me)**: the checkout carries `lbound`, "*the smallest operation ID that must be reflected in the result*", then "*An `lcart` is complete if it contains a checkout operation as well as all the actions in the ID range identified by the checkout. Hence, testing whether an `lcart` is complete is a monotone function… if any server replica determines that it has a complete cart, it can send a response to the client without risking inconsistency.*" A declared extent turning a non-monotone aggregate into a monotone threshold test, coordination-free.

**What it solved that this design has not.**
- **Blazes prices the second fact.** For multi-producer partitions — **READ (me)**: "*When a reporting server has received seal messages from all producers for a given campaign, it emits the partition… The reporting servers use **Zookeeper** only to determine the set of ad servers responsible for each campaign.*" So "no further producer will appear for Q" **needs an authority**, at one Zookeeper call per scope. The doc leaves it honour-system; that is defensible but should say why it declines a bounded, cheap alternative.
- **Blazes classifies sealing as *coordination*** — **READ (delegate)**: "*a coordination strategy based on point-to-point communication between producers and consumers — called **sealing***", and "*two alternative coordination strategies, **ordering and sealing***." The doc's "posting it is the toggle" is Blazes' argument for why sealing beats ordering; it is **not** an argument that no coordination occurs. Two inherited obligations: liveness (a crashed producer never seals, and no monotone observation distinguishes dead from slow) and **seal compatibility**, which Blazes discharges with an FD analysis. The doc has no answer to "when does `closed(Q,p)` discharge demand `Q'`?" beyond subsumption.
- **Edelweiss automates reclamation** — the doc's open problem — **READ (me)**. It is a Bloom sublanguage for "*Event Log Exchange… based on immutable state and messaging that sidesteps traditional challenges in distributed consistency, **at the expense of introducing new challenges in designing space reclamation protocols***", and it *generates* the GC by program analysis. It has a **`sealed` collection type** whose runtime auto-emits punctuations, and the clean definition: "*A punctuation is a guarantee that no more tuples matching a predicate will appear in a collection.*"
- A **compiler-checked** monotonicity analysis over whole programs.

**What this design has that Bloom^L does not.** Terms and unification (Bloom^L is relational, and "*lattice elements cannot be used as keys*"); **demand** (zero occurrences in the paper — I grepped); `conflicted`; per-functor scoped closure with contradiction detection; read-side orders.

### 2. Punctuations / stream sealing — `closed(Q,p)`, 23 years old

Covered above. Adjacent: **timely dataflow frontiers/capabilities** are the same idea with a total order (a low-water-mark rather than an arbitrary predicate); **Beam/Flink watermarks** are the heuristic version, where late data is policy rather than a detectable contradiction. The doc's "a producer appearing afterwards is a plain contradiction the store can detect" is the strictness punctuations have and watermarks give up.

**What punctuations solved that the design has not:** operator-by-operator **propagation rules** (a join emits its own punctuation once both inputs are punctuated), plus the known failure mode — a punctuation that never arrives blocks forever. The doc's `spawns(P,Q)` transitive-closure idea is that problem and gets one paragraph.

### 3. Free Termination (ICDT 2025) — the doc's rules, proved

Covered in the verdict. It is not a *system* but it is the closest **theory**, and it postdates every citation in the doc.

### 4. Concurrent constraint programming (Saraswat) and Oz / Mozart — the semantics, already built

CCP *is* this model. `ask` suspends on undecided entailment as **the rule**, not an optimisation; the determinate fragment has exactly the doc's determinacy property. The doc says "CCP is the right model for the semantics" and cites nothing. **READ (delegate)**, Valencia BRICS RS-01-20: "*Whenever a process asks some information not yet entailed by the current store, it blocks, and remains blocked until some other process adds (tells) the requested information to the store.*"

Scope caveat: CCP as published is *not* propagate-only — guarded choice, atomic tell, and angelic nondeterminism are all in the family. The design is **the determinate fragment of CCP with eventual/monotone tell**.

**Three things CCP/Oz solved that the doc has not** — **READ (delegate)**:

- **Oz already has demand-as-unsatisfied-existential, implemented.** TOPLAS 1999: "*The consumer asks for an element by binding the stream's tail to a pair of a logic variable and a new tail. The producer waits until this pair exists and then binds the logic variable to the next element*", plus `ByNeed`/`WaitNeeded` for by-need computation (Mehl/Schulte/Smolka 1998).
- **Oz's distributed binding protocol is the coordination the doc's CALM argument does not cover.** "*Binding is harder: it requires cooperation between sites… one site the "owner" of the variable… **The owner accepts the first binding request and ignores all subsequent binding requests.***" Set-union of *ground* atoms is coordination-free. But the doc keeps free variables in posted terms and has agents post by two-way unification. Two agents racing to bind a shared `V` is exactly Oz's situation. **This is the single most consequential gap found** — the doc must say which it is.
- **Computation spaces are a principled alternative to banning labelling**: speculative work runs in a nested store, merged upward only on success, so the parent stays monotone. "*The space is **stable**… no additional bindings done in an ancestor can make the space runnable*"; "*The space is **merged**… its constraint store has been added to its parent.*" Note also that Oz's *stability* is explicitly relative-to-an-ancestor — CP already found that "no more propagation" is only meaningful relative to a declared scope, which is the doc's per-functor scoping.

Also: CCP already has "agent = closure operator on a complete algebraic lattice", and a **hyperdoctrine** denotational semantics (Panangaden/Saraswat/Scott/Seely) — so the categorical-logic story is not new either.

### 5. LVars / LVish — threshold reads, `⊤`, and a theorem against `closed`

**The doc does not cite LVars at all** — I grepped `LVar|LVish|Kuper`: zero hits, despite "there is no `freeze`" implying awareness.

- **`conflicted` is LVars' `⊤` without the crash** — **READ (delegate)**: "*D has a greatest element ⊤, representing the 'error' state that results from conflicting updates… any write that would take the state of an LVar to ⊤ results in an error.*" Cleanest one-line statement of the design's actual contribution.
- **LVish `quiesce` is *not* a closure fact, and says so**: quiescence is "*a transient, negative property*", "*a **non-monotonic** property… there is no way to know that more puts will not arrive*." Clean win for the doc: `closed(Q,p)` is a monotone assertion, `quiesce` is a non-monotone detection.
- **But POPL'14 states the doc's problem verbatim and reaches a different conclusion.** Computations that "*rely on **negative information** about a monotonic data structure*" cost determinism: "***the price of negative information is the loss of determinism!***" — hence *quasi*-determinism. Making the toggle attributable changes **who can audit it**, not **whether acting on it is monotone**. The doc reads as if first-classing the toggle dissolved the problem. **Either claim quasi-determinism explicitly or show why attribution changes the outcome.**
- **FlowPools already do asserted closure**: `seal` "*requires explicitly passing the expected bag size*", and Kuper's objection is the one the doc must answer — seal is "*awkward to use when the structure of data is not known in advance*." The doc's answer (close the unconstrained `loss(S,V)`) is good and should be stated against this.
- Kuper's dissertation also names the doc's list-ordering argument: "*Streams… impose an excessively strict ordering for computing the unordered set*", and footnote 5: "*Unlike with lists, the structure of the tree is not known until all elements are present.*" The doc's rejection of list-shaped answers is correct and independently confirmed — it is the same observation that forced concurrent-logic `merge/3` to be indeterminate.

### 6. Lattice-valued Datalog: Flix, Datafun, Ross–Sagiv → Zaniolo

- **Flix defines the intended model by the merge, and the doc's store is explicitly a non-minimal one** — **READ (delegate)**, PLDI 2016. It partitions the Herbrand base into **cells** (same predicate, same first n−1 terms), then: "*we introduce the notion of **compactness**. An interpretation I is compact **iff every cell S in the partition of I has one unique element**… A model M is minimal if it is compact and there is no other model M′ ⊑ M.*" Its own worked example annotates the keep-both-atoms interpretation "*I₃ and I₄ are models, but they are not compact.*" This is the cleanest citable statement of the disagreement and belongs in the doc. Flix solved termination and a unique answer (finite-height lattices) and a real semi-naïve algorithm for lattice rules; it admits it does not check monotonicity ("*a Flix programmer may inadvertently violate one or more of the required properties*").
- **Datafun labels the doc's position as plain Datalog** — **READ (delegate)**, ICFP 2016: Flix "*extends the semantics of Datalog to support defining relations valued in arbitrary lattices (**rather than just the powerset of atoms**)*." Expect "you re-derived Datalog and called the absence of Flix a contribution." Datafun's contribution the doc lacks: **monotonicity tracked in types** ("*Datafun's key feature is to track monotonicity with types*", monotone vs discrete variables, `fix` restricted to finite semilattice eqtypes).
- **Ross & Sagiv → Van Gelder → Zaniolo hands the doc its best ammunition** — **READ (delegate)**, Zaniolo TPLP 2017 (arXiv:1707.05681): Ross & Sagiv's per-aggregate-lattice idea failed because "*(Van Gelder 1993) pointed out that automatically determining the correct lattices would be difficult in practice, and **this was one of the causes that prevented the deployment of the monotonic-aggregate idea in query languages for the following twenty years***." Zaniolo's fix converges hard on §"Orders are mostly read-side": store the downset, then `count = max(mcount)` — order applied at the end — and **pre-mappability (`PREM`)** is exactly the theory of when a read-side order may soundly be pushed into storage. **The doc has independently rederived pre-mappability.** He is also candid about the cost: `msum` over 50,000 "*will return the first 50,000 integers, thus causing serious inefficiencies*" — the free completion has the same blow-up risk.
- **LogicBlox has no lattice support** — the premise was wrong. **READ (delegate)**, SIGMOD 2015, full text: `lattice`/`semilattice`/`least upper bound` appear in no technical sense. It has the opposite — functional predicates `R[t₁..tₙ₋₁] = tₙ` with FD violations as transaction-aborting integrity constraints. (It does have *soft constraints*, "*weighted constraints… whose violations carry a specified penalty*", which is graded non-fatal violation in spirit.)

### 7. Lasp

**READ (delegate)**, PPDP 2015. A variable store of CRDTs with monotone dataflow. `bind(x,v)`: "*If the current value of x is w, this assigns **the join** of v and w to x*" — takes the join. `read(x,v)`: "*Monotonic read operation; this operation **does not return until the value of x is ≥ v** in the partial order*" — the doc's threshold rule, generalised from lattice element to term pattern. Functional layer processes "*never terminate*" — long-lived symmetric agents, but **not demand-triggered**. No demand, no closure.

**What Lasp solved that this design has not:** convergence under lost/duplicated/reordered messages, long-offline replicas, dynamic membership, and — most importantly — **non-monotone user-visible behaviour on a monotone substrate**: "*The Observed-Remove Set CRDT models arbitrary nonmonotonic operations, such as additions and removals of the same element, monotonically in order to guarantee convergence.*" The design has no removal, so it never pays this cost and equally cannot express retraction.

**Lasp's own critique lands:** §7.5 argues threshold reads make "*two assumptions: a priori knowledge of the internal state of a CRDT to properly threshold on the value, and that the queryable value of a CRDT is monotone.*"

### 8. Dedalus

**READ (me).** Datalog + explicit logical time; the model-theoretic foundation under Bloom. Relevant chiefly for a negative: it lists magic sets among the optimisations you *retain* by staying pure Datalog — "*optimizations such as **magic sets** and incremental maintenance of materialized views*" — and never does it. Its confluence TR carries `p_done()` and the "Sealing" lemma (**READ (delegate)**).

### 9. Differential dataflow / DBSP

The decisive statement is not the doc's argument but a theorem: **group-structured updates preclude free termination** (Free Termination, Cor. 20 — **READ (me)**): "*in view maintenance, which often studies rings rather than semirings, free termination is impossible… the benefits of these properties appear mutually exclusive.*" Z-sets with negative multiplicities are non-monotone in representation by construction. **Flo** (Laddad, Cheung, Hellerstein, Milano, POPL 2025, arXiv:2411.08274 — **READ (me)**) unifies Flink, LVars and DBSP under two properties (*streaming progress*, *eager execution*), makes **boundedness a type** ("*a lightweight type system to distinguish bounded streams, which allow operators to block on termination, from unbounded ones*") and stream termination a **terminator symbol `⊗`** — the doc's "closing a tail is an ordinary post of the terminating constructor," typed and static rather than posted and dynamic. Also **not investigated**: whether any demand-driven variant of differential dataflow / Materialize / Feldera exists.

### 10. Datomic (and XTDB)

**READ (delegate).**

- Datomic has had the monotone log since 2012: "*New transactions only **Accumulate** new data. Existing datoms never change*"; history "*includes the present and the unfiltered past, **including retractions***."
- **But it keeps a value layer**: "*Datomic knows that there can only be one value at a time and will **automatically retract the previous value**.*" That is what the design deletes.
- **The doc's stated contribution is Hickey's, verbatim, from 2012** (*Deconstructing the Database*): "***Identity** • A putative entity we associate with a series of causally related values (states) over time.*" The design is Hickey **minus the succession** — an unordered accreting set. Honest cost: there is no "the run's current state," only "everything ever posted about the run."
- **Datomic claims coordination-freeness for reads only, and says so**: "*Because databases are immutable, compute group instances require no coordination for query*"; "*Every successful transaction performs a storage CAS*"; "*Coordination only for process*." The design attempts what Datomic declined and pays by **losing the global basis `t`** — it cannot answer "what did the store look like at time t." Content-addressed run ids give per-computation reproducibility, not a global snapshot. State it as a capability trade.
- **Best framing against Datomic:** same coordination primitive (a storage CAS), radically smaller scope — one key before spawn, rather than the whole log on every write.
- Escape hatches the design will meet: `:db/noHistory` ("*The purpose… is to conserve storage, not to make semantic guarantees*") and excision ("*irrevocable*"; for privacy **and** "*retention period*"; cost "*proportional to the size of the entire database*"). Retention will bite an ML-run store before GDPR does.
- **XTDB: the design has no correction primitive.** VALID_TIME exists because "*You might become aware of an error in the data, and want to correct it retrospectively.*" `loss(60,0.5)` and `loss(60,0.4)` are both true and indistinguishable in *status*. Right if a logged loss is a measurement; wrong for instrument bugs, wrong units, corrupted checkpoints, or a changed metric definition. The only available answer — put every interpretive parameter inside the content-addressed identity — works only if **every** one is inside the hash. Worth an explicit invariant. Mirror image: event sourcing recovers by **replay**; never-replaying is a real advantage at six hours per event, but **a bad atom is permanently present and that `Q`'s residual is permanently non-empty**.
- **Do not cite Kafka as precedent** — compaction retains "*the **latest value** for each message key*" and tombstones are themselves deleted after `delete.retention.ms` (default 24h): non-monotone at both layers. **Do not cite RDF as a monotonicity ally** — RDF 1.1 the model has no retraction, but SPARQL 1.1 Update supplies `DELETE DATA`, `CLEAR`, `DROP`.

### 11. Demand-driven build systems

**Partially investigated.** What was established, **READ (delegate)**:

**The doc's claim "it does not carry a dependency graph, and nothing here needs one" is overclaimed, and the counterexample is the doc's own citation.** **Nix never invalidates** — "*Store objects are **immutable***" — and keeps the graph anyway, **because deletion needs reachability**. Combined with Datomic's excision-for-retention and semantic caching's fragmentation problem: **reclamation is retraction, it is unavoidable on finite disk, and without edges you cannot price it.** The doc's §"Reclamation is evolvable policy behind a fixed mechanism" and its no-graph claim are in tension.

### 12. Tabling engines

**Not investigated in depth.** What is established from adjacent sources: the doc's concession that it needs "a set of outstanding calls keyed by skeleton — that is ordinary tabling" is correct, and **CHRd's motivation was precisely integrating CHR with tabling**, because tabling independently has the design's demand/answer/completion triple (call = demand, streamed answers, completion). **INFERRED**: XSB's completion is an **evaluator-internal SCC fixpoint conclusion**, not a posted or attributable fact — which is the doc's stated contrast, unverified here. XSB's **call subsumption** vs the doc's demand subsumption, and whether any tabling engine computes a *residual* rather than re-running and deduping, remain open.

### 13. Semantic caching — the residual, 1996

**READ (delegate)**, Dar, Franklin, Jónsson, Srivastava & Tan, VLDB 1996. Splits a query into a **probe** (answered from cache) and a **remainder** (sent to the server). Four independent threads in this review converged on it as the doc's `Q ∧ ¬E`.

**And it exposes a dependency the doc does not state.** §2.1.2 explicitly contrasts *semantic* caching (cache described by a formula `V`) against *tuple* caching (per-item presence). The doc has chosen tuple granularity but wants remainder semantics, and those compose only when `V` exists: `Q ∧ ¬E` is the correct residual only if `Q`'s ground instances are enumerable without consulting the store. For an open demand `loss(S,V)`, per-atom presence cannot distinguish missing-because-not-computed from missing-because-nonexistent. **`closed(Q,p)` *is* semantic caching's `V`** — so §"the store is the cache" and §"CWA is a posted fact" are not independent bullets, and the doc should say so.

### 14. CHR / CHRd

**READ (delegate)**, Frühwirth survey. CHR's monotonicity property is named and standard: "*if a rule is applicable in a state, it is also applicable in any larger state… **it is usually called CHR's monotonicity property***." And: "*built-in constraints… can only be added… **user-defined constraints are non-monotonic** in that they can be added and removed*." Parallel CHR's side condition — "*as long as the overlap is only removed by at most one rule*" — is **vacuous** in a propagation-only store, so the doc's CHR-for-execution choice is sound while discarding the reason people use CHR. The doc's set-semantics caution is confirmed: "*The classical logical reading does not reflect CHR's multiset semantics.*"

**CHRd is the set-semantics distributed CHR the doc asks for, with a blocker** (Sarna-Starosta & Ramakrishnan, PADL 2007): "*implements a set-based operational semantics… Moreover, **CHRd's constraint store has no global access point; constraints can only be retrieved through their logical variables. This rules out (the efficient execution of) ground CHR programs.***" A store of `loss(60,0.5)` atoms is ground. The one existing implementation of what the doc wants is structurally unavailable to it.

### 15. Others, briefly

- **Provenance semirings** (Green, Karvounarakis & Tannen, PODS 2007 — **READ (delegate)**): store the **free** object `N[X]`; every concrete semantics is a **unique homomorphic image** (Prop. 4.2); query semantics **factors through** the free one (Thm 4.3); commutation holds **iff** the map is a semiring homomorphism (Prop. 3.5). This is §"No functional dependency" and §"Orders are mostly read-side" — including "off a chain the join synthesizes values nobody posted and destroys provenance" — as an algebraic theorem, twenty years earlier. Strongest available *support*.
- **Residuation** (Hanus, POPL'97): see Q3.
- **Linda** (Gelernter, TOPLAS 1985 — **INFERRED**): the store shape — pattern-matching tuple space, `rd` blocks until a match appears, `eval` spawns a producer — **without** demand-gated production and with a destructive `in`. The ancestor of "a demand is a posted pattern."
- **LCW / completeness statements** (Etzioni, Golden & Weld AIJ 1997; Levy VLDB 1996; Razniewski & Nutt; Darari et al. ISWC 2013): see Q4.
- **Demanded Abstract Interpretation** (Stein, Chang & Sridharan, PLDI 2021 — **READ (delegate)**): "*Demand-driven analyses compute only those results needed to answer a set of extrinsically-provided queries*", over arbitrary lattices with arbitrary widening, with a proof that demanded results equal batch results. Lattice-valued, demand-driven, incremental — but a graph of analysis nodes, not a fact store, and no "an unsatisfied existential *is* demand."

---

## 3. Questions 1–4

### Q1 — Is Bloom^L this design?

**No, and the divergence is one line.** Bloom^L merges at the key and frames it exactly as the thing the doc refuses — **READ (delegate)**: "*Bloom^L allows multiple facts to be derived that differ only in their embedded lattice values; **those facts are merged into a single fact using the lattice's merge function**. This is similar to specifying a procedure for how to resolve **key constraint violations**.*" And **"*lattice elements cannot be used as keys*"** — the key/value split the doc refuses is a hard rule there. Bloom itself has FDs as primitive: "*A subset of the columns in a collection form its key: as in the relational model, the key columns functionally determine the remaining columns.*"

Bloom^L's footnote 5 anticipates the doc's move and demotes it: "*If the user stores a value that does not have a natural merge function, similar systems typically provide a default merge function **that collects conflicting updates into a set** for eventual manual resolution by the user. Such a strategy could easily be implemented monotonically with Bloom^L.*"

**Everything else is close**: threshold reads (`gt_eq`, `when_true`), the no-else-branch rule (footnote 2, verbatim), monotone/morphism discipline, no deletion, whole-program CALM analysis, the `lcart` posted-extent completeness pattern.

**Where else they diverge:** Bloom^L has **no demand at all** (zero occurrences of "demand" in the paper; likewise Dedalus, the CIDR CALM paper, and *Keeping CALM*), no terms or unification, no `conflicted`. Conversely it has a compiler-checked monotonicity analysis the doc has nothing corresponding to — and note that Bloom^L's own framing of the pressure it was built to relieve, the **"type dilemma"** (sets are the only CALM-analysable type, so you either lose precision or over-coordinate), is exactly the pressure the doc is pushing back against.

### Q2 — Demand-driven evaluation + CALM-style monotonicity?

**Nobody has built it.** The CALM lineage is entirely bottom-up/eager. Dedalus names magic sets only as an optimisation retained by staying pure Datalog; Datafun says "*magic sets are a natural next step for investigation into how to optimize Datafun*"; Zaniolo notes applicability and stops. **Grep evidence (READ, me):** zero occurrences of "demand" in Bloom^L, Dedalus, the CIDR CALM paper and *Keeping CALM*.

Closest actually-built things:
- **Oz's `ByNeed`/`WaitNeeded`** — demand-gated production, implemented, but not a distributed monotone store (and its binding protocol coordinates).
- **Demanded Abstract Interpretation** (PLDI 2021) — genuinely demand-driven over arbitrary lattices with an equals-batch theorem, but an analysis-node graph, not a fact store.
- **Webdamlog delegation** — **not investigated**.

**This is the design's clearest open lane, and it should be the headline claim rather than the store model.**

**One caveat that must accompany the claim.** *Keeping CALM* names the disallowed constructs — **READ (me)**: "*we can allow each machine to employ selection, projection, intersection, join and transitive closure… but **not set-difference (the sole non-monotonic operator)**. If we use relational logic, we disallow universal quantifiers (∀) and their negation-centric equivalent (**¬∃**)…*" The doc's residual `Q ∧ ¬E` **is** set-difference and its presence check **is** `¬∃` — verbatim the two constructs the theorem excludes. The defensible narrow claim, which the design has earned: *store contents are confluent; only work is duplicated* — matching Hellerstein's own prescription, "*monotonic design does not stamp out coordination entirely, **it moves it off the critical path**.*"

**But that defence fails on the doc's own headline example.** If the producer is non-deterministic — and ML training is — two agents yield `loss(60,0.5)` and `loss(60,0.4)`, so **store contents** depend on how many agents ran. Non-confluent at the store level, not just the work level. Harmless for "plot the curve"; not harmless for "did this converge?" or "which checkpoint ships?", which will silently differ between agents with different lag. The doc preserves this and pushes a non-monotone selection to every consumer without saying so.

**And the sharpest formal finding of the review.** Via Ameloot, *Keeping CALM* states — **READ (delegate)**:

> "***the class of monotonic programs is the same as the class of programs that do not require knowledge of network membership — they do not query All.***"

Two consequences the doc should adopt:
- **Single-spawn is irreducible for a formal reason**: "run iff ¬∃ another agent already running this" requires `All`. Stronger and more citable than "it's non-monotone."
- **`closed(Q,p)` is coordination-free *precisely because of the `p`*.** A named agent asserting something about its own output needs no membership knowledge (point-to-point in Blazes' sense). Unqualified `closed(Q)` — "nobody anywhere will produce more" — requires enumerating everybody, i.e. querying `All`, i.e. consensus. **Keeping `p` in the signature is what keeps closure out of `All`.** The doc treats `p` as attribution; it is load-bearing for coordination-freeness, and should be a rule with a reason rather than an incident of notation.

### Q3 — Is "propagate, never label" an existing discipline with a name?

**Yes: residuation.** Hanus, *A Unified Computation Model for Functional and Logic Programming*, POPL'97 — **READ (delegate)**:

> "***Residuation** is based on the idea to delay function calls [until arguments are sufficiently instantiated]… The residuation principle preserves the deterministic nature of functions and provides concurrent computations with synchronization on logical variables. **Unfortunately, it is incomplete**… residuation cannot compute this solution but **flounders**.*"
> "*If the branch node is `rigid`, we delay the evaluation… This corresponds to **residuation**. If the branch is `flex`… This corresponds to **needed narrowing**.*"

So: residuation = propagate, never label; narrowing = label. It is a **rule** in residuation-only functional-logic languages, not an optimisation, and the doc's "what it costs is incompleteness" is exactly the known cost. **Adopt the word *floundering*** for the `unknown`/suspend outcome — it comes with thirty years of analysis.

Related namings: in CP the shape is "**propagate and distribute**" with the distribute step omitted (Oz: "*it alternates propagation steps… with distribution steps*"); in CCP the `ask` rule already *is* it; in **Andorra** it is the first half without the second — "*all determinate subgoals should be executed first… **Once all determinate subgoals have finished, the leftmost non-determinate subgoal is selected and its alternatives tried***." Deleting that second sentence converts a search-space-reduction heuristic into a semantic guarantee, and nobody in that literature did it because they all wanted completeness. Library-scale instances: `freeze/2`, `when/2`, `dif/2`, SICStus/SWI `block` declarations, Mercury's `when`.

**No occurrence of the phrase "propagate, never label" or "propagation-only" as a named discipline was found.** The field's names are *residuation*, *coroutining*, and propagate-and-distribute-minus-distribute.

**Two things the doc should answer.** (a) **Curry does not ban narrowing globally** — it makes `rigid`/`flex` a **per-function declaration**, which is the doc's own upstream-resolution pattern rather than a blanket ban, and a live option it has foreclosed without argument. (b) The GPU-CCP work types entailment into an *increasing* Boolean lattice — `entailed : Φ → BInc` — i.e. the doc's threshold rule **enforced by the type system** rather than by discipline. Prior art is stronger than the design here.

### Q4 — Does anyone treat closure as a posted, attributable, scoped fact?

**Yes, repeatedly. This is the most thoroughly solved part of the design.**

- **Punctuations** (Tucker/Maier/Sheard/Fegaras, TKDE 2003) — the canonical form: posted **as data**, scoped by a predicate, per-producer, with the all-producers second level and operator propagation rules.
- **Blazes sealing** (ICDE 2014) — the CALM-world version, with a measured one-producer vs many-producers split, and a Zookeeper call to learn the producer set.
- **Dedalus** `p_done()` with **Lemma 5 "Sealing."**
- **Edelweiss** `sealed` collection type, auto-emitting punctuations.
- **FlowPools** `seal` (with expected size).
- **Bloom^L's `lcart`** — a checkout message carrying `lbound`, declaring the extent that must be present.
- **LCW statements** (Etzioni, Golden & Weld, AIJ 1997) — asserted, scoped local-closed-world statements in planning. **INFERRED** beyond the citation.
- **Completeness statements over databases and RDF** — the closest formal match found anywhere in the review, **READ (delegate)**, Darari, Nutt, Pirrò & Razniewski, ISWC 2013:
  - **Def. 16**: "*An **indexed completeness statement** is a pair (C, k) where C is a completeness statement and k ∈ J is an IRI*" — statement **plus producer**, i.e. `closed(Q, p)` exactly.
  - **Def. 17**: "*The flattening… is **the union of the individual graphs***" — the doc's "the global store is the union of the local ones."
  - **Thm 20 (Smart Rewriting)** proves demand-directed routing: "*the federated version evaluates each triple pattern only over a single source.*"
  - Statements stored **as RDF**, with a vocabulary.
  - They have the **entailment calculus the doc lacks** (Thm 10: `C ⊨ Compl(Q)` iff `P̃ = T_C(P̃)`, a decidable syntactic test) — and its price: "***All completeness checks presented in this paper are NP-complete.***"

**So attributability and producer-indexing are also prior art.** The surviving increments are two, and they are real:

1. **Scoping by the producer's own extent rather than the asker's question** — durable, transfers to every subsumed question, survives the producer's death. Punctuations are stream-scoped and Blazes' seals are partition-scoped; neither makes the extent/question distinction the doc's §"CWA is a posted fact" turns on.
2. **Closure statements that cannot go stale.** The ISWC line's statements *do* go stale — they need temporal guards and a statement-*update* path, i.e. mutable metadata about a mutable source, reintroducing exactly what monotonicity avoids. Because the doc's `Q` names a content-addressed **immutable** computation, `closed(Q,p)` can never go stale. **That is the defensible novelty; closure-as-data is not.**

**And the "detected vs asserted" contrast the doc implies is right but under-stated.** Classical alternatives — Dijkstra–Scholten/Misra termination detection, LVish `quiesce` — *detect* a non-monotone property and need coordination or repetition; posting asserts a monotone one. But Blazes shows the second-level fact still needs an authority, and LVish shows acting on negative information still costs determinism. **Not investigated:** Wikidata "no value"/complete-list qualifiers, SHACL `closed` shapes, epistemic operators in DLs, and whether anyone has done a scoped/per-source **Clark completion**.

---

## 4. What to cite that it currently does not

**Load-bearing — without these the doc is rediscovering published work:**

- Tucker, Maier, Sheard & Fegaras, *"Exploiting Punctuation Semantics in Continuous Data Streams"*, TKDE 2003 — §"CWA is a posted fact", **both levels**.
- Alvaro, Conway, Hellerstein & Maier, *"Blazes"*, ICDE 2014 / arXiv:1309.3324 — sealing; the price of the producer-set fact; the classification of sealing as *coordination*.
- Conway, Marczak, Alvaro, Hellerstein & Maier, *"Logic and Lattices for Distributed Programming"*, SOCC 2012 — the closest whole system; **footnote 2** for the no-else-branch rule; **footnote 5** for the free completion as a known fallback.
- Conway, Alvaro, Andrews & Hellerstein, *"Edelweiss"*, VLDB 7(6) 2014 — §"Reclamation is evolvable policy"; the `sealed` type.
- Power, Koutris & Hellerstein, *"The Free Termination Property of Queries Over Time"*, ICDT 2025 / arXiv:2502.00222 — §"The one rule" and §"CWA is a posted fact" **are** its Props 9/13/14 and Thm 24; Cor. 20 is the argument against DBSP.
- Hellerstein, *"Complete CALM: A Coordination Criterion for Specifications"*, arXiv:2602.09435 (2026) — **this is the CALM the doc actually needs**: "*A specification maps execution histories to outcome sets under a declared refinement order; we prove it admits coordination-free implementation if and only if its outcomes are monotone.*" The doc's monotonicity is over an information order on terms, not set containment, so the 2013 relational-transducer result does not cover it.
- Ameloot, Ketsman, Neven & Zinn, TODS 40(4) 2016 — the fine-grained refinement; note they state the converse direction "*is false when taken literally*."
- Hellerstein & Alvaro, *"Keeping CALM"*, CACM 2020 / arXiv:1901.01930 — the "do not query **All**" characterisation and "coordination off the critical path."
- Saraswat, *Concurrent Constraint Programming* (1989/1993); Saraswat & Rinard, POPL'90; Saraswat, Rinard & Panangaden, POPL'91 — the doc **names** CCP and cites nothing.
- Haridi, Van Roy, Brand et al., *"Efficient logic variables for distributed computing"*, TOPLAS 1999 — the owner protocol, for the shared-variable question.
- Kuper & Newton, *LVars*, FHPC 2013; Kuper, Turon, Krishnaswami & Newton, *"Freeze After Writing"*, POPL 2014 — **the doc cites neither**, and POPL'14 has the strongest argument against its `closed` framing.
- Hanus, POPL'97 — residuation, and the word **floundering**.
- Madsen, Yee & Lhoták, *Flix*, PLDI 2016 — **compactness**, the precise statement of the disagreement.
- Arntzenius & Krishnaswami, *Datafun*, ICFP 2016 — monotonicity-in-types.
- Zaniolo et al., TPLP 2017 / arXiv:1707.05681 — **pre-mappability**, and the twenty-years-of-non-adoption history.
- Green, Karvounarakis & Tannen, *"Provenance Semirings"*, PODS 2007 — the free-object argument for §"No functional dependency."
- Dar, Franklin, Jónsson, Srivastava & Tan, VLDB 1996 — probe/remainder = the residual, **and** the semantic-vs-tuple caching distinction.
- Darari, Nutt, Pirrò & Razniewski, ISWC 2013 — indexed completeness statements; the entailment calculus and its NP-completeness.
- Sarna-Starosta & Ramakrishnan, PADL 2007 (**CHRd**) — with its ground-store blocker.
- Laddad, Cheung, Hellerstein & Milano, *Flo*, POPL 2025 / arXiv:2411.08274 — boundedness-as-type and the `⊗` terminator.
- Meiklejohn & Van Roy, *Lasp*, PPDP 2015 — the threshold read, and the OR-Set answer to retraction.
- Hickey, *Deconstructing the Database* (2012) — the doc's identity definition, verbatim.

## 5. What it should stop claiming as novel

- **"Closure is better as a posted fact than as an evaluator assumption."** Punctuations, sealing, `p_done()`, `sealed`, FlowPools `seal`, LCW statements, and — closest — **indexed completeness statements**, which already pair statement with producer. Keep only: **extent-scoping rather than question-scoping**, and **non-staleness via content-addressed `Q`**.
- **"Threshold claims always, exact claims only at settledness."** LVars threshold reads (2013), Bloom^L `gt_eq`/`when_true` (2012), and now a **theorem** (ICDT 2025) that threshold queries are the *only* freely-terminating ones.
- **"A rule that fails to match must not fire — no else-branch."** Bloom^L footnote 2, 2012.
- **"Keep the atoms, take the free completion."** This is plain Datalog / the powerset lattice — Datafun describes Flix as adding lattices "*rather than just the powerset of atoms*", and Bloom^L notes Bloom "*assumes a fixed merge function (set union)*." Lead instead with **`conflicted` as an affirmable predicate** — LVars' `⊤` without the crash — which *is* unclaimed.
- **"Propagate, never label."** Residuation, and say *floundering*.
- **"Demand is an unsatisfied existential; production is demand-gated."** Oz `ByNeed`/`WaitNeeded` and concurrent-logic demand-driven streams already do this. What is unclaimed is the **combination** with a monotone distributed store and symmetric producer/querier roles — the strongest available novelty claim, and it should be the headline.
- **"CCP is the right model for the semantics."** True, and therefore not a finding — cite Saraswat. Likewise "agent = closure operator" is CCP's, and the categorical semantics is Panangaden/Saraswat/Scott/Seely's hyperdoctrine.
- **"Monotone store."** Undersells the contribution and invites the wrong comparison. Every durable-fact system has the monotone log; **the design deletes the value layer.** Say that.
- **"It does not carry a dependency graph, and nothing here needs one."** Nix never invalidates and keeps the graph anyway, because **deletion needs reachability**. Reclamation is retraction.

**Four things to fix that are not citation issues:**

1. **State whether posted terms share bindable variables across agents.** If they do, Oz's owner protocol is unavoidable and the coordination-free claim does not hold as written. This is the most consequential gap found.
2. **Either accept quasi-determinism explicitly for the closed-world step, or show why attribution changes the LVish result.**
3. **Restate the CALM claim narrowly** — store contents confluent, work duplicated — and **answer the non-deterministic-producer case**, where even that narrower claim fails and the doc silently relocates a non-monotone selection to every consumer.
4. **Make `closed(Q,p)`'s two roles explicit**: it is semantic caching's `V` (so the residual depends on it for open demands), and the `p` is what keeps closure out of `All`.

**Coverage caveat:** five of six research threads completed. Tabling engines and the *Build Systems à la Carte* taxonomy placement are **not investigated**; claims about them are labelled INFERRED.

---

# Final addendum — question 5, corrected

## The closest published relative, which none of the earlier threads found

**Jason Morton, *Contextuality from missing and versioned data*, arXiv:1708.03264 (2017).** Its setting is the doc's setting, verbatim:

> "*modern data analysis must deal with **distributed databases with many partial local tables that need not always agree**. The computational agents tabulating these tables are **spatially separated**, with binding speed-of-light constraints and data arriving too rapidly for these distributed views ever to be fully informed and globally consistent.*"

> **Def. 6.2** "*The adjective **contextual** describes a presheaf satisfying Locality but **not Gluing**, or a particular compatible family of local sections in a presheaf which serves as a counterexample to the Gluing condition.*"
> **Prop. 6.4** "*Every presheaf of tables can be **completed to a sheaf** by adding in the relative global sections obtained by gluing.*"

He models network partition explicitly. **The doc's "lagged replication is a section not yet extended" is Morton's Prop 6.4 running forward**, and his sheafification is what a monotone store does. This should be the doc's primary citation for §"the sheaf map."

## The sheaf claim is wrong, and not for the reason first given

**1. An incompatible family is not a gluing counterexample.** The gluing axiom quantifies only over **compatible** families — Morton's definition says so, Abramsky's says so ("*consistent projections… a **compatible** family*"). Two producers disagreeing at one atom is an **incompatible** family: it fails the *precondition*, not the axiom. Worse, with the doc's implicit indexing (sections = truth restricted to a set of ground atoms), **its presheaf actually is a sheaf** — it has no gluing failures available to it. The doc's §"conflicted(K) is failure of the gluing condition" bullet is therefore not merely imprecise; it names something that cannot occur in the structure it just built.

**2. The "geometric logic becomes forced" argument is backwards.** The doc says: "*Agents are open subspaces, the maps between them are inclusions, and geometric logic is exactly what transports along those.*" But geometric logic is what's preserved by **arbitrary** geometric morphisms. Inverse image along an **open inclusion** is étale, hence a **logical** morphism — it preserves `→`, `¬`, `∀` and exponentials too. So if agents really are open subspaces, the architecture forces *more* than geometric logic, not exactly it. **This bullet does not do the work the doc assigns it**; the coordination argument in §"CALM" has to carry the restriction alone.

**And the topology is never stated.** Until the covers on the space of ground atoms are named, the sheaf condition is vacuous (discrete) or automatic — Goguen: "*If P is a totally ordered set … the finite sheaf condition is **automatically satisfied** in the downclosed topology.*" Name the covers and the claim becomes falsifiable, which is the version worth keeping.

## The doc is claiming the wrong fragment

**READ** — Dyckhoff & Negri, *Geometrisation of first-order logic*, *BSL* 21(2):123–163 (2015), item 7 — the only explicit Datalog statement in this literature:

> "*Special coherent implications ∀x. C ⊃ D **generalise the Horn clauses from logic programming**; in fact, they generalise the 'clauses' of disjunctive logic programs.*"

Monotone Datalog — no function symbols, atomic heads, no `∃`, no `∨` in heads — sits in the **Horn/cartesian** fragment, three levels below geometric. Calling the derivation language "geometric logic" is true the way "an integer is a complex number" is true: it discards exactly the properties (decidability, finite chase, unique least model) that make it Datalog. And their **item 6 is the theorem the doc actually needs and never cites**:

> "***Filtered colimits in Set of models of a coherent theory T are also models of T*** [Johnstone, Lemma D.2.4.9]"

That is the abstract statement of "monotone growth never invalidates a derivation" — *coherent*, not geometric.

Relatedly: **Vickers' own "Geometric Theories and Databases" does not support the doc** — full-text grep returns `Datalog` 0, `Horn` 0, `logic program` 0, `sheaf` 0 — and Vickers' verdict on that line is "*The applications to database theory are very simplistic.*"

## Attribution corrections

- **"An agent is an open subspace, its view is a section" is standard, verbatim.** Robinson (*Information Fusion* 2017): "*In the context of sensors, a U ∈ T is called a **sensor domain**… The space of attributes S(U) is called the **space of observations** over the sensor domain U… We call an element s ∈ S(X) a **global section**.*" Same clause set, "sensor" for "agent." Independent second tradition: topological epistemic logic (Moss & Parikh 1992), itself "*partly inspired by Vickers' work on … a logic of finite observation.*"
- **Robinson also beats the doc on conflict**: he doesn't call it failure, he **grades** it — "*The minimum value of ε for which an assignment … is an ε-approximate section is called the **consistency radius***", and Prop. 23: "*The consistency radius of an assignment is an obstruction to it being a global section.*" That is `conflicted(K)` with a magnitude, which is strictly more than the doc offers and directly relevant to its one-ulp jitter case.
- **Smyth is primary for "open = affirmable", not Vickers.** Escardó calls it "**Smyth's dictionary**"; Abramsky & Jung defer to `[Smy92]`; and **Vickers himself** writes that "*the ideas of Samson Abramsky and Mike Smyth **in effect provided an ontology** for propositional geometric logic in terms of observability. [Topology via Logic] **uses this** as the basis for its treatment of topology.*" Cite Smyth (ICALP 1983; Handbook vol. 1, 1992) and Abramsky (DTLF, APAL 1991) alongside Vickers.
- **`c` compact ⟺ `↑c` Scott-open holds in any dcpo.** "In an algebraic domain" is a superfluous hypothesis; algebraicity is what makes `{↑c}` a **base**. Cite Abramsky & Jung, "Domain Theory", Handbook vol. 3, Defs 2.2.1 + 2.3.1. Also: upper-set does **not** imply Scott-open without directed-sup closure — the doc uses that step in §"No functional dependency."
- **Goguen never connected institutions to sheaves, deliberately.** Full-text grep of Goguen & Burstall *Institutions* (JACM 1992): `sheaf` 0, `topos` 0, `Grothendieck` 0. All 10 uses of "glue" mean **colimit**. And he states the distinction the doc collapses: "*The relationship between global and local behaviour that arises between a system and its component objects **should be distinguished from** the global/local relationship stated in the sheaf condition … The first concerns … limits, while the second concerns 'glueing together'.*"

## The better formal frame for what the doc wants

Institution-theoretic **model amalgamation** (Diaconescu): "*for any pushout of signatures … M₁ and M₂ with common reduct to Σ … admit a **unique common expansion** M′ … Often its weaker variant, that does not require uniqueness, suffices; this is called **weak model amalgamation**.*" That is the sheaf axiom in the doc's actual setting, with the uniqueness/separatedness distinction already made — and the failure modes catalogued. CASL treats amalgamability as a **checked side condition**, i.e. practitioners already treat "does this glue?" as a decision problem rather than an axiom. Highest-value follow-up: Diaconescu, "Grothendieck Institutions", *Appl. Cat. Struct.* 10:383–402 (2002).

## One firm negative — and it is an opportunity

Nothing connects CALM, coordination-freeness, Bloom, Dedalus, Webdamlog or Ameloot's transducer networks to sheaves or topos theory. `sheaf ∧ Datalog` and `topos ∧ Datalog` return zero on both OpenAlex and arXiv; likewise sheaf + CRDT and sheaf + eventual consistency. A logic program's model as a sheaf does not exist in the literature (~90% confidence); the nearest is Finkelstein, Freyd & Lipton (*TCS* 300, 2003), lfp of `T_P` in a **presheaf** topos over the syntactic category — presheaf, syntactic base, no locality.

**So: the doc's individual sheaf claims are all prior art or wrong, but the specific combination — a topological/sheaf semantics for a *coordination-free monotone distributed* logic program — is genuinely unoccupied.** That is worth pursuing, and it becomes a real result rather than a slogan the moment the covers on the space of ground atoms are named.

## Addendum detail (from the sheaf thread's full report, delivered separately)

**The closest published statement of "conflict = gluing failure" is Abramsky (2013)**, *Relational Databases and Bell's Theorem*, arXiv:1208.6416, LNCS 8000 — **READ, full text**:

> "*We shall interpret a schema Σ = {A₁,…,A_k} … **as a cover**. That is, we think of the attribute sets A_i as **'open sets'** expressing some local information…*"
> "*An instance … is a **family of local sections**, defined over the open sets in the cover. A central issue … is whether we can **glue these local sections together into a global section**.*"
> **Prop. 2.2**: "*An instance satisfies the **gluing condition if and only if there is a universal relation** R for the instance.*"
> "*It is of course a well-known fact of life in databases … that **our relational presheaf R is not a sheaf**.*"

With a dictionary (attribute↔measurement, tuple↔local section, universal relation↔global section, acyclicity↔Vorob'ev), and the complexity: deciding gluability = the join consistency property, **NP-complete**. The generalised slogan is also published — Abramsky, Barbosa, Kishida, Lal, Mansfield, CSL 2015 (arXiv:1502.03097): contextuality as "*a family of data which is **locally consistent, but globally inconsistent** … an **obstruction to forming a global section***", and "*pervasive … in **databases** and **constraints**.*"

**Why the doc's presheaf cannot exhibit the phenomenon.** If a view over open `U` is *the facts in `U`*, restriction is intersection and gluing always succeeds uniquely — `(⋃Sᵢ) ∩ U_j = S_j`. Abramsky flags exactly this degeneracy for his event sheaf: "*The fact that this sheaf condition holds for E is **quite trivial**, since we are simply looking at **functions on a discrete space**.*" His obstruction lives only where restriction is **lossy** (existential image / marginalisation, `R ⊆ R|_A ⋈ R|_B` with strict inclusion possible). **So the doc must identify a lossy restriction or drop the claim.**

**Three invalid inferences, enumerated:**

1. **"Geometric logic becomes forced."** Open-subspace inclusions are **étale**, and the inverse image of an étale geometric morphism is a **logical** morphism — preserving exponentials, `Ω`, `¬`, `→`, `∀`. Restriction along inclusions preserves *strictly more* than geometric structure. Geometric logic is forced by **arbitrary** geometric morphisms. Vickers' correct version: "*In general this does not preserve the Heyting arrow … Hence we need a logic that corresponds to the structure of **frame** rather than of Heyting algebra.*"
2. **"Lower set ⟹ upper complement ⟹ affirmable."** Upper ≠ Scott-open. Abramsky & Jung Def. 2.3.1: closed = "*a **lower set AND** is closed under suprema of directed subsets*"; "*A Scott-open set O is **necessarily** an upper set*" — one direction only. Directed-sup closure is needed, i.e. conflict must be **finitely witnessed**. That step is the content and it is skipped.
3. **The topology is never stated.** With no non-trivial coverage the sheaf condition is vacuous or automatic.

**Citations that do not support the doc** (all grepped): Goguen's *Categorical Manifesto* — `sheaf` 0. Winskel/Nielsen/Cattani presheaf models — a **category error** to cite here; base is a path category, "gluing" is free colimit completion, `Grothendieck topology` 0, `sheaf condition` 0. Malcolm (LNCS 4060) — base is the trace topology, `conflict` 0. Goguen & Burstall *Institutions* — `sheaf` 0, `topos` 0, `amalgamat` 0.

**Nearest convergent thinking, independently reached and without topology:** Doing & Wisnesky, arXiv:2407.19095 — argues for **regular / existential Horn** logic for web-scale integration "*so that we need not risk inconsistency*". Also live: Felber, Hummes Flores & Rincon-Galeana, DISC 2025 / arXiv:2503.02556 — "*terminating solutions are precisely its **global sections***", though the obstruction there is to a decision function from epistemic indistinguishability, not to reconciling writers.

**Recommendation as given:** drop "derived"; adopt the standard framing with citations (Smyth 1983/1992; Abramsky 1991, 2013; Vickers 1989; Robinson 2017; Morton 2017); then either identify the lossy restriction that makes the presheaf genuinely not a sheaf — the only way the claim earns its keep — or demote it to analogy and keep "two atoms with no upper bound" as the operative definition. Either way: **state the topology first**, and replace "geometric" with **coherent (Horn/cartesian)**.
