# If built today: relational, demand-driven, identity in columns

**Status:** a target sketch, written after a session in which eleven-plus mechanisms were refuted.
It is not a plan to rewrite; it is the honest answer to *"what would this look like from scratch,
and is append-only load-bearing?"* — and the answer to the second half is **no: the load-bearing
property is monotonicity, and append-only was only one way to get it** (§"On append-only").

## The headline

**Most of this repo's defects are self-inflicted by positional inference over an append-only log.**
Episodes are derived by reading position. Terminals pair to claims by position. Stops discharge by
position. Every one of those is a bug source, and the bugs are not exotic:

| defect | cause | under a schema |
|---|---|---|
| forged verdict silently truncating `ensure` | terminal paired to a claim **by position** | `episode_id` FK |
| the **claim cascade** — one forgery, unbounded double-live | release inferred **by position** | `status` column |
| unaimed heartbeat moving `progress` 0 → 500 | beacon attributed **by position** | `episode_id` FK |
| #39, the swallowed operator halt | discharge **by position**, author- and body-blind | `discharged_by` FK |
| the whole `episode-aim.md` cluster | re-adding the identity position lost | free |
| the run-scoped halt problem | a stop scoped to an *episode* because that is what position gives | a `halt` row |

None of them arise when episode identity is a **column** rather than an inference.

## The schema

```sql
value_cell  (key JSONB, value JSONB, ...)      -- semilattice A: flat-ish, ACC
status_cell (key JSONB, attempt INT, state)    -- semilattice B: lex, attempt at the head
demand      (query JSONB, guard JSONB, requester)
```

**One semilattice per table, and that is the organising rule.** A table is a set of cells sharing a
value domain *and its join*. Values and handler-status need **different** joins, so they are
different tables — and once that is the rule, "how many tables" is open: artifacts, configs,
provenance, each with its own order.

Making the join per-*table* rather than per-*row* is the same move as types being per-column: the
evaluator can then reason about a whole table being monotone under a known join, which per-row
functions would destroy.

**`key` is a domain-supplied record, not fixed axes.** An earlier draft wrote
`(run_id, metric, step)` — that is the ML case baked into the schema, and it is the same
workload-words-in-the-protocol mistake this repo exists to avoid. A stepless producer has no step; a
non-sweep has no config axis; another domain has axes nobody here has thought of. The system
**indexes and compares the key but never interprets it** — the opaque-`body` principle applied to
the key. JSONB with a GIN index costs nothing for this.

`run_id` survives on `episode` only because production is per-run; it is the *handler's* concept
(§"The two boundaries"), and content-addressing it from config is what makes it a cache key.

**The claim becomes a declaration**, not a protocol:

```sql
CREATE UNIQUE INDEX one_live ON episode(run_id) WHERE status = 'live';
```

Two concurrent inserts; one wins on unique violation. That *is* the birth CAS, and
`../specs/write-authority.md` largely evaporates — a displaced worker's cells still carry its own
`episode_id`, so they are attributed correctly with no aim rule, no schema bump, no migration.

The rest follows: the four-state cell projection is a join on `status`; stop discharge is an explicit
FK; `ensure` is the LEFT-JOIN-over-a-grid of `demand-driven-reads.md` with NULLs as the gap.

## What survives from runstate — all the good ideas

- **The run as a durable identity outliving its processes.** The actual contribution, now *explicit*
  rather than derived.
- **Content-addressed run ids.** Becomes the cache key, unchanged.
- **The verdict as a join of two partial observers.** A status column plus a join.
- **Cooperative, no enforcement.** Unchanged.

## What it does NOT solve — the inherent residue

Exactly what stayed hard all session, which is the tell that it is real:

- **Cross-host liveness.** No schema answers *"is that worker on `ai05` alive?"* You still need a
  handle and a probe, and it still abstains off-host.
- **The artifact plane.** Checkpoints on a filesystem remain unmodelled, and remain where a
  double-live worker's real damage lands.
- **Enforcement.** Still honour-system — though row permissions *could* enforce, which a log never
  could.

## On append-only — the real property is MONOTONICITY

Append-only is not the load-bearing thing. **Monotonicity is**, and append-only was merely one way to
obtain it. That distinction is what makes dropping the log safe, and it is a much stronger claim than
"transactions handle crash-safety."

