# Spec: the observer clock — date the beacon

**Status:** CONVERGED on **Proposal B**, then hardened by a fourth (implementation) review
(2026-07-16). Draft 1 proposed a wall-clock `t` on every **envelope** (Proposal A); three
adversaries refuted its *justification* (not the field), and the owner ruled **B over A on
minimality + layer**: the clock is a semantic, run-life concern, so it belongs in the
**opt-in conventions**, not the opinion-free substrate. A final review then caught two
implementation blockers now folded in: the Watcher fix must seed **three** fields or it is
a silent no-op (§5), and `started` must be **renamed**, not `created_at`-stamped, in the
migration (§8). This is the implementable spec. Item 1 of
`../backlog/third-party-observer.md`; full A-vs-B judgment and adversarial findings in git
(`3ce77b1`, `3ce9eab`).

---

## 1. The problem: the beacon carries no clock

An envelope is `(seq, topic, name, request_id, body)` — `seq` gives **order**, not
**time**. The design deliberately put the liveness clock **in the observer**:
`observables.py` calls arrival time "the one non-log-derivable input", and the `Watcher`
knows when a beacon arrived because *it was there*. That is airtight for the party that
launched the run and watched from birth. It collapses for anyone who attaches later.

The concrete gap, verified: the reference `Worker` stamps a clock where a field exists
for one —

```
Started(handle, attached_at=self._now())   ✓ dated
Value(value, step, t=self._now())          ✓ dated (present-nullable)
Heartbeat(step, consumed_seq)              ✗ NO CLOCK FIELD
Stopped(completed, error, final_step)      ✗ NO CLOCK FIELD
Launched(handle, status)                   ✗ NO CLOCK FIELD
Terminated(reason, exit_code, signal)      ✗ NO CLOCK FIELD
```

— and **the beacon (`heartbeat`) is exactly the record liveness reads.** The Worker beats
every tick, so a live log gets fresh beacons saying "step 41, consumed 40" with no
wall-clock. A reader sees *where* the run is, never *when* that was. This is not the
Worker forgetting to stamp; the `Heartbeat` body has nowhere to put a time.

**Three victims, verified:**

1. **`Watcher.poll` returns a wrong verdict.** `last_heartbeat_at` seeds at
   *registration*, so a run whose last write was 21 days ago reports
   `Running(step=41, beacon_age=9.5e-06)`. On the real corpus: five mycooc runs, dead
   12–21 days, painted live.
2. **A viewer cannot exist.** Status, freshness, "is it stuck", sort-by-recency — every
   column is downstream of a clock the beacon lacks.
3. **The GC's safety net does not hold.** `./store.md` Recipe 3 gates an *irreversible
   deletion* on "skip homes younger than T", with no clock to compute the age; the
   fallback (file mtime) is the one `../backlog/wal-liveness-mtime.md` documents as lying
   under WAL. "When did this home last change?" = `last_activity` (§6). *Scope: this spec
   supplies the grace window's **age** (the belt); it does not by itself make the GC safe
   cross-host — the hard gate `live_episode is None` reads conservatively-live for an
   unresolvable foreign handle (`observables.py:145`). That is store.md's concern, not
   this one's; victim 3 is the missing age, not the whole gate.*

A consumer already broke the abstraction over this: mycooc reaches past the API with raw
`sqlite3` + `SELECT max(created_at)`. That is the strongest evidence a primitive is
missing — and it points at a *record* clock, not a substrate one.

---

## 2. The core fix

> **`Heartbeat` gains a required `t`. The `Worker`'s existing emit path fills it — it
> already calls `self._now()` three lines from where it builds the beacon.**

That is the whole freshness fix. `Started.attached_at` proves the pattern already exists
on the lifecycle plane; the beacon simply never got it, because the design routed the
beacon's clock through the observer's arrival time instead of the record. With `t` on the
beacon, `now() − t(latest heartbeat)` is the staleness of *any* run to *any* reader — and
the 21-day-dead run dies correctly.

Everything else below is completeness around that core.

---

## 3. The change set

**The basis: date the records the observer/liveness plane READS** — not "every lifecycle
record." That set is `{started, heartbeat, stopped}` (lifecycle) + `{launched, terminated}`
(launcher); `nak` is a lifecycle record and is deliberately *left undated* (only
`await_consumed` reads it, for request correlation, never for time), so the set is minimal
and orthogonal, not uniform-by-topic.

