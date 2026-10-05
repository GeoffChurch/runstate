# Liveness promises: each liveness record says when the next one is due

**Status:** DIRECTION, form chosen (opened 2026-10-05 with the owner, during the layer-2 walkthrough).
Not part of layer 2 ([commits-and-lineage](../specs/commits-and-lineage.md)). It adds a field to the
same records later, and needs no compatibility path (see "Adding it later").

## The problem

Heartbeat staleness (design-v0.2 §8, tier 4) presumes a worker dead when it has been silent longer than a
threshold. **The observer picks that threshold** (`Watcher(heartbeat_timeout=…)`), but it is a fact about
the workload. Design §8 calls the result the irreducible dead-vs-busy ambiguity: the threshold must exceed
"the worker's max inter-beacon gap, which the reader often can't know a priori".

- **Observers disagree.** Two observers with different timeouts give different verdicts on the same run.
  A cockpit watching mycooc and translation together must pick one number for both.
- **One number must cover the worst phase.** A worker whose loading takes hours forces a long timeout,
  which also slows hang detection during its fast phases.
- **The workaround is a background beating thread,** which proves only that the process exists: a
  deadlocked loop keeps beating.

## The form

**Every liveness record carries `next_within`:** the number of seconds within which the worker promises its
next liveness record.

- **Which records:** `started`, `lifecycle.commit` and `lifecycle.heartbeat`, the records that witness
  liveness under layer 2 (D1's pin). `stopped` needs none, since nothing follows it.
- **Each record is self-contained,** so the latest record alone decides. A field meaning "unchanged" would
  force a search back through history.
- **`started` carries the first promise,** so startup and loading, often the longest silence, are covered
  without an initial heartbeat.
- **`null` means "no promise from here on":** staleness does not apply until a later record makes one.
  This is how a worker declines to claim liveness. The other tiers (a terminal record, the handle probe,
  the episode probe) still apply.
- **It is a duration, timed from witnessed arrival** (observer-clock.md §5), never a deadline timestamp.
  The worker's and observer's clocks disagree; layer 3 measured a healthy worker taken over within 5 s at
  40 s of skew. A duration on the observer's own clock is skew-free.
- **The observer adds only its own slack,** its polling interval, which is a property of the observer, not
  of the workload.
- **It is not substrate state.** Design §8 avoids a mutable, TTL'd lease in the substrate. This is a lease
  expressed in emitted records, renewed by each one.

**The verdict:** presumed dead when nothing new from the latest claim is witnessed within the latest
record's `next_within` plus the observer's slack. Silence within the promise means busy, as declared;
silence past it is a broken promise, not an ambiguity.

**The inherent limit:** a hang inside a declared long phase is caught only when that phase's promise runs
out. A worker that wants faster detection beats from inside the phase.

## Worker API (sketch)

- **A standing promise,** set when the Worker is built: a required edge parameter (a number, or no
  promise), since it changes verdicts.
- **Any `commit()` or `heartbeat()` may override it** for the gap that follows:
  `w.heartbeat(next_within=7200)` before a known long load.
- **Under the drivers,** call `w.heartbeat(next_within=…)` before a long evaluation. The driver's next
  commit returns to the standing promise.

**What it replaces:** the Watcher's `heartbeat_timeout`, a workload threshold held by the observer.

## Alternatives considered

- **One promise on the claim only.** It is the special case where every record repeats the same number. It
  cannot vary by phase, so it forces the worst-case number or a background thread.
- **A cadence stream on the value plane,** overwritten as needed. Rejected:
  - **No claim at arrival:** a value names no claim when it arrives, and is attributed only by the commit
    that later names it.
  - **Wrong timing:** a cadence change must take effect *before* the long phase, which has no commit until
    after it. So the value sits unattributed for exactly the window it governs.
  - **Two records for one event:** "I'm alive, and I'll speak again within X" is one fact. D1's rule is one
    record per event.
  - **A reserved metric name** for the Watcher to find, which imposes a naming convention on users.
  - **The bytes it saves are small:** one integer per commit or heartbeat, and mycooc's values outnumber
    its beats about 20 to 1 (4.2M against 214k).
- **A heartbeat on the value plane:** rejected in the walkthrough, item 1
  ([review](../review-2026-10-04-commits-and-lineage.md) §12).
- **A deadline timestamp:** skew-fragile (above).

## Adding it later

It is a lifecycle schema bump: a required, nullable `next_within` on three records. A migration writes
`null` on every older record, which truthfully says it made no promise. Null has a live meaning (a worker
may decline to promise), so this passes the from-scratch test. It is not a compatibility shim.

**Revival trigger:** the cockpit watching runs of more than one workload, or any consumer whose phases
differ in silence by more than its hang-detection budget allows. mycooc already qualifies: its loading
phases were silent for up to 10,031 s against a 1,800 s timeout.
