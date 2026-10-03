# Identity in records: names, fenced writes, episode-keyed artefacts, and time as a trigger

**Status:** DESIGN, not converged (opened 2026-10-03). Every layer below has a measured spike behind
it; none is adopted.

**Evidence:** three spike branches, pushed with no PRs (`spike/reference-by-name`,
`spike/fenced-worker-writes`, `spike/time-triggered-claims`), plus a DBOS prototype. Their reports are
untracked working notes beside this repo's other reviews.

## What this is for

runstate relates records to each other **by log position**. Design §7: *"a standing fact's eliminator
must follow it by `seq`."* Four rules rest on it: stop discharge, the subscription answer fold, the
time-lease boundary, and episode terminality. Position is the common cause of a defect cluster:

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
| 1 | **Reference by name** on control and lifecycle records | order-independent control folds; #39, the cascade and the stale-beat leak fixed | `spike/reference-by-name` | adopt, with conditions |
| 2 | **Names on values** | a displaced worker's values cannot splice the series | `spike/time-triggered-claims` (E3, E5) | design open: encoding |
| 3 | **Episode-keyed artefacts**, resumed through the log's vouching | a displaced worker cannot regress a checkpoint | `spike/time-triggered-claims` (E2) | recipe measured |
| 4 | **Time as a trigger** (a staleness `ClaimGate`) | the cross-host wedge dissolves | `spike/time-triggered-claims` (E1, E3, E4) | holds, with conditions |
| 5 | **Fenced worker writes** | the displaced worker learns at once; its writes never land | `spike/fenced-worker-writes` | adopt, with conditions; **may be optional given 1** |
| 6 | Writing without a shared sequencer | single-spawn becomes best-effort deduplication | — | out of scope here ([machine-partitioned-logs](machine-partitioned-logs.md)) |

Each layer needs the ones above it. Layer 4 is safe only with 1–3 in place. Layer 6 would need 1–4,
plus an answer for the order among claims.

---

## 1. Reference by name

**The rule.** A record that answers, ends or concerns another **names it by an id that is never
reused**. Relating the two is a join on that name, so the answer is the same whatever order records
arrive in. This replaces §7's "must follow by `seq`".

**As built:**

- **Stop.** `lifecycle.stopped.honoured` lists the `request_id`s of every stop in the worker's
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
- lifecycle-v0.5: `Heartbeat.claim_seq`; `Stopped.claim_seq` (nullable) and `Stopped.honoured`; the new
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
- **The startless run** is answered. A stop sent before any worker exists is honoured exactly once,
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

**Conditions:**

1. **Each consumer migrates when it bumps its pin, not all at once.** Since 2026-10-03 mycooc,
   translation and runstate-tui depend on runstate through a git pin (`72d9c3f`), so this work
   never reaches them uninvited. A consumer upgrading runs the backfill on its logs, then bumps.
2. **The new version must refuse an old-format log loudly.** On an unmigrated log, beats name nothing
   and `ensure` and `await_consumed` **hang silently**. With pinned consumers, old logs and new readers
   will meet, for example when a cockpit or a later upgrade reads them. A format check that raises
   is required, not a compatibility reader.
3. **Consumer changes at upgrade time:**
   - mycooc's and translation's stop writers must mint `request_id`s (none of their 184 stops has one);
   - mycooc's `resume_fanout` must name the stop it discharges.
4. **Accept the renewing-client gap.** A client renewing a lease with the same id is unserved between a
   crash and its next renewal; it used to be re-anchored. The gap is bounded by the renewal period.
5. **Decide** whether `undischarged_stops` needs an incremental (Watcher-side) form before anything
   polls it on long runs.