Three places monotonicity — not the log — did the work:

1. **Incremental tabling's hard part is invalidation.** An append-only log has no retraction, so a
   derived table only ever *extends*: the semi-naive case, free, with none of the incremental
   machinery (`prolog-query-layer.md`).
2. **A resumed episode overwriting a cell looks like retraction and is not.** It is a **join** with a
   later element under the LWW order. Monotone — which is precisely why last-write-wins was the right
   resolution rather than the divergence raise that got deleted.
3. **Every demand is finite, therefore linear.** A cell settles after a bounded climb
   (`?` → failure → value), so a watch terminates and the whole linear/affine/exponential question
   dissolves. Nothing needs duplicating, so `!` has no work; nothing may-or-may-not be consumed, so
   affine has none.

**A schema keeps monotonicity by other means** — the value order on cells — so the log's contribution
was a *means*, not an end. And its unique cost stands: the total order it bought is exactly what made
identity positional.

### The design rule that falls out, and where a rewrite would go wrong

> **Every mutation must be a join.**

An in-place overwrite that loses information, a status regressing, a cell reverting to `?` — each
breaks the property everything else rests on. This is the one thing the log gave for free and a
schema must state and enforce. It is the first invariant to write down, and the first to test.

**Keep append-only where history is the point** — episode status transitions want an
`episode_event` table. Cells are immutable by construction. Everything else is better as rows whose
updates are joins.

## The two semilattices, and the constraint on both

**Values and handler-status are different semilattices**, and the reason they must be split is not
taste: **status cycles and values do not.** A value goes `⊥ → 0.2` and stays; a handler goes
`not running → running → OOM'd → running → done`. A cycle cannot live in a monotone order without
smuggling, and an earlier draft of this design smuggled it — writing `⊥ ⊑ OOM ⊑ 0.2` as though the
two were one chain. They are not; that was a state machine wearing an order.

This is the split `../backlog/protocol-algebra.md` L3 already made — *"fold observers separately;
join only at the verdict"* — rediscovered from the algebra.

**Status is monotone only when indexed by attempt.** `status(key, attempt) → state`, lexicographic
with `attempt` at the head. Attempt 1 failed *stays* failed forever; attempt 2 is a new row. The
cycle lives in the *sequence of attempts*, never in a single fact. Note where that lands: the
attempt index is `episode_id`, so the status table **is** the episode table — the same schema
reached from the algebra rather than from the defect list.

**Values need not be flat.** Non-zero-arity constructors are fine where a value genuinely refines
over time (`f(⊥)` ⊑ `f(a)`), and nothing forces an initial algebra of uninterpreted functions — any
semilattice will do.

**The constraint is not ACC.** An earlier draft required the *ascending chain condition* (no infinite
ascending chains) and derived from it that watches terminate and "every demand is finite therefore
linear." **That derivation is broken**: linearity is about how many times a demand is *consumed*, not
how much it *produces*. A demand consumed once that streams forever is still linear. Finiteness was
smuggled in and was never needed.

What is actually required is weaker and sufficient:

> **a decidable, monotone `settled` predicate** — monotone (once settled, always) and upward-closed
> (settled elements are maximal).

ACC implies it; the converse does not. So values may refine indefinitely provided completion is
*recognisable*: `[3,1,4|T]` with `T` unbound is unsettled, `T = []` makes it ground, and groundness
is decidable by traversal and monotone. That is the signal, and it costs no lattice condition.

