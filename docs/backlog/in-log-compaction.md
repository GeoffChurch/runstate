# In-log retention / compaction (design §12.9)

**Status:** DESIGN DELIBERATION (2026-07-16) — NOT CONVERGED; owner-gated (a
**retention-contract change** — it alters what design §4 promises about the log,
so it graduates only through its own spec + adversarial pass). This file
elaborates the open half of design §12.9 ("in-log retention/compaction remains
future work") and the [index](index.md) "GC / retention policy" mirror line. It
**prepares** an owner ruling; it does not make one. No code changes accompany it.

Scope: **in-log** retention (what a *single* run's log keeps). **Home-level**
collection (deleting a whole run's directory) is a separate, already-shipped
recipe — §6.

---

## 1. The pressure, with the repo's own measured numbers

Full retention means a long-lived log grows without bound, and the growth is
dominated by one topic:

- **Heartbeats are ≈ half the envelopes.** The stage-3b probes ran on
  translation-shaped sqlite logs of **10⁶ envelopes, ~50% heartbeats**
  ([visualization-story.md](visualization-story.md) "Scale constraints"). The
  beacon is **exactly one per tick, unconditional** (`worker.py:tick`, design §6
  loop step 3), so a worker that ticks fast and emits values rarely is mostly
  heartbeats on the log.
- **A months-long `serve()` worker accumulates unboundedly.** A service worker
  (`worker.py:serve`) beats every tick for its entire life with no natural end;
  its log is almost entirely superseded heartbeats. This is the sharpest real
  pressure — a training run of N steps is bounded at N; a service is not.
- **But compaction buys the viewer very little, measured.** From
  [visualization-story.md](visualization-story.md): "the topic index already
  skips heartbeats, so stripping them buys plots only **2–8%**. It halves only
  the **full-log scans** (`live_demand`, Worker attach, first event replay).
  In-log GC/compaction is a nice-to-have **~2×** on those paths, **not** a viewer
  prerequisite." The write path (~3.4k appends/s, fsync-bound, flat in N) is
  never the viewer's problem.

So the pressure is **real but modest for reads** (the topic index already makes
heartbeats cheap to skip) and **real and unbounded for storage** (the
months-long service). The case for compaction is a *storage* case, not a
*read-latency* case — which matters for how it is judged.

---

## 2. What full retention is load-bearing FOR (derived honestly)

### 2.1 The initiality property

The per-run log is the **free monoid / initial list-algebra**, and *this holds
only under full retention* ([protocol-algebra.md](protocol-algebra.md) L1;
`CLAUDE.md` rubric). Every read projection (register / queue / counter /
bounded-window / the convention folds) is a **unique fold** of that initial
object — which is exactly why they are *queries*, not primitives.
**"GC/compaction *quotients* the log and breaks initiality"** — protocol-algebra
keeps this as the explicit honesty caveat, and it is why retention-until-cleanup
is the design §4 contract and GC is an eyes-open §12 deferral. Any compaction
proposal is, formally, a decision to quotient the initial object — so the bar is:
prove every shipped fold survives the quotient.

### 2.2 The sharper per-fold analysis

Initiality is the general statement; the operational question is narrower —
**which shipped folds actually read a topic's HISTORY (a `read` over the topic),
versus only its latest (`latest`)?** Only history-reading folds can be broken by
dropping intermediate records. Verified against the tree at 2026-07-16:

**Folds that read HISTORY (a `read(topics=[…])`) — cannot lose intermediate
records of those topics:**

- **The value plane** — `value_series` (`observables.py:_value_points`,
  `read(topics=[Topic.VALUE])`) and `history` (`memoizer.py:history`,
  `read(topics=[Topic.VALUE], name=name)`) need the **full per-`(name, step)`
  series**. (§5: never compactable.)
- **The discharge / answer / boundary folds re-derive over `control.*` +
  `lifecycle.started`/`stopped`/`nak`:**
  - `live_demand` reads `control.subscribe`/`control.unsubscribe`/
    `lifecycle.nak`/`lifecycle.started` (`observables.py:live_demand` — the
    positional answer fold + the episode boundaries).
  - `undischarged_stops` reads `control.stop` after the latest `stopped`
    (`observables.py:undischarged_stops`).
  - The Worker's **birth attach** reads `lifecycle.stopped`/`started`/
    `control.unsubscribe`/`lifecycle.nak` (capped at the claim head —
    `worker.py.__init__`), and `_drain_control` reads `control.>`
    (`worker.py:_drain_control`); `retire` reads the whole tail after the cursor
    (`worker.py:retire`).
  - `await_consumed` reads `lifecycle.nak` after the request seq
    (`watcher.py:await_consumed`); `_launcher_terminal` reads
    `launcher.terminated` (`observables.py:_launcher_terminal`).

**Folds that read only `latest`:** `peek_terminal` (`latest` stopped/started; the
launcher tier reads `terminated` history), `live_episode`, `latest_episode`,
`progress`, `last_activity`.

**The key finding: heartbeats are read ONLY via `latest`.** Every heartbeat read
in the codebase is `channel.latest(Topic.LIFECYCLE_HEARTBEAT)` —
`watcher.py:_seed_beacon` (222), `_note_heartbeat` (406), `await_consumed` (474),
`observables.py:progress` (403), and `observables.py:last_activity` (288). **There
is no `read(topics=[… heartbeat …])` anywhere** (verified by grep over
`runstate/`). No fold needs heartbeat *history* — only the newest beacon's
`step`/`t`/`consumed_seq`. This is what makes heartbeat compaction *look* safe:
the intermediate beacons are, for every fold, dead weight.

**The one honest caveat:** the raw event stream — `Watcher.iter_events` /
`wait(on_event=…)` / `_drain`, which do `read(after=cursor)` over **all** topics
(`watcher.py:_drain`) — *does* replay every heartbeat. A consumer streaming from
`seq 0` (`iter_events`' first drain replays the entire history) would see fewer
heartbeat events after compaction. That is a **behavioral change for a raw-stream
consumer**, not a fold-correctness break — and it is exactly the "first event
replay" full-log scan visualization-story says compaction halves. A spec must
decide whether that behavioral change is acceptable (it is the whole point of
compaction) and document it.

---

## 3. The candidate: heartbeat keep-latest compaction

The obvious candidate, given §2.2: **keep only the latest `lifecycle.heartbeat`
per episode, drop the rest.** No fold reads heartbeat history, so folds are
unaffected; the raw-stream replay shrinks (§2.2 caveat). But it collides head-on
with a **conformance-pinned substrate contract.**

### 3.1 What it breaks: the contiguous-`1..N` `seq` contract

Design §4: `seq` is **"one contiguous, 1-based sequence — exactly `1..N`, no
gaps, across all topics,"** and **the conformance suite pins contiguity on every
backend** (`tests/test_channel.py`). Dropping a heartbeat at `seq=k` leaves a
**gap** at `k`. Consequences:

- **`last_seq() == count` dies.** The memory backend literally returns
  `len(self._log)` as `last_seq` (`memory.py:last_seq`, "seq is contiguous from
  1, so last == count"); Postgres uses `MAX(seq)+1` for the next append. With
  gaps, `last_seq` (the CAS's read half, §4) is no longer the record count, and
  the append arithmetic must change on every backend.
- **The CAS is undisturbed in principle** — it asserts the *head* (`MAX(seq)`),
  which compaction of *interior* records does not move — but any code that
  conflates "head" with "count" (via `last_seq`) must be audited.
- **`pairing-by-seq` itself survives** — a heartbeat is **never a counter-record**
  (it is not in any intro/elim pair; [protocol-algebra.md](protocol-algebra.md)
  L2, and §2.2 confirms nothing pairs against it), so dropping heartbeats cannot
  break the stop-discharge / answer / boundary folds, which pair `control.*` and
  `lifecycle.started`/`stopped`/`nak` — none of which are dropped. **But the
  *contract as written* (contiguous `1..N`) dies**, and that contract is what a
  second implementation is told to reproduce (implementer's guide §2.2), so this
  is a wire-level contract change, not an internal optimization.

### 3.2 The options for the `seq` contract

A spec must pick one, each with a real cost:

- **(a) Relax to strictly-monotonic-with-gaps** and **price every consumer.**
  `seq` stays a total order but no longer counts. Every place that assumes
  contiguity is re-audited: `last_seq`-as-count, the memory backend's
  `len`-as-seq, the head-first capped attach's "capped read == unfiltered read"
  reasoning (`worker.py.__init__`), and the implementer's-guide §2.2 rule. This is
  the most honest but most invasive: it changes what `seq` *means* protocol-wide.
- **(b) Tombstones.** Replace a compacted heartbeat with a marker record at the
  same `seq`, preserving contiguity. Cheaper on the contract (no gap) but adds a
  new record kind the whole stack must skip, and a tombstoned log is barely
  smaller than the original (the row still exists) — it buys storage only if the
  body is truncated, which is a half-measure.
- **(c) Per-`(topic, name)` compaction — design §4's named semantic choice.**
  Design §4 "Read projections" already names this: *"Per-(topic,name) compaction
  is a semantic choice (makes the register the retained object), not free GC —
  deferred, chosen with eyes open."* This reframes compaction not as "drop old
  rows" but as "the log for this `(topic, name)` **is** a register — it retains
  only the latest." For `lifecycle.heartbeat` (which every fold reads via
  `latest` anyway) this is a *coherent* semantics: the heartbeat topic becomes a
  register, and `seq` contiguity is redefined per the new semantics rather than
  violated. This is the option most aligned with the design's own framing — but it
  is a **semantic** change (the log stops being a pure append-only history for
  that topic), not a free space optimization, and design §4 flags it as
  "deferred, chosen with eyes open" precisely because of that.

---

## 4. Why the value plane must NEVER be compacted

Compaction of `value` is off the table, categorically:

- **`value_series` and `history` need the full per-`(name, step)` series** (§2.2).
  Dropping value records loses trajectory points a chart or a reuse read needs.
- **Log-as-cache: `ensure`/`history` correctness depends on it.** `ensure` serves
  the logged prefix on a cache hit and resumes-to-extend on a miss
  (`memoizer.py:ensure`); `history` replays the schedule over the logged points
  (`memoizer.py:history`). The log **is** the cache; compacting it is discarding
  cached results, defeating reuse-by-`run_id` (the whole memoizer value
  proposition, [`../specs/memoizer.md`](../specs/memoizer.md)).
- Even the **take-the-latest** collapse of a resumed overlap
  ([value-plane-divergence-resolution.md](value-plane-divergence-resolution.md))
  is a *read-time* fold that keeps **both** records on the append-only log — it
  never mutates the log. The value plane's whole robustness story assumes the
  records stay.

So any compaction spec is **heartbeat-only** (or, at most, other
read-via-`latest`-only lifecycle topics — but heartbeats are the mass); the value
plane is retained in full, always.

---

## 5. Relationship to home-level GC (orthogonal — already recipe'd)

Do not conflate this with **home-level** collection, which is a **separate,
already-shipped recipe**: [`../specs/store.md`](../specs/store.md) Recipe 3 —
**pointer-rooted mark-and-sweep, gated on `live_episode`, selective-prune
default** — deletes *whole runs' directories*, and **never truncates a live log**
(design §12.9; the GC's irreversible deletion is gated on a record-plane fact +
the `last_activity` age, observer-clock §6). That operates *between* runs (which
runs to keep); in-log compaction operates *within* one run (which records of one
run to keep). They are orthogonal:

- Home-level GC is **shipped** and needs no retention-contract change (it deletes
  at the run boundary, where retention already ends).
- In-log compaction is **this document** and *does* need a retention-contract
  change (it drops records mid-log).

A spec must not let one borrow the other's safety argument.

---

## 6. Open questions for the owner

Nothing below is ruled.

1. **Is the storage pressure real enough to pay for a retention-contract
   change?** Reads barely benefit (2–8%; the topic index already skips
   heartbeats). The case is storage, and specifically the **unbounded `serve()`
   worker** — is that the concrete trigger, or is "rotate/cap a service's log
   externally" a good-enough non-protocol answer?
2. **Which `seq` option (§3.2)?** (a) gaps + price every consumer; (b)
   tombstones; (c) per-`(topic, name)` register semantics (design §4's named
   choice). (c) is the most design-aligned and the most honest about being a
   *semantic* change; (a) is the most invasive; (b) is a half-measure. This is
   the central decision.
3. **Is the raw-stream replay change acceptable?** `iter_events` from `seq 0`
   would replay fewer heartbeats (§2.2 caveat). Confirm no consumer depends on
   full heartbeat replay (none in-repo does), and document the behavioral change.
4. **What does a second implementation have to reproduce?** Compaction changes
   the wire-level `seq` contract the [implementer's guide](../implementers-guide.md)
   §2.2 tells other-language implementers to honor. Any spec must update that
   contract and the conformance suite in lockstep — a contiguity relaxation is a
   protocol change, not a Python-backend detail.
5. **Heartbeat-only, forever?** Confirm the value plane is categorically excluded
   (§4) and that the scope is heartbeats (+ possibly other read-via-`latest`-only
   lifecycle topics), never anything a history fold reads.

## Revival trigger

Revisit when a **concrete unbounded-log deployment** (a months-long `serve()`
worker whose log outgrows its store, or a fleet whose aggregate log size is a
real operational cost) makes the storage case, **and** external log rotation is
not a good-enough answer. Until then the design §4 contract holds: retention is
full within a log, no in-log GC — the precondition `peek_terminal` / resume /
`ensure` rely on.
