<!--
PROVENANCE (not part of the review). Untracked file.
Fourth adversarial review, commissioned 2026-08-09 against the working-tree version of
docs/backlog/if-built-today.md (store-of-terms / no-cell / open-list-absence / geometric logic).
Supersedes review-3-geometric-verbatim.md. Each of the four reviewed a different draft.
The reviewer was instructed to prototype and measure rather than reason, to report findings only,
and to edit no file under docs/. Everything below is its response, verbatim.
-->

# Adversarial review — `docs/backlog/if-built-today.md` (working-tree version)

## VERDICT

**The open-tailed answer stream does not survive replication, and it is not a corner case — it is the general case.** MEASURED: two nodes' answer terms unify **iff they saw the identical delivery sequence**; at 5 answers that is 0.83% of orderings, at 10 answers 0/2000 random trials. Divergent-partition nodes **never** reconverge, even after every message is delivered to both. Two settled (ground) streams can disagree, so the design's own "exact claims are available at maximal elements" licenses two contradictory exact claims. The doc concedes this shape for *replicated multi-writer sets* at `:105-109` and does not notice that its own answer stream — which `:91` defines as multi-writer ("two producers disagreeing gives `[0.5, 0.4|T]`") — is one.

**Everything else I tested is closer to right than wrong.** "Dual plus subtraction" is *nearly* sufficient: all 13 inside rules I wrote are monotone and **all 9 folds reconstruct exactly**. But one of the doc's own named nine duals is the dual of a different function, and the outside half is "a subtraction" for only 5 of 9. The join-semilattice soundness claim survives exhaustive verification; the adjacent "changes no answer" does not. And on the real corpus the entire compression argument is worth **0.34%**.

Findings ordered by how much each would change the design.

---

## 1. The answer stream is the multi-writer set the doc excepts. MEASURED.

**Claim tested.** `:86-93` "*The repair is to make the answer to a standing call a **list with an unbound tail** … Answers bind the tail and reach the querier through the alias it already holds; two producers disagreeing gives `[0.5, 0.4|T]` with both delivered*", against `:105-109` "*The one place it does not reach is a replicated multi-writer set — a list fixes an insertion order and `[a,b|T]` does not unify with `[b,a|T']` … There, new members need a standing-call registry: ordinary tabling.*"

**What I did.** Built the mechanism verbatim in SWI-Prolog 10.0.0 — real unification, tails bound one answer at a time — and ran two-node simulators with reordering, duplication, partition and reconnect, plus exhaustive permutation sweeps and 2000-trial randomized schedules.

**Measured.**

|                                                  | result                                                                                                                    |
|--------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| exhaustive, k answers, all delivery orders       | unifiable pairs = **exactly k!** of (k!)² — i.e. **only identical orders unify**. k=2 50%, k=3 16.7%, k=4 4.2%, k=5 0.83% |
| randomized, n=10, 2000 trials                    | **0.0000** converged                                                                                                      |
| duplication (at-least-once)                      | `[a,b\|T]` vs `[a,a,b\|T']` **NOT unifiable**; a set absorbs it                                                           |
| partition, B merely behind (FIFO, same order)    | converges — **this case is fine**                                                                                         |
| partition, each node near its own producer       | `A=[a,c,d\|T]` vs `B=[a,d,c\|T']` — **DIVERGED, and still diverged after full reconnect**                                 |
| both close the tail (`settledness = groundness`) | `A=[x,y]`, `B=[y,x]` — two **ground, settled** terms that disagree. Exact claim `answers == [x,y]`: **yes on A, no on B** |
| alias across a link                              | node A's tail binding leaves node B's replica **unbound** — the alias does not propagate; B needs its own delivery        |

The reconnect row is the important one: divergence is **not** an information deficit. Both nodes hold every answer. It is purely representational, and it is permanent.

