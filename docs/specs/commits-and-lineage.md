# Spec: commits and lineage — values committed to a lineage of nodes (log format 0.4.0)

**Status:** DRAFT, revision 2, 2026-10-14; its drafting choices settled 2026-10-23 (review §12, item 14).
For the owner's review. Not implemented.

This is layer 2 of [`../backlog/identity-in-records.md`](../backlog/identity-in-records.md). It introduces
**log format 0.4.0** under [`log-formats.md`](log-formats.md), and builds on
[`reference-by-name.md`](reference-by-name.md) (format 0.3.0).
- **Revision 1** (2026-10-04) followed decisions 1–15 of identity-in-records §2. Seven independent reviews
  and an alternatives round found nine design decisions it got wrong or left open:
  [`../review-2026-10-04-commits-and-lineage.md`](../review-2026-10-04-commits-and-lineage.md).
- **This revision** applies that review as the owner settled it, one decision at a time (its §12, items
  0–14). Where the two differ, this spec is current. The review keeps the evidence and the alternatives
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

**The target** (the owner, 2026-10-25). The step writes the conforming 0.4.0 lineage that records the 0.3.0
history's own events:
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

**The encoding rules** (the owner, 2026-10-25). Each fixes how 0.4.0 writes down a 0.3.0 act that it has no
record for, and each is the least-distorting way to do so.
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
  - The native drains read whatever lies before them: at each commit, at each logged tick, and at the exit.
  - Each exit's `honored` follows from those reads by 0.4.0's own rules (§4): every stop pending at the
    exit, and every stop held at a node or heartbeat of the claim.

  *Why it distorts least:*
  - It alters no recorded fact, no place and no watermark, so every 0.3.0 watermark stays true.
  - The target's third item is which stops each worker read, and the watermarks record those reads. An
    exit's `honored` is 0.4.0's consequence of them, not a separate fact to preserve.
  - It needs no fork and no choice per consumer, so every consumer's migrated log reads alike.

  *Its cost,* which the owner accepted (2026-10-25). In two cases a migrated exit honours a stop that the
  0.3.0 exit did not list, so the stop counts as discharged there rather than pending:
  - a writer whose exits list only the stops it read at its ticks. Its native exit drains every stop
    before it;
  - a tick-first loop where a stop is cleared between an iteration's end and the next tick. §4's commit
    drain reads the stop at the iteration's end, before the clearing.

  The stop fold then lists the stop as spent. It changes behaviour only if the migrated run is resumed: the
  next claim no longer honours the stop.

  In a third case a migrated exit omits a stop the 0.3.0 exit listed. An `at-claim` writer, whose fold
  applies only the answers made before its claim, took in a stop that another party answered after its
  claim. The withdrawal at the answer then precedes the worker's native drain (golden `LATER_COMPLETED`).
  The stop is spent either way, so only that exit's `honored` differs.

For each ambiguity below, the built-in nearest the rule the step encoded before, in
`v0_3_0_to_v0_4_0_resolutions.py`, is marked "today's reading", with where that rule differs from it.
*The ambiguities are specified 2026-10-24, and not yet encoded. They were written to the old standard,
that every inference copies what the old positional reading implied, and some of their rules contradict the
target and the encoding rules: their death reports' evidence texts (E1); a withdrawal for every honoured id,
an id already withdrawn included; an exit node that keeps its 0.3.0 `honored` list, where E2 derives it from
the native reads; and P4's "No rule changes the stop fold's ids". They stay as written until the step is
rebuilt on the target.*

**What 0.3.0 pins** (review §12, item 16). The step judges against any writer conforming to format 0.3.0:
lifecycle-v0.5, value-v0.2, subscription-v0.3, launcher-v0.4 and `design-v0.2.md`: what its schemas and its
design write. Another spec may elaborate a pin, never make one. The step relies on these, each written where
it is cited:
- **Names are taken at their word.** A heartbeat's or a `stopped`'s `claim_seq` names the claim it speaks
  for, and a `stopped`'s `honored` names the stops it answered (lifecycle-v0.5; `reference-by-name.md` §2).
  A conforming worker names only its own claim. In every real 0.3.0 log these names are the 0.2.0 → 0.3.0
  step's positional inferences, and those are that step's ambiguities (`reference-by-name.md` §6), not
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

**What 0.3.0 leaves open,** and the step therefore declares:
- **who wrote a `stopped`** (`stop-author`), and **what a third party's says** (`third-party-stop`): 0.3.0
  had no record for a third party to report a death, so real writers used `lifecycle.stopped`
  (`design-v0.2.md` §7: "a third party's release beside the worker's own"), and 0.3.0's verdict took the
  newest whatever its body;
- **which claim a value belongs to** (`value-claim`), since a value names none, and since nothing in
  0.3.0's schemas or design forbids a worker a value after its halt: the reference Worker writes none
  (worker.py:230-237), and mycooc writes a teardown `status`;
- **what a heartbeat's step marks:** lifecycle-v0.5 calls the heartbeat a "tick-driven liveness beacon",
  and `design-v0.2.md` §7 says it serves "progress (step advancing)". Neither says the iteration finished,
  and §1 above says "No record marks a step complete. The heartbeat stood in for that by accident". The
  reference Worker's `tick(step)` fixes no order with the body, and `emit` raises only while no step is set,
  before `steps()`' first yield or a manual loop's first tick (worker.py:179, :240). Whether a claim's last
  beat ends or starts its iteration is `beat-ends-iteration`; where a value written after that beat at its
  step belongs is `post-beat-values`;
- **whether the step clock moves one iteration at a time** (`valueless-gap`): `design-v0.2.md` §11 makes it
  the worker's logical clock, and only the reference `steps()` driver moves it by one;
- **whether a claim ran the iteration its clock names,** or labelled a loading status with it
  (`leading-values`, `beatless-claim`);
- **what a `final_step` says** (`final-step`): 0.3.0's reads took it as the step the trajectory reached
  (`memoizer.md` Decision 2; `api.md`, "the last step reached"), while the reference Worker fills it with
  the last step yielded (worker.py:348), which a body that breaks out early never finished (§1); and, for a
  claim no exit of its worker ends, **whether its last step finished** (`exitless-step`);
- **where a resumed claim's state came from, at which step, and from which claim's save** (`resume-origin`,
  `resume-offset`, `resume-claim`, `commitless-resume`): `design-v0.2.md` §9 names "an episode rewind" as one
  way a resume begins, and no schema or design pins whether a resumed claim's state was saved by the log's
  own claims, how far below its first step it was saved, or by which claim.

**Records:**
- **A 0.3.0 heartbeat with an integer step** becomes a `lifecycle.commit` wherever it falls, even after its
  claim's end, where M4 leaves a value uncommitted; under `beat-ends-iteration`'s `starts`, a claim's last
  one becomes a `lifecycle.heartbeat`.
- **A stepless 0.3.0 heartbeat** becomes a `lifecycle.heartbeat`, without `step`. mycooc's 1,114 all sit in
  its 150 stepless-only claims, after only `status` values: they were beats.
- **A `stopped` read as its worker's exit** becomes the exit node:
  - `parent` is the claim's position: its newest commit, else its resume point;
  - `values` are null on an error exit. On any other exit they are the values of its open iteration that no
    commit claims, under `final-step`, and the post-beat values `post-beat-values`' `exit-holds` gives it,
    else `[]`. M2 and M5 commit every integer-step value the claim wrote before its exit that the readings
    vouch for, and stepless values stay outside every lineage (M3);
  - `fired` and `expired` are `[]`, and `final_step` is dropped.
- **`started`** gains `resume_from` (M1) and `sources: []`.
- **A value** drops `step`.
- **`launcher.*` records** move to `supervisor.*`, unchanged otherwise.
- **`created_at`:** each 0.3.0 record keeps its own; a synthetic record takes that of the record it is placed
  after. A synthesized record with no `t` to carry takes its `created_at` as its `t`.

**The passes, in order:**
1. **Claim attribution.** A heartbeat belongs to the claim its `claim_seq` names, as every 0.3.0 read of it
   did. A value, which names none, belongs to the latest `started` before it: the positional rule the
   0.2.0 → 0.3.0 step stated for episodes, an inference that attributes a displaced worker's values to its
   successor, as the old read did (`value-claim` asks whether to commit them). A value before every claim
   belongs to none.
2. **Classification** of every `stopped`: a worker's exit or a third party's, and what a third party's
   becomes (`malformed-stop`, `stop-author`, `third-party-stop`, and "Stops" below). Then which claim a
   launch's `terminated` reports dead.
3. **Synthesis:** M2, M4 and M5 assign values to commits or leave them uncommitted, synthesizing the commits
   M5 names, with M8's fields (`value-claim` to `exitless-step`, and the resume family for M5's third place
   and the run-ons).
4. **M1:** parents and `resume_from` (the resume family and `commitless-resume`).
5. **M6:** the moves, the withdrawals and the death reports, then one renumbering.
6. **M7:** the renames.