**`lifecycle-v0.4`** (from v0.3):
- `Heartbeat` → `{step, consumed_seq, t}` — `t` **required**. A beacon with no time cannot
  do its one job, and the emitter always knows *now*.
- `Stopped` → `{completed, error, final_step, t}` — `t` **required**. The death time; the
  "when did it finish" and GC-age answer for a cleanly-stopped run.
- `Started` → `{handle, t}` — **`attached_at` renamed to `t`, and made required non-null**
  (harmonized 2026-07-16). `t` = the worker's wall-clock when it attached. `attached_at` was
  present-*nullable* only for a symmetry with `Value.t` that does not apply — a worker that
  attaches always attaches *at a time* — so the null option goes, matching `heartbeat`/
  `stopped`. `memoizer._epoch` reads `Started.t`; **keep its junk-tolerance guard** (a
  non-numeric/absent `t` on a hand-composed or foreign started earns `None`, not a crash —
  the measurement-plane rule, `memoizer.py:186`); only the reference-Worker *null* case
  becomes unreachable, not the guard's purpose.
- `Nak` — **unchanged, undated by design.** Not on the observer plane; a clock on it would
  be spanning-creep (dating a record nothing times). Do **not** "date Nak for symmetry."

**`launcher-v0.4`** (from v0.3):
- `Launched` → `{handle, status, t}` — `t` **required**. Launch time; and it dates the
  never-beaconed startup-crash (a `launched`+`terminated` with no heartbeat still carries
  a time).
- `Terminated` → `{reason, exit_code, signal, t}` — `t` **required**. The reaped death
  time; the GC-age and finish-time answer for a killed/crashed run.

**`value` — unchanged (stays v0.2).** `Value.t` already exists and stays **present-nullable**:
it is the *data plane's* observation clock (when the worker observed the value), a
different concern from liveness, and a backfill/timeless producer must be able to say
`null` honestly (see §4). **Freshness never reads `Value.t`** — it reads the beacon — so
translation's 100%-null value clocks are correct and irrelevant here. Making `Value.t`
required is a **separate** data-plane question, not this spec's.

**Substrate — untouched.** No envelope field, no new `Channel` op, no schema stack change
below the conventions. This is the minimality that chose B over A.

---

## 4. The rules (binding — carried from draft 1, which got these right)

- **`seq` orders. `t` measures.** `seq` remains the sole authoritative order; `t` is never
  an ordering key, never a correctness filter, never an arbiter.
- **`t` is a worker/emitter wall-clock**, authoritative within one writer's records,
  approximate across writers (an observer's request vs a worker's beacon mixes two
  clocks). The **topic + episode** identify the emitter; a fold that compares across them
  is comparing clocks and must know it.
- **Staleness (`now() − t`) is a LOCAL inference**, valid only for a roughly-synced
  observer. It stays in the `Watcher` (the inference plane) and never becomes a
  record-plane verdict. At any distance where clocks cannot be synced, **absence of news
  is not news**: a log ending at T cannot distinguish "died at T" from "the rest hasn't
  arrived."
- **Time never arbitrates a claim or a death verdict.** *(Not an absolute — `ensure`
  already gates production on wall-clock, and the GC's grace window gates an `rm -rf`.)*
  Time may gate a **reversible** decision; an **irreversible** one (the GC) must be gated
  on a **record-plane fact** (`live_episode is None` ∧ no pointer), with time a belt and
  never the reason. Claiming on staleness *inference* stays a refuted dead end
  (`../dead_ends/failure-detector.md`).
- **`t` is required ⟹ never fabricated.** Required works because every emitter of a dated
  record *has* the event's time. It does **not** license a `default_factory=time.time`:
  that silently stamps "now" onto a record whose real time is unknown (a backfill),
  manufacturing data and destroying the N/A signal. Where the time may be genuinely
  unknown (`Value.t`, the data plane), the field stays **present-nullable** — always
  present, explicitly `null` when N/A — never omitted, never defaulted.

---

## 5. The `Watcher`: keep `last_heartbeat_at`; seed it from `t`

**Staleness is secretly two inferences**, and separating them is the whole design here:
- **Progress** — "has a *new* beacon seq appeared since I started watching?" Both
  timestamps are the observer's own clock, so it is **skew-immune** — but it needs an
  observation *duration*, which a cold reader has not had.
- **Cold freshness** — "how old is the newest beacon, by *its own* `t`?" Available
  **instantly** from the record, but `now_observer − t_worker` is **cross-clock** by
  construction. You cannot have both skew-immunity and an instant cold answer: instant +
  cold means all you hold is the record (worker clock) and your now (observer clock), and
  comparing them is cross-clock definitionally.

So: **keep `last_heartbeat_at` as the witnessed (skew-immune) clock — do not replace it.**
`beacon_age = now() − last_heartbeat_at` with a witnessed arrival is both terms in the
observer's clock; replacing it wholesale reintroduces the failure that kills the clamp (a
worker clock 10 min fast ⟹ negative age ⟹ fresh forever ⟹ a hang never detected). The
record's `t` does exactly one new job: **seed the prefix the observer did not witness.**

