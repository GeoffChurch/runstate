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
- **The renewing-client gap is accepted.** Re-sending a live lease's id renews it within the episode
  that bound it. Across a crash the binding voids that id for every later episode, re-sends included, so
  a client renewing under one id is unserved until it resubscribes under a fresh id; one that renews
  under a fresh id each time is unserved until its next renewal, at most one renewal period. A
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
last record. Every name it writes is exactly what the positional rule said; the step's docstring is the
precise statement of the rules.

- **Stops.** A nameless stop gets the deterministic id `stop@<seq>`. A stop keeps its id only if no other
  control request bears it, unsubscribes aside: it is the only stop with that id, and no subscription or
  unknown verb bears it. Otherwise every stop with that id gets a minted id, `<id>#<seq>`. Positionally
  each stop record was its own request, while in 0.3.0 the requests of one id are one request, which
  every nak bearing that id answers. Each `stopped.honored` lists the stops that `stopped` cleared under
  the old rule: every stop since the previous `stopped`, blind to both author and body.
- **Episodes.** The `claim_seq` of a heartbeat or a `stopped` is the latest claim before it. A `stopped`
  before any claim names none (null).
- **Subscriptions.** A reused id is split into segments at each answer, and the later segments become
  `<id>#k`, along with their answers and value sends. An answer that precedes every subscribe of its id
  answered nothing, so it is renamed to name nothing: an `unsubscribe` always, and a nak unless no
  subscription bears its id and a same-id stop precedes it. A nak with no same-id stop before it names
  nothing either, since a nak cannot refuse a stop that comes after it. A nak that keeps its id names
  its stop.
- **Episode-local subscriptions.** One `lifecycle.bound` for each one the positional boundary rule
  voided, naming the first claim between it and the latest claim.
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

**Measured on the spike, on copies of 2,569 real consumer logs:** identical reads on 2,562. The 7
differences are all one positional bug that names fix: `progress` reading a previous episode's heartbeat.
The step's golden logs (`tests/test_migration_v0_2_0_to_v0_3_0.py`) read as 0.2.0's positional folds
read them (computed at `72d9c3f`), except in two classes, both positional defects that names fix: that
stale-beat leak, and a **naked stop**, which `undischarged_stops` kept listing until the next `stopped`
and which its nak now answers.

**Known limits:**
- A historical misattribution is copied faithfully, not corrected: episode-aim's objection, stated in the
  step's docstring. A displaced worker's late heartbeat or `stopped` is named for its successor's claim,
  because the positional rule attributed it there, and the log does not record which worker wrote it.
- The `bound` records are inferred from the positional rule. The 0.2.0 log never recorded which episode
  registered a lease.
- A nak bearing a uniquely named stop's id, after that stop, is presumed to be that stop's refusal. The
  reference worker naks only the request it is handling, so only a nak it did not write (a third
  party's, or another worker's) could make that wrong: a forged nak.
- The real corpus has no subscribes, naks or unsubscribes, so those parts are tested only on synthetic
  logs.

## 7. A consumer's upgrade

At the consumer's own time, in this order:

1. Bump its pin to the commit that introduces format 0.3.0.
2. Onboard: move its legacy logs into `v0.2.0/`, as the `LogFormatMissing` message instructs.
3. Migrate a **copy** first and compare its reads, then run `runstate migrate <root>`.
4. Change its code:
   - mint a `request_id` on every `control.stop` (none of the corpus's 184 stops has one);
   - mycooc's `resume_fanout`: the claimless release names the stops it clears (`claim_seq: null`,
     `honored: [...]`);
   - mycooc's reclaim tool names the claim it releases, with `honored: []`, so it no longer clears pending
     stops;
   - anything that builds `{rid}.db` paths by hand goes through the locator.

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