**The ambiguities.** The step declares fifteen (review §12, item 16), in the order its passes ask them.
**Each site test reads only the step's input log,** never a reading of any ambiguity, its own included, so
the detection pass needs no strategy to find this step's sites. A strategy receives the readings the step
gave the ambiguities above it. The site tests use these terms, each read from the log alone:
- **a real beat** is a heartbeat with an integer step, of the claim its `claim_seq` names;
- **a claim's values** are the integer-step values position gives it, and **a trailing value** is one above
  the claim's last real beat. A claim that beat no integer step has its last real beat below every step, so
  every value of it is trailing;
- **a claim's floor** is the lowest step its own records let it hold a commit at under some reading, its
  resume gap aside ("The resume family", below). For a claim that beat, it is the lowest step among its real
  beats, its values and the `final_step` of each `stopped` naming it that the log does not prove a third
  party's. For a claim that beat none, it is 0, since every step is a position some reading of
  `beatless-claim` gives it; the built-ins' positions, where defined, are its lowest value's step less one
  and the newest real beat of the claims before it;
- **a claim's candidate first steps** are the steps its first commit can have under some reading. For a
  claim that beat, they are its first real beat's step, the step of every value it holds below that beat,
  and, where claims before it hold a real beat, a value or such a `final_step` before its `started`, every
  step above the lowest floor of those claims and below its own, its possible resume gap. For a claim that
  beat none, they are every step up to the highest step a record of the run names, and for one that also
  holds no value and no such `final_step`, there are none;
- **a claim's proven end** is a `completed: true` stop naming it, or, where it is the newest claim naming its
  launch, that launch's `terminated` carrying its handle and following it. The claim ended there or before
  under every reading, so M4 leaves every value after it uncommitted ("Stops" and "Attributed death reports"
  below);
- **a stop the log proves a third party's** is defined under "Stops" below.

| ambiguity | the question | built-ins | today's reading |
|---|---|---|---|
| `malformed-stop` | what a `stopped` whose body 0.3.0 does not define stands for | `refuse` | none: M10 was mycooc's |
| `stop-author` | who wrote a `stopped` | `first-is-exit`, `last-is-exit`, `third-party`, `refuse` | `first-is-exit` |
| `third-party-stop` | what a third party's `stopped` says | `cause-only`, `ends-only`, `every-report`, `nothing`, `refuse` | `cause-only`, with `preempted` for `lost` |
| `value-claim` | which claim a value belongs to | `positional`, `latched`, `uncommitted`, `refuse` | `positional` |
| `beat-ends-iteration` | whether a claim's last real beat ends or starts its iteration | `ends`, `starts`, `refuse` | `ends` |
| `post-beat-values` | where a value at the last real beat's step, written after it, belongs | `label`, `exit-holds`, `refuse` | `label` |
| `leading-values` | what a value below its claim's first real beat is | `loading`, `unticked`, `refuse` | `loading` |
| `beatless-claim` | where a claim that beat no integer step was: the steps it reached | `loading`, `own-values`, `predecessor-beat`, `refuse` | `loading` |
| `valueless-gap` | whether a valueless step a claim's clock moved past was an iteration | `iteration`, `stride`, `refuse` | `iteration`, between two beats; today's step synthesized nothing elsewhere |
| `final-step` | what an exit's `final_step` says | `yielded`, `finished`, `finished-if-clean`, `refuse` | `finished-if-clean`, nearly |
| `exitless-step` | whether the highest step of a claim with no exit node finished | `unfinished`, `finished`, `refuse` | `unfinished`, nearly: today's step also left the valued steps below it uncommitted |
| `resume-origin` | whether a resumed claim's state came from a save the log's own claims made | `in-log`, `outside`, `refuse` | `in-log`, where a source reaches the step below the first; today's step read `outside` elsewhere |
| `resume-offset` | which step a resumed claim's state stands at | `previous-step`, `earlier-step`, `refuse` | `previous-step` |
| `resume-claim` | which claim's save of that step a resumed claim loaded | `newest-claim`, `prefer-beat`, `latest-record`, `refuse` | `newest-claim` |
| `commitless-resume` | where a claim that committed nothing resumed from | `newest-candidate`, `successor`, `fresh`, `refuse` | `newest-candidate` |

**`malformed-stop`: what a `stopped` whose body 0.3.0 does not define stands for.**
- **Site:** a `lifecycle.stopped` whose body is not lifecycle-v0.5's `Stopped`. It has no conforming writer,
  so its meaning is open by definition: the step's malformed-record ambiguity (`log-formats.md` §6). A
  malformed stop is also a site of `stop-author` and `third-party-stop`.
- **H1:** a reclaim tool wrote `{reason: "reclaimed: slurm job 777 TIMEOUT"}` for a dead claim, which needs a
  death report.
- **H2:** another tool wrote the same keys to mean something else, a note or a report about another
  process, which needs nothing to end.
- **The reading type:** the well-formed lifecycle-v0.5 `Stopped` body the record stands for, its claim, the
  ids it honoured and its `t` among them. `stop-author` and `third-party-stop` then read it as they read any
  `stopped`, and their strategies see the record's own body too. The mapped body stands for the record in
  the checks on the readings, so a mapped `completed: true` makes it its worker's exit there. It never makes a
  proven site, since a proof reads the log, never a reading.
- **Where it comes from.** The 0.2.0 → 0.3.0 step carries such a record with its body unread, adding only the
  names its rules write (`reference-by-name.md` §6), so this step is the first to read it. Every other
  record whose body 0.3.0 does not define stays a refusal (M9).
- **Built-ins:** `refuse` only.
- **Refuse-only:** yes. 0.3.0 gives the body no meaning, so any generic reading would guess one.

**`stop-author`: who wrote a `stopped`.**
- **Site:** a well-formed `stopped` naming a claim that the log does not prove either way ("Stops" below):
  it is not `completed: true`, no `stopped` naming its claim is, no heartbeat naming its claim follows it, and
  no `launcher.terminated` of its claim's own launch, carrying the claim's handle, precedes it. A site is an
  **open stop**. The owner's decision that every stop is a site (review §12, item 16) meets one pin:
  lifecycle-v0.5 makes `completed: true` the worker's opt-in claim, so a root whose stops are all completed,
  or proven a third party's, needs no `stop-author` resolution.
- **H1:** the reference Worker reached its chunk cap at step 9, drained stop x and wrote `{completed: false,
  error: null, final_step: 9, honored: [x]}`, which needs an exit node.
- **H2:** the worker was killed with its node, and an operator's tool wrote the same body for the dead
  claim, honouring the pending x, which needs a third party's stop.
- **The reading type:** `worker`, its claim's worker's own exit, which becomes the claim's exit node; or
  `third-party`, a third party's stop, which `third-party-stop` reads.
- **Built-ins.** Each picks, for each claim, the stop that is its worker's exit, if any; every other stop
  of the claim is a third party's.
  - `first-is-exit`: a claim's first open stop is its worker's exit. Today's reading, without the rules that
    moved out.
  - `last-is-exit`: a claim's last open stop is its worker's exit. It is 0.3.0's own verdict rule, "the newest
    wins" (`design-v0.2.md` §7).
  - `third-party`: every open stop is a third party's.
  - `refuse`.
- **Refuse-only:** no. The first two read the topic as 0.3.0 defines it, the worker's dying breath; the
  third declares that a run's open stops came from third parties.

**`third-party-stop`: what a third party's `stopped` says.**
- **Site:** every `stopped` that is not `completed: true`, malformed ones included. Any such stop may be a
  third party's, proven so or as `stop-author` reads it, and the log proves only authorship, never what a
  third party meant (review §12, item 16).
- **H1:** an operator's tool, clearing a stop it had staged, wrote a default-body stop after the worker's
  own exit, which needs only the withdrawal of that stop.
- **H2:** a supervisor wrote the same body on finding the claim's process gone, which needs a death report;
  and a runner may have written it of a subprocess that never claimed, which needs that process's end.
- **The reading type,** closed, for a stop read as a third party's:
  - **`Release(outcome, evidence)`:** a death report, a `supervisor.died` naming the stop's claim, in the
    stop's place, carrying the stop's `t`. `outcome` is supervisor-v0.5's, never `completed`, and `evidence`
    is non-empty text. It ends the claim, unless something ended it before, when it is a second report.
  - **`Report(reap)`:** a report that ends no claim. With `reap` null, nothing takes the stop's place. With
    `reap = Reap(how, launch)`, a `supervisor.terminated` does: a process's end, `how` being `exited` with
    an exit code or `killed` with a signal, under a launch id the step mints fresh from `launch`, against
    every request id the run bears or honours, as the 0.2.0 → 0.3.0 step mints, carrying the stop's `t`. No
    `launched` names that launch.

  Each turns every id the stop honoured into a `control.withdraw` placed just after it, so the stops stay
  spent, and a stop that becomes nothing else leaves the log, its withdrawals taking its place. The generic
  step knows no writer's texts: a consumer's strategy builds each reading from what it knows of its own
  writers, mycooc's whole catalogue included ("What moved out", below).
