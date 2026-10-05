# Spec: commits and lineage — heartbeats commit named values (log format 0.4.0)

**Status:** DRAFT 2026-10-04, for the owner's review. Not implemented.

This is layer 2 of [`../backlog/identity-in-records.md`](../backlog/identity-in-records.md). It was designed
with the owner one decision at a time; §2 of that entry holds decisions 1–13 and the reason for each. It
introduces **log format 0.4.0** under [`log-formats.md`](log-formats.md), and builds on
[`reference-by-name.md`](reference-by-name.md) (format 0.3.0).

**What it gives:** a reader can project **one lineage** of a run's values.
- A displaced worker, a rewind or a recomputed step can no longer splice a series.
- A checkpoint can no longer name work that never committed.
- The protocol carries no notion of "step".

## 1. The problem

Today's value reads keep the latest value per (name, step).
- **The soundness argument assumed sequential episodes**
  ([`../backlog/value-plane-divergence-resolution.md`](../backlog/value-plane-divergence-resolution.md)).
  Displacement breaks that assumption. Measured: a displaced worker's values spliced the series in 8 of 36
  configurations, under every vouching rule (identity-in-records §2–§3).
- **No record marks a step complete.** The heartbeat stood in for that by accident.
  - A worker that saves a checkpoint before logging, and is killed in between, loses that step's values
    silently.
  - A `stopped` vouched a step that never committed, because `final_step` is the last step *yielded*.
- **A name finer than the claim is needed.** One episode that rolls back and recomputes a step leaves two
  values under one claim, and nothing tells them apart.

## 2. The rule

> **A heartbeat commits the value records it names. A lineage is the chain of heartbeats, by `parent`. A
> stream is a value name; its elements are the committed values of that name that answer no
> subscription, and an element's coordinate is its index along the lineage.**

- **The commit.** A value belongs to exactly the heartbeat whose `commits` names its seq. A value no
  heartbeat names is outside every lineage: uncommitted, or foreign.
- **Samples are not stream elements.** A subscription sample (a value with a `request_id`) is committed
  like any value, but it belongs to its subscription and is read by that id. It never counts toward a
  stream, so an emitted value and a sample of the same name cannot double-count, and a subscription
  cannot feed its own progress condition.
  - **This is an interim rule, and is labelled as one.** Two primitives write values of one name: `emit`
    (a stream, at the worker's cadence) and `set` plus a subscription (a sampled cell, at the observer's
    cadence). They overlap in one concept, which the orthogonality test (CLAUDE.md, design rubric) flags.
    This rule keeps that overlap consistent.
  - **The direction that removes it** is [`../backlog/demand-streams.md`](../backlog/demand-streams.md),
    where a sample is a demanded element of an *offered* stream.
  - **Layer 2 makes that step cleaner, not harder.** Samples keep today's `request_id` behaviour, so a later
    unification is an extension, not an undo.
- **The node.** A node is a heartbeat, named by its own seq, exactly as a claim is named by its
  `lifecycle.started`'s seq. `parent` names the node the worker's state was computed from.
- **Positions not compared.** No rule compares the positions of records written by different writers. The
  orders the rules rely on are:
  - the order among claims, fixed by the claim CAS;
  - each writer's own program order;
  - "a record follows what it names".

## 3. Wire format

These are the convention bumps. The envelope is unchanged.

### lifecycle-v0.6

| record | body | change from v0.5 |
|---|---|---|
| `lifecycle.heartbeat` | `{claim_seq, consumed_seq, parent, commits, t}` | `step` removed; `parent` (integer ≥ 1, or null) and `commits` (array of distinct integers ≥ 1) added |
| `lifecycle.stopped` | `{completed, error, claim_seq, honored, final_beat, t}` | `final_step` replaced by `final_beat` (integer ≥ 1, or null) |
| `lifecycle.started`, `lifecycle.bound`, `lifecycle.nak` | unchanged | — |

- **`parent`**:
  - the previous heartbeat of this worker's episode;
  - or, for the first heartbeat after a resume or a rewind, the node resumed from;
  - or null for a fresh start.
- **`commits`** names the seqs of the `value` records this heartbeat commits. A heartbeat that commits
  nothing carries `[]`, for example a liveness beat inside a slow step (§4).
