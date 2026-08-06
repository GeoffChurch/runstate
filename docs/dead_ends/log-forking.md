# Dead end: forking the log to arbitrate two possibly-live writers

**Status:** REFUTED 2026-08-04 on prior art (two independent literature sweeps), plus one
mechanical hit. **Partially survives** — the *workspace* form is live, in
`../backlog/machine-partitioned-logs.md`. Read the split in *What survives* before reusing either
half; the two are easy to conflate and only one of them is dead.

## What it proposed

Resumption **copies the log** (literally, or via a parent pointer / filesystem reflink) and the new
episode writes to the copy. If the incumbent turns out to be alive, it keeps writing to the
**original** and the two never interfere.

Its load-bearing claim: **forking converts an irreversible act into a reversible one.** Today,
resuming a run that turns out to be alive corrupts the log irrecoverably, so the decision demands
definitive evidence — which is why the staleness tier was refuted and why "who may evict, on what
evidence" has stalled every fix. Under forking, resuming wrongly costs only a duplicate run.

It claimed to dissolve double-live corruption, the lineage problem, the episode epoch, and possibly
the designated eliminator along with it.

## Why it died

**1. The pattern does not exist in distributed logs.** A targeted sweep for it — "branch the log",
"fork the stream", accept both writers and reconcile later — returned nothing:

> Across every system investigated, the answer to a suspected-dead-but-possibly-alive writer is
> **fencing** — invalidate the old writer's authority so its writes are *rejected*, and either
> truncate, hole-plug, or discard whatever tail it left behind. **Not one of them branches the log.**
> … Branching on suspected-dead-writer is not a road-less-travelled in distributed logs; it is a road
> that does not appear on the map.

The two systems that genuinely branch (CouchDB, Riak/Dynamo) are multi-master eventually-consistent
stores that abandoned total order by design, and they branch at the *document/value* level, never at
the level of an ordered log.

**2. The versioned-data systems that DO branch use it for something else.** Dolt, Iceberg, Datomic:

> Every one of them arbitrates concurrent writers with **compare-and-swap on a single mutable
> pointer** (branch head / table-metadata pointer / log root), and uses branching for *isolation,
> workflow, reproducibility, and audit* — never as the concurrency answer. **The mutable pointer is
> the fence. The branch is the workspace.**

This is the finding that matters, because **runstate's birth CAS already is that mutable pointer.**
The field converged on precisely the mechanism runstate has. Forking would add the workspace, not
the fence — so it cannot *replace* the claim, which is what the proposal needed it to do.

Related: none of these systems use Lamport/vector/HLC clocks either — content-addressed hashes plus
DAG reachability give a *partial* order, and a total order appears only where a single serialization
point does. Datomic has a global monotonic counter *because* it has a single arbiter, and
correspondingly does not branch persistently.

**3. Mechanical: forked sequence counters collide.** Dolt is candid about hitting exactly this:

> "Originally, inserting rows with AUTO_INCREMENT keys on different branches and then trying to
> merge those branches would generate conflicts. Rows with different values were assigned the same
> primary key, the next value in the sequence."

runstate's `seq` **is** an autoincrement (`sqlite.py` `INTEGER PRIMARY KEY AUTOINCREMENT`; postgres
`PRIMARY KEY (run_id, seq)`), and the whole protocol is positional — intro/eliminator pairs are
paired *by `seq` order*. Two forks assigning the same seq to different records breaks the pairing
rule the folds are built on.

## The root error (one line)

**It mistook the workspace for the fence.** Branching contains the *consequences* of a wrong
arbitration; it does not arbitrate. The proposal needed it to replace the claim, and nothing in the
field supports that because nothing in the field does it.

## Copy-on-write does not rescue it — and a tree makes the real problem worse

The obvious refinement is a **lazy, tree-shaped log**: a parent pointer instead of a byte copy, so
forking is O(1). Recorded because it will be re-proposed, and because it does kill one real
objection.

- **Storage cost — genuinely fixed.** O(log size) per resumption was a fair objection; CoW removes
  it. Credit where due.
- **Fork count — softened, not removed.** Cheap in bytes; the count still drives read traversal and
  discovery. It converts a storage problem into a traversal problem.
- **The root error — untouched.** Whether branching can arbitrate is a *semantics* question. Making
  branching cheap does not make it a fence.
- **The fork trigger — untouched.** You still decide *when* to fork, and under fork-on-resume that
  decision is "should I resume?" — the liveness judgment the scheme claimed to dissolve.
- **The `seq` problem — worse, not better.** A composite `(branch, seq)` key fixes collisions, but
  collisions were the *symptom*. The disease is that the protocol is **positional** — L2: *"a
  standing fact's eliminator must follow it by `seq`."* A tree gives only a **partial** order, so
  "follows by `seq`" is undefined across branches. The composite key restores uniqueness and removes
  comparability, and comparability is what the semantics rest on. Which is exactly what the sweep
  found in the field: a partial order from DAG reachability, and *"a total order only where a single
  serialization point exists."*

**That partial-order gate applies to any log-splitting scheme**, the surviving machine-partitioned
form included. It is enumerable — L2 lists exactly four positional rules — and the evaluation lives
in `../backlog/machine-partitioned-logs.md`. Do it once; it decides both.

## What survives (genuine — keep)

- **Containment is a real and separate goal from arbitration.** "Make a wrong fence decision
  survivable" is not the refuted proposal and is not addressed by it. That is the live thread.
- **The fork boundary should be mechanical, not a judgment.** The refuted form forked on
  *resumption*, which requires deciding when to resume — reintroducing the liveness question it
  claimed to dissolve. Forking on the **machine boundary** is a local, checkable fact
  (`resolve()` already compares hostnames), and it lands exactly where the same-host probe stops
  working. See `../backlog/machine-partitioned-logs.md`.
- **Collocation is not required to get the boundary.** Keeping every log on shared storage while
  partitioning by writer preserves remote observability, which node-local logs would destroy.

## Where it goes instead

`../backlog/machine-partitioned-logs.md` — the workspace form, with the CAS retained as the fence.
The claim plane's live thread is unaffected and stays `../backlog/claim-eviction.md` plus
`cross-host-claim-gate.md`.