- **Built-ins.** A stop **ends its claim** when nothing ended the claim before it and the claim does not
  outlive it ("Stops" below). Each built-in reads a death report's outcome as `errored` when the stop's
  `error` is set, an empty one included, and `lost` otherwise, with the `error` as the evidence, else
  `"migrated: third-party stopped"`. `lost` says what a release with no cause says, that the claim ended and
  nothing of how; a consumer's strategy may say `preempted`.
  - `cause-only`: a stop that ends its claim is a death report; one after its claim's end is a death report
    only when its `error` is set, since once a claim has ended, only a report with a cause is written (§3);
    any other is a report that ends nothing. It is review §12 item 14's rule, today's, with `lost` where
    today wrote `preempted`.
  - `ends-only`: only a stop that ends its claim is a death report.
  - `every-report`: every stop naming a claim that the claim does not outlive is a death report. 0.3.0's
    verdict took only the newest terminal (`design-v0.2.md` §7); this keeps every one, as 0.4.0's outcome set
    counts every report.
  - `nothing`: no stop is a death report, so a claim that only third parties stopped stays held unless a
    reap ends it.
  - `refuse`.
- **Refuse-only:** no.

**`value-claim`: which claim a value belongs to.**
- **Site:** an integer-step value that position gives to a claim C_j that is not the run's first, when an
  earlier claim C_i was named by a `stopped` before C_j's `started`, and no `launcher.terminated` of C_i's own
  launch, carrying its handle, precedes the value. A claim that no `stopped` named before C_j's `started` was
  gated dead (`design-v0.2.md` §4 and §8), and a reap shows its process gone, so neither can have written it.
  A completed stop does not exclude C_i: its worker halted there, but may still have written a value.
- **H1:** C_j resumed, computed step 5, emitted `loss@5` and beat 5, which needs the value in C_j's commit
  at 5.
- **H2:** a third party released C_i while its worker lived; C_j claimed; C_i's worker, still in step 5,
  emitted `loss@5` and died, while C_j computed step 5 without that name, which needs the value left
  uncommitted, after its claim's end (M4). Or C_i's worker exited and then wrote a teardown value labelled 5,
  after C_j had claimed.
- **Built-ins:**
  - `positional`: the latest claim before it, as 0.3.0's value reads took it. Today's reading.
  - `latched`: a value at a site is left uncommitted, unless `stop-author` read a stop of each such earlier
    claim, before the value, as its worker's exit. It is right for a worker that writes nothing after its
    exit, as the reference Worker latches (worker.py:230-237).
  - `uncommitted`: every value at a site is left uncommitted. It never commits a displaced or exited
    worker's value, at the price of the successor's own.
  - `refuse`.
- **Refuse-only:** no. `positional` is the user's statement that no released worker outlived its release,
  and no exited one wrote after its exit.

**`beat-ends-iteration`: whether a claim's last real beat ends or starts its iteration.**
- **Site:** a claim's last real beat, at step s, unless the claim holds a value at s written before that
  beat, or a value above s. A value at s before the beat shows the clock at s before the tick, and a value
  above s shows iteration s over. A completed stop shows neither: a loop that ticks first and breaks at its
  tick writes the same completed exit as a `steps()` loop that finished its last body. Every claim in
  mycooc's and translation's logs is outside the site (a census of their logs, 2026-10-24), since each writes
  a value at its last beat's step before the beat, and so is every `steps()` claim that emits at its last
  step.
- **H1:** a `steps()` worker, which ticks after its body, beat 0, 1 and 2 and was killed in step 3's body,
  which needs three commits.
- **H2:** a manual loop that ticks first (`w.tick(s); body(s)`) beat 0, 1 and 2 and was killed in step 2's
  body, which needs two commits: its last beat started an iteration that never finished.