- **`final_beat`** is the seq of the episode's last committed heartbeat, or null if none was ever committed.
  The verdict is thereby tied to a node.

### value-v0.3

- **Body:** `{value, t}`. `step` is removed.
- **The envelope `name`** is the stream.
- **The envelope `request_id`** names the subscription a sample answers, or is null for an emitted value.

Dense data (arrays, tensors) goes in blobs referenced by name, never inline. Large values belong to the
data-plane project.

### subscription-v0.4

**A progress atom replaces the step atom:**
- **The atom:** `{stream: <name>, n: <integer ≥ 1>}`.
- **In `from` and `until`,** it holds once the stream's prefix along the head's lineage has at least `n`
  elements.
- **In `every`,** it fires each time that prefix crosses a multiple of `n` since the last firing. So a
  batched jump from 95 to 105 fires once at `n = 10`.

**What else changes:**
- **The other atoms are unchanged:** `time_seconds` and `count`, plus the `any` and `all` combinators.
- **`StopTrigger.from`** takes the same atoms.
- **The step atom** (`{step: N}`) is removed.

**Evaluation:** subscriptions are evaluated at each `tick()`, never at a liveness `beat()`. Samples are
written before the heartbeat that commits them.

## 4. The Worker

- **`emit(name, value)`** sends a `value` record at once and adds its seq to the pending commits.
- **`set(name, value)`** updates the register. A fired subscription samples it as a `value` record
  answering that subscription. The sample is sent just before the heartbeat and is committed by it.
- **`tick()`** has no step argument. It does three things, in order:
  1. drains control;
  2. fires due subscriptions, writing their samples;
  3. writes the heartbeat, which commits all pending values and whose `consumed_seq` reports the drain.
- **`beat()`** drains control and writes a heartbeat committing **nothing**. It fires no subscriptions. It
  is the liveness beat for inside a slow step. Pending values wait for the iteration's `tick()`, so one
  iteration is still one commit.
- **`commit_external(seq)`** adds the seq of a value sent through a separate channel handle to the pending
  commits. Without it, such a value is uncommitted.
- **`steps(total, *, resume_from, checkpoint=None)`:**
  - **`resume_from` is required** and keyword-only: a checkpoint object, or an explicit `None` for a fresh
    start. It names the first heartbeat's `parent`. A rewind is a further `steps` call on the same Worker
    with an older checkpoint.
  - **`total` counts the driver's iterations,** which is a convenience. The driver's position is saved and
    restored with the checkpoint (§6), and is never stored on the log.
  - **`checkpoint=`** is described in §6. Its default, `None`, means no checkpoints. That is allowed under
    the no-defaults rule because it cannot change a result: it changes only what can be resumed, never a
    value.
- **The completed exit.** `stopped(completed=True)` first writes one last heartbeat committing any pending
  values, such as metrics written after the loop. It then names that heartbeat as `final_beat`.
- **The error exit.** An error exit inside an iteration commits nothing, because the unfinished
  iteration's values die uncommitted. It names the previous heartbeat. The Worker knows which case it is
  in.
- **`serve()` is unchanged in shape:** its loop calls `tick()` once per iteration.

## 5. Reads

