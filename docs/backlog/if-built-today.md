# If built today: relational, demand-driven, identity in columns

**Status:** a target sketch, written after a session in which eleven-plus mechanisms were refuted.
It is not a plan to rewrite; it is the honest answer to *"what would this look like from scratch,
and is append-only load-bearing?"* — and the answer to the second half is **mostly no**.

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
run     (run_id PK, config JSONB, ...)                    -- run_id = content hash of config
episode (episode_id PK, run_id FK, handle, status,
         final_step, started_at, ended_at)                -- status: live|completed|preempted|errored|killed
cell    (run_id, metric, step, episode_id FK, value, t)   -- every episode's values retained
halt    (run_id, set_by, reason, cleared_at)              -- run-scoped by construction
demand  (run_id, metric, until_step, requester)
```

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

## On append-only

**Keep it where history is the point; drop it as the architecture.** Episode status transitions want
an append-only `episode_event` table. Cells are immutable by construction anyway. Everything else is
better as mutable rows with identity.

The log's claimed advantages mostly do not survive scrutiny: crash-safety is what transactions are
for; streaming is `WHERE id > cursor` either way; opinion-freeness was protecting a substrate that
would not exist. What it uniquely bought is a **total order** — and that total order is precisely
what made identity positional.

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
