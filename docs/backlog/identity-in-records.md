# Identity in records: names, fenced writes, episode-keyed artefacts, and time as a trigger

**Status:** DESIGN, not converged (opened 2026-10-03). Every layer was measured in a throwaway spike on
2026-10-02. Layer 1 is IMPLEMENTED (2026-10-03, as log format 0.3.0:
[`../specs/reference-by-name.md`](../specs/reference-by-name.md)); the others are not adopted.

**Evidence:** the spike branches are retired. What each layer's case rests on — the mechanism's shape,
how each experiment was set up, and its numbers — is inlined in that layer's section below, enough to
reproduce it.

## What this is for

Until log format 0.3.0, runstate related records to each other **by log position**. Design §7 said: *"a
standing fact's eliminator must follow it by `seq`."* Four rules rested on it: stop discharge, the
subscription answer fold, the time-lease boundary, and episode terminality. Position was the common cause
of a defect cluster:

- the forged verdict that truncates `ensure`;
- an unaimed heartbeat moving `progress`;
- the claim cascade ([episode-aim](episode-aim.md));
- #39's stops discharged by a third party's release;
- a stop discharged unseen at the dying breath;
- a new episode's `progress` reading its predecessor's beat.

Position is also why the claim needs a single sequencer, and why time may never trigger a claim, which
leaves a crashed claim on another host wedged ([cross-host-claim-gate](cross-host-claim-gate.md)).

**The direction:** records carry the identities they answer or speak for, so that a wrong or duplicate
claim costs **waste, never corruption**. That is
[if-built-today](if-built-today/README.md)'s second commitment, *identity is data, never position*,
reached incrementally from what ships.

It also serves the regime the owner targets, where messages are slow and silence carries little
information. Safety must not depend on timing assumptions; time may only tell an observer *when to
try*.

## The layers, in dependency order

| # | layer | what it buys | evidence | status |
|---|---|---|---|---|
| 1 | **Reference by name** on control and lifecycle records | order-independent control folds; #39, the cascade and the stale-beat leak fixed | [the spec](../specs/reference-by-name.md) and its tests | IMPLEMENTED (log format 0.3.0) |
| 2 | **Names on values** | a displaced worker's values cannot splice the series | §2 | design open: encoding |
| 3 | **Episode-keyed artefacts**, resumed through the log's vouching | a displaced worker cannot regress a checkpoint | §3 | sound without layer 5: V1, or V2 with layer 2 |
| 4 | **Time as a trigger** (a staleness `ClaimGate`) | the cross-host wedge dissolves | §4 | holds, with conditions |
| 5 | **Fenced worker writes** | the displaced worker learns at once; its writes never land | §5 | **optional for correctness** given 1, 2 and V1; efficiency only |
| 6 | Writing without a shared sequencer | single-spawn becomes best-effort deduplication | — | out of scope here ([machine-partitioned-logs](machine-partitioned-logs.md)) |

Each layer needs the ones above it. Layer 4 is safe only with 1–3 in place. Layer 6 would need 1–4,
plus an answer for the order among claims.

---

## 1. Reference by name

**The rule.** A record that answers, ends or concerns another **names it by an id that is never
reused**. Relating the two is a join on that name, so the answer is the same whatever order records
arrive in. This replaces §7's "must follow by `seq`".

**As built:**

- **Stop.** `lifecycle.stopped.honored` lists the `request_id`s of every stop in the worker's
  registered pending set when it stopped, due or not. A stop stays pending until a `stopped` names it or
  a `nak` bears its id. Stops are named by `request_id`, not by `seq`, because a `nak` can name only by
  request id. Otherwise a refused stop would have no eliminator.
- **The dying breath** is compare-and-appended against a drained control tail, so no stop can land
  between the last drain and the breath unseen.
- **Subscribe.** A `request_id` names one request. Re-sending it while live updates that request; once
  any answer names it, the id is spent. Replacing a subscription means a fresh id plus an `unsubscribe`
  of the old one. "Forbid reuse and nak it" is unsound: the nak names the live original too, and kills
  it (measured).
- **Lease boundary.** The episode that registers an episode-local subscription first writes
  `lifecycle.bound(request_id=lease, {claim_seq})`. A lease is void only through the episode that
  registered it. Two alternatives were measured and rejected:
  - attributing the existing `consumed_seq` watermark to an episode is still positional (its fold moved
    under reordering in 276 of 1,327 scenarios, against 23 for `bound`);
  - client-renewed leases timed by the reader need a stateful, clocked waker.
- **Terminal.** `heartbeat` and `stopped` carry `claim_seq`.

**Schemas:**
- lifecycle-v0.5: `Heartbeat.claim_seq`; `Stopped.claim_seq` (nullable) and `Stopped.honored`; the new
  `lifecycle.bound` topic.
- subscription-v0.3: `request_id` is required on `control.stop`.

**What is still ordered, and why:**

1. **Order among claims.** "Current" is the claim with the greatest seq, and the claim CAS supplies that
   order. A claim's seq is both its name and its rank.
2. **Per-writer order.** A re-sent request's latest schedule, and one episode's newest beat.
3. **Residuals names cannot resolve:**
   - two terminals naming one claim (newest wins, as today);
   - records that name nothing (attributed by the causal window after the current claim);
   - a launcher death with no claim (latest wins).