- **Built-ins:**
  - `ends`: the beat at s is the commit ending iteration s. Today's reading.
  - `starts`: the claim's last beat starts its iteration. It is a `lifecycle.heartbeat`, not a commit, and
    the claim's values at s are trailing values, which only `final-step`, `exitless-step` or a later claim's
    start (M5's third place) can vouch for. Earlier beats read alike under both readings: the next beat ends
    each iteration, and M2 and M6 place a value written after its beat in that beat's commit.
  - `refuse`.
- **Refuse-only:** no.

**`post-beat-values`: where a value at the last real beat's step, written after it, belongs.**
- **Site:** a value at its claim's last real beat's step s, placed after that beat. M4 leaves one after its
  claim's end uncommitted whatever this reads.
- **H1:** a worker that ticks and then emits wrote iteration s's metrics after `tick(s)`, which needs the
  value in commit s.
- **H2:** a `steps()` worker emitted after its loop, and `emit` stamped the last step's clock, which needs the
  value pending at the exit, as a native 0.4.0 exit holds it, off the lineage a later claim resumes from.
- **Built-ins:**
  - `label`: M2, the commit carrying its step. Today's reading.
  - `exit-holds`: the value is pending at its claim's exit. The exit node holds it when the claim's end is its
    worker's exit that did not error, and otherwise it stays uncommitted.
  - `refuse`.

  Under `beat-ends-iteration`'s `starts`, the last beat commits nothing, and these values are trailing values
  whatever this reads.
- **Refuse-only:** no.

**`leading-values`: what a value below its claim's first real beat is.**
- **Site:** a value of a claim that beat an integer step, at a step below its first real beat.
- **H1:** the claim resumed from step 4, labelled its loading status 4 and beat 5, which needs the value in
  its first commit and its resume point at step 4.
- **H2:** the claim resumed from step 3, ran step 4 without a tick, its values labelled 4, and beat 5, which
  needs a commit at 4 in the claim and its resume point at step 3.
- **Built-ins:**
  - `loading`: the claim's first iteration is its first real beat's, and its leading values go to its first
    commit. Today's reading.
  - `unticked`: each step from its lowest leading value's up to the one below its first real beat is an
    iteration it did not tick, a commit synthesized as at a skipped tick (a valueless one under
    `valueless-gap`'s `iteration`), so its first commit is at its lowest leading value's step. At step 0 that
    makes it a fresh start.
  - `refuse`.
- **Refuse-only:** no.

**`beatless-claim`: where a claim that beat no integer step was: the steps it reached.**
- **Site:** a claim with no real beat that holds a value before its proven end, if it has one, or that a
  `stopped` not proven a third party's names with an integer `final_step`. M4 leaves a value after the proven
  end uncommitted under every reading. Its position can change the output wherever it holds either:
  - something may vouch for a step its values name: a `final_step` at or above it, or a later claim's start
    one above it (the vouched claims);
  - `finished` commits a `final_step` above its position, valueless or not. The reference Worker's
    `steps(start=k)` writes only a `stopped` with `final_step: k` when its first body raises before emitting
    (worker.py:160, :179), and a worker that finds its target reached on load and calls
    `stopped(completed=True, final_step=k)` writes the same record, completed;
  - an exit node, which a completed stop names, may hold its open iteration's values;
  - with no exit node, `exitless-step` reads its highest step, and its site reaches every claim that no
    completed stop names.

  So every such claim is a site. The mirror's 03bb6fde, claim 1, is one: a loading retry with one step-0
  status may never have run step 0, been killed in it, or finished it and saved it.
- **H1:** the claim crashed while it loaded, having labelled its loading status s, which needs the value
  left uncommitted.
- **H2:** it computed step s, saved its checkpoint and crashed before its tick, and the next claim started at
  s+1, which needs a commit at s in this claim. The run cited as 625dda74 under "Figures" (`d78b72f1…`) has
  two such claims, which finished steps 182 and 183.
- **Built-ins:**
  - `loading`: it reached no step; its values stay uncommitted as loading, and nothing is synthesized in it.
    Today's reading.
  - `own-values`: it reached the steps its values name. Its position before its lowest value step s₀ is
    s₀−1, and its values are trailing values above that position. A claim that holds no value reached no
    step under it: it has no position, as under `loading`.
  - `predecessor-beat`: its position is the newest real beat of the claims before it, fixed before
    synthesis, or below every step where none beat one. Its values above that step are trailing values, and
    those at or below it stay uncommitted as loading.
  - `refuse`.

  Each gives a position only. What becomes of the steps above it is read as for any claim (below, "How the
  position composes").
- **Refuse-only:** no, but each reading declares how its writers label: `own-values` misreads a loading
  status written at a step as that step's work, and `predecessor-beat` misreads a claim that resumed below
  the newest beat, or above it, from a step an earlier claim finished without a beat.

**`valueless-gap`: whether a valueless step a claim's clock moved past was an iteration.**
- **Site:** a step at which a claim holds no value, where the claim's clock moved past it: strictly between
  two of its records at integer steps (real beats or values), below the `final_step` of a `stopped` naming it
  that the log does not prove a third party's, or below its highest trailing step; and at or above its floor,
  which for a claim that beat none is 0. Two kinds of step outside a claim's records are sites too ("The
  resume family", below):
  - each step a claim moved past in a run-on: for a claim C with a candidate first step s, and each claim
    before C that no completed `stopped` names and that holds a real beat or a value below s−1 before C's
    `started`, each step strictly between its highest such record and s−1;
  - each step of a possible resume gap: for a claim C that beat, and the claims before it that hold a real
    beat, a value or such a `final_step` before C's `started`, each step above the lowest floor of those
    claims and below C's own.
- **H1:** the worker ran an iteration whose tick it skipped, and which emitted nothing, which needs a commit.
- **H2:** the worker's clock moves by more than one per iteration, which needs no commit there.
- **Built-ins:**
  - `iteration`: a synthesized commit, wherever M5's first place, `leading-values`' `unticked`, a run-on or a
    resume gap reaches the step. Today's reading between two real beats; elsewhere today's step synthesizes
    nothing.
  - `stride`: no commit: the clock skipped the step.
  - `refuse`.
- **For a claim that beat none, its readings apply only above the position `beatless-claim` gives it,** and
  so never under `loading` ("How the position composes", below).
- **Refuse-only:** no.

**`final-step`: what an exit's `final_step` says.**
- **Site:** a `stopped` naming a claim that the log does not prove a third party's, when its `final_step` is
  above the claim's last real beat, or at it where that beat is a `beat-ends-iteration` site, or when the
  claim holds a trailing value above its `final_step` (any trailing value, when it is null). The readings
  agree on every other.
- **H1:** the worker finished its `final_step`. A `steps()` body at step 3 saved its checkpoint and then
  broke out on convergence, or its `tick(3)` raised after the body finished, and the exit wrote
  `final_step: 3`, which needs a commit at 3. Or a manual-tick worker, whose `final_step` is the last step
  it ticked, wrote `final_step: 2` after it finished step 3 and saved it but crashed before its tick, so its
  value at 3 is its open iteration, which a later claim starting at 4 says finished.
- **H2:** the worker had not finished its `final_step`. The body broke out, or crashed, before finishing step
  3, and the exit is the same, which needs step 3 to be the exit's open iteration; and a value above
  `final_step` is no iteration of this worker's.
- **The open iteration** of an exit is the step its worker had begun and not finished. Its values are held by
  the exit node of an exit that did not error, are uncommitted on an error exit, which is torn, and, either
  way, become a commit when a later claim's start says the step finished (M5's third place).
- **Built-ins.** The two kinds of exit leave the same pair of histories, the worker did or did not finish
  its `final_step`, and a torn exit discards only an iteration that did not finish (review §12, item 16).
  Under every reading, each valued step above the claim's position and below its `final_step` is a
  synthesized commit, since the worker's clock moved past it (M5's first place); a valueless one is
  `valueless-gap`'s.
  - `yielded`: `final_step` is the last step the worker yielded or ticked, which it need not have finished,
    as the reference Worker fills it. The step at `final_step` is the open iteration, and a trailing value
    above it is no iteration of this worker's, uncommitted and never a source (the ceiling). A null
    `final_step` is below every step.
  - `finished`: `final_step` is the last step the worker finished, and a synthesized commit: a valueless one
    too, where it lies above the claim's position (below), since a `final_step` is a step the clock reached.
    The steps above it are the open iteration. A null `final_step` finished no step. It reads mycooc's
    writers' `final_step` as they fill it (the ambiguity audit's R4).
  - `finished-if-clean`: a non-error exit's `final_step` as `finished` reads it, and an error exit's as
    `yielded` does. It is right for the reference `steps()` driver, whose clean exits follow a finished body
    (the loop's end, a stop at the tick, or a `break` after the save) and whose error exits are crashes inside
    the body. It is today's reading, but for two shapes `steps()` never writes: today's step also holds a
    clean exit to the ceiling, and vouches no step below an error exit's `final_step`.
  - `refuse`.

  A claim's position here is its last real beat, or the one before it where `beat-ends-iteration` reads the
  last as starting its iteration; for a claim that beat none, see "How the position composes", below.
- **Refuse-only:** no.

**`exitless-step`: whether the highest step of a claim with no exit node finished.**
- **Site:** for a claim C that no `completed: true` stop names, its highest trailing step s among its values
  before its proven end, every value of a claim that beat none being trailing; or, where its last real beat
  is a `beat-ends-iteration` site and C holds no value above it, that beat's step s, which `starts` makes
  trailing. No claim is excluded for a later claim whose first step is s+1. Under `resume-origin`'s
  `outside`, whose site holds at every such later claim, that start does not commit s in C; nor under
  `resume-offset`'s `earlier-step`, whose site holds wherever an earlier record lies below s, as one always
  does below a claim's trailing step above a beat; nor, for a claim that beat none, under `beatless-claim`'s
  `loading`. So the precedence below decides where a later claim's start commits s.
- **H1:** the worker was killed inside step s's body, or a third party released the claim there, which needs
  step s uncommitted.
- **H2:** the worker finished step s and saved it, then was killed before its tick, which needs a commit at
  s.
- **Its readings apply only to a claim that has no exit node,** as `stop-author` reads it: one a third party
  released, one a reap ended, or one nothing ended. `final-step` reads a claim that has one, so the two never
  read one claim. For a claim that beat none they apply at the highest step above the position
  `beatless-claim` gives it (below). Under every reading, each valued trailing step below s is a synthesized
  commit, since the worker's clock moved past it (M5's first place); a valueless one is `valueless-gap`'s. A
  later claim's start that commits step s takes precedence: M5's third place, or a run-on past s ("The
  resume family", below).
- **Built-ins:**
  - `unfinished`: step s is the claim's open iteration, and its values stay uncommitted. Today's reading;
    today's step also left the valued steps below s uncommitted, which the clock moved past.
  - `finished`: step s is a synthesized commit.
  - `refuse`.
- **Refuse-only:** no.

**How the position composes.** For a claim that beat no integer step, `beatless-claim`'s reading gives only a
position: none under `loading`, and otherwise a step, its values above which are trailing values.
`final-step`, where the claim has an exit node, `exitless-step`, where it has none, and `valueless-gap` then
read the claim's steps above that position as any claim's:
- M5's first place commits each valued step below the highest, for a claim with no exit node, or below its
  exit's `final_step`, since the clock moved past it; a valueless one there is `valueless-gap`'s;
- `final-step` reads its exit's `final_step`, and a non-error exit node holds the open iteration's values;
- `exitless-step` reads its highest valued step above the position.

Under `loading`, and under `own-values` for a claim with no value, the claim has no position: nothing is
left for any of them to read, `valueless-gap`'s readings never apply in it, and it is no claim that could
have saved a resume step unrecorded ("The resume family", below). Where neither a value nor its exit's
`final_step` lies above the position, nothing is left to read either. Site tests never read these readings;
strategies compose them (D3).

**The resume family.** Three ambiguities read where a claim C resumed from, one concern each (review §12,
item 16): whether its state came from a save of the log's own claims (`resume-origin`), at which step that
save stands (`resume-offset`), and which claim made it (`resume-claim`). C's **first step** s is its first
commit's old step, its resume gap aside (below). The family reads a claim whose first step is above 0, and
`resume-origin` also reads a claim that holds no commit. Their readings compose (D3): none is hard-wired to
another's choice, and the last two apply only under `resume-origin`'s `in-log`. Their terms, for a step r
below s:
- **a source of r** is a claim before C whose span holds r: r lies at or above its floor, and at or below one
  of its real beats' or values' steps or the `final_step` of a `stopped` naming it that the log does not
  prove a third party's, each before C's `started`. A source **reaches** r where the other readings let it:
  by a commit that is neither a run-on's nor a resume gap's, a valueless one under `valueless-gap`'s
  `iteration` only, or by trailing values that `final-step`'s reading does not put above the ceiling;
- **a claim that could have saved r unrecorded** is a claim before C with no exit node, as `stop-author`
  reads it, and a position below r, that holds a real beat or a value below r before C's `started`, and no
  trailing value at or above r: under the readings, its clock never passed r. A claim that beat none has a
  position only as `beatless-claim` reads it, and none under `loading`, so a loading claim saved nothing.
  Excluding exited claims is right for a writer whose `final_step` counts every step it began, as the
  reference Worker's does: an exited claim of such a writer that began r would have a `final_step` at or
  above it, and be a source;
- **the holders of r** are both the sources that reach it and the claims that could have saved it
  unrecorded. No claim is both: a source that reaches r has an exit node, a position at or above r, or a
  trailing value at or above it. Each built-in of `resume-claim` picks among the sources first, and among
  the others only where no source reaches r; a strategy may pick either kind;
- **a run-on** is what a holder of the second kind did: it ran on from the higher of its position and its
  highest record below r to r without logging those steps, and saved r (§1); the guide's recipe saves in the
  body, before the tick (`guide.md`, the checkpoint recipe). Its trailing steps below r, which it moved past,
  are commits; each step between is `valueless-gap`'s; and r is an empty synthesized commit in it, placed
  after its last record before C's `started`;
- **C's resume gap,** where C beat an integer step and r has a holder, is the steps strictly between r and
  C's floor. `valueless-gap` reads them: under `iteration`, C ran them without a tick or a value, and each is
  a synthesized commit of C, its first commit then at r+1; under `stride`, C skipped them. A claim that beat
  none has no resume gap, since its position says where it stood (`beatless-claim`).

`resume-offset` names r, `resume-claim` picks one of its holders, and the holder's commit at r is the parent
of C's first commit (M1), which M5's third place synthesizes where the holder reaches r only by trailing
values. Where r has no holder, C's first commit is a parentless resume under every reading. The family is
read with M5's third place, the starts from the last claim back (M5, below). A commit a run-on or a resume
gap adds is one start's inference, which no record shows, so no other start's holders read it.

**`resume-origin`: whether a resumed claim's state came from a save the log's own claims made.**
- **The reading type:** `in-log` or `outside`.
- **Site:** a claim C with a candidate first step s above 0, when a claim before it holds, before C's
  `started`, a real beat, a value or the integer `final_step` of a `stopped` not proven a third party's, and
  has its floor at or below s−1; and every site of `commitless-resume`, a claim that may hold no commit.
  Elsewhere the reading is proven: no claim before C can hold a step at or below s−1, as a source or as a
  claim that saved it unrecorded, so `in-log` and `outside` both give a parentless resume, or a null resume
  point.
- **H1:** C1 writes `loss@0`, beats 0 and dies; C2 writes `loss@1`, beats 1, writes `loss@2`, beats 2, and
  exits completed with `final_step: 2`. C2 loaded the checkpoint C1 saved at step 0, which needs C2's first
  commit parented on C1's commit at 0.
- **H2:** the same log, where C2 loaded a checkpoint copied in under the run's id, one no claim of the log
  saved, which needs a parentless resume.
- **Built-ins:**
  - `in-log`: C's state descends from a save of a claim before it: the holder `resume-claim` picks of the
    step `resume-offset` reads, or a parentless resume where that step has no holder; for a claim that holds
    no commit, the node `commitless-resume` reads. Today's reading where a source reaches the step below the
    first, and for a claim that holds no commit. Where no source reaches it, today's step read a parentless
    resume, as `outside` does, where this reading can take a run-on.
  - `outside`: C's state came from outside the log, a checkpoint copied in under the run's id. Its first
    commit is a parentless resume, which P1 and P3 name; a claim that holds no commit has a null resume
    point, `Inferred(None)`.
  - `refuse`.
- **Refuse-only:** no.

**`resume-offset`: which step a resumed claim's state stands at.**
- **The reading type:** a step r below s.
- **Site:** a claim C with a candidate first step s above 0, when a claim before it that holds a real beat, a
  value or such a `final_step` before C's `started` has its floor below s−1: its writer may have resumed past
  its save. Otherwise the step below the first, s−1, is the proven reading: no claim of the log reached a
  step below s−1 under any reading, so no save of the log lies there.
- **H1:** C1 beat 0 to 9 and saved every step; C2, whose first beat is 10, loaded C1's save at 9, which needs
  C1's step 9.
- **H2:** the same log, where C1 saved every second step and C2 loaded its save at 8, which needs C1's step 8.
  Whether C2 then ran 9 without logging it or its clock skipped 9 is `valueless-gap`'s, at C2's resume gap.
- **Built-ins:**
  - `previous-step`: s−1. Today's reading.
  - `earlier-step`: the highest step below s−1 that a source reaches; with none, C's first commit is a
    parentless resume. It says only where the state stood, for a writer that saves every second step;
    whether C ran the steps between is `valueless-gap`'s.
  - `refuse`.
- **Its readings apply only under `resume-origin`'s `in-log`.**
- **Refuse-only:** no.

**`resume-claim`: which claim's save of that step a resumed claim loaded.**
- **The reading type:** one holder of r, of either kind.
- **Site:** a claim C with a candidate first step s above 0, when two or more claims before it that hold a
  real beat, a value or such a `final_step` before C's `started` have their floor at or below s−1: under some
  reading of `resume-offset`, each could hold the resume step, as a source or by a run-on. Otherwise the one
  such claim is the proven choice where it is a holder of that step, and else there is none, a parentless
  resume: no other claim reached a step at or below s−1 under any reading.
- **H1:** C1 and C2 both beat step 3, and C3, whose first beat is 4, loaded the checkpoint C2 saved, its run
  keeping one directory per claim, which needs C3's parent at C2's step 3.
- **H2:** the same log, where C3 loaded C1's save at step 3, its run keeping one file per step, or one
  location that C1's displaced worker wrote last, which needs C1's step 3.
- **H3:** C1 beats 0 to 3 and C2 beats 0 to 1, and neither has an exit node; C3, whose first beat is 6,
  loaded a save at 5 that one of them made after its last beat without logging (§1), which needs that
  claim's run-on to 5: C2's, the newer, or C1's, where C1's displaced worker saved last to one location.
- **H4:** G1 beats 0 to 5 and G2 beats 0 to 3, and neither has an exit node; G3, whose first beat is 6, loaded
  G2's save at 5, which G2 made running on from 3 in a run that keeps one directory per claim. That needs
  G2's run-on, which only a strategy that picks a saver over the source G1 gives.
- **Built-ins.** Each picks one holder of the step r that `resume-offset` reads, among the sources that reach
  r, or, where none does, among the claims that could have saved it unrecorded.
  - `newest-claim`: the newest such holder in claim order. Today's reading.
  - `prefer-beat`: one that beat r over one that reaches it otherwise, the newest among equals; for writers
    that save a checkpoint at or after their tick. Where none beat r, as `newest-claim`.
  - `latest-record`: the one whose last record at a step at or below r, before C's `started`, is the latest;
    for a run that keeps one checkpoint location, which each save overwrites, so the claim at r latest saved
    last. A claim that saved r unrecorded saved it after that record.
  - `refuse`.
- **Its readings apply only under `resume-origin`'s `in-log`.**
- **Refuse-only:** no.

**`commitless-resume`: where a claim that committed nothing resumed from.**
- **Site:** a claim that beat no integer step, or whose one real beat is a `beat-ends-iteration` site, when an
  earlier claim holds a real beat or a value below its `started`. With none, its resume point is null, the
  proven reading.
- **H1:** claim J loaded A's newest checkpoint, at step 133, and crashed while it loaded, which needs
  `Inferred` at A's step 133.
- **H2:** J's run keeps one checkpoint file, which held A's save at step 80, as B's later start at 81 shows;
  J loaded it, or crashed before it loaded anything, which needs A's step 80, or null.
- **Built-ins:**
  - `newest-candidate`: the newest candidate (M1) of the newest earlier claim holding one. Today's reading.
  - `successor`: the node that the next claim after it to resume from a candidate resumed from, when that
    node is also one of this claim's candidates, and a refusal of the run where there is none; for a run that
    keeps one checkpoint location.
  - `fresh`: null, a claim that loaded nothing.
  - `refuse`.
- **Its readings apply only under `resume-origin`'s `in-log`,** and ask only for the node: whether the
  claim's state came from outside the log is `resume-origin`'s.
- **Refuse-only:** no.

**The rules** (review §5, §11.3 and §12 items 0.13–0.21, 10, 13, 14 and 16):
- **M1. Parent.** M1 reads the commits after synthesis, in claim order, never by step nearness.
  - **A claim's candidates** are the commits of earlier claims placed below its `started` (M6). Only a beat
    can place a commit above it: a real commit's own heartbeat, or, for a synthetic commit, the real heartbeat
    before it in its claim, since M6 places it after its parent. So a displaced worker's commit, made or
    completed after the claim began, is never its resume point, and a resume point always precedes its claim.
  - A claim's later commits take the claim's previous commit.
  - A claim's first commit, at its first step s (old step; "The resume family", above), is a fresh start
    when s is 0, with a null parent. Otherwise it takes the node the resume family reads: under
    `resume-origin`'s `outside`, a null parent, a parentless resume; under `in-log`, the commit at the step r
    `resume-offset` reads, of the holder `resume-claim` picks, or a null parent where r has no holder. Where
    `valueless-gap`'s `iteration` commits the claim's resume gap, the gap's first commit, at r+1, takes that
    node instead. Today's readings, `previous-step` and `newest-claim`, take **in claim order** the newest
    earlier claim holding a candidate with old step s−1, and that candidate; `earlier-step` reads an earlier
    step. Where no source reached s−1, today's step wrote a parentless resume, as `outside` does. A commit is
    the parent even where an extendable exit follows it (review §12, item 14): no 0.3.0 log saved a checkpoint
    after its exit, and a native log has the same shape when a claim resumes from a commit's checkpoint.
  - The claim's `started.resume_from` is its first commit's parent.
  - **A claim that holds no commit** takes as its `resume_from` what `commitless-resume` reads under
    `resume-origin`'s `in-log`, labelled as an inference, and null under `outside`, or when no earlier claim
    holds a candidate. Today's reading takes the newest candidate of
    the newest earlier claim holding one. With null instead, 7 runs read empty (8,449 cells): a retry that
    crashed while loading is recorded as a fresh start.
  - **The exit's `parent`** is the claim's position at the exit (§2): its newest commit, else its resume
    point. A claim that holds no commit has its exit's parent at its resume point.
  - **Each resume point is labelled** by the rule that chose it, so a report can count them: `Fresh()` for a
    first commit at old step 0, `Resumed(node)`, `Parentless()`, and `Inferred(node)` for a claim that holds
    no commit, which is `Inferred(None)` when no earlier claim holds a candidate, as for a commitless first
    claim.
- **M2. The step label wins.** A value with an integer step is committed by its claim's commit carrying that
  old step, even when it also follows the claim's last heartbeat, under `post-beat-values`' `label`.
- **M3. Dropped** (review §12, item 13). Stepless values are registers (`config`, `completion_reason`,
  `status` and `input_provenance`) and stay outside every lineage, as an upgraded consumer leaves them.
- **M4. Values after their claim's end stay uncommitted.** There are 1,886 of them, all teardown `status`. A
  claim's end is the first record that ends it ("Stops" below): its worker's exit, a death report, or, for a
  claim nothing ended before it, the `terminated` a `died` is attributed from, since a death report ends its
  claim (§2).
- **M5. A step commit is synthesized in exactly three places,** each above its claim's first real beat, or,
  for a claim that beat none, above the position `beatless-claim` gives it:
  - for a step a claim's clock moved past without a tick: between two of its real beats, below its exit's
    `final_step`, or, for a claim with no exit node, below its highest trailing step. A valued one is
    committed whatever the clock's increments, since its value shows the clock at that step and the claim
    moving on; a valueless one only under `valueless-gap`'s `iteration`. `Commits()` must count it;
  - for the step `final-step` or `exitless-step` vouches for under the reading `finished`: an exit's
    `final_step`, or the highest trailing step of a claim with no exit node. This takes precedence over the
    exit node, which holds only what no commit claims;
  - for a trailing step r, whatever ended its claim, in the holder `resume-claim` picks for a later claim
    that resumes from r under `resume-origin`'s `in-log` (review §12, item 14): the step below its first under
    `resume-offset`'s `previous-step`, or an earlier one under `earlier-step`. Today's reading takes the
    newest claim before it, in claim order, to reach step r, by a commit or by trailing values not above its
    exit's ceiling, where it has one. M1 chooses it wherever its commit at r is a candidate, as it is unless a
    displaced worker beat after the later claim began. A claim that resumes from r says step r finished in the
    claim it resumed from, and M1 then finds the commit the old read showed. The later claim is any, not only
    the next, since a retry that crashed while loading beats nothing. **A third-place commit at r is a step
    the holder's clock moved past,** so the first place commits the holder's valued trailing steps below r,
    as it does below any other highest step, and a valueless one there is `valueless-gap`'s. A trailing step
    that neither the first two places, such a claim nor a run-on vouches for stays uncommitted: nothing says
    its iteration finished.

  **The starts are read from the last claim back, each once, and that order is the rule.** A commit the
  third place, a run-on or a resume gap synthesizes can give a claim that held none its first commit, and
  that claim's start then reads the resume family in turn: in the run cited as 625dda74 (`beatless-claim`'s
  H2), claim 4215's start synthesizes 4188's step 182, and only then does 4188's start vouch for claim 1's
  181. A start synthesizes only in the claims before it, and in its own claim's resume gap, below that
  claim's first step, so a claim's commits are final when its own start is read. No holder and no choice
  depends on what a start synthesizes, since a source reaches a step by its records and the per-claim
  readings, never by a run-on's or a resume gap's commit. So the pass reaches a fixpoint: after it, no start
  synthesizes a new commit.
  - **The third place alone is monotone.** A claim's first step, once set, never falls: a holder's
    uncommitted trailing steps all lie above its commits, since the first place commits the valued ones below
    each highest step, a third-place commit's included. So the third place reaches the same fixpoint in any
    order, its least (golden `THIRD_PLACE_MOVES_PAST`).
  - **A run-on is not monotone.** Under `valueless-gap`'s `stride`, a run-on to r gives a claim that holds no
    commit of its own only r. A run-on in that claim to a lower step, read after the claim's own start, lowers
    its first step and leaves what its start synthesized at the higher one. So no other order is the rule.

  Five readings synthesize outside those places, and each commit they make is labelled by its reading:
  `leading-values`' `unticked`, at the steps below the first real beat, as at a skipped tick;
  `beatless-claim`'s `own-values` and `predecessor-beat`, which make a claim's valued steps trailing values
  for the three places to read; `resume-origin`'s `in-log`, where the holder `resume-claim` picks could have
  saved the resume step unrecorded: a run-on, the commits up to the step it saved ("The resume family",
  above); and `valueless-gap`'s `iteration` in a claim's resume gap.

  Only a trailing value is held to `final-step`'s reading: one written before its claim's end at a real beat's
  step (M2), at a skipped tick, or below the first real beat is committed whatever the exit reports. Only an
  exit node has a `final_step` to read: a claim a third party released, a reap ended, or nothing ended has no
  ceiling, and `exitless-step` reads its trailing steps, above the position `beatless-claim` gives it where
  it beat none. A value `value-claim`'s reading leaves uncommitted, or that M4 puts after its claim's end,
  makes no step trailing and reaches none. Leading values, below a claim's first real beat, go to its first
  commit under `leading-values`' `loading`. **A claim that beat no integer step reaches no step** under
  `beatless-claim`'s `loading`: its values stay uncommitted as loading, whether it wrote them while it loaded
  or in a step it never beat, and nothing is synthesized in it.

  A value no commit holds stays uncommitted for the first of these that holds: it is stepless (M3); before
  every claim; left by `value-claim`'s `latched` or `uncommitted`; after its claim's end (M4); a claim's that
  beat no integer step; above its exit's ceiling, under `final-step`'s `yielded`; or a step of an open
  iteration that no exit node holds and no later claim's start vouches for.