6. **Rewrite the docs:** design §7 (the rule becomes "answers name; the claim CAS orders claims; windows
   are causal"), `../specs/stop-discharge.md`, `../specs/service-worker.md`,
   `../specs/time-lease-boundary.md`, the implementers-guide examples, and the 36 tests that pin
   positional semantics or the v0.4 shapes.

**Not fixed:** forgery and authority (a forger names a claim or a stop as easily as before), the value
plane, and `last_activity`.

## 2. Names on values

**Why.** Every consumer value is a raw `channel.send`: 6 sites in translation, about 7 in mycooc, and
none through `Worker.emit`. Under a mistaken takeover a displaced worker's raw values **spliced** the
series in 8 of 36 configurations, and mycooc's separate-handle final metric read as the displaced
worker's in 4 of 12. **Keying by episode at read time does not fix it**, because the displaced records
sit inside the successor's window. Two write-time fixes brought it to 0: stamping the writer's episode
into the value, or fencing the send.

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

## 3. Episode-keyed artefacts

runstate gives no directory, so this is a **recipe**, not a guarantee.

- **Writing.** `<root>/<claim_seq>/<step>.<head>`, published atomically. An episode never touches
  another's files.
- **The key is the claim seq, not the launch id.** Every claim has a seq, and a hand-run claim carries
  no launch id: 1,083 of 2,819 real claims carry none.
- **Resume rule, as measured (R\*).**
  - A checkpoint `<E>/<s>.<X>` is **vouched** if E's heartbeat lands in E's window at a seq greater than
    `X`, the head when the checkpoint was published. E's own `stopped` with `final_step ≥ s` also vouches.
  - Values never vouch.
  - Resume takes the **latest episode with a vouched checkpoint**, then its highest vouched step.
  - The first rule proposed (R0: "a landed heartbeat or value at step `s`") failed: it vouched a
    checkpoint written after displacement.

**Measured:** with in-place checkpoints, a displaced worker regressed the checkpoint 29 → 7, and 22
steps were recomputed, in 21 of 21 rounds. Episode-keyed and read through R\*: 0 of 21. The DBOS
prototype's 30 → 6 regression is this same failure.

**Conditions:**
- every off-log artefact is episode-keyed and read through R\*, including the final model and
  translation's `store.put` outputs;
- unvouched episode directories need garbage collection.

**Open:** R\*'s soundness argument, as measured, used the fence: "only E can land a heartbeat in E's
window". Under layer 1 a heartbeat names E, so the name can do that job instead. Whether the lineage
choice stays correct **without** layer 5 is untested, and it decides layer 5.

## 4. Time as a trigger

**The rule change.** From *"time never arbitrates a claim"* (`../specs/observer-clock.md` §rules;
`../dead_ends/failure-detector.md`) to:

> **Time may trigger a claim attempt; the CAS arbitrates; nothing a mistaken claim does can corrupt.**

Safety still assumes nothing about timing. Time decides only *when to try*, and a wrong guess costs
waste. The dead end's objection was that double-live poisons reuse. Under layers 1–3 it no longer
poisons the log, the verdict or the artefacts. Raw values need layer 2.

**As built:**
- A `ClaimGate` Strategy, injected into the Worker; the default is today's "no live episode".
- `StaleTakeover`, which admits a claim when the live episode's last self-report is older than a
  threshold, even if its handle cannot be resolved.
- **The gate must be used at three sites,** not one: the worker's claim, the spawn decision
  (`relaunch_if_needed`, `ensure_served`) and the foreign wait (`foreign_episode`). With the claim site
  alone, `ensure` stays wedged.

**Measured:**
- The wedge recovers after the threshold, and the run completes with every value.
- Exactly one winner in 890 of 890 claimant races, and 0 violations in 800 races against a waking
  incumbent, whose beat vetoes the claim in flight.
- A live-but-slow worker taken over by mistake costs waste only on the log, the verdict and episode-keyed
  artefacts.

**Use witnessed staleness, not the record's `t`.** The spike compared the incumbent's `t` with the
claimant's clock:
- a claimant clock 40 s slow took over a **healthy** worker within 5 s;
- a clock 3,600 s fast delayed recovery by 3,630 s.

Witnessed staleness means the claimant measures, on its own clock, how long it has watched the log
without a new beat. It is immune to skew. Its cost is one threshold of observation after attaching, the
same bounded cost the lease design accepts.

**Conditions:**
- **Beat interval < threshold in every phase**: startup, long steps, saves. translation's single-step
  shape, with the whole job in one step, livelocked: 29 episodes, 0 completions, 27.6× the compute.
  Beating once per unit removed every takeover.
- **A linearizable CAS.** SQLite over NFS is mycooc's deployment, where the CAS is documented unreliable,
  and it is untested here. There, a takeover could turn today's safe wedge into a double claim. Optuna's
  lockfile append is a candidate fix (untested).
- **Layers 1–3 in place.**

## 5. Fenced worker writes (possibly optional)

**As built.** Every Worker append is `send(expected_seq=head)`. On refusal the worker reads the tail; a
rival `lifecycle.started` there displaces it, and anything else advances `head` and retries.
`worker.py` +104/−19.

**Measured:**

| | master | fenced |
|---|---|---|
| issue #32: records a displaced worker lands after its successor's claim | 14–15, including a forged COMPLETED | 0 |
| false displacements, 700 runs with ~20 raw sends per tick plus a concurrent orchestrator | — | 0 |
| cost per write | 1× | 0.97–1.16× on a clean CAS; 1.75–11.4× on the refusal path (Postgres 226 µs vs 60 µs) |

**Costs:**
- **Starvation.** No progress guarantee: Postgres collapses from about 8k foreign records per second,
  and the memory backend starves completely.
- **A wedge without layer 4.** Without layer 4, a mistaken claim by a claimant that never runs strands
  the run.
- **How displacement surfaces matters.** Only refusing further writes silently was never worse than
  master. `tick()` returning True overwrote artefacts in both consumers' shapes.

**The overlap with layer 1.** Names already make a displaced worker's records attributable, and folds
ignore them. What fencing adds:
1. The displaced worker **learns** and can exit, which saves compute.
2. Its records never land.
3. R\*'s measured soundness used it (see layer 3, Open).

That is efficiency, not correctness, **unless** layer 3 turns out to need it.

**If adopted:**
- silent surfacing plus a public `displaced` property;
- a bounded retry;
- a contention test;
- `../specs/write-authority.md` revision 5 ("the reference Worker never lands a record above a rival
  claim", still excluding raw sends, artefacts and forged claims).

## What this does not do

- **Forgery and authority.** It is still an honour system. Names make attribution checkable; they do
  not authenticate.
- **The artefact plane** is protected only by the consumer's convention (layer 3).
- **Multi-home claims.** A claim's name is the arbiter's seq, so order among claims still needs one
  arbiter. Stops and subscriptions become portable; episodes do not.
- **`last_activity`** still takes the latest record per topic.

## Order of work

1. **Layer 1:** turn the spike into specs, including the loud old-format check. The consumers stay
   on their pins; each migrates and makes its stop-writer changes when it upgrades.
2. **Layer 2:** the encoding experiment, then value names and the stamping helper.
3. **Layer 3:** test R\* without the fence. That result decides layer 5.
4. **Layer 4:** the gate, with witnessed staleness. Decide the NFS question before any SQLite-over-NFS
   deployment uses it.
5. **Layer 5:** adopt or drop, on layer 3's result.

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
