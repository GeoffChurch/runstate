# Spec: episode-scoped time-leases (the boundary is the counter-record)

**Status:** pipeline cleared 2026-06-11 (adversarial attack:
survives-with-amendments, all four folded — pop-then-skip, the ≤2 bound,
zero-fire-void documented, the purity amendment; consistency sweep: 15
fold-backs catalogued below + the barrier finding). Implementing. Supersedes
the ghost-lease "flap bound" deliberation (backoff/give-up/cadence-knob all
rejected as waker-side compensation for a log gap) and amends
`specs/service-worker.md`'s bounded-hysteresis row and its recorded
lazy-launch constraint.

## The disease (why backoff felt janky)

Every other piece of standing protocol state re-derives from the log across
episodes — subscriptions, stops, answers, the discharge floor. One does not:
**a time-lease's elapsed countdown** lives only in the worker's memory, dies
with it, and resurrects at zero in the next episode (the "re-anchor",
documented in stop-discharge's time-axis note and accepted at the time). The
ghost-lease relaunch loop is that asymmetry biting: a dead client's lease can
be re-anchored forever by a waker that keeps relaunching workers that keep
dying young. Any waker-side policy (backoff) treats the symptom from the
wrong layer.

## The rule

> **A time-referencing registration is a contract with one living episode.**
> A `control.subscribe` whose schedule references the time axis
> (`time_seconds` anywhere in `from`/`every`/`until`) is **discharged by the
> next episode boundary after it** — pairing-by-`seq`'s fourth instance, and
> like the stop discharge, **the counter-record already exists** (the
> boundary `lifecycle.started`): nothing new is written, the worker simply
> skips a boundary-voided lease at drain, silently (already-answered is not a
> refusal — the same posture as a discharged stop), **except that the skip
> still rescinds its same-`request_id` predecessor (pop-then-skip)**. The
> discharge-floor mirror is sound for stops (a *set*) but not for subscribes
> (per-id last-write-wins *slots*): a voided subscribe behaves as
> "registered, then instantly voided" — the slot ends empty — else a
> superseded earlier subscribe (e.g. an unbounded step-sub the client had
> tightened with a time-leased replacement) would resurrect on re-drain,
> pinning the worker forever while `live_demand` reads zero.

Precisely, two equivalent forms:

- **Worker form (at drain):** a time-referencing subscribe at seq *s* is
  voided iff a `lifecycle.started` **other than the draining episode's own**
  follows *s*. (Equivalently: ∃ `started` with `s < b < own_started.seq` —
  the drainer's own `started` is the latest, so any other later `started`
  lies strictly between.)
- **Observer form (`live_demand`, the waker):** voided iff a `started` lies
  **strictly between** *s* and the latest `started`'s seq. ("Live" = the
  latest episode is still this lease's *first possible drainer*.) The two
  forms agree because a live drainer is always the latest episode.

Pure step/count schedules are untouched — they are run-absolute and persist
across episodes exactly as today (`test_relaunch_extends_one_series`). A
schedule containing *any* time atom is episode-scoped *in toto*: a time
atom's meaning (seconds since registration) cannot be honestly reconstructed
across a boundary, and partially reconstructing the step arms of a mixed
schedule would silently change its meaning — blunt-but-crisp wins.

## What it buys

- **The ghost terminates by construction: at most TWO relaunches, no
  policy.** The exact bound: a lease can be re-anchored at most once (into
  its *first possible drainer* — the first episode whose `started` follows
  it), and is voided by the boundary after that; so a waker acting on fresh
  `live_demand` reads relaunches a dead lease at most twice (one re-anchored
  serve + one voiding visit), and **at most once if any boundary already
  follows the lease**. Walkthrough of the worst case — lease lands at seq *s*
  *during* episode K (K's `started` *precedes* it, so K's boundary does not
  count against it); K dies young. `live_demand` counts it (no `started`
  strictly after *s* yet) → relaunch #1: K+1 registers it fresh (the one
  re-anchor), dies young. Relaunch #2: K+2 sees K+1's `started` between *s*
  and its own → pop-then-skip, retires. From K+1's `started` onward the
  observer form already excludes it, so no further relaunch fires. Backoff,
  give-up rules, and cadence knobs are all deleted from the waker design.