- **M6. Move each commit,** in claim order and each claim's in its order, to just after the latest of: its
  last committed value, its parent, the record its `consumed_seq` names and, for a real commit, its own
  heartbeat, so a commit only moves forward. Then renumber, once. A commit asserts that its values are
  complete, which holds only once the last of them exists, so moving the commit keeps every record at an
  honest position; moving the values would place them before a beat that preceded their computation. The
  record a `consumed_seq` names never decides, since it precedes the heartbeat and M6 never moves it (M9). A
  commit is then placed below a later claim's `started` exactly when M1 counts it a candidate. The same pass
  places each withdrawal just after its stop, in `honored` order, and each death report attributed from a
  reap just after its `terminated`.
- **M7. The rename list is derived from the schemas:** `claim_seq` (on a heartbeat, a commit, an exit and a
  `died`), `consumed_seq`, `parent`, `values` and `resume_from`. A zero `consumed_seq` names nothing and
  stays zero. `stop@N` request ids stay as they are, since they are strings. No `bound` is left to rename: M9
  refuses every subscription's record.
- **M8. A synthetic commit** takes `t` from the newest value it commits that carries one, a value's `t` being
  nullable, else from its claim's newest heartbeat before it, else from the `created_at` of the record it is
  placed after; `consumed_seq` from that heartbeat, else 0; and `fired: []` and `expired: []`. That
  heartbeat decides whether it is a candidate, else the record it is placed after. M5's three places
  synthesize above a claim's first real beat, where that heartbeat exists, or above the position of a claim
  that beat none, which may have none, as may a commit `unticked`, a run-on or a resume gap synthesizes; and a
  valueless one has no value to date it by.
