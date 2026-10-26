# Spec: reference by name — records name what they answer

**Status:** IMPLEMENTED 2026-10-03, as log format 0.3.0 (package 0.3.0.dev0). Designed with the owner
section by section, from the ground-truth spike (2026-10-02, since retired; the measured numbers in §6
and §8 are its). It is layer 1 of
[`../backlog/identity-in-records.md`](../backlog/identity-in-records.md), and introduces **log format
0.3.0** under [`log-formats.md`](log-formats.md).

**What it gives:** the four rules that relate records by log position become joins on names. The
control-plane reads then give the same answer whatever order records arrive in. #39, the claim cascade and
the stale-beat leak are fixed, and a stop sent before any worker exists is honored exactly once. It
supersedes [`../backlog/episode-aim.md`](../backlog/episode-aim.md).

## 1. The problem

Format 0.2.0 related records by position, by design §7's rule *"a standing fact's eliminator must follow
it by `seq`."* Four rules rested on it:

| standing record | answered or ended by | the positional part |
|---|---|---|
| `control.stop` | the **next** `lifecycle.stopped` | any later `stopped` clears every pending stop |
| `control.subscribe` | a later `unsubscribe` or `nak` with its `request_id` | must *follow* it, because ids are reused as slots |
| an episode-local subscription | the **next** episode boundary | the boundary names nothing |
| an episode's terminal | stands until a later claim | "current" means no later claim |

Position is the common cause of:
- **#39:** a third party's claim release clears operator stops it never carried out (11 of 37 real stops);
- **the claim cascade:** a displaced worker's late `stopped` releases its successor's claim;
- **progress from the wrong episode:** a displaced worker's heartbeat moves `progress`, and a new episode
  reads its predecessor's beat;
- **stops cleared unseen:** a stop landing between the worker's last read and its `stopped` is discharged
  without ever being seen.

## 2. The rule

> **A record that answers, ends or concerns another names it, by an identity that is never reused.** It is
> never related to it by log position.

What stays ordered, and why:

1. **Order among claims.** The current episode is the latest claim. The claim compare-and-swap totally
   orders claims, so a claim's seq is both its name and its rank.
2. **Each writer's own order.** A re-sent request's latest schedule wins, and so does one episode's newest
   heartbeat.
3. **Windows after a claim are causal, not attribution.** A record cannot name a claim it never saw.
   Windows also keep a malformed record from a dead past out of the present.

## 3. The record rules

### Stops

- **`control.stop` requires a `request_id`.** A stop without one is refused as malformed and is never
  pending.
- **`lifecycle.stopped.honored`** is a required list of `request_id`s, empty when there are none. It holds
  every stop in the worker's pending set when it stops, due or not, matching the existing rule that a
  `stopped` clears every pending stop.
- **A stop is pending until** a `stopped` lists it, or a `nak` names its id. A refused stop is answered by
  its nak.
- **The dying breath** is a compare-and-swap over a fully read control tail. `stopped()` and `retire()`
  share the loop: read the head, take in every `control.*` record up to it, then append with
  `expected_seq`. On refusal, read again. A plain `stopped()` takes in only stops: a subscribe racing it
  is left unregistered, so it stays live for the next episode to register.

Stops are named by `request_id`, not by `seq`. A `nak` can name only by `request_id`, so with seq names a
refused stop would never be answered, breaking L2's "every standing fact has a designated eliminator".

### Subscriptions

- **A `request_id` names one request.** Re-sending it while the request is live updates that request.
- **Once any answer names an id, the id is spent.** An answer is an `unsubscribe`, a `nak`, or the
  worker's expiry record. A later subscribe reusing a spent id is dead on arrival, wherever the answer sits.
- **To replace a subscription,** use a fresh id plus an `unsubscribe` of the old one. Writing the
  `unsubscribe` first keeps the replacement crash-safe.

"Refuse reuse and nak it" is unsound: the nak names the live original too, and kills it (measured). A
`replaces` field would be `subscribe` and `unsubscribe` folded into one record, and fails Independence.

### Episode-local subscriptions

