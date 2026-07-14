# Spec: the observer clock — two proposals, judged side by side

**Status:** DECISION DOC, 2026-07-14. Draft 1 was **refuted in part** by three
independent adversaries (defend-the-frozen-envelope / attack-the-basis /
steelman-the-capability); every refutation below was re-verified by hand before being
written down, and every one held. The *field* survived; the draft's justification and
all three of its deletions did not. This rewrite states **both** surviving designs
properly and judges them. Item 1 of `../backlog/third-party-observer.md`.

---

## 1. The problem

An envelope is `(seq, topic, name, request_id, body)`. `seq` gives **order**, not
**time**. `Heartbeat` is `{step, consumed_seq}`; `Stopped` / `Terminated` / `Launched`
carry no clock. The one record whose entire job is *"I am alive"* has no time of its
own.

The design did not forget this — it **put the clock in the observer**: `observables.py`
calls arrival time "the one non-log-derivable input", and the `Watcher` knows when a
beacon arrived because *it was there*. Airtight for the party that launched the run and
watched from birth; it collapses for anyone who attaches later.

**Three victims, verified:**

1. **`Watcher.poll` returns a wrong verdict.** `last_heartbeat_at` seeds at
   *registration*, so a run whose last write was 21 days ago reports
   `Running(step=41, beacon_age=9.5e-06)`. On the real corpus: five mycooc runs, dead
   12–21 days, painted live.
2. **A viewer cannot exist.** Status, freshness, "is it stuck", sort-by-recency — every
   column is downstream of a clock the protocol lacks.
3. **The GC's safety net does not hold.** `./store.md` Recipe 3 gates an *irreversible
   deletion* on a grace window ("skip homes younger than T") with no clock to compute
   the age; the natural fallback (file mtime) is the one `../backlog/wal-liveness-mtime.md`
   documents as lying under WAL.

**A consumer already broke the abstraction**: mycooc reaches past the API with raw
`sqlite3` + `SELECT max(created_at)`. That is the strongest evidence a primitive is
missing.

---

## 2. What the adversarial pass settled (binding on both proposals)

- **The library never forgets; hand-built bodies do.** Measured: `Value.t` is null on
  **90,732/90,732** translation value points (100% — hand-written as `t=None`, by workers
  whose `step` is a sentence index and who honestly decline a time axis) and on
  **0/95,738** mycooc points (0% — it uses the reference `Worker`). *(Draft 1 stated this
  backwards and drew the wrong rule from it.)*
- **`created_at` is NOT an append time.** SQLite stamps `time.time()` **before** taking
  the lock: 8 concurrent writers, one host, one clock, no NTP step → **11% of records
  have `t` going backwards vs `seq`** (worst −56.7 ms). Stamping **inside the critical
  section** (SQL-side, as Postgres already does) → **0 inversions in 2,400**.
- **The worker's clock and the append clock are DIFFERENT QUANTITIES.** `Value.t` /
  `Started.attached_at` are the *worker's* frame (design §11: "all scheduling predicates
  evaluate in the worker's tick"); an append time is the *log's*. `history()` replays the
  former. **Neither proposal may delete them.**
- **`Watcher.last_heartbeat_at` is skew-immune** (both terms are the observer's own
  clock) and must be **kept** as the preferred input for beacons the observer *witnessed*;
  a record clock may only seed the prefix it did not witness. Replacing it wholesale
  reintroduces the exact failure used to kill the clamp below.
- **"Time never arbitrates" is false as an absolute** — `ensure` already gates production
  on wall-clock, and the GC's grace window gates an `rm -rf`. The precise rule: **time
  never arbitrates a claim or a death verdict; it may gate a reversible decision; an
  irreversible action must be gated on a record-plane fact, with time as a belt and never
  as the reason.**
