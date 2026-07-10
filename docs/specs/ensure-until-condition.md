# `ensure(until=<condition>)`: generalize the drive-target to the condition-algebra

**Status:** converged 2026-06-04 (design dialogue + two adversarial reviews — orthonormality
and correctness — folded in); ready for review → implementation. Supersedes the scalar `up_to`
form (clean break — **no** back-compat alias).
**Origin:** the mycooc dogfood (runstate-adoption, Phase 4). Milestone-round-robin sweeps want to
drive each variant to a *step* milestone **or** a *wall-clock* budget with one uniform loop; the
scalar `up_to` only spans the step axis. Realizes the **bound** half of
`../backlog/memoizer-index-algebra.md` (the `ensure(schedule)` idea); the emission-*filter* half
(`from`/`every`) stays deferred there (see *Scoping*).

## The gap

`ensure(producer, name, *, up_to)` (`runstate/memoizer.py`) drives a producer until
`_progress(channel) >= up_to - 1` and returns `history(channel, name, {every:{step:1},
until:{step:up_to}})`. The drive-target is a **single scalar on the step axis**. But the library
already owns a richer vocabulary for "a threshold over the worker's coordinates": the subscription
**condition-algebra** (`runstate/vocabulary/schedule.py`) — `{"step":N} | {"time_seconds":S} |
{"count":C}` plus `any`/`all`, evaluated by `satisfied(...)`. `history` *already* replays that
algebra (including run-relative `time_seconds` `until`) over the log. Only `ensure`'s **target** is
stuck on the step scalar, so it cannot express "drive this variant for `S` seconds" — the basis
vector mycooc needs for timeout-balanced sweeps.

## The change

`ensure`'s keyword `up_to: int` becomes `until: dict` — a **Condition** from the algebra. Three
touch points:

1. **Satisfaction (the drive loop).** Replace the hard-coded `_progress >= up_to - 1` with the
   algebra's `satisfied`, evaluated over the run's coordinates:

   ```python
   def _satisfied(channel, until, *, clock) -> bool:
       return satisfied(until, step=_progress(channel) + 1,
                        time_seconds=_elapsed(channel, clock), count=0)
   ```

   (`step=_progress+1` is the half-open-window convention — see *The off-by-one*; `_elapsed` is the
   consumer's poll-clock — see *Reading the coordinates*; `count=0` because the count axis is
   rejected up front, not driven — see *Non-goals*.)

2. **Return (the read).** Unchanged in *shape*, now parameterized by the same condition:

   ```python
   return history(channel, name, {"every": {"step": 1}, "until": until})
   ```

   `history` already evaluates a `time_seconds` `until` run-relative (point `value.t` − run epoch),
   so the return generalizes with **no** change to `history`. (Drive-side and read-side time are two
   different but reconciled clocks — see *Reading the coordinates*.)

3. **Enforcement (the producer seam).** `extend(up_to: int)` → `extend(until: dict)` — the one place
   that translates a condition into the worker's own stop-bound (see *Enforcing the bound*).

`up_to=N` is exactly `until={"step": N}` (see *The off-by-one*) — so the callable surface strictly
subsumes the old one; we then **delete** the `up_to` spelling rather than keep an alias.

## The off-by-one (load-bearing; name it in code)

Today `ensure(up_to=N)` is satisfied at `_progress >= N-1`, **not** `>= N`. That encodes the
worker's *exclusive* target convention: a producer told "target `N`" emits steps `0..N-1` and
reaches `progress = N-1`. The return read `until={step:N}` likewise fires `0..N-1` (the
`Subscription` pre-fire expiry gate excludes the boundary point `N`). So `up_to=N` means the
**half-open window `[0, N)`** — drive and read agree on it.

To preserve that under `satisfied`, the satisfaction check passes `step = _progress + 1`:

> `satisfied({"step":N}, step=_progress+1)` ⇔ `_progress+1 >= N` ⇔ `_progress >= N-1`. ✓

