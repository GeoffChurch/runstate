# Lineage graph: a run's computation as named step-states

**Status:** DESIGN SKETCH, untested (opened 2026-10-04). This is the long-term direction that the
[identity-in-records](identity-in-records.md) measurements point to. Every claim below is a *framework
prediction* unless marked measured. Two falsification tests are given at the end. Nothing here is
adopted.

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

## Falsification tests

1. **Commit records make the direct check sound.** Rerun the vouching probe's kill orders (save before emit,
   killed before the emit) and its demand-sampled shapes with node records in place of separate values.
   - **Prediction:** presence of the checkpointed node's record and its ancestors gives 0 gaps, 0 holes and
     0 splices, with no lag where a worker published and died before beating.
   - **Refutation:** any gap, or a case where heartbeat vouching beats it.
2. **No arbiter, no corruption.** Run two claimants concurrently with **no** claim CAS, node-keyed values and
   checkpoints, and replay under causal reordering.
   - **Prediction:** every head is internally consistent, with 0 splices and 0 holes. Only waste differs.
   - **Refutation:** any head whose series mixes branches, or any resume that crosses branches.
