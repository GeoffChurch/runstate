<!--
PROVENANCE (not part of the review). Untracked file.
Adversarial review of docs/backlog/if-built-today.md + docs/backlog/demand-driven-reads.md,
commissioned 2026-08-07 against branch spec/episode-aim at 7d2b70e.
The reviewer was instructed to prototype and measure rather than reason, to report findings
only, and to edit no file under docs/. Everything below is its response, verbatim.
Analysis and commentary: review-if-built-today-analysis.md
-->

Both verified verbatim. The inventory surfaced one finding strong enough to change the shape of the report.

---

# Verdict

**The headline is wrong, the design's stated first invariant is falsified by shipped code, and the value plane is over-determined into unimplementability.** Of the three defects I was asked to test, **one dies and two survive unchanged** — and the split is not random: the *attribution* class dies, the *forgery* class does not, and the headline conflates them. The claim mechanism is a net **regression** on crash recovery. Magic sets does not apply, but for a reason the document never names. The grid is not a finiteness witness for either use case that motivated the design.

There is real content: the terminal↔episode FK kills a genuine shipped bug, a `discharged_by` FK would kill a second, the freeze-as-write determinism claim holds, and "monotonicity, not append-only" is correct as a reframing. But the document generalises a narrow, real fix into a general cure it does not deliver.

---

## 1. Does the schema delete the defects it claims? — 1 of 3

Schema built verbatim from `if-built-today.md:27-31` and `:55` on Postgres 18.4 and sqlite 3.53.1. Rules of engagement from the doc itself: `:71` "Cooperative, no enforcement. Unchanged.", `:82` "Enforcement. Still honour-system", `:109` "Every mutation must be a join." Every forgery below moves `status` only *upward* (`live` → `done`) — each is a legal join under the design's own rule.

**(a) Claim cascade — SURVIVES, identically. MEASURED, both designs.**

| | table/fold says | processes believing they hold the claim |
|---|---|---|
| proposed schema, 3 forged `UPDATE`s | 1 `live` row | **4** |
| runstate today, 3 forged appends | `live_episode` → 1 holder | **4** (`[True,True,True,True]`) |

One raw `UPDATE ... WHERE run_id='R' AND status='live'` under the schema; one raw append today. **The `status` column changes where the release is written, not who may write it.** The unique index constrains the table, never the world.

**(b) Forged verdict truncating reuse — SURVIVES. MEASURED.** With the four-state projection built as `:63` prescribes, a forger releases the incumbent and inserts its own `done` row with `final_step=3`. Steps 4-10 flip `unknown` → `IMPOSSIBLE` while the honest worker climbs toward 10. Every write respects the FK; nothing is mispaired.

**(b2) The displaced worker's own terminal read as the run's verdict — DIES. MEASURED both sides.** Today: A displaced by B, A runs `w.stopped(completed=True)`, `peek_terminal` → `RunResult(outcome=COMPLETED, final_step=3)` while B still runs — `observables.py:168-177` pairs by position and A's `stopped` has the higher seq. Under the schema, A's update lands on its own attempt-1 row; `ORDER BY attempt DESC LIMIT 1` returns attempt 2, `live`. **The column genuinely kills this one.**

**(c) Misattribution — SURVIVES, or the cache breaks. MEASURED.** `:59-60` claims a displaced worker's cells "still carry its own `episode_id`, so they are attributed correctly". Two episodes push `(run,loss,step=5)`; the second push joins on the PRIMARY KEY and **overwrites `episode_id` along with the value** — LWW erasure exactly as today. Moving `episode_id` into the *key* preserves both, but then the reuse lookup returns 2 rows and the grid LEFT JOIN has no single cell. **Attribution and cache-key-ness are in direct conflict**; the doc assumes both from one table.