The `+1` is the **discrete-axis exclusive-bound convention** (the same one `range(N)` and the old
`up_to` use); the **continuous** time axis takes **no** offset (`_elapsed >= S` closes `[0,S)`; the
boundary `S` is generically not a grid point — nothing to exclude); `count` takes no offset
(`count >= K` closes `[0,K)`). The offset is applied **once, in the argument to `satisfied`** — never
by rewriting the condition (`{step:N}`→`{step:N-1}` would break `any`/`all` nesting, which evaluates
every atom against the same passed coordinates).

> **Implementation requirement (review obj 4):** the `_progress + 1` lives behind a one-line helper
> whose docstring states the load-bearing triple-agreement — `[0,N)` ↔ `range(N)`/`up_to` ↔ the
> read-side `Subscription` expiry gate. That triple is the correctness argument and must be in the
> *code*, not only here.

## Reading the coordinates

`ensure` reads the **step** axis from the **dense** log axis (so it works for sparse `value`
emitters — you get the sparse series, not N points): `_progress` = latest `lifecycle.heartbeat.step`
/ `lifecycle.stopped.final_step` (unchanged).

The **time** axis is the **consumer's own poll-clock**:

```python
def _elapsed(channel, clock) -> float:
    # epoch read FRESH each call (NOT cached at entry): before the run starts there
    # is no epoch, so time conditions are inert (0.0) rather than `clock() - 0.0`.
    started = channel.read(topics=["lifecycle.started"], limit=1)
    if not started or started[0].body.get("attached_at") is None:
        return 0.0
    return clock() - started[0].body["attached_at"]   # epoch = earliest started.attached_at
```

`epoch` = earliest `lifecycle.started.attached_at` (the same epoch `history` uses), read fresh so a
pre-start call yields `0.0` (not a spurious huge elapsed). `clock` joins `poll_interval`/`sleep` as
an injectable so tests drive it deterministically.