- **M9. Refusals,** each before sealing, naming the run and the seq. What 0.4.0 cannot hold:
  - any subscription's record: a `control.subscribe`, a `control.unsubscribe`, a `lifecycle.bound` and a
    sample, a value bearing a `request_id`. Refusing the `bound` keeps every lease out of the passes, whether
    or not its subscribe is in the log;
  - a `control.stop` whose `from` holds a step atom, at any depth;
  - an in-claim rewind: a heartbeat at or below an integer step its claim already beat, as two heartbeats of
    one step in one claim or a step that goes down. A 0.4.0 writer records a rewind, and nothing in 0.3.0 says
    where one resumed;
  - a value with a null `name`, which value-v0.3 cannot hold, and an empty one, which the envelope refuses.

  The corpus holds none of the first three. A consumer that later holds such logs revives the question with
  that log in hand.

  The step reads every record as 0.3.0's schemas pin it, so an input it accepts never yields a row 0.4.0
  rejects. It reads a body strictly, exactly the keys its schema pins with each value of its JSON type, so it
  refuses an integer written as a whole-number float, such as 3.0, which 0.3.0's schemas accept. It also
  refuses:
  - a record envelope-v0.2 rejects: an empty topic, name or request id, or a body that is not an object, a
    `started`'s included;
  - a topic under a prefix 0.3.0 reserves (`lifecycle.`, `control.`, `launcher.`) that 0.3.0 does not define,
    as a `control.withdraw` or a `lifecycle.commit` would be;
  - a user's topic under `supervisor.`, valid 0.3.0, which left the prefix to users: 0.4.0 reserves it;
  - a `started`, `heartbeat`, `nak` or `value` whose body is not 0.3.0's; and a `control.stop` with no
    request id, or whose body is not subscription-v0.3's `StopTrigger`. A `stopped` whose body is not
    0.3.0's is `malformed-stop`'s site, refused unless a strategy reads it;
  - a `launcher.*` record with no launch id, as a `terminated` with a null `request_id`, or whose body is not
    launcher-v0.4's: `exited` carries an exit code and no signal, and `killed` a signal and no exit code;
  - an envelope field 0.4.0 pins null that holds a value: a lifecycle record's or a stop's `name`, or a
    heartbeat's or a `stopped`'s `request_id`. No 0.3.0 reference writer set one;
  - a `stopped` whose `honored` holds the empty id, valid 0.3.0. It names no stop, since envelope-v0.2 forbids
    an empty request id, and 0.4.0 holds a withdrawn id and an exit's `honored` items non-empty;
  - a `stopped` the log proves both its worker's and a third party's ("Stops" below): no conforming writer of
    0.3.0 produces one;
  - a heartbeat or `stopped` whose `claim_seq` names no claim before it;
  - a heartbeat whose nonzero `consumed_seq` names no record before it, a heartbeat with an integer step, or a
    third party's stop that becomes only its withdrawals. The watermark is a read position in the control
    order, and renaming cannot carry one that names no record, a record M6 moves, or one that leaves the log;
  - a second `terminated` of any launch. A process ends once, and a run nothing claimed takes its launch's
    `terminated` as its verdict (§5), which two make ambiguous;
  - a launch that not exactly one `launched` names, where an attributed death report reads it. Without one
    there is no handle to attribute the death by, and writing no `died` would leave the claim held forever.

  The liveness check that `runstate migrate` asks before it moves a run refuses one whose latest claim no
  `stopped` names and whose `started` carries no handle, its body not an object holding one: whether the
  claim is live cannot be told.
