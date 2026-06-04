# `ensure(until=<condition>)`: generalize the drive-target to the condition-algebra

**Status:** design draft 2026-06-03 (dialogue converged on the shape; this is for review →
implementation). Supersedes the scalar `up_to` form (clean break — **no** back-compat alias).
**Origin:** the mycooc dogfood (runstate-adoption, Phase 4). Milestone-round-robin sweeps want
to drive each variant to a *step* milestone **or** a *wall-clock* budget with one uniform loop;
the scalar `up_to` only spans the step axis. Realizes the bulk of
`../backlog/memoizer-index-algebra.md` (the `ensure(I)` index-algebra), with the time axis
falling out of the *same* generalization.

## The gap

`ensure(producer, name, *, up_to)` (`runstate/memoizer.py`) drives a producer until
`_progress(channel) >= up_to - 1` and returns `history(channel, name, {every:{step:1},
until:{step:up_to}})`. The drive-target is a **single scalar on the step axis**. But the library
already owns a richer vocabulary for "a threshold over the worker's coordinates": the
subscription **condition-algebra** (`runstate/vocabulary/schedule.py`) —
`{"step":N} | {"time_seconds":S} | {"count":C}` plus `any`/`all`, evaluated by `satisfied(...)`.
`history` *already* replays that algebra (including run-relative `time_seconds` `until`) over the
log. Only `ensure`'s **target** is stuck on the step scalar, so it cannot express "drive this
variant for `S` seconds" — the basis vector mycooc needs for timeout-balanced sweeps.

## The change

`ensure`'s keyword `up_to: int` becomes `until: dict` — a **Condition** from the algebra. The
two touch points:

1. **Satisfaction (the drive loop).** Replace the hard-coded `_progress >= up_to - 1` with the
   algebra's `satisfied`, evaluated over the run's coordinates read from the **dense axis**:

   ```python
   def _satisfied(channel, until) -> bool:
       return satisfied(until, step=_progress(channel) + 1,
                        time_seconds=_elapsed(channel), count=_count(channel))
   ```

2. **Return (the read).** Unchanged in *shape*, now parameterized by the same condition:

   ```python
   return history(channel, name, {"every": {"step": 1}, "until": until})
   ```

   `history` already evaluates a `time_seconds` `until` run-relative (point `value.t` − run epoch),
   so the return generalizes with **no** change to `history`.

`up_to=N` is exactly `until={"step": N}` (see *The off-by-one* below) — so the callable surface
strictly subsumes the old one; we then **delete** the `up_to` spelling rather than keep an alias.

## The off-by-one (load-bearing; the one subtle part)

Today `ensure(up_to=N)` is satisfied at `_progress >= N-1`, **not** `>= N`. That encodes the
worker's *exclusive* target convention: a producer told "target `N`" emits steps `0..N-1` and
reaches `progress = N-1` (the shipped `_LaunchProducer` injects the target as a worker kwarg; the
worker loops `range(N)`). The return read `until={step:N}` likewise fires `0..N-1` (the
`Subscription` pre-fire expiry gate excludes the boundary point `N`). So `up_to=N` means the
**half-open window `[0, N)`** — drive and read agree on it.

To preserve that under `satisfied`, the satisfaction check passes `step = _progress + 1`:

> `satisfied({"step":N}, step=_progress+1)` ⇔ `_progress+1 >= N` ⇔ `_progress >= N-1`. ✓

The `+1` is the **discrete-axis exclusive-bound convention** (the same one Python's `range(N)` and
the existing `up_to` use). The **continuous** time axis takes **no** offset:
`satisfied({"time_seconds":S}, time_seconds=_elapsed)` ⇔ `_elapsed >= S` — the `[0, S)` window is
complete once the run has run `S` seconds (the boundary `S` is generically not a grid point, so
there is nothing to exclude). For a combined `all`/`any`, each atom evaluates on its own axis with
its own convention — consistent.

> **Review flag #1:** the per-axis offset (`step+1`, `time+0`, `count+?`) lives in *one*
> `satisfied(...)` call. It is correct atom-by-atom, but it is the least-obvious line in the
> change. Confirm `count` (fires consumed) wants `+0` (a count `until={count:K}` window `[0,K)`
> closes when `K` values have been consumed, i.e. `count >= K`, no offset) — and that no axis
> wants a different rule.