**Seed THREE fields at registration, not one — this is the load-bearing detail.** `poll()`
calls `_note_heartbeat` *first* (`watcher.py:183`), and `_note_heartbeat` upgrades
`last_heartbeat_at → now()` whenever `hb.seq > last_hb_seq` (`watcher.py:341-352`). Today
`_track` leaves `last_hb_seq = 0`, so on a cold attach the *pre-existing* old beacon
(`seq 41 > 0`) is mistaken for a freshly-witnessed arrival and the seed is clobbered to
`now()` on the first poll — **the naive "seed `last_heartbeat_at`" is a silent no-op.** So
`_track`/`observe` must seed, from the latest heartbeat already on the log: `last_hb_seq =
hb.seq` (so the seed beacon is *not* re-counted as a new arrival), `last_step = hb.step`
(else `Running(step=None)` regresses), and `last_heartbeat_at = hb.t`. Only a genuinely
*newer* beacon (`seq > seed seq`) then upgrades `last_heartbeat_at` to `now()`
(skew-immune, as today). **No heartbeat on the log ⟹ seed `last_heartbeat_at = now()`** (the
current behaviour), preserving the never-beaconed-startup-death catch that tier 4 exists
for. Read the field as *"the best available estimate of when the last beacon happened, in a
clock comparable to now(): the record's own `t`, refined to your arrival observation when
you have one."*

**Why the seed is correct enough.** It converts today's *unconditional, unbounded*
false-alive (every cold-attached dead run reads live, forever) into a *skew-bounded* one:
the age estimate errs by at most the clock skew δ, extending the false-alive window from
`timeout` to `timeout + δ`. In a datacenter δ is NTP-scale against a minutes-scale timeout
— negligible. And the residual error is in the **conservative-live** direction the design
already prefers (`live_episode` treats the unresolvable as live; §4 forbids hanging any
irreversible action on staleness). A *negative* beacon_age (a future-dated beacon — the one
unambiguous "my cross-clock estimate is broken" signal) lands as conservative-live by the
existing code, which is the safe direction; no special handling is needed.

**Galaxy note.** The witnessed clock is the observer's private observation — it does not
travel. The record's `t` is on the log — it travels, and intra-worldline durations built
from it (how long the run beaconed, the beat cadence) are the worker's proper time,
frame-invariant at any distance. So the record's `t` is the *fundamental* liveness datum
and witnessed-arrival is a local refinement on top — consistent with §4's "staleness is a
local inference": at distance the tier is simply off (no shared now; absence of news is not
news), and a distant reader computes durations, not observer-relative staleness.

Whether to **expose which clock a verdict stood on** (witnessed vs seeded) is deferred to
§9 — the residual error is bounded and conservative, and §4 already forbids the decisions
where the basis would matter, so it is not load-bearing now.

---

## 6. `freshness` needs no new op

`last_activity(channel)` = **`max(t)` over the ≤3 finalists** `latest(heartbeat)`,
`latest(stopped)`, `latest(terminated)` — a handful of O(1) `latest()` reads, no full scan,
no sixth substrate op. *Pin the definition precisely* (the two phrasings in earlier drafts
were not equivalent under skew): it is the max-`t` **among the latest-by-seq record of each
of those three topics**, not "the `t` of the single newest-by-seq record" (which differ when
`t` is non-monotone vs `seq`) and not `max(t)` over the *whole* log (which a single
fast-clocked record pins into the future forever). Max-among-finalists is the conservative
choice for its consumer: largest plausible last-activity ⟹ smallest age ⟹ errs toward *not*
deleting, which is what the GC's grace window (§4, irreversible) wants.

