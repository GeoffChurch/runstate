# Spec: the observer clock — date the beacon

**Status:** CONVERGED on **Proposal B** (2026-07-16). Draft 1 proposed a wall-clock `t`
on every **envelope** (Proposal A); three independent adversaries refuted its
justification (not the field — the *reasons*), and the owner ruled **B over A on
minimality + layer**: the clock is a semantic, run-life concern, so it belongs in the
**opt-in conventions**, not the opinion-free substrate. This is the implementable spec.
Item 1 of `../backlog/third-party-observer.md`. The full A-vs-B judgment and the
adversarial findings are preserved in git history (commits `3ce77b1`, `3ce9eab`).

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
   under WAL. "When did this home last change?" = the newest dated record's time.

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

**`lifecycle-v0.4`** (from v0.3):
- `Heartbeat` → `{step, consumed_seq, t}` — `t` **required**. A beacon with no time cannot
  do its one job, and the emitter always knows *now*.
- `Stopped` → `{completed, error, final_step, t}` — `t` **required**. The death time; the
  "when did it finish" and GC-age answer for a cleanly-stopped run.
- `Started` — **unchanged**. `attached_at` already is its dated clock (and the run epoch,
  `memoizer._epoch`). *(Naming harmony — `attached_at` vs `t` — is §10 Q1.)*
- `Nak` — **unchanged**, undated. Not read by the observer plane; keep minimal.

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

## 5. The `Watcher`: keep `last_heartbeat_at`

Do **not** replace the arrival clock with `now() − t`. `beacon_age = now() − last_heartbeat_at`
is **skew-immune** — both terms are the observer's own clock — and it is the correct,
preferred measurement for a beacon the observer *witnessed*. Replacing it wholesale
reintroduces the exact failure used to kill the monotone clamp (a worker clock 10 min
fast ⟹ negative age ⟹ maximally fresh forever ⟹ a hang never detected).

The record's `t` does one new job: **seed the prefix the observer did not witness.** On
`observe()`/registration, initialize `last_heartbeat_at` from `t(latest heartbeat)` rather
than `now()`. Then a 21-day-old beacon reads its true age immediately (fixing victim 1),
while beacons that arrive *during* watching are still timed skew-immune by arrival. Surface
which basis produced a verdict (a distinct `reason`) so a consumer sees whether it stands
on a cross-clock assumption.

---

## 6. `freshness` needs no new op

`last_activity(channel)` = the newest `t` among `latest(heartbeat)`, `latest(stopped)`,
`latest(terminated)` — a handful of O(1) `latest()` reads, no full scan, no sixth
substrate op. This is exactly what mycooc reached past the API to compute, now inside the
observer plane and ~30× faster than its `SELECT max(created_at)` full scan.

Ship it **only** as `t` of the newest dated record — never `max(t)` over the whole log:
one record from a fast clock would pin `max(t)` into the future forever, and this feeds the
GC's irreversible action. §4's irreversible-action rule is its guard.

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
- `runstate/watcher.py` — seed `last_heartbeat_at` from `t(latest heartbeat)`; keep the
  arrival clock for witnessed beacons; add the verdict-basis `reason`.
- `runstate/observables.py` — a `last_activity(channel)` fold (§6); the "one
  non-log-derivable input" docstring softens (arrival time is now the *preferred* witness
  clock, not the *only* clock).
- `tests/test_schema.py` — the emitted `heartbeat`/`stopped`/`launched`/`terminated`
  bodies must now carry `t`; add the required-field negative cases.
- **Migration:** old logs' beacon/terminal bodies have no `t`. Per the no-compat doctrine,
  a one-time offline pass stamps them from the backend's existing `created_at` column
  (which every row already has — verified: 1,998/1,998 on a sampled log) — the
  `lifecycle-v0.3` precedent, committed → run → deleted. Quiescence-gated, idempotent.
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

---

## 10. Open questions

1. **Naming harmony.** `Started.attached_at` vs the new `t` on its sibling lifecycle
   records: one concept ("this record's wall-clock"), two names. No-warts leans toward
   renaming `attached_at` → `t` and letting `memoizer._epoch` read the started's `t` —
   but it touches `_epoch` and the run-epoch story. Harmonize in this bump, or leave
   `attached_at` as the named epoch anchor? *(Lean: harmonize — one name for one concept.)*
2. **Two convention bumps, or batch them?** `lifecycle-v0.4` + `launcher-v0.4` land
   together and for one reason. Doctrine says independent timelines; the owner is fine
   coupling a closed set. Cosmetic either way — but pick deliberately.
3. **The `reason` vocabulary for the witnessed-vs-seeded staleness distinction** (§5):
   does a consumer actually need to see which clock a `presumed_dead` stood on, or is it
   internal to the `Watcher`? *(Lean: expose it — a cross-clock verdict is exactly the one
   a careful consumer wants to double-check.)*