## Reading the coordinates from the log

`ensure` reads the **dense** axis (so it works for sparse `value` emitters — you get the sparse
series, not N points). Today only `_progress` (step) is needed. The generalization adds:

- `_elapsed(channel)` — **run-relative seconds**: the latest dense timestamp − the run epoch
  (earliest `lifecycle.started.attached_at`, the same epoch `history` uses). Returns `0.0` before
  the run has a clock.
- `_count(channel)` — fires consumed; only needed if the `count` axis is in scope (see Non-goals).

> **Review flag #2 (the orthonormality-sensitive one):** *which dense timestamp?* `_progress`
> reads `lifecycle.heartbeat.step`. For `_elapsed` we want a **dense** clock too, but the
> `Heartbeat` body today carries `{step, consumed_seq}` — **no** wall-clock field. Three options,
> in increasing cost:
>   1. **Read `value.t`** (the sparse value series' timestamp). Zero schema change, but a *sparse*
>      emitter's time-satisfaction lags reality between value points — wrong axis for a dense check.
>   2. **Read the envelope arrival time.** The substrate is opinion-free (`seq, topic, name,
>      request_id, body`) — timestamps live in bodies by design, so there is no envelope clock to
>      read. Rejected (would put a clock in the substrate).
>   3. **Enrich `Heartbeat` with `t` (or `elapsed`).** A *lifecycle* convention-version bump
>      (`additionalProperties:false` is load-bearing). This is the canonical home — the heartbeat
>      is the worker reporting its position on its clocks; it already carries the step clock, and
>      the design names *three clocks*. Adding the time clock is symmetric, not opinion creep.
>      But it is a wire change and deserves its own scrutiny.
>
> Recommendation: **(1) for a first cut** (step milestones need no timestamp at all; time
> milestones via `value.t` are correct for the common dense-`value` emitter, which mycooc is),
> with **(3) flagged** as the principled fix if a sparse-`value` + time-budget worker ever needs it.
> The review should decide whether to do (3) now (clean, symmetric) or stage it.

## The producer seam: `extend(until)`

The producer's `extend` goes from `extend(up_to: int)` to `extend(until: dict)` — it is the **one**
place that knows how to translate a condition into a *launch bound*:

- `{"step": N}` → the worker's step budget (the shipped `_LaunchProducer` injects it under
  `target_key`, today `"up_to"`).
- `{"time_seconds": S}` → a wall-clock budget the launcher passes to the worker (e.g. mycooc's
  per-dispatch `timeout`); for an absolute time milestone the producer subtracts elapsed:
  `timeout = S − _elapsed`.

`ensure` stays **bound-agnostic**: it passes `until` to `extend`, then polls `satisfied`. This keeps
the three concerns orthogonal — *condition* (when the window is closed, `ensure`) ⟂ *translation*
(condition → launch arg, the producer) ⟂ *resume policy* (`completed`/`preempted`/`_FAILURES`
re-drive, `ensure`, unchanged from the `preempted-vs-completed` spec).

The shipped `_LaunchProducer` / `launch_producer` translate the **step** atom only (inject the
scalar into a worker kwarg). A time (or compound) milestone needs a launcher whose worker accepts a
timeout — i.e. the **user's own producer** implementing `.channel`/`.run_id`/`.extend(until)` (the
documented seam). mycooc's Phase-4 producer is exactly this (translates `{step}`→`max_steps_per_run`,
`{time_seconds}`→`timeout`).

> **Review flag #3:** the shipped `_LaunchProducer.extend` should reject a condition it cannot
> translate (e.g. a `time_seconds` atom, or an `any`/`all`) with a clear error, rather than
> silently inject a dict into `target_key`. Decide: validate to *step-scalar-only* in the default
> producer, and document compound/time as "bring your own producer."

## The round-robin payoff (why mycooc wants this)

```python
for milestone in milestones:            # [{step:100}, {step:200}, …]  OR  [{time_seconds:600}, …]
    for variant in variants:
        ensure(variant_producer, name, until=milestone)
```

One loop spans both axes. Step milestones give balanced partials by **progress**; time milestones
give balanced partials by **wall-clock** (the timeout-balanced sweep that the scalar `up_to` could
not express, and whose absence is the `--timeout`-mode regression noted in the Phase-4 plan).

