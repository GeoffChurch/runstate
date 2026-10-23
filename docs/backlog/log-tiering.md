# Log tiering — a run's log in Postgres while it is worked on, in a file otherwise

**Status: DEFERRED, with a trigger.** Ruled 2026-10-23 as GeoffChurch/mycooc's decision 12c
([review](../review-2026-10-04-commits-and-lineage.md) §12, item 15). mycooc tiers its own logs; runstate does
not, until **a second consumer wants a tier**. Then runstate builds the general form below, not mycooc's.

## Why a tier at all

A run's writers sit on several hosts: workers on cluster nodes, plus stops, reapers and claimants elsewhere.
Only `PostgresChannel` makes the compare-and-append hold across hosts (`tests/conftest.py`'s `_MAX_TIER` rates
SQLite `cross_process`). A finished run is read far more than it is written, though. mycooc wants its
finished runs readable without the coordination server (finalize on its cluster, the readouts), its run homes
self-contained for its store holder, GC and pulls, and its backups small. So it moves an ended run's log out
of Postgres into a file in the run's home (its plan, `docs/plans/2026-10-21-postgres-staged.md`, stage 3b).

## The line: `ended`

A run's log gets its file when the latest claim has ended, by its exit or a death report: layer 2's `ended`,
the predicate every fold uses and `runstate migrate` refuses a live run by. A line at `completed` was
rejected: a run stopped at its budget is `preempted` by design, so every such run would stay in Postgres for
good.

## Two forms

- **Move and re-import (mycooc's).** The rows leave Postgres. A writer to an archived run first copies the
  file back, with its seqs, and renames the file aside. **Every write must re-import first, not only a
  claim.** Both the compare-and-append and the plain append compute `MAX(seq)` over the run's rows in
  Postgres (`runstate/channel/postgres.py`, `_CAS` and the append beside it). With the rows gone, a stop, a
  late death report or a launcher record lands at seq 1 and starts a second log. Readers, who prefer the
  file, never see it.
- **Copy, don't move (the general form).** Postgres keeps every row and stays the one truth. When a run's
  latest claim ends, a read-only snapshot is written to its home for readers that must not depend on the
  server. A writer retires a stale snapshot by renaming it aside before it writes, and a new one is written
  when the run next ends. There is no copy-back and no re-import inside the claim path. The costs: Postgres
  and its backups hold all history (mycooc's 4,683 channels are 3.0 GB), and GC deletes in two places.

runstate would build the second. The first makes the substrate own a log with two homes and a copy-back in
every claim, which is the most sensitive path.

## Not per-episode logs

Splitting a log per episode as a *protocol* change is refuted
([`../dead_ends/per-episode-loglets.md`](../dead_ends/per-episode-loglets.md)): a seq per segment lets two
claims both win. As *storage* with one global seq (old episodes in files, the tail in Postgres) it is sound
only if the frontier stays in Postgres, and then every reader stitches files to rows. Layer 2's reads span
episodes (lineage reads, durable subscriptions, the answer fold, the claim path's own fold), so every claim
would read every old episode's file. Copying instead of moving gives the same independence with no stitching.
