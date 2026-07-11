# Spec: stop-discharge (`control.stop` is a request/outcome pair)

**Status:** SHIPPED 2026-06-09 (specced and implemented the same day; kept as
the record of what was built — see `runstate/worker.py` and the S1–S4 /
crash-edge / pre-staged tests in `tests/test_worker.py`; prose folded into
design §6/§7 rev 6). Surfaced by
`tests/test_worker.py::test_resumed_episode_ignores_prior_episodes_stop`
(committed RED) plus the mycooc audit's F2/F3
(`../backlog/mycooc-migration-audit.md`). Converged via three independent design
reviews run in parallel — a clean-room design-space enumeration, an
ownership-boundary audit, and a distributed-systems prior-art survey — **all
three of which independently arrived at the same fix** (the discharge fold
below), and two of which independently *refuted* the first-proposed fix
(episode-start fencing, A2 below).

## The problem: three symptoms, one type error

1. **Cross-episode replay (the failing test).** The worker drains `control.>`
   from `seq 0` on every episode (`worker.py` sets `_cursor = 0`). A
   `control.stop` that halted episode 1 is re-drained by a resumed episode 2,
   re-armed, and honored again — the resume dies at its first step.
2. **F2 — the lost edge (mycooc paid).** Within an episode the stop decision is
   returned by `tick()` exactly once (a one-shot `Subscription` consumes its own
   fire). A caller that can't act on that single `True` — a callback-guest whose
   host loop ignores returns — loses the stop silently.
3. **F3 — the clobber.** `self._stop = Subscription(...)` is a single slot;
   a later stop overwrites an earlier still-pending one (last-writer-wins).

These are one bug. `control.stop` was implemented by **borrowing the
`Subscription` type** (it had a convenient `from`-gate), which mis-types it on
every axis: the *message* (a command-fact with a recorded outcome) is folded as
standing state re-derived from 0 (symptom 1); the *decision* (a monotone level)
is reported as a consumed-once pulse (symptom 2); the *storage* (a set of
pending requests) is a single mutable slot (symptom 3).

There is **no ownership inversion** (the boundary audit's verdict): the worker
is the right owner of control-consumption semantics — the same placement logic
as the run-episodes self-claim (`run-episodes.md` Decision 3: the rule must bind
ray / submitit / bare-`python` spawns, so it lives in the worker's protocol
behavior, not in optional Layer-3 helpers). The smell is an *abdication within*
the rightful owner: stop never got its own ontology, so its scope was decided by
an accident of mechanism.

## The model

> **A `control.stop` is the *request half* of a request/outcome pair.** It is a
> durable log fact, **pending from its append until the next
> `lifecycle.stopped` that follows it by `seq`** — the record the design already
> designates as the stop's *effect* (design §7: *"`control.stop` 'landed' = the
> watermark; its **effect** = `lifecycle.stopped`; there is no separate stop
> receipt"*). Any `stopped` discharges **every** pending stop at once (broadcast
> answer, matching the `stopped` record's own broadcast nature). A discharged
> stop is *history* — provenance, never again input.

Within an episode, the pending stops form a **set of monotone predicates**:

- the worker holds `(request_id, from_, registered_at)` per pending stop —
  no `Subscription`, no fire-count, no one-shot state;
- the tick decision is `any(satisfied(from_, …))` over the set. Conditions are
  monotone ("once true, stays true" — `schedule.py`), so the decision **latches
  by inheritance**, with no flag: `tick()` returns `True` on every safe point
  from the first trigger until the worker stops. An ignored return is recovered
  at the next consulted safe point.
- combination is the algebra's own **`any`-join** ("earliest wins" is not even
  well-defined on the condition algebra's partial order; OR is its join and
  needs no new vocabulary).
- a side-effect-free **`Worker.stop_pending`** property exposes the same
  predicate (evaluated at the last yielded step + now), so a callback-guest
  polls at its own safe point without consuming anything. This decouples
  stop-reporting from the drain/beacon pump — mycooc deletes its `stage >= 0`
  gate and recovers bootstrap heartbeats.