## Orthonormal-basis check

- **Independence (necessity):** not a new algebra — `ensure` adopts the *existing* subscription
  condition-algebra that `history`/`Subscription` already use. The scalar `up_to` was a
  **projection** of it onto one axis; we replace the projection with the basis. Removes a
  coordinate-restriction, adds no redundancy.
- **Spanning (sufficiency):** supplies the missing basis vector (a *time*/compound drive-target)
  without over-reach — `ensure` bakes no workload opinion; *which* launch arg a condition maps to is
  the **producer's** call, and *which* stop is `completed` vs `preempted` remains the **worker's**
  (the `preempted-vs-completed` invariant is untouched).
- **Canonical form:** the condition-algebra is *the* threshold vocabulary in the design (§6); making
  `ensure`'s target a Condition is the least-arbitrary choice (vs a new `Bound`/`Target` type, which
  would duplicate it). `up_to=N ≡ until={step:N}` is the universal-property tell: the new form is
  the free generalization, the old one its step-axis instance.
- **Orthogonality:** condition (when, `ensure`) ⟂ translation (how to launch, producer) ⟂ resume
  policy (`outcome`, `ensure`). Each pair carries one concern.
- **Serendipity:** one selector vocabulary now spans `Subscription`, `history`, **and** `ensure`;
  the time axis reuses the run epoch `history` already computes; the `ensure(I)` index-algebra
  (backlog) and time-milestones fall out of the *same* change. The `completed`/`preempted` bit
  (other spec) composes unchanged — `completed` short of *any* `until` still short-circuits.

## Scope / ripple

Python + docs + tests. Wire change **only if** Review-flag-#2 chooses heartbeat-`t` (option 3):

- `runstate/memoizer.py` — `ensure` signature `up_to`→`until`; `_satisfied` via `satisfied`;
  add `_elapsed` (and `_count` iff the count axis is in scope); the `dense` read uses `until`;
  `_LaunchProducer.extend(until)` + `launch_producer` translation + validation (flag #3).
- `runstate/vocabulary/schedule.py` — no change (consumed as-is).
- Tests — `test_memoizer.py`: migrate every `up_to=N` → `until={"step":N}` (RETURN assertions
  unchanged — `[0, N)` is identical); **add** `until={"time_seconds":S}` cases (synthetic channel
  with timed `value.t` + `started.attached_at`: returns the run-relative window; drives until
  `_elapsed >= S`); **add** a compound `all` case; the existing `completed`-short-circuit and
  `preempted`-re-drive cases re-expressed against `until`.
- `examples/` — any `ensure(up_to=…)` caller → `until={"step":…}`.
- `docs/specs/memoizer.md` — the `ensure` semantics (condition target; the `[·, until)` window;
  the dense-axis satisfaction; the producer-translation seam).
- `docs/backlog/memoizer-index-algebra.md` — mark the **scalar→condition** step **done** here;
  leave any genuinely-further index-algebra (e.g. random-access `name`-set targets) as the residue.
- `protocol/lifecycle-v0.2.schema.json` — **no change** unless flag-#2 option 3 (then a
  `lifecycle` convention-version bump to add `Heartbeat.t`, with its own schema/conformance update).

## Non-goals / open for the review

- **Count axis (`{count:C}`):** the algebra has it, but no in-scope driver needs "drive until `C`
  values consumed." **Lean: out of scope now** — don't add `_count` until a use case exists (adding
  it speculatively is spanning-overreach). Confirm.
- **No new `Bound`/`Target` type** — reuse the Condition dict verbatim (canonical-form decision).
- **Time = run-relative wall-clock, gaps included.** For a chunked/resumed run, `_elapsed`
  (latest dense `t` − epoch) counts idle gaps between chunks, so `{time_seconds:S}` is a
  *wall-clock-since-start* budget, **not** an accumulated-compute budget. For back-to-back chunks
  (one orchestrator process) the gap is relaunch overhead — negligible. A true compute budget
  (gaps excluded) would need the worker to report accumulated run-time — **out of scope**; document
  the caveat. (This interacts with flag #2: a worker-reported `elapsed` on the heartbeat could be
  *compute* time, sidestepping the gap issue — another reason the review may prefer option 3.)
- **`extend` translation for compound conditions** in the *default* producer — out of scope;
  bring-your-own-producer (flag #3).
