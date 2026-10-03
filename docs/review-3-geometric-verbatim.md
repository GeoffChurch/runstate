<!--
PROVENANCE (not part of the review). Untracked file.
Third adversarial review, commissioned 2026-08-08 against the geometric-logic / Scott-domain version
of docs/backlog/if-built-today.md. Supersedes review-2-constraint-store-verbatim.md, which in turn
supersedes review-if-built-today-{verbatim,analysis}.md. Each reviewed a different draft.
The reviewer was instructed to prototype and measure rather than reason, to report findings only,
and to edit no file under docs/. Everything below is its response, verbatim.
-->

# VERDICT

**Weakness (b) is confirmed, and it is the finding that should change the design most.** Measured against the real `observables.py`: **13 of 16 fold readings are non-monotone**, i.e. reports. The doc's own proposed repair — episode-indexing, `Lex(attempt, state)` — **fails on 7 of 9 folds, including within a single episode**. Meanwhile **all 9 positive-existential duals I wrote are monotone**. The derivation/report line does not fall where the doc hopes: it falls exactly on **complementation**. The geometric layer can derive `superseded`, `ended`, `discharged`, `answered`, `reached`; every fold runstate actually exports is the complement of one of those, and `channel.latest` — which the doc itself calls out as inexpressible ("**`argmax` is not expressible in derivation**") — appears **6 times directly plus 3 `[-1]`/`reversed` and 3 `max(...)`** in a 551-line module.

Three further findings, in descending order of consequence:

- **The two contradiction representations are not a free per-type choice.** MEASURED: the **flag** form *retracts a threshold claim that had already succeeded* (non-monotone, breaks CALM) and makes `contradictory(K)` **underivable**; the **forensic** form is monotone and derivable but makes one cell satisfy **two mutually exclusive constructor thresholds at once**, which the doc explicitly claims cannot happen. Neither form has both properties the design needs.
- **Angle 5's unknown, answered: `V1 ⊔ V2 undefined` is *not* a metalevel test in the logic — it is a genuine geometric formula** (verified: geometric evaluation and join-failure agree on 6/6 term pairs). But MEASURED: the rule can only *fire* in a store that **never applies the declared join**. The metalevel test is in `post()`, not in the rule.
- **`Lex(T₁,T₂)` breaks the Scott-domain claim.** Exhaustively computed: `Lex(Max(int), Flat)` joins `(1,"a") ⊔ (1,"b") = (2,⊥)` — it **fabricates attempt 2**, which nobody launched, and never records a contradiction. With a dense head it is **bounded with no lub**, i.e. not consistently complete, i.e. not a Scott domain.

Weakness (a) is confirmed with 12 cited corpus cases, and weakness (c) is confirmed and sharpened: the demand back-channel is non-monotone by *three separate doc-mandated mechanisms*.

---

## 1. Every rule the design needs, classified. Weakness (b): the derivation column is empty. MEASURED.

**Claim tested.** The derivation/report split (`:200-204`), and `:209-211` "`progress` decreasing across an episode boundary is not a violation … It is a *report*."

**What I did.** Built a real `MemoryChannel` (`runstate.__file__` asserted in-process as `/home/gchurchill/src/runstate/runstate/__init__.py`), appended records one at a time across 10 scenarios, and after **every** append recomputed each fold's answer set as a set of ground atoms. A fold is monotone iff no atom is ever lost. Two readings per fold: extensional, and the doc's own charitable **threshold** reading (`{f ≥ k : k ≤ n}`).

**Measured — `a1_classify.py`, 5 scenarios:**