- **M10. A malformed `stopped`** (the reclaim tool's 7 `{reason, honored, claim_seq}` bodies) is no longer
  read by the generic step: it is `malformed-stop`'s site (review §12, item 16). The owner's M10 mapping
  (review §12, item 14) is now the content of mycooc's strategies: the body stands for a default-body stop of
  its claim, honouring its ids, dated by its record's `created_at` since it has none (`malformed-stop`); it is
  a third party's (`stop-author`); and the first naming its claim is a release, `supervisor.died {claim_seq,
  outcome: preempted, evidence: <its reason>, t}`, the outcome the tool argues for in its own comments, an
  empty reason taking the mapping's label as its evidence (`third-party-stop`).
- **Stops** (review §12, items 5, 10, 14 and 16).
  - **What the log proves about authorship.** A well-formed `stopped` naming a claim is:
    - **its worker's exit** when it is `completed: true`;
    - **a third party's** when a heartbeat naming its claim follows it, since the worker outlived it; when
      another `stopped` naming its claim is `completed: true`, since a worker's `stopped` is its halt
      (lifecycle-v0.5) and it halts once; or when a `launcher.terminated` of its claim's own launch, carrying
      the claim's handle, precedes it, since the process had ended.

    A `stopped` naming no claim is a third party's, since a worker's exit names its claim. One the log
    proves both ways, as a completed one its claim beats after, is refused (M9). Every other is an open stop,
    `stop-author`'s site. The log proves authorship only: what a third party's stop says is
    `third-party-stop`'s, at every one of them.
  - **A claim's end** is the first of: its worker's exit, a death report, and the `terminated` a death report
    is attributed from (below). **A claim outlives a stop** when a heartbeat naming the claim, or the claim's
    worker's exit, follows the stop.
  - **The checks on the readings,** per claim: at most one worker's exit; none for a stop proven a third
    party's, and a completed stop is one, a malformed stop whose mapped body is completed included; and no
    death report for a stop its claim outlives, or for one naming no claim, since a death report would end a
    claim that was not dead. A reading that fails is refused, naming the strategy and the seq.
  - **Three rules follow.** Each was found in the ambiguity audit (review §12, item 16), and today's step is
    wrong for every conforming writer there:
    - **A completed `stopped` after another `stopped` of its claim is the claim's exit,** and the earlier one
      a third party's that its claim outlives, which ends nothing under every reading. Today the step kept
      the first as the exit and dropped the completion. Golden `LATER_COMPLETED`: the run's one claim beats
      0 to 3; a stop x; a default-body `stopped` honouring x; a value at step 4; a completed `stopped` with
      `final_step: 4`. It gives a closed exit node at the completion; the first `stopped` becomes only the
      withdrawal of x; and the value at 4 is a commit at 4 under `final-step`'s `finished`, or held by the
      exit node under `yielded`.
    - **A `stopped` its claim beats past ends nothing,** under every reading, and the claim's next end is its
      end. Today the step made it a release, a `died`, and left the claim's later work after its end. Golden
      `BEATEN_PAST`: the run's one claim beats 0 to 2; a `stopped` with an error; a value at step 3; a beat at
      3; a completed `stopped`. The first `stopped` leaves the log; beat 3 is a commit holding the value at 3;
      and the completion is a closed exit node. The golden "a stop its claim beats after" (below) now reads so
      too.
    - **A `stopped` after its claim's own reap is a third party's,** and the reap ends the claim: its death
      report is attributed from the `terminated` (below), and the `stopped` is after its claim's end. Today
      the step read the stop as the worker's exit and wrote no death. Golden `STOPPED_AFTER_ITS_REAP`: a
      `launched` of launch L with handle h; the claim's `started` with h and L; values and beats 0 to 2; a
      value at step 3; L's `terminated`, killed by signal 9; a stop x; a default-body `stopped` honouring x.
      Under `third-party-stop`'s `cause-only` and `exitless-step`'s `unfinished`, it gives
      `supervisor.died {claim, killed, "signal 9"}` just after the `terminated`, the `stopped` as only the
      withdrawal of x, no exit node, and the value at 3 as the claim's open iteration, uncommitted.
- **Attributed death reports** (review §12, items 13, 14 and 16). A claim that 0.3.0 saw ended only by its
  launch's `launcher.terminated` gets a `supervisor.died` from it, placed just after it, when the launch's
  handle is the claim's: the same `local://host/pid`, compared whole, since two hosts can share a pid. The
  launch is the one the claim names, by the launch id its `started` re-emitted, and its handle is its
  `launched`'s, found by that id anywhere in the log, since a child can claim before its parent writes
  `launched`. Only the newest claim naming the launch gets one, when nothing ended it before the
  `terminated` and the `terminated` follows it, since §3's live launcher reports only for that claim; an
  older claim of the same launch stays as 0.3.0 left it. A `stopped` naming the claim after the
  `terminated` does not take the report away: the reap proves it a third party's (above). A ThreadLauncher's
  launch carries its own process's handle, which is its worker's, so it matches, as the live rule attributes
  it by launch id. The report mirrors the live reap: a signal maps to `killed`, a nonzero exit code to
  `errored` and an exit 0 to `lost`, with `signal s` or `exit n` as the evidence and the record's `t`. The
  `terminated` is a reap, which is evidence; the migration only attributes it to the claim, as a live
  launcher does when it reaps. A launch whose handle differs (a wrapper) gets none; its run's verdict changes
  from the wrapper's exit to none, a correction, which P4 attributes. A claim released only by a
  `resolve()`-dead handle is the next rule's, when a later claim followed it; the latest such claim needs
  nothing, since a same-host caller of `report_if_dead` reports it (§3).
- **A claim its successor's claim proves dead** (review §12, item 16). A claim that no `stopped` names and no
  attributed death ends, and that a later claim follows, gets `supervisor.died {claim, lost, evidence naming
  its handle}`, carrying the later `started`'s `t`, placed just before that `started`. The later claim passed
  0.3.0's claim gate (`design-v0.2.md` §4 and §8), which found this one's worker dead, and that is when
  0.4.0's claim path writes `died(lost)` (§4). Today the step left such a claim held for good, a shape no
  native writer produces, since `report_if_dead` reads only the latest claim. Golden `SUCCESSOR_PROVED_DEAD`:
  claim 1, handle `local://h/111`, beats 0 and 1; claim 2, handle `local://h/222`, beats 2 and exits completed
  at 2. Under `beat-ends-iteration`'s `ends`, it gives that `died` for claim 1 just before claim 2's
  `started`. Found in the ambiguity audit, and wrong today for every writer that claims through the gate.

**What moved out of the generic step** (review §12, item 16). These rules read one consumer's texts or
shapes, so the generic step no longer has them. Each is a reading a consumer's strategy may give, and
mycooc's strategies, which mycooc writes, are where they now live. The owner's rulings on them, c′ and the
M10 mapping (review §12, item 14), are the content of those strategies. A built-in is any reading expressible
over 0.3.0's own vocabulary that is right for some conforming writer; a reading of a consumer's texts or
shapes is a consumer's strategy (review §12, item 16).
- **the runner's hard-crash backstop,** identified by its error text, "subprocess exited N with no terminal
  record", and read as a third party's wherever it falls: a `stop-author` reading;
- **c′:** a backstop that ends no claim, a later one or one naming no claim, becomes a
  `supervisor.terminated`, `exited` with exit code N or `killed` by signal −N, under a launch id minted
  `backstop@<seq>`: `Report(Reap(how, "backstop@<seq>"))`, a `third-party-stop` reading, which the step makes
  fresh;
- **M10's reading** of the reclaim tool's malformed body (above): `malformed-stop`, `stop-author` and
  `third-party-stop` readings;
- **clause (ii) of the rule for lone stops:** a stop whose `t` precedes its own `created_at` by more than 60 s
  read as a third party's, because a self-report stamps both from one clock at one moment: a `stop-author`
  reading, with its constant and its clock assumption;
- **the readings the ambiguity audit found right only for mycooc's writers:** a later error `stopped` after an
  operator's default-body stop on a live claim, read as the worker's torn exit, which `last-is-exit` gives
  every claim, wrongly for mycooc's other claims; a backstop release's signal read as `killed`, not
  `errored`; a death with no cause read as `preempted`; and the reclaim tool's death-report text read as a
  release wherever it falls.