**The multi-writer tail is worse than non-convergent — it is a CAS loop with a lossy close.** MEASURED: after P1 binds the tail, P2's post *against the alias it holds* **fails outright**; P2 must re-walk to the current tail, which is a read-modify-write retry. And `:92-93` says "*only the producer may close it*" — singular, but `:91` posits two. Measured: once P1 binds `T = []`, P2's next answer is **rejected by unification failure**. Whoever closes first silently truncates the other producer's stream.

**Doc-internal contradiction.** `:421-422`: "*CHR's store is a multiset, and **idempotence is what makes one-way replication safe — use set semantics**.*" The answer stream is a list, i.e. an ordered multiset, i.e. neither idempotent nor order-free. The doc states the requirement and then chooses a representation that fails it.

**Minimal repair, and its cost.** MEASURED, four candidates, same harness, 2000 trials, n=8:

| candidate                                              | dup=0.0 | dup=0.1    | what it costs                                                                                                                              |
|--------------------------------------------------------|---------|------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| C0 open list (doc as written)                          | 0.0000  | 0.0000     | —                                                                                                                                          |
| C1 open list + global sequencer index                  | 1.0000  | 1.0000     | a **global sequencer** (the coordination CALM prices) *and* head-of-line blocking — the converged term is only the maximal in-order prefix |
| C2 one open list **per producer**, static producer set | 1.0000  | **0.3070** | a **statically known producer set** + a **per-producer cursor** to dedup                                                                   |
| C3 set / table of answers                              | 1.0000  | 1.0000     | absence stops being a term                                                                                                                 |

C1 is out on the doc's own grounds. C3 is the doc's own escape hatch, and its price is exact: with answers as a set, "*no answer has arrived*" is `¬∃A. answer(call,A)` — **the `¬∃` that `:86-89` exists to avoid**. (Settledness survives; only absence-as-positive dies.)

