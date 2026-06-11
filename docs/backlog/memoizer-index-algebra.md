# Memoizer: generalize `ensure(up_to=N)` to an index term-algebra `ensure(I)`

Forward-looking (surfaced 2026-06-02, from the sequence-vs-function thread).
Background: the memoizer spec's "Design note" (`../specs/memoizer.md`) — the
memoizer is thin, the worker owns structure, and `up_to=N` is already sugar for
"ensure the log holds indices `I`."

## Status: DORMANT (2026-06-11) — unfunded, not refuted; trigger below

The first function-shaped consumer (mycooc-analyze) **refuted the premise**,
not the idea: its ~8 kinds are cheap, always demanded together, and gated by
one dominant shared load — so the right key is the whole snapshot, one run
per key, and the existing `ensure(until=)` spans it with no filter language
(`specs/derived-runs.md`, the dissolution; pinned by
`test_derived_run_dissolution_pin`). **Revisit trigger:** a consumer with
many *independent*, *individually expensive*, *sparsely demanded* keys for
which one-run-per-key is too heavy — and the proposal must explicitly beat
the run-granularity competitor (per-key derived runs) before adding
vocabulary. The most plausible future bearer is the viewer thread
(frame-scrubbing-shaped demand).

## (historical) the *bound* half landed; the *filter* half is the residue

`../specs/ensure-until-condition.md` (2026-06-04) generalized the **bound** —
`ensure(up_to: int)` → `ensure(until: Condition)` over the full condition-algebra
(`step`/`time_seconds`, `any`/`all`; `count` rejected as an un-driven axis). So the
`until` lever of a `Subscription` is now exposed. What remains here is the
**emission-filter** half — the `from`/`every` levers — i.e. *which* of the produced
points a request selects (strided / windowed / random-access), as opposed to *how
far* to drive. That is **still not needed until the function/service producer
lands** (with one index shape — the contiguous prefix — `until` suffices), and
when it does it is an **additive** change (optional `from=`/`every=` kwargs on the
already-`until=` signature; no second breaking rename).

## The idea

Generalize the request from a single `N` to a small **term algebra over
indices** — ranges `[a,b)`, strides, unions, single points — so a consumer
compactly expresses *which* subsequence it wants, and the spec is **forwarded to
the worker** to interpret per its own structure:

- a **range `[a,b)`** → a self-advancing sequence worker resumes from its
  checkpoint and runs to `b`, emitting `[a,b)` (one resume covers the range);
- a **strided / sparse selection** → a sequence worker still *runs* every step
  (recurrence — can't skip *running*) but *emits* only the selected ones; a
  function worker *computes only* the selected keys (it can skip);
- a **union of points / keys** → a function worker evaluates exactly those keys.

The same index-spec is thus interpreted efficiently downstream by whichever
worker strategy applies — structure-exploitation stays in the worker.

## The pleasing connection (don't invent a new algebra)

This is essentially the **subscription condition-algebra already shipped**
(`runstate/vocabulary/schedule.py`: `from`/`every`/`until` over
`step`/`time`/`count`) — which already *selects* indices, and which `history`
already *replays* over logged points. Indeed `up_to=N` ≡
`{every:{step:1}, until:{step:N}}` (what `ensure` builds internally for its
`history` read). So a general `ensure(schedule)` would **reuse** that algebra,
and it folds the memoizer's two levers into one forwarded spec: the bound is the
run *target*, the selector is the emission *filter*.

## Open questions / prerequisites

- Lands with the **function/service producer** (the second worker strategy);
  until then there is only one index shape (the contiguous prefix), so `until=`
  suffices and the `from`/`every` filter is YAGNI.
- ~~Take a `Subscription`-shaped spec directly (reuse), or a narrower range type?~~
  **Resolved** (`../specs/ensure-until-condition.md`, *Scoping*): expose the
  individual levers as typed kwargs (`until=` now; `from=`/`every=` additively
  later), **not** a whole-`Subscription`/`schedule=` argument — keeping the run
  *bound* un-conflated with the emission *filter*, and shipping no unexercised
  surface.
- **Caveat to document:** a sequence worker can't skip *running* steps for a
  strided request (the recurrence forces it), only skip *emitting* — so strided
  requests are not cheaper to *produce* on a sequence worker, only sparser to
  *store/serve*. (A function worker genuinely skips computation.)
