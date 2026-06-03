# Memoizer: generalize `ensure(up_to=N)` to an index term-algebra `ensure(I)`

Forward-looking (surfaced 2026-06-02, from the sequence-vs-function thread).
**Not needed until the function/service producer lands** — `ensure(up_to=N)` is
the validated sequence case. Captured so we don't re-derive it. Background: the
memoizer spec's "Design note" (`../specs/memoizer.md`) — the memoizer is thin,
the worker owns structure, and `up_to=N` is already sugar for "ensure the log
holds indices `I`."

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
  until then there is only one index shape (the contiguous prefix), so `up_to=N`
  suffices and `ensure(I)` is YAGNI.
- Take a `Subscription`-shaped spec directly (reuse), or a narrower range type?
- **Caveat to document:** a sequence worker can't skip *running* steps for a
  strided request (the recurrence forces it), only skip *emitting* — so strided
  requests are not cheaper to *produce* on a sequence worker, only sparser to
  *store/serve*. (A function worker genuinely skips computation.)