**C2 is the only repair that keeps `:86-93` intact**, because with a statically known producer set, absence becomes a **finite conjunction** — each producer's list is still `[|T]` — which is geometric. Its two costs are both real and both new: the producer set joins the sorts as a second static, deployment-time signature; and the per-producer cursor is a querier→store back-channel, needed here for **correctness**, not merely to bound reconnect as review 3 priced it. MEASURED: without the cursor, at-least-once delivery drops C2 to 0.307. And the producer registry cannot itself be an open list — MEASURED `[p1,p2|T]` vs `[p2,p1|T']` **NOT unifiable**.

**Canonicalising by sorting is not available.** MEASURED: a node holding `[b|T]` cannot be refined to `[a,b|_]` — the spine is fixed. Order-canonicalisation is not expressible by tail-binding, i.e. not expressible by the one operation.

> **Corrected statement.** `:105-109` → "*The one place it does not reach is a replicated multi-writer set*" understates its own scope. **The answer stream to a standing call is a replicated multi-writer set whenever there is more than one producer (`:91`) or more than one replica (`:44-47`), which is the case the design is for.** Measured, two nodes' answer terms unify only under identical delivery order (0/2000 at n=10), divergent partitions never reconverge even after full delivery, duplication is not absorbed, and two closed streams can be ground, settled and contradictory. Either the answer stream becomes a set — at the cost of `:86-93`, absence returns as `¬∃` — or it becomes one open list per producer over a **statically declared producer set with per-producer cursors**, which keeps `:86-93` and adds a second static signature plus a back-channel. `:421-422`'s own "use set semantics" points at the first.

---

## 2. "Dual plus subtraction": the duals are real; "a subtraction" is right for 5 of 9, and one named dual is wrong. MEASURED.

**Claim tested.** `:481-492` "*Each becomes **dual plus subtraction** — the monotone half derived inside, one complementation performed outside … All nine duals (`superseded`, `ended`, `discharged`, `answered`, `reached`, …) measured monotone, so the inside half is where the work is and the outside half is a subtraction.*"

**What I did.** Built 10 logs through the real API (`runstate.__file__` asserted in-process), wrote 13 inside rules as strictly positive geometric rules over the raw record relation, checked their monotonicity by prefix replay, then **reconstructed each fold from them** and compared to the real fold on every scenario. The outside half was instrumented with an explicit algebra (`SUB` / `EMPTY` / `AGG` / `ORACLE`) and each reconstruction rewritten to its **minimal** form (subtract the *union* of the duals, not one at a time) so the residual cost is a floor, not an artefact.

**Measured — the good half, and it is most of it.** 13/13 inside rules monotone (0 retractions across 10 scenarios). **9/9 folds reconstruct exactly, 10/10 scenarios each.** The doc's own worked example is exact: `undischarged_stops = stops − discharged` reproduces the shipped fold verbatim, `SUB=1`.

**Measured — the outside half is not uniformly "a subtraction":**

| fold                     | agree | minimal outside half                               |
|--------------------------|-------|----------------------------------------------------|
| `latest_episode`         | 10/10 | `SUB=1`                                            |
| `live_episode`           | 10/10 | `SUB=1` + `ORACLE` (the probe)                     |
| `_episode_stopped`       | 10/10 | `SUB=1`                                            |
| `undischarged_stops`     | 10/10 | `SUB=1`                                            |
| `value_series`           | 10/10 | `SUB=1`                                            |
| `progress`               | 10/10 | `SUB=1` + **`AGG=1`**                              |
| `last_activity`          | 10/10 | `SUB=1` + **`AGG=1`**                              |
| `_launcher_terminal`     | 10/10 | `SUB=2` + **`EMPTY=1`**                            |
| `peek_terminal` (record) | 10/10 | `SUB=3` + **`EMPTY=1`**                            |
| 4-state projection (§5b) | —     | `SUB=3` + **`EMPTY=1`** (inherits `peek_terminal`) |

Two distinct residuals, and they are different in kind.

**(a) An aggregate outside, on the subtraction's survivors.** `progress` and `last_activity` both end in a `max` over a set the subtraction can *shrink*, ordered by something other than `seq`. `last_activity`'s `max` is over `t`, which its own docstring notes is non-monotone against `seq`. This is the "aggregate hiding inside" the brief asked after — it hides *outside*, downstream of the complementation.

**(b) A second complementation, and it is unaffirmable.** `_launcher_terminal`'s no-episode arm is guarded by `if started is None` (`observables.py:231`) — `¬∃ started`, which no finite observation can affirm. MEASURED on the real code:

```
after the null-worker startup crash: peek_terminal = errored
a later worker claims:               peek_terminal = None   <-- the published verdict RETRACTED
```

That is not `A − B` with both monotone. It is a guard that an arriving atom flips, retracting an already-published verdict.

**(c) The doc names the wrong dual for `progress`.** `reached` appears in `:489`'s list of nine. It is `reached(K) :- heartbeat(_,S), K ≤ S` — the **high-water mark**. `progress`'s docstring is explicit that it is deliberately not that: "*NOT 'max the trajectory ever reached', and the difference is the whole point … **A monotone watermark here would re-open the splice it just closed.**"* MEASURED, reconstructing `progress` from `reached`:

```
episode rewind (ep1@40 preempts, ep2 resumes @5)   progress()=5   from reached=40   overshoot 35
displaced ep-1 worker beats 41 while ep2 is at 6   progress()=6   from reached=41   overshoot 35
```

And what that does downstream, MEASURED against `ensure`'s window test (`memoizer.py:323-335`):

```
until={'step':10}: real satisfied=False  dual-based satisfied=True   <<< ensure returns a SPLICED series as complete
until={'step':20}: real satisfied=False  dual-based satisfied=True
until={'step':41}: real satisfied=False  dual-based satisfied=True
```

That is runstate's own known splice bug, reintroduced by the repair. The **correct** dual is `beaten_hb` (the complement of "a later heartbeat exists"), which reconstructs `progress` exactly — at `SUB=1 + AGG=1`. The same trap catches `last_activity`: its threshold dual `activity_at_least(T)` over the whole log gives **990000000.0** against the fold's **110.0** on a single fast-clocked heartbeat — 11,458 days into the future, read by the GC's grace window.

**(d) Reconstruction does not make anything monotone, as expected — and that is where the price lands.** MEASURED: all 7 reconstructed outputs still retract (`latest_episode` ×4, `progress` ×6, `last_activity` ×21, …). The doc says this; the measurement makes it concrete that **every fold runstate ships pays a coordination round in its outside half**.

> **Corrected statement.** `:481-492` → the inside half is confirmed: 13/13 duals monotone, 9/9 folds reconstruct **exactly**. But "*the outside half is a subtraction*" is true for **5 of 9** folds. `progress` and `last_activity` need a subtraction **plus an aggregate over a non-`seq` order**; `_launcher_terminal`, `peek_terminal` and the four-state projection need a subtraction **plus an emptiness test on an already-complemented set** — `¬∃ started` — which is unaffirmable and, measured, retracts a published verdict when a claim arrives. Separately, `reached` in `:489`'s list of nine is the dual of the **high-water mark**, not of `progress`; reconstructing `progress` from it overshoots by 35 steps on both the rewind and the displaced-worker case and makes `ensure` return a spliced series as complete at `until={'step':10, 20, 41}`. The correct dual is the complement of "a later heartbeat exists". "*None of them is mechanical*" (`:492`) is, if anything, understated: one of the nine published duals is for a different function.

---

## 3. What `ensure` becomes: a report-driven control loop with two mechanisms the logic does not supply. MEASURED.

**Claim tested.** Not stated in the doc — `ensure` is untouched by three reviews. `:95-98`'s "*a demand is never 'satisfied' in a way that ends it*" and `:430-431`'s "*finiteness of what you asked for — the requester's obligation*" are the nearest load-bearing statements.

**What I did.** Instrumented the real `ensure` with a real producer over a real `MemoryChannel`, tapping every observable call, across three cases.

**Measured — every decision `ensure` makes:**

| case                            | result            | decisions                                                                                         |
|---------------------------------|-------------------|---------------------------------------------------------------------------------------------------|
| clean run to 10                 | 10 points         | 4× `progress`, 1× `peek_terminal`, 1× `worker_completed`, 1× produce                              |
| preempt at 4, resume to 10      | 10 points         | 8× `progress`, 2× `peek_terminal`, 2× `worker_completed`, 2× produce, 1× probe                    |
| own spawn dies with no progress | `NoProgressError` | 7× `progress`, 2× `peek_terminal`, 2× `worker_completed`, 1× produce, 1× probe, 1× `live_episode` |

So the loop condition is a threshold claim on `progress` — which §2 measures as **`SUB=1 + AGG=1`, retracting ×6**. `ensure` gates on a *report*, not on a monotone value. `:104-106`'s "*Monotone by construction: values only go up, so once true, always true*" does not cover it, and the docstring says it must not: the frontier is *supposed* to go down.

**Measured — the two decisions that are not reads of the store at all** (all `FOUND` in `memoizer.ensure` source):

- `before = _progress(channel)` … `_progress(channel) <= before` — a **temporal delta**: the same fold compared at two times. Over a monotone store this is "*nothing new was derived*", which has no positive form.
- `RecordlessExitError`'s own docstring: "*raises at the FIXED POINT: one full cycle against the same recordless death that left `progress` where it was. Identical inputs, identical outputs — another lap can only reproduce them.*" That is **inflationary fixpoint detection** — a survey of everything there is, i.e. exactly the coordination round `:204-207` prices.
- plus a clock, an OS probe, and an unbounded retry loop.

**What it becomes.** `ensure(until={"step":N})` is "post `loss(0..N-1, V)` as an unsatisfied existential and wait for the tail to close". `:150-152` forbids the obvious shortcut: "*nothing may derive settledness from demand going quiet … only whoever is producing the answers may close the tail.*" So a crashed producer never closes, and `ensure` blocks forever. Today it escapes by the probe (which the doc keeps, `:409-413`) **and** by the temporal delta and the fixpoint test (which it does not name anywhere).

> **Corrected statement.** `ensure` survives, but not as derivation. Its loop condition is a threshold claim on a **retractable** quantity, and its two termination guards — the no-progress delta and the recordless-exit fixed point — are a temporal difference and an inflationary fixpoint test respectively. Neither is geometric and neither is a "report" in the doc's sense, because both **feed demand** (they decide whether to relaunch). `:224-225` names one exception to the derivation/report split, the residual; measured, **`ensure` is a second, and it is the library's core operation.** The doc's list of what the substrate is for (`:407-413`: persistence, indexing, liveness probe) is missing the fourth thing `ensure` actually needs: an *observation that nothing changed*.

---

## 4. The join-semilattice quotient loses nothing and gains something — and on the real corpus the whole argument is worth 0.34%. MEASURED.

**Claim tested.** `:296-300` "***Any join-semilattice may be quotiented at the storage layer, freely and soundly.*** Sound because `a ⊔ b ⊒ a` and `⊒ b`, so no threshold claim either post satisfied is ever lost"; and `:365-368` "*A relation whose order is a join-semilattice may additionally be quotiented at storage … but that is a compression choice and **changes no answer***"; and `:476-477` "*storage grows wherever the order is partial*".

**What I did (soundness).** Enumerated **every** finite join-semilattice on 3 and 4 elements and tested every `(a, b, threshold)` triple: does quotienting `{a,b}` to `a ⊔ b` lose a satisfied threshold claim, or add an unsatisfied one?

```
n=3: (is-chain, adds-claims, loses-claims) -> count      n=4:
     (False, True,  False) -> 3                               (False, True,  False) -> 52
     (True,  False, False) -> 6                               (True,  False, False) -> 24