Two readings the audit found right only for mycooc ship as built-ins, since they read no consumer's text:
`final-step=finished` (the audit's R4, with W-c) and `commitless-resume=successor` (W-b). So does the
step's own reading of `final_step` today, as `final-step=finished-if-clean`.

A malformed `stopped` is refused unless `malformed-stop`'s strategy reads it, and any other malformed record
the step reads is refused outright (M9; `log-formats.md` §6).

**The only renumbering step.** 0.2.0 → 0.3.0 only appended, so this is the only step that renumbers, and the
last that may: from 0.4.0 on, checkpoints hold seqs. The runner **enforces** it: every later step must keep
each input record's seq and topic. Appending instead of renumbering was rejected: it would add about 1.23M
records and leave shapes no 0.4.0 writer produces.

**The properties to test.** They hold under the readings chosen. P2's and P3's classes name the reading
behind each, and a corpus run states its resolutions.
- **P1:** for every 0.3.0 heartbeat with integer step s whose commit's chain M1 linked back to old step 0,
  `count(Commits(), its commit) = s + 1`. A parentless resume is counted, and named. A chain across a
  trailing gap, which M5 leaves without commits, counts too few and fails under that name, and so does a
  chain across a step `valueless-gap`'s `stride` leaves without a commit, a resume gap's included.
- **P2:** every value with a step is committed by its claim's commit carrying that step, except leading
  values, which go to the claim's first commit by design, and these, which stay uncommitted or sit on an exit
  node: values before every claim; values after their claim's end (M4); the values of a claim that beat no
  integer step (loading); trailing values above their exit's ceiling; an open iteration's values, which an
  exit node holds or no exit does; the highest trailing step of a claim a third party released, a reap
  ended, or nothing ended, under `exitless-step`'s `unfinished`; the values `value-claim`'s `latched` and
  `uncommitted` leave; and the post-beat values an exit node holds under `post-beat-values`' `exit-holds`.
- **P3:** reads equal the old reads **cell by cell, by attribution.** Every differing cell must trace to a
  named rule: cells beyond the head's lineage, steps the lineage revisited, nodes holding several values,
  leading values, a parentless resume, a parentless resume by M1's candidacy rule, a trailing gap (a gap of
  steps with no values below a trailing step M5 synthesized), loading cells, M4, trailing values above
  their exit's ceiling (a displaced worker's or a crashed iteration's), an open iteration's values, the
  unfinished highest trailing step of a claim a third party released, a reap ended, or nothing ended, the
  values `value-claim` leaves, values an exit node holds, a last beat `beat-ends-iteration` reads as starting
  its iteration, and a step `valueless-gap` reads as a stride. A value before every claim has no class, so a
  cell it explains is unattributed. An unattributed cell fails the test.
- **P4:** the verdict's outcomes, 0.3.0's one outcome taken as a one-element set, and the stop fold's ids
  (`peek_terminal`, `undischarged_stops`) are equal across the migration, except where a named rule changes
  the verdict: a third party's death report, `lost` where 0.3.0 read `preempted` when it carries no cause; a
  third party's later stop; a stop read as a reap, now a `terminated`, on which a claimed run's verdict no
  longer rests and a never-claimed run's does, a signal reading `killed` where 0.3.0 read `errored`; a
  claimless `stopped` that was a run's only terminal; an attributed death report (an exit 0 changes
  `completed` to `lost`), one attributed past a later `stopped` included; a wrapper's `terminated`; and a
  third party's stop that ends nothing, which leaves a claim nothing else ended held. No rule changes the stop
  fold's ids. A verdict read that raises is a value: two raises are equal, and a raise differs from every
  verdict.
- **Also:** 0 commits name a later value, 0 parents follow their child, 0 commits or heartbeats precede the
  record their `consumed_seq` names, 0 resume points follow their claim, 0 records name a claim at or after
  themselves, every seq-valued field names a record of the kind it names, no claim has a second exit, and
  every migrated record conforms to the 0.4.0 schemas.

**Golden logs.** Each runs under stated resolutions, and states the reading of every ambiguity it is a site
of: the built-in marked today's reading, unless it tests another. Where today's rule is no built-in, the
marked one is the nearest: `finished-if-clean` for `final-step`, `unfinished` for `exitless-step`,
`iteration` for `valueless-gap`, and `in-log` for `resume-origin`, with `previous-step` for `resume-offset`
and `newest-claim` for `resume-claim` beside it. A golden whose output today's step gave otherwise says so.
- **lineage:** the displacement-parent history; a displaced worker's beats, which keep their claim; a
  displaced worker's value above its successor's exit's last step; a commitless claim; a parentless resume;
  a resume past a preempted exit, which parents on the commit; and M1's candidacy: a loading retry beside a
  displaced worker, a resume from a step a displaced worker committed after the claim, a resume on an older
  claim's step, a doubly displaced claim whose third-place commit M1 passes over, and a tick a displaced
  worker skipped after the claim and across it, no candidate and a candidate;
- **values:** a step-0 loading `status`; a skipped tick, with and without values; post-loop values; a
  trailing step before a non-error exit (M5's second case); M5's third case past a retry that crashed while
  loading, and beside a newer claim that reached the step by a real beat; a claim that beat no integer step,
  as a loading retry the backstop released, one nothing ended, one whose status sits at its resume step, and
  one that finished a whole step; values after `stopped`, and after an attributed death's `terminated`;
- **records:** a worker's exit honouring a stop; errored and preempted exits; a stepless heartbeat;
  watermarks that renumbering moves; `launcher.*` records moved to `supervisor.*`;
- **third parties:** a malformed `stopped`, and its repair; one log per class of third-party stop: the
  backstop releasing a claim, then a default body; later stops with an error, with a default body, M10's, and
  with an error that honoured a stop; claimless stops with a default body, with an error and M10's; and the
  backstops that end no claim: one naming none, one after a first backstop, one after the worker's exit, and
  one whose minted id is taken;
- **lone stops:** a stop its claim beats after, which now ends nothing; the reclaim tool's stop written after
  the time it reports, with a default body and with its cause; the 60 s boundary on either side; and the
  cooperative stop, a worker's own exit;
- **attributed deaths:** a claim ended only by `terminated`, with an equal handle, a differing one and a pid
  shared across hosts; a late reap after a relaunch; and one launch claimed twice;
- **the audit's rules:** `LATER_COMPLETED`, `BEATEN_PAST`, `STOPPED_AFTER_ITS_REAP` and
  `SUCCESSOR_PROVED_DEAD` (above);
- **the third place:** `THIRD_PLACE_MOVES_PAST`. A beats 0 to 4 with a value at each and writes `loss@6`, and
  nothing ends it. B writes `loss@5` and `loss@7`, and an error `stopped` with a null `final_step`. L0 writes
  `loss@6` and beats 6, and nothing ends it. L1 writes `loss@8`, beats 8, and exits completed with
  `final_step: 8`. Under `first-is-exit`, `predecessor-beat` (B at 4), `final-step=finished`,
  `exitless-step=unfinished`, `valueless-gap=iteration`, `in-log`, `previous-step` and `newest-claim`:
  - A commits 0 to 4 and a valueless 5, and its value at 6 stays uncommitted;
  - B commits 5, a valueless 6, and 7, its first commit 5 with parent A's 4;
  - L0's 6 has parent B's 5, and L1's 8 has parent B's 7.

  L1's start synthesizes B's 7, and with it B's 5 and 6, so B's start reads 5. Every order of the starts
  gives this output;
- **the ambiguities:** for each, a site beside a proven site, under each built-in and under `refuse`, and a
  reading the checks refuse.

The goldens that rest on mycooc's writers (the backstop in each place, M10's body and its repair, and the
reclaim tool's stop written after the time it reports, with the 60 s boundary) run under a test strategy
that encodes that catalogue, which shows the reading types can express it. Without that strategy, the
generic step refuses those whose stops are open or malformed, and reads the rest under the built-ins.

**Figures.** translation migrates exactly. The mycooc figures in review §11.3 predate the exit node and the
stepless-heartbeat rule, so the implementation round re-measures P1–P4 from an implementation of these final
rules. That round also runs the census (the writers of lone `stopped` records, third-party stops, claims
ended only by `terminated`, null-name values), and resolves the 41 cells of run 625dda74 that were still
unattributed: each gets a named rule or a golden log, never a tolerance.

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
    lift rule counts against it. It would also have settled none of §7's fifteen ambiguities: three concern
    third parties' stops, which a tag on `started` does not name; `value-claim` asks who wrote a value, to
    which no tag on `started` attaches; `exitless-step` is an event fork that no habit settles; and the
    other ten are one consumer's loop and checkpoint habits across its own versions, which only that
    consumer's own table could map.
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
- **Migration:** the golden logs and the properties of §7, including each pass's order and every M9 refusal;
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
  mechanism (`log-formats.md` §6, "Ambiguity"); and the built-in strategies, `v0_2_0_to_v0_3_0_resolutions.py`
  and `v0_3_0_to_v0_4_0_resolutions.py`.
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
  (`reference-by-name.md` §6) and `value-claim=positional` (§7), as before. Other readings leave a contested
  value uncommitted, or refuse the run. Its figures wait on the implementation round (§7).
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
