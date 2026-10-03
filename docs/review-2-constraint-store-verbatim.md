<!--
PROVENANCE (not part of the review). Untracked file.
Second adversarial review, commissioned 2026-08-08 against the rewritten
docs/backlog/if-built-today.md (the constraint-store / CALM / types-carry-the-semilattice version).
Supersedes review-if-built-today-{verbatim,analysis}.md, which reviewed the EARLIER schema-based
draft and no longer describe a document that exists.
The reviewer was instructed to prototype and measure rather than reason, to report findings only,
and to edit no file under docs/. Everything below is its response, verbatim.

CAVEAT FOR A LATER READER: the target doc has moved again since this was written. Its findings are
absorbed, and two of them dissolved rather than being fixed — `Dom` was a misreading (branch
reification, not a value lattice), and the whole compression apparatus was replaced by "threshold
claims always; exact claims require settledness" plus the removal of `⊤` from the value domain.
Read the doc first; treat this as a record of how it got there, not as criticism of what it says now.
-->

## VERDICT

**`Dom` does not survive.** It fails on both axes independently: it fits **0 of ~51** stored things in the four consumer repos, and — the finding that would change the design most — **a derivation over a `Dom` cell is not monotone**, which breaks CALM at the exact point the doc makes CALM load-bearing. The doc's own justification for downgrading single-spawn to "a cost mechanism" (`:52-56`) is refuted by the lattice the doc recommends in the same document.

Two further claims are unsupported as stated: the `s(3)`/`p(3)` distinction is **not reproducible in either engine** (both behave identically; the real mechanism is compression under streaming, not self-reference), and the free-variable-in-key test **classifies the doc's own flagship demand example as a read**. The 275× indexing claim survives as a number but not as an argument — I measured the term representation at **0.089 ms vs the JSONB blob's 0.085 ms**, i.e. the term buys nothing; the btree does.

What holds up: `settled = singleton = coatom` is correct as stated; `Set`/`Bag` as the default is correct and is what the corpus actually uses; the attribution/forgery split and the CALM reframing are untouched by anything I found.

---

## 1. A derivation over a `Dom` cell is non-monotone — CALM's own criterion, violated at the core. MEASURED.

**Claim tested.** `:118` `Dom(T)` join = intersection; `:125` "the shape most of the interesting cells want"; `:205-206` the continuation rule `demand(S+1) :- demand(S), value(S,V), V >= 0.1`; `:37-50` monotone ⟺ coordination-free.

**What I did.** Built the smallest thing that could work (`store.py`, 149 non-blank lines: terms, unification, `post`, one lattice, definite clauses, naive fixpoint) and ran the doc's own rule over a cell, once with `Dom` and once with `Set`, re-deriving from scratch after each post.

```
Dom(T)  -- join = INTERSECTION  (:118)
  after producer A posts 0.5    cell={0.5}                demand(1) derivable = True
  after producer B posts 0.05   cell=TOP(contradiction)   demand(1) derivable = False
  MONOTONE (answer set only grows)?  NO  <-- coordination leak

Set(T)  -- join = UNION, the free construction (:120)
  after producer A posts 0.5    cell=set{0.5}             demand(1) derivable = True
  after producer B posts 0.05   cell=set{0.05,0.5}        demand(1) derivable = True
  MONOTONE?  YES
```

The mechanism is structural, not incidental: `Dom`'s join **removes** members, and a rule body binds a variable to a member. Growing in the lattice shrinks the relation. `Dom` is monotone as a lattice and **antitone as a relation**, and derivation reads it as a relation.

**The escape route is closed too.** `:133-135` mandates constructive disjunction — "propagate only what holds in *every* disjunct". I measured that reading on a `⊤` cell:

| cell | `V >= 0.1` | `V < 0.1` | `V == 42` |
|---|---|---|---|
| `{0.5}` settled | ∀ True | ∀ False | ∀ False |
| `∅` = `⊤` | **∀ True** | **∀ True** | **∀ True** |

So: the **∃** reading is sound and non-monotone (a streamed answer must be retracted); the **∀** reading the doc mandates is monotone and **explosive** — a contradictory cell satisfies a guard *and its negation*, so every demand chain fires at once. That is *ex falso quodlibet* with a scheduler attached. There is no third reading.

**And `⊤` is reachable without any forgery.** `:53-54` says "Producing a cell twice is *sound* under a join — agreeing pushes are absorbed by idempotence, disagreeing ones land in the value order". Measured, two honest producers differing by **one ulp**:

```
a = 0.30000000000000004   b = 0.3   difference = 5.551e-17  (1 ulp)
Dom({a}) join Dom({b}) = TOP(contradiction)
```

This is not hypothetical. `/home/gchurchill/src/mycooc/analyze_run.py:1383-1387`, verbatim, hand-rolls a guard against exactly it:

> *"EMIT-ONLY-MISSING: a crash-retry episode fills gaps instead of re-emitting, so **recompute jitter** can never poison the append-only log with divergent same-cell values (which history() treats as an error, permanently)."*

Downstream, the poisoned cell silently stops the chain: `demand(1) = True` with one producer, `False` with two.

> **Corrected statement.** `:53-55` "Producing a cell twice is *sound* under a join — agreeing pushes are absorbed by idempotence, disagreeing ones land in the value order — so the claim is a **cost** mechanism in the cell plane, not a correctness one." → This holds for `Set`, `Bag`, `Max`, `Min`. It is **false for `Flat` and `Dom`**, where disagreeing pushes land at `⊤` and one ulp of recompute jitter is a permanent contradiction. Since `:125` says `Dom` is what most cells want, the claim fails for most cells *by the doc's own account*, and single-spawn is a **correctness** mechanism in the cell plane after all. And `:118`+`:205-206` cannot both stand: **a `Dom`-typed cell may not be read by a derivation rule**, because narrowing a domain is antitone in the relation the rule joins against. Either `Dom` is read-only (reports stratum), or CALM stops applying to the very rules the design is built from.

The doc says at `:270-271` that where two observers conflict "the honest answer is `∅`". Measured: `∅` is not an answer, it is a cell that either kills every consumer or satisfies every consumer.

---

## 2. `Dom` fits none of the consumer data — 0 of ~51. MEASURED (survey, two independent passes).

**Claim tested.** `:125` "`Dom(T)` deserves its own note, because it is the shape most of the interesting cells want."

**What I did.** Re-surveyed all four repos with the constructor set as the classification target, and with one literal test applied per stored thing: *does any writer ever post a set of possibilities narrower than everything but wider than one answer?*

| | mycooc | translation + l&t + tui | total |
|---|---|---|---|
| distinct stored things | 18 | ~33 | **~51** |
| FULLY-DETERMINED-SINGLE-VALUE | 16 | ~23 | **~39** |
| ACCUMULATING-SET (`Set`/`Bag`, union) | 2 | ~10 | **~12** |
| **GENUINE-PARTIAL-INFORMATION (`Dom`)** | **0** | **0** | **0** |

Mapped onto the `:113-121` constructor table:

| constructor | count | where |
|---|---|---|
| `Lex(seq, Flat)` — i.e. LWW | ~26 | every take-the-last reader; `value_series`, `_episode_stopped`, status/completion_reason registers |
| `Flat` — write-once, hand-rolled | **16 guard sites** | `main.py:250-252`, `analyze_run.py:1391-1393`, `analyze_run.py:1522`, `translation/workers.py:28,31`, `worker.py:227,347`, `run_snapshot.py:265-269,288,295,298`, `control.py:102-104`, `run_experiment.py:337-347,482-489,1563-1565`, `reclaim_experiment.py:299,326` |
| `Set`/`Bag` — union | ~12 | `input_provenance` digests, `undischarged_stops`, jsonl logs, git tag namespaces |
| `Max`/`Min` | ~4 | `progress`, `last_activity`, severity roll-up |
| `Dom` | **0** | — |
| `Herbrand`, `Prod`, no-join | 0 / 0 / 2 | — |

The three closest candidates all **fail on inspection, and each fails by design**:

- `live_episode`/`resolve(handle)` is genuinely three-valued (`True`/`False`/abstain) — and the design **forbids** storing the abstention. `observer-clock.md` §4 is quoted twice in mycooc: *"staleness never becomes a record-plane verdict"*.
- `Stopped(completed=False, error=None)` reads like "not DONE and not FAILED", but `reclaim_experiment.py:231-232` picks it *because* it is one definite member of a closed enum, not a hedge.
- `manifest["slurm_complete"]=False` literally asserts `true_set ⊇ {j1,j2}` — a real lower bound. But `ltcluster_lib.py:967-969` full-replaces rather than meets, and `run_metrics.py:290-292,1846-1853` responds by **refusing to aggregate**, never by narrowing.