**The unifying drain rule** (one rule, both verbs — supersedes the earlier
"state-vs-event" framing): *every control fact is live until its
counter-record, and the worker folds the **whole** log applying
counter-records.* Subscribe's counter-record is `unsubscribe` (an explicit
rescind); stop's counter-record is the next `stopped` (a discharge). Both
re-derive from `seq 0` across episodes; subscriptions persist because their
*(forward note, 2026-06-10: this clause was incomplete until
`specs/service-worker.md` — natural expiry left no rescission on the log, so
expired leases resurrected per episode. The worker now writes expiry
counter-records, the counter-record set is {unsubscribe, nak}, and the
pairing is positional — design §7's pairing-by-`seq` rule.)*
rescissions are on the log, and stops expire because their discharges are too.
The subscribe/stop asymmetry (no `un-stop` verb) is principled: **a stop is
self-clearing — its receipt is the very thing it requests.**

## Why this is the canonical form (rubric)

- **Extracted, not invented.** The discharge record was designated in design §7
  a month before this bug; the code was reading half the pair.
- **Mirror of a shipped rule.** Episode-aware `peek_terminal`: *a `stopped` is
  terminal iff no `started` follows it by `seq`*. The discharge rule is the same
  follow-by-`seq` fold on the opposite pair. One derivation shape, two
  directions.
- **Independence / net deletion.** No new vocabulary, no schema change, no new
  record; the `Subscription`-for-stop machinery is *deleted* (stops have no
  `every`/`until` — the schema already rejects them — so all that type ever
  provided was `satisfied(from)`).
- **Orthogonality.** Subscribe = rescinded standing configuration; stop =
  discharged command-fact. Neither borrows the other's machinery.
- **Serendipity (the signature it's right).** The F2 latch and the F3 OR-join
  fall out of the re-typing with zero added state; and B′'s "commandedness is
  recoverable from the log" becomes *precise*: the commanding stop(s) for a
  given `stopped` = exactly the pending set at its `seq` — the same fold read
  from the other side.

## Semantics (scenario matrix)

| scenario | today | this spec |
|---|---|---|
| S1: stop fires mid-episode; host loop ignores `tick()`'s return once | lost forever | honored at the next safe point (level, not pulse) |
| S2: stop sent while the run is **down** (between episodes) | honored by ep N+1 **and re-honored by every later episode** (poisoned) | honored **exactly once** — ep N+1 stops cleanly at its first safe point (the "blip"); the blip's own `stopped` discharges it; ep N+2 runs free |
| S3: stop honored by ep1; ep2 resumes (the failing test) | ep2 dies at its first step | discharged by ep1's `stopped`; ep2 runs |
| S4: two pending stops with different `from`s | later clobbers earlier | OR-join — first satisfied condition stops the run; the resulting `stopped` discharges both |
| pre-staged stop (sent before the worker attaches — the test idiom) | honored | honored (pending: no `stopped` follows it) |
| crash edge: ep1 *drains* a conditional stop, crashes before honoring it | n/a (re-drained anyway) | still pending (no `stopped` followed) → resumed episode re-arms it. At-least-once toward an idempotent effect (halt + emit `stopped`), which converges |

**The S2 "blip" is a deliberate semantic choice, surfaced loudly:** a stop sent
while no episode is live is *not* dropped — it is answered by the next episode
immediately and exactly once. Rationale: the durable channel's defining idiom is
addressing a run before any worker exists (pre-staged subscriptions rely on it);
§12.7 promises every orchestrator's command takes effect; and silent drops are
the failure mode F2 already taught us to fear. The stop-vs-relaunch-target
tension resolves *loudly and exactly once*: the stop wins once (it halts the
episode that drains it), the relaunch wins thereafter. Verified compatible with
`ensure`'s re-drive loop (the blip is a `preempted` terminal that advances
progress by one heartbeat, so the no-progress guard doesn't trip and the next
relaunch extends freely). The policy "don't auto-extend a run a human commanded
to stop" stays where it belongs — the relaunch decider (`ensure`-level, a log
read; see `../backlog/ensure-redrive-recoverable-terminations.md`) — not in the
worker's drain semantics.

## Where the alternatives fall short

**A1 — status quo plus a `_stop_fired` latch flag (the audit-F2 sketch).**
Fixes S1 only. Keeps the `Subscription` mis-typing and the single slot, so S3
still kills resumes and S4 still clobbers (latch-on-fire doesn't protect a
pending-unfired earlier stop). Patches the symptom the consumer happened to hit.