An **episode-local subscription** is one whose expiry is measured inside an episode: a `time_seconds` or
`count` atom anywhere in its schedule. They are often used as renewed keepalives, and are informally called
leases. A step-bounded subscription is not episode-local, and carries over between episodes.

- **Before registering one,** the worker writes **`lifecycle.bound`**, with `request_id` set to the
  subscription's id and a body of `{claim_seq}`.
- **It is void for every other episode,** and void for every reader once a terminal names its bound
  episode. One that no episode registered is never void.
- **One predicate decides voidness** for the worker and the observers alike.
- **The renewing-client gap is accepted, and `await_consumed` makes it visible.** Re-sending a live lease's id renews it within the episode
  that bound it. Across a crash the binding voids that id for every later episode, re-sends included, so
  a client renewing under one id is unserved until it resubscribes under a fresh id; one that renews
  under a fresh id each time is unserved until its next renewal, at most one renewal period.
  `await_consumed` raises `ValueError` for a re-send of a void lease id (one id is one lease is one
  episode), telling the same-id renewer to resubscribe under a fresh id, so the gap is never reported
  as an acceptance. A lease nobody has bound yet is not void and waits as usual. A
  client-side helper that resubscribes under a fresh id as soon as a new claim appears could shrink the
  gap; this spec notes it and does not build it.

### Which episode a record belongs to

- **`lifecycle.heartbeat.claim_seq`** is required: an integer of at least 1.
- **`lifecycle.stopped.claim_seq`** is required, and null for a run that never claimed, such as a release
  naming a stop before any worker exists.
- **Residual cases:**
  - Two terminals naming one claim, for example a third-party release plus the worker's own `stopped`:
    the newest wins, as today.
  - A lifecycle record naming nothing is malformed. The verdict read (`peek_terminal`) raises
    `MalformedRecordError`; measurement reads and the claim gate (`live_episode`) skip it.
  - A launcher death with no claim at all: the latest wins, as today.

## 4. Schemas

| schema | change |
|---|---|
| `lifecycle-v0.5` | `Heartbeat.claim_seq` (required, integer ≥ 1). `Stopped.claim_seq` (required, nullable) and `Stopped.honored` (required, array of unique strings). New topic `lifecycle.bound`: body `{claim_seq}`, with the envelope `request_id` required. |
| `subscription-v0.3` | `request_id` is required on `control.stop`. |

The envelope, launcher and value schemas are unchanged. `additionalProperties: false` stays everywhere.
The closed topic set and the public API gain `LIFECYCLE_BOUND` and a `Bound` payload. All of this is log
format **0.3.0**, with the package bumped to 0.3.0.dev0 in the same commit.

## 5. The reads

**The pure reads in `observables.py` follow names.** `live_episode`, `progress`, `peek_terminal` (through
the verdict record), `live_demand` and `undischarged_stops` read the records that name the current claim.
So do the Watcher's heartbeat-staleness credit and cold-attach seed, and `await_consumed`'s watermark.
Two helpers carry the lookups:

- **The current heartbeat:** the newest beat naming the current claim. It searches newest-first, and on a
  miss searches backward in a window that grows ×4 each time.
- **A claim's terminal:** the `stopped` naming it. It is strict for the verdict (`peek_terminal`), and
  tolerant for measurement reads and, deliberately, for the claim gate (`live_episode`): a record naming
  nothing is no evidence that the claim ended.

`last_activity` and the value plane are unchanged; the value plane is layer 2.

**Pending stops have a pure form and an incremental form.**

- **The pure fold is the definition:** stops no `stopped.honored` and no `nak` names. It reads every stop,
  `stopped` and nak.
- **`Watcher.pending_stops(run_id)` is the incremental form.**
  - Its state is a small object on the run's Watcher entry: the unanswered stops by id, the **spent ids**
    (every id an answer has named), and its own read cursor. The cursor is separate from the event cursor
    that `iter_events` callers advance.
  - Each call reads only the three relevant topics after the cursor:
    - a stop is added, unless its id is spent, in which case it is dead on arrival;
    - a `stopped` or nak marks the ids it names as spent and removes them.
  - Keeping the spent ids is what makes it match the pure fold exactly, which treats an answered id as
    spent wherever the answer sits. Memory grows by one short id per answered stop; time per call stays
    proportional to the new records. Because answers are remembered, an answer that arrives before its
    stop is also handled, so the form does not depend on log order.
  - The cursor starts at 0, so the first call computes the pure fold and no separate seed is needed.