> **Corrected statement.** `:10` "Most of this repo's defects are self-inflicted by positional inference over an append-only log" and `:23` "None of them arise when episode identity is a **column**." → *Attribution* defects are caused by positional inference and a column deletes them — that is real, and #39 is a second instance (`observables.py:445-456` measures the author-and-body-blind discharge: **11 of 37 stops in the real corpus discharged by a record the worker did not write, 6 of them malformed**; a `discharged_by` FK would fix it). *Forgery* defects are caused by the absence of write authority; the schema does not change that, and 2 of the 3 headline examples are forgery defects.

---

## 2. Does `one_live` reproduce the birth CAS? — It reproduces the birth *mutex*, and loses crash recovery

**16 simultaneous claims, real processes, barrier-synchronised: exactly 1 winner on Postgres, 1 on sqlite. MEASURED.** No spurious errors. As mutual exclusion it works, and it removes today's observe-then-CAS TOCTOU (`worker.py:108-126`) — a real, unclaimed win.

**Crashed holder — the regression. MEASURED.**

| | after `SIGKILL` of the claim holder |
|---|---|
| proposed schema | `live` row persists; successor gets `UniqueViolation` → **run stranded forever** |
| runstate today | `live_episode` → `None`; **successor's `Worker.claimed == True`** |

`resolve()` probes the dead pid and releases the claim. The schema has no probe hook, no lease, no expiry — the only exit is a manual `UPDATE`, **which is precisely the attack-(a) forgery, now mandatory**. The prompt asked whether this strands the run "exactly as the old design did": it does not. The old design does not strand it.

**The release is raceable. MEASURED.** `UPDATE episode SET status='done' WHERE run_id='R' AND status='live'`, replayed once (timeout, reconnect, at-least-once delivery), **released a successor that had already claimed**: `[('e1',1,'done'), ('e2',2,'done')]`. `send(expected_seq=)` cannot have this bug — a replayed append with a stale `expected_seq` is provably lost and returns `None` (`channel/base.py:59-61`). The fix is `WHERE episode_id = :me AND status='live'`, i.e. a CAS. The declaration does not remove the CAS; it relocates it and leaves the unguarded spelling as the obvious one.

> **Corrected statement.** `:58-59` "That *is* the birth CAS, and `write-authority.md` largely evaporates." → A unique constraint is **test-and-set** (Herlihy consensus number 2); `send(expected_seq=)` is **compare-and-swap** (consensus ∞), cited by name at `layers.md:95`. It reproduces the birth *mutex*. `write-authority.md`'s central finding is unchanged by it; `retire()`'s death-CAS (`worker.py:321`) and the designated eliminator's `aim + expected_seq` have no analogue. It also loses the `None`-vs-raise distinction — provable loss vs indeterminate fault, which `base.py:58-62` makes normative and every backend conformance test pins.

---

## 3. Magic sets over a growing EDB — the category error is real, but not the one alleged

**The naive charge fails. MEASURED (XSB 5.0).** A deterministic, total, side-effecting oracle is just materialisation-on-access: answers `[1-10, 2-20, 3-30]`, fixpoint stable across re-evaluation on the grown EDB. Production-triggered-by-demand does not by itself void the semantics.

It breaks in three specific places:

**(i) Over-filling breaks the theorem. MEASURED: |model| = 1, |store| = 5.** With demand for step 5 only and a handler that must produce 1-5 (`demand-driven-reads.md:63-68`), the store holds 4 facts the transformed program **cannot derive** — each needs a `demand(S,T)` no rule produces. Magic sets guarantees `P^magic` derives exactly the relevant facts; here store ⊋ model. So `value` must be an EDB table read directly — **and then the demand rules are not the semantics of `value`; they are a side-effecting scheduler wearing a Datalog costume.** That is the category error, stated correctly.

**(ii) Negative caching is unstratified. MEASURED under WFS.** `demand-driven-reads.md:79-81` requires it. The program cycles `demand →¬ failed → tried → demand`:

```
cell 1 (producible): demand=undefined  failed=undefined  value=undefined
cell 2 (impossible): demand=true       failed=false      value=false
cell 3 (producible): demand=undefined  failed=undefined  value=undefined
```