This is morally what mycooc reached past the API to compute — though not identically:
mycooc runs `SELECT max(created_at)` over the *whole* log (`run_experiment.py:2395`), the
whole-log form this fold deliberately avoids; the two coincide only for a reference-Worker
run whose `t` is monotone.

**Known blind spot (accepted):** `last_activity` reads the liveness records, not `value`, so
a worker that *opts out of the `lifecycle` convention* and emits only dated `value` records
has activity but no `last_activity`. The reference `Worker` always beats a heartbeat per
tick (`worker.py:229`), so this never bites it; and "you composed your own loop, you own
your liveness" is the design's standing line for convention opt-outs. Noted, not fixed.

---

## 7. Rejected alternatives

- **Proposal A — `t` on every envelope** (the substrate stamps the append instant).
  Rejected on **minimality + layer**, *not* on the frozen-envelope argument (the owner set
  that aside) and *not* on cost (measured: A's read overhead is +12.4%, B's is +12.9% —
  a **wash**; it is a timestamp cost, not an envelope cost). The real reasons: (i) a clock
  is a *semantic, run-life* concern, and stamping it onto every user body is the substrate
  asserting something about data it is supposed to route opaquely — the opinion-free line;
  (ii) A subsumes B by projection ("the append `t` on a beacon ≈ the beacon's emit `t`,
  co-located"), and *"A gives you B for free"* is precisely the bread-price argument —
  subsumption is not a reason to enlarge the substrate; (iii) A's one unique win, dating
  `control.*` and *future* topics, is speculative ("someone might want it later") and
  recoverable by a convention bump the day it is real. A's genuine costs that B avoids:
  the first-ever envelope bump; `send()` cannot re-supply `t`, so a log cannot be
  re-materialized into another backend through the API without forging every timestamp
  (ruled: transfer is a substrate op — copy the file / `INSERT … SELECT`; a future
  `restore(envelopes)` is its named trigger, never an optional `t=` on `send()`).
- **Proposal C — a shared "timestamped" base for the convention bodies** (declare the
  clock once). Rejected: it has **no wire-level chokepoint to hang a guarantee on.**
  Inheritance is Python-local — invisible on the wire, so a Rust author still writes `t`
  per body. Pushing it into the schema (a shared `$defs` via `allOf`) **fights
  `additionalProperties: false`**: an `additionalProperties:false` subschema cannot see a
  sibling's `allOf`-inherited `t`, so a body would reject its own inherited field — the
  escapes are re-declare `t` per body (which *is* B, no DRY) or drop the closed-body
  guarantee (worse). Coupling/unifying the convention versions (tolerable for a closed
  set) does not change this; C reduces to B either way. The forgetting-prevention C wants
  already exists at the right place — the reference `Worker`'s emit path, the only
  chokepoint at the convention layer — which is why the library never forgets (mycooc:
  0/95,738 null value clocks; the 100%-null field is the one *user code* hand-builds).
- **Four clock designs, dead in every proposal** (do not revisit): the **monotone clamp**
  (one fast clock poisons the log forever, silently, while staying monotone — CockroachDB
  can clamp only because it *enforces* a clock bound we cannot); a **monotonic/stopwatch
  clock** (no shared origin ⟹ staleness structurally unanswerable); **wall anchored to a
  stopwatch** (`CLOCK_MONOTONIC` does not tick across suspend ⟹ a slept machine's beacon
  reads fresh — the very bug); and **doing nothing** (victim 1 is a wrong verdict).

---

## 8. Scope / ripple

- `protocol/lifecycle-v0.4.schema.json` — `Heartbeat.t`, `Stopped.t` required (v0.3
  deleted). `protocol/launcher-v0.4.schema.json` — `Launched.t`, `Terminated.t` required
  (v0.3 deleted).
- `runstate/vocabulary/payloads.py` — `t` on the four dataclasses.
- `runstate/worker.py` — the `Heartbeat(...)` and `Stopped(...)` emits gain `t=self._now()`
  (the clock is already in hand). `runstate/launcher.py` — the `Launched`/`Terminated`
  emits gain `t` (both launchers; the reaper's clock for `terminated`).
- `runstate/watcher.py` — seed **three** fields at registration (`last_hb_seq`,
  `last_step`, `last_heartbeat_at`) from the latest heartbeat, with the no-heartbeat →
  `now()` fallback (§5 — the naive one-field seed is a no-op). Keep the arrival clock for
  witnessed beacons. No verdict-basis `reason` added (§9 defers it).
- `runstate/observables.py` — a `last_activity(channel)` fold (§6); the "one
  non-log-derivable input" docstring softens (arrival time is now the *preferred* witness
  clock, not the *only* clock).
- **Tests — the rename ripples widely.** `attached_at` appears across `test_observables.py`,
  `test_memoizer.py`, `test_service_worker.py`, `test_payloads.py`, `test_postgres_channel.py`
  (and fixtures asserting the now-deleted `attached_at=None` path must change); every fixture
  constructing `heartbeat`/`stopped`/`launched`/`terminated` must add `t`. `test_schema.py`
  gains the required-`t` negative cases. **Add the victim-1 regression test the current suite
  lacks:** `observe()` a log whose only heartbeat is old, then `poll()` — it must return
  `presumed_dead` (today, and under a naive one-field seed, it returns `Running`).
- **Migration — two distinct operations, do not conflate:**
  - `started`: **RENAME** `attached_at` → `t` in each body (preserve the value). It must
    **not** be stamped from `created_at` — a fabricated epoch (e.g. a test's
    `attached_at=1000.0`) is precision-load-bearing for `history()`'s time replay, and
    stamping the row's real wall-clock would silently shift the run epoch. Where an old
    `attached_at` was `null` (rare; the reference Worker never wrote null), fall back to the
    row's `created_at`.
  - `heartbeat` / `stopped` / `launched` / `terminated`: **STAMP** `t` from the backend's
    existing `created_at` column (every row has it — verified 1,998/1,998 on a sampled log).
    These are freshness-only readers, so `created_at`'s pre-lock, mildly-non-monotone value
    is sound (an approximate age is the point). This is orthogonal to the launcher-v0.3
    migration (which stamped the envelope `request_id`, done and deleted `6b48886`): a
    different column, no field collision, no ordering hazard.
  - Quiescence-gated, idempotent, converge-over-passes; committed → run → deleted (the
    `lifecycle-v0.3` precedent). `MemoryChannel` has no persistence — a migration non-issue.
- **No envelope change, no substrate op.**

---

## 9. Parked, with named triggers

- **Dating `control.*`** (control-latency: "stop issued at T₁, honored at T₂"). A B-shaped
  extension (a `subscription`-convention bump adding `t`), not a step toward A. Trigger:
  a third-party controller that needs issue-time it did not itself send.
- **`Value.t` required** (the data-plane wall-clock x-axis). Trigger: the viz project's
  data plane. Until then it stays present-nullable.
- **`restore(envelopes)`** (cross-backend log transfer preserving `seq`). Trigger: a
  consumer that needs to re-materialize a log into a different backend through the API.
- **Expose the staleness clock-basis** (witnessed vs seeded, §5). Deferred: the seeded
  reading's error is bounded and conservative, and §4 forbids the only decisions where the
  basis would matter, so no verdict needs it yet. Trigger: a consumer that wants to
  *wait-and-confirm* a cold "dead" before acting on it.

---

## 10. Resolved decisions (2026-07-16)

1. **Naming harmony — HARMONIZE.** `Started.attached_at` → `t`, required non-null;
   `memoizer._epoch` reads the started's `t`, null-branch removed (§3). One name, one
   contract, for one concept.
2. **Two bumps, not one version — INDEPENDENT.** `lifecycle-v0.4` + `launcher-v0.4` land in
   one commit but keep separate numbers: coupling buys nothing (a shared schema fragment
   fights `additionalProperties:false` regardless, §7-C), and independent versioning keeps
   its real property — a consumer implementing only `lifecycle` never tracks `launcher`'s
   churn. Two conventions are two conventions.
3. **The staleness clock-basis — DO NOT expose now** (§5, §9). The seeded reading is a
   bounded, conservative approximation, and §4 forbids the decisions where the
   witnessed-vs-seeded distinction would matter; exposing it is surface without a consumer.
   Parked with a named trigger (§9). *(This reverses the draft's "expose it" lean, on the
   same minimality ground that chose B over A.)*
