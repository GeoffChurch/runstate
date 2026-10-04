# Lineage graph: a run's computation as named step-states

**Status:** DESIGN SKETCH, partly measured (opened 2026-10-04). It is the long-term direction that the
[identity-in-records](identity-in-records.md) measurements point to.

Both falsification tests ran the same day (see "Measured" below):
- **The lineage plane held.** There were no gaps, holes, splices or cross-branch resumes, and no pick
  depended on ordering, with or without an arbiter.
- **The observer plane, head choice and stops did not.** Each still speaks for "the latest claim", or names
  no branch at all.

Any claim not marked measured is a *framework prediction*. Nothing here is adopted.

## Why

Every fix in identity-in-records made a record name what it is about. Each remaining problem is something
still identified by position, or by a name too coarse for it:

- **A claim names an episode, not a history.** An episode that rolls back and recomputes a step leaves two
  values under one claim, so a lineage read cannot tell them apart (§3, measured).
- **Heartbeat vouching is needed only as a per-step commit marker.** Values written as separate records
  carry no "this step is done". The Worker's program order (values before the beat) lets the heartbeat
  stand in for it. A direct presence check fails without that marker (§3, measured).
- **Order among claims still needs one arbiter.** Claims are named by the arbiter's seq, so episodes are
  not portable.

## The design

1. **Each computed step is a node.** The computing worker mints the node's id; the owner accepted that
   producers can mint unique ids. A node record names:
   - its **parent node**, the state it was computed from;
   - its step label;
   - the step's **values, inside the record**.

   The record is the step's commit: present means complete. A node with no values means "none were
   written", for example demand-sampled values nobody subscribed to.
2. **Checkpoints name their node.** The manifest is the node id, because the ancestry lives in the graph.
3. **A read chooses a head, then walks parents.** The series of a head is its ancestors' values. The
   lineage read stops being a repair and becomes the definition of a read.
   - **Resume** takes a head whose checkpointed node and all its ancestors are present. This is V∅+P, made
     sound by the commit record.
   - **Head choice is a policy**: most advanced, then a deterministic tie-break. It is an opinion, so it is
     an opt-in helper, never substrate.
4. **Rewind and fork are branches**, children of an older node. The episode-versus-history problem
   disappears, because the key is the node.
5. **Claims become coordination, not correctness.**
   - Two workers that both believe they own the run produce two branches, and neither can corrupt the
     other. "A wrong claim costs waste, never corruption" then holds with **no** arbiter.
   - The claim CAS stays only as deduplication of work.
   - Time may trigger claims freely (layer 4). Fencing (layer 5) is unnecessary.
6. **Episodes are named by minted ids**, not the arbiter's seq. Logs from different machines then combine by
   union, since every reference is a name. That is layer 6
   ([machine-partitioned-logs](machine-partitioned-logs.md)) without a sequencer for correctness.

This is [if-built-today](if-built-today/README.md)'s "identity is data, never position", reached for the
value and artefact planes as well as control.

## What it costs or leaves open

- **Reads take a head.** "The latest loss" becomes relative to a chosen head. Simple consumers need a helper
  that picks the head for them.
- **Abandoned branches need garbage collection**, by the same mechanism as unvouched episode directories.
- **The Worker threads the current node id through**, and raw sends need a stamping helper; layer 2 needs
  that helper anyway. Node records batch a step's values under one name, which addresses layer 2's
  per-value stamp overhead. Whether this loses the `latest(topic, name=)` index that today's reads use is
  open.
- **"Step" becomes a node label.** It is already a convention; the graph must stay one: an opt-in convention
  over the opinion-free substrate.
- **Migration from 0.3.0.** Without rewinds, a node is (claim, step), so migration should be mechanical.
  Rewinds before migration cannot be recovered, because the log never recorded them.
- **Prior art to check before citing.** W&B is believed, from memory and unverified, to offer rewinding a run
  to a step and forking from a step. Those are this graph at whole-run granularity.

## Falsification tests (as written before the runs)

1. **Commit records make the direct check sound.** Rerun the vouching probe's kill orders (save before emit,
   killed before the emit) and its demand-sampled shapes with node records in place of separate values.
   - **Prediction:** presence of the checkpointed node's record and its ancestors gives 0 gaps, 0 holes and
     0 splices, with no lag where a worker published and died before beating.
   - **Refutation:** any gap, or a case where heartbeat vouching beats it.
2. **No arbiter, no corruption.** Run two claimants concurrently with **no** claim CAS, node-keyed values and
   checkpoints, and replay under causal reordering.
   - **Prediction:** every head is internally consistent, with 0 splices and 0 holes. Only waste differs.
   - **Refutation:** any head whose series mixes branches, or any resume that crosses branches.

## Measured (2026-10-04)

Both tests were throwaway probes on the layer-3 harness, with node records in place of separate values. Each
was replayed under random and adversarial causal reorderings.

### Test 1: commit records. Correctness held; the lag clause did not.

**The rule G:** resume from the most advanced checkpoint whose node and every ancestor node are present. It
was compared with heartbeat vouching (V2) over:
- the layer-3 matrix;
- every kill order around save, emit and beat;
- both forms of visibility lag;
- demand-sampled `set()` shapes;
- a rewind;
- three extended kill orders.

| | gaps | holes | splices | picks varied by ordering |
|---|---|---|---|---|
| G | 0 | 0 | 0 | 0 |
| V2 | 4, permanent (one extended case) | 12, transient (per-topic lag) | 0 | 0 |

