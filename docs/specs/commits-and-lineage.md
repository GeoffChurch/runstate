# Spec: commits and lineage — values committed to a lineage of nodes (log format 0.4.0)

**Status:** DRAFT, revision 2, 2026-10-14; its drafting choices settled 2026-10-23 (review §12, item 14).
Its migration (§7) and §5's pure reads are implemented, and the step was run over every runstate log the
consumers hold (2026-10-28; review §12, item 16); it stays unregistered until layer 2's last commit. For the
owner's review; the rest is not implemented.

This is layer 2 of [`../backlog/identity-in-records.md`](../backlog/identity-in-records.md). It introduces
**log format 0.4.0** under [`log-formats.md`](log-formats.md), and builds on
[`reference-by-name.md`](reference-by-name.md) (format 0.3.0).
- **Revision 1** (2026-10-04) followed decisions 1–15 of identity-in-records §2. Seven independent reviews
  and an alternatives round found nine design decisions it got wrong or left open:
  [`../review-2026-10-04-commits-and-lineage.md`](../review-2026-10-04-commits-and-lineage.md).
- **This revision** applies that review as the owner settled it, one decision at a time (its §12, items
  0–16). Where the two differ, this spec is current. The review keeps the evidence and the alternatives
  rejected; this spec cites it as "review §N".
- **The drafting choices,** which the decisions left open or the drafting changed, were settled by the owner
  one at a time (review §12, item 14). None is open.

**What it gives:**
- **A reader can project one lineage of a run's values.** A displaced worker, a rewind or a recomputed step
  can no longer splice a series; a checkpoint can no longer name work that never committed; and the
  protocol carries no notion of "step".
- **A read can know it is finished.** A completed exit closes its lineage, and a read of a closed lineage is
  settled for good.
- **A claim's end is told from two viewpoints.** The worker's exit is its own word; a supervisor's death
  report names the claim, carries its evidence and ends the claim. Neither has to forge the other.
- **An asker can withdraw its own request,** a stop or a subscription, by name.

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
- **A third party can end a claim only by forging its exit.** No external record names a claim, so mycooc's
  reclaim tool writes the `lifecycle.stopped` the dead worker would have written (review §12, item 5).
- **A stop cannot be withdrawn.** To clear its own staged stop, mycooc's runner forges a `stopped` (review
  §12, item 10b).

## 2. The rule

> **A commit is a lineage node: it names the node it extends and the values it commits. A lineage is the
> chain of nodes from a head, by `parent`. A stream's elements are its values committed along a lineage.
> The claim states who computes and from which node; a rewind restates the node; the exit is the claim's
> last node. A completed exit closes its lineage, and nothing extends an exit that discarded a torn
> iteration.**

**Terms,** each defined below:

| term | of | meaning |
|---|---|---|
| closed / torn / extendable | a node | a completed exit / a non-completed exit that discarded its open iteration / any other node |
| position / head | a claim | where it stands now / its readable exit, else its position |
| settled | a read | its subject cannot grow |
| `ended` | a claim | its exit, or a death report naming it, is on the log |
| held | a claim | not `ended` |
| released | a worker | it has read a death report naming its own claim, and writes nothing more |
| source / eager | a name | sampled on demand / an element whenever emitted |
| lease / durable | a subscription | has a time or count atom, and lives per claim / has neither, and lives per lineage |
| spent | a request | named by a node's `expired`, a `withdraw`, a `nak`, or (a stop) an exit's `honored` |
| live | a subscription | neither spent nor void; an `expired` spends a lease when a node of its claim names it, and a durable one only on a lineage that names it |
| void | a lease | its claim has ended |
| W | a read | the reader's `last_seq()`, taken first; it reads nothing above it |

**Nodes.**
- **A node** is a `lifecycle.commit` or a worker's `lifecycle.stopped`, the exit node. It is named by its own
  seq, as a claim is named by its `lifecycle.started`'s seq.
- **Closed, torn and extendable** (review §12, item 13).
  - A node is **closed** if and only if it is a completed exit.
  - It is **torn** if and only if it is an exit that did not complete and discarded its open iteration:
    every error exit, and an exit that did not complete after `w.discard()` (§4). The wire marks it
    `values: null`. A completed exit is closed whatever its `values`.
  - It is **extendable** if and only if it is neither: a commit, or an exit that did not complete and
    committed its pending values. The worker asserts, by not discarding, that such an exit sits at an
    iteration boundary, so a preempted exit is resumable from, literally.
- **`parent`** names an extendable node of the same run. It is null at the first node after a fresh start or
  a rewind to the root.
- **One record per event.** `commit()` writes one node per iteration. `heartbeat()` writes liveness only and
  is not a node. A rewind writes a record of its own (below). So **`Commits()` counts iterations**; lineage
  depth does not, since a lineage may pass through an exit.

**The commit.**
- **A value belongs to the node whose `values` names its seq.** A value no node names is outside every
  lineage: uncommitted (pending at a rewind, a discard or an error exit, written after its claim's exit, or
  a register), or foreign.
- **A node follows what it names.** Every seq in `values` is lower than the node's own, and so is `parent`.
- **Order within a node** is the order of its `values` list: the writer's own order, which no reordering of
  the log can move. Two `emit`s of one name in one iteration are two elements.
- **A seq named twice along one lineage** is malformed.

**Position: who and where.** The episode did two jobs, and layer 2 gives each its own record (review §12,
item 4).
- **The claim** (`lifecycle.started`) states *who* computes: the witness, given exclusive ownership by the
  claim CAS, which holds liveness, leases and the process's state. It also states *where* it starts:
  `resume_from`, an extendable node of this run, or null: no node of this run: a fresh start, or state no
  node of the run holds, such as a checkpoint from outside it. A claim that resumes from a node must name
  it, or a resumed episode silently becomes a fresh root.
- **Commits advance the position.**
- **`lifecycle.rewind`** restates the position only. A rewind changes where, never who: the witness, its
  leases and its liveness survive it, and its position-dependent state is rebuilt (§4).
- **A claim's position** is left by its newest position-bearing record, in the claim's own order: a commit
  leaves itself, and a `started` or a `rewind` leaves its `resume_from`. A null `resume_from` leaves the
  empty lineage.

**The head.**
- **A claim's head** is its exit, if it has a readable one (§5), and otherwise its position.
- **A node's `parent` is its claim's position before it.** The exit's `parent` is the position at the exit.
- **"Newest" within a claim** is that writer's own order. No rule compares positions across writers.

**Streams and sources: you ask for sources, and measure by eager streams** (review §12, items 6–8).
- **A source** is a name the worker exposes to be sampled on demand, such as a register. The worker
  declares its sources on the claim (`started.sources`). A subscription samples a source at the nodes where
  it fires.
- **An eager stream** is every other value name. It gets an element whenever the worker emits it.
- **Membership.** A committed value is an element of its name's stream if and only if the claim that
  committed it did not declare the name a source. So a name may change kind between claims (code edited,
  then resumed), and membership is decided by name, from one point read per claim.
- **A source's samples are keyed by their node,** (name, node), and read by node. Their identity does not
  depend on who asked, but their existence does, so they are never counted.
- **What names what:**
  - a subscription targets a source, or nothing: pure demand, which keeps a `serve()` worker alive, and has a
    lifetime but no schedule (§3);
  - a progress atom, `prefix_len`, `ensure` and `steps()` name an eager stream or `Commits()`;
  - `prefix`, `series`, `last_element` and `history` name an eager stream.

**The coordinate.**
- **`count(X, node)`** is the number of X's elements on the lineage up to **and including** `node`, where X is
  an eager stream or `Commits()`. It counts elements; the `{count: n}` atom counts a subscription's firings.
- **`Commits()`** counts the `lifecycle.commit` nodes: iterations, exactly. An exit is not a commit, so it
  adds no iteration; its values still count as elements of their streams. **"Step" means this count
  everywhere,** never depth.
- **Every progress number is a count.** Empty is 0, with no sentinel, and a target `n` is reached if and only
  if the count is at least `n`. Elements are indexed from 0, so counts and indices are `len` and `range` of
  one convention.

**Settledness is a property of a node** (review §12, item 5).
- **A read is settled if and only if its subject cannot grow:** always under `AtNode(x)`, since the lineage
  ending at `x` is fixed, and under `LatestClaimHead` if and only if the head is closed.
- **Permanence is trivial,** because a node's kind never changes. The (outcome, node) pair a read returns is
  permanent under either Strategy; only `LatestClaimHead`'s *choice* of head is a report.

**How a claim ended: two viewpoints** (review §12, items 5, 12 and 13).
- **The worker's exit** (`lifecycle.stopped`) is its self-report. There is at most one per claim, by the
  dying-breath CAS, and it is always a node.
- **A supervisor's death report** (`supervisor.died`) is a third party's: it names the claim and carries its
  evidence. It sits on the external plane beside `supervisor.terminated`, which names a launch.
- **A claim has ended** once its exit or a death report naming it is on the log. A record ends the claim its
  `claim_seq` names even when the rest of its body is unreadable (§5). This one predicate, `ended`, serves
  every fold: the claim gate, lease voiding, the verdict and the Watcher. No fold probes a handle, and no
  fold matches a `supervisor.terminated` to a claim. Whether a launch's process is the claimant is a fact
  its launcher can check, and when it can, it writes the death report (§3).
- **The verdict** is the claim's self-report if one is readable, else the death reports naming it. A run that
  nothing ever claimed takes its launch's `terminated`.
- **A worker released by a death report fences itself:** it writes nothing more (§4).

**Requests: by name, per lineage, per claim** (review §12, items 10b and 13).
- **By name, for the whole log:**
  - **acts:** `control.withdraw`, the asker's, and `lifecycle.nak`, the worker's refusal, spend the request
    they name; an exit's `honored` spends a stop;
  - **demand:** a subscription is spent once any node names it in `expired`, or a withdrawal or a refusal
    names it, and nothing is demanded of a closed head. The decision to launch a worker reads demand so,
    which keeps a claim that dies before its first commit from reviving answered demand;
  - **stops:** a stop is a request about the process. One honoured on one branch is not re-armed on another.
- **Per lineage:** a durable subscription, whose atoms are all lineage coordinates, is live on a lineage while
  no withdrawal or refusal names it and no node on that lineage expires it. The Worker serves it so, and its
  answers are the samples on that lineage. Firing and expiry are facts at a node: a node's `fired` names the
  subscriptions that fired there, and its `expired` those whose schedule ended there.
- **Per claim:** a lease, a subscription with any time or count atom, is bound to its claim at drain by
  `lifecycle.bound`. Its clock, fire count and expiry are the claim's, kept across a rewind, and it is void
  once the claim has ended. Its lineage atoms' baselines are read on the lineage, as a durable subscription's
  are (§3).
- **The boundary:** a request whose answers are lineage data is per lineage, and a request about the process
  is by name.

**Positions not compared.** No rule compares the positions of records written by different writers. The
orders the rules rely on are:
- the order among claims, fixed by the claim CAS;
- each writer's own program order;
- "a record follows what it names", which covers a watermark (`consumed_seq`) and the request it passes;
- a record its writer CAS-appends at W follows every record at or below W, all of which that writer read;
- the substrate contract of §5: a reader that has seen a seq has seen every seq below it.

## 3. Wire format

These are the convention bumps. The envelope is unchanged.

| convention | was | records |
|---|---|---|
| lifecycle-v0.6 | lifecycle-v0.5 | `started`, `commit` (new), `heartbeat`, `rewind` (new), `stopped`, `bound`, `nak` |
| value-v0.3 | value-v0.2 | `value` |
| subscription-v0.4 | subscription-v0.3 | `control.subscribe`, `control.stop`, `control.withdraw` (new); `control.unsubscribe` retired |
| supervisor-v0.5 | launcher-v0.4 | `supervisor.launched`, `supervisor.terminated`, `supervisor.died` (new); the `launcher.*` prefix retired |

### lifecycle-v0.6

| record | body | change from v0.5 |
|---|---|---|
| `lifecycle.started` | `{handle, t, resume_from, sources}` | `resume_from` and `sources` added |
| `lifecycle.commit` | `{claim_seq, consumed_seq, parent, values, fired, expired, t}` | new: one per `commit()`; a node |
| `lifecycle.heartbeat` | `{claim_seq, consumed_seq, t}` | `step` removed; not a node |
| `lifecycle.rewind` | `{claim_seq, resume_from, t}` | new: restates the position |
| `lifecycle.stopped` | `{completed, error, claim_seq, honored, parent, values, fired, expired, t}` | `final_step` removed; `parent`, `values`, `fired`, `expired` added; `claim_seq` no longer nullable; `honored` items non-empty; the exit node |
| `lifecycle.bound`, `lifecycle.nak` | unchanged | — |

**The envelope:** every lifecycle record carries `name: null`. `request_id` is the launch id on `started`, as
in v0.5; the request on `bound` and `nak`, as in v0.5; and null on `commit`, `heartbeat`, `rewind` and
`stopped`.

**The fields.** Constraints a schema cannot check are marked *prose*.
- **`resume_from`** (on `started` and `rewind`): the seq of an extendable node of this run, or null. On
  `started`, null is no node of this run: a fresh start, or state no node of the run holds, such as a
  checkpoint from outside it. On `rewind`, it is the root. *Prose:* it names an extendable node below its
  record's own seq.
- **`sources`:** an array of unique, non-empty strings; `[]` declares none.
- **`parent`:** an integer ≥ 1, or null. On a commit it is the claim's position before it; on `stopped`, the
  position at the exit. *Prose:* it names an extendable node, and equals that position.
- **`values`:** an array of unique integers ≥ 1, with order significant. *Prose:* every seq in it is lower
  than the record's own. It has no `maxItems`; a worker that emits and never commits builds one large
  commit.
  - On `stopped` it is nullable: the pending values, then the samples, or null when the exit discarded its
    open iteration. An exit with nothing pending and nothing discarded writes `[]`.
- **`fired`:** an array of unique, non-empty strings: the request ids of the subscriptions that fired at this
  node. A firing takes a sample, so a pure demand never fires (§3, subscription-v0.4). *Prose:* each names a
  source subscription the claim had registered.
- **`expired`:** an array of unique, non-empty strings: the subscriptions whose schedule ended at this node,
  because their `until` held or a one-shot fired. One record holds firing and expiry, so no crash can fall
  between them; item 10b's separate `lifecycle.expired` record is retired by item 13. *Prose:* a request in
  `expired` fires at no later node of that lineage.
- **`honored`** (on `stopped`): an array of unique, non-empty strings, the stops the exit honoured. v0.5's
  items could be empty, though an empty one names no stop, since no request id is empty.
- **An exit that discarded its iteration evaluates nothing:** `values: null` implies `fired: []` and `expired:
  []`, as an `if`/`then`, whether or not it completed.
- **`consumed_seq`** is the control-drain watermark, as in v0.5. No 0.4.0 read uses it except
  `await_consumed` (§5), which takes it from the claim's commits and heartbeats.
- **`t`** is the writer's wall clock when it wrote the record, as in v0.5. A commit is written when its
  iteration ends, after the values it holds (§4, `commit()`). *Prose:* a commit's `t` is at or after the `t`
  of every value it holds that its writer stamped.
- **One shared type.** `parent`, `values`, `fired` and `expired` each reference one shared definition,
  property by property, because `allOf` does not compose with `additionalProperties: false`. `values`'
  definition is the array, which `stopped` wraps as nullable. In the vocabulary, a `Node` Protocol is
  satisfied by both `Commit` and `Stopped`, and lineage walks take a `Node`.
- **`stopped` is the worker's alone.** "completed ⇒ `error` is null", as in v0.5, and "`error` non-null ⇒
  `values` null", both as `if`/`then`.

**Liveness** follows [`observer-clock.md`](observer-clock.md) §5: witnessed arrival, not the record's `t`. A
new `started`, or a new `commit`, `heartbeat` or `rewind` naming the latest claim, resets the Watcher's
witnessed clock: the claim's own records witness it.

### value-v0.3

- **Body:** `{value, t}`. `step` is removed. `t` stays nullable, as in v0.2: translation writes `t: null`.
- **The envelope `name`** is required and non-null: the stream or source.
- **The envelope `request_id`** is null. A value never names a subscription; `fired` does (§2).

Dense data (arrays, tensors) goes in blobs referenced by name, never inline. Large values belong to the
data-plane project.

### subscription-v0.4

