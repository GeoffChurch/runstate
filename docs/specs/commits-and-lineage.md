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
  `resume_from`, an extendable node of this run, or null for a fresh start. It must, or a resumed episode
  silently becomes a fresh root.
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
| `lifecycle.stopped` | `{completed, error, claim_seq, honored, parent, values, fired, expired, t}` | `final_step` removed; `parent`, `values`, `fired`, `expired` added; `claim_seq` no longer nullable; the exit node |
| `lifecycle.bound`, `lifecycle.nak` | unchanged | — |

**The envelope:** every lifecycle record carries `name: null`. `request_id` is the launch id on `started`, as
in v0.5; the request on `bound` and `nak`, as in v0.5; and null on `commit`, `heartbeat`, `rewind` and
`stopped`.

**The fields.** Constraints a schema cannot check are marked *prose*.
- **`resume_from`** (on `started` and `rewind`): the seq of an extendable node of this run, or null: a fresh
  start on `started`, the root on `rewind`. *Prose:* it names an extendable node below its record's own seq.
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
- **An exit that discarded its iteration evaluates nothing:** `values: null` implies `fired: []` and `expired:
  []`, as an `if`/`then`, whether or not it completed.
- **`consumed_seq`** is the control-drain watermark, as in v0.5. No 0.4.0 read uses it except
  `await_consumed` (§5), which takes it from the claim's commits and heartbeats.
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
- **`outcome`** is `preempted`, `errored`, `killed` or `lost`. `lost` says that its writer saw the claim's
  process gone without an exit, and nothing of how (review §12, item 14). A death is never a completion.
- **`evidence`** is a non-empty string that no fold parses: for example `slurm:TIMEOUT`.
- **`t`** is the reporter's clock when it saw the death, as on `terminated`.
- **Unchanged:** the Python `Launcher` classes keep their names, since they launch, and so does the launch id
  (`RUNSTATE_LAUNCH_ID`).

