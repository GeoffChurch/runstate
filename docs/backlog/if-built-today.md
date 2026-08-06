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
episode (episode_id PK, run_id, handle, status,
         started_at, ended_at)          -- status: live|completed|preempted|errored|killed
cell    (key JSONB, episode_id FK, value JSONB, revision)   -- every episode's values retained
demand  (query JSONB, guard JSONB, requester)
```

**`cell.key` is a domain-supplied record, not fixed axes.** An earlier draft wrote
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

- **`?` is a value, not a status channel.** Every cell defaults to it; a query returns whatever is
  there, `?` included. So **read-only is the default and demand is opt-in**, and status, admission
  control and the read-vs-demand distinction are all answered by one construct. The value domain is
  a short chain — `?` ⊑ failure ⊑ value — with `never` for definitively-absent, and incomparable
  successes resolved by the revision order.
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
