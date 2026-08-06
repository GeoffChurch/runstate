# A lifecycle record that speaks for an episode must name it

**Status:** PROPOSED, **unattacked as a unit**. Supersedes `episode-correlation.md`, which proposed
the same mechanism for one defect and mislocated it. The three defects below are one defect, and the
mechanism is one field.

**This is a cluster.** It is co-gated with `claim-eviction.md` and touches
`lifecycle-stopped-unbundling.md`. Deciding any of them alone reproduces exactly the redundancy
`protocol-algebra.md` L2's minimality rule exists to prevent — and shipping this one alone hangs the
suite (§5, measured).

## The rule

> **Every `lifecycle.*` record that speaks for an episode carries `claim_seq` — the `seq` of the
> `lifecycle.started` it speaks for.** Readers attribute by that, never by position.

`value` is the deliberate exception (§6).

## Why: the tier is inconsistent, and the inconsistency is the bug

The launcher and control tiers already aim. The lifecycle tier aims only where it *answers a
request*, and not where it **reports** or **eliminates**:

| record | aims at | |
|---|---|---|
| `lifecycle.started` | its launch (`request_id`) | ✅ |
| `lifecycle.nak` | the request it refuses | ✅ |
| `launcher.launched` / `terminated` | their launch | ✅ |
| `control.subscribe` / `unsubscribe` / `stop` | their request | ✅ |
| **`lifecycle.stopped`** | — | ❌ |
| **`lifecycle.heartbeat`** | — | ❌ |
| `value` | — | deliberate, declared |

So the two records that speak *for an episode* without naming it are exactly the two that are
misattributed. `_launcher_terminal`'s docstring already states the principle, one tier up:

> the only death that can speak for the run is the death of *the launch that claim answered* — found
> by correlation id. **Position cannot do this job**: a reap is a reader-side observation that lands
> arbitrarily late … Both forgeries die by construction here.

