# Post-migration audit (mycooc, first end-to-end consumer)

**Surfaced by:** completing the mycooc migration onto runstate (the orchestrator
`run_experiment.py` + the `main.py`/`training.py` worker now use the channel as
their complete coordination interface) and a follow-up adversarial audit of the
library against that consumer's hand-rolled workarounds. The migration is the
first real end-to-end exercise of the worker control plane + the
observer/value-read path, so it's the best available signal for "what's missing
or sharp." One finding (F1) is an empirically-verified **correctness bug**; the
rest are a design gap mycooc actually paid for (F2/F3) plus a cluster of
"every consumer reimplements this" primitive gaps (F5–F8) — those are precisely
the files mycooc was forced to write (`channel_read.py`, `_channel_*` helpers).

Cross-refs to already-tracked items: F1 relates to the run-episodes self-claim +
the "Lazy-launch double-spawn guard (§12.1)"; F3 to "Multi-orchestrator
attribution (§12.7–8)"; F8 to conventions-hygiene's deferred F9 pid disambiguator.

---

## F1 (CRITICAL — correctness bug, VERIFIED) — the `expected_seq` CAS is not atomic under concurrency

**Resolved 2026-06-07 (`BEGIN IMMEDIATE`); fix superseded 2026-06-09.** The
first fix closed the cross-connection race but opened a same-connection one:
its multi-statement BEGIN..COMMIT window made a *shared* handle thread-unsafe —
a concurrent plain `send()` could join the open transaction and be silently
erased by the CAS rollback, and two concurrent CAS sends raised "cannot start a
transaction within a transaction" (the `ThreadLauncher` topology, one handle
shared by the worker thread and the Watcher, hits both; reproduced).
`channel/sqlite.py` now ships four pieces: **(1)** the CAS is ONE guarded
statement — `INSERT … SELECT … WHERE (SELECT COALESCE(MAX(seq),0) FROM log) = ?`,
rowcount-gated — atomic by construction, leaving no transaction state on the
connection; **(2)** a per-instance lock serializes all connection use (the
cpython `sqlite3` binding mis-handles concurrent statement use on one connection
even at threadsafety=3); **(3)** `busy_timeout` exhaustion is disambiguated:
log moved past `expected_seq` ⇒ `None` (provably lost), unmoved ⇒ raise (wedged
writer, indeterminate — synthesizing a loss would leave the run claimed by
*nobody*), which settles follow-on (a) below by design in the *opposite*
direction; **(4)** `__init__` retries the WAL-conversion `SQLITE_BUSY` birth
race, which sqlite exempts from the busy handler. The `Worker.__init__` claim
loop still needs no change. Pinned by five tests in `tests/test_channel.py`:
the multi-handle race (follow-on (b), shipped), the shared-handle serialization
test, wedged-writer, moved-log, and init-retry. Diagnosis kept below.

`channel/sqlite.py` `SqliteChannel.send(..., expected_seq=)`. The check+insert ran
under `with self._conn:` — which with `isolation_level=None` opens **no
transaction at all**: each statement autocommits, so the SELECT and the INSERT
were two independent implicit transactions. Concurrent connections all read the
same `last`, all passed the check, and all `INSERT`ed. The CAS rejected nothing.
(The inline comment claimed "check + INSERT are one atomic transaction" — false.
*Correction 2026-06-09:* this diagnosis originally blamed a **deferred**
transaction; verified wrong — a genuine DEFERRED transaction in WAL admits one
winner and fails the losers' write-upgrade with `SQLITE_BUSY_SNAPSHOT`: broken
differently, but never multi-winner. The failure here was pure autocommit.)

**Verified** with the real `SqliteChannel`: 8 threads each `send(expected_seq=0)`
on a fresh db admitted **2, 1, 2, 4, 2 winners** across 5 trials (a correct CAS
admits exactly 1).