LAW: adds-claims <=> not-a-chain : True                  LAW: adds-claims <=> not-a-chain : True
LAW: never loses a claim         : True                  LAW: never loses a claim         : True
```

**The doc's soundness argument is exactly right and survives without amendment.** But the quotient is not *conservative*. Concrete, on the doc's own `Set` type: posts `{0}` and `{1}`; threshold `V ⊒ {0,1}` — **free: NO, quotient: YES**. 36 such added claims on `Set` over a 3-element carrier; 200 on `Prod(Max,Max)` 4×4; **0 on `Max(int)`**, because it is a chain.

**What I did (compression).** Copied **823 real runstate SQLite logs** out of `mycooc/outputs/runs` into scratchpad and read them through the public `attach_channel` API. 819 readable, **2,512,417 records**, 2,390,016 of them on the value plane.

```
free completion (keep every atom): 2,390,016 atoms
quotiented (one atom per cell)   : 2,381,914 atoms
compression                      : 0.339%   (saves 8,102)
whole-log compression            : 0.322%
runs with >1 episode             : 51 of 819

runs with >1 episode      compression = 2.394%
runs with <=1 episode     compression = 0.183%
cells with >1 DISTINCT value (the genuinely partial-order case): 1,719  = 0.072% of cells
```

**Answering the brief's question directly: "storage grows where the order is partial" is a footnote — 0.072% of cells.** But the same measurement cuts the other way, and harder: **the compression the design gives up is 0.34%.** The `⊤`-versus-powerset dilemma at `:306-311`, the "cost is real but bounded" at `:311-312`, and the "storage grows wherever" concession at `:476-477` are all arguing over a third of one percent of a real corpus.

Where the partial-order case *does* land is instructive and supports the design: the 1,719 divergent cells are `status` (1,714) and `completion_reason` (5) — an app event mirrored onto the value plane at a reused step, e.g. one cell holding `{'phase':'training','status':'aligning step 87/100'}` and `{'phase':'saving','status':'saving results'}`. LWW picks one and reports the run as *saving* at step 87. That is precisely the "no cell, two atoms" case, and keeping the atoms is right.

> **Corrected statement.** `:296-300` survives — verified exhaustively, **0 claims lost over every finite join-semilattice on 3 and 4 elements**. `:365-368`'s "*a compression choice and **changes no answer***" is **false**: the quotient **adds** a threshold claim exactly when the semilattice is **not a chain** (verified as an iff, 3/3 at n=3 and 52/52 at n=4). Of the doc's own compressible orders, `max`/`min`/LWW are chains and safe; `Set` and any product are not, and there a quotient makes `V ⊒ {a,b}` true when no post satisfied it. The sentence needs "changes no answer **on a chain**", or `:296` needs to say *totally ordered*, not *join-semilattice*. And `:311-312`'s "*the cost is real but bounded*" should carry the measured number in both directions: on 823 real logs the free completion costs **0.34%** and the partial-order cells are **0.072%** — the compression discussion is a footnote, not a design axis.

---

## 5. The memo check is not an optimisation: it decides the answer set. MEASURED.

**Claim tested.** `:95-97` "***Production is gated on demand, never on absence*** — the evaluator's memo check ('do I already have answers?') is an optimisation over a data structure, not a rule in the logic."

**What I did.** Built both evaluators in SWI-Prolog (untabled demand-gated vs `:- table`), then repeated the experiment on the **real `ensure`** with the absence gate removed and everything else identical.

**Measured, Prolog:**

| producer                       | demand-gated                       | memo-checked                       | agree?                    |
|--------------------------------|------------------------------------|------------------------------------|---------------------------|
| exactly reproducible           | answers `[6.0]`, **5** invocations | answers `[6.0]`, **1** invocation  | same answer set, 5:1 cost |
| not exactly reproducible       | **5** answers, 5 invocations       | **1** answer, 1 invocation         | **DIFFERENT ANSWER SETS** |
|                                | `conflicted(60)` **derivable**     | `conflicted(60)` **not derivable** |                           |
| 100 polls of one standing call | **100** producer runs              | **1**                              |                           |

**Measured, the real `ensure`:**

```
20 ensure() calls, same window:
  memo-checked (shipped)  ->  1 producer run,  log =   102 records
  demand-gated (removed)  -> 20 producer runs, log = 2,040 records