`../specs/launcher-record-identity.md` made this move once. `../specs/write-authority.md` revision 4
then withdrew the invariant positional pairing depended on (*"single-writer holds at the claiming
instant only"*), and the lifecycle tier was never revisited.

## The three defects, all measured

**1. A forged verdict truncates `ensure` silently.** A displaced worker's honest `stopped` lands
after the successor's claim, so `peek_terminal` reads it as the successor's terminal:

```
peek_terminal -> RunResult(outcome=COMPLETED, final_step=1)   # successor still running
ensure(until={"step": 100}) -> 3 points, NO re-drive, NO error
```

**2. The claim plane cascades — the severe one.** From **one** third-party forged `stopped`, the
displaced worker's own honest dying breath releases the *live successor's* claim:

```
B claimed: True          <- legitimate successor
A.stopped()              <- A's OWN honest dying breath
  -> live_episode = None <- B's claim RELEASED
C claimed: True          <- B and C both live, from one forgery
```

The outcome `claim-eviction.md` and mycooc's own ruling both name as the worst available: *"a false
positive admits a second writer onto a live run, which is worse than the stranded claim it would
fix."* A bypass of the single-spawn guard, not a resolution policy.

**3. An unaimed heartbeat moves the frontier and the clock.**

```
successor B is at step 0;  progress = 0
A (displaced) ticks step 500 -> progress = 500     <- A's beacon moved it
```

`last_activity` is dated by it too.

## The mechanism — one field

`claim_seq: int`, non-null, on `Stopped` and `Heartbeat`. The worker already holds it
(`Worker._started_seq`). Folds attribute by aim:

- `_episode_stopped` / `live_episode` — a terminal ends the claim it names, not the claim it follows;
- `progress` (heartbeat axis) and `last_activity` — a beacon reports for the claim it names;
- `_discharge_floor` / `undischarged_stops` — **a `stopped` that names no live claim discharges
  nothing**, which closes the forgery route of #39 with the same field and no second one.

Prototyped for defects 1–2 (aim on `stopped` only): 4 files, +18/−11; verdict and claim planes both
correct, `ensure` re-drives.

**Required, not optional.** `lifecycle-v0.4` → **v0.5**, `claim_seq` non-null, so an unaimed record
is loudly malformed rather than silently inert. A `stamped → attribute; unstamped → positional`
branch would be worse than useless: it makes an unstamped record strictly **more** powerful than a
stamped one (it would release any claim, forever) and makes forgery easier by omission.

## The sequencing gate — measured, and it is hard

**This cannot ship before a legal third-party release exists.** Under the aim rule an unaimed
`lifecycle.stopped` releases nothing — and that is today's *only* claim-release mechanism for a
stranded claim. `ensure` has no hang timeout:

```
tests/test_memoizer.py -- HANGS (60s timeout, exit 143)
  first hang: test_ensure_redrives_when_extend_noops_onto_a_live_episode
Rest of suite: 48 failures across 3 files (baseline 814 passed / 0 failed)
```

The failure mode is a **silent infinite hang**, not a loud error.

**This reframes `claim-eviction.md`, and improves its case.** Its old justification was *"it fixes
#39 and #42"* — both of which the census then showed to be small. Its real justification is now
structural: **once `stopped` requires aim, a third party needs a legal verb to release a claim at
all.** That argument does not depend on any incident count.

Note the eliminator's own record must carry `claim_seq` too — it already specifies exactly that
(*"the `lifecycle.started` this evicts — the AIM"*), so the two halves are one rule.

## What this does NOT fix

- **The honest-worker route of #39.** A worker whose own `stopped` discharges a stop it never
  drained is still discharging it. That is not a forgery — it is the run-scoped-vs-episode-scoped
  mismatch, filed separately as `run-scoped-halt.md`. Aim closes the *forgery* route only.
- **The value plane.** Deliberate: `value_series`'s docstring declares *"a **convergent merge**
  (last-write-wins per cell), not a consistent snapshot"*, and `write-authority.md` calls it
  *"resolved, not corrupt … a declared cost."* Stamping values was proposed and dropped; asking for
  a different winner there is a request to change the consistency model, not a bug report.
- **The artifact plane.** This makes the log stop lying. The checkpoint a displaced worker wrote is
  still on disk, and that is where the real damage lands (`write-authority.md`).
- **Authentication.** A forger can stamp any epoch. Aim is *checkable* (does the named claim exist,
  and is it in force?); authorship is not. **Name what you act on, not who you are** — which is
  precisely why the launcher tier used correlation ids rather than an author field, and why no
  record here carries one.

## Migration — precedent exists and shipped

`launcher-record-identity.md` did exactly this backfill: a one-time offline pass stamping synthetic
ids positionally, *"applied ONCE, over a whole quiescent log rather than live against a moving one,
which is what made it wrong"*, reporting genuinely-ambiguous dbs by path.

The half that does **not** transfer: launcher ambiguity is detectable; a forged `stopped` is
*"byte-indistinguishable from a real one"*, so there is no report-the-ambiguous pass. Already-broken
logs stay broken — acceptable per the owner's standing directive, and the point of `claim_seq`
being required is that the breakage is loud.

## Attack this before building

Unattacked **as a unit** — the halves were reviewed against different questions.

1. **Does one field really cover three folds?** The discharge filter is asserted, not prototyped.
   Does *"a `stopped` naming no live claim discharges nothing"* break any legitimate case — in
   particular a worker whose claim was legitimately superseded between drain and dying breath?
2. **Is the heartbeat aim right for `last_activity`?** A displaced worker's beacon *is* activity, of
   a dead episode. Filtering it may make a genuinely-running process read stale. Progress and
   freshness may want different answers from the same field.
3. **Does `retire()`'s death-CAS interact?** It already CASes with `expected_seq=observed`;
   `stopped()` appends unguarded. That asymmetry is undocumented and may compose badly with aim.
4. **Is the sequencing gate the only one?** 48 failures were counted; they were not classified. Some
   may be tests encoding the old semantics (fine) and some may be real consumers of positional
   attribution (not fine).
5. **Does it subsume or duplicate the unbundling thread?** If `stopped` names both its claim and
   (implicitly, via the discharge filter) its stops, is the five-job bundle still the defect
   `lifecycle-stopped-unbundling.md` describes, or has aim dissolved it?

## Related

- `claim-eviction.md` — co-gated; its justification is restated by §5
- `lifecycle-stopped-unbundling.md` — see attack 5
- `run-scoped-halt.md` — owns the honest-worker route of #39
- `../specs/launcher-record-identity.md` — the shipped precedent, one tier up
- `../specs/write-authority.md` — why the invariant this replaces was withdrawn
- `value-plane-divergence-resolution.md` — the declared exception; its residual list needs amending
  to include the forged-`stopped` route