The coverage was 194 histories × 44 orderings.

**Results:**
- **Every published value is now covered.** "Values exist but no manifest names them" was 8–12 cells under
  every rule in today's shapes. Here it is 0, because a checkpoint names a node rather than records.
- **G recomputes no more than V2, with one trade-off.** It recomputed 13 and 11 fewer steps where "latest
  episode first" chose a killed successor. Under per-topic visibility lag, V2 resumes further, but its
  resume point has transient holes; G falls back instead.
- **The "no lag" clause is refuted.** The node record is written at the commit, where today's heartbeat
  lands. A checkpoint saved before the commit therefore names a node that dies with the worker, and G
  falls back one step, exactly as V2 does. G is lag-free only when **the save follows the commit**.
  - So the commit point is a design choice with a measured price.
  - Saving after the tick, or losing only the beat after the commit, gives no lag. V2 lags in every save
    order.
- **A V2 defect was found and is recorded in identity-in-records §3.** An error `stopped` reports the last
  *yielded* step, so it vouches a checkpoint for a step that never committed. Building on it leaves a
  permanent gap. G resumes one step back.
- **Most-advanced head choice cannot see a rewind.** A worker that rolls back abandons a branch, and the
  abandoned branch's tip can still be the most advanced.

### Test 2: no arbiter. The lineage plane held; the observer plane did not.

**The setup:**
- **Claimants.** Two or three per run, with **no** claim CAS.
- **Scenarios:**
  - both start from scratch;
  - both resume from one checkpoint;
  - a mistaken claim mid-run;
  - a three-way race with a rewind, and a variant with a stop;
  - then a later resumer.
- **Size.** 1,152 histories in total, including 192 with real processes SIGKILLed on SQLite WAL.
- **Edge sets** for the reordering: reads follow only what they read; every read sees the full prefix; and
  the claim order restored.

**The lineage plane:**
- 0 splices, 0 holes, 0 cross-branch resumes, and 0 picks varied by ordering, in every scenario.
- The later resumer always resumed cleanly and finished.
- Waste was the only difference: a mean of 2–18 node records off the final head.

**The controls bite:**
- A latest-wins reader spliced and varied in 88–120 of 120 histories per scenario.
- Resuming the newest checkpoint without the presence check left permanent holes in 17 of 960.
- A resumer naming the wrong parent produced holes in 107 histories.

**Where it fails: everything that speaks for "the latest claim".**
- **Verdict and progress follow the latest claim, not the head.** In real-time order, `peek_terminal` was
  None while the chosen head's branch had completed, in 26–67 of 120 histories per scenario. It said
  preempted where the head had completed in 17. Single-claimant controls: 0.
- **Without an arbiter those folds depend on order.**
  - `latest_episode`, `peek_terminal`, `progress` and the claim test varied in every history when reads
    follow only what they read.
  - Even with full-prefix reads they varied in 52–85 of 120 with concurrent claims, because two concurrent
    claims have no causal order.
  - `undischarged_stops` never varied.
  - A prototype verdict tied to the head (the `stopped` naming the head writer's episode) never varied.
- **The chosen head flips.** Equal-speed claimants leapfrog, and the node-id tie-break follows them: flips
  in 21 of 24 histories, and one flip rewrote 12 steps a poller had already seen. Every read is clean, but
  the series a poller sees is not monotone.
- **A stop names no branch.** In 20 of 120 runs, another claimant computed one more step after the stop was
  honored. In 1, a claimant that attached after the honoring `stopped` treated the stop as spent and ran to
  completion, and that log is indistinguishable from a legitimate relaunch. So "discharged" stops meaning
  "every claimant stopped".

## What the design needs next (open; from the measurements)

These matter only without an arbiter. Under the claim CAS, concurrent claims cannot both succeed, so "the
latest claim" stays well defined.

1. **Observation relative to a head.** Verdict, progress and liveness become views of a chosen head. The
   terminal names the minted episode, and the measured prototype was invariant. Leases, which key on the
   latest claim, need the same treatment. They are untested.
2. **A head choice that is stable over time**, so a poller's series is monotone. Node-id tie-breaks are not
   stable.
3. **Abandonment.** A record naming the branch a rewind abandoned, or a rule that a writer's own later node
   on another branch supersedes its earlier tip. The rule needs only one writer's program order, so it is
   position-free. *Prediction, untested.*
4. **What a stop names.** Today it names no branch and no set of episodes. With concurrent claimants it must
   say which computations it stops, and a stop already honored must still reach a claimant that was
   concurrent with it.
5. **The commit point.** Save after the commit to avoid the lag measured in test 1.

## Consequence for layer 2 (*framework prediction*)

The step record (parent, step and values in one record) is useful **with today's arbiter**, before any of the
above:
- It is the per-step commit marker that heartbeat vouching stands in for.
- It is the lineage key finer than the claim.
- It amortizes the per-value stamp.

The node-record half of this design is therefore a candidate shape for layer 2 itself. The observer, head
and stop problems above belong to layer 6.

**Layer 2 chose the split form** ([identity-in-records](identity-in-records.md) §2, revised 2026-10-04).
The node is a small heartbeat that **names the value records it commits, by seq**; values stay ordinary
records. The commit semantics are the same as the node records measured above, with one difference: a
commit can now be visible before the values it names. A read must therefore check that the named values are
present too, and it waits or falls back rather than reading a hole. *Not re-measured in this form.*