Exactly backwards: the impossible cell stays permanently demanded — the livelock negative caching exists to prevent — while producible cells' demand is undecidable. The doc's round-indexed variant repairs this but downgrades the guarantee from stratified to **locally** stratified.

**(iii) The oracle is not a function. MEASURED.** `:131` makes status attempt-indexed; the same query over the same store returned `none`, then `700`. There is no single EDB to quantify the equivalence theorem over.

> **Corrected statement.** `:285-286` "range-restricted ... and **stratified**. Both decidable." → Range-restriction is decidable and vacuous here (finding 5). Stratification fails for the program that has the negative caching §4 requires; the round-indexed repair gives local stratification, not decidable in general.

---

## 4. "Every mutation must be a join" is falsified by shipped code

The doc calls this "the one thing the log gave for free and a schema must state and enforce. **It is the first invariant to write down, and the first to test**" (`:109-113`). It is already false of runstate, deliberately, in the fold the design most needs to reproduce. `observables.py:483-486`:

> "It follows that this value may **DECREASE** across an episode boundary. That is correct, not a defect to smooth over: a resumed episode genuinely rolled the frontier back, and those steps must be recomputed. **A monotone watermark here would re-open the splice it just closed.**"

`progress` is the loop condition for `ensure` (`layers.md:57-59`). A monotone `progress` reinstates the exact bug `_episode_stopped` exists to close — a preempted episode-1 terminal read while episode 2 is climbing, closing the window early and returning a spliced series **as complete, with no error and no re-drive**. The design's universal rule and this shipped non-monotone value cannot both stand; the rule needs a stated exemption for episode-relative frontiers, and the doc has none.

---

## 5. The two monotonicity fixes do not compose

**MEASURED (XSB).** Round-indexed demand (`:292-295`) plus recursive demand (`:279-282`), with `needs(x,y)` discovered by a handler *after* withdrawal:

```
after round 1 : demand=[x-1]        ACTIVE=[x]
after round 2 : demand=[x-1]        ACTIVE=[x]     <- querier withdrew x
after round 3 : demand=[y-1, x-1]   ACTIVE=[x,y]   <- needs(x,y) asserted
```

A demand withdrawn at round 2 **spawned a new sub-demand at wall-clock round 3, for a round already in the past.** Monotonicity of the relation is exactly what lets a late `needs` reach back and re-derive it.

Two consequences. First, under the doc's own definition of activity (`:271` "activity is just whether `demand(X)` is derivable now"), **withdrawal never works at all** — `ACTIVE=[x]` after round 2 — because over a monotone relation a derived fact stays derivable. Second, the repair (activity = derivable *at the current round*) makes withdrawal work but means a later-discovered `needs` can never propagate, killing the dependency graph that `:281-283` says is "the case it most needs to allow". **The T column must be a clock the evaluator advances and data the handler joins against later. It cannot be both.**

---

## 6. The grid is not a finiteness witness

**MEASURED: both cases fail to reach a fixpoint in 15 s (exit 124).**

- **Unbounded step axis.** `demand(S,T) :- grid(S), q(S,T)` with `grid` = the naturals is *syntactically* range-restricted — S is bound by a positive body literal — and does not terminate. Range-restriction is a syntactic test over a relation *asserted* finite; it does not make one finite. A run whose length is decided by convergence has no finite step grid, and bounding it in advance is exactly the enumeration demand-driven evaluation exists to avoid.
- **The optimiser/bandit.** A Bayesian optimiser or bandit picks the next config from values already seen: `grid ← value ← demand ← grid`, one SCC. The finiteness witness is *derived from the thing it is supposed to bound*. Does not terminate.

These are `demand-driven-reads.md:7-9`'s two motivating consumers. The grid is a witness only for a fixed sweep — the case where you did not need demand-driven evaluation.

---

## 7. "One semilattice per table" is not satisfiable on the real consumer data

Survey of ~20 distinct stored things across `mycooc`, `translation`, `learn-and-teach`, `runstate-tui`. READ.