**Errors.** Opening a log can raise the `LogFormatError`s of `log-formats.md` §4, or `RunNotFound`. On the
verdict plane an unnamed lifecycle record raises the existing `MalformedRecordError`.

## 6. Migration: format 0.2.0 → 0.3.0

`runstate/migrations/v0_2_0_to_v0_3_0.py`, under `log-formats.md` §6. It reads
`<root>/v0.2.0/<rid>.db` and writes `<root>/v0.3.0/<rid>.db`, **preserving every record's seq**, because
`claim_seq` values point at seqs. It deletes nothing, and appends only `lifecycle.bound` records, after the
last record. Where 0.2.0's conventions settle a name, it writes exactly what the positional rule said; the
step's docstring is the precise statement of the rules. Where the positional rule answers a question the
0.2.0 log leaves open, the answer is the reading of one of the step's declared projections, whose chooser a
user names explicitly (below, "Ambiguities").

- **Stops.** A nameless stop gets the deterministic id `stop@<seq>`. A stop keeps its id only if no other
  control request bears it, unsubscribes aside: it is the only stop with that id, and no subscription or
  unknown verb bears it. Otherwise every stop with that id gets a minted id, `<id>#<seq>`. Positionally
  each stop record was its own request, while in 0.3.0 the requests of one id are one request, which
  every nak bearing that id answers. Each `stopped.honored` lists the stops that `stopped` cleared under
  the old rule: every stop since the previous `stopped`, blind to both author and body (`stop-honored`'s
  `positional`, below).