This is the primitive the entire single-episode / single-spawn model rests on
(`Worker.__init__`'s self-claim, run-episodes §3, the §12.1 double-spawn guard).
Two workers can both win the claim for one `run_id`, both resume the same
checkpoint, and interleave-corrupt the value series — the exact corruption the
model exists to prevent. mycooc happens to dodge it (strictly single-dispatch —
the orchestrator runs one worker per `run_id` at a time and waits on
`communicate`, so the claim is only a safety net), but it is silent and
data-corrupting for any concurrent consumer.

**Fix (in runstate, ~1 line):** when `expected_seq is not None`, open the
transaction with `BEGIN IMMEDIATE` (take the write lock *before* the read) rather
than relying on the implicit deferred transaction. Verified this yields exactly
one winner. Two follow-ons: (a) losers can now surface `sqlite3.OperationalError`
(busy) under contention, so the `Worker.__init__` claim loop must treat that as
"retry/lost," not propagate; (b) add a **concurrent multi-connection CAS
conformance test** for both backends (`MemoryChannel` is already correct via its
shared lock; the bug is sqlite-only). The current `tests/test_channel.py` CAS
test is sequential, so the race is entirely unpinned.

## F2 (HIGH — design gap mycooc hit) — a fired `control.stop` is lost if the caller can't act on the single `True`

**Fix specced 2026-06-09:** `../specs/stop-discharge.md` — the stop is re-typed
as a *set of monotone pending predicates*, so the latch falls out with no flag
(monotone conditions ⟹ the decision is a level, not a pulse) and `stop_pending`
is the side-effect-free read. Note: the spec's model *supersedes* fix-sketch (1)
below (a `_stop_fired` flag would patch the symptom while keeping the
`Subscription` mis-typing — see the spec's A1).

`worker.py` `Worker.tick` + `vocabulary/schedule.py` `Subscription.tick`. A stop is
a one-shot `Subscription`: `tick()` returns `True` only on the *first* tick where
the stop fires, then `False` forever (the subscription marks itself expired/fired).
Verified: `control.stop {}` → tick1 `True`, tick2 `False`; `control.stop
{"from":{"step":100}}` → tick@100 `True`, tick@101 `False`, even though `step>=100`
stays true.

**mycooc hit this for real.** A `control.stop` drained during the aligner's
*bootstrap* phase — whose callback return value the bootstrap loop ignores —
wasted the single fire, and the run *completed instead of preempting*. The
workaround was to gate `tick()` to the main loop (`stage >= 0`), which has the
side effect of *also dropping the heartbeat during bootstrap*, because `tick()`
couples three concerns (drain control + beacon heartbeat + report the stop) and
only *returns* the stop decision — so a consumer that can't consume the return
must skip `tick()` entirely.

The root issue: a commanded stop is a **latched fact** ("a stop was commanded"),
but the API models it as an **edge**. An observed-but-unacted edge silently
vanishes.

**Fix (in runstate):**
1. Make a fired stop **latch** — once any stop has fired, `tick()` returns `True`
   on every subsequent tick until the worker stops (track `self._stop_fired`).
2. Expose a side-effect-free predicate `Worker.stop_pending -> bool` (or
   `should_stop`) so a callback-guest consumer (mycooc's bootstrap/aligner
   callbacks) can poll at its own safe point without racing the one-shot.
This also decouples drain+beacon from stop-reporting, so the heartbeat keeps
beaconing through phases where the stop can't yet be acted on — and would let
mycooc **delete its `stage >= 0` gate and restore bootstrap heartbeats**.

## F3 (HIGH) — a later `control.stop` clobbers an earlier still-pending one

**Fix specced 2026-06-09:** `../specs/stop-discharge.md` — pending stops become
a *set* combined by the condition-algebra's `any`-join. NB the spec corrects two
claims below: "earliest commanded stop wins" is ill-posed on the condition
algebra's partial order (OR is the canonical combination), and "the F2 latch
subsumes this" is insufficient (a latch-on-fire over a single slot still loses
an earlier *pending-unfired* stop — the set is required).

`worker.py` `_handle_control`: `self._stop = Subscription(e.body, …)` overwrites any
prior pending stop (last-writer-wins). Verified: orchestrator A sends "stop at
step 5", B later sends "stop at step 10" → the worker forgets step-5 and only
stops at 10. The design claims multi-orchestrator commands all take effect
(§12.7) and the intuitive contract is "earliest commanded stop wins." **Fix:**
keep the earliest-firing pending stop, or simply latch on the first fire of *any*
stop (the F2 latch subsumes this). Ties into "Multi-orchestrator attribution
(§12.7–8)".

## F4 (HIGH — leak) — channels are never closed; `_LaunchProducer` reopens per access

Only `SqliteChannel.close()` exists; nothing in `runstate/` calls it (grep-clean).
`Watcher` (one channel per tracked run), `sweep`, `peek_terminal`/`live_episode`
call sites, and especially `_LaunchProducer.channel` (`memoizer.py` — opens a
**fresh** `SqliteChannel` on *every property access* inside `ensure`'s poll loop)
all leak sqlite connections + WAL fds. A long-lived orchestrator (mycooc's
`--status`, the `ensure` loops) accumulates connections without bound. **Fix:**
make channels context managers and/or cache the connection in `_LaunchProducer`
instead of reopening; give `Watcher`/`sweep` a `close()`. At minimum, document
the ownership/lifecycle contract (who closes what) — it's currently unstated.

## F5 (MED — missing primitive) — no first-class reader for the historical value series

The `Worker` *emits* `value` events (subscription-serviced) and `memoizer.history`
replays a *schedule* over them, but there is no plain reader for the dense value
series. mycooc had to write the whole of `channel_read.py` —
`channel_metrics`/`channel_best_metrics` hand-parsing
`channel.read(topics=["value"])` + `int(body["step"])`/`float(body["value"])` with
same-step dedup. This is the single most obvious "every consumer reimplements it"
gap. **Suggested:** ship `value_series(channel, name=None, *, dedup_by_step=True)
-> {name: {step: value}}`, sharing `history`'s same-step collapse/divergence logic.

## F6 (MED — promote to public) — the progress projection should be public API

`memoizer._progress(channel)` (max step from the dense heartbeat+stopped axis) is
exactly the "how far has this run gotten?" projection an orchestrator needs, but
it's private. mycooc copied it **verbatim** into `_channel_progress` (its
docstring literally says *"Mirrors runstate's memoizer._progress"*). **Suggested:**
promote to `runstate.progress(channel) -> int` and re-export. Same for a public
reuse predicate — mycooc's `_reusable_from_channel` is `peek_terminal is not None
and progress >= min_steps`, a recurring shape.

## F7 (MED — missing primitive) — no "latest event of topic X in the current episode" reader

A cell's channel is reused across dispatches (many `started…stopped` episodes in
one log). `live_episode`/`peek_terminal` are episode-aware internally, but there's
no general "latest `value`/`status`/`started` *in the current episode*" helper.
mycooc hand-rolled it twice — for the live status string (`_channel_live_status`:
`latest("lifecycle.started")` then `read(after=started.seq, topics=[...])`) and for
the pid. It also shipped a **real bug** here first: `_channel_pid` read the
*oldest* `lifecycle.started`, returning a stale (dead/recycled) pid after resume —
caught only in final review, and it sat in the `--stop` liveness/kill path.
**Suggested:** ship `current_episode(channel) -> Started | None` (latest started,
parsed, episode-aware) and/or `latest_in_episode(channel, topic, name=None)`. This
removes the most-copied consumer boilerplate and a whole class of episode-staleness
bugs for the next consumer.

## F8 (MED — stable contract) — `started.handle` pid extraction is an unspecified hand-parsed string

`vocabulary/handle.py` emits `local://{host}/{pid}`; consumers recover the pid by
`handle.rsplit("/", 1)[-1]` (mycooc `_channel_pid`, and runstate's own
`liveness.resolve` re-parses the same way). There is no public
`handle_pid(handle) -> int | None` / structured handle. When the deferred
`?start=T` disambiguator (conventions-hygiene F9) lands, the format changes and
every consumer's `rsplit` silently breaks (pid becomes `"<pid>?start=T"`).
**Fix:** ship `handle_pid(handle)` (and a `Started.pid` accessor) *now* and route
`resolve()` through it, so the format is owned in one place behind a stable
function — *before* the disambiguator forces a breaking change on consumers.

## F9 (LOW — ergonomics) — `await_consumed` rescans all naks each poll

`watcher.py` `await_consumed` does `channel.read(topics=["lifecycle.nak"])` then
filters by `request_id` in Python — re-scanning every nak ever written on each
poll. The substrate already supports `request_ids=[...]`; pass it. Perf/ergonomics,
not correctness.

## F10 (DOCS) — the episode model + the resumable-must-be-preempted discipline are under-documented

- The **consumer-facing** episode rule ("a channel hosts many episodes; always
  read the *latest* `started`, never the oldest; statuses/pids are
  episode-scoped") is real in the code and specced in `docs/specs/run-episodes.md`,
  but appears nowhere a consumer would look (README, design §7). mycooc's stale-pid
  bug (F7) came straight from this gap — surface the rule where consumers read.
- "**A resumable/extendable worker must emit `preempted`, never `completed`, per
  chunk**" (a per-chunk `completed=True` silently truncates a time budget in
  `ensure`) currently lives only in a test docstring (`tests/test_memoizer.py`).
  Promote it into the `ensure` / `Worker.stopped` docstrings.

---

## Top 3 highest-leverage

1. ~~**F1 — `BEGIN IMMEDIATE` on the CAS path** + a concurrent conformance test.~~
   **Done 2026-06-07** (see the F1 section above). Items 2 and 3 are now the top
   pickups.
2. **F2 (+F3) — latch a fired `control.stop` and expose `stop_pending`.** The
   one-shot edge silently loses stops; mycooc already paid with a gating
   workaround that also suppressed heartbeats. Latch + poll-predicate fixes the
   lost-stop, the multi-orchestrator clobber, and the all-or-nothing `tick()`
   coupling in one stroke — and lets mycooc drop its workaround.
3. **F5/F6/F7 — ship `value_series()` / `progress()` / `current_episode()`.**
   Exactly the files mycooc was forced to write (`channel_read.py`,
   `_channel_progress`, `_channel_live_status`/`_channel_pid`) — one around a
   stale-read bug. Promoting them erases the most-duplicated consumer boilerplate
   and removes the episode-staleness footgun for the next adopter.