| correct join | count | notable |
|---|---|---|
| LWW (genuinely) | **2** | `mycooc/runstate_emit.py:52` metrics, `:69` status register |
| **"must be identical / write-once"** | **7** | each already hand-rolls a read-before-write guard — 4 spellings of one missing primitive |
| **set-union** | 3 | `mycooc/graph_adapter.py:215` `input_provenance` |
| **no join exists** | 2 | `translation/workers.py:113-123`, `ignition/workers.py:155` |

The sharpest case is `input_provenance`: same table, same topic, same fold as everything else, but its reader (`graph_adapter.py:226-236`) reports keys where `len(shas) > 1` — **the point of the read is that the set is non-singleton**. A collapsing join makes a check its own docstring calls *"THE SAFEGUARD THAT HASHING THE RID LEAVES OPEN"* silently vacuous. `translation/workers.py:113-123` stores a *running prefix aggregate*: two writers who saw different prefixes produce values both self-consistent and mergeable by nothing. Six holistic aggregates (median, percentile rank, argmax-with-tiebreak, permutation test) are computed over these and require the free multiset, which a table storing the join cannot supply.

---

## 8. `settled` = coatomicity, and the decisive value-plane contradiction

**Freeze-as-write: the claim HOLDS. MEASURED.** freeze∘write = write∘freeze = ⊤. Confluent — LVish's determinism goal, achieved. Two caveats: determinism is bought by always taking the worst case, and `:159-160` gives the wrong *reason* the race cannot arise ("one handler owns a cell via the claim"); finding 1 measured that the claim does not deliver that, so ⊤ is the only actual protection.

**Herbrand coatomicity = groundness: HOLDS. MEASURED** (coatoms ≡ ground terms, decidable by traversal). The over-reach is `:169-171`'s "the evaluator always knows exactly when a standing query is dead ... **perfect GC**": a per-cell settledness test does not decide a query over a *range* of cells, which needs the range finite. **GC inherits the grid problem.**

**`settled` is not upward-closed. MEASURED.** `:149-151` requires "monotone (once settled, always) and upward-closed"; `:161` defines it as "maximal below ⊤". Every coatom has ⊤ above it and ⊤ is not a coatom — so freezing a cell then joining a conflicting write takes it **settled → unsettled**. Repair is one word (settled = coatomic *or* ⊤), after which "maximal below ⊤" is no longer the definition.

**The decisive one — the value lattice is over-determined.** `:182` says values are "**pairwise incomparable** — exactly the incompatibility structure LVars require", i.e. flat. `:96-97` says a resumed episode's overwrite "is a **join** with a later element under the **LWW order**". Two different lattices on one table:

- **Flat**: `join(0.50, 0.73) = ⊤`, absorbing. That is the divergent resume of `value-plane-divergence-resolution.md:24-28` landing on permanent conflict — **the sticky raise, verbatim, which that document deleted precisely because it "permanently blocked reuse" (:30-34)**. And it is *worse* than what it recreates: the raise was a read-time fold over an append-only log and came out in six lines (`:125`); a stored join has destroyed both values, so nothing is left to re-fold.
- **LWW**: reuse works, but values are a chain in `seq`, so `:182`'s determinism consequence is gone; and the chain is infinite ascending, so it is not ACC and has **no coatoms** — `settled` never fires and `read(Q, while settled)` never returns. The doc's own words for that case (`:166-167`): *"a dense domain has no coatoms at all ... nothing in it ever settles."*

> **Corrected statement.** `:28` "`value_cell ... -- semilattice A: flat-ish, ACC`" is not satisfiable together with `:96-97`'s LWW. Pick flat and you re-ship a refuted sticky failure *irreversibly*; pick LWW and you lose ACC, settledness, threshold reads and GC.

---

## 9. What the concession list misses

**"JSONB with a GIN index costs nothing for this" (`:47`) is wrong for the design's central read. MEASURED**, 200k cells, Postgres 18.4:

