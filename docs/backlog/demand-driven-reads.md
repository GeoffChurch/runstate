# The relation as interface: demand-driven reads over a lattice-valued cell space

**Status:** a target, not a defect — the first forward-looking entry here in a while. Two of its
pieces are **buildable today, read-side, with no protocol change**, and stand alone even if the
target never lands (§5). The rest is a direction with its hard parts named.

**Origin:** the observation that a consumer doing Bayesian optimisation or a multi-armed bandit does
not want `start` and `stop`. It wants to *query a mostly-unmaterialised relation* — all configs ×
all steps × all metrics — and be handed atomic values as they arrive. The framing is **incremental
tabling** in the Prolog sense, with **answer subsumption** as the compression rule.

## 1. The shape

```
(config, step, metric) → value
```

Query it. Most of it does not exist. Querying an absent region **causes it to be produced**.

```sql
SELECT g.config, g.step, v.value
FROM   grid g                              -- the space asked about; a sweep IS a grid predicate
LEFT JOIN cells v USING (config, step, metric)
WHERE  g.lr BETWEEN 0.001 AND 0.1 AND g.step <= 60
```

**The NULLs are the demand.** SQL cannot tell you about rows you asked for that do not exist, so the
space is made explicit and absence becomes a value. Sweeps fall out of the grid predicate; stepless
producers fall out as a one-step grid.

Two properties the design must hold:

- **The cache is removable with no logical impact.** That is the definition of a correct memo: the
  handler defines the semantics, the table is an optimisation. (Semantically true, operationally
  lethal — dropping it reruns six-hour jobs. Nix's store has exactly this shape.)
- **Results stream.** The querier takes what exists, then receives cells as they land. This is what
  lets a bandit prune an arm mid-run rather than after it.

## 2. The residual must preserve structure — and this is the concrete constraint

The handler is passed *the query minus what is cached*. If that residual is a point set
(`{(c,1)…(c,100)}`) the handler must re-infer "one contiguous run to step 100." If it is a
*predicate* (`loss, config=c, step ≤ 100`) the handler knows immediately: spin one worker, run to
100.

So the query language must be **closed under subtraction while keeping its shape**. That is not
free: `Q ∧ ¬{scattered points}` degenerates into a point list exactly when the cache is patchy.

Half of it exists. The condition algebra (`vocabulary/schedule.py`, `{from, every, until}`) is a
structured description of a step set and imports nothing. What is missing is subtraction, and the
ability to ship the residual to where the data lives — which is **#15** (push log queries into the
substrate), and the serialisable-DSL question, restated as one problem: *make the query a value you
can subtract from and transport.*

**The internalised answer is magic sets** — demand as a derived relation, where the residual is not
computed and shipped but *derived in place* by demand rules. See `if-built-today.md`
§"The internalised form", which also covers why the guard disappears under it and what the safety
condition is.

## 3. Over-filling is forced, not optional

`loss@100` is **not computable** without 1…99, and they share a checkpoint. The handler's natural
granularity is a *run*; the query's is a *cell*. They will never match, so a fill for one cell
routinely produces many.

Two consequences: cache contents are **not a function of the queries** (so "what is cached?" becomes
its own query), and the streaming in §1 falls out for free, since 1…99 land before 100.

## 4. Why the run does not dissolve into the relation

The demand-driven interface can be the front door. Episodes and claims do **not** disappear behind
it; they become the machinery of the fill:

- **The claim is the cache's concurrency control.** Two queriers with overlapping residuals both see
  a cell missing and both call the handler — duplicate six-hour work, or two workers trampling one
  checkpoint. The single-spawn birth CAS is what prevents that.
- **Episodes are what make a fill restartable.** A handler that dies at step 380 and resumes from a
  checkpoint is an episode boundary by another name.
- **Negative caching is required or the system livelocks.** A cell that *cannot* be produced must be
  memoised as such, or the residual re-requests it forever. This is why `ensure` raises on a failure
  outcome instead of retrying.

So the honest statement: **the relation is the interface, the run is the unit of production, the
claim is the concurrency control.**

## 5. What is buildable now — two folds, read-side, no protocol change

Both stand alone. Neither is gated on anything above.

### 5a. Parameterise the value fold's lattice

The log is append-only, so **every** value ever written for a cell is still there. `value_series`
does not *store* one value per cell — it *chooses* one, by max-`seq`. That choice is a lattice join
(the LWW-register CRDT), and the repo already frames the plane this way:
`protocol-algebra.md` L3 calls `RunResult.outcome` *"the canonical projection of their join into a
closed **verdict lattice**"*, and L2 notes multi-writer turns Γ into a **join-semilattice**.

Making the lattice a **parameter of the read** recovers, with no substrate change:

| lattice | recovers |
|---|---|
| max-`seq` | today's last-write-wins |
| maximal elements under a domain order | **answer-subsumptive tabling** |
| identity (no compression) | the full answer set |
| "are there incomparable elements?" | divergence inspection |

**But it is two independent choices, not one, and conflating them is how you ship a median that
silently returns whatever survived compression.**

**Axis 1 — how much state may the summary hold?** This is a property of the *aggregation*, and the
boundary is old. Gray et al.'s data-cube taxonomy names three classes:

| class | example | may the free multiset be collapsed? |
|---|---|---|
| **distributive** | `max`, `sum`, `count`, LWW-by-`seq` | yes — a one-element carrier suffices |
| **algebraic** | mean | yes, to *bounded* state (`sum`, `count`) plus a finalisation step |
| **holistic** | **median**, mode, rank | **no** — the free commutative monoid must survive to read-out |