**A2 — episode-start fencing: react only to stops with `seq > my own
`started.seq`` (the first-proposed fix — REFUTED).** Looks like the obvious
cursor rule, and is wrong twice over. (a) It breaks the pre-staged idiom: in the
failing test itself the stop (seq 1) precedes ep1's `started` (seq 2) — the
worker CAS-claims *after* the orchestrator sends — so ep1 would ignore the stop
it must honor; `test_control_stop_now` / `test_control_stop_at_step` break the
same way. (b) The repaired variant ("exempt stops addressed to *my* epoch" /
void-by-intervening-`started`) silently drops a stop sent while idle and a stop
orphaned by a crash — violating §12.7 — and still fails the general
mid-episode-S3 case unless it adds the discharge clause, at which point it *is*
the discharge fold plus a redundant conjunct. Prior art names the category
error: fencing (Chubby sequencers, Kafka producer epochs, Raft terms) answers
**"who may act,"** but this problem is **"has this intent been served."**

**A3 — correlated acknowledgment: `stopped` carries the discharged stop's
`request_id` (or a new `lifecycle.ack`).** Two schema version bumps
(`additionalProperties: false`), `request_id` becomes mandatory on stops (a
wire-compat break), and it re-litigates two settled decisions — §7's "there is
no separate stop receipt" and B′'s removal of `Stopped.reason` in favor of log
juxtaposition. Worse, per-id discharge gives the *non-firing* stop of an S4 pair
survivor semantics — it haunts the resume — unless `stopped` carries the whole
pending list, at which point the ids are decorative and it collapses to the
broadcast discharge. Under full retention, `seq`-juxtaposition already encodes
the pairing; the explicit field fails Independence.

**A4 — sender-declared scope or TTL on the stop body (`scope:
"episode"|"run"`, `expires_at`).** Hands the decision to the party with the
*least* knowledge: episodes are deliberately implicit (`run-episodes.md`
Decision 1 — there is no episode-id to address), so the sender would need a
racy read to learn an identity the model refuses to reify. The default value
decides the semantics anyway — the question must still be answered once, now
with a footgun knob attached. A TTL'd stop is the `until`-gates-the-stop footgun
the StopTrigger schema already rejects, re-spelled; and it makes the fold's
result depend on *when* you evaluate it — timing-dependent semantics in a system
whose verdicts are otherwise pure log folds (cf. §8 deliberately avoiding TTL'd
liveness leases).

**A5 — persist the worker's control cursor across episodes (resume from the
last heartbeat's `consumed_seq`).** The watermark is the wrong boundary:
`consumed_seq` means **registered** ("seen"), not **answered** (§6 defines it as
the registration watermark). A conditional stop drained at step 5 advances the
watermark immediately; if the episode crashes before the trigger, the resumed
episode skips it — an unanswered command silently dropped (the crash edge above,
inverted). It also skips pre-episode *subscriptions* unless the cursor is split
per verb-class — which is the discharge fold wearing cursor clothing. Cursor
persistence stays what §12.5 says it is: a read-efficiency optimization,
orthogonal to correctness once the fold is right.

**A6 — relauncher hygiene: `ensure`/`relaunch_if_needed` sanitize stale intent
before relaunching.** Wrong layer, same shape run-episodes Decision 3 already
litigated for the single-spawn guard: an optional Layer-3 helper cannot bind
foreign spawners (ray / submitit / bare `python`), so any worker relaunched
outside our helpers still dies at step 1 — the failing test exercises a bare
`Worker` and stays red by construction. Also nothing *can* sanitize an
append-only log.