- **Episodes.** The `claim_seq` of a heartbeat or a `stopped` is the latest claim before it
  (`record-claim`'s `positional`, below). A `stopped` before any claim names none (null).
- **Subscriptions.** A reused id is split into segments at each answer, and the later segments become
  `<id>#k`, along with their answers and value sends. An answer that precedes every subscribe of its id
  answered nothing, so it is renamed to name nothing: an `unsubscribe` always, and a nak unless no
  subscription bears its id and a same-id stop precedes it. A nak with no same-id stop before it names
  nothing either, since a nak cannot refuse a stop that comes after it. A nak that keeps its id names
  its stop.
- **Episode-local subscriptions.** One `lifecycle.bound` for each unanswered one that a claim follows,
  naming the first claim after its latest send (`lease-bound`'s `first-claim`, below). Positionally a
  lease was void for a drainer when a claim lay strictly between the lease and the drainer's own claim, so
  once any claim follows it, every later claim finds that one in between: the first claim after it was its
  last drainer. That holds when the first claim is the latest one too, and then the next worker, which
  claims after migration, is the drainer the binding voids it for. *Found in the final review* (Ruling
  17): binding only to a claim strictly before the latest one left a lease served by an ended latest
  episode unbound, and the next 0.3.0 worker served it again with a fresh `time_seconds`, which neither
  master nor a native 0.3.0 log does.
- **Every minted name is fresh:** it is checked against every request id in the run and every name minted
  before it.
- **Refused: a heartbeat before any claim.** `Heartbeat.claim_seq` is a required integer, so format 0.3.0
  cannot hold such a beat, and leaving it unnamed would put a 0.2.0 record in a 0.3.0 log
  (`log-formats.md` §2, rule 5). The step refuses the run, naming the heartbeat's seq. It never drops
  the record.
- **Refusals come before sealing.** A refusal is deterministic, so the runner meets it on a dry run of
  the transform before the seal (`log-formats.md` §6), and the refused run stays writable and
  unmigrated.
- **The step carries its own copy of the 0.2.0 semantics.** It is retained forever, so it must read 0.2.0
  and write 0.3.0 as they were defined when it was written, whatever later code does. It imports no
  record semantics from the package (not `Envelope`, not `Topic`, not the schedule predicates): only
  `Row`, `MigrationError`, and `resolve()`, an OS probe of a handle rather than a format rule. A test
  pins the import list.
- **Liveness.** To refuse a run with a live episode, the step reads liveness as 0.2.0 did: the latest
  claim is live unless a `stopped` follows it or its handle resolves dead, and an unresolvable foreign
  handle reads as live.

**Ambiguities** (*specified 2026-10-24, encoded 2026-10-26*; review §12, item 16; `log-formats.md` §6,
"Ambiguity"). The step judges against any writer conforming to format 0.2.0, whose records named nothing
and were related by position (§1's table). Three of its answers are inferences that 0.2.0's records do not
settle. They are its declared projections, asked in the table's order, and the positional rule is one
reading of each, whose chooser a user must name. The step states no facts (`NO_FACTS`, the plan's Task
21): each fork is settled by a chooser, and `record-claim`'s `positional` is itself the user's statement
about their own orchestration (below). The built-in choosers are in the step's module,
`v0_2_0_to_v0_3_0.py`. The step is shipped, and no consumer has run it yet, so each consumer's first
migration is its first choice. Each site test reads only the 0.2.0 log; a chooser is asked once per run,
with every fork of its projection and the readings given before it.

| projection | the question | built-ins | the step's reading today |
|---|---|---|---|
| `record-claim` | which claim a heartbeat or `stopped` speaks for | `positional`, `refuse` | `positional` |
| `stop-honored` | which stops a `stopped` answered | `positional`, `drained`, `none`, `refuse` | `positional` |
| `lease-bound` | which claim registered an episode-local subscription | `first-claim`, `unbound`, `refuse` | `first-claim` |

What the site tests rely on as settled by 0.2.0's conventions:
- **`completed: true` is the worker's own claim** (`design-v0.2.md` §7: "`completed=True` is the worker's
  opt-in claim of intrinsic, permanent completion"; `completed-opt-in.md`), and **a worker's `stopped` is its
  halt** (§7: "Existence = a clean, resumable halt"), so a claim a completed `stopped` ended was ended by its
  worker's own halt, after which that worker beat no more and wrote no second `stopped`;
- **a `launcher.terminated` follows its process's end**, since only a `wait()`ing parent writes it
  (`design-v0.2.md` §8), and the launch id a claim's `started` re-emits, with an equal handle, makes that
  process the claimant;
- **a claim is taken only when the latest one is followed by a `stopped` or its worker resolves dead**: "the
  single-claim guards (run-episodes self-claim, §12.1) rest on" the claim CAS (`design-v0.2.md` §4), and "the
  lazy-launch race is arbitrated by the worker's birth-CAS" (§8); `run-episodes.md` §3 spells the guard out.
  So a claim that no `stopped` followed was dead when the next one began.

The entries:
- **`record-claim`: which claim a heartbeat or `stopped` speaks for.**
  - **Site:** a heartbeat or `stopped` after the `started` of a claim that is not the run's first, when an
    earlier claim was followed, before that `started`, by a `stopped` that is not `completed: true`, and no
    `launcher.terminated` of that earlier claim's launch, carrying its handle, precedes the record. Such a
    `stopped` let a later claim begin, and a third party may have written it, so the earlier claim's
    worker may have outlived it.
  - **H1:** the latest claim's worker wrote the record, which needs the latest claim before it.
  - **H2:** a third party's `stopped` released the earlier claim while its worker lived, and that displaced
    worker wrote the record after the later claim began, which needs the earlier claim.
  - **Built-ins:** `positional`, the latest claim before it, which is the step's rule today; `refuse`.
  - **Refuse-only:** no, as a declaration. Nothing in the log says who wrote the record, so no reading
    computed from it is right for most histories. `positional` is the user's statement about their own
    orchestration: no worker outlived a release of its claim.
- **`stop-honored`: which stops a `stopped` answered.**
  - **Site:** a `stopped` with at least one `control.stop` since the previous `stopped`. 0.2.0's positional
    rule cleared them all, whoever wrote the `stopped`, while 0.3.0's `honored` names the stops its writer
    carried out.
  - **H1:** the worker drained stop x at its last tick and stopped on it, which needs `honored: [x]`.
  - **H2:** a third party released a dead worker's claim, or the worker stopped at a bound of its own while x
    landed unseen, which needs `honored: []`, with x pending for the next claim, as §7 has mycooc's reclaim
    tool leave it.
  - **Built-ins:**
    - `positional`: every stop since the previous `stopped`, which is the step's rule today;
    - `drained`: those that the newest heartbeat of its claim before it had consumed, its `consumed_seq` at
      or above the stop's seq: what a 0.2.0 reference worker held when it stopped. It reads `record-claim`'s
      readings;
    - `none`: no stop, so each stays pending;
    - `refuse`.
  - **Refuse-only:** no.
- **`lease-bound`: which claim registered an episode-local subscription.**
  - **Site:** an unanswered episode-local subscription that a claim follows, when the first claim after its
    latest send wrote no heartbeat whose `consumed_seq` reaches that send. A claim whose heartbeat reaches
    it drained it, and so registered it: the watermark passes a request only once it is registered or
    refused (`design-v0.2.md` §6), and an unanswered one was not refused. There the binding to that claim is
    the proven reading.
  - **H1:** the first claim drained and registered the lease, then died before that tick's heartbeat, which
    needs the lease bound to that claim, as 0.2.0's positional void treated it.
  - **H2:** the first claim died before it drained the lease, which needs the lease unbound: a `bound`
    asserts that its claim registered the lease (lifecycle-v0.5's `Bound`), and here none did, which the
    0.2.0 log cannot record. The next claim then registers and serves it.
  - **Built-ins:**
    - `first-claim`: the first claim after its latest send, which reproduces 0.2.0's positional void. It is
      the step's rule today, and Ruling 17's;
    - `unbound`: no `bound` record, so the lease is void for no episode;
    - `refuse`.
  - **Refuse-only:** no. The real corpus has no subscribes, so no consumer's log has a site.

**Carried unread.** A `stopped` whose body 0.2.0 does not define is carried with its body unread, the step
adding only the names its rules write: the step relates it by position and never reads its body, so it
declares no projection for it. The 0.3.0 → 0.4.0 step, which reads it, declares `malformed-stop`
(`commits-and-lineage.md` §7; `log-formats.md` §6). Since the step does not read it, a malformed `stopped`
proves nothing in a site test: in `record-claim`'s, it counts as a `stopped` that is not `completed: true`,
the way that makes the site wider.

**Determined, not declared,** because 0.2.0's own rules settle them, and the step reproduces those rules:
- a `stopped` before any claim names none;
- a stop's id, kept or minted, since each 0.2.0 stop record was its own request;
- a reused subscription id split at each answer, and an answer before every subscribe of its id renamed to
  name nothing, since 0.2.0 paired an answer with the request before it;
- a nak that keeps its id names its stop, since a nak is the refusal of the request its id names.

A heartbeat before any claim stays a refusal.

**Measured on the spike, on copies of 2,569 real consumer logs:** identical reads on 2,562. The 7
differences are all one positional bug that names fix: `progress` reading a previous episode's heartbeat.
The step's golden logs (`tests/test_migration_v0_2_0_to_v0_3_0.py`), migrated under the readings marked
"today" above, read as 0.2.0's positional folds read them (computed at `72d9c3f`), except in three classes,
each a positional defect that names fix:

1. that **stale-beat leak**;
2. a **naked stop**, which `undischarged_stops` kept listing until the next `stopped` and which its nak
   now answers;
3. an **ended lease**: a lease whose first claim after it is the latest claim, and a terminal names that
   claim. Master's `live_demand` listed it, though no 0.2.0 worker would serve it, which cost a ghost
   launch. Bound to that claim, it is void for every reader once a terminal names the claim.

A worker resumed on a migrated log serves exactly the demand master's next worker would: a test resumes
one on the golden logs. The final review's fuzz (7,000 synthetic logs, master's reads computed at
`72d9c3f`) found no difference outside the three classes, and none in the demand a resumed worker faces.

**Divergences** (`log-formats.md` §6; *declared 2026-10-26*). Each class above is a divergence, a rule under
which a read of the migrated log answers differently from the same read of the input log, and so is each
difference a reading other than the positional one makes. The step declares five, and finds each by
computing both reads, its own copies of 0.2.0's positional folds (`72d9c3f`) on the input and 0.3.0's
by-name folds on the output: `progress`, `undischarged_stops` and `live_demand`, the only reads its rules
change. A run where one fires is refused until the user names it with `--accept`.

| divergence | the read it changes, and how | fires |
|---|---|---|
| `stale-beat` | `progress` reads only the records that name the latest claim | the stale-beat leak, under any reading |
| `naked-stop` | `undischarged_stops` no longer lists a stop its own nak answered | a naked stop, under any reading |
| `ended-lease` | `live_demand` no longer lists a lease bound to the latest claim once a terminal names it | an ended lease, under `first-claim` or a proven binding |
| `unhonored-stop` | `undischarged_stops` lists a stop a later `stopped` cleared positionally | under `stop-honored`'s `drained` or `none` |
| `unbound-lease` | `live_demand` lists a lease a later claim voided positionally | under `lease-bound`'s `unbound`, where a second claim follows the lease |

Each is a direction of one read: a stop or a lease that only the input's read lists is `naked-stop` or
`ended-lease`, and one that only the output's lists is `unhonored-stop` or `unbound-lease`. A test checks
every golden log, under four sets of readings, against master's reads and runstate's: each divergence fires
exactly where its read answers differently, and nothing else differs. `peek_terminal` and `live_episode`
answer alike under every reading, since `record-claim`'s built-in is the positional one.

**Known limits:**
- Under `record-claim`'s `positional`, a historical misattribution is copied faithfully, not corrected:
  episode-aim's objection, stated in the step's docstring. A displaced worker's late heartbeat or `stopped`
  is named for its successor's claim, because the positional rule attributed it there, and the log does not
  record which worker wrote it.
- Under `lease-bound`'s `first-claim`, the `bound` records are inferred from the positional rule. The 0.2.0
  log never recorded which episode registered a lease.
- A nak bearing a uniquely named stop's id, after that stop, is presumed to be that stop's refusal. The
  reference worker naks only the request it is handling, so only a nak it did not write (a third
  party's, or another worker's) could make that wrong: a forged nak.
- The real corpus has no subscribes, naks or unsubscribes, so those parts are tested only on synthetic
  logs.

## 7. A consumer's upgrade

At the consumer's own time, in this order:

1. Stop its old processes and drain its queued jobs (`log-formats.md` §8).
2. Bump its pin to the commit that introduces format 0.3.0.
3. Onboard: move its legacy logs into `v0.2.0/`, as the `LogFormatMissing` message instructs.
4. Migrate a **copy** first and compare its reads, then run `runstate migrate <root>`, with a `--resolve`
   for each projection its detection pass reports, and an `--accept` for each divergence it reports (§6,
   "Ambiguities" and "Divergences"; `log-formats.md` §6).
5. Change its code:
   - mint a `request_id` on every `control.stop` (none of the corpus's 184 stops has one). Grep its
     code for every send of `control.stop` (`Topic.CONTROL_STOP`) and check each carries a
     `request_id`: one that does not is malformed under subscription-v0.3, so after the upgrade the
     worker naks it and runs on, and it stops nothing;
   - drop the `request_id=` argument from every `await_consumed` call: the id is now the one the
     request's own record bears;
   - mycooc's `resume_fanout`: the claimless release names the stops it clears (`claim_seq: null`,
     `honored: [...]`);
   - mycooc's reclaim tool names the claim it releases, with `honored: []`, so it no longer clears pending
     stops;
   - anything that builds `{rid}.db` paths by hand goes through the locator;
   - **mycooc: tick on every step.** `training.py`'s `on_step` ticks at its end, and three
     "patience exhausted" branches return early, before the tick (`training.py:1256`, `:1287`, `:1313`).
     - **What it costs today.** At those steps the step's metrics land with no heartbeat, the control drain
       is skipped (so a stop takes effect one step late), and liveness has a one-step gap.
     - **How often** (2026-10-04 census): 891 times, every one a +2 heartbeat jump with a full metric set at
       the skipped step.
     - **Why it matters more under layer 2.** Commits make the skip *visible*: the skipped step's values
       would be committed one step late ([`../backlog/identity-in-records.md`](../backlog/identity-in-records.md) §2).
     - **The fix:** move the tick ahead of the early returns, or into a `finally`.

## 8. Measured cost (spike)

- **Bytes:** corpus +3.5% (heartbeat 51.5 → 65.6 B, stopped 120 → 149 B).
- **Real-log reads:** 7–32% slower, all at most 3 µs.
- **`undischarged_stops` (pure):** the one scaling cost. About 1.2 ms with 400 stops and 7.5–10 ms with
  2,000 stops, against about 10 µs on master. The Watcher's incremental form exists for long runs that
  poll it.
- **`live_demand`:** faster on long runs, because it no longer reads claims (2,783 → 14 µs at 1,000
  episodes on SQLite).

## 9. Tests

- **Rewrite the 36 tests the spike left failing on purpose.** 24 encode the v0.4 record shapes, or build
  fixtures the new model cannot express (a heartbeat with no claim). 12 pin positional semantics; each
  gets a replacement under names, not just a deletion.
- **Port the spike's 19 scenario tests,** on all four backends:
  - #39, a stop before any worker exists, and the claimless release that names it;
  - a stop after the last read, and a stop racing the dying breath;
  - the cascade, and a displaced worker's heartbeat;
  - two terminals naming one claim, and a spent subscription id.
- **Order independence, as a property test.** Random histories through the real Worker, reordered under
  the causal model: each writer's own order, the order among claims, "a record follows what it names",
  and "a launch's death follows its claim". The named reads must not change. The default run uses a
  fixed seed and is sized to add at most about a second to the suite. An environment variable selects the
  spike's full run of 2,000 histories × 40 orderings.
- **Incremental equals pure** for `Watcher.pending_stops`, after every step, on random histories with
  stops sent while the run is down, refusals, third-party releases and many episodes.
- **The migration step:** golden 0.2.0 logs migrate to the expected 0.3.0 reads, including the synthetic
  subscription cases.
- **Schema conformance** extended to lifecycle-v0.5 and subscription-v0.3.

## 10. Docs ripple

- `design-v0.2.md` §7: "an eliminator follows by `seq`" becomes §2's rule. §10: the schema versions.
- `stop-discharge.md`: the discharge names its stops, and the dying breath is a compare-and-swap over the
  read tail.
- `service-worker.md`: the answer fold, and spent ids.
- `time-lease-boundary.md`: the recordless boundary void becomes `lifecycle.bound`.
- `observables.md`, `api.md`, `implementers-guide.md`, and `CLAUDE.md`'s architecture notes.
- Backlog:
  - `identity-in-records` layer 1: specified;
  - the `discharge-by-id` entry in `index.md`: realised;
  - `protocol-algebra` L2's list of positional rules.

## 11. Order of work

Two stages, one release:

1. **[`log-formats.md`](log-formats.md),** with today's format named `0.2.0`: versioned addresses, the
   opening checks, sealing and `runstate migrate`. Testable on its own.
2. **This spec,** with its migration step. The package bump to 0.3.0 and `LOG_FORMAT = "0.3.0"` land in
   this stage's final commit.

The consumers stay pinned throughout, and upgrade at their own time (§7).

## 12. Not in scope

- **Forgery and authority.** A forger names a claim or a stop as easily as before.
- **Names on values** (layer 2), episode-keyed artefacts (layer 3), time-triggered claims (layer 4) and
  fenced writes (layer 5).
- **`last_activity`.**
- **Multi-home claims.** A claim's name is the arbiter's seq, so order among claims still needs one
  arbiter. Stops and subscriptions become portable; episodes do not.

## Related

- [`log-formats.md`](log-formats.md) — how format 0.3.0 is introduced and migrated to.
- [`../backlog/identity-in-records.md`](../backlog/identity-in-records.md) — the layered design.
- [`stop-discharge.md`](stop-discharge.md), [`service-worker.md`](service-worker.md),
  [`time-lease-boundary.md`](time-lease-boundary.md) — the specs this rewrites.
- [`../backlog/episode-aim.md`](../backlog/episode-aim.md) — superseded by this spec.