4. **Windows** after a claim are causal, not attribution: a record cannot name a claim it never saw.

**Measured:**

- **Order independence.** 2,000 histories through the real Worker, 40 causal reorderings each. The
  named stop, `progress` and `live_episode` folds never changed; master's positional folds changed in
  8–40% of reorderings. The named residuals all trace to the two causes in item 3 above, plus one missing
  causal edge: a launch's death follows its claim.
- **Fixes against master:**
  - #39 (a third party's release discharges no stop);
  - a stop landing just before or during the dying breath is named, not discharged unseen;
  - the cascade (a displaced worker's late `stopped` no longer releases its successor);
  - a displaced worker's beat no longer sets `progress`.
- **The startless run** is answered. A stop sent before any worker exists is honored exactly once,
  which [episode-aim](episode-aim.md) could not answer: a stop is named, not scoped to a claim.
- **Migration.** On copies of 2,569 real consumer logs, the backfill is exact on 2,562. The 7
  differences are all a positional bug the names fix: progress reading a prior episode's beat. Where a
  log recorded a misattribution, the backfill copies it faithfully, which is episode-aim's recorded
  objection, and it stands for historical logs.
- **Cost.**
  - Corpus bytes +3.5%.
  - Real-log folds 7–32% slower, all under 3 µs.
  - **`undischarged_stops` is the one scaling cost:** about 10 ms at 2,000 stops, against about 10 µs,
    because an answer can sit anywhere.

**Conditions** (met 2026-10-03, at format 0.3.0):

1. **Each consumer migrates when it bumps its pin, not all at once.** Since 2026-10-03 mycooc,
   translation and runstate-tui depend on runstate through a git pin (`72d9c3f`), so this work
   never reaches them uninvited. A consumer upgrading bumps its pin, then runs `runstate migrate` on
   its logs (`../specs/log-formats.md` §8, `../specs/reference-by-name.md` §7).
2. **The new version refuses an old-format log loudly.** On an unmigrated log, beats name nothing
   and `ensure` and `await_consumed` would **hang silently**. With pinned consumers, old logs and new
   readers will meet, for example when a cockpit or a later upgrade reads them. So a log's format is
   part of its address, and opening one of another format raises `LogFormatMismatch`
   (`../specs/log-formats.md`), with no compatibility reader.
3. **Consumer changes at upgrade time:**
   - mycooc's and translation's stop writers must mint `request_id`s (none of their 184 stops has one);
   - mycooc's `resume_fanout` must name the stop it discharges.
4. **Accept the renewing-client gap.** Across a crash a lease's id is void for every later episode,
   re-sends included, so a client renewing under one id is unserved until it resubscribes under a
   fresh id; one that renews under a fresh id each time waits at most one renewal period
   (`../specs/time-lease-boundary.md`, "Who pays"). The owner chose this (2026-10-03): one id = one
   lease = one episode, and `await_consumed` refuses a void lease id with a `ValueError` telling the
   caller to resubscribe under a fresh id.
5. **`undischarged_stops` has an incremental form,** `Watcher.pending_stops`, for anything that polls
   it on long runs.
6. **The docs are rewritten:** design §7 (the rule is "answers name; the claim CAS orders claims;
   windows are causal"), `../specs/stop-discharge.md`, `../specs/service-worker.md`,
   `../specs/time-lease-boundary.md`, the implementer's guide, and the tests that pinned positional
   semantics or the v0.4 shapes, each replaced by one pinning the named behavior.

**Not fixed:** forgery and authority (a forger names a claim or a stop as easily as before), the value
plane, and `last_activity`.

## 2. Names on values

**Why.** Every consumer value is a raw `channel.send`: 6 sites in translation, about 7 in mycooc, and
none through `Worker.emit`. Under a mistaken takeover a displaced worker's raw values **spliced** the
series in 8 of 36 configurations, and mycooc's separate-handle final metric read as the displaced
worker's in 4 of 12. **Keying by episode at read time does not fix it**, because the displaced records
sit inside the successor's window. Two write-time fixes brought it to 0: stamping the writer's episode
into the value, or fencing the send.

*How it was measured:* a live worker A is frozen longer than the takeover threshold, B takes over, and A
wakes. The 8 of 36 come from a generic job: A frozen after training, after its values, or after saving;
woken before B runs, mid-run, or after B finishes; two ways of telling A it was displaced; raw sends
fenced or not. The 4 of 12 come from mycooc's shape: 20 raw `channel.send(topic="value")` per tick, then
a final metric through a separate channel handle, a forced save and `stopped`; `latest("value",
name="final_acc")` returned A's metric whenever A woke after B finished. Each ran on memory, SQLite (WAL
and DELETE) and Postgres with byte-identical results. Training records which episode computed each step,
so a cell is "spliced" when the series a reader gets holds a different episode's value from the lineage
the model came from.

**The logical rule:** each value carries its writer's claim, by name. A library helper stamps raw sends
from the worker's process. A separate handle must be given the claim explicitly, because it has no
Worker.

**The encoding is a physical choice, and must not change the logical rule.** Measured on copies of 300
logs per consumer:

| | value records, share of all | median value row | stamp as JSON text in the body | stamp as an integer column |
|---|---|---|---|---|
| mycooc | 95% | 89 B | +22% | about +3% |
| translation | 50% | 134 B | +15% | about +2% |

Options, all keeping the name per value:

- **An integer column** instead of JSON text: about 3 bytes. In the envelope, readers would filter on
  it, which satisfies the lift rule. But a claim is a convention concept, in tension with an opinion-free
  envelope. In the body, it argues for a more compact body encoding.
- **Batching inside one record**: one record carries a tick's values under one stamp. mycooc writes
  about 20 values per step, so the stamp falls to about 1%, and the repeated keys are amortised too.
  Trade-offs:
  - per-tick batches across names lose the index that serves `latest(topic, name=)`; per-name batches
    keep it, but amortise less;
  - values become visible at the flush cadence;
  - a crash loses the unflushed batch, which resume from the checkpoint recomputes anyway.
- **Columnar compression at rest** (e.g. a Parquet archive) run-length-encodes a claim column to almost
  nothing, invisibly to the semantics.

**Rejected: run-length-encoded names on a second channel** ("records *a* to *b* were written under
claim C"). It is attribution by position again:

- it reintroduces exactly the cross-stream position comparison layer 1 removes;
- the runs are not contiguous, since the worker's records interleave with others';
- values have no owner between their landing and the range record.

Run-length encoding belongs in storage, not in the protocol.

**Open:** an experiment on bytes and read time, comparing per-record stamps, per-name batches and
per-tick batches on real-shaped data. Then the schema change (value-v0.3, or an envelope field) and
tag-aware `value_series`, `history` and `latest`.

**The key must be finer than the claim** (measured 2026-10-04, §3). A claim names an episode, not a
history. An episode that rolls back and recomputes a step leaves two values named by one claim, and a
lineage read cannot tell them apart. The candidates are one value per (episode, step, name), or a
checkpoint manifest that names its values by seq. The encoding experiment should include them.

### Design decisions so far (2026-10-04, converging with the owner)

The measurements in §3 and [lineage-graph](lineage-graph.md) shaped layer 2 into the **tick record**. The
owner settled the following one question at a time. They supersede the per-value-stamp options above, and
the rollback finding is answered by decision 2.

1. **One record per tick: `lifecycle.tick`, which replaces `lifecycle.heartbeat`.**
   - **What it is.** It is the tick's commit, the worker's liveness beacon, and a lineage node, all at once.
     The three are always written together, at the same moment, by the same writer.
   - **Why one record.** The heartbeat was already the only per-step commit marker (§3), so splitting them
     would encode no real independence.
   - **What it costs.** Liveness reads now carry the value payload, which keeps that payload bounded (decision
     8). This is log format 0.4.0.
2. **A node is named by its tick record's seq,** exactly as a claim is named by its `started`'s seq.
   - `parent` is a seq, or null for a fresh start. No new id space is introduced.
   - The rollback case resolves itself, because the key is the tick, not the claim.
3. **Resume and rewind are one call.** `steps(…, resume_from=node)` names the parent of the next tick.
   - A rewind is the same call with an older node.
   - Without it, the run is a fresh start. Its lineage is then truncated, never spliced.
   - Inferring the parent from the log was rejected, as a positional guess.
4. **The tick's data.** The record carries `values: {name: value | [values…]}` and `answered: [request_id…]`.
   - **Values.** A name appears if it was emitted this tick, or if a fired subscription sampled it. A list
     means the tick carries several elements of that stream (batching).
   - **Answers.** `answered` names the subscriptions this tick served.
   - **Time.** Every value takes the tick's `t`.
   - **Subscriptions** already fire only at ticks, so snapping them to ticks loses nothing.
   - **What it costs.** A request id moves from the envelope into the body, so the backend's `request_ids=`
     index stops serving samples. Nothing in the library or in runstate-tui uses that index for values; the
     TUI follows logs by cursor.
5. **No step in the protocol: option S.** Each value name is a **stream**. Its coordinate is intrinsic:
   the element's index along the lineage.
   - **Batching** advances the index by the length of a list.
   - **Liveness ticks inside slow steps** carry no element of the stream.
   - **Alignment across metrics** is the user's choice: emit `{step, loss}` together.
   - **Alternatives rejected.** An optional step label (P) is an opinion; this one has no scale at all.
     Tick depth (T) breaks once liveness ticks fall inside steps. Conditions over arbitrary values (V)
     reduce to P plus target conditions, and target conditions belong to shipped programs
     ([programmable-subscriptions](programmable-subscriptions.md)).
6. **Reads.**
   - **The default head** is the newest tick of the newest claim that has written one. It uses no clocks:
     the sequencer's order, the claim order and each writer's own order.
   - **Every read** takes `head=`.
   - `value_series`, `history` and `ensure` walk parents from the head.
   - **The take-the-latest collapse is deleted.** A stream index is unique along a lineage, so nothing needs
     collapsing.
7. **The `value` convention is retired.** The 0.3.0 → 0.4.0 migration folds existing `value` records into
   the ticks that commit them. A point outside lineage goes on a topic of the consumer's choosing.
8. **Dense data goes in blobs referenced by name,** never inline. Bodies are JSON text on every backend, so
   a 1M-float array costs about 18–20 MB as JSON against 8 MB raw. Large values belong to the data-plane
   project.
9. **Known cost: the name index.** A single-name `history` now reads every value in the lineage, about 20
   times the bytes at mycooc's shape. This is not a polled path. Measure it in the encoding experiment; if
   it matters, the remedy is a derived index that is never authoritative.

**Still open, in order:**
- **`stopped.final_tick` replacing `final_step`.** It names the last committed tick, which fixes §3's
  stopped-clause defect by construction.
- **The condition algebra under S.**
  - Register sampling keeps tick, time and count cadence.
  - Progress conditions become conditions on stream prefixes.
  - Under S, `steps(total)`'s `total` counts loop iterations, which is a driver convenience, not a protocol
    concept.
- **`ensure`'s signature**, as a stream-prefix demand.
- **The checkpoint recipe:** resume from the most advanced complete checkpoint, and save after the commit.
- **The 0.3.0 → 0.4.0 migration.**

## 3. Episode-keyed artefacts

runstate gives no directory, so this is a **recipe**, not a guarantee.

- **Writing.** `<root>/<claim_seq>/<step>.<head>`, published atomically. An episode never touches
  another's files.
- **The key is the claim seq, not the launch id.** Every claim has a seq, and a hand-run claim carries
  no launch id: 1,083 of 2,819 real claims carry none.
- **Resume rule, as measured (R\*).**
  - A checkpoint `<E>/<s>.<X>` is **vouched** if E's heartbeat lands in E's window at a seq greater than
    `X`, the head when the checkpoint was published. E's own `stopped` with `final_step ≥ s` also vouches.
    **That clause is unsound as written** (measured 2026-10-04, [lineage-graph](lineage-graph.md)). The
    Worker's `final_step` is the last step it *yielded*, set before that step's tick (`worker.py`). So a
    worker that saves before emitting and then raises reports a step whose values never landed, and the
    `stopped` vouches it. Every resume built on that checkpoint keeps the gap permanently. The clause must
    use the last *ticked* step.
  - Values never vouch.
  - Resume takes the **latest episode with a vouched checkpoint**, then its highest vouched step.
  - The first rule proposed (R0: "a landed heartbeat or value at step `s`") failed: it vouched a
    checkpoint written after displacement.

The writer, in full:

```python
def save(root, channel, claim_seq, step, write):
    head = channel.last_seq()                      # X: the log's head when the checkpoint is published
    d = root / str(claim_seq); d.mkdir(exist_ok=True)
    tmp = d / f".{step}.tmp"; write(tmp)
    os.replace(tmp, d / f"{step}.{head}")          # atomic: a reader sees all of it or none
```

**Measured:** with in-place checkpoints, a displaced worker regressed the checkpoint 29 → 7, and 22
steps were recomputed, in 21 of 21 rounds. Episode-keyed and read through R\*: 0 of 21. The DBOS
prototype's 30 → 6 regression is this same failure.

*How it was measured:* worker A trains 30 steps and checkpoints each one. It is frozen with `SIGSTOP`
around step 7; B takes the run over by staleness and finishes; A is woken with `SIGCONT` and writes its
next checkpoint before discovering it was displaced; then `ensure(until={"step": 40})` resumes. 21
rounds per layout (7 each on SQLite WAL, SQLite DELETE and Postgres). R0 failed in one variant: A is frozen just after its step-6 value lands, B takes over
and dies before saving, A wakes and saves step 6, and a third worker resumes. R0 vouched A's
post-displacement checkpoint because its step-6 value had landed, leaving one mismatched cell; R\* gives
none.

**Conditions:**
- every off-log artefact is episode-keyed and read through R\*, including the final model and
  translation's `store.put` outputs;
- unvouched episode directories need garbage collection.

### R\* without the fence (measured 2026-10-04)

R\* as first measured attributed beats by position. That was sound only on top of the fence, which
guaranteed that only E lands records in E's window. A throwaway spike on master (format 0.3.0: named,
unfenced) ported the episode-keyed writer and the staleness gate, and compared three vouching rules for
`E/s.X`. E's own `stopped` with `final_step ≥ s` vouches under the same condition as a beat.

| rule | condition on the vouching beat | checkpoint-plane mismatches (940 decisions) | regressions |
|---|---|---|---|
| V0, R\* as first written | any heartbeat in E's window, seq > X | 8 (the constructed race R2 only) | 0 |
| **V1** | names E, seq > X, **before the next claim** | **0** | 0 |
| V2 | names E, seq > X, anywhere | 172 | 0 |

*How it was measured:*
- **The reference.** The spike's E2 (`SIGSTOP`), E2b and 36-configuration false-death scenarios. The fenced
  reference was reproduced byte for byte first, not cited.
- **Two constructed races.**
  - R1: freeze between reading X and publishing.
  - R2: A is displaced by B, B by C, and B publishes after C while A's late beat lands in B's window.
- **Backends:** memory, SQLite WAL, SQLite DELETE and Postgres; the deterministic runs were identical on all four.
- **What counted as a mismatch:** resuming from a checkpoint whose episode did not own the run when it was
  published.

*Analytic predictions*, written before the results, got V0 and V1 right and V2 half right (see the
open question below).

**Results:**

- **V0 is unsound unfenced.** In R2, A's late beat vouches the checkpoint that B published after C
  displaced it. The spike's own scenarios never build that race, so they showed 0.
- **V1 is sound, and depends on position.** Its argument has three steps:
  1. a record naming E was written by E;
  2. E published before writing it, by program order;
  3. a seq below C's means the record was appended before C.

  The load-bearing comparison is the **beat's** seq against the next claim, not X's. In R1, X sits inside
  A's window while the only beat naming A lands after B's claim. So V1 needs **one sequencer ordering
  beats against claims**, which is stronger than "order among claims needs one arbiter" (below).
- **V2 mismatches under that criterion, but the criterion itself is in doubt.** Two false-death histories
  produce byte-identical logs and identical checkpoint files. A checkpoint published before the takeover in
  one is published after it in the other. Identical logs and files mean identical futures, so "owned the
  run at publish" cannot cause harm by itself. Every harm V2 actually caused, in 40 configurations, came
  through unnamed values: the series held the successor's value at a step where the resumed model was the
  predecessor's.
- **Without the fence, layer 2 is mandatory.** Under every rule, a displaced worker's unnamed values
  disagreed with the resumed model: 1 cell per E2 round when it notices displacement at its next tick, 23
  when it never notices. Fenced, it was 0.

**Conditions V1 needs (untested):**
- a linearizable sequencer for claims and every vouching record (SQLite over NFS is not one);
- no record of E's between its read of X and its publish (no background heartbeat thread);
- X read on E's own handle, so it sees E's own appends;
- a third party's release carries `final_step = null`, otherwise it vouches.

### V2 plus named values: the prediction held (measured 2026-10-04)

The prediction, written before the run: V2 is sound if values name their writer's claim (layer 2) and each
checkpoint records its lineage (the claim that computed each step it covers), and the series is read by
that lineage. A recipe-level manifest beside the checkpoint holds the lineage; no protocol change is
needed. A single remaining splice would have refuted it.

| rule | splices: lineage read / latest-wins read | holes | regressions | steps trained (deterministic runs) |
|---|---|---|---|---|
| V0 | 0 / 20,144 | 0 | 0 | 16,476 |
| V1 | 0 / 20,128 | 0 | 0 | 16,492 |
| **V2** | **0** / 20,208 | 0 | 0 | **15,348** |

*How it was measured:*
- **Which runs.** The same matrix as above, under all three rules, on all four backends: 80,160 cells per
  rule, where a cell is one (name, step) at one reader point.
- **Throwaway changes.** Values carry their writer's claim, and the checkpoint writer stores the model's
  lineage.
- **The reader.** For each step it takes the value named by `lineage[s]`.
- **Scoring.** Splices are judged against a hash of the model's history, never against the claim stamp.
  The latest-wins reader still splices on the same runs; it is the control.
- **Fidelity.** Every earlier count reproduced exactly, so the stamp changed nothing else.

**Results:**
- V2 trained **1,144 fewer steps** than V1, fewer in 28 of 66 paired configurations and never more. The
  saving comes from reusing a displaced worker's genuine late checkpoints instead of recomputing them.
- So V2 plus layer 2 plus the manifest beats V1 on recompute and ties it on correctness.
- It also drops the dependence on a sequencer ordering beats against claims. The next probe measures this.

### V2 is free of log position, apart from the order among claims (measured 2026-10-04)

*The argument, written before the probe.* V2 reads three things:
- a beat that **names** E;
- `seq > X`, which compares E's own beat with E's own frontier. Since E writes nothing between reading X
  and publishing, this means "E beat after it published", a fact of E's program order alone;
- the **order among claims**, which is fixed by the claim CAS and already conceded (see "What this does
  not do").

V1 also compares E's beat with the *next* claim. That is another writer's record, concurrent with E's late
beat whenever E has been displaced.

*The probe.* It used the order-independence machinery shipped with layer 1. Each recorded history was
replayed in random and adversarial linear extensions of its causal order:
- each writer's program order, including the frontiers it read: a claim follows its CAS head's prefix, and
  a publish follows its X;
- "a record follows what it names";
- the chain of claims.

Nothing encodes real time. `claim_seq`, X and the lineage manifests were alpha-renamed to match each
ordering. The run covered 198 histories × 44 orderings, about 8.8M graded cells per edge set, under five
edge sets.

| rule | histories whose pick varied (of the 66 it drove) | splices | holes |
|---|---|---|---|
| V0 | 15 | 0 | 0 |
| V1 | 18 | 0 | 0 |
| **V2** | **0** | 0 | 0 |

**Results:**
- **V2's pick never moved,** under all five edge sets.
- **The checks bite.**
  - The positive control, the latest-wins reader, varied in 186 views.
  - Baseline replays reproduced the harness's own picks exactly.
  - 61% of orderings moved a record, and 8% moved a beat or `stopped` across another writer's claim.
- **V1 varies where a displaced writer's late beat is concurrent with the successor's claim**, as argued.
  The predicted list of histories was wrong in detail, in both directions:
  - E2b never varies, because A reads X after B's claim, so its late beat causally follows it.
  - R2 does vary.
  - A later episode's vouched checkpoint masks the difference.
- **Every pick, under every rule, was clean under the lineage read: 0 splices, 0 holes.** That includes
  V0's picks vouched by a foreign beat. Once the series is read by lineage over named values, the vouching
  rule no longer carries correctness. It decides only **which** clean checkpoint to resume from, and so
  how much is recomputed and whether the choice depends on how concurrent events interleaved.
- **On that axis V2 dominates.** It never varies, and it recomputes least.
- **The "owned the run at publish" criterion is itself position-dependent.** In R2, B's publish is
  concurrent with C's claim, and V1 picks the post-claim checkpoint in 34 of 45 orderings. That is a second
  reason not to grade by it.

**Caveats:**
- **The orderings where V1 and V0 vary need conditions that today's deployment lacks.** With the staleness
  gate, honest clocks and a CAS claim, a fresh beat landing before the claim makes the gate refuse. So those
  orderings arise only under skewed clocks, a weaker gate, or a weak sequencer. That is the slow,
  inconsistent regime this design targets, and V2's invariance covers it.
- **Topic-scoped reads are load-bearing for V0 and V1, not for V2.** If every read counted as a
  full-prefix frontier, nothing would cross a claim and all three rules would be invariant.
- **Not tested:**
  - a weak sequencer for claims themselves (layer 6);
  - re-driving workers and gates under a new interleaving, since the records and files were fixed;
  - lagging visibility of values relative to the checkpoint.

### Is vouching needed at all? Yes, in today's record shapes (measured 2026-10-04)

**The prediction, written before the run.** Heartbeat vouching is replaceable by a direct **presence**
check, V∅+P:
- resume from the latest episode's highest published checkpoint whose values, named in its manifest by
  seq, are all present;
- the predicted result was no more holes or recompute than V2.

**Refuted.** The manifest can name only records that already exist. A worker that **saves before
emitting** publishes checkpoint s before step s's values are written. Two things then look the same:
- "not written yet";
- "not expected", for example the `set()` values of a step nobody subscribed to.

**The measured cases:**
- **Killed between the publish and the emit.** V∅+P resumed past step s, and step s's values never
  exist: a permanent gap of 4 cells. A demand-sampled variant lost a demanded step the same way.
- **V2 was clean in both.** The reference Worker writes `emit` and `set()` **before** the heartbeat. So a
  heartbeat after X is, by accident, **the only per-step commit marker on the log**: "this step's writes
  are complete".
- **Expecting values by (claim, step, name)** fixes the gap. But it then expects `set()` values nobody
  subscribed to, and falls back needlessly.

What held:
- V∅+P has no lag where a worker published and died before beating: 6–12 steps saved per case against V2.
- 0 splices everywhere, including the in-episode rewind; the seq key handles it.
- Order-invariant under reordering.
- It is sound when every value of step s is written before checkpoint s is published.

*How it was measured:*
- **Scenarios:** the l3 matrix, plus:
  - kill orders around save, emit and beat;
  - two forms of visibility lag: a lagging prefix, which is causally consistent, and per-topic lag, which
    is not (V2 shows 18 transient holes under it);
  - demand-sampled `set()` workers.
- **The causal-reordering replay:** 273 histories × 44 orderings. No rule's pick varied.

**The lesson for the design.** What vouching really supplies is a **per-step commit record**. Values written
as separate records carry no "this step is done", so the heartbeat stands in for it. A design whose step
record **is** the commit, with the step's values inside it, makes a direct presence check sound. That design
is [lineage-graph](lineage-graph.md). *Prediction, untested; its falsification test is there.*

**Also:** "latest episode first" made V∅+P resume from a successor's unvouched checkpoint (13 more steps)
where V2 fell back to the displaced predecessor's further, clean one. Head choice by "most advanced
complete step" is the alternative. It is untested.

**A condition the prediction missed: a claim names an episode, not a history.** One episode computing a
step twice breaks the lineage read under every rule. In the probe:
1. A trains 0–5.
2. A rolls back to its own `A/3`; a second `steps(start=4)` loop is legal.
3. A retrains 4–5 and saves 4, then is killed.
4. The resumed `A/5` from the first pass meets two values named A at steps 4 and 5. It splices whichever
   one the reader takes.

So the lineage key must be finer than the claim. Two candidates, both *untested*:
- one value per (episode, step, name);
- the manifest names each step's value records by seq.

The second is reference by name again. This feeds layer 2's open encoding question (§2).

**Not covered:**
- a manifest that is itself wrong (here it is faithful by construction);
- hole shapes: saving before emitting, a kill between publishing and emitting, `set()`-only values,
  asynchronous or batched value writers;
- `ensure` and `history`, which still read latest-wins and splice under every rule until layer 2 makes
  them lineage-aware;
- a weak sequencer.

## 4. Time as a trigger

**The rule change.** From *"time never arbitrates a claim"* (`../specs/observer-clock.md` §rules;
`../dead_ends/failure-detector.md`) to:

> **Time may trigger a claim attempt; the CAS arbitrates; nothing a mistaken claim does can corrupt.**

Safety still assumes nothing about timing. Time decides only *when to try*, and a wrong guess costs
waste. The dead end's objection was that double-live poisons reuse. Under layers 1–3 it no longer
poisons the log, the verdict or the artefacts. Raw values need layer 2.

**As built:**

```python
class ClaimGate(Protocol):
    def admits(self, channel: Channel) -> bool: ...   # asked inside the claim loop, after reading the
                                                       # head; it decides only whether to TRY -- the CAS
                                                       # at that head still arbitrates

class NoLiveEpisode:     # the default: today's rule
    def admits(self, ch): return live_episode(ch) is None

class StaleTakeover:     # admits when no episode is live, OR the live episode's last self-report
                         # (its started.t, or a later heartbeat's t) is older than stale_after --
                         # whether or not its handle resolves from here. Junk clocks fail closed.
    def __init__(self, stale_after: float, now: Callable[[], float]): ...
```

- The gate is a Strategy, injected as `Worker(..., gate=)`.
- **Under layer 1, the gate must read the latest claim's own beat by name** (master's `current_heartbeat`),
  not "the last beat" by position as built. Measured 2026-10-04: a displaced worker's beat, though it names
  its own claim, kept a dead successor looking fresh. B's last beat was 35 s old and the threshold 30 s,
  but A's beat, 15 s old, made the positional gate refuse the takeover. The by-name variant admits it. This
  is a liveness delay, bounded by how long the zombie keeps beating, not a safety fault.
- **The gate must be used at three sites,** not one: the worker's claim, the spawn decision
  (`relaunch_if_needed`, `ensure_served`) and the foreign wait (`foreign_episode`). With the claim site
  alone, `ensure` stays wedged.

**Measured** (deterministic runs on a fake clock, threshold 30 s, all four backends with byte-identical
results):
- **The wedge recovers.** A foreign claim that never runs lands after A's step 5. Before: a fresh claim is
  refused at +10 s, +31 s and +3,600 s, forever. After: refused at +10 s, claimed at +31 s, and the new
  worker resumes from A's vouched step 5 and completes all 20 steps. Through `ensure` this needs the same
  gate at all three sites; with the worker's site alone, `ensure` never returns.
- **Exactly one winner:** 8 threads × 200 trials and 6 OS processes × 30 trials racing to take over a
  stale run, 890 races in all; and 0 violations in 800 races where the incumbent wakes and beats while a
  claim is in flight (its beat vetoes the claim).
- **A false death costs waste only.** A live worker frozen past the threshold (§2's 36 configurations,
  plus 60 `SIGSTOP` rounds) lands nothing above its successor's claim, forges no verdict and regresses no
  episode-keyed artefact. It costs one redone step, plus whatever A keeps computing until it next writes.

**Use witnessed staleness, not the record's `t`.** `StaleTakeover` compared the incumbent's `t` with
the claimant's clock, and skewing the incumbent's clock showed the cost:
- an incumbent clock 40 s slow got a **healthy** worker taken over within 5 s (it cost one redone step);
- an incumbent clock 3,600 s fast delayed recovery from a real death by 3,630 s.

Witnessed staleness means the claimant measures, on its own clock, how long it has watched the log
without a new beat. It is immune to skew. Its cost is one threshold of observation after attaching, the
same bounded cost the lease design accepts.

**Conditions:**
- **Beat interval < threshold in every phase**: startup, long steps, saves. translation's shape — a
  10-unit × 10 s job that beats only at its single tick, a 30 s threshold, a claimant every 5 s —
  livelocked: in 1,000 s, 29 episodes, 0 completions, 276 unit-computations for a 10-unit job. The same
  job ticking once per unit (`steps(total=10)`) had no takeovers.
- **A linearizable CAS.** SQLite over NFS is mycooc's deployment, where the CAS is documented unreliable,
  and it is untested here. There, a takeover could turn today's safe wedge into a double claim. Optuna's
  lockfile append is a candidate fix (untested).
- **Layers 1–3 in place.**

## 5. Fenced worker writes (possibly optional)

**As built** (`worker.py` +104/−19):

```python
def _append(self, body, *, topic, **envelope):
    while not self._displaced:                           # latched: once displaced, nothing lands
        seq = self._ch.send(body, topic=topic, expected_seq=self._head, **envelope)
        if seq is not None:
            self._head = seq; return seq
        last = self._ch.last_seq()                       # refused: classify, never assume
        if any(self._is_rival_claim(e) for e in
               self._ch.read(after=self._head, topics=[Topic.LIFECYCLE_STARTED])):
            self._displaced = True                       # a rival claimed
        else:
            self._head = last                            # someone else's record: adopt, retry
    return None
```

`retire()` keeps its read-based death CAS and checks the tail it already reads for a rival claim. The
flag is separate from `_lost`, so `claimed` and the `tick`/`stop_pending` contracts are unchanged.

**Measured:**

| | master | fenced |
|---|---|---|
| issue #32: records a displaced worker lands after its successor's claim | 14–15, including a forged COMPLETED | 0 |
| false displacements, 700 runs with ~20 raw sends per tick plus a concurrent orchestrator | — | 0 |
| cost per write | 1× | 0.97–1.16× on a clean CAS; 1.75–11.4× on the refusal path (Postgres 226 µs vs 60 µs) |

*How it was measured:*
- **#32:** a reclaim releases a live worker A; B claims and resumes; A and B interleave, and A then calls
  the usual post-loop `stopped(completed=True)`. On master A lands 7 heartbeats, 6 values and a `stopped`
  above B's claim, forging COMPLETED while B is live and releasing B's claim; with B twice as fast, A wins
  4 of 7 loss cells.
- **False displacement:** 100 runs each on 7 backend/threading combinations, ~780 records per run: 20
  raw same-handle values per tick, a concurrent orchestrator (subscribe, malformed subscribe,
  unsubscribe, `launcher.launched`) in a thread or a forked process, and a separate-handle value just
  before `stopped`. All 700 completed; at most 4 retries inside one write.

**Costs:**
- **Starvation.** No progress guarantee. A co-writer appending foreign records at a fixed rate: Postgres
  degrades gracefully to about 4,000 records/s, collapses from about 8,000/s (two such writers cut ticks
  by 91%), and a tight-loop writer starved the worker for its whole lifetime (9,545 consecutive refusals).
  The memory backend starves too. SQLite is unaffected, because its file lock serialises the writers.
- **A wedge without layer 4.** Without layer 4, a mistaken claim by a claimant that never runs strands
  the run.
- **How displacement surfaces matters.** Three styles were tried against both consumers' loop shapes.
  Only refusing further writes silently was never worse than master. `tick()` returning True
  replaced translation's result with a truncated one, and regressed mycooc's shared checkpoint from B's
  step 11 to A's step 3. Raising tripped mycooc-style crash backstops ("non-zero exit and no `stopped`
  since dispatch"), which forge ERRORED over the live successor when the displacement was a mistaken claim.

**The overlap with layer 1.** Names already make a displaced worker's records attributable, and folds
ignore them. What fencing adds:
1. The displaced worker **learns** and can exit, which saves compute.
2. Its records never land.
3. ~~R\*'s measured soundness used it.~~ Not needed: V1 is sound unfenced (§3, measured 2026-10-04).

That is efficiency, not correctness, for checkpoints. **But dropping it makes layer 2 mandatory.** Unfenced,
a displaced worker's unnamed values splice the series under every vouching rule (§3).

**If adopted:**
- silent surfacing plus a public `displaced` property;
- a bounded retry;
- a contention test;
- `../specs/write-authority.md` revision 5 ("the reference Worker never lands a record above a rival
  claim", still excluding raw sends, artefacts and forged claims).

## What this does not do

- **Forgery and authority.** It is still an honor system. Names make attribution checkable; they do
  not authenticate.
- **The artefact plane** is protected only by the consumer's convention (layer 3).
- **Multi-home claims.** A claim's name is the arbiter's seq, so order among claims still needs one
  arbiter. Stops and subscriptions become portable; episodes do not.
- **`last_activity`** still takes the latest record per topic.

## Order of work

1. **Layer 1: IMPLEMENTED 2026-10-03** (`../specs/log-formats.md`, `../specs/reference-by-name.md`).
   The consumers stay on their pins; each migrates and makes its stop-writer changes when it upgrades.
2. **Layer 2:** the encoding experiment, then value names and the stamping helper.
3. **Layer 3:** MEASURED (2026-10-04). Without the fence, V1 is sound. V2 with named values and a lineage
   manifest is equally correct, recomputes less, and is free of log position apart from the order among
   claims, provided the lineage key is finer than the claim. **V2 is the target**, built with layer 2.
   Heartbeat vouching stays: it is today's only per-step commit marker. The long-term direction that would
   retire it is [lineage-graph](lineage-graph.md).
4. **Layer 4:** the gate, with witnessed staleness and by-name beat selection. Decide the NFS question
   before any SQLite-over-NFS deployment uses it.
5. **Layer 5:** optional for correctness, given V1 and layer 2. It stays on the list for efficiency: the
   displaced worker learns at once and stops computing.

## Relationship to existing entries

- **[episode-aim](episode-aim.md): superseded by layer 1** (2026-10-03). Layer 1 does its job by naming,
  and answers its open startless-run case. Its objection about copying misattribution in a backfill
  stands for historical logs. *Possible cleanup:* condense it into a short note in this section.
- **`discharge-by-id`** ([index](index.md)): layer 1 realises it.
- **[claim-eviction](claim-eviction.md), [lifecycle-stopped-unbundling](lifecycle-stopped-unbundling.md):**
  layer 1 closes #39's discharge half. A third party's release is still a `stopped` that asserts a
  verdict, so the unbundling question remains.
- **`../specs/write-authority.md`:** revision 5 if layer 5 is adopted.
- **`../specs/observer-clock.md` §rules, `../dead_ends/failure-detector.md`:** layer 4 replaces "time
  never arbitrates a claim" once adopted; both must say so.
- **[machine-partitioned-logs](machine-partitioned-logs.md):** layer 6; its gate needs layer 1 first.
- **[if-built-today](if-built-today/README.md):** this is its *identity is data* commitment, reached
  from what ships.