Every read that depends on a branch takes a **required, keyword-only `head` Strategy**, chosen once at the
edge (the owner's no-defaults rule).

| Strategy | Head |
|---|---|
| `LatestClaimHead()` | the newest heartbeat of the newest claim that has written one: the order among claims, then that writer's own order. No clocks. |
| `AtNode(seq)` | that heartbeat (forensics; reading an abandoned branch) |

**The reads:**
- **The lineage.** Walk `parent` from the head.
- **Completeness.** A read is complete only when every heartbeat on the walk is visible, and so is every
  value it commits. Under visibility lag the read waits, or raises with the missing seqs. It never reads a
  hole.
- **`series(ch, name, *, head)`** gives one stream's elements in lineage order, by index. Use it for
  progress.
- **`aligned(ch, names, *, head, progress)`** aligns values by shared commit. Each node that commits any of
  `names` becomes one row.
  - The row's x is the prefix length of the `progress` stream through that node, including that node's
    own commits.
  - Metrics that start late or have gaps line up by commit.
  - `progress` is required. Choosing the heartbeats themselves is allowed where they mean iterations.
- **`history(ch, name, schedule, *, head)`** replays a schedule over `series`.
  - **What is deleted:** the take-the-latest-per-step collapse. A stream index is unique along a lineage,
    so nothing needs collapsing.
- **`ensure(…, stream, n, *, head)`** demands the first `n` elements of `stream`.
  - **Read first:** it is satisfied from the log when the head's lineage already holds them.
  - **Otherwise it produces:** it drives the producer, then follows the new episode's lineage.
- **`progress(ch, stream, *, head)`** is the prefix length of `stream` along the head's lineage. It
  replaces the step frontier.
- **Unchanged:** `peek_terminal`, `live_episode`, `live_demand`, `undischarged_stops`, `last_activity`, and
  the Watcher's staleness, which reads the latest heartbeat's `t`.

`value_series` is removed. Its replacements are `series` (one stream) and `aligned` (several).

## 6. The checkpoint recipe

This is a recipe, not protocol. runstate supplies no directory.

- **No lineage lie can be written.** A lineage lie is a checkpoint whose state is not the state committed by
  the node it names. The recipe prevents it in two ways:
  - **Save.** `steps(…, checkpoint=Every(k, save))`, or `tick(checkpoint=save)`, calls `save(node)`
    immediately after a heartbeat lands. At that instant the state is exactly the state that heartbeat
    commits. Saving at any other moment is outside the recipe.
  - **Load.** A checkpoint object carries its node and the driver's position. `resume_from` takes the
    object, never a bare seq, so the state loaded and the node named come from one place.
- **Choosing a checkpoint** is a required Strategy over **complete** checkpoints only: the named heartbeat,
  all its ancestors, and every value they commit are visible. There are two policies:
  - `OnHeadLineage(head)`: consistent with reads and the verdict, and respects rewinds. This is the
    suggested preset.
  - `MostProgress(stream)`: the least recompute, but it can resume from a branch abandoned by a rewind.
- **An optional fingerprint guard.**
  - **What it is for:** custom loops that bypass the recipe.
  - **How it works:** the user supplies a cheap fingerprint of the state, such as the optimizer's step
    counter. The Worker records it with each commit, and the recipe compares it at save and at load.
    *Untested.*

## 7. Migration: format 0.3.0 → 0.4.0

`runstate/migrations/v0_3_0_to_v0_4_0.py`, under `log-formats.md` §6. Every inference copies what the old
positional reading implied, the same standard as the 0.2.0 → 0.3.0 step.

- **Commits.** A `value` record is committed by the heartbeat of its own claim whose old `step` equals the
  value's old `step`. The claim is the latest claim before the value: the old window rule, with
  misattribution copied faithfully as a known limit.
- **Stepless values** (old `step` null; 1,843 metric names in mycooc) are committed by the next heartbeat
  of their claim, by position, as the old reading implied.
- **Missing ticks are repaired.** Where a claim's values carry a step label that no heartbeat of that claim
  carries, a **synthetic heartbeat** is inserted where the tick should have been, and commits them.
  - **Renumbering.** The copied log is renumbered, and every seq reference is renamed with it:
    `claim_seq`, `parent`, `commits`, `final_beat`, and `bound`'s `claim_seq`.
  - **Why this is safe:** migration writes a new file, and nothing outside a log holds its seqs.
- **Post-loop values.** Values after an episode's last heartbeat are committed by a synthetic final
  heartbeat (§4's completed exit).
- **`parent`.**
  - Within a claim, the previous heartbeat.
  - For a claim's first heartbeat, the latest earlier heartbeat whose old step is one less: the as-resumed
    predecessor.
  - Otherwise null.
- **`final_beat`** is the claim's last heartbeat, synthetic or not.
- **Field removals.** `step` is dropped from heartbeats and values.
- **Subscriptions with a step atom refuse the run.** The real corpus has none. A refusal names the run and
  the seq, and refuses before sealing (log-formats §6).
- **Measured 2026-10-04 on the real-log corpus:**
  - **translation:** depth along the inferred lineages equals the old step on all 1,010,405 heartbeats.
  - **mycooc:** 53,757 of 215,529 heartbeats do not, all from 891 skipped ticks (§8), each skipped step
    carrying a full metric set.
- **The migration's tests must show three things on the corpus:**
  - after repair, depth equals the old step on every heartbeat;
  - every value is committed by the heartbeat carrying its old step;
  - the reads equal the old reads, apart from named classes of positional defect fixed.

## 8. A consumer's upgrade

These additions extend [`reference-by-name.md`](reference-by-name.md) §7:

- **Tick on every iteration.** mycooc's `on_step` returns early on three "patience exhausted" branches,
  before its tick (`training.py:1256`, `:1287`, `:1313`).
  - At those 891 steps its metrics landed with no heartbeat, the control drain was skipped, and liveness
    had a gap.
  - The fix is to move the tick ahead of the returns, or into a `finally`.
- **Send values through the Worker,** or `commit_external` them. This affects mycooc's separate-handle final
  metric.
- **Drop `step=` from value sends** and from `tick()`. A user who wants a step label emits it as data, or
  reads with `aligned`.
- **Rewrite subscriptions' and `ensure`'s step conditions** as progress atoms on a chosen stream.
- **Pass `head=` and `resume_from=` explicitly.**

## 9. Interop

These are protocol, and another implementation must match them:
- the commit rule;
- the lineage walk and its completeness check;
- the progress atom's crossing rule;
- the `final_beat` semantics.

The conformance suite gains a test for each.

## 10. Tests

On every backend:
- **Commits:**
  - each value is committed exactly once;
  - an uncommitted value is excluded;
  - `commit_external` works;
  - a liveness `beat()` commits nothing and splits no iteration.
- **Lineage and visibility:**
  - resume and rewind form the right parent chains;
  - a read under visibility lag waits or raises, and never holes.
- **The exit commits:**
  - a completed exit commits post-loop values;
  - an error exit inside an iteration commits nothing;
  - `final_beat` is correct in both cases.
- **The checkpoint recipe:**
  - the driver-invoked save names the right node;
  - `resume_from` takes a checkpoint object;
  - both choice policies select only complete checkpoints;
  - the fingerprint guard catches a planted mismatch.
- **The progress atom:**
  - `every` crossings under batching (95 → 105 fires once);
  - `until` and `from`;
  - a sample does not count toward its own `every`.
- **Aligned reads:** metrics that start late or have gaps align by commit.
- **Order independence:** extend `tests/test_order_independence.py` with heartbeats, commits and parents.
  Every read stays invariant under causal reordering.
- **Migration:**
  - golden 0.3.0 logs, including a skipped tick, post-loop values, a resume and a stepless heartbeat;
  - the three corpus properties of §7;
  - a refusal for a step-atom subscription.

## 11. Ripple

- **`design-v0.2.md`:** §6, the heartbeat and tick; §7, commits; §10, versions; and a revision-history
  entry.
- **`observables.md`, `memoizer.md`, `ensure-until-condition.md`, `service-worker.md`,
  `time-lease-boundary.md`:** step atoms become progress atoms, and step frontiers become stream prefixes.
- **`value-plane-divergence-resolution.md`:** the take-the-latest collapse is superseded by lineage.
- **`run-id-recipe.md`:** state the assumption that observers' demands count as inputs where they perturb
  (decision from [`../backlog/demand-streams.md`](../backlog/demand-streams.md)).
- **`api.md`, the implementers guide, `CLAUDE.md`:** the API and architecture notes.

## 12. Known limits and open items

- **Forgery and authority are unchanged:** still an honor system
  ([`../backlog/authenticated-records.md`](../backlog/authenticated-records.md)).
- **Observation remains relative to the latest claim.** Head-relative verdicts and stops belong to layer 6
  ([`../backlog/lineage-graph.md`](../backlog/lineage-graph.md)).
- **A heartbeat can be visible before the values it names.** The completeness check covers it. This split
  form is not re-measured: the lineage-graph probes carried values inside the node record.
- **Bytes.** Values lose `step`, and heartbeats gain `commits`, about one integer per value. Users may batch.
  The encoding experiment (identity-in-records §2) should measure the net effect on the real corpus.
- **A historical misattribution is copied faithfully** by the migration, as before.