with re-production jitter, 5 calls:
  memo-checked  distinct values at step 0: [0.500001]                         conflicted = False
  demand-gated  distinct values at step 0: [0.500001 … 0.500005]              conflicted = True
```

An optimisation must not change the answer set. This one does: it decides whether `conflicted(K)` — the doc's one named affirmable predicate (`:254-256`) — holds.

**And the standing call needs the registry the doc says it avoids.** MEASURED: two queriers independently posting `loss(60,V)` hold **distinct** terms with distinct tails, so one producer answer does not satisfy both; the producer must bind both tails, which requires it to know both calls exist. The two calls are `variant/2`-equal, i.e. they share a table key. `:104-105`'s "*no subscriber registry at all*" therefore needs a set of outstanding calls keyed by skeleton — which is the call table, which is the registry.

**One honest counterweight.** `:288-291` says "*conflict is reachable without forgery … two honest producers differing by one ulp do not reconcile*", citing mycooc's hand-rolled guard. MEASURED on the corpus: of 3,165 re-posted **numeric** cells, **0 diverged**. The hazard is real, but the guard the doc cites is measurably working — the doc's own framing of that sentence is accurate, and I could not make it bite on real data.

> **Corrected statement.** `:95-97` → "*the memo check … is an optimisation over a data structure, not a rule in the logic*" is true only when production is exactly reproducible. Measured on the real `ensure`, removing it costs **20 producer runs instead of 1** for 20 calls of the same window (one run = a six-hour job, per `demand-driven-reads.md` §1's "operationally lethal"), and when re-production does not reproduce it **changes the answer set and flips `conflicted(K)` from false to true**. It is not an optimisation; it is the semantics of `ensure`. Relatedly, `:104-105`'s "*no subscriber registry at all*" holds only for a single standing call: two `variant`-equal calls need the producer to bind both tails, which requires a set of outstanding calls keyed by skeleton — the call table.

---

## What I could not break

- **`:296-300`, the quotient's soundness.** Exhaustive over every finite join-semilattice on 3 and 4 elements: **0 threshold claims lost, ever.** The `a ⊔ b ⊒ a` argument is correct as given.
- **The inside half of `:481-492`.** 13/13 duals monotone; 9/9 folds reconstruct **exactly** on 10/10 scenarios. The claim that the monotone half can be derived inside is real, not aspirational.
- **`:487-488`'s worked example.** `unhandled = stops − discharged` reproduces the shipped `undischarged_stops` verbatim, one subtraction.
- **`:288-291`, conflict without forgery.** Real, but the cited mycooc guard is effective — 0/3,165 numeric re-productions diverged on the corpus.
- **Prefix-preserving replication of the open list.** A node that is merely *behind* on a FIFO link converges, during and after the partition. The failure is specific to divergent arrival order, not to lag.
- **`:44-47`'s answer-path claim**, in the set reading — re-confirmed incidentally (C3 converges 1.0000 under reorder *and* duplication). It is the *list* reading that fails.

---

## Environment and hygiene

SWI-Prolog **10.0.0** at `~/miniconda3/envs/swipl/bin/swipl` (real unification and tabling for lenses 1 and 3). Python 3.12.9, stdlib only, plus the real package — `runstate.__file__` asserted in-process as `/home/gchurchill/src/runstate/runstate/__init__.py` in every script that imports it. XSB was not needed. **No Postgres was started**: `pgrep -u $(whoami) postgres` returns nothing, `/tmp/rs-pg` does not exist on this machine and was never referenced, port 55432 was never contacted. Nothing is left running; no background jobs.

**Nothing was edited.** `/home/gchurchill/src/runstate` `git status --porcelain` is byte-identical to session start: `M docs/backlog/if-built-today.md` plus four untracked review files, all pre-existing. `runstate-tui` and `learn-and-teach` are **clean**. `mycooc` (485) and `translation` (21) are as found — I only *read* from them and `cp`'d 823 `.db` files **out** to scratchpad; nothing was written into any consumer repo. No `git add`, `commit` or `stash` was run anywhere.

**One thing left on disk to note:** the corpus copy — 340 MB at `…/scratchpad/corpus/` (823 `.db` files copied from `mycooc/outputs/runs`). It is inside the session scratchpad, not the project, and is only there so the measurements re-run; delete it freely.

**Prototypes**, all in the session scratchpad:

| file               | what it does                                                                                                                         |
|--------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `p1_openlist.pl`   | E1–E8: exhaustive permutation unification, duplication, partition, close-to-ground, sorted-insertion, per-producer, set              |
| `p1b_twonode.pl`   | randomized two-node simulator; partition/reconnect; the multi-writer tail and its close race; alias-across-a-link                    |
| `p1c_repair.pl`    | the four repair candidates under one harness, 2000 trials each                                                                       |
| `p2_duals.py`      | fact base + 13 geometric inside rules + first-cut reconstruction of 10 folds over 10 real-API scenarios                              |
| `p2b_minimal.py`   | minimal-form outside half, inside-rule monotonicity, reconstructed-output monotonicity, four-state projection                        |
| `p2c_sharp.py`     | `reached`-vs-`progress` overshoot, its effect on `ensure`'s window test, `last_activity`'s dual, the unaffirmable `¬∃ started` guard |
| `p3_demand.pl`     | demand-gated vs memo-checked: answer sets, invocation counts, `conflicted` derivability, call-table variance                         |
| `p3b_memo_real.py` | the same on the real `ensure` with the absence gate removed                                                                          |
| `p4_quotient.py`   | quotient conservativity on `Set`/`Max`/`LWW`/`Prod`; corpus compression over 823 logs                                                |
| `p4b_chainlaw.py`  | exhaustive over every finite join-semilattice (n=3,4); compression concentration; real value conflicts                               |
| `p5_ensure.py`     | corpus re-production idempotence; instrumented anatomy of every decision `ensure` makes                                              |