The one reader in the entire corpus that reports disagreement (`graph_adapter.py:220-235`, `provenance_divergence`) unions and reports; it wants `Set`, not `Dom`. It has **no production caller**.

> **Corrected statement.** `:125` "because it is the shape most of the interesting cells want" → **zero of ~51 stored things in the four consumer repos is a narrowing domain, and none is even close.** The measured shapes are LWW (~26), write-once/`Flat` (16 hand-rolled guard sites — the strongest signal in the survey, and the one primitive the corpus is visibly missing), `Set` union (~12), and `Max` (~4). Every place partial information genuinely exists (`resolve`'s abstention, `slurm_complete`, the PBA posteriors in `translation/ignition/{pba,shift,driver}.py`), the codebase's deliberate response is to **keep it out of the store** — either by spec (`observer-clock.md` §4) or by refusing the aggregation. This is weakness (a), confirmed and strengthened: not merely unsupported, but contradicted by the whole corpus.

---

## 3. The `s(3)`/`p(3)` distinction is not reproducible. The mechanism is compression under streaming, not self-reference. MEASURED, both engines.

**Claim tested.** `:160` "Measured on SWI 10.0.0 and XSB 5.0. The mechanism is self-reference, not aggregation", with `:163` annotated *"lossy, but consistent in any order"* and `:164` *"UNSOUND: order decides the answer"*.

**What I did.** Ran the exact pair on SWI 10.0.0 (`:- table r(max)`) and XSB 5.0 (2-ary, `lattice(maxj/3)` — XSB rejects `max` and then diverges untabled, timing out at 20 s, as documented). Four clause orderings statically; then, since "which producer finished first" is an *arrival*-order claim, evaluated fresh in a separate process against the store **as it stands at each instant**.

| store state | SWI `r` | SWI `s` | SWI `p` | XSB `r` | XSB `s` | XSB `p` |
|---|---|---|---|---|---|---|
| `{0}` (producer 0 finished first) | `[0]` | **`[3]`** | **`[3]`** | `[0]` | **`[3]`** | **`[3]`** |
| `{1}` (producer 1 finished first) | `[1]` | `[]` | `[1]` | `[1]` | `[]` | `[1]` |
| `{0,1}`, 0 first | `[1]` | `[]` | `[1]` | `[1]` | `[]` | `[1]` |
| `{0,1}`, 1 first | `[1]` | `[]` | `[1]` | `[1]` | `[]` | `[1]` |

LFP baselines (SWI): untabled `r=[0,1] s=[3]`; plain-tabled `p=[3,1,0]`, so LFP-max = 3.

Three things fall out:

1. **`s` and `p` are behaviourally identical at every store state, in both engines.** Both derive `3` from `{0}`; both lose it at `{0,1}`.
2. **Neither is order-dependent at quiescence.** `p = [1]` in all four static orderings in both engines. `:164`'s "order decides the answer" is not what either engine does.
3. **`s` *is* order-dependent in transit, and `s` has no self-reference at all.** With producer 0 first, a consumer of `s` observes `3` and must later un-observe it. With producer 1 first, it never does. `:163`'s "consistent in any order" is true only of the final quiescent answer — and the design is explicitly *not* quiescent: `:81-82` "bindings arrive as they are learned", `:206` "You never retract".

Under SWI incremental tabling the order-dependence becomes final as well, repeatably over 5 runs:

```
ORDER 0-then-1  after 2nd arrival (1): r=[1] s=[] p=[0]
ORDER 1-then-0  after 2nd arrival (0): r=[1] s=[] p=[1]
```

`p=[0]` is neither the LFP answer (3) nor the static greedy answer (1).

> **Corrected statement.** `:160-168` → The distinction between the two lines is **not measurable in either engine**; both programs read the table's during-state and both lose the `3`. What separates them is soundness relative to the intended semantics — `p`'s quiescent `[1]` disagrees with LFP-under-max `[3]`, while `s`'s `[]` is exactly "read `r`'s compressed value" — **not order-dependence**. The design-relevant consequence is larger than the doc's rule: **in a streaming evaluator, any consumer of a compressing type emits answers it must later retract**, recursive or not. `s(3)` is derived when the store is `{0}` and false when it is `{0,1}`, with no cycle anywhere. So `:158` "A compressing type may not appear in its own recursion" is **necessary but not sufficient**, and the section title `:143` "it costs recursion" understates it: compression costs *every* consumer, and under CALM that means every consumer of a compressing type is coordinating. The `:171-181` "check is per query, at plan time — does a value of compressing type sit in a cycle" does not catch `s`, which is the case that breaks.

---

## 4. "A free variable in key position" classifies the doc's own demand example as a read. MEASURED (prototype).

**Claim tested.** `:87-91` "The only way to name a cell that does not exist is a free variable in **key** position. Joining relations that only contain existing facts costs nothing … That is syntactically visible in the posted term and **cannot be omitted**."

**What I did.** Implemented the test (`store.py:classify`) using the doc's own key/value reading — `:68-70` states that in `value(loss, 60, V)`, `V` is the value and `(loss, 60)` identifies the cell — and ran it on the doc's examples plus the edge cases.

| posted term | `:87-91` says | note |
|---|---|---|
| `value(loss, 60, V)` | **READ** | `:68-71` calls this *the* canonical demand |
| `value(loss, S, V)` | FORCE | free var in key |
| `value(loss, S, V) ∧ S=60` | **READ** | semantically identical to row 1 |
| `value(cfg(Lr,32), 60, V)` | FORCE | var nested inside a structured key |
| `value(M, 60, V)` | FORCE | forces every metric that could ever exist |
| `value(loss, 60, cfg(A,B))` | READ | free vars only in value position, nested |
| `K` (a bare variable) | **UNDEFINED** | no key/value split exists |
| `value(loss, 60, 0.5)` | READ | assertion |

Four defects, all measured:

- **Row 1 vs `:68-71`.** The doc's flagship demand — the one that fuses `?`, `⊥`, and "this cell is demanded" — has a **ground key**. Under `:87-91` it "costs nothing" and is a plain join over existing facts. The two passages contradict each other on the same term.
- **You cannot demand one cell.** To force, you need a free key variable; a free key variable necessarily names more than one cell. "Produce exactly `(loss, 60)`" is inexpressible.
- **The test is not stable under trivial rewriting.** Rows 1 and 3 denote the same thing and classify oppositely. `:90-91` "syntactically visible … and cannot be omitted" is exactly backwards — it can be *introduced or removed* by adding or folding an equality conjunct. Since this one test carries both the read/force distinction **and** the admission-control marker (`:241`), a consumer flips its own admission-control classification by rewriting `S=60` into the argument position.
- **The key/value split is not derivable from the term at all.** `classify` has to hard-code "the last argument is the value". Applied to real cells the corpus stores:

  ```
  status(attempt, State)       -> READ   (guessed: 1 key + 1 value)
  verdict(Outcome, FinalStep)  -> FORCE  (wrong: two value positions, no key)
  provenance(key, Prid, Sha)   -> FORCE  (wrong: two key positions)
  ```

  A per-functor arity declaration fixes it, and that declaration is precisely the semantic knowledge `:95` says the engine does not have ("nothing knows that `step` means a step").

> **Corrected statement.** `:87-91` → The syntactic test does not carry the read/force distinction. It misclassifies the doc's own demand example (`:68-70`), is not invariant under `∧ S=60`, is undefined when the term is a variable, and presupposes a key/value split that no term carries. The old two-verb interface was buying *a declaration of intent that survives rewriting*; a syntactic property of the posted term cannot replace it. If read/force is to be a property of the term, the term needs a declared key arity per functor — which reopens `:99-123` (types) as the place the distinction lives, not the syntax.

---

## 5. Terms do not fix the 275×. The btree fixes it, and JSONB takes a btree. MEASURED, Postgres 18.4, 200k cells.

**Claim tested.** `:94-97` "a term with known arity is indexable per position; an opaque JSONB key with a GIN index is not, and a range query over an axis measured **275× slower** than a btree on the same data."

**What I did.** Built three 200k-cell stores over `(metric, config, lr, step) → float`: **A** JSONB key + GIN; **B** JSONB key + btree expression index on the queried axes; **C** the doc's proposal — a term laid out one column per position, values kept semantically opaque (`jsonb`), btree on `(functor, a0, a1, a3)`. Query: the demand-driven residual, `metric` and `config` fixed, `step ≤ 60`. Best of 5, warm.

| | plan | time | buffers |
|---|---|---|---|
| A JSONB + GIN | Bitmap Heap Scan | 0.337 ms | 28 |
| B JSONB + btree on known axes | Bitmap Heap Scan | **0.085 ms** | 8 |
| **C TERM + btree per position** | Index Scan | **0.089 ms** | 8 |

**B ≈ C, within 5%.** The term representation buys nothing over the JSONB blob. The gap is between *having a btree on the queried positions* and *not having one*; both representations reach the btree, and both need to know which positions will be ranged over. `:94-97` compares the term's best index to the blob's worst.

Two further measurements sharpen it:

**GIN wins when the query moves off the btree's prefix.** With `(metric, step)` indexed and a query fixing `metric` and `config`, GIN was **30× faster** (0.331 ms / 28 buf vs 10.0 ms / 2528 buf). GIN indexes every path in one structure; a btree indexes one ordering.

**"Indexable per position" is combinatorially expensive.** Covering equality on any subset plus a range, for arity 4:

| | indexes | size | 200k inserts |
|---|---|---|---|
| GIN | 1 | 6.3 MB | 1.33 s |
| per-position btrees | 8 | **108 MB** | **7.84 s** |

**And it does not work at all once terms are heterogeneous** — which is what "domain-supplied" means. With `loss(Config,Step)`, `grad(Config,Layer,Step)`, `ckpt(Run,Config,Shard,Step)`, the step axis sits at positions 1, 2, 3. The semantics-blind positional query returned **132,879 rows; the correct answer is 91,500** — it is not slow, it is *wrong*, because it also matches `config ≤ 60` and `layer ≤ 60`. Getting the right rows requires a per-functor axis→position map, i.e. exactly the semantics `:95` disclaims.

> **Corrected statement.** `:94-97` → Delete the parenthetical. The 275× is a gap between *no suitable index* and *a btree on the queried axes*; it is not a gap between blobs and terms, and a JSONB key with a btree expression index measures **0.085 ms against the term's 0.089 ms**. Structure-transparent/semantics-opaque **does not** deliver per-position indexing over heterogeneous terms: axis-blind positional predicates return the wrong rows (132,879 vs 91,500), and axis-aware ones require the semantic map the design refuses. Terms may still be right for pattern matching — but they should be argued from `:93` ("the engine must traverse a key to match a pattern"), not from an indexing cost they do not remove.

---

## 6. Three more coordination leaks; the "reports never feed demand" rule has at least three exceptions. MEASURED / READ.

`:188-190` states flatly that reporting **may not** feed demand, and `:192-193` calls this "a structural guarantee, not a stratification analysis."

**(a) Reclamation. MEASURED — weakness (b) confirmed and deepened.**

```
with the standing query live : produce(1) derivable = True
after the lease expires      : produce(1) derivable = False
MONOTONE?  NO
```

Worse than the doc's framing: `:255-256` requires that "reclamation must not depend on receiving a message", so the trigger is a **clock**. The set of derivable facts is therefore not a function of the store at all. Every other non-monotone step in the design is at least a function of the data; this one is not, so no amount of stratification analysis reaches it.

**(b) The type check. MEASURED (prototype).** `:104` "The only obligation is never to unify values of different semilattices"; `:106` "checkable mechanically". Two agents posting the same cell key under different types:

```
agent A posts loss@60 : accepted as Max(float)
agent B posts loss@60 : REJECTED (would unify Max(float) with Dom(float))
```

Whoever posts first fixes the type. The accept/reject split is decided by arrival order, not by data — the type check *is* a first-writer-wins register. Making it order-independent means agreeing the type signature up front, which is a coordination round. `:106`'s "checkable mechanically rather than maintained by convention" is true only *given* an agreed signature; distributing the signature is the coordination CALM prices.

**(c) The residual. READ + INFERRED, but the failure chain is measured.** `:262-263` argues the residual's negation is safe "because the residual is a *message* computed from current state, never a stored relation, so it belongs to the report stratum." But the residual is *sent to the handler*, and the handler produces from it. A report with negation in it is deciding what gets produced — the definition of feeding demand. The doc treats the resulting error as benign, and it is benign only if re-production is idempotent — which finding 1 measured it is not, under `Dom` or `Flat`: a stale `E` → re-production → 1 ulp of jitter → `⊤` → the cell is dead. `:52-56`, `:258-263`, and `:118` are all wrong for the same reason.

**(d) Answer-set streaming.** Finding 3, measured. **(e) `⊤`.** Finding 1, measured.

> **Corrected statement.** `:188-193` → The table needs at least three exceptions, and one of them is not repairable by stratification: **reclamation** (clock-triggered, non-monotone, feeds demand by removing it), **the type check** (first-writer-wins on arrival order), and **the residual** (a negation-bearing message that decides production). `:192` "a structural guarantee, not a stratification analysis" is the strongest sentence in the section and the one that does not hold — the structure has holes the analysis would have found.

---

## 7. The smallest thing that could work: `Dom` breaks first, and what is left is a Prolog on a database. MEASURED + READ.

**What I built.** `store.py` — post-only, terms + unification, one lattice, definite clauses, naive fixpoint. **149 non-blank lines**, no persistence.

**What breaks, in order:**

1. **`Dom` over a continuous carrier has no representable bottom.** `⊥` = the full set of float64 = 1.845 × 10¹⁹ elements. Not a set. The only workable encoding is an interval domain — and then narrowing **never reaches a coatom**:
   ```
   after   1 narrowing posts: [0.25, 0.75]                          singleton? False
   after  40 narrowing posts: [0.49999999999954525, 0.5000000000004547]  singleton? False
   STALLED at n=54: [0.49999999999999994, 0.5] -- further posts change nothing
   ```
   `:131` "`settled` = singleton = coatom, exactly" is **correct as a lattice statement** (in reverse inclusion the coatoms are exactly the singletons, at any cardinality) but **unreachable by narrowing** on a continuous carrier. This settles `:303-306` Open 1 in the negative: `freeze` — or an equivalent "this is the last word" post — **is** needed, because for float-valued cells settledness cannot come from structure. It arrives only by a post that names the singleton directly, which is `Flat`, not `Dom`.
2. **Two producers poison the cell** (finding 1).
3. **The key/value split is undeclarable** (finding 4).

**Does the result still require this library? Weakness (c): partly confirmed, with one correction to the doc.**

`:218-221` lists exactly two jobs for the substrate: **persistence** and **indexing**. Both are a database. But I verified in-process (`runstate.__file__ = /home/gchurchill/src/runstate/runstate/__init__.py`) that `live_episode` calls `resolve()` — an OS pid probe — and the doc itself concedes at `:284-285` "You still need a handle and a probe, and it still abstains off-host." **A probe is neither persistence nor indexing.** The previous review measured the consequence: a crashed claim holder strands the run forever without it.

> **Corrected statement.** `:218-221` → The list is incomplete by the doc's own `:284-285`. The substrate does three things, not two: persistence, indexing, and **liveness resolution** — a probe that is not a database feature and is the reason the crashed-holder case recovers today. That third item is the only one of the three that is not commodity, and it is the honest answer to "why this library rather than Postgres plus a type discipline." Note also what the *other* two become: per-position term indexing over heterogeneous keys is not something a database does (finding 5) and unification is not either — so `:223-224` "the database is the durable, indexed projection of a monotone constraint store" understates the gap in the opposite direction. The buildable object is a Prolog with a durable fact base and a pid probe. That is a coherent thing to want; it is a much larger thing to build than "persistence and indexing" implies, and finding 1 says the store it would project is not monotone anyway.

---

## 8. `demand-driven-reads.md` §5a misapplies Pitman–Koopman–Darmois. MEASURED + READ. (In scope per the brief; secondary.)

`demand-driven-reads.md:119-127`, cited by `if-built-today.md:149-150` as the reason holistic aggregates forbid compression.

**The "iff" is false as stated.** PKD requires the support not to depend on the parameter. `U(0,θ)` is the standard counterexample — a one-dimensional sufficient statistic that does not grow with `n`, and not an exponential family:

```
U(0,theta) n=10      MLE from a SINGLE float (running max) = 2.408458   (theta=3.7)
U(0,theta) n=100000  MLE from a SINGLE float (running max) = 3.699940   (theta=3.7)
```

**And it is the wrong theorem.** PKD is about sufficient statistics for a *parametric family* — inference about a parameter. The median of a finite multiset is a deterministic function of the data; whether it is computable in bounded space is a streaming question (Munro–Paterson 1980), not a sufficiency one. Measured against the corpus's actual holistic aggregate — percentile 2.5/97.5 over 2000 bootstrap resamples, `mycooc/lattice_viz.py:307-312`:

| | 2.5 / 97.5 pct | state |
|---|---|---|
| exact | 0.41909320 / 0.42274876 | 2000 floats |
| 200-bucket bounded sketch, one pass | 0.41913228 / 0.42275081 | **200 floats** |

Error is **1.07% of the CI width**, at 10× less state.

> **Corrected statement.** `demand-driven-reads.md:119-127` → Drop the PKD paragraph, or restate it with the regularity condition and stop calling it "the statistical form of the same boundary" — it is a different boundary. **Gray et al.'s holistic class already carries the claim** and carries it correctly ("no constant bound on the size of the storage needed to describe a sub-aggregate"), and that is an *exact*-computation statement, which is the one the design needs. `if-built-today.md:149-150` "where Pitman–Koopman–Darmois says no bounded summary exists at all" should become "where Gray et al.'s holistic class says no bounded *exact* summary exists" — bounded *approximate* summaries do exist and are what the corpus's bootstrap CIs would accept.

---

## What I could not break

- **`settled` = singleton = coatom (`:131`)** is correct, and correct at any cardinality — every non-empty set strictly contains a singleton in inclusion, so the coatoms in reverse inclusion are exactly the singletons. The limitation is reachability (finding 7), not the identification.
- **`Set`/`Bag` as the default (`:145-155`)** is right and is what the corpus does: ~12 accumulating-set things, and the free join is the only lattice under which the doc's own demand rule stayed monotone in my measurement.
- **The attribution/forgery split and the corpus number (11 of 37 stops, 6 malformed)** — not re-tested, unchanged.
- **CALM as a reframing** — the theorem is fine. The findings above are all cases where the design fails its own criterion, not cases where the criterion is wrong.
- One small thing in the design's favour that the doc does not claim: `:158` "A compressing type may not appear in its own recursion" is decidable and cheap as stated, and my measurements support the *check*; they only show its scope is too narrow (finding 3).

---

### Environment and hygiene

SWI-Prolog 10.0.0 (`~/miniconda3/envs/swipl/bin/swipl`); XSB 5.0.0 Green Tea, slg-wam, local scheduling (`~/opt/XSB/bin/xsb`); Python 3 (stdlib only); PostgreSQL 18.4 from `~/miniconda3/bin`, on **my own cluster** at `/tmp/rvwpg/data`, socket `/tmp/rvwpg/sock`, **port 55731** — **stopped with `pg_ctl -m fast` and the directory removed**; `pgrep` confirms no Postgres of mine running. **`/tmp/rs-pg` was never present and never touched**; I never connected to port 55432. `/tmp/rvw3` (XSB working copies) removed after copying its files into the scratchpad.

`runstate.__file__` asserted in-process as `/home/gchurchill/src/runstate/runstate/__init__.py`.

**No file under `docs/` was edited.** `/home/gchurchill/src/runstate` `git status` is identical to session start (`M docs/backlog/if-built-today.md` plus the two untracked review files — all pre-existing). `learn-and-teach` and `runstate-tui` are clean; `mycooc` (475 dirty files) and `translation` (21) are exactly as found — no git write command was run in any of them.

**Prototypes**, all in the session scratchpad:

| file | what it does |
|---|---|
| `store.py` | the smallest thing that could work — terms, unification, `post`, `Dom`/`Set`, definite clauses, fixpoint, `classify` |
| `exp.py` | E1 monotonicity, E2 honest-producer `⊤`, E3 poisoned consumer, E4 read-vs-force table, E5 continuous `Dom`, E6 key/value split |
| `exp2.py` | E7 one-ulp jitter, E8 reclamation, E9 type-check order dependence |
| `exp3.py` | PKD counterexample + bounded quantile sketch vs exact bootstrap CI |
| `exp4.py` | the ∀-vs-∃ horn on a `⊤` cell |
| `t3_swi_{r,r_rev,p,p_rev,p_ruleFirst,p_ruleFirst_rev}.pl` | the `s`/`p` pair, four static clause orderings |
| `t3_base.pl`, `t3_base_p.pl` | untabled and plain-tabled LFP baselines |
| `t3_dyn.pl` | SWI incremental tabling, both arrival orders |
| `st_{r0,r1,01,10}.pl` | fresh-process evaluation per store instant (no incremental machinery) |
| `l{0,1,01,10}.P` | the XSB `lattice(maxj/3)` versions |
| `x_store{0,1,01,10}.P` | the XSB `max`-mode versions that XSB rejects and then diverges on |
| `t2_build.sql`, `t2_run.sh`, `t2_run2.sh`, `t2_run3.sh` | the 200k-cell blob-vs-term index benchmark |