| query | plan | time | buffers |
|---|---|---|---|
| range on a key field (`step <= 60`) — the grid LEFT JOIN | **Parallel Seq Scan** | 14.305 ms | 2736 |
| same, with a btree expression index on the two axes | Bitmap Index Scan | **0.052 ms** | **5** |
| containment (`key @> '{...}'`) — what GIN actually serves | Bitmap Index Scan on `vc_gin` | 0.922 ms | 118 |

GIN on jsonb indexes containment, not order; `demand-driven-reads.md:20-25`'s query is a *range* over the step axis. The fix needs a btree per key field, which means knowing the axes — and `:42` "key is a domain-supplied record, not fixed axes" is what forbids knowing them. **The opaque key and the indexable grid are in direct tension.** 275×.

**Replayable reads have no replacement. MEASURED.** A table storing the join loses the divergent pair entirely (`0.50`/`0.73` → `0.73`) — which is what made the duplicate detectable (`value-plane-divergence-resolution.md:39-41`) and what `demand-driven-reads.md:100-106` needs for its *own* "identity (no compression)" lattice and "are there incomparable elements?" row. **The two companion documents ask the substrate for opposite things.** The proposed streaming replacement, `LISTEN`/`NOTIFY` (`:192`): connected subscriber received `['cell1']`; after a reconnect across two notifies, `[]`. No cursor, no retention. `read(after=seq)` is a replayable incremental read with a caller-owned cursor and no per-reader state in the substrate (`protocol-algebra.md:35-38`) — and `watcher.py:389`'s replay-from-0 is a **documented public guarantee**, not an implementation detail.

**Also dropped, unlisted:**
- **`Heartbeat.consumed_seq` is a public wire field** (`payloads.py:106`, `protocol/lifecycle-v0.4.schema.json`), consumed by `await_consumed`. Dropping the cursor breaks the wire schema, not just internals.
- **`live_demand`'s resubscribe-after-answer property** (`observables.py:381-383`) — an id-set model breaks it, and there is a test named after exactly that mistake: `test_live_demand_is_positional_not_an_id_set`.
- **`lifecycle.stopped` is read by four distinct rules with different scoping** (`live_episode` boundary, `_episode_stopped` verdict, `undischarged_stops` floor, `worker._discharge_floor`). One `status` column must preserve all four.
- **The backend tier.** Three backends; `memory.py` is 118 lines. The proposal needs partial unique indexes, JSONB, GIN, recursive CTEs, `generate_series`. `layers.md`'s layer 0 — "the only layer whose guarantees survive an uncooperative writer" — does not survive.
- **The halt does not dissolve** (`:221-223`). `run-scoped-halt.md:17-25` measures the real case: the stop is *correctly* discharged and a **scheduler** relaunches. Under demand-withdrawal the operator must withdraw *someone else's* demand, and `demand(query, guard, requester)` is per-requester. §4.1's refutation transfers intact: "the write path was never designed"; "not 'last writer sets the goal' — the writer with the fastest poll loop wins". (READ + INFERRED.)

**One point in the design's favour the doc does not make:** `observables.py:203-229` already separates windowing (positional) from attribution (content, by `request_id`), and its *stated* reason for keeping the position window is malformed-record poisoning under append-only — "an append-only log cannot retract it". A mutable schema removes that reason. That is the closest thing in the repo to a worked example of the rewrite actually paying.

---

### Environment

Postgres 18.4 on my own socket `/tmp/pgnew/sock:55499` — **stopped and removed**; `/tmp/rs-pg` was never present and never touched. `runstate` left clean on `spec/episode-aim` at `7d2b70e`; **no file under `docs/` edited**, no file in any repo modified. `runstate.__file__` asserted in-process as `/home/gchurchill/src/runstate/runstate/__init__.py` in every Python run. Prototypes in the scratchpad: `attack1.py`, `attack2.py`/`attack2d.py`, `attack7.py`, `attack8.py`, `rs_cascade.py`, `rs_crash.py`, `rs_b2.py`, `ms1.P`, `ms2.P`, `ms45.P`.
