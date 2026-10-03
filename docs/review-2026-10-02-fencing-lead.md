# The fencing lead: commands out of the worker's stream

**Status:** a lead, untested. Surfaced 2026-10-02 by a prior-art survey (event-sourced actors) and a
second survey (durable execution). Written down so independent adversaries attack the same thing.

## Background

runstate (repo `GeoffChurch/runstate`, checkout /home/gchurchill/src/runstate) keeps **one append-only
log per run**. Everyone writes into it: the worker (`lifecycle.started/heartbeat/stopped`, `value`),
orchestrators and operators (`control.subscribe/unsubscribe/stop`), launchers (`launcher.launched/
terminated`), and third parties (e.g. a consumer's reclaim tool writing `lifecycle.stopped` to release a
stranded claim). The substrate offers `send(..., expected_seq=S)`: append iff the log's last seq is `S`.

Only two writes use it: the **birth claim** (`lifecycle.started`) and the **death** (`retire()`). So the
guarantee is "at most one claimant **at the instant of claiming**" — a displaced worker that is still alive
keeps writing after its successor's claim, and nothing stops it (`docs/specs/write-authority.md`, issue
#32). That spec refutes three remedies (revision 1: promote the Postgres lock; revision 2: an
epoch-fenced append, refuted five ways; revision 3: per-tick displacement detection in the Worker, refuted
seven ways) and explains "why fencing tokens are not available here": acquiring the claim is itself an
append, so it cannot be fenced without a fifth substrate operation or topic routing in the substrate.

## What the surveys observed

- **Event-sourcing systems keep commands out of the journal.** Akka Persistence's SQL journal has
  `PRIMARY KEY (persistence_id, sequence_number)` — the same shape as runstate's Postgres table — and
  because only the entity writes its journal, *every* journal write is effectively a compare-and-swap on
  the next sequence number. runstate cannot make every worker write a CAS today, because other parties'
  records (commands, launcher reports) interleave in the same log, so the worker's `expected_seq` would
  be stale constantly.
- **DBOS** fences every step write by checking an ownership token under a row lock — acquisition there is
  a different kind of operation (a row update), which is write-authority.md's declined "fifth op" path.

## The lead

Split each run's log into two streams:

- a **worker stream** holding only records that speak *for an episode*: the claim (`lifecycle.started`),
  heartbeats, `value`, and the worker's terminal (`lifecycle.stopped`) — and
- a **control stream** holding everyone else's records: `control.*`, `launcher.*`, and third-party
  reports.

Then **every append to the worker stream is a CAS** on that stream's head. A new claimant's `started`
moves the head; the displaced worker's next append fails its CAS (`send` returns `None`), so the
displaced worker **learns** it has been displaced at its very next write, and its later records never
land. No new substrate operation (it is the existing `send(expected_seq=)`), no topic routing in the
substrate (the split is by stream, chosen by the convention layer).

Claimed to answer, of revision 2's five refutations: (1) "a floor with an opt-out is not a floor" — every
worker-stream write goes through the library's CAS; (3) "the residue is a zombie" — the displaced worker
gets `None` and knows; (5) "breaks opinion-freeness / topic routing" — the substrate routes on nothing.
Claimed **not** to answer: (2) the artifact plane (checkpoints on disk) stays unfenced, as in every
surveyed system; (4) whether a forged claim still mutes a live worker.

Not obviously in conflict with `docs/dead_ends/per-episode-loglets.md` ("a CAS arbitrates only writers who
share a frontier"), since every claimant of a run shares the worker stream's frontier — but unchecked.

## What an adversary should test

Whether this survives contact with runstate's actual folds, conventions, consumers and dead ends —
including anything that pairs records *across* the two streams by position, every writer that would have
to choose a stream, and what the worker must now do on every write.
