# Spec: the observer clock — `seq` orders, `t` measures

**Status:** DRAFT 2026-07-14 — **REFUTED IN PART by the adversarial pass; under
revision.** The *field* survives; its **justification and all three of its deletions do
not**. Corrections landed below inline; the full rewrite awaits the owner's ruling.
Item 1 of `../backlog/third-party-observer.md`.

**The corrections (each verified by hand, not taken on an adversary's word):**
1. **A load-bearing statistic in this doc was INVERTED.** It read "`Value.t` … null on 0
   of 150 sampled translation runs", which as written says the field was *never*
   forgotten — refuting the very claim it was cited for. Measured truth: translation
   **90,732 / 90,732 value points are null (100%)** — written by hand as `t=None`,
   deliberately, by workers whose `step` is a sentence index and who honestly decline a
   time axis they do not have. And mycooc: **0 / 95,738 null (0%)**, because it uses the
   reference `Worker`. **The real rule is not "body fields get forgotten" — it is
   "fields the LIBRARY writes are never forgotten; fields user code hand-builds are."**
   That weakens the coverage killer aimed at the heartbeat-only alternative below.
2. **`created_at` is NOT an append time**, so §2.4's "a projection, not a change" is too
   strong. `SqliteChannel` calls `time.time()` **before** taking the lock, so under 8
   concurrent writers — one host, one clock, no NTP step — **11% of records have `t`
   going backwards versus `seq`** (worst: −56.7 ms, measured). The stamp must move
   **inside the append critical section** (SQL-side, as Postgres already does): the same
   hammer then yields **0 inversions in 2,400**. Which is the payoff — **monotone-with-`seq`
   becomes a testable CONFORMANCE PROPERTY**, answering the objection that `t` would be
   the first envelope field a backend cannot be held to.
3. **The keystone rule (§2.2) is FALSE as stated** — "the envelope carries what the
   substrate KNOWS" evicts `name` and `request_id`, which are *writer*-supplied (the
   substrate indexes them; it does not know them). See §2.2's replacement.
4. **All three deletions (§2.3) are withdrawn.** See §2.3.

## 1. The problem: nothing on the log says when anything happened

An envelope is `(seq, topic, name, request_id, body)`. `seq` gives **order**, not
**time**. `Heartbeat` is `{step, consumed_seq}`; `Stopped`, `Terminated`, `Launched`
carry no clock at all. The one record whose entire job is *"I am alive"* has no time
of its own.

The design did not forget this — it **placed the clock in the observer**:
`observables.py` says the Watcher "adds the one non-log-derivable input, **arrival
time**." The Watcher knows when a beacon arrived because it *was there when it
arrived*. That is airtight for the party that launched the run and watched it from
birth, and it collapses for anyone who attaches later, because they were not there
and the record has no time of its own.

**Three victims, all verified:**

1. **`Watcher.poll` returns a wrong verdict.** `last_heartbeat_at` seeds at
   *registration*, so a run whose last write was 21 days ago reports
   `Running(step=41, beacon_age=9.5e-06)` — reproduced. On the real corpus that is
   five mycooc runs, dead 12–21 days, painted live. Same failure family as the forged
   launcher verdict (`./launcher-record-identity.md`), one tier up.
2. **A viewer cannot exist.** Every list-view column — status, freshness, "is it
   stuck", sort-by-recency — is downstream of a clock the protocol does not have.
3. **The GC's safety net does not hold.** `./store.md` Recipe 3 gates collection on a
   grace window — *"skip homes younger than T"* — an age check guarding an
   **irreversible deletion**, with no clock in the protocol to compute the age. The
   natural source, file mtime, is the one `../backlog/wal-liveness-mtime.md`
   documents as lying under WAL (306 s stale on a healthy run, measured).

**A consumer already broke the abstraction over it.** mycooc opens the channel with
raw `sqlite3` (`file:…?mode=ro`) and runs `SELECT max(created_at) FROM log` — reaching
past the public API into a backend-private column, having explicitly rejected file
mtime. A consumer bypassing the protocol is the strongest evidence a primitive is
missing.

## 2. The design

**Every envelope carries `t`: the wall-clock time of its append, stamped by the party
that appends it.** That is the whole change.

```
Envelope = (seq, topic, name, request_id, t, body)
```

