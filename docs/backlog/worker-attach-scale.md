# Worker attach is O(N_total): topic-filter the claim-read folds

**Status:** measured, fix designed — 2026-07-10 (stage-3b scale probes of the
holistic review). Implementation-only; **no protocol change** — but it surfaces
one substrate-basis question (below) for the basis audit.

## The numbers

On a translation-shaped 10⁶-envelope sqlite log (~50% heartbeats, ~50% values,
ONE control record), warm cache:

- `Worker` attach (`__init__`): **~3.4 s and ~0.77 GB transient RSS** — the
  unfiltered `channel.read()` at `worker.py` decodes every envelope to compute
  the discharge floor, the answer fold, the boundary list, and the CAS's
  `expected_seq`.
- The first tick's control re-drain (what §12.5's deferred cursor-persistence
  targets): **~2.2 ms** — `read(topics=["control.>"])` is index-served.
- Per *control* record the refold costs ~6.6 µs; control volume alone crosses
  1 s only at M ≈ 1.5×10⁵ — far beyond realistic control traffic.

So §12.5's "pure efficiency, non-blocking" framing survives for the *refold*,
but the dominant resume term is the init read, and persisting the cursor would
optimize the cheap term while leaving the expensive one.

## The fix (exactness preserved)

The folds only need `lifecycle.stopped` (floor), `control.unsubscribe` +
`lifecycle.nak` (answers), and `lifecycle.started` (boundaries) — all
index-served and rare. The claim loop becomes:

1. Read the log's **head seq** `S` *first*.
2. Compute the folds from topic-filtered reads, **capped at `seq <= S`**
   (records landing after `S` are ignored this attempt).
3. Claim with `send(..., expected_seq=S)`.

CAS success ⟺ the head was still `S` at append ⟹ the capped folds are exactly
the folds of the same-read discipline (`specs/stop-discharge.md`'s same-read
fusion, preserved by assertion instead of by one big read). CAS loss ⟹ loop, as
today.

## The missing affordance (the basis question)

Step 1 has no cheap spelling on the four-op surface: `read()` is O(N),
`latest(topic)` is per-topic, and a max over the reserved topics is unsound
(the substrate admits arbitrary user topics). Options:

1. **A `head() -> int` substrate op** (or `latest()` sans topic). Rubric case
   FOR: the CAS already *speaks* head-language — `expected_seq` IS a head
   assertion — so reading what every claimant must assert looks canonical, and
   every backend has it O(1) (`MAX(seq)` on a keyed table; `len(log)`). Rubric
   case AGAINST: it is derivable from `read()` (independence), and adding a
   fifth op for one caller's performance is the kind of accretion §4's
   lift-rule exists to resist.
2. **A capability Protocol** (the `EpisodeProbe` pattern): `isinstance`-gated
   fast path, `read()`-derived fallback. No surface growth for backends that
   can't serve it; the Worker stays correct everywhere.
3. Live with O(N) attach until a consumer hurts (translation's logs are already
   at 10⁶; a resume there pays 3.4 s today).

Decide at the basis audit. The viewer-side siblings of this O(N) class
(`Watcher._drain`'s from-0 first replay, `live_demand`) are catalogued as
design constraints in `visualization-story.md`, not here — this entry owns the
worker-resume half.
