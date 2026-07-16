# Dead end: `window_closed(progress, until)` — the fencepost as API

**Refuted 2026-07-11** (the 2026-07 review, agenda item 6 — the agenda itself is
pruned; git carries its deliberation). Proposed as a public observable answering
*"has this run closed its `until` window?"*; **cut, and the rule documented instead**
— on `observables.progress`'s docstring.

## What was proposed

A helper for the half-open-window fencepost: a target `until={"step": N}` is `[0, N)`,
so it is reached iff `progress + 1 >= N`. The pitch was footgun-prevention — an
off-by-one that a second-language implementer or a viewer could plausibly get wrong.

## Why it is a dead end

**It is shallow arithmetic on an already-correct value, not a derivation.** Every
observable that earns its place traces to the append-only/multi-episode staleness the
observer plane exists to hide (`peek_terminal`'s episode-awareness, `progress`'s
two-register frontier, `live_demand`'s positional fold). `progress + 1 >= N` traces to
nothing — it is addition on a number the caller already has.

**The empirical kill: both consumers spelled it right by hand and neither would
adopt it** — translation's `keys.py:floor_ok`, mycooc's inline `p >= req`. A
footgun nobody trips is not a footgun.

**The beneficiaries needed the *rule*, not the API.** A second-language implementer
and a viewer need to *know* the fencepost; they do not need a Python function they
cannot call. Resolution: the rule is written on `observables.progress`, where anyone
asking "did this run reach its target?" already is. The memoizer keeps its one
internal home (`_window_step`).

## The two lessons worth not re-treading

1. **"Unused by mycooc + translation" is weak sugar-evidence.** Both repos are the
   *same* (reuse) persona and speak for no other. It was strong here only because the
   claim was footgun-prevention and *those very consumers* are the ones who would have
   tripped. Do not generalize the move.
2. **Banked, do NOT re-flag as sugar** (the helper-classification audit's other soft
   spots, cleared): `sweep` is the **batch-sweep persona's entry point** (run a fixed
   variant set to completion, collect verdicts) — parallel to `ensure`'s
   memoized-target door, not sugar; both consumers are reuse-shaped so they take the
   `ensure` door, and translation reuses `sweep`'s `Variant` + `launch_producer`
   regardless. `pinned` / `broadcast` / `ensure_served` are the service/leased-demand
   plane the basis audit (Q4) already ruled KEEP for a future persona.

## Postscript (2026-07-16)

The fencepost's documented home was load-bearing sooner than expected: it is the rule
[`../specs/control-target.md`](../specs/control-target.md) builds on, and the
adversarial pass that refuted that spec found the rule **has two sites, not one** —
`steps(total)` checks it **pre-yield** (`while step < total`) while a post-tick target
check needs the `+1`. They agree only for `start < N`, and diverge at `N = 0` and on
resume-into-a-met-target. *That* is a real footgun — and it lives in the arithmetic's
**placement**, not in the arithmetic. A `window_closed(progress, until)` helper would
not have caught it: both sites would have called it and passed different coordinates.
The refutation holds, and the near-miss sharpens it.