### 2.1 The rules (this, not the field, is the spec)

- **`seq` orders. `t` measures.** The authoritative order is, and remains, `seq`. `t`
  is never an ordering key, never a filter for correctness, never an arbiter.
- **`t` is authoritative within one writer, approximate across writers, meaningless
  across relativistic distance.** A worker's own records within an episode come from
  one process — one clock, one worldline — so durations between them (beat intervals,
  step timings, stall detection) are **real**. A comparison spanning writers (an
  observer's `control.stop` at T₁ vs the worker's `lifecycle.stopped` at T₂) mixes two
  clocks and is only as good as their synchronization. The topic identifies the writer
  class (`lifecycle.*`/`value` = the claiming worker; `launcher.*` = the launcher;
  `control.*` = an observer), so a fold always knows whether it is mixing clocks.
- **Staleness is a *local inference*, not a log fact.** `now() − t` compares the
  reader's clock to the writer's; it is valid only for an observer whose clock is
  roughly synced. That is why it stays in the `Watcher` (the inference plane) and never
  becomes a record-plane verdict — consistent with `resolve()`'s hostname scoping and
  with the fact that at any distance where clocks cannot be synced, **absence of news is
  not news**: a log ending at T cannot distinguish "the worker died at T" from "the rest
  has not arrived."
- **Time never arbitrates a CLAIM or a DEATH VERDICT.** *(Corrected: "time NEVER
  arbitrates" was false the day it was written — `ensure` already gates production on
  wall-clock via `until={"time_seconds": …}`, and `store.md`'s GC grace window gates an
  `rm -rf`.)* The precise rule: time may gate a **reversible** decision (`ensure`'s drive
  window); an **irreversible** one (the GC) must be gated on a **record-plane fact**
  (`live_episode is None` ∧ no pointer), with time as a belt and never as the reason.
  Claims, verdicts and discharge are decided by `seq` + the CAS, forever. Claiming on staleness *inference* is an already-refuted dead end
  (`../dead_ends/failure-detector.md`: it admits a double-live window that permanently
  poisons reuse). The clock describes; it does not decide. This line must be defended
  every time someone says "we have timestamps now."

### 2.2 The lift-rule refinement (design §4)

The current rule — *"a field is in the envelope iff the substrate indexes/routes/
filters on it"* — misfiles time: our backends store it and do not route on it, while
Kafka (`offsetsForTimes`) and Redis Streams (whose IDs **are** timestamps) do. The
rule is a proxy for something truer:

> ~~**The envelope carries what the SUBSTRATE knows. The body carries what the WRITER
> knows.**~~ **REFUTED**: `name` and `request_id` are supplied by the *writer*; the
> substrate merely indexes them. The rule evicts two fields already in the envelope.
>
> **The replacement — an ADDED clause, not a new rule.** The envelope carries **(a)**
> what the substrate **assigns at the append chokepoint**, uniformly, for every record,
> without reading the body — `seq` (the order) and `t` (the instant); and **(b)** what it
> **indexes / routes / filters** on — `topic`, `name`, `request_id` (the original rule,
> intact). Author/provenance (§12.8) is in neither: the *writer* supplies it, and nothing
> routes on it — so it stays excluded, which is what the rule had to achieve.
>
> The real argument for `t` is **universality + chokepoint**, not epistemics: every record
> is appended at some instant, and `channel.send` is the one code path every record passes
> through — so a field stamped there cannot be forgotten by any convention, any writer, or
> any *future* topic. (The "only envelope bump we will ever need" boast is withdrawn: it is
> unearned, and was written the same week the ledger found another missing basis vector.)

`seq` — the substrate assigns it. `topic`/`name`/`request_id` — the routing keys it
indexes. **`t` — the substrate is the party doing the appending, and appending is an
event in spacetime: it is the only party that can honestly say *when*.** Meanwhile
author/provenance (design §12.8, the other standing envelope candidate) is a **writer**
fact — the substrate never knows it, which is precisely why every log broker on earth
stamps a timestamp and **none** stamp an author. The refined rule admits `t`, still
excludes author, and *explains* the empirical fact the old rule could not.

It also **bounds this bump**: what else does a substrate know? Order, routing keys,
time. That is the complete list — so this is plausibly the only envelope bump the
design will ever need, which is the claim one wants before touching a frozen wire.