The statistical form of the same boundary is **Pitman–Koopman–Darmois** (Darmois 1935, Koopman 1936,
Pitman 1936; sufficiency itself is Fisher, 1922): a sufficient statistic whose dimension stays
bounded as the sample grows exists *iff* the family is exponential. The mean is one; the median is
not, which is why it needs the order statistics — i.e. the whole multiset. Stated as factorisation:
every aggregation is a function on the free commutative monoid, and the question is whether it
factors through a **small** quotient.

**So "identity (no compression)" in the table above is not a nicety — it is *required* for any
holistic aggregate.** A median over seeds cannot use a collapsing lattice at all.

**Axis 2 — may the program consume from the summary recursively?** This is a property of the
*program*, not the aggregation, and it is **orthogonal to axis 1**. `max` is maximally distributive
— associative, commutative, idempotent, one-element carrier — and greedy subsumption over it still
loses the least fixed point, measured on both SWI 10 and XSB 5
(`prolog-query-layer.md` §3), because the rule `p(3) :- p(X), X = 0` needs an **element**, not the
aggregate. The recursion demanded the free monoid even though the aggregation did not.

So: a median fails axis 1 regardless of any program; `max` passes axis 1 and fails axis 2. The
literature for axis 2 is `Tabling with Sound Answer Subsumption` (TPLP 2016), which supplies a
correctness *condition* and no fix.

**Design consequence.** The read parameter carries both: a Gray class (bounding what the summary may
hold) and a recursion discipline (whether derived queries may consume it). Today's LWW fold is
*distributive, non-recursive* — the safe corner of both axes, which is exactly why it is correct.

That last row of the table un-defers something deliberately deferred.
`value-plane-divergence-resolution.md` deleted the divergence raise (sticky; it blocked reuse
permanently) and parked *"attribution + a fork-surface … a forensic affordance **no consumer has
asked for**. Defer until one does."* **A bandit that must distinguish "loss at step 60" from "two
attempts disagreed at step 60" is that consumer.**

### 5b. The four-state cell projection

Unknown / success / failure / impossible are **already derivable** from records that all exist:

| state | fold |
|---|---|
| **success** | a `value` record exists at `(name, step)` |
| **failure** | the terminal is `ERRORED` / `KILLED` (or `PRESUMED_DEAD`) |
| **impossible** | terminal is **`COMPLETED`** and `step > final_step` — no more will ever come |
| **unknown** | otherwise: live, or `PREEMPTED` and resumable |

Worth stating explicitly, because it explains a shipped distinction:
**`COMPLETED` vs `PREEMPTED` *is* impossible-vs-unknown on the step axis.** Which is exactly why
`../specs/preempted-vs-completed.md` treats a premature `completed` as the dangerous error — it is a
false *impossible*, and it silently truncates `ensure`.

## 6. The tabling correspondence, including where it strains

**Where it holds.** A tabled predicate whose partially-populated table causes the engine to call a
producer for exactly the residual is this design. Answer subsumption is §5a.

**Where it strains.** Tabling assumes the producer is a pure logical goal — cheap, repeatable,
side-effect-free. Here it is a six-hour job that writes checkpoints. So: no re-derivation on demand,
negative answers must be *persisted* rather than recomputed, and §1's removable-cache property is
semantic only.

**Where it is *easier*, and this is the nice one.** Incremental tabling's hard part is
**invalidation** — a fact retracts and everything depending on it must be found and recomputed. An
append-only log has **no retraction**. Facts only accumulate, so the table is monotone and only ever
extends: the semi-naive case, free, with none of the incremental machinery.

The apparent exception — a resumed episode overwriting a cell — is not retraction either. It is a
**join** with a later element under the LWW order. Monotone in the lattice, which is precisely why
LWW was the right choice there.

## 7. Tools, honestly

No single tool does all three of query + demand hook + streaming.

| piece | closest |
|---|---|
| relation, aggregation, sweep predicates | any SQL engine; DuckDB/Polars over Parquet or Iceberg for pushdown |
| streaming as cells land | **Materialize / Feldera** natively (subscription to an incrementally-maintained view, DBSP); Postgres via `LISTEN`/`NOTIFY` (= **#16**) with more glue |
| the demand hook, receiving query *structure* | **Postgres FDW** — quals are pushed down, so the handler sees predicates, not just keys. Feature stores (Feast/Tecton) ship the same materialised/on-demand split. Shake/Bazel/Nix are `need` with the same idea |
| durable per-run identity across attempts | — |

The last row is the gap, and it is what this library is. The formal match for the whole shape is
tabled logic programming; the practical one is an FDW behind a view that subtracts the cache.

## 8. Open

1. **Residual subtraction** (§2) — the concrete blocker, and the thing #15 would have to become.
2. **Admission control.** A sweep's LEFT JOIN yields thousands of NULLs. Something must decide how
   many to produce at once and in what order. That is scheduling — layer 7 of `../layers.md`, the
   consumer's — but the structure should make demand *visible* to it.
3. **Whether §5a's lattice belongs in the fold or the caller.** A parameterised fold is one function
   with a strategy argument; the alternative is exposing the raw answer set and letting callers fold
   it themselves, which is smaller but pushes the LWW default onto every consumer.