**Who writes a death report** (review §12, items 13 and 14). Writing one is an act, not a kind of party: any
party, whatever else it does, an observer included, may write one for a death it holds evidence of.
- **Evidence, never inference.** A reap (the exit code or signal), a same-host probe that finds the claim's
  process gone, and an accounting record are evidence. Staleness, silence and a cross-host absence are not:
  a slow worker is alive, and a report would fence it (§4), turning a slow run into a dead one.
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
  and its handle is on this host and probes dead, it reports `lost` through `report_death`, with the handle
  as the evidence, and returns the report's seq; otherwise it returns `None`.

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
- **The outcomes** (review §12, item 14), with `signal s`, `exit n` or the exception as the evidence:
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
  - **an explicit `None`,** for a fresh start.

  The Worker validates the node with one point read before the claim CAS: an extendable node of this run, at
  or below W. That covers `steps()`, `serve()` and a worker that calls `commit()` itself. It exposes the node
  as `w.resume_node`, `None` for a fresh start, which a rewind moves to its target (§6's load assertion).
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
handles nothing (below). A drain that registers a lease writes each `lifecycle.bound` by CAS at the tail it
last read, its own writes included, and drains again on a lost CAS (review §12, item 14), so a death report
that lands first fences the worker before it binds anything. Without the CAS, a binding could land after a
report the worker has not read and void the request for good, where a racing commit costs one iteration.

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
  binds a lease, and no longer writes checkpoints beside it. A mistaken release costs at most the iteration
  in flight.
- **The race:** a death report that lands before the exit's CAS or a binding's CAS moves W, and the retry's
  drain sees it; a commit racing it may land, which is that one iteration.

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

`runstate/migrations/v0_3_0_to_v0_4_0.py`, under `log-formats.md` §6. Every inference copies what the old
positional reading implied, the standard of the 0.2.0 → 0.3.0 step. An inference is labelled as one.

**Records:**
- **A 0.3.0 heartbeat with an integer step** becomes a `lifecycle.commit`.
- **A stepless 0.3.0 heartbeat** becomes a `lifecycle.heartbeat`, without `step`. mycooc's 1,114 all sit in
  its 150 stepless-only claims, after only `status` values: they were beats.
- **A worker's 0.3.0 `stopped`** becomes the exit node:
  - `parent` is the claim's position: its newest commit, else its resume point;
  - `values` are `[]`, or null on an error exit. M2 and M5 commit every integer-step value of the claim at or
    below the exit's last step, except an error exit's trailing step that M5's third case does not reach; a
    value above it, which pass 1 gave the claim from a displaced worker, stays uncommitted, and stepless
    values stay outside every lineage (M3);
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
   successor, as the old read did.
2. **Classification** of every `stopped`: a worker's exit or a third party's (below).
3. **Synthesis:** M2 and M5 assign values to commits, synthesizing the commits M5 names.
4. **M1:** parents and `resume_from`.
5. **M6:** the moves, then renumbering.
6. **M7 and M8:** the renames and the synthetic fields.

**The rules** (review §5, §11.3 and §12 items 0.13–0.21, 10, 13 and 14):
- **M1. Parent.**
  - A claim's later commits take the claim's previous commit.
  - A claim's first commit, with old step s, takes, **in claim order**, the newest earlier claim holding a
    commit with old step s−1, and that claim's last such commit. With no such claim, the parent is null: a
    parentless resume. A commit is the parent even where an extendable exit follows it (review §12, item 14):
    no 0.3.0 log saved a checkpoint after its exit, and a native log has the same shape when a claim resumes
    from a commit's checkpoint.
  - The claim's `started.resume_from` is its first commit's parent.
  - **A claim with no integer-step heartbeat** takes as its `resume_from` the newest commit of the newest
    earlier claim holding one, labelled as an inference. With null instead, 7 runs read empty (8,449 cells):
    a retry that crashed while loading is recorded as a fresh start, and the generic migration uses the log
    alone.
- **M2. The step label wins.** A value with an integer step is committed by its claim's commit carrying that
  old step, even when it also follows the claim's last heartbeat.
- **M3. Dropped** (review §12, item 13). Stepless values are registers (`config`, `completion_reason`,
  `status` and `input_provenance`) and stay outside every lineage, as an upgraded consumer leaves them.
- **M4. Values after their claim's `stopped` stay uncommitted.** There are 1,886 of them, all teardown
  `status`.
- **M5. A step commit is synthesized in exactly three places:**
  - for a tick skipped between two real beats of one claim, even one with no values: `Commits()` must count
    it;
  - for a trailing step at or below a non-error exit's last step. This takes precedence over the exit node,
    which holds only what no commit claims;
  - for a trailing step s, whatever the exit, in the claim M1 will choose for a later claim whose first real
    beat is s+1 (review §12, item 14): the newest claim before it, in claim order, to reach step s, by a
    commit or by trailing values. A claim that starts at s+1 says step s finished in the claim it resumed
    from, and M1 then finds the commit the old read showed. The later claim is any, not only the next, since
    a retry that crashed while loading beats nothing. A trailing step that no such claim needs stays
    uncommitted: nothing says its iteration finished.

  Leading values, below a claim's first real beat, go to its first commit.
- **M6. Move each commit,** in its claim's order, to just after the latest of: its last committed value, its
  parent, and the record its `consumed_seq` names. Then renumber. A commit asserts that its values are
  complete, which holds only once the last of them exists, so moving the commit keeps every record at an
  honest position; moving the values would place them before a beat that preceded their computation.
- **M7. The rename list is derived from the schemas:** `claim_seq` (a `died`'s included), `consumed_seq`,
  `parent`, `values`, `resume_from`, and `bound`'s `claim_seq`. `stop@N` request ids stay as they are, since
  they are strings.
- **M8. A synthetic commit** takes `t` from the last value it commits, else from its claim's previous real
  heartbeat; `consumed_seq` from that heartbeat, else 0, the watermark of a claim that witnessed no drain;
  and `fired: []` and `expired: []`.
- **M9. Refusals,** each before sealing, naming the run and the seq:
  - any subscription, with its samples, and any `control.unsubscribe`;
  - a `control.stop` whose `from` holds a step atom;
  - two heartbeats of one step in one claim, an in-claim rewind;
  - a value with a null `name`, which value-v0.3 cannot hold.

  The corpus holds none of the first three; the implementation round counts the fourth. A consumer that
  later holds such logs revives the question with that log in hand.
- **M10. A malformed `stopped`** (the reclaim tool's 7 `{reason, honored, claim_seq}` bodies) becomes
  `supervisor.died {claim_seq, outcome: preempted, evidence: <its reason>, t}`: a third party's release, with
  the outcome the tool argues for in its own comments (review §12, item 14). Its `honored` ids become
  withdrawals, as below.
- **Third-party stops are reclassified** (review §12, items 5, 10 and 14):
  - **the first `stopped` naming a claim,** when well-formed, is the claim's exit, as the old read implied,
    unless the rule for lone stops below classifies it otherwise. A third party's becomes a
    `supervisor.died`;
  - **a later `stopped`,** after the first naming the same claim (a malformed one counts), is a third
    party's. It becomes a `supervisor.died` only when its `error` is set, and otherwise only its
    withdrawals: the claim has ended, and after that only a report with a cause is written (§3). So the
    repair script's well-formed copy of a malformed release writes no death, and neither does the "clear
    staged stop" path's default body;
  - **the runner's hard-crash backstop** is a third party's wherever it falls, by its error text,
    "subprocess exited N with no terminal record", and always becomes a `supervisor.died`;
  - **the mapping** of a `stopped` to a `died`: `outcome` is `errored` when `error` is set and `preempted`
    otherwise, a `completed: true` one included, since a death is never a completion; `evidence` is the
    `error` text, else `"migrated: third-party stopped"`; `t` is carried. Whatever a third party's `stopped`
    becomes, each id in its `honored` becomes a `control.withdraw` placed after it, so the stops stay spent;
  - **a `stopped` naming no claim** becomes one `control.withdraw` per stop it honoured, and nothing when it
    honoured none. A run whose only terminal was one loses its verdict, which P4 attributes;
  - **lone stops:** the implementation round's census of who wrote them (review §12, item 10(d)) yields a rule
    that reads the log alone, as M1's does, and the migration encodes it. A stop it attributes to a third
    party becomes a `died`, mapped as above. One it cannot attribute stays a self-report with `values: null`:
    it never vouches for a possibly torn iteration, and errs towards committing too little, as M4 does.
- **Attributed death reports** (review §12, items 13 and 14). A claim that 0.3.0 saw ended only by its
  launch's `launcher.terminated` gets a `supervisor.died` from it, placed just after it, when the launch's
  handle is the claim's: the same `local://host/pid`, compared whole, since two hosts can share a pid. A
  ThreadLauncher's launch carries its own process's handle, which is its worker's, so it matches, as the
  live rule attributes it by launch id. The report mirrors the live reap: a signal maps to `killed`, a
  nonzero exit code to `errored` and an exit 0 to `lost`, with `signal s` or `exit n` as the evidence and the
  record's `t`. The `terminated` is a reap, which is evidence; the migration only attributes it to the
  claim, as a live launcher does when it reaps. A launch whose handle differs (a wrapper) gets none; its
  run's verdict changes from the wrapper's exit to none, a correction, which P4 attributes. A claim released
  only by a `resolve()`-dead handle needs nothing: a same-host caller of `report_if_dead` reports it (§3).

**The only renumbering step.** 0.2.0 → 0.3.0 only appended, so this is the only step that renumbers, and the
last that may: from 0.4.0 on, checkpoints hold seqs. The runner **enforces** it: every later step must keep
each input record's seq and topic. Appending instead of renumbering was rejected: it would add about 1.23M
records and leave shapes no 0.4.0 writer produces.

**The properties to test:**
- **P1:** for every 0.3.0 heartbeat with integer step s whose commit's chain M1 linked back to old step 0,
  `count(Commits(), its commit) = s + 1`. A parentless resume is counted, and named.
- **P2:** every value with a step is committed by its claim's commit carrying that step, except leading
  values, by design, and two kinds that stay uncommitted: values above their exit's last step, and an error
  exit's trailing step that M5's third case does not reach.
- **P3:** reads equal the old reads **cell by cell, by attribution.** Every differing cell must trace to a
  named rule: cells beyond the head's lineage, steps the lineage revisited, nodes holding several values,
  loading cells, M4, error exits, a displaced worker's values above its successor's exit, a parentless resume.
  An unattributed cell fails the test.
- **P4:** the verdict's outcomes, 0.3.0's one outcome taken as a one-element set, and the stop fold's ids
  (`peek_terminal`, `undischarged_stops`) are equal across the migration, except where a named rule changes
  them: a reclassified third-party stop, an attributed death report (an exit 0 changes `completed` to
  `lost`), a wrapper's `terminated`, a claimless `stopped` that was a run's only terminal.
- **Also:** 0 commits name a later value, 0 parents follow their child, 0 commits precede the record their
  `consumed_seq` names, and every migrated record conforms to the 0.4.0 schemas.

**Golden logs:** the displacement-parent history; a displaced worker's beats, which keep their claim; a
step-0 loading `status`; a skipped tick, with and without values; post-loop values; a trailing step before a
non-error exit (M5's second case); M5's third case past a retry that crashed while loading, and beside a
newer claim that reached the step by a real beat; values after `stopped`; errored and preempted exits; a
malformed `stopped`, and its repair; a stepless heartbeat; a commitless claim; a parentless resume; a claim
ended only by `terminated`, with an equal handle, a differing one and a pid shared across hosts; and one log
per class of third-party stop.

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
- **Emit before you commit.** A value emitted after the commit lands in the next iteration's node. With the
  checkpoint recipe it is worse: node s is saved before step s's metrics exist, and a crash loses them.
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
- a worker that reads a death report naming its claim writes nothing more, and binds a lease only by CAS;
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
  - `report_if_dead` reports a held claim's same-host dead handle as `lost`, and nothing once the claim has
    ended; the claim path, `relaunch_if_needed` and `ensure_served` call it before acting, and `ensure` on
    each poll, its own drive's or a foreign one; `Watcher.poll` and `is_alive()` write nothing, but for a
    Watcher's reap of a handle it holds, which writes through the launcher's reap;
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
- **`migrations/`:** the new step in `STEPS`, and the renumbering check.
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
  that hold the evidence report it: a claimant on that host, a launcher's reap, a reclaim tool. Whether a
  claimant may also report from Postgres's `EpisodeProbe`, cross-host evidence of a lost session, is open.
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
- **The migration copies a historical misattribution faithfully,** as before, and its figures wait on the
  implementation round (§7).
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
