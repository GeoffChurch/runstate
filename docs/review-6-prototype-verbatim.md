<!--
PROVENANCE (not part of the review). Untracked file.
Sixth adversarial review, commissioned 2026-08-10: the prototype-driven one. Three lenses —
build end-to-end; demand-subsumption cost under constraints; whether the agent model delivers
demand propagation. Review 5's findings (corpus boundary, claim batch) were marked established.
Everything below is the reviewer's response, verbatim.
-->

# Adversarial review 6 — prototype-driven, `docs/backlog/if-built-today.md`

## VERDICT

**The build runs, and the thing that breaks is not on the brief's list.** Stages 1–4 (monotone term store, per-functor index, cursor standing calls, individual streaming, constraint-carrying demands) built and ran clean. **Stage 5 — `closed(Q,p)` — is where the design fails, and it fails on the library's own stated contribution.** Closure is attributed to a *producer*, which is a process; the run is supposed to outlive its processes. Measured: once producers exit, a query nobody thought to ask while they were alive **can never settle**, permanently. The obvious repair (close over the producer's extent rather than over `Q`) works and then affirms `¬∃` for everything **undemanded** — which under the doc's own demand-gating is most of the space.

**The two hazards the brief named are real but conditional, and they are the same hazard.** Resource-limited entailment and solver-internal search both appear **exactly when the constraint domain's propagator is incomplete for entailment**, and in no other case. Measured across seven constraint classes: propagation alone decided entailment in every one a step axis would plausibly use; only subset-sum-hard constraints went undecided, and there the timeout (≥22 shared variables) and the coroutine leak arrived together. This converts **Open #2 from a free choice into a constrained one**.

**Lens 2 is a cost result with a clean answer.** Naive compaction is 52.8 s at n=1600 and extrapolates to **2.65 hours** at corpus scale. A syntactic pre-filter is **exact, not merely sufficient**, and recovers 43×; restricting the domain to intervals deletes the solver entirely (197×). But **containment is the wrong compaction** — it leaves **2.42× the minimum work** at n=1600.

**Lens 3: the edge lives nowhere.** Confirmed by construction, and the repair is monotone, which is the wrong shape for the job `:651` assigns it.

**Constraint-store monotonicity was a strawman, as the brief anticipated** — `fd_dom` is restored exactly after backtracking; the solver *is* a black box with respect to constraint state. It is not a black box with respect to *coroutines*, which is a different thing and is finding 5.

---

## 1. Closure cannot settle a question asked after the producer exits — and the repair affirms the wrong proposition. MEASURED.

**Claim tested.** `:160` — *"`closed(Q, p)` — "producer p will send no more for Q" — delivered like any other fact"* — together with `:204`, *"that is exactly when `¬∃` becomes affirmable, because "no answer arrived" is then decidable by inspecting finitely many exhausted streams."*

**What I did.** Built the two-level closure verbatim (`e3_closure.pl`): a producer serving `loss(S,V)` over `S ∈ 0..1000`, demand-gated per `:181-182`; `closed(Q,p)` and `no_more_producers(loss)` as ordinary posted atoms; `settled/2` requiring both. Then exercised both indexing choices.

**Measured — horn 1, closure indexed by `Q` (the doc as written):**

```
Q1 = loss(S,V), S =< 100.  p serves it, posts closed(Q1,p).
post no_more_producers(loss).      settled(Q1) = TRUE
p's process finishes.
a new querier asks Q2 = loss(S,V), S =< 200.
                                   settled(Q2) = FALSE   <- needs closed(Q2,p)
retry:                             [p has exited: cannot post closed/2]
                                   settled(Q2) still FALSE -- and PERMANENTLY so
```

The world is closed, every producer is finished, and `Q2` can never settle. Settledness is not a property the store accumulates; it is a property **re-issued per query shape by a live process**.

**Measured — horn 2, closure indexed by the producer's extent (the obvious repair):** `p` serves `0..1000`, is demanded only `0..100`, produces **101 atoms**, and at exit honestly posts `closed(loss(S,V), p)` — *"I will send no more"*, which is true. Now every `Q` settles by subsumption, and the store **affirms `¬∃ loss(150,V)`**. But nothing was ever wrong with step 150; nobody asked. Re-demanding it produces `loss(150,1.5)` immediately.

The two horns are one fact: under demand-gating, *"no answer arrived"* and *"no such fact exists"* differ by **exactly the undemanded region**, and `:204` equates them.

> **Corrected statement.** `:204`'s *"`¬∃` becomes affirmable, because "no answer arrived" is then decidable"* is **wrong as an equation between propositions**. The two facts of `:196-206` make *"no answer arrived for Q"* decidable; they do not make `¬∃` affirmable, because `:181-182`'s demand-gating guarantees no answer arrives for anything undemanded. Closure therefore has no safe indexing: indexed by `Q` (`:160` as written) settledness **expires with the producer process** — measured, a query issued after producers exit can never settle, which contradicts the design's own contribution, *"the run as a durable identity outliving its processes"* (`:822`); indexed by the producer's extent it settles every `Q` and affirms absence for the undemanded region, which is not a claim about the world. `:226`'s careful *"`closed(Q, p)` settles "what did p produce?" completely"* is the only defensible reading — and *what p produced* is a fact about a process, not about the run, so it cannot gate negation over the run.

---

## 2. The memo check and closure are the same missing fact. MEASURED.

**Claim tested.** `:241` — *"before running a producer the evaluator asks "do I already have an answer?" That memo check **is the cache**"* — and `:262`, *"There is no `read`, and no syntactic substitute for one."*

**What I did.** Built producers as `:184-185` specifies them — *"an agent that watches for patterns it can serve, computes, and posts atoms that unify with them"* (`e4_demand.pl`, `e8_couple.pl`).

**Measured — it does not terminate.** The first end-to-end run of stage 6 hit a **2-minute wall-clock timeout**. The producer watches `loss(S,V)`; its own answer `loss(60,0.5)` unifies with that pattern, so it re-triggers on its own output. `:262-268` explicitly forecloses the syntactic fix. Adding the memo check terminates it — so **the memo check is load-bearing for termination**, which the doc does not say.

**Measured — and answer-keying is wrong for pattern demands:**

```
standing demand loss(S,V), S free.  one answer arrives: loss(0,0.5).
memo check for the SAME standing demand: HIT
  -> producer never runs again; steps 1..500 are never produced
```

**Measured — the coupling.** For a *wider* demand `D2 = loss(S,V), S ≤ 200` after `D1 = S ≤ 100` was served, the evaluator has two options and both are wrong: trust the memo hit and return a silently **incomplete** answer set, or distrust it and re-run **a six-hour job the memo exists to prevent**. The fact that would decide between them is `closed(D2,p)` — and by finding 1 it can only be posted by a process that has exited.

> **Corrected statement.** `:241`'s *"That memo check **is the cache**"* is under-stated in one direction and wrong in another. Measured, it is also the **only thing preventing a producer from re-triggering on its own output**, because `post` cannot distinguish a posted demand from a posted answer and `:262-268` bans the syntactic test — so it is load-bearing for *termination*, not only for cost and answer-set identity. And as written it is **answer-keyed** (*"do I already have an answer?"*), which is correct for a ground demand and wrong for a pattern: measured, one answer suppresses production of the other 500. Keying it by **call** instead is the repair, and it is not free — a variant-keyed table re-runs the overlap between two calls (finding 4: 58.7% of post-compaction work at n=1600), and a subsumption-keyed table needs the subsumed call to be **complete**, which is `closed(Q,p)`, which is finding 1.

---

## 3. Lens 3 — the edge lives nowhere, and the repair is monotone, which is the wrong shape. MEASURED.

**Claim tested.** `:184-189` — *"A producer needing something of its own just posts a pattern too, which makes it a querier; the roles stay symmetric all the way down"* — against `:647`, which lists the internalised demand relation as *"this was wanted; **this depends on that**"*, and `:651`, which assigns it *"dependency graphs, admission analysis."*

**What I did.** Built the two-handler case: A serves `report(R)` and needs `loss`; B serves `loss(S,V)`. A posts a pattern because it needs B's output, exactly as `:184-185` prescribes.

**Measured — the entire store after one top-level demand for `report`:**

```
t=1  report(_G1)
t=2  loss(_G1,_G2)      <- A's demand for B's output
t=3  loss(60,0.5)
t=4  report(rep(_,_))
```

No atom of any shape records that A's demand caused `t=2`. A's posted `loss(_,_)` is byte-identical to the same term posted by an unrelated top-level querier. **Symmetric roles means indistinguishable, and indistinguishable means no edge.**

**Measured — it is not recoverable from the log either.** With two consumers both needing `loss`, the demand is posted **once** and the second consumer's request is a memo hit, so it never posts at all. There is no k-to-1 edge latent in the ordering to reconstruct; the second edge was never an event.

**The minimal repair, and whether it survives.** A posts `wants(ChildOpen, ParentOpen)` alongside its demand. It survives the design's constraints — monotone (accumulates), no negation, and consistent with `:647`'s placement of the demand relation inside the store. It costs three things: demands need **identity** (the `Open` sort of `:624-626`), the agent must know **which demand it is currently serving** (so a producer is no longer stateless, which is what `:184-189` was buying), and the edge is **many-to-one** under sharing.

**Measured — but monotone is the wrong shape for the stated job.** Five subscriptions recorded, four withdrawn (`:583` says leases expiring and queriers withdrawing are normal and non-monotone):

```
wants/2 atoms in the store : 5
live subscriptions         : 1
```

> **Corrected statement.** `:647`'s claim that the internalised demand relation carries *"this depends on that"* is **not delivered by the mechanism `:184-189` specifies**. Measured on the two-handler case, the dependency edge exists in no atom, and it is not recoverable from log order because demand sharing means the second dependent never posts. The repair (`wants(Child,Parent)`) is available, is monotone, and survives every constraint the design imposes — but it costs demand identity plus per-agent serving context, and it **cannot do the job `:651` assigns it**: `wants` records what was *ever* wanted, and admission control needs what is wanted *now*. Measured, 5 recorded against 1 live. `:648`'s own row already puts the live subscription outside the store as control; the consequence is that **admission analysis is not available from the internalised relation**, and `:651` should not list it.

---

## 4. Lens 2 — the cost, and containment is the wrong compaction. MEASURED.

**Claim tested.** `:631` — a subsumed *"standing `loss(V,12)` is redundant for production and can be dropped from the work set"* — plus `:639-641`, *"derive the work set, never destructively shrink it … Recomputing the work set from the surviving subscriptions handles that for free."*

**Setup grounded on the corpus** (`steps.out`, public API, 80 logs sampled): step axis `0..500` (p50 max step 150), **22 value names per run**, 824 runs → realistic work sets of 10²–10⁴ demands; 824 × 22 = **18,128** if everything were demanded.

**Measured — one comparison:**

| | µs/call |
|---|---|
| term subsumption (trie walk) | **0.233** |
| clpfd entailment (solver call) | **29.05** |
| specialised interval test | **0.174** |
| **solver / trie ratio** | **125×** |

**Measured — compaction of *n* demands, three strategies (all three produce identical kept sets):**

| n | pairs | naive (solver every pair) | pre-filter: solver calls | pre-filter time | domain-restricted |
|---|---|---|---|---|---|
| 100 | 9,900 | 0.28 s | 72 | 0.01 s | 0.001 s |
| 400 | 159,600 | 4.23 s | 1,375 | 0.09 s | 0.021 s |
| 800 | 639,200 | 15.91 s | 5,659 | 0.35 s | 0.088 s |
| 1600 | 2,558,400 | **52.82 s** | 23,402 (**0.91%**) | **1.22 s** | **0.268 s** |

Extrapolated at 29.05 µs/call: n=10,000 → **48 min** naive, 26 s pre-filtered; n=18,128 → **2.65 h** naive, 87 s pre-filtered.

**The cheaper sufficient condition is in fact exact.** Subsumption is a conjunction of term subsumption and constraint entailment, so testing the syntactic conjunct first can only skip pairs that would have failed anyway — measured, identical kept sets at every n (98/173/295/500/842), for a **43× speedup**. Restricting the constraint domain to intervals removes the solver entirely for **197×**, and the interval test is *cheaper than the trie walk it accompanies* (0.174 µs vs 0.233 µs) — so on a fixed interval domain, **constraint-carrying demands cost nothing over bare patterns**.

**Measured — containment is the wrong compaction** (`e6_overlap.py`; cover = the `(name, run, step)` triples a demand denotes):

| n | kept | naive work | after containment | true minimum | gap containment cannot see |
|---|---|---|---|---|---|
| 100 | 98 | 197,360 | 197,289 | 196,910 | 379 (0.2%) |
| 400 | 295 | 789,200 | 763,020 | 696,148 | 66,872 (8.8%) |
| 800 | 500 | 1,568,720 | 1,490,337 | 1,104,277 | 386,060 (25.9%) |
| 1600 | 842 | 3,127,760 | 2,886,911 | 1,192,123 | **1,694,788 (58.7%)** |

Containment-based compaction leaves **1.35× the minimum at n=800 and 2.42× at n=1600** — and the gap *grows with n*, because overlap density grows while containment density does not.

> **Corrected statement.** `:628-641` is sound and its cost is **not** the problem the brief anticipated. Measured, a solver call is 125× a trie walk, but naive O(n²) compaction is recovered by a **syntactic pre-filter that is exact rather than merely sufficient** (identical kept sets at every n; 0.91% of pairs reach the solver; 43×), and by fixing the constraint domain to intervals the solver disappears entirely (197×, and cheaper per comparison than the term walk). The doc should say that the pre-filter is exact — it follows from subsumption being a conjunction — because otherwise the O(n²)-solver-calls reading makes `:639-641`'s mandated **recomputation** (paid per subscription change, i.e. at UI cadence) look unaffordable when it is not. **The real defect is the choice of relation.** `:631`'s *"redundant for production and can be dropped"* is the only compaction the doc considers, and measured it leaves **2.42× the minimum work at n=1600** — 58.7% of post-compaction work is overlap that containment structurally cannot notice, and the fraction grows with n. Two demands that merely *overlap* share production, and neither subsumption nor `:634-637`'s correctly-rejected anti-unification addresses it. The construction that does is the doc's own **residual** (`Q ∧ ¬E`, `:816`) applied on the demand side rather than the answer side — which the doc already names as a negation-bearing exception, so the cost is known but is currently booked against the wrong section.

---

## 5. The entailment timeout and the solver leak are one hazard, gated on the constraint domain. MEASURED.

**Claims tested.** `:683` — *"entailment claims are monotone … "The store entails `S ≤ 100`" is affirmable and permanent"* — and `:889`, Open #2: the domain *"must be fixed and decidable and says nothing about **which**."*

**Measured — the timeout is reachable.** Complete entailment (`C ⊨ φ` iff `C ∧ ¬φ` unsat, with labelling), fixed 30 M-inference budget, low-density subset-sum where `S #\= T` is entailed at every size:

| shared FD vars | propagation alone | with labelling | inferences |
|---|---|---|---|
| 10 | UNDECIDED | entailed | 48,245 |
| 16 | UNDECIDED | entailed | 2,194,334 |
| 20 | UNDECIDED | entailed | 19,881,861 |
| **22** | UNDECIDED | **timeout** | 30,000,029 |
| 24 | UNDECIDED | **timeout** | 30,000,026 |

The timeout is a third answer that a caller reads as "not entailed" — **true, then false, on a strictly larger store**, and it errs in the anti-CALM direction: the replica with *more* information derives *fewer* facts.

**Measured — but only for domains whose propagator is incomplete.** Across seven classes, propagation alone decided entailment in **every** case:

```
interval, interval2, lin2var, disequality, modular, nonlinear-product, all_distinct
  -> propagation-only: DECIDED   (no labelling, no timeout, no leak)
subset-sum (low density)
  -> propagation-only: UNDECIDED (labelling required)
```

SWI's clpfd even does GCD reasoning on linear sums and factorisation on `X*Y #= 143` — my first two attempts to force a timeout were both killed by propagation.

**Measured — and the leak appears in exactly the undecided case.** The doc's own two mechanisms are instantiation-triggered: `:306-309`, a threshold claim *"suspends until that threshold is reached, then binds `X`"*, and `:251`, *"Streaming is constraint propagation to aliases."* Labelling instantiates.

```
entailment check decided by propagation : reader observed []          (no leak)
entailment check that LABELS            : reader observed [1,2,3,6,7,8]
producer woken inside the search        : 6 atoms DERIVED FROM HYPOTHETICALS,
                                          permanently in the durable store
watcher fired during the subset-sum entailment check (M=16): 2 times
```

**The constraint store itself is properly isolated** — `fd_dom(S)` was `1..10` before and after, restored exactly. The solver *is* a black box for constraint state; it is not one for coroutines and side effects, and those are the doc's wake-up mechanism.

**Measured — the obvious isolation does not isolate.** `copy_term/2` copies the frozen goal along with the attributes, so the watcher fires on the copy (4/4 labels). Only `copy_term/3` + `del_attrs` works — i.e. isolation requires **deleting the attributes**, which is the same machinery the streaming alias of `:251` is made of.

**Scope, honestly.** The design's own structures keep constraint components small: each demand carries fresh variables (`:255-258` — two queriers posting `loss(60,V)` hold **distinct** terms), so a standalone demand is a 1-variable component and a subsumption test conjoins exactly 2. Reaching 22 shared variables in one component needs a handler chain of that depth **and** an incomplete propagator. Neither hazard is reachable on any domain the corpus needs. **INFERRED** from the measured component structure, not separately measured at chain depth.

> **Corrected statement.** `:683`'s *"entailment claims are monotone"* is right semantically and, unlike the brief's hypothesis, **not** defeated by resource limits in any domain this system would use — measured, propagation alone decides entailment for intervals, linear constraints, disequality, modular, `all_distinct` and non-linear products, so no labelling occurs and neither the timeout nor the coroutine leak arises. Both appear together, and only, when the propagator is **incomplete for entailment**: measured, yes at 20 shared variables and timeout at 22 under a fixed budget, with the reader observing 6 non-entailed values and 6 hypothetical atoms landing permanently in the durable store in the same case. This is a live constraint on **Open #2** (`:889`), which currently says the domain *"must be fixed and decidable and says nothing about **which**"*: decidability is not enough. The domain must have a propagator that is **complete for entailment**, or the design acquires a third truth value in `:683` and needs a solver isolation boundary that `:251`'s streaming-by-aliasing does not permit — measured, `copy_term/2` does not provide it, because it copies the suspended goals too.

---

## What I could not break

- **Constraint-store monotonicity** — a strawman, as the brief anticipated, and confirmed as one: `fd_dom` restored byte-identically after backtracking. Reported so the budget spent on it is visible: it was small.
- **Stages 1–3 of the build.** Monotone store with set semantics, per-functor index, cursor standing calls, individual streaming. Two posts stayed two atoms; the duplicate collapsed; a constrained cursor over 200 atoms returned the right 11. Nothing merged.
- **`:634-637`'s rejection of anti-unification.** Correct, and the measured overlap data (finding 4) independently confirms *why* — generalising `loss(V,12)` and `loss(V,13)` to `loss(V,S)` inflates the cover far past the union.
- **The exactness of the pre-filter** — I tried to find an n where the cheap conditions changed the kept set and could not; identical at all five sizes.
- **`:255-258`'s call table.** The build needs it for exactly the stated reason, and the doc's honest narrowing of that claim holds up.

---

## Environment and hygiene

SWI-Prolog **10.0.0** at `~/miniconda3/envs/swipl/bin/swipl` with `library(clpfd)` — real unification, coroutining, attributed variables, and `call_with_inference_limit/3` for deterministic reproducible budgets (no wall-clock noise in any reported number). Python 3.12.9 stdlib, plus the real package: `runstate.__file__` asserted in-process as `/home/gchurchill/src/runstate/runstate/__init__.py` in every script that imported it. Corpus: the pre-existing 824-log copy in the session scratchpad, read through the public `attach_channel` + `Channel.read` API only. XSB was not needed.

**No Postgres was started.** `/tmp/rs-pg` does not exist on this machine and was never referenced; port 55432 was never contacted. **Nothing is left running** — no background jobs, no stray `swipl` or `xsb`.

**Nothing was edited.** `/home/gchurchill/src/runstate` `git status --porcelain` is byte-identical to session start (`M docs/backlog/if-built-today.md` plus six untracked review files, all pre-existing). No file under `docs/` was touched in any repo. `runstate-tui` and `learn-and-teach` are clean; `translation` (21) and `mycooc` (484) are as found — read-only, nothing written into any consumer repo. No `git add`, `commit`, `stash` or `checkout` anywhere.

**Prototypes**, all in `…/scratchpad/r6/`:

| file | what it does |
|---|---|
| `e2e.pl` | the staged end-to-end build: monotone store, per-functor index, cursor standing calls, individual streaming, constraint-carrying demands (stages 1–4) |
| `e1_probe.pl`, `e1_entail.pl` | budgeted entailment on a growing store; the two constructions propagation killed, and the one it did not |
| `e2_leak.pl`, `e2b_leak.pl`, `e2c_hard.pl` | reader/producer wake-up inside a labelling search; `fd_dom` restoration; `copy_term/2` vs `/3` isolation; the seven-class propagation-vs-labelling census; timeout and leak in the same case |
| `e3_closure.pl` | two-level closure built and exercised; both horns of the indexing dilemma |
| `e4_demand.pl` | Lens 3: producer self-triggering, memo as termination guard, the missing dependency edge, the monotone-`wants` over-count |
| `e5_subsume.pl`, `e5b_subsume.pl` | subsumption micro-benchmark and compaction at n=100…1600, three strategies, with corpus-scale extrapolation |
| `e6_overlap.py` | containment compaction vs the true minimum work |
| `e7_final.pl`, `e8_couple.pl` | memo any-vs-all ambiguity; the memo/closure coupling |
| `steps.out` | corpus step-axis and value-name census (public API) |
| `store.pl` | abandoned first draft of a general store module, superseded by `e2e.pl` |
