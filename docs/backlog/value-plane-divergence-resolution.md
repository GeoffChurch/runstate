# Value-plane divergence: take-the-latest resolution (delete the sticky raise)

**Status:** forward-looking design (2026-06-26), not implemented. This is the keystone the
[ensure-redrive-recoverable-terminations](ensure-redrive-recoverable-terminations.md) item
calls **G1**, and the value-plane robustness the cross-host claim gate needs
(`../dead_ends/failure-detector.md` "where it goes instead"; index.md "Cross-host liveness").
**An earlier draft proposed attribution + an "authoritative attempt" default; a code-grounded
red-team refuted it** — it added machinery to resolve a case that cannot arise. The converged
design is below: the fix is to *delete* the raise, not add machinery. Living document.

## The problem

`history(channel, name, until)` is the **reuse** projection: fold the logged `value` records
into the trajectory a consumer asked for. A `value` is keyed by `(name, step)`. When a run is
resumed from a checkpoint **behind its last-emitted step**, the resume re-runs the overlap and
**appends** a second `value` record for those steps. If the recompute is non-deterministic
(GPU/BLAS, unseeded), the log now holds two *different* values for one `(name, step)`:

```
  (loss, 41) = 0.50     attempt 1, just before it died
  --- resumed from the step-40 checkpoint ---
  (loss, 41) = 0.73     attempt 2 re-ran step 41, got a different number
```

`history` detects this and **raises** `divergent re-emission … reuse would be unsound`. Because
the log is append-only, both records stay forever, so *every future* `history`/`ensure` on this
run raises — **reuse is permanently blocked.** That stickiness is the bug. (The display
projection `value_series` already resolves the same duplicate silently by last-write-wins —
fine for a chart.)

## The fix: take-the-latest, at read time

Resolve a duplicate `(name, step)` by **keeping the latest record (highest `seq`)**, and
**delete the divergence raise**. This is a *read-time fold*, not a log mutation: both records
remain in the append-only log forever (which is what made the duplicate detectable in the first
place); only the *returned* trajectory prefers the later value. `history` already collapses by
step keeping the latest (`memoizer.py:130`); removing the ~6-line raise (`memoizer.py:124-129`)
makes it the **same fold `value_series` already ships** (`observables.py:250`). The resolution
fix *removes* code.

## Why take-the-latest is sound (the reachability argument)

The tempting objection — "take-the-latest is wrong when an earlier attempt *finished* (reached
the goal) and a later attempt re-emits a divergent value, because it discards the finished
attempt's authoritative value" — describes a case that **cannot arise through `ensure`**:

- **`ensure` never re-drives a `completed` run.** It short-circuits to a pure log read the
  moment a run reports `completed` (`memoizer.py:234-235`, and post-drive `:259-260`) — even
  when the requested `until` exceeds where it completed. So a finished attempt is never followed
  by a re-driven one.
- **Every divergence `ensure` *can* produce is a resume after a non-completed death** (a
  `preempted` re-drive, or a recordless-death recovery). Episodes are sequential
  (single-writer-per-run), so the continuing branch owns the highest `seq` at every overlapped
  step. There "the latest" *is* the continuing branch — the only coherent trajectory to the
  goal — so take-the-latest returns the right value.

A code-grounded red-team verified both points and reproduced them against a real channel: for
every divergence reachable through `ensure`, take-the-latest yields exactly the value an
"authoritative attempt" rule would, and the finished-then-rerun counterexample is unreachable.
(Scope: "the right value" is per-step; a re-emitted step's *time-window membership* rides the
resumed wall-clock — a pre-existing property of `history`'s latest-`t` collapse, not a
take-the-latest regression.)

## G1 also fixes a latent bug on `master` (not just a future gate)

`ensure` *already* re-drives `preempted` runs, and "preempt ⟹ no overlap" is **not** guaranteed:
the reference `Worker` has no checkpoint primitive (saving is the user's job, and the default
producer restarts a naive worker from step 0), and a kill can land between
`lifecycle.stopped(preempted)` and the user's checkpoint write — a clean `preempted` terminal
with a *stale* checkpoint. So the shipped preempt-redrive can already re-emit a divergent overlap
and sticky-poison reuse on a non-reproducible worker. Take-the-latest fixes this **today**,
independent of any new feature — G1 is a bug fix, not only a prerequisite.

## What we rejected, and why

- **An "authoritative attempt" default** (prefer the finished/continuing attempt over the
  latest). It buys **zero** correctness on any input reachable through `ensure` (the reachability
  argument), and the only case where it would differ is out-of-contract (a *direct*
  `relaunch_if_needed` on a completed run, or NFS double-live). A new default policy for an
  unreachable case is a wart.
