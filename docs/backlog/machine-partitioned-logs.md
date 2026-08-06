# One log file per writing machine

**What this is:** the surviving half of log-forking (`../dead_ends/log-forking.md`). Forking cannot
*arbitrate* two writers — the field is unanimous that a CAS on a single mutable pointer does that,
which is what runstate's birth claim already is. But forking can **contain** the consequences of a
wrong arbitration, and that is a separate, unaddressed goal.

## The gap it closes

`observables.resolve()` answers *is this worker alive?* three ways: **True** (same host, pid alive),
**False** (same host, dead), **None** (abstain — any foreign host, `vocabulary/handle.py`).

The claim plane's safety rule is that a probe may **veto** a claim release, never authorise one
(`cross-host-claim-gate.md` §8.1). So:

| where the worker is | probe | eviction |
|---|---|---|
| same machine | True / False | correctly arbitrated |
| **another machine** | **None** | **proceeds unverified** |

The abstention is deliberate and correct — inferring death from silence was measured and rejected
(`../dead_ends/failure-detector.md`; the consumer's own ruling is that a false positive admits a
second writer, "worse than the stranded claim it would fix"). But it means the cluster case, which
is the *motivating* case, releases a claim nobody could check. If that is wrong, two workers share
one log and the value series becomes a silent interleaving (`memoizer.py` take-the-latest).

## The proposal

**Partition the log by writing machine.** A worker writes only to its own machine's partition;
readers union across partitions. The fork boundary is then a local, mechanical fact — compare the
handle's hostname to your own — and it falls *exactly* where the probe stops working:

- **same machine** → same partition → the probe works, so arbitrate properly; no fork needed
- **different machine** → different partition → nothing shared, so a wrong eviction cannot splice

The two mechanisms cover disjoint cases and together cover everything. `resolve()`'s abstention
stops being the gap and becomes the trigger.

**Keep every partition on shared storage.** Node-local logs would make the log exactly as
unreachable as the worker, destroying the third-party observation that is runstate's reason to
exist. The partition is by *writer*, not by *location*.

**Bounded by machines, not resumptions** — which is far fewer files than fork-on-resume, and the
count does not grow with a long-running run that resumes many times on one node.

## The gate: does a partial order still carry the protocol?

**Answer this before anything else. It decides this idea and log-forking together, so it is worth
doing once, properly.**

`seq` collisions across partitions look like an implementation detail with an obvious fix — a
composite `(partition, seq)` key, which is what Dolt landed on and what the Postgres backend already
does with `(run_id, seq)`. But the collision is the symptom. The disease is that **the protocol is
positional**: `protocol-algebra.md` L2 requires that *"a standing fact's eliminator must **follow it
by `seq`**."*

Split the log and the order becomes **partial**. "Follows by `seq`" is defined within a partition
and undefined across them. A composite key restores *uniqueness* and removes *comparability* —
and comparability is what the semantics are built on. This is also what makes it a law rather than
an accident: the prior-art sweep found those systems get a partial order from DAG reachability and
*"a total order only where a single serialization point exists"* (`../dead_ends/log-forking.md`).
Everyone who branches has paid this.

It is bounded and cheap to evaluate, because L2 enumerates the positional rules and there are
**four**:

| rule | the question across partitions |
|---|---|
| `control.stop` ↔ next `lifecycle.stopped` | does a stop on partition A get discharged by a stopped on B? |
| `control.subscribe` ↔ next unsubscribe-or-nak bearing its `request_id` | may the answer land on a different partition than the request? |
| time-referencing subscribe ↔ next episode boundary | is a boundary on another partition a boundary for this lease? |
| episode terminality ↔ no opener following the terminal | what is "following" when the opener is elsewhere? |

Four questions of one shape. If the honest answer to most of them is "this needs a cross-partition
tiebreak," then that tiebreak is a new total order in disguise, and both this idea and forking cost
far more than they look. **Note this gate applies equally to any log-splitting scheme — it is the
shared crux, not a discriminator between them.**

## What else has to be answered

1. **Which folds are cross-partition, and what do they mean then?** `latest_episode`,
   `live_episode`, `peek_terminal`, `last_activity`, `value_series`, `progress`. Some are
   naturally a union (freshness = max); some are not (which episode is "latest" across partitions
   with no shared order?).
3. **Discovery.** A run id maps to a *set* of files, not one. `channel-locators.md` and every
   consumer entry point are affected.
4. **Read cost.** Every fold spans the set. Measure before asserting — but note that #15's richer
   `read` is the same shape of problem and its measured batching already turns three round trips
   into one, so these compose.
5. **Does it actually help, or move the splice?** Two partitions mean no interleaved *log*, but the
   two workers still both believe they own the run. Whether the union-fold produces a coherent
   answer, or just relocates the incoherence, is the question that decides this.
6. **Postgres.** The shared-table backend keys on `(run_id, seq)`. A partition axis is a schema
   change there, and it cuts against the "one arbiter reachable from everywhere" pitch.

## What would make it wrong

If (5) shows the union fold is incoherent rather than merely more expensive, this is a more
elaborate way to store the same problem. If (1) forces every fold to be re-derived, the cost likely
exceeds the eviction design plus an honest documented gap.

## Relationship to the live claim work

**Complementary, not alternative.** `claim-eviction.md` is still needed: within a single
partition a claim can still strand, and a third party still needs a record that releases it without
forging a verdict. This does not obviate that design; it would close the blind spot the design
documents and cannot itself fix.
