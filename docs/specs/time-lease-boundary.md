# Spec: episode-scoped leases (`lifecycle.bound` names the episode)

**Status:** shipped 2026-06-11 (adversarial attack: survives-with-amendments,
all four folded — pop-then-skip, the ghost bound, zero-fire-void documented,
the purity amendment; consistency sweep: 15 fold-backs catalogued below + the
barrier finding). **Converted to reference by name 2026-10-03**
([`reference-by-name.md`](reference-by-name.md), log format 0.3.0): the
recordless boundary void became the `lifecycle.bound` record, which names the
episode that registered a lease. The sections below state the converted rule.
Supersedes the ghost-lease "flap bound" deliberation (backoff/give-up/
cadence-knob all rejected as waker-side compensation for a log gap) and amends
`specs/service-worker.md`'s bounded-hysteresis row and its recorded
lazy-launch constraint.

## The disease (why backoff felt janky)

Every other piece of standing protocol state re-derives from the log across
episodes — subscriptions, stops, answers, the spent ids. One does not:
**a time-lease's elapsed countdown** lives only in the worker's memory, dies
with it, and resurrects at zero in the next episode (the "re-anchor",
documented in stop-discharge's time-axis note and accepted at the time). The
ghost-lease relaunch loop is that asymmetry biting: a dead client's lease can
be re-anchored forever by a waker that keeps relaunching workers that keep
dying young. Any waker-side policy (backoff) treats the symptom from the
wrong layer.

## The rule

> **A registration referencing an EPISODE-LOCAL coordinate is a contract with
> one living episode.**
> A `control.subscribe` whose schedule references an episode-local coordinate
> (`time_seconds` or `count` anywhere in `from`/`every`/`until`; the amendment
> below) is a **lease**. The episode that registers it first writes
> **`lifecycle.bound`** — envelope `request_id` = the lease, body `{claim_seq}`
> naming its own claim — and then registers it (emit-then-register: a crash
> between the two leaves the lease bound, so void elsewhere, never orphaned).
> The lease is **void for every other episode**, and **void for every reader
> once a terminal names its bound episode**. A lease that no episode has bound
> is never void: the next episode to drain it binds it.

A void lease is skipped at drain silently (already-answered is not a refusal —
the same posture as a discharged stop), **and the skip still empties its
registration slot (pop-then-skip)**. A `request_id` names one request, so a
re-send of a live id is the same request; once that request is a lease bound
elsewhere, the whole request is void here, every re-send included. Without the
pop, a superseded earlier schedule under the same id (e.g. an unbounded
step-sub the client had tightened into a time lease) would resurrect on
re-drain, pinning the worker forever while `live_demand` reads zero.

**One predicate** (`observables.lease_void(bound_claims, drainer_claim,
drainer_ended)`), shared by the worker and the observers, in two forms:

- **Worker form (at drain):** the drainer is the worker's own live claim,
  never ended. Void iff the lease is bound to any other claim.
- **Observer form (`live_demand`, the waker):** the drainer is the latest
  claim, ended iff a terminal names it (a `stopped` naming it, or a
  `launcher.terminated` naming the launch it answered). Void iff the lease is
  bound to any other claim, or bound to the latest claim once it has ended.

The two forms agree because a live drainer is always the latest claim.

Pure step schedules are untouched — they are run-absolute and persist across
episodes, unbound (`test_relaunch_extends_one_series`). A schedule containing
*any* episode-local atom is episode-scoped *in toto*: a time atom's meaning
(seconds since registration) cannot be honestly reconstructed across a
boundary, and partially reconstructing the step arms of a mixed schedule would
silently change its meaning — blunt-but-crisp wins.

## Amendment — `count` is the second episode-local coordinate

The rule shipped testing only `time_seconds`, via `references_time`. But the
disease this spec diagnoses — state that *"lives only in the worker's memory,
dies with it, and resurrects at zero in the next episode"* — is equally true of
`count`: `Subscription.__init__` sets `self.count = 0`, and a resumed episode
refunds the whole budget. Measured before the fix: `until={"count": 5}` fired
3+3+3 across three episodes where the time equivalent correctly fired 3+0+0.

The predicate is now `references_episode_local` (`time_seconds` OR `count`), and
`_EPISODE_LOCAL_ATOMS` names the category. `step` is deliberately excluded: it is
run-absolute (`steps(start=k)` emits run-absolute steps), so it survives a
boundary intact.

`references_time` survives as a **separate, narrower** predicate. The two
questions are not the same and diverge exactly on `count`: `memoizer.history`
asks *"does the time axis need anchoring to `started.t`?"* — and a count-only
schedule is a lease that needs no epoch. Conflating them was what hid the gap.

**The road not taken:** `count` *could* be re-derived from the log (unlike
elapsed time, the fires are countable), making a count lease genuinely survive.
Declined: that is a per-atom carve-out, which the rule above forbids by name,
and a schedule mixing both atoms would then carry two incompatible survival
rules.

## What it buys

- **The ghost terminates by construction, with no policy.** A lease nobody
  will renew is served by at most one episode: the one that binds it. A waker
  acting on fresh `live_demand` reads launches a worker for a dead lease once
  to serve it; once that episode's terminal is on the log, the binding voids
  the lease for every reader. At most one more launch follows, and only when
  the serving episode died with no terminal naming it (a crash nobody reaped):
  the observer form cannot yet tell that episode ended, so `live_demand` still
  counts the lease, and the next episode's drain sees the binding and retires.
  `test_ghost_relaunch_bound` pins the clean case at exactly one launch.
  Backoff, give-up rules, and cadence knobs are all deleted from the waker
  design.
- **The re-anchor is gone.** A lease's countdown is never restarted by a later
  episode.
- **The lazy-launch spec loses its hardest input** — `service-worker.md`'s
  recorded constraint "the decider must bound its own relaunch cadence" is
  void; the waker needs no flap policy at all (the tell that the fix is at
  the right depth).

## Who pays

- **A renewing client.** Re-sending a live lease's id is the same request: its
  latest schedule replaces the registration and restarts its clock, so a
  client renewing under one id keeps its lease alive within the episode that
  bound it. Across a crash the binding voids that id for every later episode,
  re-sends included, so such a client is **unserved until it resubscribes
  under a fresh id**. A client that renews under a fresh id each time
  (unsubscribing the old one) is unserved between a crash and its next
  renewal, at most one renewal period. **The renewing-client gap is
  accepted**, and `await_consumed` raises `ValueError` for a re-send of a
  void lease id, so a same-id renewer learns to resubscribe under a fresh id
  rather than being told "accepted". A client-side helper that resubscribes under a fresh id as
  soon as a new claim appears could shrink it; this spec notes it and does
  not build it.
- A non-renewing long-lease client ("keep alive an hour, no renewals") is cut
  off by any episode boundary (crash, extend, blip) and must resubscribe. The
  alternative is the *silently wrong* opposite — a fresh full countdown per
  boundary, so a 60 s lease could last hours. The rule replaces unpredictable
  generosity with a crisp, log-readable answer.
- **A lease can be voided with ZERO fires** — if the episode that binds it
  dies before its first fire (a `from` not yet reached, or a crash right
  after the drain). Stated plainly: **acceptance ≠ will-serve**. Episodes
  that die before draining the lease bind nothing and void nothing, so a
  lease is never voided by a boundary no episode drained it at; the client's
  detection mechanism is its own renewal cadence.
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
  the asymmetry ever bites, the same binding extends — recorded, not built.
- **One record, `lifecycle.bound`** (`lifecycle`-`v0.5`): body `{claim_seq}`,
  envelope `request_id` required. Written once per (lease, registering
  episode), before the registration. The rest is drain/fold semantics:
  `worker._handle_control` (the binding and the pop-then-skip clause),
  `observables.live_demand` (the observer form), `observables.lease_void` (the
  one predicate), `schedule.references_episode_local` (a `time_seconds` or
  `count` atom anywhere in `from`/`every`/`until`; an unparseable schedule is
  NOT a lease — the worker naks it, which answers it), and the worker's attach
  read, which collects the bindings beside the answers.
- **`live_demand` loses one purity stripe, honestly:** it must peek at
  subscribe bodies for the time-atom check, so service-worker.md's
  "envelope-level fold, body untouched" claim is amended (it remains
  value-blind — it reads schedule *shape*, never payloads).
- Doc steering (the A5 guidance): a bound meant to survive episodes is
  spelled `until: {step: N}` (run-absolute); **any time atom makes the whole
  registration a lease** — degenerate cases included (`{from:
  {time_seconds: 0}}`, huge time-`until`s) — blunt-but-crisp, no per-atom
  carve-outs. Likewise **`Watcher.broadcast` barriers should be step-keyed**:
  a time-keyed barrier subscription is a lease, so on a run that resumes it
  is void for the new episode — the fifth never-fire cause, whose handler is
  the binding — and a capless pure-sync would otherwise wait on a healthy run
  forever (design §9 gains the cause; a boundary-aware re-broadcasting
  Watcher is a backlog note, not this spec). And "anticipatory warmth" in
  service-worker.md becomes honest **renewed** periodic demand — standing
  warmth without renewal was the immortal-pin smell all along.
- **One predicate, one home:** the worker form and the observer form share a
  single voided-check (in `observables`, imported by the worker — the F7
  lesson, applied preemptively); the spec's worker/observer agreement test is
  mandatory. The third time-anchoring in the corpus — `memoizer.history()`
  replays time atoms run-epoch-anchored — is named in the backlog
  (time-axis unification) rather than touched here.

## Docs deliverables (the consistency sweep's fold list, 2026-06-11)

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

## Tests (all backends)

`tests/test_service_worker.py`, `tests/test_reference_by_name.py`,
`tests/test_observables.py`:

- The founding idiom: a pre-staged lease is bound and served by the first
  episode that drains it (`test_founding_prestaged_time_lease_registers`).
- The binding: the registering episode writes one `lifecycle.bound` naming its
  claim, and a later episode does not serve the lease — no values, no nak, no
  expiry record; the binding is the answer
  (`test_a_time_lease_is_void_only_through_its_binding_by_name`,
  `test_the_registering_episode_binds_the_lease`).
- A lease arriving during an episode that is already dead is bound by the
  first episode that drains it, and void for the one after
  (`test_reanchor_once_then_void`).
- Crash-births that never drained a lease bind nothing and void nothing
  (`test_crash_births_that_never_drained_a_lease_do_not_void_it_by_name`,
  `test_a_lease_no_episode_drained_survives_crash_births`).
- A step-keyed subscription carries across episodes unbound
  (`test_step_keyed_lease_crosses_boundaries`); a mixed schedule is a lease,
  and only it is bound (`test_mixed_schedule_is_episode_scoped_by_name`).
- Supersession (the A1 attack): a step-sub tightened into a lease under the
  same id is one request, a lease; once its episode ends the superseded
  immortal schedule does not resurrect, and `live_demand` agrees
  (`test_voided_lease_pops_its_same_id_predecessor_by_name`).
- The observer form: `live_demand` counts a lease until its binding voids it,
  and a clean stop voids it for every reader
  (`test_live_demand_voids_a_time_lease_only_through_its_binding_by_name`,
  `test_a_lease_is_void_once_a_terminal_names_its_episode`).
- The ghost: a waker-shaped loop over fresh `live_demand` reads launches a
  worker for a dead lease exactly once, and the next read is empty
  (`test_ghost_relaunch_bound`).
- `count` is episode-local like time, and a count lease does not refund its
  budget each episode
  (`test_a_count_lease_does_not_refund_its_budget_each_episode_by_name`).