- **Attribution + a fork-surface** (tag each value with its attempt; expose unresolved forks).
  Needed only to *inspect* divergences — a forensic affordance no consumer has asked for. Defer
  until one does (YAGNI). Take-the-latest needs no attribution.
- **A "raise on double-live" guard.** Walked back. Genuine concurrent double-live can't be
  distinguished from "first attempt died silently, then resumed" without liveness evidence
  (same-host a PID probe; cross-host only heartbeat-staleness — the unsolved problem the
  failure-detector dead-end hit). And it is moot where it could be cheap: on atomic-CAS backends
  (memory, single-host sqlite) the worker birth-CAS muzzles the double-spawn loser
  (`worker.py:72-74,225`), so no value-emitting double-live occurs; it can only occur on NFS,
  which is already out-of-contract. Double-live belongs to the cross-host claim gate, not here.

## What it unblocks

- **Killed-redrive** ([ensure-redrive-recoverable-terminations](ensure-redrive-recoverable-terminations.md)):
  re-driving a non-self-diagnosed death re-emits the overlap; a non-sticky `history` no longer
  permanently blocks reuse. Killed-redrive needs **only** this non-stickiness — not authoritative
  resolution.
- **The cross-host claim gate** (index.md "Cross-host liveness"): a residual ◊P double-live can
  emit a divergent pair; a non-sticky `history` keeps that from permanently poisoning reuse. (The
  *detection* of that double-live is the claim gate's separate, hard problem.)

## The out-of-contract residual (and why we are *not* "closing a hole")

Take-the-latest is silently *wrong* only in the finished-then-rerun case, reachable only by (a) a
*direct* `relaunch_if_needed` on a completed run — and even then only if the worker re-runs
already-finished steps non-deterministically (a contract violation; a conformant worker resumes
*forward* from its frontier checkpoint with no overlap) — or (b) NFS double-live (out-of-contract,
CAS-prevented on every other backend). So no in-contract path produces a silently-wrong reuse.

An earlier draft proposed "closing the hole" by giving `relaunch_if_needed` / `ensure_served`
`ensure`'s `completed` guard. **Dropped:** it is redundant for `relaunch_if_needed` (which only
overloads a liveness primitive with verdict policy that already lives in `ensure`) and **wrong**
for `ensure_served` — `specs/lazy-launch.md` documents re-waking a *completed* service run as
accepted leased-demand behavior, so a `completed` guard there would break the model. The direct
`relaunch_if_needed`-on-completed case is **caller-owned**. If a forensic "show me the
divergences" need ever arises, the deferred attribution/fork-surface is the place to add it —
strictly additive.

## Implementation notes
- **The change:** delete the raise at `memoizer.py:124-129`; keep the collapse at `:130` — `history`
  then folds identically to `value_series`.
- **One test flips:** `test_memoizer.py::test_history_collapses_benign_re_emission_but_raises_on_divergence`
  — its benign half still passes; replace the `raises(… "divergent")` half with an assertion that the
  latest-by-`seq` value wins. (A full-suite run with the raise removed showed exactly this one test
  failing.)
- **Migrate the shipped docs that describe the raise** (per the no-legacy directive): `specs/memoizer.md`
  Decision 4 (and its Tests line), and `specs/derived-runs.md:96` (which currently leans on the
  *permanent* raise as the emit-only-missing safety rationale — that rationale shifts to "emit-only-missing
  *prevents* the re-emit").

## Orthonormal-basis check
- **Independence:** the fix *removes* a primitive (the raise) rather than adding one; resolution
  reuses the existing `value_series` fold. No new field, no new index, no new API.
- **Spanning:** non-stickiness is the property both gated features need, and take-the-latest
  provides it. The one thing it cannot express — inspect a fork — is out of scope until a consumer
  needs it.
- **Canonical form:** one resolution rule (latest-by-`seq`) shared by display and reuse — the
  least-arbitrary fold, and the one already blessed for the rewind case.
- **Orthogonality:** resolution (a read fold) is cleanly separate from the append-only log
  (untouched) and from liveness/double-live (deferred to the claim gate). Correctness for
  reachable inputs comes from `ensure`'s `completed` guard, kept where it already lives.
- **Serendipity:** display and reuse collapse to one fold; the keystone for two gated features
  becomes "delete six lines," not "add a subsystem."

Meta: the substrate stays opinion-free and append-only — the fix neither mutates the log nor adds
a field; it only stops a reader from refusing a duplicate it can safely resolve.