**A7 — a `control.unstop` retraction verb.** Adds a basis vector to retract
something that is not standing state; the discharge fold subsumes it (the stop
self-clears via the very `stopped` it requests). Fails Independence and
"helpers earn their place." (If a durable "never run again" is ever wanted,
that is a *hold/park* concept — a different convention, or the caller's
relaunch policy — not a stop that outlives its answer. The Store this
sentence once deferred to dissolved (`specs/store.md`); the named home is
now the cell-local `.skip` policy file / the caller's relaunch policy.)

## Implementation

Two near-independent changes in `runstate/worker.py`, no wire change:

1. **The discharge floor (fixes S3, S2-exactly-once).** `Worker.__init__`
   already reads the whole log for the CAS claim; from that same read, record
   `self._discharge_floor = max(seq of lifecycle.stopped envelopes, default 0)`
   — zero extra I/O, exact at claim time (the CAS serializes attach against any
   concurrent append). The same-read fusion generalizes: the positional answer
   fold (`specs/service-worker.md`) and the episode-boundary list
   (`specs/time-lease-boundary.md`) are computed from that **same read** the
   claim CAS is issued against — which is what makes all three exact. In
   `_handle_control`'s `control.stop` branch, first:
   `if e.seq < self._discharge_floor: return` — **silently**, before
   validation/nak (a discharged-but-malformed stop was already naked by its own
   era's worker; and "already answered" is not a refusal — the nak `reason`
   enum (`malformed`/`unsatisfiable`/`unsupported`) rightly has no word for it).
   *(2026-07-11: the rule gained its public observer home,
   `observables.undischarged_stops` — the fold a status surface or dispatch
   gate reads; pending ≠ due and naked-stop over-reporting documented there.)*
2. **The re-typing (fixes S1, S4).** Delete the `Subscription`-for-stop
   machinery; `self._stop` (slot) → `self._stops` (list of
   `(request_id, from_, registered_at)`); the tick decision and the new
   `stop_pending` property are `any(from_ is None or satisfied(from_,
   step=step, time_seconds=now - registered_at, count=0) for …)`. Validation
   (nak on `every`/`until`, malformed `from`, unsatisfiable) is unchanged.

Note on the time axis: a time-keyed `from` (`{time_seconds: 60}`) re-anchors
`registered_at` at each episode's drain — the worker's time coordinate is
seconds-since-registration and episodes re-register. Documented, accepted
(step-keyed conditions, the common case, are run-absolute and unaffected).
*(Forward note, 2026-06-11: this acceptance now applies to STOPS ONLY —
time-referencing SUBSCRIBES no longer re-anchor indefinitely; they are
episode-scoped, discharged by the next episode boundary
(`specs/time-lease-boundary.md`). Stops deliberately keep the re-anchor:
at-least-once toward an idempotent effect is their spec'd posture, and no
relaunch flap is reachable through them.)*

## Deliverables

- **worker:** the discharge floor + the pending-set re-typing +
  `Worker.stop_pending`.
- **tests:** see TDD targets below.
- **docs:** the request/outcome contract + the unifying drain rule into design
  §6/§7 (beside its mirror, episode-aware terminality) and
  `specs/run-episodes.md`; close the `backlog/run-episodes.md` control-cursor
  open question (resolved by this spec; the "react after the worker's cursor"
  sketch there is A2/A5 — refuted); update the mycooc audit F2/F3 entries and
  the synergy-map Cluster-3 line to point here.

## Non-goals

- A standing "never run again" / hold verb (caller's relaunch policy; cf. A7).
- Worker control-cursor persistence (§12.5 — an efficiency item, untouched).
- Any schema/wire change. The substrate and all convention bodies are
  unchanged; this is convention-layer *read semantics*, keyed on envelope
  fields (`topic`, `seq`) only.

## Tests (TDD targets)

- **S3 (exists, RED):** `test_resumed_episode_ignores_prior_episodes_stop`.
- **S1 latch:** stop fires at tick *k*; the return is ignored; `tick(k+1)` is
  still `True`; `stop_pending` is `True` throughout and consumes nothing.
- **S4 OR-join:** two stops (`from step 10`, `from step 5`) → fires at 5; both
  discharged by the resulting `stopped` (a resume runs free).
- **S2 exactly-once:** stop sent between episodes → next episode stops at its
  first safe point (`preempted`, progress advances); the episode after runs to
  target.
- **Pre-staged regression guard:** the existing `test_control_stop_now` /
  `test_control_stop_at_step` (stop sent before attach is honored by ep1) must
  stay green — they are the tests that refuted A2.
- **Crash edge:** ep1 drains a `from step 100` stop, "crashes" (no `stopped`)
  at step 50; the resumed episode re-arms it and stops at 100.