- **Four clock designs are dead, in both proposals** (do not revisit): the **monotone
  clamp** (one fast clock poisons the log forever, silently, while staying monotone —
  CockroachDB can clamp only because it *enforces* a clock bound we cannot); a
  **monotonic/stopwatch clock** (no shared origin ⟹ staleness structurally unanswerable);
  **wall anchored to a stopwatch** (`CLOCK_MONOTONIC` does not tick across suspend ⟹ a
  slept machine's beacon reads fresh — the very bug we are fixing); and **doing nothing**
  (victim 1 is a wrong verdict, not a missing convenience).

---

## 3. Proposal A — `t` on the envelope: the log's own clock

**Every envelope carries `t`, the wall-clock instant at which the log ACCEPTED the
record, stamped at the append point, inside the append critical section.**

```
Envelope = (seq, topic, name, request_id, t, body)
```

### 3.1 The rules

- **`seq` orders. `t` measures.** `seq` remains the sole authoritative order; `t` is
  never an ordering key, never a correctness filter, never an arbiter.
- **One log, one clock.** `t` is *not* the writer's clock — it is the **append locale's**.
  A log has exactly one append point (that is what makes `seq` a total order), so every
  `t` on a log comes from one clock, and **durations between any two records on a log are
  meaningful** — including cross-writer ones ("the stop was accepted at T₁, the stopped
  landed at T₂"). *Exception, documented: sqlite-over-NFS has multiple append locales and
  therefore multiple clocks; that deployment is already scoped single-host-conservative
  elsewhere in the design.*
- **Monotone with `seq` — a CONFORMANCE PROPERTY**, not a hope. Because the stamp is
  taken inside the same critical section that assigns `seq`, `t` is non-decreasing along
  the log; a backend that cannot honor this is non-conformant. This answers the sharpest
  structural objection to a new envelope field (that it would be the first with no
  property a conformance suite can assert).
- **Two clocks, two homes — and they are orthogonal, not redundant.** The **envelope**
  carries the **log's** proper time (*when the record was accepted*). The **body** carries
  the **writer's** proper time where the writer has something to say (`Value.t` = when the
  worker *observed* the value; `Started.attached_at` = when it attached). A buffering or
  backfilling worker makes these genuinely differ. This is Kafka's `LogAppendTime` vs
  `CreateTime` — which Kafka made a *config* because it could not choose; we keep both,
  in different places, with different names.
- **Staleness (`now() − t`) is a LOCAL inference** — it compares the reader's clock to the
  log's, so it is valid only for a roughly-synced observer. It stays in the `Watcher`
  (the inference plane) and never becomes a record-plane verdict. At any distance where
  clocks cannot be synced, **absence of news is not news**.

### 3.2 The lift-rule: an ADDED clause, not a new rule

Draft 1's *"the envelope carries what the substrate KNOWS"* is **refuted** — `name` and
`request_id` are writer-supplied; the substrate *indexes* them, it does not know them.
The rule evicted two fields already in the envelope. The repair:

> The envelope carries **(a)** what the substrate **assigns at the append chokepoint**,
> uniformly, for every record, without reading the body — `seq` (the order) and `t` (the
> instant); and **(b)** what it **indexes / routes / filters** on — `topic`, `name`,
> `request_id` *(the original rule, intact)*.

Author/provenance (design §12.8) is in neither — the *writer* supplies it and nothing
routes on it — so it stays excluded, which is what the rule had to achieve. *(The "only
envelope bump we will ever need" boast is withdrawn as unearned.)*

The real argument for `t` is **universality + chokepoint**: `channel.send` is the one path
every record passes through, so a field stamped there cannot be forgotten by any
convention, any writer, or any **future topic**.

### 3.3 Cost

- The **first envelope bump** (`envelope-v0.3`). §12's standing boast that no open item
  "changes the wire envelope" is spent. Under a fixed-forever protocol this is a one-time,
  irreversible expenditure.
- A **pure addition** to the basis — no deletions (draft 1 claimed three; all withdrawn).
- The stamp must move into the critical section in all three backends; `MemoryChannel`
  starts stamping and needs a clock-injection hook for deterministic tests.
- **No data migration**: every row of both durable backends already carries `created_at`
  (verified: 1,998/1,998 on a sampled real log). Old rows carry the *pre-lock* stamp —
  weaker (non-monotone under contention), bounded by the lock wait, irrelevant to
  freshness. New rows get the real thing.
- `read()` carrying `t` costs ~15% on full-scan folds (measured by an adversary); the
  `latest()`-backed poll plane is unaffected.

---

## 4. Proposal B — the dated beacon: `t` on the records that matter, envelope untouched

**`lifecycle-v0.4`** — `Heartbeat` and `Stopped` gain `t`. **`launcher-v0.3`** — `Launched`
and `Terminated` gain `t`. **`Channel.freshness() -> float | None`** — promoted from an
opt-in capability to a **substrate op** in design §4's Surface, on exactly the ground
`last_seq()` was admitted (2026-07-10), pinned in the conformance suite and named in
`protocol/`. `Started.attached_at` already is such a clock.

This is the strongest envelope-frozen design, and the corrected evidence *supports* it:

- **Coverage where it counts, with a measured 0% miss rate.** Every record the observer
  plane reads — `started`, `heartbeat`, `stopped`, `launched`, `terminated` — is
  **library-written**, and library-written clocks are never forgotten (mycooc: 0/95,738
  null). The 100%-null field is the one *user code hand-builds*. Draft 1's "body fields get
  forgotten" killer **does not touch this proposal.**
- **All three victims are served.** Freshness/status: the beacon's own `t`. Stall
  detection: the heartbeat is emitted *inside* `tick()` — there is no background beat
  thread — so a hung step means a **quiet log**, which a head-only reading detects
  exactly. "When did it finish": the last record is the terminal on 1,128/1,129 finished
  runs. The GC's grace window: `freshness()`.
- **The freeze survives.** Convention schemas version on independent timelines by
  doctrine; the envelope — the one artifact every backend and every other-language
  implementation cannot opt out of — stays frozen. A v0.2 Rust orchestrator keeps working.
- **Body fields round-trip.** `send()` takes a `body` and no `t`, so a body clock survives
  export/import/replay through the public surface; an envelope clock **cannot be
  re-supplied** through `send()` at all (a real gap in Proposal A, see §6).
- **No frame confusion.** Every clock is the *writer's*, everywhere. One meaning, one
  frame, no per-backend variation.

### What it permanently forgoes (under a fixed protocol, "permanently" is literal)

- **`control.*` is never dated.** "The stop was issued at T, honored at T+40 s" is
  unaskable, forever — and the *issuer's* proper time is lost, recoverable from nowhere
  else.
- **User `value` bodies are never dated** unless their author remembers — and the corpus
  says the author (translation) *doesn't*.
- **Every future topic starts undated**, and every future convention must remember to
  carry a clock. The chokepoint guarantee is unavailable by construction.
- **`freshness()` is a projection of the thing it avoids.** `last_activity` is derivable
  from envelope-`t`; envelope-`t` is not derivable from `last_activity`. Under the basis
  rubric's Independence criterion, that asymmetry is a mark against B, not A.

---

## 5. Judged side by side

| Criterion | A — envelope `t` | B — dated beacon |
|---|---|---|
| Fixes victim 1 (wrong verdict) | ✅ | ✅ |
| Fixes victims 2–3 (viewer, GC age) | ✅ | ✅ |
| Dates third-party records (`control.*`) | ✅ | ❌ **never** |
| Dates user `value` bodies | ✅ | ❌ (author must remember; measured: doesn't) |
| Dates **future** topics | ✅ by construction | ❌ each must opt in |
| Can a writer forget it? | ❌ impossible (chokepoint) | possible in principle; **0% in practice** for library-written records |
| Cross-record durations | ✅ one clock per log | ⚠️ writer's clock; cross-writer is approximate |
| Monotone-with-`seq` conformance property | ✅ | n/a (body fields carry no such contract) |
| Envelope freeze | ❌ **spent** | ✅ preserved |
| Breaks a v0.2 other-language implementer | ✅ **yes, unavoidably** | only if it implements those conventions |
| Round-trips through `send()` (export/replay/import) | ❌ **no** (§6) | ✅ free (`body` is opaque) |
| Basis effect | pure **addition** (+1 envelope field) | +4 body fields, +1 substrate op |
| Independence (rubric #1) | `freshness()` becomes derivable from it | its op is a **projection** of A's field |
| Data migration | none | none for new logs; **old beacon bodies would need rewriting** |

**The two decisive rows are the two that cannot be undone.** B spends nothing and
permanently forgoes coverage of every record it does not enumerate. A spends the envelope
freeze — once, irreversibly — and buys coverage that no future convention, topic, or
writer can escape.

---

## 6. The galaxy-scale discriminator

Under the owner's premise — *a fixed protocol, never changed, used by an enormous number
of entities across lightyear-level distances* — the fork is not symmetric.

1. **Only records survive distance.** Every probe (`resolve`, `EpisodeProbe`, and any
   `freshness()`-style query) asks about a **remote present**, and there is no remote
   present. Both proposals are record-based, so both survive — but B's `freshness()` op is
   a probe, and at distance it degenerates to "read the last record's `t`", i.e. to A's
   field, done worse.
2. **The append point is necessarily ONE locale.** You cannot append across four
   light-years, so a log's records all enter it in one place, on one clock. That makes A's
   `t` **the log's own proper time** — a single coherent time axis over the entire log,
   which is the *maximum* time information physics permits a log to carry. (It also
   rescues "a log is a worldline", which I had retracted for the wrong reason: not one
   writer, but **one acceptor**.)
3. **A protocol that dates only some records discards the rest forever.** Every record is
   an event; every event has a proper time; the party that accepts it is the only one who
   can date it. B dates the worker's and the launcher's records and throws away the time
   of every other participant's — permanently, since the protocol never changes. At
   galaxy scale a log has *many* correspondents, and B is blind to all but two.

**The Doppler case leans to A**, and harder than the terrestrial argument did.

---

## 7. Recommendation

**Proposal A**, with everything the adversaries forced: the append-locale stamp inside
the critical section; monotonicity as a conformance property; the two-clause lift-rule;
`Value.t`, `Started.attached_at` and `Watcher.last_heartbeat_at` all **kept**; and the
honest admission that this is a **pure addition** to the basis, not the reduction that
was pitched.

If the envelope freeze is judged too valuable to spend — a legitimate call, and B is a
real design, not a straw man — then B is the answer and its coverage gaps are the price.
**What would be wrong is to take A's field and B's justification**, which is what draft 1
did.

## 8. Open questions

1. **`send()` cannot re-supply `t`** (Proposal A). So a log cannot be exported and
   re-materialized into another backend through the public surface without forging every
   timestamp to the copy time. Is log *transfer* a substrate-level operation (copy the
   file; `INSERT … SELECT`), or must the API support it? If the latter, an import path
   that accepts `t` reopens "a writer can supply it" — and with it, part of the argument.
2. **Monotonicity across processes on one host** is asserted (the stamp is taken while the
   write lock is held) but has only been measured *within* one process. Pin it with a
   conformance test before promising it.
3. **`MemoryChannel` needs a clock-injection hook**, or the clock-injected tests
   (`Watcher(now=…)`, the memoizer's epoch fixtures) will compare an injected clock against
   a real one.
4. **Does `last_activity(channel)` ship at all?** It is derivable under A — but it is the
   GC's input, and the GC deletes. Its safe form is not obvious (a single record from a
   fast clock pins `max(t)` into the future forever). Consider shipping it *only* as
   `t(latest envelope)`, with the irreversible-action rule of §2 as its guard.