**The mechanism has a name: `freeze`** (LVish — Kuper, Turon, Krishnaswami, Newton, *"Freeze After
Writing"*). An LVar grows monotonically under threshold reads; freezing makes it maximal and licenses
an *exact* read. Closing the tail is freezing. That literature's one nondeterminism source is a
**freeze-after-write race**, which cannot arise here because **one handler owns a cell** via the
claim — a second job for the claim, and a reason it survives into this design.

**What is lost without ACC is only optimisation.** A cell that never settles means a
`read(Q, while settled)` that never fires and never GCs — the **liveness** problem, not a lattice
problem, and better named than legislated away. And the evaluator's dormancy GC gets weaker, since it
cannot always prove a guard permanently false. Neither is a correctness issue.

**Consequence worth having: threshold reads become deterministic.** With status out of the value
domain, values are pairwise incomparable — exactly the incompatibility structure LVars require. So
"block until settled, deterministically" is available *and* retry is available, which the merged
version could not give you at once.

**Multi-table queries are then required** — the four-state projection (unknown / success / failure /
impossible) is a join across value and status. That is fine and standard: **the product of
semilattices is a semilattice**, componentwise, so a multi-table result lives in the product and
stays monotone.

*Terminology hazard:* relational **⋈** and lattice **⊔** are both called "join" and appear in the
same sentence constantly here. Worth naming them differently in any implementation.

**And a design rule that makes flatness usually right anyway: structure goes in the key, not the
value.** A partially-known record (`{loss: 0.2, acc: ⊥}`) becomes two cells keyed by metric, not one
cell with a structured order. Reach for constructors only where the *same* cell genuinely refines.

**Where `never` goes:** it is a **value**, not a status — a fact about the cell ("no value will ever
exist here"), not about an attempt. Pushing a concrete value onto a `never` cell joins to `⊤`, which
is correct: a contradiction, not a revision. And the four-state projection makes visible what the
merged version hid — **failure is a statement about an attempt, not about the cell.**

## The honest cost

A **rewrite, not a refactor**, with three consumers on the current API. And it trades a design whose
failure modes are now intimately known for one whose failure modes would have to be learned. The
DB-shaped ones are well-trodden — migrations, connection lifetime, transaction scope — but not free.

## The two boundaries, which are the real design question

The system is defined by its edges, and everything above is interior:

1. **The querier's interface** — what a bandit or optimiser sees. Cells, streamed, with status; and
   *withdrawing demand* rather than a stop verb (see below).
2. **The handler's interface** — what receives the residual query and produces cells.

**The run is born at boundary 2.** The query layer speaks *cells*; the handler is what decides a
cell-range maps to one worker with one checkpoint. So "run" is the handler's concept, "cell" is the
querier's, and the system between them owns only the cache and the demand.

**And the halt dissolves.** If production is demand-driven, a run runs *because someone wants cells*.
Stopping it is **withdrawing the demand** — which is naturally run- or demand-scoped, exactly the
scope `run-scoped-halt.md` concluded was right, and needs no record, no verb, and no discharge rule.
The existing `serve` / `retire` / `live_demand` machinery is already this shape.

### The interface, as far as it has been reasoned

```
read (Q, while Q')   →  stream of cells          -- creates NO demand
force(Q, while Q')   →  stream of cells          -- creates demand for the `?`s
push (cell, v)       →  handler deposits; the value order resolves
```

Three operations and one guard combinator. What each piece is doing:

- **`?` is a value, not an out-of-band status.** Every cell defaults to it; a query returns whatever
  is there, `?` included. So **read-only is the default and demand is opt-in**, and admission control
  and the read-vs-demand distinction are both answered by one construct. Note `?` is `⊥` of the
  *value* semilattice — handler status is a different table with a different order
  (§"The two semilattices").
- **`read` is `force` minus the demand-producing effect.** That is the rigorous difference. A
  *watcher* is `read(cell, while <it is ?>)`: one-shot, terminating, and satisfied by *someone else's*
  forcing.
- **`push` is not overwrite.** The handler deposits; the order decides. Pushing information-decreasing
  values is a no-op semantically (it is the join) and a handler bug operationally. Two incomparable
  successes have exactly two principled resolutions — add a top (`⊤` = conflict) or take the powerset
  (branch) — which are the two standard lattice completions.
- **Handlers both `push` what they produce and `force` what they depend on.** The second is what
  turns this from a two-party protocol into a build graph.
- **`while Q'` is a stopping condition, not a modality selector.** All demands are linear (see
  monotonicity above). The guard also gives *dormancy* for free — a consumed watcher cannot revive, a
  guarded one can — and needs no reification, since over a monotone store an antitone guard is
  provably dead once false, which the evaluator can reclaim as an optimisation.

The two boundaries are **dual session types**: the querier's side is *send query, receive cells*; the
handler's is *receive residual, send cells*.