**The records** (the envelope's `name` and `request_id` are always present, nullable where stated):
- **`control.subscribe`:** `request_id` is the subscription; `name` is the source it targets, or null for pure
  demand. A pure demand's body takes only an `until` (review §12, item 14): a `from`, an `every` or a
  `{count: n}` atom in it schedules samples a pure demand cannot take, and is refused with a `nak` of reason
  `malformed`, which spends the id.
- **A re-send of a live id** (review §12, item 14) may replace its schedule, which only the worker reads, but
  not what readers read: its target, which `answers` reads, or its class (lease or durable), which voiding
  reads. One that changes either is refused with a `nak` of reason `malformed`, a request that conflicts with
  its own earlier send. The refusal spends the id, since a `nak` names the id and so the live original too,
  and the Worker drops the registration. So a subscription's target is its `name`, fixed.
- **An accepted re-send registers the request afresh** for what the claim holds, as in 0.3.0: a lease's time
  atoms count from the re-send, its fire count returns to 0, and its time baseline is dropped. That is how a
  lease is renewed. What the lineage holds stays: a lease's lineage baselines, and all of a durable
  subscription's firing state, are read on the lineage as before.
- **`control.stop`:** unchanged, except that `StopTrigger.from` takes the new atoms. `name` is null.
- **`control.withdraw`** (new): body `{t}`, with `t` a required number ≥ 0; `request_id` names the request
  withdrawn, a stop or a subscription; `name` is null.
- **`control.unsubscribe`** is retired. Its two jobs went to `control.withdraw` (the asker's) and a node's
  `expired` (the worker's).

**The atoms:**
- **`{stream, n}`:** both required; `stream` at least 1 character, naming an eager stream; `n` from 0 to
  2⁵³−1; `additionalProperties: false`.
- **`{commits: n}`:** `n` from 0 to 2⁵³−1; `additionalProperties: false`. It is the lineage's own clock,
  counting iterations: a position coordinate that takes over `step`'s place as the run-absolute atom in
  [`time-lease-boundary.md`](time-lease-boundary.md). It cannot be circular, since commits grow whatever is
  demanded.
- **Every threshold takes n ≥ 0** (review §12, item 14), as 0.3.0's atoms did. A 0 holds at once. In `from`
  and `until` it re-spells an absent `from` or an `until` that holds, which the free algebra allows; in
  `every` it fires at every evaluation, the exit included, which no other durable schedule says.
- **Unchanged:** `time_seconds` and `count`, and the `any` and `all` combinators.
- **Removed:** the step atom, `{step: N}`.
- **Lease or durable** (§2). A schedule with any `time_seconds` or `count` atom is a lease. Every other
  schedule, whose atoms are all `{stream, n}` or `{commits: n}`, is durable.

**Evaluation:**
- **When.** At each `commit()`, and at an exit that did not discard its iteration (§2); never at a
  `heartbeat()`, which evaluates only stops (§4). Each node is evaluated once, `until` first, with no
  interpolation inside a jump.
- **Where.** Counts are taken at the node being written, its pending values included. Evaluated at the
  previous node instead, `from {commits: 1}` would fire one commit late.
- **`from` and `until`** hold once `count(X, node) ≥ n`.
- **`every`** is the delta rule: it fires when `count(now) − count(at the last firing) ≥ n`, not rounded to a
  multiple.
  - **A lineage atom's baseline,** `{commits: n}`'s or `{stream, n}`'s, is its count at the newest node on
    the lineage whose `fired` names the subscription, for a lease as for a durable subscription (review §12,
    item 14). It lives on the lineage and is rebuilt at every position change, so after a rewind below the
    last firing, `every` counts from the newest firing left on the lineage.
  - **A time atom's baseline** is the wall clock at the lease's last firing, kept by the claim, which a rewind
    does not move.
  - **An atom with no baseline holds,** which makes the first firing immediate (below).
- **Expiry.** A subscription whose `until` holds at a node, or a one-shot that fires there, is named in that
  node's `expired`. One whose `until` already holds when it registers expires at the next node, firing
  nothing (review §12, item 14: 0.3.0 refused it as unsatisfiable, but whether it holds depends on the
  position). A withdrawn subscription is spent by the withdrawal, and no node names it.
- **A source fires once per node.** At a node where one or more subscriptions fire for a source, the Worker
  writes **one** sample of its register, before the node that commits it.
- **A source with no value yet does not fire.** The subscription waits for the first `set`, as reading an
  unbound dataflow variable blocks.
- **Pure demand has a lifetime, not a schedule** (review §12, item 14, amending item 8). A firing takes a
  sample, and a pure demand targets nothing, so it never fires and no node's `fired` names it. It is live
  until its `until` holds at a node, which names it in `expired`, or until a withdrawal or refusal names it;
  as a lease, whose `until` has a time atom, it is also void once its claim has ended. With no `until`, it
  lasts until withdrawn.
- **The first firing is immediate,** as today.

**Refusals,** each a `nak` of reason `unsatisfiable`, judged against the declaration of the claim evaluating
the request:
- a subscription targeting a name that claim does not declare a source: eager streams are read, not
  subscribed;
- a progress atom naming a name that claim declares a source: its count depends on who asked;
- an empty window: a `from` that implies the `until` (on one X, `until`'s n at most `from`'s), restated in
  counts from 0.3.0's check. It does not depend on the position.

The request id is spent, so an asker resubscribes if the kind changes back. Revision 1's circularity refusal
is deleted: circular demand can no longer be expressed.

**What the refusals keep.** A registered source subscription can fire in future, with two exceptions no
refusal can see: one waiting on a source that is never `set`, and a `{stream}` atom naming a stream that is
never emitted, such as a misspelling. Either pins a `serve()` worker. An asker bounds such a request with a
time `until`, which makes it a lease that dies with its claim.

### supervisor-v0.5

The external-report plane, renamed from `launcher.*` (review §12, item 12). Its writer outlives the worker
and reports on it from outside: "a supervisor writing about a child"
([`../design-v0.3-exploration.md`](../design-v0.3-exploration.md) §7).

| record | body | change from launcher-v0.4 |
|---|---|---|
| `supervisor.launched` | unchanged | prefix only |
| `supervisor.terminated` | unchanged | prefix only |
| `supervisor.died` | `{claim_seq, outcome, evidence, t}` | new: the death report |

- **What each names.** `launched` and `terminated` name a launch: the envelope `request_id` is the launch id,
  required as before. `died` names a claim by `claim_seq`, and its `request_id` is null. launcher-v0.4's
  prefix-wide `request_id` rule is split per record.
- **`outcome`** is `preempted`, `errored`, `killed` or `lost`. `lost` says that its writer saw the claim end
  without an exit, and nothing of how (review §12, items 14 and 15): its process gone, or the session that
  wrote it ended (below). A death is never a completion.
- **`evidence`** is a non-empty string that no fold parses: for example `slurm:TIMEOUT`. It is where a report
  says who wrote it, since `died` has no author field. The reference writers prefix it by its kind (below):
  `reap:` for a launcher's reap of a process or a thread, `probe:` for `report_if_dead`'s same-host probe,
  and `session:` for its session probe. Other writers name their own kinds, as `slurm:` does, and the
  migration's reports begin `migrated:` (§7, E1).
- **`t`** is the reporter's clock when it saw the death, as on `terminated`.
- **Unchanged:** the Python `Launcher` classes keep their names, since they launch, and so does the launch id
  (`RUNSTATE_LAUNCH_ID`).

**Who writes a death report** (review §12, items 13 and 14). Writing one is an act, not a kind of party: any
party, whatever else it does, an observer included, may write one for a death it holds evidence of.
- **Evidence, never inference.** A reap (the exit code or signal), a same-host probe that finds the claim's
  process gone, an accounting record, and a backend's session probe that finds ended the session that wrote
  the claim are evidence. Staleness, silence and a cross-host absence are not: a slow worker is alive, and a
  report would fence it (§4), turning a slow run into a dead one.
- **A session probe** (review §12, item 15) is an opt-in backend capability. Postgres offers it
  (`channel-postgres.md`): each connection holds an advisory lock under a fresh key from before its first
  write, the backend stamps every row with its writer's key, and the probe asks whether the key on the
  claim's row is still held. Its evidence is that the claim can write nothing more, since a channel never
  reconnects and every record naming a claim travels on the session that wrote it. It is not about the
  process, which may outlive its session until its next write raises, so it outranks a live pid on the same
  host, and a launcher there kills such an orphan rather than launch a successor beside it.
- **Evidence without a cause reports only while the claim is held.** A probe that finds the process gone, an
  exit 0 and a thread that returned say that it is gone and nothing of how, or of why it left no exit. Each
  reports `lost`, which adds nothing once anything has ended the claim (§5). A signal, a nonzero exit code, a
  raised exception and an accounting record carry a cause, and report whether or not the claim has ended.
- **A read never writes.** `peek_terminal`, `Watcher.poll`, every fold and every handle's `is_alive()` stay
  pure: the Watcher's probe and staleness tiers still return `presumed_dead`, and write nothing. A party
  that watches a run and wants its probe recorded calls `report_if_dead`. The one exception is a reap:
  `waitpid` yields the exit status once, so whoever reaps records it there, and a Watcher that reaps a
  handle it holds writes through the launcher's reap.

**The helpers,** in `launcher.py`:
- **`report_death(ch, claim_seq, *, outcome, evidence, force) -> int | None`** reads the claim at a W. It
  returns `None` when the claim has a readable exit, and raises `ClaimAliveError` when the claim's handle
  probes alive, unless `force=True`: the caller's statement that its evidence outranks the probe. A dead or
  unknown probe (`None`: another host) does not refuse. A claim already ended by a death report is reported
  again (review §12, item 14), so that each holder of evidence records it once, and the verdict keeps them
  all (§5). A `lost` report on a claim that has ended returns `None`: evidence without a cause reports only
  while the claim is held, and the helper checks it at the W of its CAS. It appends `died` with a CAS at W,
  reads again on a lost CAS, and returns the report's seq.
- **`report_if_dead(ch) -> int | None`** is the probe's report (working name): when the latest claim is held,
  and its handle is on this host and probes dead, or the channel's session probe finds the claim's session
  ended, it reports `lost` through `report_death`, with `probe:` and the handle, or `session:` and the
  session, as the evidence, and returns the report's seq; otherwise it returns `None`. On session evidence
  it passes `force=True`, which the evidence earns over a live pid (above).

**The writers:**
- **The launchers** write `died` beside `terminated` when the launch has ended and the newest claim naming the
  launch has no readable exit (review §12, item 14; item 13 named `LocalLauncher`'s reap):
  - **`LocalLauncher`,** at the one place it writes `terminated` (the handle's reap, run by any `wait()` or
    `poll()`, a Watcher's included), when that claim's handle is the launch's, the same `local://host/pid`
    compared whole, as the migration does (§7). It passes `force=True`, since `waitpid` is definitive and
    the freed pid may already be reused. A launch whose process is not the claimant, such as an `sbatch`
    wrapper, writes `terminated` only;
  - **`ThreadLauncher`,** when the thread finishes. The claim names the launch, and a thread cannot be told
    apart by pid, so the launch id alone attributes it. It passes `force=True`: the claim's handle names the
    launcher's own process, which probes alive, and a finished thread is definitive.
- **The library's actors call `report_if_dead`** before they act on a held claim: the Worker's claim path,
  `relaunch_if_needed` and `ensure_served` before they claim or launch, and `ensure` on each poll while it
  waits on a claim, its own drive's or a foreign one (review §12, item 14; item 13 named the claim path).
  `foreign_episode`'s `is_alive()` is a pure read: the latest claim has not ended.
- **A custom producer's launcher** reports its claimant's death through the helper when its launch contains
  the claimant, as a SLURM job or a container does. A launch handle that has ended is no evidence by itself,
  since it may be a wrapper's: `ensure` never reports on its strength (§5).
- **mycooc's reclaim tool** reports from SLURM accounting, through the helper.
- **The outcomes** (review §12, item 14), with `reap: signal s`, `reap: exit n`, `reap:` and the exception,
  `probe:` and the handle, or `session:` and the session, as the evidence:
  - a reap maps a signal to `killed` and a nonzero exit code to `errored`, and a thread that raised is
    `errored`;
  - an exit 0, a thread that returned and a dead probe are `lost`, by the rule above. A worker released by a
    mistaken report exits 0 so (§4), and its reap adds nothing to the report that released it.

## 4. The Worker

**Construction:** `Worker(channel, *, resume_from, sources, save)`. All three are required and keyword-only.
- **`resume_from: NodeRef | LatestClaimHead | None`** (review §12, item 14) writes its node on `started`:
  - **a `NodeRef`** names a fixed node. It is a Protocol whose one member is `.node: int`, named for the one
    thing the Worker reads. A worker that holds state passes its checkpoint object (§6), which is one. A
    worker that holds none may pass `AtNode(x)` (§5), also one, to branch from a node it chooses, such as
    the node before a faulty version's commits; a resume that names only a position is honest when there
    is no state for the node to vouch for. The type cannot tell a checkpoint from any other object with an
    integer `.node`; the load assertion (§6) checks that the state loaded matches;
  - **`LatestClaimHead()`,** for a worker that holds no state, such as a woken service. It continues the
    nearest extendable node of the head it resolves at the claim's own W: the head itself, or for a torn
    exit its `parent`, or a fresh start for the empty lineage. A closed head raises, since the run is
    complete. It continues the previous claim, so a woken service does not re-serve requests an older
    branch expired. A resolved `Head` is not accepted: its node may be `None`, so it is not a `NodeRef`, and
    resuming at one is spelled `AtNode(head.node)`, where it can be seen;
  - **an explicit `None`,** for no node of this run: a fresh start, or state no node of the run holds, such
    as a checkpoint from outside it.

  The Worker validates the node with one point read before the claim CAS: an extendable node of this run, at
  or below W. That covers `steps()`, `serve()` and a worker that calls `commit()` itself. It exposes the node
  as `w.resume_node`, `None` when it names no node, which a rewind moves to its target (§6's load assertion).
- **`sources`** takes the names the worker exposes; an explicit `[]` is fine. It is written on `started`.
- **`save`** takes the checkpoint save policy (§6), a `SavePolicy` such as `NoSave()` or `Every(k, fn)`.

**The claim.** In order:
1. read W, and fold the answers by name up to it (a stop's `honored`, withdrawals and refusals) and the
   lease bindings;
2. if the latest claim is held, call `report_if_dead` (§3): if it reports, start again, and if not, lose the
   claim;
3. CAS `started` at W;
4. after winning, rebuild position-dependent state at `resume_from` (below).

The Worker's own fold drops a subscription only when a withdrawal or a refusal has spent it, or it is a void
lease. Expiry it takes per lineage, from the rebuild, so that it re-arms a durable subscription another
lineage expired.

**Values:**
- **`emit(name, value)`** sends a `value` record at once and adds its seq to the pending values. It raises on
  a declared source.
- **`set(name, value)`** updates a source's register, which is sampled only when demanded. It raises on an
  undeclared name.
- **`include(seq)`** adds a value sent through a separate channel handle to the next node. It raises
  `ValueError` unless:
  - the record at `seq` on this channel is a `value` with a non-null name and a null `request_id`;
  - `seq > claim_seq`;
  - the seq is neither pending, nor committed on the current lineage, which the Worker holds after its
    rebuild, nor discarded by this claim at a rewind;
  - its name is not a declared source.

  It costs one point read. Authorship stays unprovable: an honor system.

**`commit()`** ends an iteration. In order, it:
1. drains control, and the death reports naming its claim; a subscription registered in this drain may fire
   in this commit;
2. evaluates subscriptions at the node being written;
3. writes one sample per source whose subscriptions fired;
4. writes the `lifecycle.commit`, with `values` (the pending values, then the samples), `fired` and
   `expired`;
5. runs the save policy (§6);
6. returns the stop decision.

**A drain** reads the death reports up to its W before it handles any control there, so a released worker
handles nothing (below). Each record a drain writes, a `lifecycle.bound` for a lease it registers or a
`lifecycle.nak` for a request it refuses, is CAS-appended at the tail the drain last read, its own writes
included, and the drain runs again on a lost CAS (review §12, item 14, for `bound`; the 0.4.0 audit,
2026-10-25, for `nak`). So a death report that lands first fences the worker before it binds or refuses
anything. Without the CAS, either record could land after a report the worker has not read, and settle the
request for good, where a racing commit costs one iteration:
- **a binding** would void the lease with the released claim;
- **a refusal** would spend the id. A refusal that is judged against the claim's declaration (§3,
  subscription-v0.4, "Refusals") is the released claim's, which its successor may not share, and a `nak`
  names no claim, so the asker cannot tell it from the successor's.

**The stop decision** (review §12, item 14). A stop's `from` is evaluated wherever the decision is returned:
at each node the claim writes, with the counts at the node being written, and at each `heartbeat()`, with the
counts at the position and the heartbeat's clock. A stop with no `from` holds once drained, as one whose
`from` holds at once does. Lineage counts change only at nodes, so a heartbeat newly decides only a stop
drained there or a time atom come due inside an iteration. Once a pending stop holds, the decision is True for
the rest of the claim, a rewind included: a monotone level, as in 0.3.0. `commit()` and `heartbeat()` return
it, and `stop_pending` reads it without side effects, for a callback guest whose host loop cannot act on a
return value.

**`heartbeat()`** drains control and writes a `lifecycle.heartbeat`. It evaluates no subscription, and of the
conditions only the stops' (above), and commits nothing: it is the liveness record for inside a slow
iteration. It returns the stop decision, so a stop drained mid-iteration whose `from` holds, or one whose time
`from` comes due inside the iteration, lands there.

**`rewind(target: NodeRef | None)`** writes `lifecycle.rewind {claim_seq, resume_from, t}`.
- **The target** is an extendable node of the run, validated as `resume_from`'s is, with the same load
  assertion for a checkpoint (§6). `rewind(None)` rewinds to the root.
- **Pending values are discarded.** They stay on the log outside every lineage; commit first to keep them.
- **The stop decision is unchanged.** A pending stop is decided at the next node or heartbeat, and honoured
  by the exit.

**At every position change,** a claim or a rewind, the Worker rebuilds what depends on the position from the
lineage at the new position:
- the per-stream counts and the `Commits()` count;
- each durable subscription's fire count, `every` baseline and expiry, from `fired` and `expired`. It re-arms
  every durable subscription live on the new lineage, so it keeps expired ones in memory;
- each lease's lineage baselines, from `fired`;
- the source registers are reset: a register holds no value at a new position until the next `set`, so a
  sample never carries a value computed on an abandoned branch.

A lease's clock, fire count and expiry are the claim's, and survive a rewind (§2). Unlogged state follows
the classes recorded in [`../backlog/programmable-subscriptions.md`](../backlog/programmable-subscriptions.md):
lineage folds are rebuilt, wall time survives a rewind and dies with the claim, and folds over unlogged
sources are reset. State with no declared class is treated as the last. The Worker holds the count map of
its lineage, so a rewind to a node on it is O(1); a target elsewhere costs the walk from it.

**`discard()`** declares the open iteration torn (review §12, item 13). Its pending values stay on the log
outside every lineage, as at a rewind. A torn iteration to be redone is a `rewind` to a checkpoint instead.
- **After it, `commit()` and `heartbeat()` write nothing and return True** (review §12, item 14), and
  `stop_pending` is True, so every driver ends, a callback driver's host loop included, which may call its
  hook once more before it stops. A `heartbeat()` does not drain either: a drain could register and bind a
  lease, which the exit, registering nothing, would void at once, losing a request the next claim could serve.
- **After it, `emit`, `set`, `include` and `rewind` raise.** The worker discarded the iteration itself, so a
  later write is its own bug, and could never be committed. A released worker's writes do nothing instead
  (below), since it cannot know when the report landed.
- **The exit after it** writes `values: null` and evaluates nothing. A `stopped(completed=True)` after it is
  closed, not torn.
- **Call it from the worker's own flow,** never from inside a signal handler, which may interrupt a
  `commit()` between its steps. A handler that must stop an iteration it cannot let finish, such as SLURM's
  `--signal=TERM@60` with iterations longer than the grace period, sets a flag, and the loop calls
  `discard()` at its next safe point and leaves; or the handler raises, and the `with` body catches it,
  discards and leaves normally. Either way `__exit__` writes a preempted, torn exit.

**The exit.** `stopped(*, completed, error)` writes the exit node by the dying-breath CAS. In order, it:
1. reads W and drains up to it: stops, withdrawals, and the death reports naming its claim. It registers no
   subscription, as 0.3.0's plain exit did not: one that arrives after the last commit stays live for the
   next claim, where a lease bound here would be void at once;
2. unless the exit discards its iteration (an error, or after `discard()`), evaluates the registered
   subscriptions once, at the exit node;
3. writes the samples once. A failure while writing them makes the exit an error exit: torn, with the
   failure as its `error`, and the samples already written stay outside every lineage;
4. reads W again, drains as in step 1, and CAS-appends `stopped` at it, with `parent` the position,
   `values` (the pending values, then the samples, or null if discarded), `fired`, `expired` and `honored`.
   On a lost CAS it repeats this step only: it does not evaluate again, and a stop first drained here is
   honoured;
5. if the exit is extendable, runs the save policy (§6).

The exit's other rules:
- **`honored`** names every stop pending at the exit, and every stop whose own condition held at a node or a
  heartbeat of the claim, even one withdrawn since (review §12, item 14, amending item 10b's "honours only
  stops still pending"). So what the exit says about why it stopped does not depend on when another writer's
  `withdraw` arrived. The answer fold is unchanged, since `honored` and `withdraw` both spend a stop.
- **A withdrawal after `commit()` or `heartbeat()` has returned True for a stop is too late.** The claim
  stops, and `honored` names the stop.
- **`stopped()` takes no node argument.** A caller-supplied one would be a lineage lie.
- **A completed exit is closed.** It can be neither resumed from nor rewound to. "Train more" resumes from
  the last real checkpoint, an ancestor, which starts a new branch.
- **A non-completed exit that is not torn is extendable.** The next claim may resume from it, and a chunk's
  post-loop values stay on the lineage the next chunk extends.
- **`retire()`,** `serve()`'s careful death, drains everything at steps 1 and 4, subscriptions included, and
  returns False if one arrived at either. Its exit fires nothing, since nothing is registered when it writes.
  It is extendable, so a woken successor that holds no state continues from it.
- **`__exit__`** writes an error exit on an exception, and otherwise `stopped(completed=False, error=None)`,
  unless the worker has already stopped.

**A released worker fences itself** (review §12, item 13). Every drain reads the death reports first (above).
Once one names its own claim, the Worker latches, and every method that writes writes nothing: `commit()`,
`heartbeat()` and `retire()` return True; `stop_pending` is True; `stopped()`, `emit`, `set`, `include`,
`rewind` and `discard()` do nothing; and `released` is True.
- **What it closes:** a worker released by mistake no longer takes a stop meant for its successor, no longer
  binds a lease or refuses a request, and no longer writes checkpoints beside it. A mistaken release costs
  at most the iteration in flight.
- **The race:** a death report that lands before the CAS of the exit, a binding or a refusal moves W, and
  the retry's drain sees it; a commit racing it may land, which is that one iteration.

**Leases.** A lease is bound to its claim at drain by `lifecycle.bound`, written by CAS before the
registration takes effect (above). It survives a rewind, which changes no owner, and is void once its claim
has ended (§2). A withdrawn lease is spent.

**`Target(x, n)`** (review §12, item 14: one type for `steps()`, `ensure` and the producer seam, so a target
passes through `extend` to `steps(until)` unconverted) is "the first n of x": `x` an eager stream name or
`Commits()`, a singleton marker type that no stream name can collide with, and `n ≥ 0`, checked once at
construction. A target of 0 is reached at once. The wire spells a target `{stream: x, n}` or `{commits: n}`.
`prefix` keeps `(stream, n)`: it returns elements, which `Commits()` has none of.

**The drivers:**
- **`steps(until)`** takes a `Target`, or `None` for no bound. It calls `commit()` once per iteration, and
  yields each iteration's index, `count(Commits(), position)` before its commit. So a resumed driver
  continues from the log, run-absolute, and a rewind inside the body moves the next index. It ends when the
  target is reached at the position, or on a stop. 0.3.0's `start=` is removed: the log holds the position
  (rubric S11).
- **`serve()`** is the driver for demand: it calls `commit()` once per iteration while some subscription is
  live for it, a lease of its claim or a durable subscription live on its lineage, and retires at zero. It
  yields the same index.
- **A worker that calls `commit()` itself** from a per-step callback is a first-class driver, as in
  Lightning, the HF Trainer and Keras (mycooc's aligner, review §12 item 3).

## 5. Reads

**The head.** Every read that depends on a branch takes a **required, keyword-only `head: HeadStrategy`**,
chosen once at the edge. `HeadStrategy.resolve(ch) -> Head(node, settled, w)` reads `w = last_seq()` first,
and resolves the head at it. `node` is `None` for the empty lineage, which is not settled.

| Strategy | Head |
|---|---|
| `LatestClaimHead()` | the newest claim's head (§2): the order among claims, then that writer's own order. No clocks, and never an older claim's head (a commitless claim's head may still be an older claim's node). |
| `AtNode(node)` | that node (forensics; reading an abandoned branch). A seq that is not a node, or is above w, raises `ValueError`. It is also a `NodeRef` (§4). |

**A `Head` is itself a `HeadStrategy`** that resolves to itself. So a polled caller resolves once per poll and
passes the value to every read in that poll, and a one-shot caller passes the Strategy. `resume_from` takes
no `Head` (§4).

**The substrate contract.** A reader that has seen seq W has seen every seq below W. Contiguity alone does not
guarantee it: a sequence allocator broke it 30,780 times while leaving a contiguous log (review §11.2, D9).
It becomes a clause of design-v0.2 §4 and of the backend checklist, with a concurrent "read W, then read the
tail" conformance test per backend, by tier. Layer 1's cursors already depend on it.
- **Reads take `W = last_seq()` first** and read nothing above it.
- **Reads never wait.** Completeness is well-formedness.

**Well-formedness.** A read raises `MalformedRecordError(seq, topic, detail)`:
- **Structural, checked on every node walked:**
  - a `parent`, a `resume_from` or a `values` seq at or above its record's own seq;
  - a `parent` or a `resume_from` naming a record that is not an extendable node.
- **Structural, checked where the claim's records are held,** by the Worker at its own writes and by the head
  fold: a node whose `parent` is not its claim's position before it. It stays within one writer's order, so
  §2 allows it, and it rules out a rollback written as a commit with no rewind.
- **Membership, checked only for the records a read resolves:** a `values` seq naming a record that is not a
  `value`.
- **Checked by the pure walk only:** a seq committed twice along one lineage. In a fold it would cost
  O(depth) per value.

A malformed value body raises when its value is read, never when it is counted: membership is decided by the
envelope (topic `value`, and the name), which keeps tolerant readers' counts aligned with the worker's.

**Elements** are typed `Element(node: int, seq: int, value: Any, t: float | None)`.

**The reads:**
- **`prefix_len(ch, x, *, head)`** is `count(x, head)`, for an eager stream or `Commits()`. `progress` is
  retired, not redefined: it was the same number as `prefix_len`, and a new name breaks every unedited call
  site loudly instead of leaving a silent off-by-one. A name that was a source throughout counts 0.
- **`prefix(ch, stream, n, *, head)`** is the threshold read. It returns one of three outcomes, each carrying
  the elements it found and the head it evaluated, whose `node` is `None` on the empty lineage:
  - **`Complete(elements, node)`:** the first `n` elements;
  - **`Pending(partial, node)`:** fewer than `n`, and the read is not settled;
  - **`SettledShort(partial, node)`:** fewer than `n`, and the read is settled. No more will ever come.

  At `n = 0` it is `Complete((), node)`. Only `Complete` has `elements` (review §12, item 14): on the union,
  `.elements` is a type error, so a caller tells the outcomes apart before it reads, and a short read cannot
  pass for a complete one.
- **`series(ch, stream, *, head)`** gives one eager stream's elements in lineage order.
- **`last_element(ch, stream, *, head)`** gives the last element in lineage order, or `None`. That is not
  always the newest by seq: `include` can commit an older value after a newer one.
- **`aligned(ch, names, *, head, axis)`** aligns values by node. Each node that commits any of `names`
  becomes one row.
  - **The row's x** is `count(axis, node)`, where `axis` is an eager stream or `Commits()`. x is 0 before the
    axis's first element, and rows share an x where the axis is sparser than the metric.
  - **Cells are uniform `tuple[Element, …]`:** empty for an absent name, several for several elements. A
    `None | value | list` cell would be ambiguous when a value is itself a JSON array.
  - Sources' samples appear here, keyed by node.
- **`answers(ch, request_id, *, head)`** gives, at each node on the head's lineage whose `fired` names the
  request, the sample of its target: one per firing. A pure demand has none, since it never fires.
- **`history(ch, stream, schedule, *, head)`** replays a schedule over the lineage's nodes, evaluating once
  per node as live evaluation does, and returns the elements of `stream` committed at the nodes where it
  fires. Time atoms read the node's `t`, and the epoch stays the earliest `started.t`. The
  take-the-latest-per-step collapse is deleted: an element's index is unique along a lineage, so nothing
  needs collapsing.
- **`streams(ch, *, head)`** returns `Streams(eager, sources)`, two sets of names with a committed value on
  the lineage, each by the committing claim's declaration. A name that changed kind is in both.
- **`ensure(producer, target)`** drives a producer until the latest claim's lineage holds the target, and
  returns `Reached(node)` or `Unreachable(closed_at)` (review §12, item 14). Neither carries elements, since
  a target may count `Commits()`, which has none. The caller reads anything at `AtNode(node)`, which is
  permanent, so every read composes with it (rubric S6). `Reached.node` is `int | None`: `None` only where
  a target of 0 meets the empty lineage, which holds nothing to read. The two outcomes name their node
  differently, as `prefix`'s name their elements: on the union, `.node` is a type error, so a run that ended
  short of the target cannot be read as one that reached it.
  - **It has no `head` parameter.** Under `AtNode` every read is settled and nothing could be produced, and
    the produce branch follows the newest claim, so `LatestClaimHead` is implied.
  - **Read first:** when `count(x, head) ≥ n`, it returns `Reached` at the head.
  - **Unreachable:** when the head is closed below `n`, it returns `Unreachable` at it without producing. This
    generalizes today's "never re-drive a completed run".
  - **Otherwise it produces:** it calls `producer.extend(target)`, then polls the newest claim's head with
    held folds. It tests the two outcomes before the failure exits, so a drive that resumes from a node
    already holding the target is `Reached`.
  - **A drive's claim** is the claim naming the drive's launch id, which the handle `extend` returns
    carries. A foreign handle carries none, and its drives are attributed to no claim.
  - **The failure exits:**
    - `RunFailedError(result)` when a drive's claim ends with any of its outcomes in `Outcome.failures()`;
    - `NoProgressError` when a drive's claim ends having written no commit. Progress is measured per drive by
      `Commits()`, not by `x`: a chunk may end with no new element of a sparse stream, and a claim that
      resumes from an older checkpoint legitimately lowers every count;
    - while a drive's claim has not ended, `ensure` waits, whatever the drive's handle says: a handle that has
      ended may be a wrapper's, and the claimant's launcher reports its death (§3). On each poll it calls
      `report_if_dead`;
    - `RecordlessExitError` when a drive's process has died with no claim naming its launch, at the fixed
      point only (a re-drive reproduces it) and never while another claim is unended, as today's
      claim-aware, own-spawn-scoped guard does.

    The errors carry the target and the count, where they carried `progress` and `until`.
  - **The producer seam:** a `Producer` has `.channel`, `.run_id` and `.extend(target)`, which returns a
    liveness handle, never `None`, carrying its launch id when it launched one. The default,
    `launch_producer`, launches the worker with `until=target`, which it passes to `steps(until)`. The worker
    then exits preempted at the target, an extendable exit, so a later `ensure` with a larger target
    continues from it.
- **`value_series` is removed.** Its replacements are `streams` plus `series` or `aligned`.

**Bounded reads.**
- **The criterion for a polled read:** its cost is independent of lineage length and log age, given held
  state.
  - **Polled,** through held state: the head, the folds `PrefixLen`, `LastElement`, `Answers` and
    `LiveDemand`, `peek_terminal`, `live_episode`, staleness, `pending_stops`, and `Running`'s head and
    count.
  - **One-shot:** the pure functions `prefix_len`, `last_element`, `answers` and `live_demand`, and `series`,
    `prefix`, `aligned`, `history` and `streams`. `live_demand`'s pure form scans the whole log, since expiry
    sits inside node bodies, and never runs on a polled path.
- **The incremental folds are public and standalone,** like `pending_stops`, because `ensure`'s loop has no
  Watcher and the TUI may use public API only. Each is built with its question and polled with
  `fold.poll(ch, head)`, at O(new records) per poll. Their test: incremental equals pure after every step.
  - **The stream-prefix fold** holds a cursor over nodes, a cursor over the values of S, the set of S's value
    seqs, and `count[node] = count[parent] + |values ∩ S|`, with membership decided per committing claim.
  - **`Answers`** re-derives on a head that does not descend from its last one, and returns the full list.
    A caller that streams answers diffs the lists, since a callback cannot retract an answer.
- **The pure walk** uses windowed bulk reads backward from the head (about 5 µs a row), not point reads (65 µs
  a node on Postgres).
- **The memory backend** reads a tail as a slice (seq = index + 1), and gains a per-topic index: its `latest`
  is O(N) today.

**The verdict.** `peek_terminal` reads the latest claim's self-report if it is readable, else the death
reports naming that claim. A run that nothing ever claimed takes its launch's `terminated`, as today's
null-worker startup crash does.
- **Readable** means the body parses as lifecycle-v0.6's `Stopped`, or supervisor-v0.5's `Died`. An unreadable
  record says nothing of how its claim ended: an unreadable exit is no self-report, and counts as absent for
  the head. Either still ends its claim when its `claim_seq` parses, so it can neither poison the run nor
  strand the claim, and it counts as a `lost` among the death reports' outcomes (review §12, item 14): it
  says only that the claim ended, which is all `lost` says. No fold reads more of its body, since a lenient
  parse is a second reader that another implementation need not share; its seq is in `RunResult.malformed`.
- **Several death reports are all kept** (review §12, item 14). Under death reports, the verdict's outcomes
  are the set of the reports' outcomes, less `lost` when another is present, since `lost` says only what
  every death report says. Nothing is chosen among them: the reports need no order of precedence, the
  verdict does not depend on their order, and it only grows as reports arrive. A consumer's policy tests the
  set, as `ensure` does with "any of its outcomes in `Outcome.failures()`".
- **`RunResult`:**
  - `outcome` becomes `outcomes: frozenset[Outcome]`, never empty: one element under a self-report, a
    launch's `terminated` or an inference, and the set above under death reports. `Outcome` gains `lost`,
    which is in `Outcome.failures()`, as `presumed_dead` is;
  - `reports: tuple[DeathReport, ...]` is new: the readable death reports naming the claim, in seq order,
    each `DeathReport(seq, outcome, evidence, t)`, and empty under every other viewpoint;
  - `final_step` becomes `node`: the exit node's seq under a self-report, `None` otherwise;
  - `claim_seq` is new: the claim the verdict is about, `None` for a run nothing claimed;
  - `malformed: tuple[int, ...]` is new: the seqs of the unreadable records the verdict skipped, so they are
    reported, not raised;
  - `reason: str` becomes `tier: Tier`, a closed enum naming the viewpoint the verdict rests on (review §12,
    item 14): `exit`, the claim's own exit; `died`, the death reports and unreadable records that ended it;
    `terminated`, an unclaimed run's launch; and the Watcher's `probed_dead`, `heartbeat_stale` and
    `episode_lock_released`. Under `died`, `error` is `None`, and the evidence is in `reports`.
- **`worker_completed`** is "the head is closed". Its reading of an exit-0 `terminated` goes, since
  `terminated` no longer enters a claimed run's verdict.

**The answer fold.** `live_demand`, `undischarged_stops` and `Watcher.pending_stops` read §2's rules:
- a stop is spent by an exit's `honored`, a `control.withdraw` or a `nak` naming it;
- a subscription is spent by any node's `expired`, a `control.withdraw` or a `nak` naming it; a lease is also
  void once its claim has ended;
- **nothing is demanded of a closed head:** `live_demand` is empty while `LatestClaimHead` is closed, since
  nothing more can fire on a completed run. A subscription posted to one is never answered and never
  refused: the head shows it settled, and `await_consumed` returns the terminal `RunResult`;
- the spent-id check on a re-sent request is by name, as before.

`ensure_served`'s standing form holds a `LiveDemand` fold, and reads it again after its `report_if_dead`
reports, since the report may have voided the only lease.

**`await_consumed(ch, seq)`** answers "did my request land?":
- a `Nak`, when a refusal names it;
- `None`, accepted, when a watermark of a claim reaches it (a commit's or a heartbeat's `consumed_seq`), or
  an answer names it (a node's `fired` or `expired`, an exit's `honored`);
- the terminal `RunResult`, when the latest claim ended by its own exit above the request, and no watermark of
  that claim reaches it. The exit was CAS-appended after its writer read the request (§2), so the claim saw
  the request and ended without taking it;
- the terminal `RunResult`, when the head is closed, since nothing will ever take it;
- otherwise it waits for the next claim: a request above the ending exit, and a request above the last
  watermark of a claim that death reports ended. A death report shows that its writer read the request, not
  that the dead claim did.

It takes no `control.withdraw`, which needs no worker. `_verdict_record`'s single-record contract goes.

**Sites that change:**
- `Watcher._note_heartbeat`, `_heartbeat_seed` and `await_consumed` parse `Heartbeat(**body)`, which raises
  `TypeError` on the new bodies; they read commits and heartbeats alike. `current_heartbeat` returns the
  claim's newest commit or heartbeat, the records that carry `consumed_seq`; the Watcher seeds staleness from
  the newest of the claim's `started`, commits, heartbeats and rewinds, as §3's liveness counts them.
- `Running.step` becomes `Running.head: Head` and `Running.commits: int`, from a fold held per run.
- `last_activity`'s dated topics are the worker's own records, `started`, `commit`, `heartbeat`, `rewind` and
  `stopped`, and `supervisor.launched` (review §12, item 14). `supervisor.died` and `supervisor.terminated`
  are not: each is dated when its writer saw an end, which may be hours after the run last did anything, and
  counting it would make a dead run look fresh ([`../backlog/claim-eviction.md`](../backlog/claim-eviction.md)
  §3, on #42). Dating a report by the death instead fails for a probe, which cannot know when it was.
- `live_episode`, and with it the claim gate, returns the latest claim's handle unless that claim has ended
  (§2). Its probe moves to `report_if_dead`, which the claim path, `relaunch_if_needed`, `ensure_served`
  and `ensure` call before they act (§3).
- `Watcher.broadcast(name, …)` takes `None` for pure demand; the worker refuses a target that is not a
  source.
- Every fold that routes on `launcher.*` follows the rename.

**Unchanged:** `latest_episode`, and the Watcher's staleness by witnessed arrival.

## 6. The checkpoint recipe

This is a recipe, not protocol. runstate supplies no directory.

- **No lineage lie can be written.** A lineage lie is a checkpoint whose state is not the state committed by
  the node it names. The recipe prevents it at both ends.
  - **Save.** The Worker runs its save policy immediately after a commit lands, and after an extendable exit
    lands. At that instant the state is exactly the state the node commits. It never runs it after a torn
    exit, whose state is part of an iteration ahead of its parent, nor after a closed one. Saving at any other
    moment is outside the recipe.
  - **The policy is a Strategy** (review §12, item 14): the `SavePolicy` Protocol's one method,
    `after(node, *, commits, exit)`, which the Worker calls at each of those moments, with `commits` the
    node's `count(Commits(), node)` and `exit` True at the exit. The Worker fixes *when* a save may happen,
    and the policy decides only *whether*, so no policy can save at a wrong moment. Two ship:
    - `Every(k, fn)` calls `fn(node)` at each commit whose `commits` is a multiple of `k`, and at every
      extendable exit;
    - `NoSave()` never saves, and is passed explicitly, since a chunked run without checkpoints restarts at
      the root every claim.

    Another cadence, such as by wall time for a SLURM job, is the caller's own few lines.
  - **Load.** A checkpoint object satisfies the `NodeRef` Protocol (§4): `.node`, the node it was saved at.
    Pass `resume_from` and `rewind` the object itself, never `AtNode` of its node, so the state loaded and
    the node named come from one place. It carries no driver position: the driver reads its position from
    the log.
- **The order at resume:** choose the checkpoint and read its node; construct the Worker with it, which
  claims; load it; assert `loaded.node == w.resume_node`. A rewind asserts the same after `w.rewind(ckpt)`,
  when `resume_node` is the rewind's target. The assertion catches a consumer that loads from a mutable file
  after claiming.
- **Choosing a checkpoint** is a required Strategy, `choose(ch, candidates, *, head) -> C | None`, over the
  candidates the caller supplies. It is generic in the caller's checkpoint type `C`, bound to `NodeRef`
  (review §12, item 14), so the chosen object loads without a cast. It takes **complete** checkpoints only:
  those whose node is at or below the head's w, which by "a node follows what it names" and the substrate
  contract puts every ancestor and every committed value there too.
  - `OnHeadLineage()` is consistent with reads and the verdict, and respects rewinds. It walks once from the
    head, O(L + K). This is the suggested preset.
  - `MostProgress(stream)` recomputes least, but it can resume from a branch abandoned by a rewind. It is a
    lookup in the count map.
- **No fingerprint guard.** Revision 1's guard had no field on the wire, and construction already prevents
  lineage lies. Its form, for custom loops that bypass the recipe, is in the backlog with its trigger.
- **One writer per checkpoint location** is the recipe's premise. A mistaken release produces two, for at
  most the iteration in flight, which the released worker's fence bounds (§4).

## 7. Migration: format 0.3.0 → 0.4.0

`runstate/migrations/v0_3_0_to_v0_4_0.py`, under `log-formats.md` §6.

**The target** (the owner, 2026-10-25; review §12, item 16). The step writes the conforming 0.4.0 lineage that
records the 0.3.0 history's own events:
- which iterations finished, and where;
- what each claim loaded;
- which stops each worker read, so that its drains follow the 0.3.0 watermarks;
- who reported each death.

A 0.3.0 log often leaves these events open, so more than one lineage can be consistent with it. An inference
is labelled as one.
- **Where 0.3.0's schemas and design pin a meaning,** the step applies its rule.
- **Where no event or pin decides what happened, the consumer decides it** (the owner, 2026-10-25): by a
  stated fact about its writers, or by an explicit choice that resolves the fork (`log-formats.md` §6,
  "Ambiguity"; review §12, item 16). Examples are whether a save that landed finishes an iteration whose
  pass was cut, or when an iteration that no record dates ended. No 0.3.0 meaning is pinned by ruling, and
  the step makes no default guess.
- **How 0.4.0 writes it down is runstate's,** the same for every consumer: the encoding rules below.
  Otherwise a migrated log's records would mean different things depending on who migrated it, and every
  generic reader would need to know the consumer. That is 0.3.0's ambiguity, reintroduced by the migration.
- **The referee** is the prose: §2–§4 and the encoding rules. The simulator in `tests/migration_sim/`
  encodes the same target independently. Where it and the step disagree, the prose decides which one is
  wrong.

**The encoding rules** (the owner, 2026-10-25; review §12, item 16). Each fixes how 0.4.0 writes down a 0.3.0
act that it has no record for, and each is the least-distorting way to do so.
- **E1. A death report the step writes says it was migrated.** Its `evidence` is `migrated:`, then the kind
  of the 0.3.0 record it stands for, then that record's own evidence:
  - `migrated: reap: signal s` or `migrated: reap: exit n`, for one attributed from a `launcher.terminated`
    ("Attributed death reports", below);
  - `migrated: gate: <handle>`, for a claim that a later claim passed 0.3.0's claim gate over ("A claim its
    successor's claim proves dead", below);
  - `migrated: stopped: <its error>`, or `migrated: stopped` when it has none, for a third party's `stopped`
    read as a release.

  *Why it distorts least:*
  - A migrated report is the step's inference from a 0.3.0 record, not a reporter's observation, so it says
    so and names that record, which is all the step knows.
  - Any other text would read as a native report, or drop the kind. `probe:` would assert a same-host probe
    that 0.3.0's gate may not have made, since it may have judged by staleness.
  - No fold parses `evidence` (§3), so nothing a fold reads changes. A person reading `RunResult.reports` can
    tell migrated reports from native ones on any log, whoever migrated it.
- **E2. Every record stays where 0.3.0 put it, and each exit's `honored` follows from the native reads.**
  - The step writes a `control.withdraw` for each id that a `stopped` honours, when that `stopped` is not
    its claim's exit node (it is a third party's, or a fenced worker's own) and the id is not already
    withdrawn. The withdrawal stands just after that `stopped`, its answer, and carries that `stopped`'s `t`.
  - No stop moves.
  - The native drains read whatever lies before them: at each commit, at each logged tick, a stepless
    heartbeat included, and at the exit.
  - Each exit's `honored` follows from those reads by 0.4.0's own rules (§4): every stop pending at the
    exit, and every stop held at a node or heartbeat of the claim.

  *Why it distorts least:*
  - It alters no recorded fact, no place and no watermark, so every 0.3.0 watermark stays true.
  - The target's third item is which stops each worker read, and the watermarks record those reads. An
    exit's `honored` is 0.4.0's consequence of them, not a separate fact to preserve.
  - It needs no fork and no choice per consumer, so every consumer's migrated log reads alike.

  *Its cost.* The native reads can make a migrated exit's `honored` differ from its 0.3.0 list in five cases.
  The owner accepted the cost for cases 1 and 2 (2026-10-25); the controller's rulings generalised case 2 and
  extended the same mechanism to cases 3 to 5, and told the owner; ruling 12a confirms the five (review §12,
  item 16). Each is named here as the simulator's `Truth.costs` labels it:
  1. **`TICKS_EXIT`: a ticks-only exit honours a stop it did not list.** A writer whose exits list only the
     stops it read at its ticks omits a stop logged after its last tick. Its native exit drains every stop
     before it, and honours that one. A stop 0.3.0 left pending is spent there. A stop a commit's native
     drain read, and a third party then cleared with no tick of the worker between, counts here too, though
     only the exit's list differs, as in case 2.
  2. **`CLEARED_AFTER_DRAIN`: an exit honours a stop cleared after a commit read it.** A stop is cleared
     between a commit's native drain, which reads it at the iteration's end (§4), and the worker's next 0.3.0
     read, which skips it. That next read may be its next tick, in either loop order, or its exit. A
     tick-first loop does this when the stop lands inside the body and is cleared before the next tick.
     Where the next read is the exit, it most likely falls at a run's last iteration. The native exit honours
     the stop, which was cleared either way, so only that exit's list differs.
  3. **`ANSWERED_AFTER_CLAIM`: an exit omits a stop answered after its claim.** An `at-claim` writer, whose
     fold applies only the answers made before its claim, took in a stop that another party answered after
     its claim. The withdrawal at the answer precedes its native drain (golden `LATER_COMPLETED`). The stop
     is spent either way.
  4. **`DISCHARGED_EARLIER`: a later exit omits a stop an earlier one discharged.** A later claim already in
     the log, whose 0.3.0 exit honoured a stop, gets a migrated exit that omits it, because an earlier
     migrated exit already discharged it under case 1.
  5. **`INFERRED_INPUT_LIST`: an exit's input list was an earlier step's inference.** In a log born in format
     0.2.0, the 0.2.0 → 0.3.0 step filled every `stopped`'s `honored`, by the reading its consumer chose for
     `stop-honored` (`reference-by-name.md` §6): under `positional`, every stop since the previous
     `stopped`. The native exit's `honored` may then differ from that list in either direction. Every
     difference in such a log is this case, since cases 1–4 describe a writer's own list, and such a log
     carries none. The simulator counts 152 of them, all in its `positional` family.

  *What the cost changes:*
  - The recorded `honored` lists differ at once, in every case.
  - So does the stop fold (`undischarged_stops`), where case 1 spends a stop that 0.3.0 left pending, or left
    for a later claim to honour, and where case 5 can spend a stop the inferred list left pending, or leave
    pending one it named.
  - Running behaviour changes only if the migrated run is resumed: its next claim no longer finds that stop
    pending.
  - The step declares the cost as the divergence `exit-honored` ("The divergences", below), which fires at each
    exit whose `honored` differs from its 0.3.0 list.

**The writer facts** (the owner, 2026-10-25; review §12, item 16). A consumer states what it knows about its
writers in this vocabulary (`v0_3_0_to_v0_4_0_facts.py`), passed as `--assume <module>:<attr>` naming a
`Configuration`, and the step's candidates are the lineages of the histories that write the log and satisfy
every statement and every classifier answer. A fact is a choice a conforming 0.3.0 writer makes that no event
of the log records; none names a 0.4.0 encoding, since how 0.4.0 writes a history down is runstate's (above).
A consumer that states nothing states the whole domain of every fact, with a classifier that answers every
record's whole domain; under it all but the smallest logs exceed the cap, so a consumer states its writers'
facts, and the refusal says so, naming the facts that widen the pass.
- **The reference preset** is a constructor a consumer builds its statement with, `reference(*, ...)` in
  `v0_3_0_to_v0_4_0.py`: a consumer's `--assume` names its own call of it (`FACTS = reference(...)` in its
  module), which is its word that every claim's worker is runstate's reference `Worker` driven by `steps()`,
  and that no third party writes a `stopped`. It states what `worker.py` fixes: each pass after the first
  labelled one past the last and every pass ticked after its body, the loop continuing only past a tick; any
  exit's `final_step` the last pass begun; every stop taken in honoured, the fold at the claim, the newest
  control record its drain read as its watermark; no rewind; a takeover only of a halted worker. Every fact
  the consumer's own code decides is a required keyword-only argument, and none is left wide unasked. A
  claim's first pass is labelled by the consumer's `steps(start=…)`, whose `start` its checkpoint code reads,
  so two facts are its word: `resume`, where that is against the save it loaded (the Worker's docs resume
  "from checkpoint step k" with `start=k`, and what a save labelled `k` holds is the consumer's: pass `k`'s
  result, which `start=k` then runs again, `at`, or the state before pass `k`, which it continues, `after`;
  stated both, a resumed claim whose `load` admits both saves forks at its origin and the `origin` chooser
  decides), and `stride`, `next` where the first pass is at the floor so set, or at 0 when fresh, `skip` where
  it is above (the fact has one class per pass, so a consumer whose first pass skips states both, and the
  engine then admits a skip at any pass). What its clock reads before its first pass (`loading`) matters where
  its code writes a record before the first pass: `emit` raises there, but its own message sends
  caller-clocked points to `channel.send`, as a resumed claim logging the score of the checkpoint it loaded at
  that checkpoint's step; so the preset takes `loading`, and `values` in every phase (the Worker itself writes
  only inside a pass, and a subscription's samples are refused at the boundary, so any other phase is such a
  raw send), and its classifier answers a value every worker role for the two to narrow. The rest: how a
  pass's body ends (`closing`), whether a pass that closes silently wrote a value (`marker`), how its loop is
  left (`loop_end`), its checkpoints (`discards`, `cut_save`, `save_at`, `save_cov`, `leave_save`, `load`),
  and other writers' values (`strays`). A consumer that does not know one passes its whole domain,
  `every(...)`, explicitly. Last, `born`: where its root's logs were born, for a log no evidence dates
  ("Names an earlier step filled", below), which is the root's history, not the Worker's, and which the
  preset takes as a required argument too (`None` states nothing). The preset can be false of a run, as a control record landing between a drain and
  its heartbeat breaks `watermark`; a false word is not silent, since no history then writes the log and the
  refusal names the fact to relax.
- **A fact classifies** every element of one kind, a claim or one kind of act its writer makes, into one class
  of a closed domain. It is defined over what the writer does, never over a pattern in the log.
- **A statement** is a subset of the domain: every such element of the writer's claims falls in it. It is true
  of a history when each element has a class in it. An element falls in every class its behaviour is
  consistent with: one, except where classes coincide on it, as a fresh claim's first values read 0 under
  `loaded` and `zero` alike. The unstated fact is the whole domain, and the empty set says the element never
  occurs. Statements compose by conjunction.
- **Facts belong to a writer version, never to a root.** A consumer states one set for each version of each
  worker and of each third-party writer, and its classifier (below) names the versions, since
  `lifecycle.started` names no writer. One root can hold several versions: mycooc's worker changed its record
  order within its corpus.
- **Facts and the classifier state one concern at two scopes.** A fact bounds what a version's elements may
  be, from what its code does; the classifier answers what one record is, from its content; the engine
  intersects the two. A consumer with no classifier still states facts, and no classifier answer widens a
  fact.
- **What refuses.** An unstated fact refuses nothing by itself. Where its value would decide an output, that
  output is a fork, reported with the facts that would settle it, and refused unless a chooser resolves it. A
  stated fact or a classifier answer that the log contradicts refuses the run: no history writes the log and
  satisfies them all. The refusal names each stated fact and each classifier answer whose relaxation alone, to
  its whole domain, admits a history. Where no single relaxation does, it names the smallest sets whose joint
  relaxation does.
- **Names an earlier step filled are no writer's.** In a log born in format 0.2.0, the 0.2.0 → 0.3.0 step
  filled every `stopped`'s `honored` and every heartbeat's and `stopped`'s `claim_seq`, by the readings its
  consumer chose for `stop-honored` and `record-claim` (`reference-by-name.md` §6). They are that step's
  choices, so no fact states them. The step reads the log's birth, never those readings (below, "Names an
  earlier step filled"; the owner's D3, 2026-10-24, as the controller applied it; review §12, item 16), and
  in a log born in 0.2.0 reads every such name as an inference, not as its writer's word, whichever reading
  made it: under
  `positional`, a displaced worker's heartbeat or `stopped` after a later `started` names the later claim, and
  an exit's `honored` lists every stop since the previous `stopped` (E2's case 5). So any live worker may have
  written such a heartbeat or `stopped`, and no `honored` is checked against its writer's encoding. The facts
  that describe how a writer fills those fields hold only of a log born in 0.3.0.
- **Encodings.** Four facts say only how a writer fills a field of its records: a worker's `fold`, `honored`
  and `watermark`, and a third party's `final-step`. No output depends on them. Under E2 every record stays in
  place and each native drain reads what lies before it, whatever the 0.3.0 worker folded, listed or wrote as
  its watermark, and 0.4.0 records no `final_step`. They stay as checkable statements, since a log can
  contradict them, and as the simulator's rendering inputs. A third party's `honored` is no such encoding: E2
  writes a withdrawal for each id its `stopped` lists, so its list decides an output, and its fact says how
  the list was filled.
- **Other writers' acts.** `takeover` and `strays` describe what writers other than the version's worker do: a
  gate that took its claim over, and a writer holding no claim. They are stated for the claim they concern,
  since the log names neither writer.

The terms. A worker's **pass** is one iteration of its loop, from its start to its **close**, the point its
loop treats as done. A **tick** is a heartbeat with a step. A **save** is of state a later claim can load.
"Feeds" names where a fact's value decides something:
- **End:** a stop's author and meaning, the deaths, the fence point;
- **Span:** the claim's position, its finished passes and where each stands, the value holds, the exit's kind
  and values, and each undated commit's end time;
- **Origin:** fresh, outside, or a node;
- **the cross-claim state:** the checkpoint's content, the promise, and the displaced workers still alive.

**The worker facts,** stated per worker version:

| fact | one element per | classes | feeds |
|---|---|---|---|
| `stride` | pass | `next`, `skip` | Span; Origin; the cross-claim state |
| `resume` | load of a saved state: a claim's (`load` `newest`, `older` or `outside`) or a rewind's (`rewinds` `newest` or `older`) | `after`, `at` | Span; Origin; the cross-claim state |
| `loading` | claim whose clock before its first pass labels a value or fills a `counter` `final_step` | `loaded`, `zero` | Span; Origin |
| `values` | value the worker writes with a step | `loading`, `pass`, `between`, `teardown` | Span; End |
| `beat-pos` | tick | `opening`, `inside`, `after-close` | Span |
| `beat-cov` | pass the worker's loop continued past | `beaten`, `unbeaten` | Span |
| `marker` | pass that closed neither at once after a value nor after a tick of its own | `recorded`, `silent` | Span |
| `closing` | pass that closed | `at-value`, `other` | Span |
| `loop-end` | own `stopped` without an error | `in-pass`, `after-close` | Span |
| `discards` | own `stopped` without an error, inside a pass | `keeps`, `discards` | Span |
| `final-step` | own `stopped` without an error | `yielded`, `finished`, `ticked`, `counter` | Span; End |
| `error-final-step` | own `stopped` with an error | the same | Span; End |
| `cut-save` | pass cut after it began a save of its completed state | `landed`, `closed`, `save-start` | Span; Origin; the cross-claim state |
| `save-at` | save | `before-loop`, `in-pass`, `at-close`, `between`, `partial`, `after-exit` | Origin; the cross-claim state; Span |
| `save-cov` | pass the worker's loop continued past | `saved`, `unsaved` | the cross-claim state |
| `leave-save` | own `stopped` without an error, after a pass's close | `saved`, `unsaved` | the cross-claim state |
| `load` | claim | `fresh`, `restart`, `newest`, `older`, `outside` | Origin; the cross-claim state |
| `rewinds` | rewind | `root`, `newest`, `older` | Span; Origin; the cross-claim state |
| `takeover` | later claim that takes a claim of this version over | `dead`, `live` | End; Span; the cross-claim state |
| `strays` | value no claim's worker wrote, while a claim of this version is the latest | `stepped`, `stepless` | Span |
| `fold` | claim | `at-claim`, `at-drain` | none: an encoding |
| `honored` | own `stopped`, in a log born in 0.3.0 | `exit`, `ticks` | none: an encoding |
| `watermark` | heartbeat | `control`, `claimed`, `tail` | none: an encoding |

- **`stride`:** whether the worker labels a pass with its floor (`next`): 0 when fresh, the last pass's step plus
  one, or the step a load or a rewind resumes at (`resume`). Otherwise it labels it above that (`skip`).
- **`resume`:** where the worker's next pass resumes after it loads a saved state, by its claim's load or a
  rewind, against the saved state's step: the step after it (`after`), the saved pass standing; or the step
  itself (`at`), the saved pass run again. It is what the consumer's checkpoint code fixes: what the step it
  labels a save with means, and which step it hands its loop. A save holds a pass's completed state, its step
  that pass's. A worker that labels the save with the pass it completed and resumes at the label plus one, or
  labels it with the next pass to run and resumes at the label, resumes `after`. One that labels the save with
  the pass it completed and resumes at the label, as the reference Worker's documented `steps(start=k)` after
  loading "checkpoint step k" does, resumes `at`. The two write one 0.3.0 log, a resumed claim whose first pass
  is labelled `k`, and name different `resume_from` commits: pass `k - 1`'s under `after`, pass `k`'s under
  `at`. Stated both, the claim's origin forks between the two saves, and the `origin` chooser decides. A
  rewind resumed `at` runs the rewound pass again: with a tick, an in-claim rewind the boundary refuses; without
  one, a value at a step the claim passed, which the rewind's target then tells apart (`rewinds`).
- **`loading`:** what the worker's step clock reads before its first pass: the loaded state's step (`loaded`,
  0 when fresh), or 0 whatever it loaded (`zero`). That clock labels the values it writes before its first
  pass, and fills a `counter` `final_step` when no pass finished.
- **`values`:** where in its program the worker writes a value with a step:
  - before its first pass, or in a loop that ran none (`loading`);
  - inside a pass (`pass`);
  - after a pass's close, before its next pass or its own `stopped` (`between`), which holds both values after
    the tick and values after the loop;
  - after its own `stopped` (`teardown`).

  Whether a node holds the value is the classifier's answer for its record, `register`.
- **`beat-pos`:** where in a pass the worker ticks: as the pass's first act (`opening`, a loop that ticks
  first); after some act of the pass, before its close (`inside`); or after the close (`after-close`, the
  reference `steps()` driver).
- **`beat-cov`:** whether the worker ticks a pass its loop continues past. A claim's last pass is no element:
  whether it ticked is an event, or `loop-end`'s.
- **`marker`:** whether the worker writes a value of a pass before the pass's close (`recorded`), or nothing
  (`silent`: a run-on the log cannot show). Its element is narrowed to the passes the next two facts leave
  open: a pass that closes at once after a value (`closing`), or after a tick of its own (`beat-pos`), has a
  record before its close by definition.
- **`closing`:** whether the writer treats a pass as done at once after a value of it, its closing value, so
  that nothing of the pass follows that value. The classifier says which values close their pass.
- **`loop-end`:** where the worker leaves its loop for a clean exit: inside an open pass (`in-pass`), or after
  a pass's close or before any pass (`after-close`).
- **`discards`:** whether, at a clean exit inside a pass, the worker's loop leaves the open pass's state
  resumable (`keeps`) or not (`discards`, which 0.4.0 records as an exit with `values: null`).
- **`final-step` and `error-final-step`:** what an own `stopped`'s `final_step` reports:
  - the last pass the worker began (`yielded`), finished (`finished`) or ticked (`ticked`);
  - or its own counter: the last pass it finished, else what its clock read before its first pass (`counter`).

  They are two facts because one worker's exits can fill the field two ways: mycooc's main writes its counter,
  and the Worker's dying breath after an exception writes its last tick.
- **`cut-save`:** whether a pass finished that was cut after the worker began a save of its completed state,
  and before its close. The cut is the worker's death, its fence, or its own `stopped`. It finished once the
  save landed (`landed`), only at its close (`closed`), or once the save started, landed or not
  (`save-start`).
- **`save-at`:** where in its program the worker saves:
  - before its first pass, or in a loop that ran none: the state it loaded (`before-loop`);
  - inside a pass: its completed state (`in-pass`), or its partial state (`partial`);
  - as a pass's last act (`at-close`);
  - after a pass's close, before its next pass or its own `stopped` (`between`);
  - after its own `stopped` (`after-exit`).

  Which node a later load names follows from the place, by §6 and the target's second item. A save records the
  step of the state it holds, whatever the worker's clock reads.
- **`save-cov`:** whether the worker saves a pass's completed state before its next pass begins.
- **`leave-save`:** whether a worker that leaves its loop after a pass's close saves that pass's state before
  its clean exit.
- **`load`:** what the worker loads when it claims:
  - nothing, no claim of the run having saved (`fresh`), or nothing though a save existed (`restart`);
  - the newest save the run's claims had made (`newest`), or an older one (`older`);
  - a checkpoint no claim of the run saved (`outside`).

  `newest` is the constraint across claims that the checkpoint carries forward: under it, which claim a later
  claim loaded depends on what the claims between them saved.
- **`rewinds`:** whether, between passes, the worker reloads a state and drops what it wrote since its last
  position, and which: the root (`root`), the newest save the run had made (`newest`), or an older one
  (`older`). No record shows a rewind, and a rewind to the newest save writes the log no rewind writes, so
  without the fact every point between passes is a fork.
- **`takeover`:** when a later claim's gate judged a held claim of this version dead while its process lived,
  whether its worker had halted (`dead`) or still lived (`live`: the takeover's surviving worker, whom the
  later claim's death report fences, §4).
- **`strays`:** whether a writer that holds no claim writes a value into the run while a claim of this version
  is the latest, with a step (`stepped`) or none (`stepless`). Such a writer is a process before it claims, a
  claim-race loser, or another program: mycooc's orchestrator writes a `completion_reason` register in the
  middle of a held claim. No node holds such a value, and its step is no evidence of where the worker was.
- **`fold`:** when the worker applies an answer that another party wrote after its claim, a `stopped`'s
  `honored` or a nak: once, at its claim (`at-claim`, the reference Worker), or at each drain (`at-drain`,
  `design-v0.2.md` §6). A readable exit can contradict it.
- **`honored`:** which stops an own `stopped` lists: every stop the worker took in, its exit draining too
  (`exit`), or only those its ticks drained (`ticks`). In a log born in 0.2.0 the earlier step filled the list
  (above), so no element exists.
- **`watermark`:** what a heartbeat's `consumed_seq` holds: the newest control record before it (`control`),
  the newest since its claim, else 0 (`claimed`), or the newest record of any topic before it (`tail`).

**Why these and no fewer** (the orthogonality test). Where one fact's classes imply another's, the later
fact's element is narrowed so that it speaks only where the others do not: `marker` above, and `via` below,
which covers only a release of a claim whose worker had exited. `save-cov` and `leave-save` ask one question
of different passes, and a writer can answer them differently: mycooc saves on a throttle, and always when it
leaves its loop. `resume` and `stride` are two facts because they classify different acts, a load and a pass:
`resume` says where a load sets the floor, `stride` whether a pass is at the floor. Where a first pass fits
both readings of one load (pass `k`'s save resumed `after` with a `next` pass at `k + 1`, or `at` with a
`skip` to `k + 1`), the two histories write one lineage, since the floor is read only before the pass and no
piece carries a label, and the engine keeps one state for them. No fact is derivable from the others:
`counter` reads `loading`'s clock, but which convention a writer uses is its own.

**The third-party facts,** stated per third-party writer version, which the classifier names for each
`stopped`:

| fact | one element per | classes | feeds |
|---|---|---|---|
| `acts` | `stopped` it writes | a state (`unnamed`, `live`, `dead`, `exited`, `ended`) with an act (`release`, `note`, `reap`) | End; Span; the cross-claim state |
| `via` | release it writes of a claim whose worker had exited | `if-held`, `always` | End |
| `outcome` | release it writes | `preempted`, `errored`, `killed`, `lost` | End |
| `final-step` | `stopped` it writes | `null`, `progress` | none: an encoding |
| `honored` | `stopped` it writes, in a log born in 0.3.0 | `none`, `undischarged` | none: E2 reads the list itself |

- **`acts`:** when the third party writes a `stopped`, and what it records. It is one fact because a writer's
  act follows the state it finds.
  - The state of the claim it acted on: none (`unnamed`); held with its worker alive (`live`: a stop at any
    time); held with its worker dead (`dead`); ended first by its worker's own `stopped` (`exited`); or ended
    first otherwise, by a release or a later claim (`ended`). In a log born in 0.3.0 the `stopped` names that
    claim. In a log born in 0.2.0 the earlier step filled the name (above), so the claim is read through that
    step's inference, and the state is still that of the claim the writer acted on, never of the name alone.
  - The act: a death report (`release`); nothing ended (`note`: a note, or the clearing of the stops it
    honours); or a process's end (`reap`).

  A release names its claim, so `unnamed` with `release` is no class.
- **`via`:** what the third party's release of a claim whose worker had already exited says: a death only
  while the claim is held, so nothing there (`if-held`); or the death, with its cause, whatever the claim's
  state (`always`; §3, "Who writes a death report"). It is decision D2's meaning, and the only place a
  release's meaning is not already the act's.
- **`outcome`:** the outcome a release reports.
- **`final-step`:** how the third party fills `final_step`: null, or the value runstate's `progress()`
  returned when it wrote (`progress`): the greater of the current episode's heartbeat `step` and its
  `stopped`'s `final_step`, each where an integer (an `int`, never a `bool`), else null. Which records are the
  episode's is the reading the `progress()` of the runstate that wrote the log made, which a log's birth
  tells:
  - in a log born in 0.2.0, by position (runstate `72d9c3f`, `runstate/observables.py:463-501`;
    `design-v0.2.md` §9): the newest heartbeat of any claim, whose `step` counts where an integer, while a
    stepless one (a beacon) adds nothing; and the newest `stopped`, unless a newer `started` follows it;
  - in a log born in 0.3.0, by name (`specs/observables.md`): with `c` the newest `started`, the newest
    heartbeat naming `c`, and the newest `stopped` after `c` naming `c`, which wins even when it is malformed
    or carries no `final_step`; when nothing has claimed, the newest `stopped` naming no claim
    (`claim_seq` null).
- **`honored`:** how the third party fills `honored`: empty, or every stop no `stopped` has named yet
  (`undischarged`). In a log born in 0.2.0 the earlier step filled it (above), so no element exists.

**The classifier** is consumer code: a Strategy, resolved once where the configuration is read and passed to
the engine with the facts. It names what the log leaves open about who wrote a record and what the record is,
reading the record's content and the log around it. Only consumer code may read a consumer's texts ("What
moved out of the generic step", below). The engine hands it each log once, and it returns a reader for that
log, whose state is that log's alone; it never keeps state across logs. The reader has one method per kind of
record whose writer only its content can name. In a log born in 0.2.0 a heartbeat's writer is open too, since
the earlier step filled its `claim_seq`, but the engine resolves that from the facts, not the classifier:

```python
class Classifier(Protocol):
    def read(self, log: Sequence[Row]) -> LogReader: ...

class LogReader(Protocol):
    # the versions of the worker whose `started` is at `seq`
    def started(self, seq: int) -> frozenset[WorkerVersion]: ...

    # who wrote the `stopped` at `seq`, and what it records:
    # OwnStop(), or ThirdStop(version, Release(outcome) | Note() | Reap(exited n | killed s))
    def stopped(self, seq: int) -> frozenset[OwnStop | ThirdStop]: ...

    # ValueRole(role, register): role loading, pass, closing, between, teardown or stray
    def value(self, seq: int) -> frozenset[ValueRole]: ...
```

- **Each method returns every answer the record allows:** one where the content decides, the whole domain
  where it says nothing, and never a default. Where `started` names several versions, the claim satisfies one
  version's whole statement: the engine never mixes one version's facts with another's.
- **A value's role** is where its writer wrote it: a worker's phase, as in `values`, or `stray` for a writer
  holding no claim. `closing` is a pass value that its pass closes at once after. `register` says that no node
  holds it: a value with no step, a stray, or a value its writer keeps outside every lineage, as mycooc's
  `status` register. A register's step, where its writer is the worker, still says where the worker was.
- **The classifier can be wrong silently.** An answer that leaves no history refuses the run, naming the
  record. But where the classifier alone separates two histories, a value's `closing` role or a stop's author,
  a wrong answer gives one wrong lineage, not a refusal. The manifest records those answers as the consumer's
  assertions.

**The end-time fork** is its own projection, `end-time`, asked once the other forks are decided. It reads when
and where each silent act stands that no record dates: an iteration's end (a commit) and a reload (a
rewind).
- **The question.** A native commit's `t` is when its pass ended (§3). A 0.3.0 log dates that end only where a
  record stands at it, as a tick after the close does: the tick then becomes the commit (below). Whether a
  tick dates a commit is therefore part of the reading. So is where an undated commit stands: just after the
  latest of its interval's first record and its claim's own last record before it ("Records", below), which
  sets its lower bound. Where a record of the claim's own that no other reading reads (a beacon, a register)
  may fall on either side of a silent close, two candidates place the commit apart with different bounds,
  and the place is content. The reading of a commit is one of:
  - **dated by the tick at seq s,** one of the ticks that dates it on some remaining candidate; or
  - **ended at t, just after the record at seq a:** a place some remaining candidate leaves it undated at,
    and `t` the chooser's, at or after that place's lower bound. A place is offered once, at the least lower
    bound any candidate has there, with the `t` of the record after it.

  A commit the log dates on every remaining candidate, by one tick, is no fork. Every other commit a
  remaining candidate holds is one: one no tick dates (the step synthesizes it, it stands at an unlogged end,
  or its pass was cut under `cut-save`'s `landed` or `save-start`), or one a tick dates on some candidates and
  not others, or several ticks date. Detection reports a fork for each commit some candidate holds and that
  is not dated by one tick on every live path, an over-approximation of what the decode asks. Whether a tick
  dates a commit depends on facts (where the worker ticks, `beat-pos`; whether a pass closes at once after a
  value, `closing`; how passes run that no record shows, `marker`; where a cut pass ends, `cut-save`), so a
  fork names those that some version states with more than one class. When a pass ended, where no record
  dates it, is an event, which no fact decides.
- **A rewind** is placed as an undated commit is, and no tick dates one, so every rewind is a fork, answered
  ended at t just after a place. Its place sets its bound, and where a record of the claim's own may fall
  on either side of it, the place is content as a commit's is.
- **The order.** The commits are asked from the last claim back to the first, and within a claim in the
  order its worker made them; then the rewinds, in the same order. After each answer only the candidates
  through it are kept: dated by s keeps those on which that tick dates the commit; ended at t after a keeps
  those on which it is undated, stands just after a, and has its lower bound at or before t. Every path kept
  then writes the commit where the answer places it, at a time its bound allows there, and a later answer
  only narrows them, so the step never writes a time below the bound of the path it writes.
- **Its cost.** Each question is about one act of one claim, so the step reads it, and keeps the candidates
  through its answer, over the act's window of records, never the whole log. The window runs from the first
  record that holds the act or a tick dating it, or, if earlier, the first record at or before the act's
  latest interval end that is the claim's own on some candidates and not on others; it ends at the last record
  that holds the act or that interval end, whichever is later. An index of where each act and each claim's own
  records stand is built once, off the candidates the other forks leave, and stays a bound on every set an
  answer leaves, since an answer only removes candidates: no record outside those it lists holds an act; a
  record that is the claim's own on every candidate, or on none, still is; one that is the claim's own on some
  candidates may have become either, and is read as open, which can only start a window earlier. The window
  gives what a pass over the whole log gives. Before it, no candidate holds the act or a tick dating it, and
  each record that could be the claim's own last record before the act is the claim's own on every candidate
  or on none: so every candidate stands alike there, none is removed, and where the act's place may begin is
  known from the index alone. After it, nothing more is decided: the act stands at no record there, and every
  candidate that holds it has its place settled by the window's end, so those kept are those the window kept,
  followed until they reach every record's states again. On a log of one long claim whose own records are
  settled (each the claim's own on every candidate or on none), whose acts each stand at a record or two, the
  windows together cost about one pass over the log, not one per commit; each answer also copies the list of
  the log's records once (a pointer each), so the stage holds a term in acts times records, with a constant
  small enough not to show at mycooc's sizes (365 commits over 8,398 records). A claim whose own records are
  open early (a value or a heartbeat several live workers may have written) widens each of its acts' windows
  back to the first such record, however early: the stage then costs the claim's acts times the distance back,
  at worst its acts times its length, as the whole-log read did. Neither corpus has such a claim, so the bound
  is not tightened; it could start each window instead at the first open record after the claim's last record
  before the act that is its own on every candidate, which sets every path's marks alike. The owner had the
  stage made linear before the step freezes (2026-10-27; review §12, item 16): it had read the whole log for
  each commit's question.
- **The reading's key** is the claim's seq, then the commit's index in its claim from 0, or `rewind <e>` for
  the rewind that closed the claim's epoch e: `{"end-time": {"<claim seq>": {"<n>": {"is": "DatedBy", "seq":
  s} | {"is": "EndedAt", "after": a, "t": t}}}}`. The other
  projections key a reading by its fork's site, the record at which it is read, then its subject. An
  end-time fork has no site of its own before it is decided, since its answer places the commit; its claim
  and the commit's index name it on every candidate.
- **The generic choosers** order every answer offered by its time, then by its place, and take one end: a
  tick at its own `t`, in its own place; a place at a time each defines.
  - **`lower`:** the earliest. A place counts at its lower bound, the newest `t` among the claim's own
    records at or before the commit there. The bound is always defined, since every commit follows its
    claim's `started`, and it meets §3's bound. `last_activity` then never moves past a time the claim's own
    records hold, except to a copied value's `t`, which is true. `history`'s `from` and `until` thresholds
    hold at the native node or a later one, never an earlier one.
  - **`upper`:** the latest. A place counts at the `t` of the next record after it, or at its lower bound
    where that is later (the next record is another writer's, whose clock runs behind: `upper` never answers
    below a bound, which the check would refuse) or after the log's last record. The next record may be
    another writer's, hours later. A claim's last commit before a reclaim tool's `stopped` then takes that
    `stopped`'s `t`, and `last_activity` reads a dead run as fresh until then: the hazard §5 keeps `died` out
    of `last_activity` to avoid.
  - **A consumer's own chooser** may date the end from evidence off the log, such as checkpoint file times. It
    answers one of the ticks offered, or one of the places with a time at or after its lower bound, since a
    commit is written after the values it holds (§3).
- **A closing value is an argument, not a fact.** A consumer whose passes close at once after their closing
  value (`closing`) has its reason to choose `lower`, but the choice stays a chooser's.

**What 0.3.0 pins** (review §12, item 16). The step judges against any writer conforming to format 0.3.0:
lifecycle-v0.5, value-v0.2, subscription-v0.3, launcher-v0.4 and `design-v0.2.md`: what its schemas and its
design write. Another spec may elaborate a pin, never make one. The step relies on these, each written where
it is cited:
- **Names are taken at their word.** A heartbeat's or a `stopped`'s `claim_seq` names the claim it speaks
  for, and a `stopped`'s `honored` names the stops it answered (lifecycle-v0.5; `reference-by-name.md` §2).
  A conforming worker names only its own claim. In every real 0.3.0 log these names are the 0.2.0 → 0.3.0
  step's positional inferences, and those are that step's projections (`reference-by-name.md` §6), not
  this step's.
- **A worker's `stopped` is its halt:** "its existence on the log = a clean, resumable halt"
  (lifecycle-v0.5's `Stopped`), "the cooperative dying breath" (`design-v0.2.md` §7). A halted worker beats
  no more, and writes no second `stopped`. Whether it writes a value after its halt is not pinned (below).
- **`completed: true` is the worker's own claim:** "the worker's opt-in claim of intrinsic completion"
  (lifecycle-v0.5's `Stopped`; `completed-opt-in.md`).
- **A `launcher.terminated` follows its process's end,** since only a `wait()`ing parent writes it
  (`design-v0.2.md` §8). The launch id a claim's `started` re-emits, with the launch's handle equal to the
  claim's, makes that process the claimant.
- **A claim is taken only when the latest one has ended or its worker resolves dead:** the claim CAS is one
  critical section, and "the single-claim guards (run-episodes self-claim, §12.1) rest on it"
  (`design-v0.2.md` §4); "the lazy-launch race is arbitrated by the worker's birth-CAS" (§8).
  `run-episodes.md` §3 spells the guard out ("Worker self-claim", "Live-episode test").
- **A value's `step` is the worker's step clock when it wrote the value** (`design-v0.2.md` §6,
  "step=worker step-clock", and §11, "the worker's logical clock").
- **Steps are run-absolute:** the step clock is the run's, read across its claims. `design-v0.2.md` §9's
  `value_series` reads a resumed episode's steps as the earlier episode's cells ("an episode rewind's
  overlapped steps take the resumed values"), and a subscription with a step atom, which is not
  episode-local (§6), carries over from one episode to the next (§7). `run-episodes.md` §4 elaborates it: a
  resumed worker "continues `value.step` at `k, k+1, …`". That quote pins nothing itself, so whether the
  clock moves one iteration at a time, and where a resumed claim's state came from, stay open (below).

**What 0.3.0 leaves open** is what the writer facts classify and the classifier answers: who wrote a `stopped`
and what a third party's says, which claim's worker wrote a value and in which phase, what a heartbeat's step
marks, whether the step clock moves one iteration at a time, what a `final_step` reports, and where a resumed
claim's state came from. Each is an event of the 0.3.0 history that no record of the log fixes, so it is a
fact's class (above), and where no stated fact settles it, a fork (below).

**The candidates.** The step reads a run in four parts, in this order (`v0_3_0_to_v0_4_0.py`; its engine,
`v0_3_0_to_v0_4_0_engine.py`):
1. **The boundary:** every record read as 0.3.0 pins it, and refused where the step cannot carry it ("The
   refusals", below).
2. **The candidates:** the 0.4.0 lineages of every conforming 0.3.0 history that writes the log and satisfies
   every stated fact and every classifier answer. They are found by one forward-backward pass over the log,
   one layer per record, whose state between two records is the world a later record can depend on: the
   checkpoint's candidates and the older saves promised, the workers alive with their program state, and the
   claims ended, released or exited and the stops spent. An empty set refuses the run. A set of one is the
   answer.
3. **The decode:** where the candidates differ, a fork, decided by its projection's chooser in the decode's
   order (below), the candidates through each answer kept.
4. **The writing:** the decided lineage written as 0.4.0 records by the encoding rules (E1, E2, and "Records",
   below), and renumbered once.

Three limits bound the pass, each set where the command line is read and recorded with every migrated run
(`log-formats.md` §6), since each can decide a result: the **cap**, the states one layer may hold; the
**budget**, the states held summed over every layer, which the pass's memory follows (about 400 bytes a state
built, its edges included, on 664f8795 re-measured after fix 5's floor; the decode then narrows the graph in
place of itself, holding it and the one its answer leaves and sharing every edge the answer keeps, and its
peak is the pass's: about 1.2 times the build's on a long log, under it on a wide one, `cli.py` gives the
measurements); and the **run-on**, how many labels past the log's highest step a run of passes that no record
pins is followed, per claim. Each is a limit, never a reading. A run past the cap or the budget is refused,
naming the record; under no stated facts the refusal names the facts that widen the pass and the reference
preset ("The writer facts"). The horizon refuses a run only where its cut could change a reading: a candidate
that closes a pass at the horizon with its loop free to continue past it, so that only the horizon forbade the
next pass and the commits after it, which Span reads. The refusal names the claims and the facts that can
clear it: `marker` (no pass closes silently), `closing` (none closes but at a value) and `beat-cov` (no loop
continues past an unticked pass). Nothing else the horizon cuts is read. No commit carries a label, so what
the horizon can change is how many commits a run makes, and a commit past it needs a pass begun past it: a
pass begins at its worker's floor or above, and a floor past the horizon comes from a close at the horizon,
which is the marked case where the loop may continue, or from a load or a rewind of a state at the horizon
resumed `after` it, which admits no pass and whose step nothing reads; one resumed `at` it sets the floor at
the horizon. A pass begun at the horizon itself and cut before its close makes at most one commit, at the
horizon, where `cut-save` says its save finished it, which the bound admits; the passes the horizon forbids
all follow a marked close. A load sets the worker's clock, which Origin does not read; a later record that
reads the clock (a step, a `final_step`, a save a later claim loads) bounds it by its own label, below the
horizon, so a load at the horizon is on no candidate where its step is read.

**Names an earlier step filled** are read by the log's birth (above), which is part of what happened, so it is
never read from silence (the final review's I1, 2026-10-28). The step reads it from evidence, in this order:
1. the root's manifest entry of the 0.2.0 → 0.3.0 step;
2. the store's own trace of the log in format 0.2.0 (`log-formats.md` §6, `history`): a sealed v0.2.0 log, a
   legacy tombstone, or a row of `runstate_v0_2_0.sealed_runs`.

The log itself cannot show it, so it is not consulted: the earlier step's output is a log some conforming
0.3.0 history writes, each name it fills one a writer may have written (a minted stop id is a request id like
any other), so nothing in it, which a copy between roots would keep, tells it from a log written in 0.3.0. No
marker is added to the 0.3.0 format to make it so. Evidence only ever shows a birth in 0.2.0: no record says
a log was born in 0.3.0.

Where no evidence decides, birth is the consumer's statement, `born` in its `Configuration`: a closed set of
births, `Birth.V0_2_0` and `Birth.V0_3_0`, the births a log of the root may have. It is a per-root statement,
not a fact, since no writer version decides where a log was born; it sits beside the classifier, the other
part of the configuration that speaks for the root.
- `{0.3.0}` reads each name as its writer's word; `{0.2.0}` reads it as an inference.
- Both is the wider reading, under which names are inferences, so who wrote a heartbeat surfaces as the
  `writer` fork (below) instead of being settled.
- Unstated (`None`, as under no `--assume`), the run is refused, and the refusal names the statement. So is
  the empty set, which says no log of the root lacks evidence. Unlike a fact's, birth's unstated form is no
  whole domain: the two readings are not nested (in a log born in 0.2.0 the engine also reads `progress`
  positionally, as the runstate that wrote it did), so reading silence as either would answer a question
  about what happened.
- Evidence that contradicts the statement refuses the run, naming both: a log the evidence dates in 0.2.0,
  under a statement that leaves 0.2.0 out. A root holding logs of both births therefore states both.

**A consumer that migrated from 0.2.0 with a runstate that wrote no manifest** (master's `runstate migrate`
before this round, which moves 0.2.0 logs to 0.3.0 and seals them) states its logs' birth: its root still
holds each run's sealed v0.2.0 log, which dates the birth by itself, and where it deleted them, or copied runs
between roots without them, it states `born={Birth.V0_2_0}`, or both where the root also holds logs written
in 0.3.0.

**The projections,** each a kind of fork the candidates can leave open, read per subject: each claim, and the
run for what speaks for no claim. A fork's site is the record at which its reading is read.

| projection | what it reads | the generic built-ins |
|---|---|---|
| `end` | how a claim ended: its death reports (outcome and source), the withdrawals its stops make, and the run's process ends | `refuse` |
| `span` | what a claim did: its commits (each its parent, the values it holds and the boundary it stands after), its rewinds, and its exit node (kind, parent, values, `honored`) | `fewest`, `most`, `refuse` |
| `origin` | where a claim's state came from: its `resume_from`, a node of the run or none | `newest`, `refuse` |
| `writer` | the heartbeats a claim's worker wrote, in a log born in 0.2.0, where any live worker may have written one | `newest`, `refuse` |
| `end-time` | each commit not dated by one tick on every candidate, and each rewind: which tick dates it, or where it stands undated and when it ended ("The end-time fork") | `lower`, `upper`, `refuse` |

- **`fewest`** takes the answer that finishes the fewest iterations, then holds the fewest values, then
  continues the newest nodes; **`most`** the most iterations, then the most values, then the newest nodes.
  Each refuses where answers tie, since a built-in never picks among answers it cannot tell apart.
- **`newest`**, for `origin`, takes the answer that continues the newest node offered: a later claim's before
  an earlier one's, within a claim a later commit's, its exit newest, and no node oldest. It refuses a tie.
- **Who wrote a heartbeat is its own projection, `writer`,** because 0.4.0 writes it as the heartbeat's
  `claim_seq`, and folds read that: `await_consumed` takes a claim's watermarks from its commits and
  heartbeats, and the Watcher seeds a claim's staleness from its records, heartbeats among them (§5). It is a
  different question from what a claim did, so Span's built-ins never weigh it. A log born in 0.3.0 names the
  writer, so the projection never forks there; in a log born in 0.2.0 any live worker may have written a
  heartbeat. A fork is read per claim at the heartbeat: did this claim's worker write it. Its facts are
  derived from what its pieces depend on, not listed by hand: a writer is a live worker whose clock the
  tick's step matches and whose watermark convention admits its `consumed_seq`, and which workers live at a
  record and what each one's clock reads is what Span's pieces depend on too, so the fork names Span's facts
  (the cross-claim state included: a `load` settles whose clock could reach a step) and `watermark`, an
  over-approximation.
- **`newest`**, for `writer`, takes the answer in which the claim asked wrote the heartbeat. The decode asks
  the last claim first, so it reads a heartbeat whose writer is open as the newest live worker's.
- **A consumer's chooser** answers with one of the fork's own answers, each carrying a witness lineage. The
  question gives it the subject, the projection, the site and every answer.
- **A fork names the facts that could settle it:** each fact that feeds its projection, or the cross-claim
  state a later claim reads, and that some version states with more than one class. It is an
  over-approximation: stating one may leave the fork open.

**The decode order is a rule** (`log-formats.md` §6): subjects from the last claim back to the first, then the
run; for each subject its `end`, then its `span`, then its `origin`, then its `writer`, which no other reading
depends on; within a projection, position by position in log order. After each answer only the candidates
through it are kept, and the next fork is read off what remains. The end-time forks are asked last, on the
decided lineage, commit by commit, then rewind by rewind ("The end-time fork", the order). The last claim
goes first because its records are the ones a resumed worker and every current read rest on; the claims
before it are read in the light of what it was. Candidates that remain after the whole decode write the same
records: they differ only where no byte is written (which writer wrote a value, whose record names none; who
wrote a `stopped` that becomes nothing, a fenced worker or a third party noting; how far a commit's interval
runs past where it stands). The step takes the first by a fixed order, and a divergence whose rule reads such
a difference (`fenced-exit`, `third-party-note`, `register-value`, `unheld-value`) fires where any remaining
candidate has it, never by the one taken. The pass's states are numbered in an order no hash decides: the
classifier's answers, sets of the consumer's keys, are put in the configuration's declared order once, and
every set of the engine's own values is iterated sorted. So the same log, facts and readings write the same
bytes in every process.

**Records.** How the decided lineage is written down, the same for every consumer. Every input record stands
where 0.3.0 put it (E2), and a record the step adds stands just after the record it follows:
- **A `started`** gains `resume_from`, its claim's origin's node or null, and `sources: []`.
- **A heartbeat with an integer step that dates a commit,** the tick that follows its pass's close at once,
  becomes that commit, in its place, keeping its `t`, its claim and its `consumed_seq`. **Every other
  heartbeat** becomes a `lifecycle.heartbeat` without `step`, naming the claim of the worker the lineage says
  wrote it (the `writer` reading).
- **A commit no record dates,** and a rewind, stands just after the latest of the first record of the interval
  it stands in and its claim's own last record before it: the place its end-time reading names. Its `t` is
  its end-time reading's; a commit's `consumed_seq` is the newest control record before it, 0 for none, since
  its native drain reads there.
- **A commit** carries its claim, `parent`, the values it holds, `fired: []` and `expired: []`. **A rewind**
  carries its claim and the node it reloads as `resume_from`.
- **A `stopped` read as its worker's exit** becomes the exit node: its `completed` and `error`, its claim, the
  `honored` its native drains give (E2), `parent` (the claim's position at the exit), `values` (the open
  iteration's held values, `[]`, or null where the exit discarded them), `fired: []` and `expired: []`;
  `final_step` is dropped. A worker's own `stopped` after a release or a takeover fenced it (§4) is no exit
  node, and leaves the log.
- **A third party's `stopped`** becomes what its act records: a release, a death report in its place, unless
  §3 holds it back (through the helper on a claim with a readable exit, or without a cause on a claim that has
  ended); a process's end, a `supervisor.terminated` in its place under a launch id minted `backstop@<seq>`,
  fresh against every request id the run bears or honours; a note, nothing.
- **A stopped that becomes nothing leaves the log,** and each id it honours, not already withdrawn, is a
  `control.withdraw` in its place (E2), as each such id of any non-exit `stopped` follows it at once. Several
  withdrawals of one `stopped` stand in its `honored` order, each id once at its first mention, each dated by
  that `stopped`'s `t`; an M10 body, which has no `t`, dates them by its `created_at`.
- **A death report attributed from a reap** stands just after its `launcher.terminated`: a claim whose
  launch's `terminated` carries the claim's own handle, the same `local://host/pid` compared whole, found by
  the launch id its `started` re-emitted and its `launched`'s handle, gets one when nothing ended it first and
  it is the newest claim naming the launch, as §3's live launcher reports only for it. A signal reads
  `killed`, a nonzero exit code `errored` and an exit 0 `lost`.
- **A death report the gate proves** stands just before the later claim's `started`, carrying its `t`: a claim
  that no `stopped` names and no attributed death ends, and that a later claim follows, gets `lost`, since
  that claim passed 0.3.0's claim gate (`design-v0.2.md` §4 and §8), and that is when 0.4.0's claim path
  writes it (§4).
- **Every death report's evidence** is E1's: `migrated: stopped[: <its error or reason>]`, `migrated: reap:
  signal s` or `exit n`, `migrated: gate: <the dead claim's handle>`.
- **A value** drops `step`; **`launcher.*` records** move to `supervisor.*`; a stop, a `nak` and a user's
  topic are carried as they are.
- **The renames:** every seq-valued field the 0.4.0 schemas define names the record it named, renumbered once:
  `claim_seq`, `consumed_seq`, `parent`, `values` and `resume_from`. A zero `consumed_seq` stays zero. Request
  ids are strings, so a `stop@N` stays as it is.
- **`created_at`:** each input record keeps its own; a record the step adds takes that of the record it stands
  after, and a gate's death report that of the claim it precedes.

**The divergences** (the owner, 2026-10-25; `log-formats.md` §6; review §12, item 16). Each is a rule under
which a read of the migrated log answers differently from the same read of the 0.3.0 log, corrections
included. A run where one fires is refused until `--accept` names it. Each fires where its rule applies,
which a read may then answer differently, so its sites can be wider than the reads it changes, never
narrower: the corpus runner attributes every read the migration changes to a divergence that fired there, and
an unattributed one is a rule this table lacks.

| divergence | the read it changes, and how |
|---|---|
| `register-value` | the series: a value with a step that the classifier answers a register (a stray's included), which no node holds, leaves every lineage |
| `unheld-value` | the series: a value with a step that no node holds though it is no register (loading, after its claim's end, of an iteration that never finished) leaves every lineage |
| `recounted-value` | the series: a value a node holds is read at that node's count along its lineage, where it differs from the value's own step (a leading value, a stride, a resume from outside or from no node) |
| `off-head-value` | the series: a value a node holds off the newest claim's lineage (a displaced worker's branch, a claim resumed from an older node) is read on no head |
| `several-values` | the series: one node holds several values of one name, each an element, where 0.3.0 read the newest at its step |
| `exit-honored` | the stop fold and the exit's `honored`: E2's cost, its five cases |
| `third-party-release` | the verdict: a third party's `stopped` read as a release is a death report, whose outcome is its act's |
| `third-party-note` | the verdict: a third party's `stopped` that ends nothing leaves the log, where 0.3.0 read it as its claim's end |
| `third-party-reap` | the verdict: a third party's `stopped` read as a process's end is a `terminated`, on which a claimed run's verdict no longer rests |
| `fenced-exit` | the verdict: a worker's own `stopped` after a release or a takeover fenced it is no exit node and leaves the log |
| `attributed-death` | the verdict: a claim that 0.3.0 saw ended only by its launch's `terminated` gets a death report from it (an exit 0 is `lost`, not `completed`) |
| `unattributed-reap` | the verdict: a launch's `terminated` that reports no claim dead (a wrapper's, another host's) leaves the newest claim naming its launch held, where 0.3.0 took it as that claim's verdict |

E1 is no divergence: no fold parses `evidence`.

**The refusals,** each before the seal, naming the run.
- **At the boundary,** naming the seq, the first record the step cannot carry. The step reads every record as
  0.3.0's schemas pin it, so an input it accepts never yields a row 0.4.0 rejects, and it reads a body
  strictly, an integer only as a JSON integer, so it refuses a whole-number float such as 3.0, which 0.3.0's
  schemas accept. It refuses:
  - what 0.4.0 cannot hold: any subscription's record (a `control.subscribe`, a `control.unsubscribe`, a
    `lifecycle.bound`, and a sample, a value bearing a `request_id`); a `control.stop` whose `from` holds a
    step atom, at any depth; an in-claim rewind, a heartbeat at or below an integer step its claim already
    beat, since nothing in 0.3.0 says where one resumed; and a value with a null `name`;
  - a record envelope-v0.2 rejects: an empty topic, name or request id, or a body that is not an object;
  - a topic under a prefix 0.3.0 reserves (`lifecycle.`, `control.`, `launcher.`) that 0.3.0 does not define,
    and a user's topic under `supervisor.`, which 0.4.0 reserves;
  - a `started`, heartbeat, `nak` or value whose body is not 0.3.0's; a `control.stop` with no request id or
    whose body is not subscription-v0.3's `StopTrigger`; a `launcher.*` record with no launch id or whose body
    is not launcher-v0.4's;
  - a `stopped` whose body is neither 0.3.0's nor M10's `{reason, honored, claim_seq}`; and an M10 body under
    the unstated classifier, since it has no conforming writer and only a consumer's classifier may read it
    (the malformed-record rule);
  - an envelope field 0.4.0 pins null that holds a value: a lifecycle record's or a stop's `name`, a
    heartbeat's or a `stopped`'s `request_id`;
  - a `stopped` whose `honored` holds the empty id, which names no stop;
  - a heartbeat or `stopped` whose `claim_seq` names no claim before it, and a heartbeat whose nonzero
    `consumed_seq` names no record before it, or a heartbeat with an integer step, which may become a commit;
  - a second `terminated` of any launch: a process ends once, and a run nothing claimed takes its launch's
    `terminated` as its verdict (§5), which two make ambiguous.
- **No history writes the log:** a stated fact or a classifier answer the log contradicts. The refusal names
  each stated fact and each classifier answer whose relaxation alone, to its whole domain, admits a history;
  where no single relaxation does, the smallest pairs whose joint relaxation does. A relaxation too wide to
  follow at the cap is reported undecided, with the cap. A narrower relaxation that admits a history shows the
  whole one does, since a fact's whole domain contains every statement of it, so the step tries each class
  some version leaves out where the whole domain is too wide to follow.
- **Past a limit:** more states in one layer than the cap, or summed over the layers than the budget, naming
  the record.
- **A chooser** that refuses, raises or answers in another shape, naming the projection and the chooser; and
  `refuse`, every projection's built-in.
- **A name renumbering cannot carry:** a heartbeat's `consumed_seq` naming a `stopped` that becomes nothing.
- **An attributed death report whose launch several `launched` records name:** the claimant's handle is not
  known.

The liveness check that `runstate migrate` asks before it moves a run refuses one whose latest claim no
`stopped` names and whose `started` carries no handle: whether the claim is live cannot be told.

**What moved out of the generic step** (review §12, item 16). Rules that read one consumer's texts or shapes
are no part of the step: each is a statement a consumer's facts or classifier make, and mycooc's, which mycooc
keeps, hold them. The owner's rulings on them, c′ (review §12, item 16) and the M10 mapping (item 14), are
their content:
- **the runner's hard-crash backstop,** known by its error text, "subprocess exited N with no terminal
  record": the classifier answers it as a third party's, a release where it ends its claim, a reap (`exited`
  with code N, or `killed` by signal −N) where it ends none (c′);
- **M10's body** of the reclaim tool: the classifier answers it as a third party's release, `preempted`, of
  the claim it names, and the facts state that third party's acts;
- **a stop written after the time it reports,** its `t` before its own `created_at` by more than 60 s, read as
  a third party's: a classifier's reading, with its constant and its clock assumption;
- **the readings the ambiguity audit found right only for mycooc's writers,** each a fact of mycooc's versions
  or an answer of its classifier.

A malformed `stopped` is refused unless a consumer's classifier reads it, and any other malformed record the
step reads is refused outright (`log-formats.md` §6).

**The only renumbering step.** 0.2.0 → 0.3.0 only appended, so this is the only step that renumbers, and the
last that may: from 0.4.0 on, checkpoints hold seqs. The runner **enforces** it: every later step must keep
each input record's seq and topic. Appending instead of renumbering was rejected: it would add about 1.23M
records and leave shapes no 0.4.0 writer produces.

**The properties to test.**
- **The oracle** (`tests/test_migration_oracle.py`) holds the engine to the simulator of conforming 0.3.0
  writers (`tests/migration_sim/`), the target's second encoding, over every named history and the families
  the mutation test needs: no silent answer, every truth reachable, every output defined, no unique wrong
  answer, and an order-free candidate set whose decode follows its stated rule. E2's sites are pinned against
  the simulator's stated costs (`Truth.costs`): where the engine reads one lineage, the exits where
  `exit-honored` fires are exactly the exits of the truth's costs.
- **The goldens** (`tests/test_migration_goldens.py`) restate each golden log as its facts, its choices and
  its whole output, pinned.
- **The reads** (P4 restated): the series, the verdict's outcomes (0.3.0's one taken as a one-element set) and
  the stop fold's ids are equal across the migration except where a divergence fires, which names the rule.
  The corpus runner (`scripts/migration_round.py`) checks it on real logs: every differing cell, verdict and
  stop fold is attributed to a divergence that fired, else counted unattributed.
- **Also:** 0 nodes name a value at or after themselves, 0 parents follow their child, 0 commits or heartbeats
  precede the record their `consumed_seq` names, 0 resume points follow their claim, 0 records name a claim at
  or after themselves, every seq-valued field names a record of the kind it names, no claim has a second exit,
  and every migrated record conforms to the 0.4.0 schemas.

**Figures** (review §12, item 16, which gives them in full). The step on its engine at `8ccf4a8`, run over
every runstate log the consumers hold, from its legacy address through the 0.2.0 → 0.3.0 step, under each
consumer's stated facts, with `end-time` resolved `lower` and `span` and `origin` left unresolved; the
choosers and accepts were measurement settings only, each the consumer's to make:
- **Translation:** 1,218 of 1,221 runs migrate, and 3 fork on `span`. No divergence fires, and none of its
  1,010,403 series cells differs.
- **mycooc:** in its store copy 4,882 of 4,953 runs migrate, 69 fork on `span` (30 of them also on `origin`),
  and 2 are refused at the boundary, logs older than format 0.2.0; in its mirror 4,982 of 5,088 migrate and
  106 fork; in its archive 40 of 41 migrate and 1 forks. Every read the migration changes is a divergence that
  fired: `register-value` (mycooc's `status` register, which leaves every lineage) in every changed series
  cell but 24, which are `off-head-value`, and `third-party-note` in the 27 runs whose verdict changes.
- **In every run that reached the transform:** no crash, no row the 0.4.0 schemas reject, no violation of
  "Also", and no changed read that a divergence does not explain. The forked and refused runs never reached
  it.
- **The cost** is linear in commits (fitted exponent 1.05 to 1.09). `runstate migrate` was measured over 300
  of mycooc's store logs, each run's home its own root, as mycooc lays them out. The step was registered in
  process, since the shipped command does not register it yet, and the logs sat on tmpfs, not on mycooc's
  NFS. It took 0.90 s a run, 0.73 s of it detection, at a peak RSS under 80 MB: about 1.2 to 1.3 hours for
  each of mycooc's two large corpora, before NFS. The costliest log, 664f8795 (4,017 records, 670 claims),
  took 819 s at a peak RSS of 1.8 GB under provisional choosers, with the liveness check overridden for the
  measurement: `runstate migrate` refuses it as live, since its last claim never ended.


## 8. A consumer's upgrade

These extend [`reference-by-name.md`](reference-by-name.md) §7. File and line references date from review §6
(2026-10-04) and §12 (2026-10-05 on).

**Every consumer:**
- **`tick(step)` becomes `commit()`,** with no argument, and `steps(total, start=)` becomes `steps(until)`:
  the log holds the position. The renames force every call site to be read for the next item.
- **Emit before you commit:** a value emitted before the commit lands in the iteration that computed it. One
  emitted after lands in the next iteration's node, and with the checkpoint recipe node s is then saved
  before step s's metrics exist, so a crash loses them. Which state a value describes, the state before its
  iteration's update or after it, stays the writer's: no record says it, and no runstate read selects by it.
- **Pass `resume_from=`, `sources=` and `save=` to `Worker`,** and `head=` to lineage reads, explicitly.
- **Raw value sends drop `step=`:** value-v0.3's `additionalProperties: false` refuses it.
- **Progress is a count, not a 0-based label.** A target reached at `p >= req` on the old label is reached at
  `prefix_len > req`, or `prefix_len >= n` for a count of n.
- **`value_series` becomes `streams()` plus `series` or `aligned`,** and elements are `Element`s.
- **`control.unsubscribe`** becomes `control.withdraw` for an asker; a reader of expiries reads the nodes'
  `expired`. **`launcher.*`** becomes `supervisor.*`.
- **A stranded claim is released by a death report,** through `report_death`, never by a forged `stopped`.
- **A same-host crash that nobody reports reads as held,** where 0.3.0's probe released it at once. A
  claimant, a launcher's reap or a reclaim tool reports it.
- **Registers stay raw sends,** outside every lineage, read by
  [`completed-opt-in.md`](completed-opt-in.md)'s register recipe: the newest value of the name after the
  claim's `started`. That recipe compares positions across writers, and stays outside the convention.
- **A code version on the log: one recipe; you can build your own.** Emit it before the claim's first
  `commit()`, as `w.emit("code_version", v)`, with `v` whatever the consumer means by a version: a git hash,
  a package version or an image digest. The claim's first node commits it, its exit included unless the
  exit is torn, and that node names the claim. So the version is attributed by name, where a 0.3.0
  positional register was not. A claim torn before its first commit leaves it uncommitted, as it leaves
  every value. runstate reads no such value.
  - **Why `started` names no writer** (the 0.4.0 audit, 2026-10-25). 0.4.0 records every fact its own folds
    use. Each writer habit that another reader needs can be recorded by a later format, under a null a
    native writer can mean, or needs no record. So a tag would give no reader anything to look up. It fails
    two of the basis rubric's tests: independence, since this recipe composes it; and canonical form, since
    a version could be any of the coordinates above, and an opaque slot only moves that choice to the
    consumer, where a value already leaves it. Nothing would read it, which `design-v0.2.md` §12, item 8's
    lift rule counts against it. It would also have settled little of what the 0.3.0 → 0.4.0 step leaves
    open (§7): a third party's stop is no claim's, so a tag on `started` does not name its writer; who wrote a
    value no tag on `started` attaches to; when an undated pass ended is an event no habit settles; and the
    rest are one consumer's loop and checkpoint habits across its own versions, which its classifier already
    maps to writer versions.
  - **Revival triggers,** both logical: a format step whose input leaves open a fact that no recorded fact
    settles and that has no null a native writer could mean; and authenticated records, where authorship
    becomes load-bearing (`design-v0.2.md` §12, item 8). Then authorship belongs on every record's writer,
    third parties' included, not on `started` alone.

**mycooc:**
- **Name the resume point on `Worker`** (main.py:277; training.py:1369; main.py:933 `start_step=`), choosing
  the checkpoint before claiming: read its node, claim, load, assert.
- **Emit before the commit:** move `_emitter.step` ahead of it (main.py:922–923, against training.py:1369).
  94.3% of its values are emitted after their step's heartbeat today. Add a conformance test.
- **Commit on every path:** the early returns before the tick at training.py:1017 (the NaN sentinel), :1256,
  :1287, :1313, :1319, :1334 and :1350 move it into a `finally`. At 891 such steps its metrics landed with no
  heartbeat, the control drain was skipped, and liveness had a gap.
- **Every exit commits:** its 735 clean exits that did not complete now commit their pending values, and are
  extendable, so a resume that names them keeps those values. A SIGTERM handler that cannot let its
  iteration finish sets a flag, and the loop calls `w.discard()` at its next safe point.
- **Save through the Worker's policy,** `save=Every(k, fn)`: 3 of its 4 checkpoint saves precede the tick that
  would name their node (training.py:1332, :1346 and :1354, against :1369).
- **Counts:** `p >= req` at run_experiment.py:1102 and :1109; `best_step` at channel_read.py:168, against
  training.py:455 and the CSV pinned by tests/migration/test_phase6a.
- **Delete the emit-only-missing guards,** which the lineage supersedes: analyze_run.py:1391–1393 and :1534.
- **`value_series`** at channel_read.py:151 (about 10 sites) and summarize_outputs.py:88; the element type at
  analyze_run.py:1532–1534.
- **Registers stay raw sends,** without `step=`: `status`, `completion_reason`, `config`, `input_provenance`
  and `analyzed`. That includes the "done" `status` written after `w.stopped` (main.py:1063), which `emit`
  would drop at the post-terminal latch (worker.py:238), the orchestrator's `completion_reason`
  (run_experiment.py:3866, :3877), and the separate-handle `input_provenance` (graph_adapter.py:213–217).
- **Declare `sources=[]`:** it calls `w.set` nowhere.
- **Its verdict tests become tests on the set:** `term.outcome == Outcome.ERRORED` at graph_adapter.py:175 and
  run_experiment.py:1204 and :4100 becomes `Outcome.ERRORED in term.outcomes`; `in Outcome.failures()` at
  run_experiment.py:1049 and :1068 becomes a non-empty intersection; graph_adapter.py:184's `COMPLETED` test
  and run_experiment.py:2902's `completed_reason` read a one-element set.
- **The third-party writers:**
  - the runner's hard-crash backstop (run_experiment.py:689) and the reclaim tool
    (scripts/reclaim_experiment.py) write `supervisor.died` through `report_death`. The tool's targets are on
    other hosts, where the probe returns `None`, so the helper's veto never refuses them;
  - the tool's dating by SLURM's end time (scripts/reclaim_experiment.py:220, `_stopped_payload`) stops
    mattering, since `last_activity` dates no `died`: a reclaimed run's age becomes its last record's, and
    tests/test_reclaim_experiment.py:61 and :65 change. The end time can go in `evidence`;
  - the "clear staged stop" path (run_experiment.py:3926) writes one `control.withdraw` for each id in
    `undischarged_stops`;
  - `scripts/repair_malformed_stopped.py` is deleted: it existed only because a malformed terminal raised
    forever.
- **Its GC gate** (gc_runs.py:179, `live_episode is None`) waits after a same-host crash until someone
  reports it: the safe direction.
- **Convert its `checkpoint_last` files** from steps to nodes, once, after the log migration.

**translation:**
- **Counts:** keys.py:77's `progress+1 >= n` becomes `prefix_len >= n`; its mirror has drifted (keys.py:75,
  `floor_ok(None, 0)` is False).
- **Delete the emit-only-missing guard** at workers.py:28, which raises `KeyError` on `body["step"]`.
- **The element type** at runner.py:33 and orchestrator.py:36–37 and :69.
- **No checkpoint object:** its state is the log plus its ArtifactStore. It passes `resume_from=None` and
  `save=NoSave()`, and re-emits; its tests at tests/test_workers.py:40–75 pin resume.
- **Declare `sources=[]`.**

**runstate-tui:**
- use `Commits()` for its frontier, and `last_element` for its live value;
- fix the `step>N` filter (table.py:161–169) and "@ step" (fold.py:114, detail.py:40, format.py:60);
- make `latest` lineage-aware (fold.py:111);
- handle `--objective None` (env.py:41), which `Commits()` now serves;
- follow the `supervisor.*` rename.

**The in-repo examples** (`examples/`):
- **minimal and submitit:** `every {step: 1}` becomes `{commits: 1}`. minimal's stop `from {step: 0}` meant
  "at once", and drops its `from`.
- **vqe:** `every {step: 10}` on its `job_seconds` source becomes `{commits: 10}`. Its wait loop re-ticks at
  one step while a QPU job is queued; it calls `heartbeat()` instead, which keeps liveness and lands a stop
  mid-wait without writing a node. vqe emits only after the wait, so such a stop leaves nothing torn; a
  variant that emits before it calls `w.discard()` on the way out.
- **minimal, submitit and vqe's observers** select values by `e.request_id == "driver"` and print
  `e.body['step']`. Under value-v0.3 neither exists, and the filters would match nothing in silence; they poll
  an `Answers` fold instead.
- **reuse and redrive:** `loss` becomes eager, via `emit`, because `ensure` counts it.
  `ensure(…, until={"step": N})` becomes `ensure(producer, Target("loss", N))`. Their `every {step: 1}`
  subscriptions on `loss` existed only to get it logged, and are deleted.
- **monitor:** reads its subscription's expiry from the nodes' `expired`, through the answer fold, where it
  read `control.unsubscribe`.
- **All:** `Worker(…, resume_from=…, sources=[…], save=…)`, declaring `loss` (minimal, submitit),
  `job_seconds` (vqe), and `load1` and `mem_available` (monitor) as sources.

## 9. Interop

These are protocol, and another implementation must match them:
- the node rule: `parent` and `resume_from` name an extendable node, and `parent` is the claim's position; a
  node follows what it names; order within a node; a seq at most once along a lineage;
- closed, torn and extendable, read from the exit's `completed` and `values`; an error exit is torn, and an
  exit that discarded its iteration (`values: null`) evaluates nothing, whether or not it completed;
- membership by the committing claim's declared sources, decided by the envelope;
- the head rule, and settledness as a property of a node;
- the substrate contract, and well-formedness as completeness;
- `fired` and `expired` on nodes, and one sample per fired source per node;
- leases per claim, with their lineage atoms' baselines read on the lineage, durable subscriptions per
  lineage, and demand by name, empty on a closed head;
- a stop's `from` evaluated at each node and each heartbeat, its decision latched, and `honored` naming every
  stop that held at a node or a heartbeat of the claim, even one withdrawn since;
- the atoms: counts at the node being written for `from` and `until`, the delta rule for `every`, and
  `{commits: n}`; an `until` that holds at registration expiring at the next node;
- the three `unsatisfiable` refusals, and the `malformed` refusals of a re-send that changes its target or
  class and of a pure demand with a `from`, an `every` or a `{count: n}` atom;
- a pure demand's lifetime: it never fires, and lives until its `until` holds, a withdrawal or a refusal
  names it, or, as a lease, its claim ends;
- `ended` as one predicate, the verdict by viewpoint, and its outcomes under death reports: the set of the
  reports' outcomes, an unreadable record that ends the claim counting as `lost`, less a `lost` beside
  another;
- the answer fold, by name: `honored`, `expired`, `withdraw` and `nak`;
- a worker that reads a death report naming its claim writes nothing more, and its drain writes each record
  it makes, a `bound` or a `nak`, only by CAS at the tail it last read;
- a death report from evidence, never inference, and a `lost` one only while its claim is held.

The conformance suite gains a test for each.

## 10. Tests

On every backend:
- **The schemas:** extend `tests/test_schema.py`'s scenario to emit `commit`, `rewind`, `withdraw` and `died`,
  and the changed `started`, `heartbeat`, `stopped` (completed, preempted, torn, errored) and `value`, with
  negative cases: `error` with non-null `values`, a torn exit that fires, a `died` with an outcome of
  `completed`, a `value` with a `step`, `{commits: -1}`.
- **Nodes and commits:**
  - each value is committed at most once along a lineage; an uncommitted value is excluded;
  - a node naming a later seq, a parent or `resume_from` that is not an extendable node, a parent that is
    not the claim's position, and a seq committed twice are malformed, each at its stated check;
  - a `values` seq naming a non-value raises only when resolved; a malformed value body raises on read, never
    on count;
  - order within a node follows `values`;
  - `include`'s checks, including a value committed on the current lineage by another claim, and one
    discarded at a rewind;
  - a `heartbeat()` commits nothing, fires nothing and splits no iteration.
- **Position:**
  - resume on a Worker that calls `commit()` itself;
  - the head of a commitless claim is its `resume_from` node, and after a trailing rewind it is the rewind's
    target; an exit's `parent` after a rewind is the target;
  - a rewind writes a `lifecycle.rewind`, discards pending values, resets registers, keeps the stop decision
    and keeps leases; `rewind(None)` reaches the root;
  - resume and rewind form the right parent chains, and `Commits()` counts iterations across both, and
    across an extended exit;
  - the Worker refuses a `resume_from` or a rewind naming a closed node, a torn exit, a value or a
    heartbeat;
  - `LatestClaimHead()` as `resume_from` continues the previous claim's head; after an error exit it
    continues the exit's parent; on the empty lineage it starts fresh; a closed head raises. `AtNode(x)`
    resumes and rewinds at x.
- **The exit:**
  - every exit kind writes its node; one that did not discard its iteration commits post-loop values and
    fires on them;
  - an error exit and an exit after `discard()` are torn, and evaluate nothing; a completed exit after
    `discard()` is closed, writes `values: null` and evaluates nothing;
  - after `discard()`, `commit()` and `heartbeat()` return True and write nothing, and `emit`, `set`,
    `include` and `rewind` raise;
  - a preempted exit is extendable: the next claim resumes from it and its values stay on the lineage;
  - the exit registers no subscription: a lease that lands before a torn exit is served by the next claim;
  - a failure while writing the exit's samples gives a torn error exit;
  - a lost CAS re-drains without evaluating again;
  - a withdrawal after `commit()` or `heartbeat()` returned True is too late, and `honored` names the stop;
  - `retire()`'s exit fires nothing.
- **Stops:** `heartbeat()` drains a stop and returns True mid-iteration, and so does a time `from` that comes
  due inside an iteration; `honored` names a stop that held only at a heartbeat; the decision is a monotone
  level across a rewind; `stop_pending` reads it.
- **Death reports:**
  - a `died` naming the claim ends it: the claim gate, `live_episode`, lease voiding and the verdict; an
    unreadable one ends it too;
  - `terminated` ends nothing for a claimed run, and stays the verdict of an unclaimed one;
  - `report_death` raises on a live probe unless forced, returns `None` for a claim with a readable exit and
    for a `lost` report on an ended claim, records a second report, and retries a lost CAS;
  - `LocalLauncher`'s reap writes `died` when the handles match, whoever waited, and only `terminated` when
    they differ; `ThreadLauncher` writes `died` when its thread finishes with no exit;
  - a signal reports `killed`, and a nonzero exit code or a raise `errored`, whether or not the claim has
    ended; an exit 0 or a return reports `lost` while the claim is held, and nothing after a release;
  - `report_if_dead` reports a held claim's same-host dead handle as `lost`, and on a backend with a session
    probe a held claim whose session has ended, though its pid lives, and nothing once the claim has ended;
    the claim path, `relaunch_if_needed` and `ensure_served` call it before acting, and `ensure` on each poll,
    its own drive's or a foreign one; `Watcher.poll` and `is_alive()` write nothing, but for a Watcher's reap
    of a handle it holds, which writes through the launcher's reap;
  - a released worker latches before it handles the batch that holds the report: it honours no stop, binds
    no lease, writes nothing more, and a later claim honours the stop.
- **Settledness:**
  - a completed exit closes its lineage; a preempted or errored exit does not;
  - the head is closed if and only if it is a completed `stopped`;
  - a displaced worker's completed exit does not settle the newest claim's read;
  - `AtNode` is permanent;
  - `prefix` returns `Complete`, `Pending` and `SettledShort` correctly, with `elements` on the first and
    `partial` on the others, and `Complete(())` at n = 0.
- **`ensure`:** read first; `Unreachable` without producing; the produce loop following the newest claim; the
  outcomes tested before the failure exits; each failure exit; a drive whose handle ended while its claim
  runs, as a wrapper's does, which waits and reports nothing; a claim-race loser that raises nothing while the
  winner runs; no progress measured by `Commits()` per drive; a later, larger target continuing from the
  earlier drive's extendable exit.
- **The verdict:** self-report over death reports; an unreadable record counts as absent yet ends its claim,
  and is named in `RunResult.malformed`; `{lost}` when only unreadable records ended the claim;
  several death reports kept as a set whatever their order, with a malformed one skipped and `lost` dropped
  beside another outcome; `RunResult`'s `outcomes`, `reports`, `node`, `claim_seq` and `tier` under each
  viewpoint.
- **`await_consumed`:** accepted by a watermark or an answer; the verdict for a request the ending exit saw
  and did not take; a request posted after the latest claim ended waits for the next claim, and so does one
  above a dead claim's last watermark, though a death report follows it; the verdict on a closed head.
- **Sources and subscriptions:**
  - declared kinds, with `set`, `emit` and `include` misuse raising;
  - membership by the committing claim's declaration, including a name that changes kind between claims;
  - two subscriptions firing on one source at one node write one sample;
  - a source with no value yet does not fire;
  - pure demand never fires and no `fired` names it; it keeps `serve()` alive until its `until` holds, or
    with no `until` until withdrawn; a `from`, an `every` or a `{count: n}` atom on it is refused as
    `malformed`, and its id is spent;
  - the three `unsatisfiable` refusals; an `until` that already holds expires at the next node; a re-send
    with another target or class is refused, and its id is spent; an accepted re-send restarts a lease's
    clock and fire count, and keeps the lineage baselines of either class;
  - `answers` via `fired`, including time atoms, a rewind and a resume; its fold re-derives on a head flip;
  - a durable subscription answered on an abandoned branch is answered again on the new one, exactly once per
    lineage, and not on a lineage that expired it, though `live_demand` counts it spent;
  - a lease keeps its clock, fire count and expiry across a rewind, reads its lineage baselines on the new
    lineage, and is void once its claim has ended; a count lease whose service dies at its first firing stops
    the waker after one launch;
  - a lease binding is written by CAS, and a death report landing first leaves the lease unbound;
  - a refusal is written by CAS, and a death report landing first leaves the request unrefused, so a
    successor whose sources serve it registers it;
  - `live_demand` is by name, and empty on a closed head; a claim that dies before its first commit revives
    nothing.
- **The atoms:** `every`'s delta rule under batching, with lineage baselines rebuilt after a rewind for both
  classes and a lease's time baseline kept; counts taken at the node being written; `{commits: n}`; `until`
  and `from` as counts; a 0 in each atom, and `every` with one firing at every evaluation, the exit included.
- **Withdrawal:** a withdrawn subscription, lease or stop is spent.
- **Reads:** the coordinate's convention; aligned cells as tuples, late or gappy metrics aligned by node;
  `streams`' two sets; `last_element` in lineage order; `history` once per node.
- **Bounded reads:** incremental equals pure after every step, for every fold; polled reads stay within a
  bound as the lineage grows.
- **Liveness:** a `started`, a commit and a rewind witness liveness; `last_activity`'s dated topics.
- **The substrate contract:** a concurrent "read W, then read the tail" test per backend, by tier.
- **Order independence:** extend `tests/test_order_independence.py` to rename `consumed_seq`, `parent`,
  `values` and `resume_from` as well as `claim_seq`, and to cover `answers`, order within a node, `expired`
  and the two-viewpoint verdict. Every read stays invariant under causal reordering.
- **The checkpoint recipe:** the save policy names the right node, at commits and at extendable exits only;
  a caller's `SavePolicy` is called at each and nowhere else; `Every(k)` counts `Commits()`; `steps(until)`
  continues from the log after a resume; `resume_from` takes the checkpoint itself, and `choose` returns the
  caller's type; the load assertion fires on a mismatch; both choice policies select only complete
  checkpoints.
- **Migration:** the golden logs and the properties of §7, including the decode order and every refusal at the
  boundary;
  the runner refuses a later step that renumbers.

## 11. Ripple

**Documents:**
- **`design-v0.2.md`:** the substrate-contract clause in §4; §6, the commit, the heartbeat, the rewind and the
  exit; §7, commits; the two viewpoints, renamed `supervisor`, and `ended`; §10, versions; and a
  revision-history entry.
- **`log-formats.md` §6:** this is the only renumbering step, and the runner enforces it.
- **The backend checklist** (CLAUDE.md, "Add a new Channel backend"): the substrate-contract clause and its
  conformance test.
- **`observables.md`:** the verdict by viewpoint, `ended`, `live_episode` without a probe, `last_activity`'s
  dated topics, and the reads.
- **`memoizer.md`, `ensure-until-condition.md`:** `ensure`'s new signature, the producer seam and the
  failure exits.
- **`store.md`:** Recipe 2's foreign episode is a pure read of `ended`, and `ensure`'s poll calls
  `report_if_dead`; Recipe 3's GC gate rests on `ended`.
- **`lazy-launch.md`, `derived-runs.md` and `run-episodes.md`:** `live_episode` no longer probes; an
  unreported same-host crash reads as held until someone reports it.
- **`preempted-vs-completed.md`:** a non-completed exit is extendable, and `discard()` marks a torn one.
- **`service-worker.md`:** `serve()` per lineage, the retire exit as an extendable head, and expiry on the
  node. Its invariant "registered ⟺ a future fire is possible" becomes "live while not spent", and a pure
  demand's lifetime is its `until`.
- **`time-lease-boundary.md`:** the step atom becomes `{commits: n}`; the count lease stays, with its cost;
  lease voiding reads `ended`, so a reaped crash voids its leases through the launcher's `died`.
- **`stop-discharge.md` and `reference-by-name.md`:** the answer fold gains `control.withdraw` and `expired`,
  and `control.unsubscribe` is retired.
- **`channel-postgres.md`** (review §12, item 15): the session probe. A fresh random key per connection,
  taken by try-lock before its first write and carried losslessly; a column stamping each row with its
  writer's key, qualified by the server's identity so a probe elsewhere abstains; the probe reading only the
  server that wrote the stamp, never a standby; the clause that every record naming a claim travels on the
  claim's session, never re-established; `synchronous_commit = on` set on the channel's own connection, and
  a server with `fsync = off` refused, since a crash under either loses rows readers saw and reuses their
  seqs; no transaction-mode pooler. The lock taken after the claim CAS, and its birth grace, go. Its design
  principle is restated: the probe yields evidence for a record, which `report_if_dead` writes, and the CAS
  stays the only arbiter of a claim.
- **`launcher-record-identity.md` and `observer-clock.md`:** the `supervisor.*` rename, `supervisor.died`, and
  `terminated` leaving a claimed run's verdict; observer-clock's dated topics lose `terminated` and gain
  `commit` and `rewind`, and date no `died`.
- **`completed-opt-in.md`:** the register recipe without `step`.
- **`value-plane-divergence-resolution.md`:** the take-the-latest collapse is superseded by lineage.
- **`run-id-recipe.md`:** state the assumption that observers' demands count as inputs where they perturb
  (from [`../backlog/demand-streams.md`](../backlog/demand-streams.md)).
- **[`../backlog/claim-eviction.md`](../backlog/claim-eviction.md):** the probe veto lives in the writer's
  helper, and can be forced.
- **if-built-today's 5-domain:** its step is depth, which no longer counts iterations once a lineage passes
  through an exit; a follow-up there.
- **`api.md`, the implementers guide, `CLAUDE.md`:** the API and the architecture notes.

**Code paths** the spec changes (precision review §5):
- **`vocabulary/payloads.py`:** `Topic` gains `LIFECYCLE_COMMIT`, `LIFECYCLE_REWIND`, `CONTROL_WITHDRAW` and
  `SUPERVISOR_*`, and loses `CONTROL_UNSUBSCRIBE` and `LAUNCHER_*`; new `Commit`, `Rewind`, `Withdraw` and
  `Died`; the `Node` Protocol.
- **`vocabulary/schedule.py`:** `satisfied`, `Subscription.tick`, `is_unsatisfiable`, the step corner and the
  stepless clauses move to counts, and `is_unsatisfiable` drops its refusal of an `until` that already
  holds; the "exactly one key" rule admits `{stream, n}`; `_EPISODE_LOCAL_ATOMS` becomes the lease test.
- **`worker.py`:** the claim path, whose fold no longer shares `_SUBSCRIBE_ANSWERS` with `live_demand`;
  `steps` and `serve`; `emit` loses its "before the first tick" raise and its register update;
  `_handle_control` gains `CONTROL_WITHDRAW` for both kinds, the re-send rule and the pure-demand rule; the
  CAS'd binding; `_service` writes one sample per source with a null `request_id`, none for pure demand;
  `_stop_decision` evaluates at heartbeats too, and it and `stop_pending` latch; `_die` follows the exit's
  order; `retire`; `discard`; the fence; the `SavePolicy` Protocol, `Every` and `NoSave`; `resume_from` and
  `rewind` typed by the `NodeRef` Protocol, which `AtNode` and a checkpoint satisfy.
- **`observables.py`:** `_terminal_stopped` (the claimless branch goes); `_episode_ended` and `live_episode`
  become `ended`; `_launcher_terminal` serves only an unclaimed run; `_verdict_record` goes;
  `worker_completed`; `_DATED_TOPICS`; `current_heartbeat`; `is_step` renamed; `progress` and `value_series`
  out; the reads and folds in; `Outcome` gains `LOST`; `Tier` is new, and `RunResult` takes `outcomes`,
  `reports`, `node`, `claim_seq`, `malformed` and `tier`.
- **`sweep.py`:** `stop_on_failure` tests the set.
- **`watcher.py`:** `_RunState`'s held head and count; `broadcast`; `_refuse_a_void_lease`; `await_consumed`;
  the inference tiers' `Tier` members.
- **`memoizer.py`:** the error classes, and `_FAILURES` as a test on the set; `ensure`'s `Reached` and
  `Unreachable` over a `Target`; `Producer`, `_LaunchProducer` and `launch_producer`; `history`; the
  step-and-time helpers, which go; the guards; `_ForeignEpisode.is_alive`, a pure read; `ensure`'s poll,
  which calls `report_if_dead`.
- **`launcher.py`:** `report_death`, `report_if_dead` and `ClaimAliveError`; `_LocalHandle._reap` and
  `ThreadLauncher` write `died`; `relaunch_if_needed` and `ensure_served` call `report_if_dead`;
  `ensure_served`'s held `LiveDemand`.
- **`formats/`:** `LOG_FORMAT`, `FORMATS`, and `v0_4_0.py` (`runstate_v0_4_0` on Postgres).
- **`migrations/`:** the new step in `STEPS`, and the renumbering check; `ambiguity.py`, the shared
  mechanism (`log-formats.md` §6, "Ambiguity"); and each step's built-in choosers, in its own module.
- **`__init__.py`:** the exports.

## 12. Known limits and open items

- **Forgery and authority are unchanged:** still an honor system
  ([`../backlog/authenticated-records.md`](../backlog/authenticated-records.md)). Hash-reveal withdrawal,
  which would let only the asker withdraw its request, waits in the backlog with no reserved field. Its
  triggers are a second independent asker on shared runs, or authenticated-records being taken up.
- **Observation is relative to the newest claim.** The choice of head is a report. Minted ids, verdicts
  relative to a head, a stable head without an arbiter, and stop scope without one belong to layer 6
  ([`../backlog/lineage-graph.md`](../backlog/lineage-graph.md)).
- **A stop names no claim,** as in 0.3.0. A stop its claim never honoured, because the claim died, is
  honoured by the next claim, and a released worker's fence keeps it from honouring one meant for its
  successor. if-built-today's stops name their claim and lapse with it, and a halt that outlives a claim is a
  forbid ([`../backlog/run-scoped-halt.md`](../backlog/run-scoped-halt.md)).
- **Death reports are trusted.** The probe veto lives in the writer's helper and can be forced. pid reuse
  fools the probe while the reusing process lives, and the `?start=T` handle disambiguator
  (conventions-hygiene F9) closes it. A mistaken release costs the iteration in flight, and its verdict
  stands, since the fenced worker writes no exit to correct it.
- **A same-host crash that nobody reports reads as held,** where 0.3.0's claim gate probed it. The parties
  that hold the evidence report it: a claimant on that host, a launcher's reap, a reclaim tool, and on
  Postgres any caller of `report_if_dead`, from the claim's session (review §12, item 15).
- **A session probe carries no cause.** A walltime kill seen only by it reads `lost`, which is a failure,
  where accounting would say `preempted`. A deployment that wants the cause keeps an accounting reporter
  beside it; the verdict drops `lost` once a cause arrives (§5).
- **A custom producer's launcher** that does not report its claimant's death leaves the claim held, and
  `ensure` waiting on it, since an ended handle may be a wrapper's.
- **A death report cannot contest another.** The verdict shows every report's outcome, so a disagreement is
  visible, but no record says which report is wrong. Dispute records are in the backlog.
- **A count subscription is cut at a claim boundary:** it is a lease, so a resumed claim does not continue its
  count. Rebuilding it from the lineage was rejected, because a service that dies at its first firing then
  loops the waker (review §12, item 13). time-lease-boundary's "Who pays" states the cost.
- **A subscription posted to a completed run** is never answered and never refused (§5).
- **Bytes.** Values lose `step`; commits gain `values`, `fired` and `expired`, about one integer per value.
  Users may batch. The encoding experiment (identity-in-records §2) should measure the net effect on the real
  corpus.
- **The migration copies a historical misattribution faithfully** under `record-claim=positional`
  (`reference-by-name.md` §6), where a consumer's facts and classifier let a displaced worker's value be its
  successor's (§7). Other statements leave a contested value unheld, or fork it. Its figures wait on the
  corpus re-run (§7).
- **Sources are declared once per claim.** A worker-written declaration in force within its claim, and
  derived streams named by the demand that creates them, are backlogged
  ([`../backlog/programmable-subscriptions.md`](../backlog/programmable-subscriptions.md)).
- **Liveness promises** (`next_within` on every liveness record) are backlogged
  ([`../backlog/liveness-promise.md`](../backlog/liveness-promise.md)). They add cleanly later, since `null`
  truthfully migrates older records.
- **The control plane is the next layer.** Demand as `asked` facts
  ([if-built-today](../backlog/if-built-today/3-questions.md) §"Demand is control";
  [`../backlog/demand-streams.md`](../backlog/demand-streams.md)) would replace subscriptions. This spec's
  data model is what that layer needs: sources asked for and eager streams measured, with identities that
  do not depend on the asker, and settledness.