- **The re-anchor becomes bounded and principled:** at most once, into the
  first-possible-drainer — instead of indefinitely.
- **The lazy-launch spec loses its hardest input** — `service-worker.md`'s
  recorded constraint "the decider must bound its own relaunch cadence" is
  void; the waker needs no flap policy at all (the tell that the fix is at
  the right depth).

## Who pays

- A renewing client (the documented lease discipline) notices nothing — its
  next renewal is a fresh subscribe, live for the new episode.
- A non-renewing long-lease client ("keep alive an hour, no renewals") is cut
  off by any episode boundary (crash, extend, blip) and must resubscribe.
  Today that client gets the *silently wrong* opposite — a fresh full
  countdown per boundary, so a 60 s lease can last hours. The rule replaces
  unpredictable generosity with a crisp, log-readable answer. (No-warts: this
  deletes the documented-and-shrugged-at re-anchor squishiness.)
- **A time-lease can be voided with ZERO fires** — if consecutive episodes
  die between birth and first drain, nobody ever evaluates it before the
  boundary rule answers it. Stated plainly: **acceptance ≠ will-serve**; the
  rule guarantees a drain *attempt* only if some episode survives birth →
  drain, and the client's detection mechanism is its own renewal cadence (a
  renewing client is never stranded — each renewal is a fresh latest same-id
  subscribe with no boundary after it yet, registered cleanly by every next
  episode no matter how many die young).
- `await_consumed` nuance, stated honestly: a voided lease was *processed*
  (the watermark passes it; no nak), so `await_consumed` reports acceptance —
  true at drain time, and per the above, not a service guarantee. The lease's
  *lifecycle* is read where it lives: `live_demand` / the records. No
  codomain change.

## Scope notes

- **Stops are deliberately excluded.** A time-keyed `control.stop` still
  re-anchors on a crash-resume (stop-discharge's crash-edge: a drained,
  unanswered stop re-arms — at-least-once toward an idempotent effect). Stops
  pin nothing (no flap exists); their at-least-once is the *spec'd* behavior;
  and a stop's discharge already has its own counter-record (`stopped`). If
  the asymmetry ever bites, the same boundary rule extends — recorded, not
  built.
- No schema or wire change; no new records. This is drain/fold *semantics*:
  `worker._handle_control` (the pop-then-skip clause), `observables.live_demand`
  (the observer form), `schedule.references_time` (the one shared predicate:
  a `time_seconds` atom anywhere in `from`/`every`/`until`; an unparseable
  schedule is NOT time-referencing — the worker naks it, which answers it),
  and the `__init__` read additionally retains the `started` seqs it already
  fetches.
- **`live_demand` loses one purity stripe, honestly:** it must peek at
  subscribe bodies for the time-atom check, so service-worker.md's
  "envelope-level fold, body untouched" claim is amended (it remains
  value-blind — it reads schedule *shape*, never payloads).
- Doc steering (the A5 guidance): a bound meant to survive episodes is
  spelled `until: {step: N}` (run-absolute); **any time atom makes the whole
  registration a lease** — degenerate cases included (`{from:
  {time_seconds: 0}}`, huge time-`until`s) — blunt-but-crisp, no per-atom
  carve-outs. Likewise **`Watcher.broadcast` barriers should be step-keyed**:
  a time-keyed barrier subscription on a run that resumes is boundary-voided
  with no record — the fifth never-fire cause, whose handler is the boundary
  `started` itself — so a capless pure-sync would otherwise wait on a healthy
  run forever (design §9 gains the cause; a boundary-aware re-broadcasting
  Watcher is a backlog note, not this spec). And "anticipatory warmth" in
  service-worker.md becomes honest **renewed** periodic demand — standing
  warmth without renewal was the immortal-pin smell all along.