*(Amended 2026-07-10: "the same epoch" is true by construction — one shared reader,
`_epoch(channel) -> float | None`. The null-epoch responses differ by role: `_elapsed` returns
`0.0` (time inert during its transient run-hasn't-started wait), while `history` with a
time-referencing schedule **raises** `ValueError` — an epoch-less log cannot anchor run-relative
time, and anchoring at `0.0` would evaluate absolute `value.t` as elapsed. Step-only replays never
touch the epoch.)*

**Why the poll-clock and not a logged timestamp** (this was the sharpest review finding):

- **`Heartbeat.t` is rejected, not staged.** `design-v0.2.md:156` is a converged decision: the
  heartbeat carries **no embedded timestamp** *by design* — staleness uses the *reader's* arrival
  clock — and that body `{step, consumed_seq}` is pinned (`additionalProperties:false` is
  load-bearing). Adding `t` re-opens a frozen convention to duplicate a clock the reader already has.
- **`value.t` is rejected** (the first draft's "first cut"). It is the **sparse** value-series
  stamp (written only when a subscription fires, `worker.py`). Reading it as a "dense" clock is a
  misnomer and a livelock: a worker that stops emitting while still alive freezes `_elapsed`, so a
  `{time_seconds}` milestone **never satisfies** (and `ensure` has *no* hang timeout, by design).
  It also splits a compound `all:[{step},{time}]` across two different clocks (dense step, sparse
  time) — an orthogonality break.
- **The poll-clock is dense, monotone, gap-inclusive, and needs no wire change.** `ensure` is a
  *consumer*; reading its *own* wall-clock while polling is exactly what `:156` blesses (not a
  worker stamping a body — that would be a substrate concern). It advances every poll regardless of
  emission, so the livelock is structurally impossible.

The **read-side** `history` keeps `value.t − epoch` (a *replay* needs the recorded stamp; the live
drive does not). Drive-side and read-side time can differ by ≤ one poll interval at the boundary —
bounded and acceptable, unlike the sparse lag of `value.t`.

## Enforcing the bound (the producer seam, and how time stops)

`ensure` stays **bound-agnostic**: it passes `until` to `extend`, then polls `_satisfied`. The
producer is the **one** place that translates a condition into the worker's own stop-bound — and the
worker **self-bounds** (symmetric with step), `ensure`'s poll-clock only *judges* satisfaction at
episode-end:

- `{"step": N}` → the worker's step budget. The default `_LaunchProducer` injects the **scalar**
  `until["step"]` (not the dict!) under `target_key` (today `"up_to"`).
- `{"time_seconds": S}` → the worker's own time budget (e.g. mycooc's per-dispatch `timeout`, or a
  self-limit on persisted run-time). The producer computes the residual against elapsed for an
  absolute milestone. The worker runs, self-preempts at its budget (checkpoints, emits
  `preempted`), the episode ends; `ensure` then checks `_elapsed >= S` to decide return-vs-re-drive.

This keeps the three concerns orthogonal — *condition* (when the window is closed, `ensure`) ⟂
*translation/enforcement* (condition → the worker's bound, the producer/worker) ⟂ *resume policy*
(`completed`/`preempted`/`_FAILURES`, `ensure`, per the `preempted-vs-completed` spec).

**Why self-bound (model a), not consumer-commanded (model b).** An alternative is to launch the
worker unbounded and have `ensure` send `control.stop` when `_elapsed >= S`. It is coherent and more
"cooperative-control idiomatic," but: it makes step self-bounded and time consumer-commanded (an
asymmetry with no payoff), it bolts a control-plane *write* onto the memoizer (breaking its
drive+read orthogonality), and the unbounded run re-introduces the livelock surface. Model (a) reuses
mycooc's existing self-timeout directly. Model (b) stays **available** to a producer that genuinely
wants it (the seam permits `extend` to arm a cooperative stop) — it is just not the default.

**Default-producer translation + validation (review obj/critical e — mandatory).**
`_LaunchProducer.extend(until)` must:
1. for `until == {"step": N}` — inject the **scalar `N`** under `target_key`;
2. for **any other shape** (`time_seconds`, `count`, `any`/`all`) — **raise** a clear error
   (`ValueError`/`NotImplementedError`): *"the default launch-producer translates only `{'step': N}`;
   bring your own producer (`.channel`/`.run_id`/`.extend(until)`) for time/compound milestones."*

Without this, today's `worker_kwargs[target_key] = up_to` would inject the **dict** verbatim →
`TypeError` deep inside the launched episode → opaque `errored`. The canary is the existing
`test_launch_producer_extend_injects_target_and_runs` (its `extend(3)` becomes `extend({"step":3})`
and must still inject `3`). Rename the `extend` parameter and locals `up_to`→`until` so the
verbatim-injection trap cannot survive review.

## The no-progress guard must be axis-aware (review critical c — design hole in the first draft)

The first draft wrongly said the re-drive path was "unchanged." It is not. Today
`memoizer.py` raises "made no progress" when `handle is not None and _progress(channel) <= before` —
a **step** quantity. For a `{time_seconds:S}` milestone whose chunk legitimately advances **0 steps**
while wall-clock advances (a slow step, a tiny residual timeout), this **false-positives and kills a
healthy run.** Step-progress is the wrong liveness signal when the target is not the step axis.

**Fix:** snapshot `before = _progress(channel)` and raise only when re-driving genuinely cannot
make progress toward `until` — i.e. when **step stalled** AND **no amount of time could satisfy
`until` from the current step** (step progress is required and missing). The clean test is the
algebra itself with time set to infinity — no separate axis-classifier:

```python
if (handle is not None and _progress(channel) <= before
        and not satisfied(until, step=_progress(channel) + 1,
                          time_seconds=float("inf"), count=0)):
    raise RuntimeError(...)            # step required, stalled, and time can't rescue it
```

Correct across the lattice:
- **`{step:N}`** stalled below target → `satisfied(step=progress+1<N, time=∞)` is False → raise (the
  genuine livelock).
- **`{time_seconds:S}`** → `satisfied(time=∞)` is True → never raises (the poll-clock always reaches it).
- **`{all:[{step:N},{time:S}]}`** with the **step part still unmet** and stalled → False → raise; with
  the **step part already met** (only time pending) → `satisfied(step≥N, time=∞)` True → does **not**
  raise (time will finish it). A coarse "the `all` contains a step atom, so guard on step-stall" rule
  gets *this* case wrong — false-killing a healthy run.
- **`{any:[{step},{time}]}`** → time alone satisfies → True → never raises.

Implement and **test** this — including the compound-`all` step-met/time-pending case, which is the
one a naive classifier botches.

## The `completed`/`preempted` discipline extends to the time axis (review obj/important f)

`ensure`'s early-`completed` short-circuit fires on *any* completion, regardless of which axis the
milestone targets — correct for a *converged* worker. But a **time-budgeted** worker that mistakenly
emits `reason="completed"` at each per-chunk stop would make the round-robin **stop after the first
chunk** instead of accumulating to the wall-clock budget. The `preempted-vs-completed` discipline is
therefore **load-bearing for time too**: a chunked/resumable time-budgeted producer's per-chunk stop
is `preempted`, **never** `completed`. The shipped tests encode this for the step axis; **add** the
time-axis analogue.

## The round-robin payoff (why mycooc wants this)

```python
for milestone in milestones:            # [{step:100}, {step:200}, …]  OR  [{time_seconds:600}, …]
    for variant in variants:
        ensure(variant_producer, name, until=milestone)
```

One loop spans both axes. Step milestones give balanced partials by **progress**; time milestones by
**wall-clock** — the timeout-balanced sweep the scalar `up_to` could not express (the `--timeout`-mode
regression noted in the Phase-4 plan).

## Scoping: `until=` only, not the full `schedule=`

A full subscription has three levers — `from` (window start), `every` (emission stride), `until`
(the bound). `ensure` hard-codes `from`=start, `every`={step:1} (dense) and exposes only the bound.
We keep it that way (`until=`), **not** a `schedule=`/`Subscription`-shaped argument, because:

- **YAGNI / spanning.** The bound is the only lever any in-scope driver needs. `from`/`every` are the
  emission *filter* (the strided/windowed `ensure(I)` case) and would ship as **unexercised,
  untested surface** — the rubric's spanning-overreach.
- **Orthogonality.** `until` is the *run bound* (how far to drive); `from`/`every` are *which logged
  points to return* — a different concern. Keeping the argument to the bound stops `ensure`'s surface
  conflating "how far to drive" with "how to slice the result."
- **Cheap to extend.** Deferring costs ~nothing: `from=`/`every=` can be added later as **optional**
  kwargs without breaking a single `until=` caller (additive — unlike the `up_to`→`until` rename). So
  there is no "second breaking change later" argument against `until=`.

The `from`/`every` generalization (and `ensure(I)` over a function/service producer) stays tracked in
`../backlog/memoizer-index-algebra.md`, and the `ensure` implementation carries a comment pointing
there (so the next reader sees the deliberate scoping, not an oversight).

## Orthonormal-basis check

- **Independence:** not a new algebra — `ensure` adopts the *existing* condition-algebra that
  `history`/`Subscription` already use; the scalar `up_to` was its step-axis projection. Removes a
  coordinate-restriction, adds no redundancy.
- **Spanning:** supplies the missing time/compound *drive-target* vector — and now actually *reaches*
  it: the poll-clock is a real dense time source (the first draft's `value.t` did not span the sparse
  case it claimed). Out-of-scope levers (`from`/`every`, the `count` *drive*) are deliberately
  excluded, not smuggled in.
- **Canonical form:** the condition-algebra is *the* threshold vocabulary (design §6); `until = a
  Condition` is the least-arbitrary target (`up_to=N ≡ until={step:N}` is the universal-property
  tell). The `step+1`/`time+0`/`count+0` offsets are the *one* half-open-window convention, named in
  code.
- **Orthogonality:** condition (`ensure`) ⟂ enforcement (producer/worker self-bound) ⟂ resume policy
  (`outcome`). The clock fix closes the leak the first draft had (drive vs read on two clocks); the
  axis-aware guard closes the step-axis hard-wiring.
- **Serendipity:** one selector vocabulary now spans `Subscription`, `history`, **and** `ensure`; the
  run epoch is reused; the `completed`/`preempted` bit composes unchanged; `ensure(I)` falls out of
  the *same* generalization (now an additive kwarg away).

## Scope / ripple — **no wire-schema change**

- `runstate/memoizer.py` — `ensure` signature `up_to`→`until`; add `clock=time.time` injectable;
  `_satisfied` via `satisfied` (with the named `+1` helper); `_elapsed(clock, epoch)`;
  the dense read uses `until`; the **axis-aware** no-progress guard; `_LaunchProducer.extend(until)` +
  `launch_producer` extract-scalar-for-`{step}` / reject-otherwise; rename `up_to`→`until` throughout;
  a comment pointing at `../backlog/memoizer-index-algebra.md` (the `from`/`every` residue).
- `runstate/vocabulary/schedule.py` — no change (consumed as-is).
- `runstate/vocabulary/payloads.py`, `protocol/*.schema.json` — **no change** (`Heartbeat.t`
  rejected).
- Tests — `tests/test_memoizer.py`: migrate every `up_to=N`→`until={"step":N}` (RETURN assertions
  unchanged); **add** the drive-loop tests below; `examples/reuse/driver.py` (calls + the line-3/line-22
  docstring) → `until={"step":…}` (the worker's own `up_to` kwarg stays a scalar — the producer
  extracts it).
- `docs/specs/memoizer.md` — the `ensure` semantics (condition target; the `[·, until)` window; the
  poll-clock; the producer-translation seam; the axis-aware guard).
- `docs/backlog/memoizer-index-algebra.md` — mark the **scalar→condition (`until`)** step **done**
  here; the residue is the `from`/`every` emission-filter + `ensure(I)` over a function/service
  producer.

## Test plan (the drive-loop tests need a non-frozen clock)

The suite's workers use `now=lambda:0.0`, so `value.t≡0` and any `{time_seconds:S>0}` is otherwise
unsatisfiable — drive-loop time tests **must** inject `clock`:

- **Step exact-preservation** (regression): `until={"step":N}` reproduces today's drive + RETURN on
  the edge cases (`progress=-1`, `N=0/1`, `N` already past). (The correctness review verified the
  arithmetic; lock it with a test.)
- **Time satisfaction** (injected `clock`): a worker driven under `until={"time_seconds":S}` returns
  when `_elapsed >= S`; with a *sparse* `value` emitter it still satisfies (poll-clock, not `value.t`)
  — i.e. **no livelock** when emission stalls but the clock advances.
- **No-progress guard, time axis** (the critical-c regression): a chunk that advances **0 steps**
  while `clock` advances must **not** raise "no progress" for a `{time_seconds}` milestone; a
  step-stalled `{step}` milestone **must** still raise.
- **Default-producer rejection**: `extend({"time_seconds":S})` and `extend({"all":[…]})` raise a clear
  error; `extend({"step":N})` injects the **scalar** `N`.
- **`completed`/`preempted` on time**: a time-budgeted resumable worker emitting `preempted` per chunk
  accumulates to the budget; one emitting `completed` per chunk truncates (documenting the discipline).
- **Compound `all`** (return + drive): `until={"all":[{"step":N},{"time_seconds":S}]}` honors both
  axes with the per-axis offset.
- **`count` rejection**: a stray `{"count":K}` atom in `until` raises up front (no silent livelock).

## Non-goals / out of scope

- **The `count` *drive* axis.** The algebra has it, but no in-scope driver needs "drive until `C`
  values consumed," and an un-driven `count` atom would silently never satisfy → livelock. So
  `_count` is **not** implemented; a `count` atom in `until` is **rejected at `ensure` entry** — a
  one-pass validation walk over the `until` tree, before any driving, raising loudly (so it fires
  for *every* producer, not only the default one). (`count` remains legal in a *subscription*
  `until` — only the `ensure` *drive-target* rejects it.)
- **`{time_seconds:S}` is wall-clock-since-run-start** (the design's real-time axis, run-relative,
  **gaps included**). An accumulated-*compute* budget (gaps excluded) is a *distinct primitive* this
  axis does not provide and this spec does not add — it is **not** a reason to reach for `Heartbeat.t`.
  For back-to-back chunks (one orchestrator process) the gap is relaunch overhead — negligible.
- **No new `Bound`/`Target` type** — reuse the Condition dict verbatim.
- **Compound/time translation in the *default* producer** — out of scope; bring-your-own-producer.
- **The `from`/`every` emission filter (`ensure(schedule=)` / `ensure(I)`)** — deferred to the
  backlog (additive when it lands).