### 2.3 What this DELETES — ~~the basis reduction~~ **ALL THREE WITHDRAWN**

**The deletions were the spec's selling point and they are wrong.** `t` is a pure ADDITION
to the basis. Honest, and weaker than what was pitched:

- **`Watcher.last_heartbeat_at` — KEEP.** Deleting it is a *regression*. Today
  `beacon_age = now() − arrival`, and BOTH terms come from the observer's own clock —
  **skew-immune by construction**. `now() − t` is a *cross-clock* subtraction: a worker
  clock 10 min slow makes a healthy, beaconing run read `presumed_dead` (a failure
  outcome — `sweep` would fail a live run); 10 min fast makes `beacon_age` negative → the
  run reads maximally fresh forever and a hang is never detected. **That is verbatim the
  failure this spec uses to execute the monotone clamp** (§3). Correct design: keep the
  arrival clock as the preferred input for beacons the observer *witnessed*, and use `t`
  ONLY to seed the prefix it did not.
- **`Value.t` / `Started.attached_at` — KEEP.** They are the **worker's frame**; envelope
  `t` is the **appender's frame**. Design §11: *"all scheduling predicates evaluate in the
  worker's tick, against step/wall-clock"* — and `history()` replays that axis
  (`t_point − epoch`). On `PostgresChannel` the appender is the *server*, so deleting them
  silently moves the replay into a different frame from the live evaluation. The null is
  load-bearing too: a timeless worker (translation's) *declines* the axis, and the memoizer
  documents and TESTS the resulting inertness. If coverage is wanted, it is a `value-v0.3`
  bump making `Value.t` required — a separate question, not this spec's.

*(Superseded text follows.)*

- **`Value.t`** — the value's wall-clock, worker-stamped, nullable. Now derivable from
  its envelope. *(And unreliable in practice: null on **0 of 150** sampled translation
  runs. A worker-stamped clock is a request; a substrate stamp is a fact.)*
- **`Started.attached_at`** — the run-epoch anchor (`memoizer._epoch`). Now derivable:
  the epoch is the `t` of the `started` envelope.
- **`Watcher.last_heartbeat_at`** and `_note_heartbeat` — the arrival-time state that
  exists *only* because the log had no clock. Staleness becomes
  `now() − t(latest lifecycle record)`, with the never-beaconed startup-crash case
  falling out for free (the `started` envelope's own `t`). The docstring claim that
  arrival time is "the one non-log-derivable input" **dies with it.**

Three ad-hoc semi-clocks and one piece of observer state collapse into one field on
the record.

### 2.4 Migration: none

**Verified:** every row of every real log already carries the timestamp. `SqliteChannel`
has written `created_at REAL NOT NULL` since the beginning (`time.time()`);
`PostgresChannel` writes `created_at double precision NOT NULL`
(`clock_timestamp()`, server-side). A sampled real log: 1,998 envelopes, **0** without
a timestamp, **0** timestamps out of order vs `seq`. The bump *exposes* what the
substrate has been recording all along — a projection, not a change. `MemoryChannel`
(which stores no time today) starts stamping at append.

The first envelope bump in the project's history is therefore also the cheapest change
in it. That is not a coincidence: the backends all independently decided the append
time was worth recording, which was the evidence the lift-rule was wrong.

## 3. Rejected alternatives (each with its killer)

- **A clock on `Heartbeat` only** (a `lifecycle` bump). *Killers:* (i) **coverage** —
  it dates the beats and nothing else, so "when did this run finish?" stays unanswerable
  forever, and every record a *third party* writes (`control.stop`, `launcher.terminated`)
  stays undated, so "the stop was issued at T, honored at T+40 s" is unaskable; (ii)
  **it is a body field, and body fields are forgotten** — `Value.t` is exactly this
  shape and is null on 0/150 real runs. Under a never-changed protocol, a coverage gap
  is permanent.
- **A `freshness()` backend capability** (the `EpisodeProbe` pattern; what the 2026-07
  review predicted). *Killers:* (i) it is a **probe, not a record** — it answers about
  the log's *head*, so it cannot date history, cannot measure a stall, cannot say when a
  run finished; (ii) it does not travel — copy the log and the capability stays behind,
  breaking the **replay principle** the viz project is founded on; (iii) it is a Python
  `Protocol` class, **invisible in `protocol/`** — a Rust implementer working from the
  schemas gets no time at all; (iv) at any real distance a probe is a question about a
  remote *present*, and there is no remote present.
- **A monotone clamp** — stamp `max(own clock, previous t)` so time can never contradict
  `seq` (a hybrid logical clock, as CockroachDB does). *Killer:* **unbounded, silent,
  permanent poisoning.** One writer whose clock is far ahead pins every subsequent
  record — by any writer, forever — to that value: durations collapse to zero and
  `now() − t` goes negative, so the run reads *maximally fresh forever* and stalls become
  invisible. Both primary uses are destroyed, and the timestamps stay perfectly
  monotone, so **it passes every consistency check while lying**. CockroachDB can clamp
  only because it *enforces* a max clock offset and kills nodes that exceed it; that
  antidote is unenforceable here, and taking the technique without it is taking the
  poison alone. Meanwhile the disease it treats is minor: the clock's primary consumer
  (beat intervals within an episode) is **single-writer**, hence monotone by
  construction, clamp or no clamp.
- **A monotonic ("stopwatch") clock instead of wall.** *Killer:* **no origin.**
  `time.monotonic()` counts from an arbitrary zero (process start / boot), so its
  readings are incomparable across processes — an observer cannot subtract its own now
  from a worker's stopwatch at all. That makes staleness, the headline use case,
  structurally unanswerable.
- **Wall anchored to a stopwatch** — `t = wall_at_start + (mono_now − mono_at_start)`:
  absolute *and* jump-free. *Killer:* **suspend.** `CLOCK_MONOTONIC` does not tick while
  a machine is suspended, so a workstation that sleeps for eight hours resumes claiming
  minutes passed — and its last beacon reads **fresh**. That is the exact bug this spec
  exists to kill, reintroduced by the clever fix. Plain wall gets suspend right.

**Accepted cost of plain wall:** a clock *step* (NTP correction, a manual set) can make
a duration wrong or negative even within one episode. That failure is **bounded** (by
the step) and **loud** (a `t` that goes backwards contradicts `seq` — trivially
detectable, and it is exactly the signal "this writer's clock was adjusted"). Every
alternative's failure is silent. Loud-and-bounded is the failure to choose, and it is
the same principle the verdict folds already follow.

## 4. Scope / ripple

- `protocol/envelope-v0.3.schema.json` — `t` required, `number`, `minimum: 0` (v0.2
  deleted).
- `protocol/lifecycle-v0.4.schema.json` — `Started.attached_at` removed (derivable).
- `protocol/value-v0.3.schema.json` — `Value.t` removed (derivable).
- `runstate/channel/envelope.py` + the three backends — `t` on the record; `MemoryChannel`
  starts stamping.
- `runstate/watcher.py` — the staleness tier reads the beacon's own `t`;
  `last_heartbeat_at` / `_note_heartbeat` deleted.
- `runstate/memoizer.py` — `_epoch` reads the `started` envelope's `t`.
- `runstate/observables.py` — the "one non-log-derivable input" docstring; a
  `last_activity(channel)` fold becomes trivial (and is what the GC's grace window and
  the viewer's freshness column both wanted).
- `docs/design-v0.2.md` §4 (the lift-rule refinement), §11 (three clocks — the
  wall-clock now has a home on the record).
- **No data migration.**

## 5. Open questions (for the adversarial pass)

1. **Defend the frozen envelope.** §12 boasts that no open item "changes the wire
   *envelope*." Is that stability worth more than this? Is there a formulation that
   gets the persona what it needs without touching it — and does that formulation
   survive §3's killers?
2. **Attack the lift-rule refinement.** If "the substrate knows it" is wrong or
   under-determined, the whole argument collapses. Is append-time really substrate
   knowledge on an *embedded* backend, where the "substrate" is a file with no clock and
   the stamper is just… a writer? (This is the sharpest objection I know of against my
   own proposal, and I do not have a knock-down answer.)
3. **Is deleting `Value.t` an over-reach?** Acquisition time ≠ append time for a
   buffering or batching worker, and the future data plane (Bluesky-style detectors)
   will care. Does the core keep one clock and let the data plane carry its own?
4. Does anything break if `t` is *not* monotone along a log (the accepted cost)? Name a
   fold that would silently misbehave.