- **One predicate, one home:** the worker form and the observer form share a
  single voided-check (in `observables`, imported by the worker — the F7
  lesson, applied preemptively); the spec's worker/observer agreement test is
  mandatory. The third time-anchoring in the corpus — `memoizer.history()`
  replays time atoms run-epoch-anchored — is named in the backlog
  (time-axis unification) rather than touched here.

## Docs deliverables (the consistency sweep's fold list)

service-worker.md: the `live_demand` purity claim amended; the "answered by
exactly one of the two" rule gains the boundary forward-note; the
bounded-hysteresis scenario row → re-anchor ≤1 then voided; the Non-goals
relaunch-cadence constraint deleted (stepless-`ensure` survives alone); the
warmth recipe → renewed demand. design-v0.2.md: §6 loop step 1 gains
pop-then-skip; the never-fire count → five (the fifth's handler is the
boundary `started`, recordless) + the acceptance≠will-serve nuance; §7's
pairing instances → four; §7 lifelines crash-expiry qualified; §9 barrier
steering (step-keyed); §12.1's first decider constraint deleted; §12.5's
replay bound now global; rev 9. stop-discharge.md: forward-note on the
time-axis paragraph (subscribes no longer re-anchor; stops deliberately
still do). overview.md: the pairing paragraph gains instance four + the
nuance. protocol-algebra.md: the addendum's instance list + the time-sub's
second eliminator. tests/test_worker.py S3-docstring: qualify "carries
across episodes" to non-time schedules.
test_service_worker.py::test_resumed_episode_does_not_resurrect_an_expired_lease:
re-keyed to `until: {count: 1}` so it keeps isolating the answer fold (the
boundary rule would mask it). backlog: Watcher boundary-aware re-broadcast;
time-axis unification (three anchorings: history run-epoch, subs
episode-scoped, stops re-anchored). CLAUDE.md post-implementation.
specs/run-episodes.md "re-derives standing subscriptions" → FOLD-LATER
qualifier.

## Tests (TDD targets; both backends)

- Founding idiom regression: a pre-staged time-lease is registered by
  episode 1 (no boundary between them).
- Boundary void: time-lease served by ep1 (clean stop), resumed ep2 does NOT
  re-register it (no values, no nak, no expiry record — silent skip; the
  `started` is the answer); same with ep1 crashed (fabricated dead-pid
  `started`).
- Re-anchor-once: a lease arriving DURING ep1 (after its `started`) is
  registered fresh by ep2 (its first possible drainer), then voided by ep3.
- Step-keyed lease unaffected: `{every: {step: 1}}` carries across episodes
  (the existing relaunch-extends test stays green; add the explicit sibling).
- Mixed schedule (`until: {any: [{step}, {time_seconds}]}`) is episode-scoped.
- Supersession regression (the A1 attack): step-sub at seq *a*, time-sub same
  `request_id` at seq *b* > *a*, boundary, re-drain → the worker registers
  NOTHING (pop-then-skip rescinds the predecessor) and `live_demand` agrees
  (empty) — the superseded immortal sub must not resurrect.
- Zero-fire void (the A3 attack): two crash-births (claims that die before
  any drain) around a pre-staged lease → the third episode voids it with zero
  fires; documented behavior, asserted.
- `live_demand` observer form: counts the lease before any foreign `started`
  follows; excludes it after; agrees with the worker at every step of the
  ghost walkthrough (the ≤2-relaunch bound asserted end-to-end, and ≤1 when a
  boundary already follows the lease).
- `serve()`/ghost integration: waker-shaped loop over fresh `live_demand`
  reads relaunches a Worker against a dead pre-boundary lease exactly once;
  the next `live_demand` read is empty.