| fold | reading | verdict | retractions |
|---|---|---|---|
| `latest_episode` | EXT | **REPORT** | 3 |
| `live_episode` | EXT | **REPORT** | 3 |
| `_episode_stopped` | EXT | **REPORT** | 2 |
| `_launcher_terminal` | EXT | **REPORT** | 2 |
| `peek_terminal` | EXT | **REPORT** | 3 |
| `last_activity` | EXT | **REPORT** | 22 |
| `last_activity` | THR | (survives here; dies in I, below) | 0 |
| `live_demand` | EXT | **REPORT** | 1 |
| `undischarged_stops` | EXT | **REPORT** | 3 |
| `progress` | EXT | **REPORT** | 5 |
| `progress` | **THR** | **REPORT** | 2 |
| `value_series` | EXT | **REPORT** | 1 |
| `value_series` (∃ reading) | EXT | **derivation** ✓ | 0 |
| `worker_completed` | EXT | (survives here; dies in H) | 0 |
| 4-state projection (`demand-driven-reads.md` §5b) | EXT | **REPORT** | 7 |
| 4-state, positive states only | EXT | **REPORT** | 1 |

`progress` fails **even under the threshold reading** — the concession the doc makes is not enough:

```
step 11 +lifecycle.heartbeat  NON-MONOTONE progress [THR]
      lost: [('progress>=',2),('progress>=',3),('progress>=',4),('progress>=',5)]
      now : [('progress>=',0),('progress>=',1)]
```

**The doc's own repair, measured — `a1b_episode_indexed.py`.** `:439-440`: *"`Lex(attempt, state)` puts the cycle in the **sequence of attempts**, never in a single fact — and the attempt index is the episode."* Re-read every fold as `f(ep, …)`:

| fold, episode-indexed | verdict | the case that kills it |
|---|---|---|
| `peek_terminal` | **REPORT** | **F**: within ONE episode, `launcher.terminated(exit 1)` lands first → `verdict(1,errored)`; the worker's own `lifecycle.stopped(completed=True)` lands after → `verdict(1,completed)`. **`lost: [('verdict', 1, 'errored')]`** |
| `progress` | **REPORT** | **G**: a displaced ep-1 worker beats step 41 while ep 2 is at step 5 (runstate#32) — retracts *inside* episode 2 |
| `undischarged_stops` | **REPORT** | **J**: pending → discharged inside one episode |
| `live_episode` | **REPORT** | **J**: alive → stopped inside one episode |
| `latest_episode` | **REPORT** | any new claim |
| `worker_completed` | **REPORT** | **H**: `ensure`-redrive of a COMPLETED run |
| `last_activity` [THR] | **REPORT** | **I**: clock skew — the docstring's own stated hazard, `t` non-monotone vs `seq` |
| `live_demand` | monotone here | (dies on `control.unsubscribe`, scenario C) |

Scenario **F** is the decisive one: it is not an episode-boundary effect at all. `_verdict_record` prefers `_episode_stopped` and falls back to `_launcher_terminal`, so **the verdict for a fixed attempt flips `errored → completed`** on an ordinary append. Indexing by attempt cannot fix a non-monotonicity that lives inside the attempt.

**Where the line actually falls — `a1c_duals.py`, 10 scenarios, 9/9 monotone:**

```
MONOTONE  superseded(E)   :- started(E), started(E2), E2 > E.      [dual of latest_episode]
MONOTONE  ended(E)        :- started(E), stopped(S), S > E.        [dual of live_episode]
MONOTONE  probe_dead(E)   :- started(E,H), resolve(H) = False.     [dual of resolve-alive]
MONOTONE  discharged(C)   :- stop(C), stopped(S), S > C.           [dual of undischarged_stops]
MONOTONE  answered(U)     :- subscribe(U,R), answer(A,R), A > U.   [dual of live_demand]
MONOTONE  terminal_record(S,Kind)                                  [dual of peek_terminal]
MONOTONE  reached(K)      :- heartbeat(_,S), K <= S.               [dual of progress]
MONOTONE  value_posted(N,S,V,Seq)                                  [dual of value_series]
MONOTONE  dated(Topic,T,Seq)                                       [dual of last_activity]
```

> **Corrected statement.** `:200-204`'s table, and `:209-211`. The split is real and the classification is correct, but the **derivation column contains one non-trivial member** — `value_posted`, i.e. `value_series` read existentially — **and the raw record relation**. Everything else, including every fold `observables.py` exports, is a report. The generating mechanism is not "aggregation" or "episode boundaries": it is **complementation of a positive existential**, and the operator that performs it is `latest`. `:208` already says *"`argmax` is not expressible in derivation"*; `latest = argmax(seq)` appears in `latest_episode`, `live_episode`, `_episode_stopped` (×2), `last_activity`, `undischarged_stops`, `progress`, plus `deaths[-1]`, `reversed(deaths)`, `starteds[-1]`, and three `max(...)` folds. **Weakness (b) is confirmed as stated: for the folds this repo ships, the derivation layer is a relation of raw records and the answers live in the reports.** That does not make the design worthless — a monotone record relation plus reports is a real and clean architecture — but it does mean the geometric-logic apparatus is buying the *record plane*, not the *observable plane*, and the doc should say which.

**What this does not settle.** Demand rules (`live_demand`'s dual `answered`) and the production chain are genuinely derivational; §"The internalised form"'s magic-sets story is untouched by this. The finding is about the *read* side.

---

## 2. The flag and the forensic form each break a different load-bearing claim. MEASURED (prototype `store2.py` + `a3_exp.py`).

Built the smallest thing with the **new** primitives only: partial join with **no top** (a failed join raises; it is never an element), one-way matching with suspension as a threshold read, both contradiction representations, union-find aliasing, static signatures.

**(a) The flag form retracts a threshold claim.** `:143-144` says *"Once reached it stays reached, and `X`'s binding can only refine further, so the whole thing is monotone."* `:352-354` says *"Under the flag, the value slot goes meaningless once the bit is set: no threshold claim about the value holds … **Monotone, and correct.**"* These are the same claim about the same object, with opposite answers. Measured:

```
mode=forensic   threshold value(k,f(X))  before=YES  after=YES                  RETRACTED=False
mode=flag       threshold value(k,f(X))  before=YES  after=NO-value-meaningless RETRACTED=True
```

`:352-354`'s monotonicity argument is about *revival* ("a later post agreeing with one of the vanished witnesses does not revive the cell"). It does not address *retraction*, which is what actually happens: every rule that fired on `value(k, f(X))` must un-fire.

**(b) The forensic form resurrects exactly what `⊤` was banned for.** `:307-309`: *"**And this does not resurrect `⊤`.** The contradiction lives in a *different relation*, so **no threshold claim about the *value* is satisfied by a contradictory cell** and the explosion cannot happen."* Measured on a cell holding `f(a)` and `g(b)`:

```
mode=forensic  f(A)->YES   g(B)->YES   h(C)->NO
mode=flag      f(A)->NO    g(B)->NO    h(C)->NO
```

The forensic cell satisfies **two mutually exclusive constructor thresholds simultaneously**. It is not full *ex falso* — `h(C)` still fails, which is the real content of "no `⊤`" — but the sentence as written is false for the form the doc requires wherever disagreement is the signal.

**(c) `contradictory(K)` is underivable under the flag.** Measured:

```
mode=forensic  contradictory('k') = True   (f('a') vs g('b'))
mode=flag      contradictory('k') = False  (only 0 value(s) in the store)
```

The doc concedes this at `:344-346` via the removable-cache property. What it does not say is that the flag form therefore **cannot support the rule at `:294` at all** — the rule is the doc's only account of how contradiction becomes a fact about the store rather than about history.

**(d) Alias-based failure propagation does not reach anyone in the case that matters.** `:331-334`: *"Everyone who posted a value for a cell holds a variable unified with it, so that alias set **is** the subscriber set streaming already keeps … with **no registry of 'everyone who ever wrote here.'**"* Measured with a real union-find unifier, on the doc's own one-ulp example under `Flat`:

```
clash on 'k' raised by honest-B: alias set = []  (size 0)
```

Two mechanisms, both structural: a **ground post creates no variable**, so a poster of `0.3` is not in any alias set; and a reader's variable, once bound, **derefs to a ground term forever** — `V_reader` still reads `f('a')` after the clash. The alias relation is variable→cell; notification needs cell→variables, which is a reverse index maintained per cell — precisely the registry the doc says it avoids.

> **Corrected statement.** `:336-350` presents flag-vs-forensic as a per-type cost/expressiveness choice with the same semantics. Measured, they differ on **soundness**: the flag form is **non-monotone** (it retracts a satisfied threshold) and makes `contradictory` underivable; the forensic form is monotone and derivable but makes a contradictory cell satisfy two incompatible thresholds, contradicting `:307-309`. And `:331-334`'s "the alias set *is* the subscriber set" is **false for ground posts and for readers whose variable has already been bound** — which is every `Flat` cell, i.e. exactly the type where contradiction is possible.

---

## 3. Angle 5's unknown: `V1 ⊔ V2 undefined` is geometric. The metalevel test is in `post`, not in the rule. MEASURED (`a5b_clash.py`).

**Claim tested.** `:294` and whether it smuggles a domain test into the object language.

**What I did.** Implemented `clash(V1,V2)` twice and compared on 6 term pairs: (i) **purely geometrically** — `⋁_{p, (f,n)≠(g,m) ∈ Σ} (V1 ⊒ f/n at p) ∧ (V2 ⊒ g/m at p)`, each conjunct a threshold claim `↑c`, no call to the join; (ii) by **calling the unifier** and seeing whether it failed.

```
  V1                 V2                 geo    join   agree
  f('a')             g('b')             True   True   True
  f('a')             f('b')             True   True   True
  f('a')             f(X)               False  False  True
  h('a','b')         h('a','b')         False  False  True
  h('a','b')         h('a',f('a'))      True   True   True
  f('a')             Y                  False  False  True
  ALL AGREE: True
```

**Answer to the question as posed: no metalevel test has been smuggled into the derivation layer.** `V1 ⊔ V2 undefined` is a bona fide geometric formula — finite ∧, arbitrary ∨, each atom a basic open. The doc is right, and the reason is exactly the one it gives at `:302-305`: non-joinability is stable, so it is affirmable from a finite observation (one position, two symbols).

**But the rule cannot fire in the store the doc describes.** Same two posts, two store shapes:

```
A: Set-typed value relation (never joins)
    value('k') has 2 tuples: [f('a'), g('b')]     contradictory('k') derivable: True
B: Herbrand-typed, store applies the partial join
    value('k') has 1 tuple:  [f('a')]             contradictory('k') derivable: False
```

`:57-58` says *"Agents post … The store reconciles by the value order."* A store that reconciles holds **one** value per cell, so `value(K,V1), value(K,V2)` has one solution and `V1 = V2` always. The forensic store keeps two tuples **only for clashing posts** — measured: two *joinable* posts leave `members=[] value=f(X)`, one tuple. So the body is satisfiable **exactly when `post()` already ran the join, watched it fail, and elected to retain both witnesses**. The rule is sound geometric logic reading back a decision the write path made.

Two consequences the doc does not draw:

- **The clean version of this design makes `value` `Set`-typed everywhere and derives `contradictory` in the logic** (store A above). Then the partial join has **no runtime role at all** — it becomes a *type-level* statement about what readers may claim. That is a coherent and arguably better design, and it is not the one the doc describes.
- **The disjunction ranges over the signature**, which ties this directly to weakness (a). Measured scaling: |Σ| = 2/10/100/1000 → 6/102/10 002/1 000 002 disjuncts examined. Per constructor:

| constructor | the ∨ ranges over | status |
|---|---|---|
| `Herbrand(Σ)` | positions × Σ² | finite per pair; **needs Σ enumerable** |
| `Flat(A)` | A² ∖ diagonal | `Flat(float)` → ~3.4 × 10³⁸ disjuncts |
| `Set`/`Bag`, `Max`/`Min` | — join is total — | **vacuous**: no contradiction possible |
| `Prod(T₁,T₂)` | finite ∨ over components | fine |
| `Lex(T₁,T₂)` | — join is total — | **vacuous**, and see §4 |
| `Quotient(Σ,E)` | E-unification | decidable only for unitary/finitary `E` (the doc already restricts this) |

> **Corrected statement.** `:294` is geometric and the doc's justification at `:302-305` is correct — this claim survives. What needs adding is the **precondition**: the rule is satisfiable only in a store that does *not* apply the declared join to the cells it is asked about, and its disjunction is indexed by Σ, so `:253-254`'s *"if signatures were themselves data, posted like anything else, the problem returns unchanged"* is not merely about type-checking — **the contradiction rule is not expressible without a fixed Σ either.** Weakness (a) reaches further than the doc says.

---

## 4. `Lex` takes the structure out of the Scott domains and fabricates an attempt. MEASURED (exhaustive, `a5_math.py`).

**Claim tested.** `:360-362`: *"joins for bounded subsets, a bottom, no top, is a **Scott domain** — a consistently complete algebraic cpo … 'Semilattice' was the wrong word throughout."* And `:262` `Lex(T₁,T₂) | lexicographic | e.g. status, attempt at the head`, plus `:438-440` `Lex(attempt, state)`.

**What I did.** Enumerated `Lex(Max(int 0..3), Flat({a,b}))` exhaustively and checked consistent completeness on all pairs; then repeated the head axis with exact rationals.

```
Lex(Max(int0..3),Flat(a,b)): bounded-but-no-lub pairs = 0
join of ((1,'a'), (1,'b')) = [(2,'BOT')]   <-- attempt was 1 and 1; the join INVENTS attempt 2

Lex(Max(Q), Flat({a,b})):
  upper bounds of {(1,a),(1,b)} = {(q,x) : q > 1}
  least element of {q ∈ Q : q > 1}?  11/10, 101/100, 1001/1000, ... strictly decreasing, no least: True
  => BOUNDED, NO LUB.
```

Two distinct failures:

- **Integer head:** the join is **total** — it never fails, so a `Lex(attempt,state)` cell can never be contradictory — and it discharges the conflict by **inventing attempt 2 with state `⊥`**. Posting "attempt 1 is running" and "attempt 1 was OOM-killed" yields "attempt 2, state unknown". That is a fabricated record of a launch that never happened, in a design whose headline is about **attribution defects** (`:20`). It is worse than a recorded contradiction, and it is silent.
- **Dense head:** not consistently complete, so **not a Scott domain**.

For comparison, exhaustively checked: `Herbrand(depth≤2)` and `Flat({a,b,c})` have **0** bounded-but-no-lub pairs — consistently complete, as claimed.

> **Corrected statement.** `:360-362` → the Scott-domain characterisation holds for `Herbrand`, `Flat`, `Set`, `Prod`, and for `Max`/`Min` over a discrete carrier. It **fails for `Lex`**: over a dense head `Lex` is bounded-but-lub-free (not consistently complete, not a Scott domain), and over an integer head its join is total and **manufactures a successor head value**, so `Lex(attempt, state)` — `:438-440`'s answer to status cycling — cannot record a contradiction and instead invents an attempt. Either drop `Lex` from the table or state that it is a *lexicographic presentation of a product* whose join is the product join (which fails honestly), not the lex lub.

Smaller, same section: `:279-287` *"There is no `⊤` … So joins are **partial**"* is a property of `Flat` and `Herbrand`, not of the constructor set. The doc's own default (`:266` *"The default is `Set`/`Bag`"*) is a **complete lattice with top = T**, and its join is total. The sweeping "there is no ⊤" should be scoped.

---

## 5. Weakness (a): the corpus supplies value shapes no signature could have been compiled against. MEASURED + READ.

**Claim tested.** `:238-243` *"The signature belongs to the **program**, not to the data … With static signatures a type conflict **is not expressible at runtime**."*

**Measured against the real API (`a2_typeconflict.py`):**

```
[1] one NAME, two carriers, selected by a runtime flag
  --permutations=   0  value_series['permutation'][0] = None                  (NoneType)
  --permutations= 100  value_series['permutation'][0] = {'p':0.03,'n':100}    (dict)

[3] the substrate's OWN runtime type check, exercised
  send(set) raised TypeError: Object of type set is not JSON serializable
  with json_default the SAME value is accepted at seq=1: [1, 2, 3]
```

The `permutation` case is `/home/gchurchill/src/mycooc/analyze_run.py:1391-1395`, verified verbatim:

```python
    existing = {e.name for e in channel.read(topics=["value"])}
    for k, v in bundle.items():
        if k not in existing:
            channel.send(_asdict(Value(value=v, step=0, t=_time.time())),
                         topic="value", name=k)
```

That loop does three forbidden things at once: the **functor comes from a dict key**, the **carrier varies by CLI flag** (`None` vs nested dict), and it **discovers the existing key set by reading the log** — the "first writer defines the cell" pattern `:239-241` says nobody needs.

The strongest cases from the corpus sweep, each verified against the file:

| where | what | why no static signature reaches it |
|---|---|---|
| `mycooc/runstate_emit.py:45-55` | `for k, v in metrics.items(): … topic="value", name=k` with admissibility decided by `isinstance(v,bool)` / `float(v)` in a `try` | functor = a runtime dict key; carrier decided by **probing the value's type** |
| `mycooc/training.py:1020-1022` | `mk = f"{agg_prefix}_{metric_name.value}"` | ~30 possible functors from a 15-member enum × a config bool, chosen **per experiment** |
| `mycooc/graph_adapter.py:214-217` | `input_provenance` body = `{override key → [rid, sha]}` where keys are `edge.key` from user experiment YAML (`profile_compression_evidence_path`) | the doc cites `input_provenance` at `:266-267` and `:349-350` as motivation for `Set` — **its key set is a scientist-authored YAML file** |
| `translation/ignition/workers.py:104-106, 153-156` | `name="ignition"` body gains a `Verdict` dataclass with `np.ndarray` fields iff `consensus=True` | one cell, two carriers, chosen by a keyword argument |
| `translation/workers.py:119-123` | `value={"collision":u, "var":var, **asdict(fid)}` | body key set owned by `kernelcore`, a **separate library on a separate release cadence** |
| `learn-and-teach/harness/run_snapshot.py:24-38` | `SnapshotMeta` is a `TypedDict` *because* it is `{**meta}`-splatted into a `Value` body; `outcome` "stays a plain `str`, not a `Literal`" | an in-repo, first-party argument that the carrier **cannot** be declared once |
| `runstate-tui/__main__.py:50-56` + `fold.py:108-114` | `--objective NAME` free-form, read as `object` | runstate's own third-party acceptance test takes the functor from `argv` |
| `runstate-tui/resolver.py:74-79` | manifest `attrs` validated only by `isinstance(v, str)`; row identity = `"\x00".join(f"{k}={v}" …)` | **key functor structure supplied by a user-written JSON file** |

And the substrate already concedes it: `worker.py:196` `def set(self, name: str, value: object)`; `payloads.py:51` `value: Any`; `payloads.py:34-38` *"The `name` axis stays open/app-owned"*; `worker.py:465-476` raises a bespoke runtime `TypeError` whose documented fix is **injecting a coercion function at channel construction**.

> **Corrected statement.** `:243-245` *"With static signatures a type conflict **is not expressible at runtime**"* → true of the `lifecycle.*`/`launcher.*` plane, which is genuinely closed (`Topic` is a `StrEnum`; `Stopped`/`Terminated` are frozen dataclasses with `__post_init__` validation). **False of the `value` plane**, which is where every consumer lives. There the functor is routinely an f-string, a dict key, a CLI argument or a YAML identifier, and the carrier is chosen by a runtime flag or by a third-party library's field set. `:253-254` calls the residual "real" and says *"Keep them in the program"* — measured, that is not a residual, it is the common case, and §3 above shows the contradiction rule depends on the same fixed Σ. The honest split is **two signature regimes**: static for the protocol plane, domain-supplied for the value plane — which is what `runstate` already ships and what `Topic`-closed / `name`-open already says.

---

## 6. Weakness (c): the answer path is one-way; the demand path is non-monotone by design. MEASURED (`a4_replication.py`).

**Claim tested.** `:44-47` *"**monotone** ⟹ information flows one way, no ownership protocol, no consensus, no round trips."*

Two-node simulator, at-least-once delivery, reordering, duplication, partition.

**R1 — the answer path holds.** 5 values, shuffled and duplicated: querier state monotone across every delivery. Set union absorbs reorder and duplication. **This claim survives unchanged.**

**R2 — the demand path does not.** Three doc-mandated mechanisms each retract it:

```
post demand           derivable=3  monotone
lease expiry (clock)  derivable=2  RETRACTS [('produce','loss',2)]     :219-221
self-withdrawal       derivable=1  RETRACTS [('produce','loss',3)]     :450-453
operator halt         derivable=0  RETRACTS [('produce','loss',1)]     :450-453
```

**R3 — the querier cannot tell a lost demand from a slow handler.**

```
demand DELIVERED, handler slow   querier state = [('demand','loss',60)]   store got demand = True
demand LOST (partition)          querier state = [('demand','loss',60)]   store got demand = False
```

Identical. An unsatisfied existential is the same object either way. Liveness therefore needs retry-forever (unbounded, and requires an idempotent handler — which §2(a)/(b) shows is not free under `Flat`) or an acknowledgement, a store→querier message *about* the demand rather than an answer to it.

**R5 — reconnect.** Full replay after a 6-message partition is monotone and correct at **O(whole store)** per reconnect. Bounding it requires the querier to send a cursor — querier→store state *about what was received*, which is an acknowledgement by another name.

**R4 — two producers.** Preventing duplicate six-hour production requires one producer at the claiming instant = test-and-set = consensus 2, per `../specs/write-authority.md`. That mechanism sits **on the demand path**, and CALM prices it.

> **Corrected statement.** `:44-47` → correct for the **answer** relation, and this is worth keeping. It is false for the **demand** relation, which the doc requires to be retractable in three separate places (`:219-221` reclamation-by-clock, `:450-453` self-withdrawal and the operator halt). CALM applies to demand as much as to answers, so **the design has a non-monotone relation at its centre by construction, not by oversight** — and `:226-227`'s *"a discipline with named exceptions, not a structural guarantee"* is the right register but understates the scope: it is not that reports leak into demand, it is that **demand is not monotone in the first place.** Separately, `:44-47`'s "no round trips" is a **safety** statement; measured, **liveness** and bounded reconnect cost each need a back-channel.

---

## 7. Two smaller mathematical scoping gaps. MEASURED (exact rationals).

**(a) The algebraicity claim is right, and right for the reason given.** `:147-150` *"On an **algebraic** domain the compact elements are the finite partial terms and the sets `↑p` form a **basis of the Scott topology**."* Exhaustively verified on `Herbrand(f/1,g/1,a/0,b/0)` truncated at depth 3 (|D| = 29): **0** up-closed sets fail to be rebuildable as a union of principal up-sets. Holds for `Herbrand`, `Flat`, `Set`, `Prod`. **Not a finding — this is fine.**

**(b) It does not extend to `Max(T)`/`Min(T)` over a dense carrier, and the doc applies "threshold claims are always available" universally.** Measured with exact rationals, `c = 1/10`, `D = {1/10 − 10⁻ᵏ}` directed with `sup D = c`:

```
is any d ∈ D >= c ?  False        (so c is NOT compact)
^c = [1/10,∞) contains sup(D)=1/10 but NO member of D
=> ^c is NOT Scott-open.
V > 1/10 IS affirmable in finite time: 1/5 > 1/10 = True
```

On a densely ordered carrier the only compact element is ⊥, so `{↑c : c compact}` is `{whole space}` — not a basis — and **`V ⊒ t`, the doc's canonical threshold claim (`:104-106`), is not affirmable in finite time. `V ⊐ t` is.** In today's runstate this is latent rather than live (`progress` is `Max(int)`, algebraic; `float64` is finite), but `Max(T)` is generic in `T`, and the doc's Open 1 (Smyth on the demand side) and Open 4 (streaming a partially-narrowed value) are exactly where a continuous carrier arrives — and where the doc's own n=54 bisection stall already showed up.

> **Corrected statement.** `:147-150` should carry its scope forward: `↑c`-as-basis and "threshold claims are always available" hold on **algebraic** domains, which is `Herbrand`/`Flat`/`Set`/`Prod` and `Max`/`Min` over a discrete carrier. On a continuous carrier the basis is `↑↑b` (way-above), not `↑c`, and the affirmable claim is **strict** `⊐`, not `⊒`. One sentence in the `Max(T)` row of `:256-264` fixes it.

**(c) "non-joinability is stable, joinability is not" survives.** Exhaustive counterexample hunt over all pairs and all refinements: `Herbrand(depth≤2)` **0** violations of stability (580 joinable→non-joinable, as predicted); `Flat({a,b,c})` **0** (18 as predicted); `Lex` **0**. `:302-305` is correct.

---

## What I could not break

- **The `↑c`-basis and algebraicity argument for terms** (`:147-150`) — verified exhaustively.
- **"non-joinability is stable; joinability is not"** (`:302-305`) — 0 counterexamples across three domains under all refinements.
- **"contradiction is affirmable, consistency is not"** (`:300-301`) — correct, and it is what makes the geometric `clash` formula exist.
- **The `var`/`ground` monotonicity split** (`:157-163`) — `nonvar`/`ground` are monotone, `var` is antitone; the threshold sits in the first half. The prototype's `match`/`Suspend` implements exactly this and never needed a negative instantiation test.
- **The answer path is one-way and monotone** (`:44-46`) — measured under reorder, duplication and partition.
- **`:78-85`'s refutation of the read/force syntactic test** — reads as correct, and my prototype needed a declared arity to split key from value exactly as the passage says.
- **The headline** (`:13-36`) — untested here, unchanged.

---

## Environment and hygiene

Python 3 (stdlib only: `fractions`, `itertools`, `random`, `dataclasses`) plus the real `runstate` package. `runstate.__file__` asserted in-process as `/home/gchurchill/src/runstate/runstate/__init__.py` in every script that imports it (`a1_classify.py`, `a1b_episode_indexed.py`, `a1c_duals.py`, `a2_typeconflict.py`). SWI-Prolog and XSB were **not** needed — no claim under review turned on engine behaviour this round. **No Postgres was started**: `pgrep -u $(whoami) postgres` returns nothing, `/tmp/rs-pg` does not exist on this machine and was never referenced, and port 55432 was never contacted.

**Nothing was edited.** `/home/gchurchill/src/runstate` `git status --porcelain` is identical to session start: `M docs/backlog/if-built-today.md` plus the three untracked review files, all pre-existing. `runstate-tui` and `learn-and-teach` are **clean**; `translation` (21) and `mycooc` (479) are exactly as found — no `git add`, `commit` or `stash` was run in any repo.

**Prototypes**, all in the session scratchpad:

| file | what it does |
|---|---|
| `a1_classify.py` | 16 fold readings × 5 scenarios, extensional + threshold; the primary classification |
| `a1b_episode_indexed.py` | the doc's `Lex(attempt,state)` repair, plus the cases that kill the 3 survivors |
| `a1c_duals.py` | the 9 positive-existential duals, over all 10 scenarios |
| `a2_typeconflict.py` | one name / two carriers; the `json_default` escape hatch as a data-supplied signature |
| `store2.py` | the smallest thing with the NEW primitives — no-top partial join, matching-with-suspension, flag + forensic, union-find aliases, static signatures, geometric `clash` |
| `a3_exp.py` | E1 derivability, E2 threshold retraction, E3 alias reachability, E4 Σ-scaling, E5 undeclared functor, E6 no-top explosion |
| `a4_replication.py` | two-node one-way replication: answer path, demand path, lost-demand indistinguishability, two producers, reconnect |
| `a5_math.py` | algebraicity/basis check, dense-carrier compactness, consistent completeness of the constructor table, stability counterexample hunt |
| `a5b_clash.py` | geometric vs join-based `clash` agreement; the Set-store vs Join-store firing test; the per-constructor ∨ table |
